"""profile_extractor.py — LLM-based applicant profile extraction.

Extracts structured ApplicantProfile fields from free-form conversation history.
The LLM ONLY extracts — it does NOT make eligibility decisions.

Design:
  • Sends the full conversation to the LLM with a strict JSON extraction prompt
  • Returns an ApplicantProfile dataclass + list of missing required fields
  • Any field not mentioned → None (rules engine treats as NOT_EVALUATED)
  • Conversions applied:
      - Annual income → divide by 12 for monthly
      - "X years" → multiply by 12 for months
      - loan type aliases: "house loan", "housing loan" → "home_loan"
"""
import json
import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger("loanassist.profile")

# Import from same package
from rules_engine import ApplicantProfile


# ── extraction prompt ─────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """\
You are a structured data extractor for a loan eligibility assistant.

Given a conversation, extract all applicant-provided information into a
JSON object. Return ONLY valid JSON — no explanations, no markdown.

Schema (use null for any unknown field):
{
  "loan_type":                  "personal_loan" | "home_loan" | null,
  "age":                        integer | null,
  "employment_type":            "salaried" | "self_employed" | "business" | null,
  "monthly_net_income":         float (INR, monthly) | null,
  "employment_duration_months": integer | null,
  "credit_score":               integer | null,
  "existing_emi":               float (INR, total monthly) | null,
  "requested_amount":           float (INR) | null,
  "requested_tenure_months":    integer | null,
  "property_value":             float (INR) | null,
  "down_payment":               float (INR) | null,
  "property_type":              "apartment" | "villa" | "residential_house" | "plot" | "commercial" | null,
  "property_location":          string | null,
  "existing_property_loan":     boolean | null
}

Conversion rules:
- If the applicant switches or requests a different loan product (e.g. from home loan to personal loan, or vice versa), loan_type MUST be the LATEST requested loan product.
- When loan_type is personal_loan, all property fields (property_value, down_payment, property_type, property_location, existing_property_loan) must be null.
- If income is stated as annual, divide by 12 and store monthly result
- If duration is stated in years, multiply by 12 and store months
- "house loan", "housing loan", "mortgage", "home loan" → "home_loan"
- "personal loan", "consumer loan" → "personal_loan"
- "salary", "take-home" → monthly_net_income (assume monthly unless annual stated)
- Amounts in lakhs: "5 lakhs" = 500000, "1 crore" = 10000000
- CIBIL score = credit score

Extract only what the applicant has explicitly stated. Do not infer or guess
values that were not mentioned. Use null for anything not stated.
"""


def _build_extraction_messages(history: list[dict]) -> list[dict]:
    """Build LLM messages for profile extraction from conversation history."""
    # Flatten history to a readable transcript
    transcript_lines = []
    for msg in history:
        role    = msg.get("role", "user").capitalize()
        content = msg.get("content", "")
        transcript_lines.append(f"{role}: {content}")
    transcript = "\n".join(transcript_lines)

    return [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user",   "content": f"Conversation:\n{transcript}"},
    ]


def _parse_profile_json(raw: str) -> dict:
    """Extract JSON from LLM output, tolerating markdown code fences."""
    # Strip ```json ... ``` or ``` ... ```
    stripped = re.sub(r"```(?:json)?", "", raw).strip()
    # Find first { ... } block
    start = stripped.find("{")
    end   = stripped.rfind("}") + 1
    if start == -1 or end == 0:
        return {}
    try:
        return json.loads(stripped[start:end])
    except json.JSONDecodeError:
        return {}


def _normalise(data: dict) -> dict:
    """Apply type coercions and clean up extracted values."""
    # Ensure numeric types
    for float_field in ("monthly_net_income", "existing_emi", "requested_amount",
                        "property_value", "down_payment"):
        v = data.get(float_field)
        if v is not None:
            try:
                data[float_field] = float(v)
            except (TypeError, ValueError):
                data[float_field] = None

    for int_field in ("age", "credit_score", "employment_duration_months",
                      "requested_tenure_months"):
        v = data.get(int_field)
        if v is not None:
            try:
                data[int_field] = int(v)
            except (TypeError, ValueError):
                data[int_field] = None

    # Normalise loan_type aliases
    lt = data.get("loan_type")
    if isinstance(lt, str):
        lt_lower = lt.lower().replace(" ", "_")
        if lt_lower in ("home_loan", "housing_loan", "house_loan", "mortgage"):
            data["loan_type"] = "home_loan"
        elif lt_lower in ("personal_loan", "consumer_loan", "pl"):
            data["loan_type"] = "personal_loan"

    # Normalise property_type
    pt = data.get("property_type")
    if isinstance(pt, str):
        data["property_type"] = pt.lower().replace(" ", "_")

    return data


def extract_profile(
    history:   list[dict],
    llm_client,
    model:     str,
) -> Tuple[ApplicantProfile, list[str]]:
    """Extract ApplicantProfile from conversation history using the LLM.

    Returns:
        (profile, missing_fields)
        missing_fields is empty when the profile is complete for the loan type.
    """
    messages = _build_extraction_messages(history)

    try:
        completion = llm_client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=400,
            temperature=0,  # deterministic extraction
        )
        raw = (completion.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("profile_extraction_failed error=%s", type(exc).__name__)
        raw = "{}"

    data    = _parse_profile_json(raw)
    data    = _normalise(data)

    # Dynamic product switch detection from recent conversation turns
    recent_user_text = ""
    for msg in reversed(history):
        if msg.get("role") == "user":
            recent_user_text = msg.get("content", "").lower()
            break

    if "personal loan" in recent_user_text or "consumer loan" in recent_user_text:
        data["loan_type"] = "personal_loan"
        for pf in ("property_value", "down_payment", "property_type", "property_location", "existing_property_loan"):
            data[pf] = None
        if data.get("requested_tenure_months") and data["requested_tenure_months"] > 60:
            data["requested_tenure_months"] = None
        if data.get("requested_amount") and data["requested_amount"] > 3000000:
            data["requested_amount"] = None
    elif any(k in recent_user_text for k in ("home loan", "housing loan", "house loan", "mortgage")):
        data["loan_type"] = "home_loan"

    # Build ApplicantProfile from extracted data — unknown fields stay None
    profile = ApplicantProfile(
        loan_type                  = data.get("loan_type"),
        age                        = data.get("age"),
        employment_type            = data.get("employment_type"),
        monthly_net_income         = data.get("monthly_net_income"),
        employment_duration_months = data.get("employment_duration_months"),
        credit_score               = data.get("credit_score"),
        existing_emi               = data.get("existing_emi"),
        requested_amount           = data.get("requested_amount"),
        requested_tenure_months    = data.get("requested_tenure_months"),
        property_value             = data.get("property_value"),
        down_payment               = data.get("down_payment"),
        property_type              = data.get("property_type"),
        property_location          = data.get("property_location"),
        existing_property_loan     = data.get("existing_property_loan"),
    )

    missing = profile.missing_fields()
    logger.info(
        "profile_extracted loan_type=%s missing=%d fields",
        profile.loan_type, len(missing),
    )
    return profile, missing


# ── missing-field descriptions (for UX) ──────────────────────────────────────

FIELD_LABELS: dict[str, str] = {
    "loan_type":                  "loan type (Personal Loan or Home Loan)",
    "age":                        "your age",
    "employment_type":            "employment type (salaried, self-employed, or business owner)",
    "monthly_net_income":         "monthly net income (take-home pay after deductions)",
    "employment_duration_months": "how long you have been employed / in business",
    "credit_score":               "your credit / CIBIL score",
    "existing_emi":               "total existing monthly EMI obligations (enter 0 if none)",
    "requested_amount":           "loan amount you are requesting",
    "requested_tenure_months":    "preferred loan tenure in months",
    # Home loan
    "property_value":             "property market value",
    "down_payment":               "down payment amount",
    "property_type":              "property type (apartment, villa, or residential house)",
}


def describe_missing(missing_fields: list[str]) -> str:
    """Return a concise bullet list of missing field descriptions."""
    lines = [f"• {FIELD_LABELS.get(f, f)}" for f in missing_fields]
    return "\n".join(lines)
