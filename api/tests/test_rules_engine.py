"""test_rules_engine.py — unit tests for the deterministic eligibility rules engine."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from calculators import LoanCalculations, run_all_calculators
from rules_engine import (
    ApplicantProfile,
    Decision,
    EligibilityResult,
    RuleResult,
    evaluate_home_loan,
    evaluate_personal_loan,
    run_rules_engine,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _pl_calcs(profile: ApplicantProfile) -> LoanCalculations:
    from rules_engine import PERSONAL_LOAN_CONFIG as cfg
    return run_all_calculators(
        profile.requested_amount, profile.monthly_net_income, profile.existing_emi,
        profile.requested_tenure_months, cfg["ANNUAL_RATE_PCT"], cfg["MAX_FOIR"],
    )


def _hl_calcs(profile: ApplicantProfile) -> LoanCalculations:
    from rules_engine import HOME_LOAN_CONFIG as cfg
    return run_all_calculators(
        profile.requested_amount, profile.monthly_net_income, profile.existing_emi,
        profile.requested_tenure_months, cfg["ANNUAL_RATE_PCT"], cfg["MAX_FOIR"],
        property_value=profile.property_value,
    )


def _full_pl_profile(**overrides) -> ApplicantProfile:
    """Return a fully-eligible personal loan profile with optional overrides."""
    base = dict(
        loan_type="personal_loan",
        age=35,
        employment_type="salaried",
        monthly_net_income=60_000,
        employment_duration_months=24,
        credit_score=750,
        existing_emi=5_000,
        requested_amount=5_00_000,
        requested_tenure_months=36,
    )
    base.update(overrides)
    return ApplicantProfile(**base)


def _full_hl_profile(**overrides) -> ApplicantProfile:
    """Return a fully-eligible home loan profile with optional overrides."""
    base = dict(
        loan_type="home_loan",
        age=35,
        employment_type="salaried",
        monthly_net_income=1_00_000,
        employment_duration_months=36,
        credit_score=760,
        existing_emi=10_000,
        requested_amount=40_00_000,
        requested_tenure_months=240,
        property_value=50_00_000,
        down_payment=10_00_000,
        property_type="apartment",
        property_location="Mumbai",
    )
    base.update(overrides)
    return ApplicantProfile(**base)


# ── Personal Loan — eligible ──────────────────────────────────────────────────

class TestPersonalLoanEligible:
    def test_fully_eligible_profile(self):
        p = _full_pl_profile()
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.POTENTIALLY_ELIGIBLE
        assert not result.failed_rules
        assert not result.missing_fields

    def test_all_rules_pass(self):
        p = _full_pl_profile()
        result = evaluate_personal_loan(p, _pl_calcs(p))
        pass_ids = {r.rule_id for r in result.rule_checks if r.result == RuleResult.PASS}
        expected = {"PL-AGE-001", "PL-INC-001", "PL-EMP-001", "PL-CRD-001",
                    "PL-FOIR-001", "PL-AMT-001", "PL-TEN-001"}
        assert expected.issubset(pass_ids)

    def test_boundary_age_21(self):
        p = _full_pl_profile(age=21)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.POTENTIALLY_ELIGIBLE

    def test_boundary_age_60(self):
        p = _full_pl_profile(age=60)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.POTENTIALLY_ELIGIBLE

    def test_boundary_credit_700(self):
        p = _full_pl_profile(credit_score=700)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.POTENTIALLY_ELIGIBLE

    def test_self_employed_eligible(self):
        p = _full_pl_profile(employment_type="self_employed", employment_duration_months=30)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.POTENTIALLY_ELIGIBLE


# ── Personal Loan — not eligible ──────────────────────────────────────────────

class TestPersonalLoanNotEligible:
    def test_age_too_young(self):
        p = _full_pl_profile(age=20)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE
        assert any(r.rule_id == "PL-AGE-001" for r in result.failed_rules)

    def test_age_too_old(self):
        p = _full_pl_profile(age=61)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE

    def test_income_too_low(self):
        p = _full_pl_profile(monthly_net_income=20_000)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE
        assert any(r.rule_id == "PL-INC-001" for r in result.failed_rules)

    def test_credit_score_too_low(self):
        p = _full_pl_profile(credit_score=680)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE
        assert any(r.rule_id == "PL-CRD-001" for r in result.failed_rules)

    def test_foir_too_high(self):
        """High existing EMI + new EMI > 50% FOIR"""
        p = _full_pl_profile(existing_emi=25_000, monthly_net_income=40_000)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE
        assert any(r.rule_id == "PL-FOIR-001" for r in result.failed_rules)

    def test_loan_amount_too_high(self):
        p = _full_pl_profile(requested_amount=30_00_000)  # > 25L max
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE
        assert any(r.rule_id == "PL-AMT-001" for r in result.failed_rules)

    def test_tenure_too_long(self):
        p = _full_pl_profile(requested_tenure_months=72)  # > 60 months
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE
        assert any(r.rule_id == "PL-TEN-001" for r in result.failed_rules)

    def test_salaried_employment_too_short(self):
        p = _full_pl_profile(employment_type="salaried", employment_duration_months=6)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE
        assert any(r.rule_id == "PL-EMP-001" for r in result.failed_rules)

    def test_multiple_failures(self):
        """Both age and income fail → NOT_ELIGIBLE"""
        p = _full_pl_profile(age=20, monthly_net_income=15_000)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE
        assert len(result.failed_rules) >= 2

    def test_income_multiplier_cap(self):
        """Loan > 30× monthly income fails"""
        p = _full_pl_profile(monthly_net_income=30_000, requested_amount=10_00_000)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE


# ── Personal Loan — insufficient information ──────────────────────────────────

class TestPersonalLoanInsufficient:
    def test_missing_age(self):
        p = _full_pl_profile(age=None)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.INSUFFICIENT_INFORMATION
        assert "age" in result.missing_fields

    def test_missing_income(self):
        p = _full_pl_profile(monthly_net_income=None)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.INSUFFICIENT_INFORMATION

    def test_missing_multiple_fields(self):
        p = ApplicantProfile(loan_type="personal_loan", age=35)
        result = evaluate_personal_loan(p, _pl_calcs(p))
        assert result.decision == Decision.INSUFFICIENT_INFORMATION
        assert len(result.missing_fields) > 3


# ── Home Loan — eligible ──────────────────────────────────────────────────────

class TestHomeLoanEligible:
    def test_fully_eligible_profile(self):
        p = _full_hl_profile()
        result = evaluate_home_loan(p, _hl_calcs(p))
        assert result.decision == Decision.POTENTIALLY_ELIGIBLE

    def test_boundary_ltv_80pct(self):
        """LTV exactly at 80% (high-value loan > 30L)"""
        p = _full_hl_profile(requested_amount=40_00_000, property_value=50_00_000)
        result = evaluate_home_loan(p, _hl_calcs(p))
        # LTV = 40/50 = 80% → exactly at limit → PASS
        assert result.decision == Decision.POTENTIALLY_ELIGIBLE

    def test_boundary_age_21(self):
        p = _full_hl_profile(age=21, requested_tenure_months=240)  # closes at 41
        result = evaluate_home_loan(p, _hl_calcs(p))
        assert result.decision == Decision.POTENTIALLY_ELIGIBLE

    def test_villa_property(self):
        p = _full_hl_profile(property_type="villa")
        result = evaluate_home_loan(p, _hl_calcs(p))
        assert result.decision == Decision.POTENTIALLY_ELIGIBLE

    def test_residential_house_property(self):
        p = _full_hl_profile(property_type="residential_house")
        result = evaluate_home_loan(p, _hl_calcs(p))
        assert result.decision == Decision.POTENTIALLY_ELIGIBLE


# ── Home Loan — not eligible ──────────────────────────────────────────────────

class TestHomeLoanNotEligible:
    def test_ltv_too_high(self):
        """LTV > 80% for high-value loan"""
        p = _full_hl_profile(requested_amount=45_00_000, property_value=50_00_000)
        result = evaluate_home_loan(p, _hl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE
        assert any(r.rule_id == "HL-LTV-001" for r in result.failed_rules)

    def test_commercial_property_ineligible(self):
        p = _full_hl_profile(property_type="commercial")
        result = evaluate_home_loan(p, _hl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE
        assert any(r.rule_id == "HL-PROP-001" for r in result.failed_rules)

    def test_plot_ineligible(self):
        p = _full_hl_profile(property_type="plot")
        result = evaluate_home_loan(p, _hl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE

    def test_age_loan_closes_after_70(self):
        """Age 50, tenure 360m → closes at 80 → FAIL"""
        p = _full_hl_profile(age=50, requested_tenure_months=360)
        result = evaluate_home_loan(p, _hl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE
        assert any(r.rule_id == "HL-AGE-001" for r in result.failed_rules)

    def test_income_too_low(self):
        p = _full_hl_profile(monthly_net_income=30_000)
        result = evaluate_home_loan(p, _hl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE

    def test_credit_too_low(self):
        p = _full_hl_profile(credit_score=680)
        result = evaluate_home_loan(p, _hl_calcs(p))
        assert result.decision == Decision.NOT_ELIGIBLE


# ── Home Loan — manual review ─────────────────────────────────────────────────

class TestHomeLoanManualReview:
    def test_self_employed_24_35_months(self):
        """Self-employed 24–35 months → MANUAL_REVIEW on employment rule"""
        p = _full_hl_profile(employment_type="self_employed", employment_duration_months=30)
        result = evaluate_home_loan(p, _hl_calcs(p))
        assert result.decision == Decision.MANUAL_REVIEW
        assert any(r.rule_id == "HL-EMP-001" for r in result.manual_review_rules)

    def test_self_employed_exactly_24_months(self):
        p = _full_hl_profile(employment_type="self_employed", employment_duration_months=24)
        result = evaluate_home_loan(p, _hl_calcs(p))
        assert result.decision == Decision.MANUAL_REVIEW


# ── run_rules_engine dispatcher ───────────────────────────────────────────────

class TestDispatcher:
    def test_dispatches_to_personal_loan(self):
        p = _full_pl_profile()
        result = run_rules_engine(p, _pl_calcs(p))
        assert result.product == "personal_loan"

    def test_dispatches_to_home_loan(self):
        p = _full_hl_profile()
        result = run_rules_engine(p, _hl_calcs(p))
        assert result.product == "home_loan"

    def test_unknown_loan_type(self):
        p = ApplicantProfile()  # loan_type=None
        result = run_rules_engine(p, LoanCalculations())
        assert result.decision == Decision.INSUFFICIENT_INFORMATION
        assert "loan_type" in result.missing_fields


# ── to_dict serialisation ─────────────────────────────────────────────────────

class TestSerialization:
    def test_eligible_result_to_dict(self):
        p = _full_pl_profile()
        result = evaluate_personal_loan(p, _pl_calcs(p))
        d = result.to_dict()
        assert d["decision"] == "POTENTIALLY_ELIGIBLE"
        assert "rule_checks" in d
        assert "failed_rules" in d
        assert all("rule_id" in r for r in d["rule_checks"])

    def test_profile_to_dict_excludes_none(self):
        p = ApplicantProfile(loan_type="personal_loan", age=35)
        d = p.to_dict()
        assert "age" in d
        assert "monthly_net_income" not in d  # None excluded
