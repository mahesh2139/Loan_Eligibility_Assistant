"""LoanAssist — Streamlit UI v2

Features:
  • Streaming chat (SSE via requests stream=True)
  • Eligibility result card with per-rule pass/fail table
  • EMI / FOIR / LTV calculator panel
  • Policy citations with Rule IDs and version
  • Document checklist (loan-type-specific)
  • "Why not eligible?" breakdown of failed rules
  • What-if scenario simulator (sidebar sliders)
  • Policy version comparison (Personal Loan v1 vs v2)
  • Audit trail — view full audit record by Application ID
"""
import json
import os
import time
import uuid

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="LoanAssist",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_URL = os.getenv("API_URL", "http://localhost:8001")
API_KEY = os.getenv("API_KEY", "local-dev-key")

# ── session state ─────────────────────────────────────────────────────────────
for key, default in [
    ("messages",       []),
    ("session_id",     f"ui-{uuid.uuid4().hex[:8]}"),
    ("last_decision",  None),
    ("last_calcs",     None),
    ("last_citations", []),
    ("last_profile",   {}),
    ("last_app_id",    None),
    ("missing_fields", []),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── helpers ───────────────────────────────────────────────────────────────────

PRODUCT_LABELS = {
    "personal_loan": "Personal Loan",
    "home_loan":     "Home Loan",
}

DECISION_CONFIG = {
    "POTENTIALLY_ELIGIBLE":   ("✅", "success", "Potentially Eligible"),
    "NOT_ELIGIBLE":           ("❌", "error",   "Not Eligible"),
    "MANUAL_REVIEW":          ("⚠️", "warning", "Manual Review Required"),
    "INSUFFICIENT_INFORMATION": ("ℹ️", "info",  "More Information Needed"),
    # backward compat
    "PRE_QUALIFIED":          ("✅", "success", "Pre-Qualified"),
    "NOT_PRE_QUALIFIED":      ("❌", "error",   "Not Pre-Qualified"),
    "NEEDS_INFORMATION":      ("ℹ️", "info",    "More Information Needed"),
}

DOC_CHECKLISTS = {
    "personal_loan": [
        "Aadhaar Card (identity proof)",
        "PAN Card (mandatory)",
        "Last 3 months pay slips (salaried) / 2 years ITR (self-employed)",
        "Last 6 months bank statements",
        "Form 16 (salaried applicants)",
        "Business registration + GST certificate (self-employed)",
        "Address proof (utility bill / rental agreement)",
    ],
    "home_loan": [
        "Aadhaar Card + PAN Card (mandatory)",
        "Last 3 months pay slips / 2 years audited financials (self-employed)",
        "Last 12 months bank statements (salaried) / 24 months (self-employed)",
        "Form 16 for last 2 years",
        "Sale agreement / allotment letter",
        "Title deed / chain of title documents",
        "Approved building plan",
        "NOC from housing society / builder",
        "Property tax receipts",
    ],
}


def _fmt_inr(val):
    """Format a number as ₹ with Indian comma format."""
    if val is None:
        return "—"
    return f"₹{val:,.0f}"


def _pct(val):
    if val is None:
        return "—"
    return f"{val * 100:.1f}%"


# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🏦 LoanAssist")
    st.caption("Loan pre-qualification assistant")
    st.divider()

    # ── API settings ──────────────────────────────────────────────────────────
    with st.expander("⚙️ API Settings"):
        api_url_input = st.text_input("API URL", API_URL)
        API_URL = api_url_input

    # ── Profile card ──────────────────────────────────────────────────────────
    if st.session_state.last_profile:
        profile = st.session_state.last_profile
        st.subheader("📋 Your Profile")
        prod = profile.get("loan_type", "")
        if prod:
            st.markdown(f":blue-background[🏷️ **{PRODUCT_LABELS.get(prod, prod)}**]")
        for field, val in profile.items():
            if field == "loan_type":
                continue
            label = field.replace("_", " ").title()
            if isinstance(val, float):
                if "income" in field or "emi" in field or "amount" in field or "value" in field or "payment" in field:
                    val = _fmt_inr(val)
                else:
                    val = f"{val:.2f}"
            st.caption(f"**{label}:** {val}")

        if st.session_state.missing_fields:
            st.warning(
                "Still needed:\n" +
                "\n".join(f"• {f.replace('_', ' ')}" for f in st.session_state.missing_fields)
            )

    # ── Calculator panel ──────────────────────────────────────────────────────
    if st.session_state.last_calcs:
        st.divider()
        st.subheader("🧮 Calculations")
        calcs = st.session_state.last_calcs
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Monthly EMI",     _fmt_inr(calcs.get("emi")))
            st.metric("FOIR",            _pct(calcs.get("foir")))
        with col2:
            st.metric("LTV",             _pct(calcs.get("ltv")))
            st.metric("Max Affordable",  _fmt_inr(calcs.get("max_affordable_loan")))
        if calcs.get("total_interest"):
            st.caption(f"Total interest: {_fmt_inr(calcs['total_interest'])}")

    # ── What-if scenario simulator ────────────────────────────────────────────
    if st.session_state.last_profile.get("loan_type"):
        st.divider()
        st.subheader("🔮 What-If Simulator")
        st.caption("Adjust values and re-run the eligibility engine instantly.")

        with st.form("scenario_form"):
            col1, col2 = st.columns(2)
            with col1:
                new_income = st.number_input(
                    "Monthly income (₹)",
                    value=int(st.session_state.last_profile.get("monthly_net_income") or 0),
                    step=5000,
                )
                new_credit = st.number_input(
                    "Credit score",
                    value=int(st.session_state.last_profile.get("credit_score") or 700),
                    min_value=300, max_value=900, step=10,
                )
            with col2:
                new_amount = st.number_input(
                    "Loan amount (₹)",
                    value=int(st.session_state.last_profile.get("requested_amount") or 0),
                    step=50000,
                )
                new_tenure = st.number_input(
                    "Tenure (months)",
                    value=int(st.session_state.last_profile.get("requested_tenure_months") or 60),
                    min_value=12, step=12,
                )
            run_scenario = st.form_submit_button("▶ Re-run eligibility")

        if run_scenario:
            overrides = {
                "monthly_net_income":      float(new_income),
                "credit_score":            int(new_credit),
                "requested_amount":        float(new_amount),
                "requested_tenure_months": int(new_tenure),
            }
            try:
                r = requests.post(
                    f"{API_URL}/scenario",
                    json={"session_id": st.session_state.session_id, "overrides": overrides},
                    headers={"X-API-Key": API_KEY},
                    timeout=30,
                )
                r.raise_for_status()
                result = r.json()
                sc_decision = result["decision"]["decision"]
                icon, kind, label = DECISION_CONFIG.get(sc_decision, ("?", "info", sc_decision))
                if kind == "success":  st.success(f"{icon} Scenario result: **{label}**")
                elif kind == "error":  st.error(f"{icon} Scenario result: **{label}**")
                elif kind == "warning": st.warning(f"{icon} Scenario result: **{label}**")
                else:                  st.info(f"{icon} Scenario result: **{label}**")

                if result["decision"].get("failed_rules"):
                    st.caption("Failed rules:")
                    for r_ in result["decision"]["failed_rules"]:
                        st.caption(f"  ✗ [{r_['rule_id']}] {r_['detail']}")

                if result.get("calculations"):
                    calcs = result["calculations"]
                    st.caption(
                        f"EMI: {_fmt_inr(calcs.get('emi'))}  |  "
                        f"FOIR: {_pct(calcs.get('foir'))}  |  "
                        f"Max loan: {_fmt_inr(calcs.get('max_affordable_loan'))}"
                    )
            except Exception as exc:
                st.error(f"Scenario error: {exc}")

    # ── New conversation ───────────────────────────────────────────────────────
    st.divider()
    if st.button("🆕 New Conversation", use_container_width=True, type="secondary"):
        for key in ["messages", "last_decision", "last_calcs", "last_citations",
                    "last_profile", "last_app_id", "missing_fields"]:
            st.session_state[key] = [] if key in ("messages", "last_citations", "missing_fields") else None
        st.session_state.session_id = f"ui-{uuid.uuid4().hex[:8]}"
        st.rerun()

    st.divider()
    st.caption(f"Session: `{st.session_state.session_id}`")
    st.caption(f"API: `{API_URL}`")


# ── top banner: product switcher & status ──────────────────────────────────────
current_prod = st.session_state.last_profile.get("loan_type") if st.session_state.last_profile else None
prod_display = PRODUCT_LABELS.get(current_prod, "Not Selected")
prod_icon = "🏠" if current_prod == "home_loan" else ("💳" if current_prod == "personal_loan" else "🔍")

banner_c1, banner_c2, banner_c3, banner_c4 = st.columns([3.5, 2.5, 2.5, 2], vertical_alignment="center")

with banner_c1:
    st.markdown(f"**Active Mode:** {prod_icon} :blue-background[**{prod_display}**]")

with banner_c2:
    if st.button("💳 Switch to Personal Loan", use_container_width=True, key="banner_switch_pl"):
        st.session_state.last_profile["loan_type"] = "personal_loan"
        for pf in ["property_value", "down_payment", "property_type", "property_location", "existing_property_loan"]:
            st.session_state.last_profile.pop(pf, None)
        st.session_state.last_decision = None
        st.session_state.last_calcs = None
        st.session_state.last_citations = []
        st.session_state["_quick_msg"] = "I want to check my eligibility for a personal loan."
        st.rerun()

with banner_c3:
    if st.button("🏠 Switch to Home Loan", use_container_width=True, key="banner_switch_hl"):
        st.session_state.last_profile["loan_type"] = "home_loan"
        st.session_state.last_decision = None
        st.session_state.last_calcs = None
        st.session_state.last_citations = []
        st.session_state["_quick_msg"] = "I want to check my eligibility for a home loan."
        st.rerun()

with banner_c4:
    if st.button("🔄 New Application", use_container_width=True, key="banner_new_app"):
        for key in ["messages", "last_decision", "last_calcs", "last_citations",
                    "last_profile", "last_app_id", "missing_fields"]:
            st.session_state[key] = [] if key in ("messages", "last_citations", "missing_fields") else None
        st.session_state.session_id = f"ui-{uuid.uuid4().hex[:8]}"
        st.rerun()

st.divider()

# ── main area ─────────────────────────────────────────────────────────────────

col_chat, col_info = st.columns([3, 2], gap="large")

with col_chat:
    st.header("💬 Chat")

    # Scrollable messages viewport — keeps chat_input fixed at the bottom!
    chat_container = st.container(height=580)

    # Chat input anchored directly at the bottom
    quick_msg = st.session_state.pop("_quick_msg", None)
    prompt = st.chat_input("Type your question or provide your details…") or quick_msg

    with chat_container:
        # ── Welcome screen (shown only when no messages exist) ────────────────
        if not st.session_state.messages and not prompt:
            st.markdown("""
            Welcome to **LoanAssist** — your pre-qualification assistant for Personal Loans
            and Home Loans.

            I will guide you through providing the required information and give you a
            policy-backed eligibility assessment.

            > ⚠️ This is a **preliminary assessment only**. It does not constitute
            > an offer or final approval.
            """)

            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**🏠 Home Loan**")
                if st.button("I want to check my home loan eligibility", key="welcome_hl_btn"):
                    st.session_state["_quick_msg"] = "I want to apply for a home loan"
                    st.rerun()
            with col_b:
                st.markdown("**💳 Personal Loan**")
                if st.button("I want to check my personal loan eligibility", key="welcome_pl_btn"):
                    st.session_state["_quick_msg"] = "I want to apply for a personal loan"
                    st.rerun()

        # ── Conversation history ──────────────────────────────────────────────
        for msg in st.session_state.messages:
            role = msg["role"]
            with st.chat_message(role):
                st.markdown(msg["content"])
                if role == "assistant":
                    decision = msg.get("decision")
                    if decision:
                        icon, kind, label = DECISION_CONFIG.get(decision, ("?", "info", decision))
                        container = st.container()
                        if kind == "success":    container.success(f"{icon} {label}")
                        elif kind == "error":    container.error(f"{icon} {label}")
                        elif kind == "warning":  container.warning(f"{icon} {label}")
                        else:                    container.info(f"{icon} {label}")
                    cits = msg.get("citations", [])
                    if cits:
                        with st.expander(f"📚 Policy sources ({len(cits)} clauses)"):
                            for i, c in enumerate(cits, 1):
                                st.markdown(
                                    f"**{i}. [{c.get('rule_id', '—')}]** "
                                    f"{c.get('doc', '')} (v{c.get('version', '?')})"
                                )
                                st.caption(c.get("text", "")[:250])

        # ── Active streaming of current prompt inside the container ───────────
        if prompt:
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                placeholder     = st.empty()
                full_text       = ""
                decision_data   = None
                calcs_data      = None
                citations_list  = []
                profile_data    = {}
                missing_fields  = []
                app_id          = None
                error_shown     = False
                start_ts        = time.perf_counter()

                try:
                    headers = {
                        "Content-Type": "application/json",
                        "X-API-Key":    API_KEY,
                        "X-Session-Id": st.session_state.session_id,
                    }
                    body = {
                        "session_id": st.session_state.session_id,
                        "message":    prompt,
                    }

                    with requests.post(
                        f"{API_URL}/chat",
                        json=body,
                        headers=headers,
                        stream=True,
                        timeout=120,
                    ) as resp:
                        resp.raise_for_status()

                        for raw in resp.iter_lines():
                            if not raw:
                                continue
                            if isinstance(raw, bytes):
                                raw = raw.decode("utf-8")
                            if not raw.startswith("data: "):
                                continue
                            try:
                                event = json.loads(raw[6:])
                            except json.JSONDecodeError:
                                continue

                            etype = event.get("type", "")

                            if etype == "token":
                                full_text += event.get("content", "")
                                placeholder.markdown(full_text + "▌")

                            elif etype == "profile_update":
                                profile_data  = event.get("profile", {})
                                missing_fields = event.get("missing_fields", [])

                            elif etype == "decision":
                                decision_data = event.get("data", {})

                            elif etype == "calculations":
                                calcs_data = event.get("data", {})

                            elif etype == "citations":
                                citations_list = event.get("sources", [])

                            elif etype == "audit_ref":
                                app_id = event.get("application_id")

                            elif etype == "error":
                                st.error(event.get("content", "An error occurred."))
                                error_shown = True

                except requests.exceptions.ConnectionError:
                    st.error(f"Cannot connect to LoanAssist at {API_URL}.")
                    error_shown = True
                except requests.exceptions.Timeout:
                    st.error("Request timed out. Please try again.")
                    error_shown = True
                except requests.exceptions.HTTPError as exc:
                    status = exc.response.status_code if exc.response else "?"
                    try:
                        detail = exc.response.json().get("detail", exc.response.text)
                    except Exception:
                        detail = str(exc)
                    st.error(f"API error {status}: {detail}")
                    error_shown = True
                except Exception as exc:
                    st.error(f"Unexpected error: {exc}")
                    error_shown = True

                elapsed = time.perf_counter() - start_ts

                if not error_shown:
                    placeholder.markdown(full_text or "*(No response text)*")

                    if decision_data:
                        dec = decision_data.get("decision", "")
                        icon, kind, label = DECISION_CONFIG.get(dec, ("?", "info", dec))
                        if kind == "success":    st.success(f"{icon} **{label}**")
                        elif kind == "error":    st.error(f"{icon} **{label}**")
                        elif kind == "warning":  st.warning(f"{icon} **{label}**")
                        else:                    st.info(f"{icon} **{label}**")

                    if citations_list:
                        with st.expander(f"📚 Policy sources ({len(citations_list)} clauses)"):
                            for i, c in enumerate(citations_list, 1):
                                st.markdown(
                                    f"**{i}. [{c.get('rule_id', '—')}]** "
                                    f"{c.get('doc', '')} (v{c.get('version', '?')})"
                                )
                                st.caption(c.get("text", "")[:250])

                    st.caption(f"⏱ {elapsed:.1f}s" + (f" · 📋 {app_id}" if app_id else ""))

                if profile_data:
                    st.session_state.last_profile   = profile_data
                    st.session_state.missing_fields = missing_fields
                if decision_data:
                    st.session_state.last_decision = decision_data
                if calcs_data:
                    st.session_state.last_calcs    = calcs_data
                if citations_list:
                    st.session_state.last_citations = citations_list
                if app_id:
                    st.session_state.last_app_id   = app_id

                st.session_state.messages.append({
                    "role":      "assistant",
                    "content":   full_text,
                    "decision":  decision_data.get("decision") if decision_data else None,
                    "citations": citations_list,
                })

            st.rerun()


# ── right column: decision + audit ───────────────────────────────────────────

with col_info:
    # ── Eligibility card ──────────────────────────────────────────────────────
    if st.session_state.last_decision:
        dec_data = st.session_state.last_decision
        dec      = dec_data.get("decision", "")
        icon, kind, label = DECISION_CONFIG.get(dec, ("?", "info", dec))

        st.subheader("📊 Eligibility Result")
        if kind == "success":   st.success(f"{icon} **{label}**")
        elif kind == "error":   st.error(f"{icon} **{label}**")
        elif kind == "warning": st.warning(f"{icon} **{label}**")
        else:                   st.info(f"{icon} **{label}**")

        prod = dec_data.get("product", "")
        if prod:
            st.markdown(f":blue-background[🏷️ **{PRODUCT_LABELS.get(prod, prod)}**]")

        # Rule-by-rule table
        checks = dec_data.get("rule_checks", [])
        if checks:
            st.markdown("**Rule Results**")
            for check in checks:
                result_val = check.get("result", "")
                if result_val == "PASS":
                    st.markdown(
                        f"✅ `{check['rule_id']}` {check['rule_name']}"
                    )
                elif result_val == "FAIL":
                    st.markdown(
                        f"❌ `{check['rule_id']}` {check['rule_name']}"
                    )
                    st.caption(f"   ↳ {check['detail']}")
                elif result_val == "MANUAL_REVIEW":
                    st.markdown(
                        f"⚠️ `{check['rule_id']}` {check['rule_name']}"
                    )
                    st.caption(f"   ↳ {check['detail']}")
                else:
                    st.markdown(
                        f"⏳ `{check['rule_id']}` {check['rule_name']} — not evaluated"
                    )

        # Why not eligible section
        failed = dec_data.get("failed_rules", [])
        if failed:
            st.divider()
            st.markdown("**❓ Why am I not eligible?**")
            for r in failed:
                with st.expander(f"[{r['rule_id']}] {r['rule_name']}"):
                    st.write(r["detail"])

        # Missing fields
        missing = dec_data.get("missing_fields", [])
        if missing:
            st.divider()
            st.info(
                "**Still needed:**\n" +
                "\n".join(f"• {f.replace('_', ' ')}" for f in missing)
            )

    # ── Document checklist ────────────────────────────────────────────────────
    loan_type = st.session_state.last_profile.get("loan_type")
    if loan_type and loan_type in DOC_CHECKLISTS:
        st.divider()
        st.subheader("📁 Document Checklist")
        st.markdown(f":blue-background[🏷️ **{PRODUCT_LABELS.get(loan_type, loan_type)}**]")
        for doc in DOC_CHECKLISTS[loan_type]:
            st.checkbox(doc, key=f"doc_{doc[:20]}")

    # ── Policy version comparison ─────────────────────────────────────────────
    st.divider()
    with st.expander("📜 Policy Version Comparison (Personal Loan v1 vs v2)"):
        st.markdown("""
        | Criterion | Policy v1 | Policy v2 (current) |
        |:---|:---|:---|
        | Minimum income | ₹25,000/month | ₹22,000/month |
        | Minimum credit score | 700 | 680 |
        | Maximum FOIR | 50% | 55% |
        | Maximum loan amount | ₹25,00,000 | ₹30,00,000 |
        | Effective from | 2026-01-01 | 2026-07-01 |

        *v2 was issued to extend credit access to a broader applicant base.*
        """)

    # ── Audit trail ───────────────────────────────────────────────────────────
    st.divider()
    st.subheader("🔍 Audit Trail")
    audit_input = st.text_input(
        "Application ID",
        value=st.session_state.last_app_id or "",
        placeholder="APP-XXXXXXXXXXXX",
    )
    if st.button("🔍 View Audit Record") and audit_input:
        try:
            r = requests.get(
                f"{API_URL}/audit/{audit_input}",
                headers={"X-API-Key": API_KEY},
                timeout=10,
            )
            if r.status_code == 200:
                record = r.json()
                st.json(record)
            elif r.status_code == 404:
                st.warning("Audit record not found.")
            else:
                st.error(f"Error {r.status_code}: {r.text}")
        except Exception as exc:
            st.error(f"Could not retrieve audit: {exc}")

    if st.session_state.last_app_id:
        st.caption(f"Last application: `{st.session_state.last_app_id}`")