"""calculators.py -- deterministic financial calculators for loan eligibility.

All calculations are pure Python arithmetic. No LLM involvement.
Used by the rules engine and returned in the API response for full transparency.

Formulas:
  EMI  = P x r x (1+r)^n / ((1+r)^n - 1)    (reducing-balance)
  FOIR = (existing_emi + new_emi) / monthly_income
  LTV  = loan_amount / collateral_value  (property OR on-road vehicle price)
  max_loan: solve EMI formula for P given max allowed EMI
  tenure: solve EMI formula for n given max allowed EMI
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class LoanCalculations:
    """All calculation results for a loan application.

    None means the field could not be computed (missing inputs).
    """
    emi:                 Optional[float] = None   # monthly EMI in INR
    foir:                Optional[float] = None   # Fixed Obligation to Income Ratio (0-1)
    ltv:                 Optional[float] = None   # Loan-to-Value ratio (0-1)
    max_affordable_loan: Optional[float] = None   # max loan by income + FOIR + tenure
    total_interest:      Optional[float] = None   # total interest paid over tenure
    total_payment:       Optional[float] = None   # principal + total_interest
    total_household_income: Optional[float] = None # primary + co-applicant income
    total_household_emi:    Optional[float] = None # primary + co-applicant existing EMI

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class RemediationAdvice:
    """Actionable alternative pathways when an application is NOT_ELIGIBLE."""
    feasible: bool = False
    max_affordable_loan: Optional[float] = None
    suggested_loan_amount: Optional[float] = None
    suggested_tenure_months: Optional[int] = None
    suggested_down_payment: Optional[float] = None
    suggested_co_applicant_income: Optional[float] = None
    remediation_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feasible": self.feasible,
            "max_affordable_loan": self.max_affordable_loan,
            "suggested_loan_amount": self.suggested_loan_amount,
            "suggested_tenure_months": self.suggested_tenure_months,
            "suggested_down_payment": self.suggested_down_payment,
            "suggested_co_applicant_income": self.suggested_co_applicant_income,
            "remediation_notes": self.remediation_notes,
        }


# ---------------------------------------------------------------------------
# Individual calculators
# ---------------------------------------------------------------------------

def calculate_emi(principal: float, annual_rate_pct: float, tenure_months: int) -> float:
    """Standard reducing-balance EMI.

    EMI = P x r x (1+r)^n / ((1+r)^n - 1)
    where r = monthly rate = annual_rate_pct / (12 x 100)
          n = tenure_months

    Edge cases:
      - principal <= 0  -> 0
      - annual_rate_pct == 0  -> simple division (zero-interest loan)
    """
    if principal <= 0 or tenure_months <= 0:
        return 0.0
    if annual_rate_pct <= 0:
        return round(principal / tenure_months, 2)
    r = annual_rate_pct / (12.0 * 100.0)
    factor = math.pow(1.0 + r, tenure_months)
    emi = principal * r * factor / (factor - 1.0)
    return round(emi, 2)


def calculate_foir(existing_emi: float, new_emi: float, monthly_income: float) -> float:
    """Fixed Obligation to Income Ratio.

    FOIR = (existing_emi + new_emi) / monthly_income

    Returns float 0-inf. Returns float('inf') when monthly_income == 0.
    """
    if monthly_income <= 0:
        return float("inf")
    return round((existing_emi + new_emi) / monthly_income, 4)


def calculate_ltv(loan_amount: float, collateral_value: float) -> float:
    """Loan-to-Value ratio.

    LTV = loan_amount / collateral_value
    Works for both property (home loan) and on-road price (auto loan).

    Returns float 0-inf. Returns float('inf') when collateral_value == 0.
    """
    if collateral_value <= 0:
        return float("inf")
    return round(loan_amount / collateral_value, 4)


def calculate_max_affordable_loan(
    monthly_income: float,
    existing_emi: float,
    max_foir: float,
    annual_rate_pct: float,
    tenure_months: int,
) -> float:
    """Maximum loan amount an applicant can afford given FOIR constraint.

    Step 1: max_allowed_emi = (max_foir x monthly_income) - existing_emi
    Step 2: Solve EMI formula for P:
            P = max_emi x ((1+r)^n - 1) / (r x (1+r)^n)

    Returns 0 when no budget is available.
    """
    if monthly_income <= 0 or tenure_months <= 0:
        return 0.0
    max_allowed_emi = (max_foir * monthly_income) - existing_emi
    if max_allowed_emi <= 0:
        return 0.0
    if annual_rate_pct <= 0:
        return round(max_allowed_emi * tenure_months, 2)
    r = annual_rate_pct / (12.0 * 100.0)
    factor = math.pow(1.0 + r, tenure_months)
    max_loan = max_allowed_emi * (factor - 1.0) / (r * factor)
    return round(max_loan, 2)


def calculate_tenure_for_target_emi(
    principal: float,
    annual_rate_pct: float,
    target_max_emi: float,
) -> Optional[int]:
    """Calculate minimum tenure in months required to bring EMI <= target_max_emi.

    Formula: n = ln(EMI / (EMI - P*r)) / ln(1+r)
    Returns None if target_max_emi <= P*r (EMI cannot even cover monthly interest).
    """
    if principal <= 0 or target_max_emi <= 0:
        return None
    if annual_rate_pct <= 0:
        return math.ceil(principal / target_max_emi)
    r = annual_rate_pct / (12.0 * 100.0)
    monthly_interest = principal * r
    if target_max_emi <= monthly_interest:
        return None  # Loan cannot amortize at this EMI
    ratio = target_max_emi / (target_max_emi - monthly_interest)
    n = math.log(ratio) / math.log(1.0 + r)
    return max(1, math.ceil(n))


# ---------------------------------------------------------------------------
# Remediation Engine -- Actionable Adverse-Action Paths
# ---------------------------------------------------------------------------

def calculate_remediation_options(
    requested_amount:    Optional[float],
    monthly_income:      Optional[float],
    existing_emi:        Optional[float],
    tenure_months:       Optional[int],
    annual_rate_pct:     float,
    max_foir:            float,
    max_tenure_months:   int,
    property_value:      Optional[float] = None,
    on_road_price:       Optional[float] = None,
    max_ltv:             Optional[float] = None,
    co_applicant_income: Optional[float] = None,
    co_applicant_emi:    Optional[float] = None,
) -> RemediationAdvice:
    """Generate deterministic, actionable remediation advice for borderline/failed cases."""
    advice = RemediationAdvice()
    income = (monthly_income or 0.0) + (co_applicant_income or 0.0)
    existing = (existing_emi or 0.0) + (co_applicant_emi or 0.0)
    tenure = tenure_months or max_tenure_months
    collateral = property_value or on_road_price

    if income <= 0:
        advice.remediation_notes.append("Income proof is required to calculate affordability options.")
        return advice

    # 1. Calculate max loan by FOIR at current tenure
    max_foir_loan = calculate_max_affordable_loan(
        income, existing, max_foir, annual_rate_pct, tenure
    )
    advice.max_affordable_loan = max_foir_loan

    # 2. Check if loan amount can be reduced
    if requested_amount and requested_amount > max_foir_loan:
        if max_foir_loan > 0:
            advice.feasible = True
            advice.suggested_loan_amount = round(max_foir_loan, -3)  # round to nearest thousand
            advice.remediation_notes.append(
                f"Reduce requested loan from ₹{requested_amount:,.0f} to ₹{max_foir_loan:,.0f} "
                f"to bring your monthly obligations within the {max_foir*100:.0f}% FOIR limit."
            )

    # 3. Check if extending tenure can rescue the loan amount
    if requested_amount and tenure < max_tenure_months:
        max_allowed_emi = (max_foir * income) - existing
        if max_allowed_emi > 0:
            req_tenure = calculate_tenure_for_target_emi(requested_amount, annual_rate_pct, max_allowed_emi)
            if req_tenure and req_tenure <= max_tenure_months:
                advice.feasible = True
                advice.suggested_tenure_months = req_tenure
                advice.remediation_notes.append(
                    f"Extend loan tenure from {tenure} months to {req_tenure} months "
                    f"(within policy max of {max_tenure_months} months) to lower your monthly EMI."
                )

    # 4. Check LTV remediation (Down payment)
    if collateral and requested_amount and max_ltv:
        max_ltv_loan = collateral * max_ltv
        if requested_amount > max_ltv_loan:
            excess = requested_amount - max_ltv_loan
            advice.feasible = True
            advice.suggested_down_payment = round(excess, -3)
            advice.remediation_notes.append(
                f"Increase down payment by ₹{excess:,.0f} to meet the {max_ltv*100:.0f}% LTV collateral cap."
            )

    # 5. Check co-applicant income pooling recommendation
    if requested_amount and max_foir_loan < requested_amount:
        # What income is needed to support requested_amount?
        req_emi = calculate_emi(requested_amount, annual_rate_pct, tenure)
        # We need (existing + req_emi) / total_income <= max_foir
        # => total_income >= (existing + req_emi) / max_foir
        needed_total_income = (existing + req_emi) / max_foir
        needed_additional_income = max(0.0, needed_total_income - income)
        if needed_additional_income > 0:
            advice.suggested_co_applicant_income = round(needed_additional_income, -3)
            advice.remediation_notes.append(
                f"Add a co-borrower (e.g. spouse/parent) earning at least ₹{needed_additional_income:,.0f}/month "
                f"to combine household income and qualify for ₹{requested_amount:,.0f}."
            )

    return advice


# ---------------------------------------------------------------------------
# Composite runner -- used by app.py
# ---------------------------------------------------------------------------

def run_all_calculators(
    requested_amount:    Optional[float],
    monthly_income:      Optional[float],
    existing_emi:        Optional[float],
    tenure_months:       Optional[int],
    annual_rate_pct:     float,
    max_foir:            float,
    property_value:      Optional[float] = None,
    on_road_price:       Optional[float] = None,
    co_applicant_income: Optional[float] = None,
    co_applicant_emi:    Optional[float] = None,
) -> LoanCalculations:
    """Compute all applicable calculators and return a LoanCalculations object.

    Silently skips any calculation where required inputs are None.
    Supports home loan (property_value) and auto loan (on_road_price) LTV.
    Supports co-applicant income and obligation pooling.
    """
    calcs = LoanCalculations()
    primary_income = monthly_income or 0.0
    co_income = co_applicant_income or 0.0
    total_income = primary_income + co_income

    primary_existing = existing_emi or 0.0
    co_existing = co_applicant_emi or 0.0
    total_existing = primary_existing + co_existing

    if co_income > 0 or co_existing > 0:
        calcs.total_household_income = total_income
        calcs.total_household_emi = total_existing

    if requested_amount and tenure_months:
        emi_val = calculate_emi(requested_amount, annual_rate_pct, tenure_months)
        calcs.emi = emi_val
        calcs.total_payment = round(emi_val * tenure_months, 2)
        calcs.total_interest = round(calcs.total_payment - requested_amount, 2)

        if total_income > 0:
            calcs.foir = calculate_foir(total_existing, emi_val, total_income)

    # LTV: home loan uses property_value; auto loan uses on_road_price
    collateral = property_value or on_road_price
    if collateral and requested_amount:
        calcs.ltv = calculate_ltv(requested_amount, collateral)

    if total_income > 0 and tenure_months:
        calcs.max_affordable_loan = calculate_max_affordable_loan(
            total_income, total_existing, max_foir, annual_rate_pct, tenure_months
        )

    return calcs
