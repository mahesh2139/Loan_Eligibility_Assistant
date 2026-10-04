"""test_remediation.py -- unit tests for deterministic adverse-action remediation."""
import sys
from pathlib import Path

# Allow importing from api/ without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from calculators import (
    calculate_remediation_options,
    calculate_tenure_for_target_emi,
    calculate_emi,
    run_all_calculators,
)
from rules_engine import (
    ApplicantProfile,
    Decision,
    run_rules_engine,
    HOME_LOAN_CONFIG,
    PERSONAL_LOAN_CONFIG,
    AUTO_LOAN_CONFIG,
)


class TestRemediationCalculators:
    def test_tenure_for_target_emi(self):
        # 10 Lakhs at 10.5% annual rate
        # Suppose target EMI is 25,000
        tenure = calculate_tenure_for_target_emi(1_000_000, 10.5, 25_000)
        assert tenure is not None
        # Verify EMI at this tenure is <= 25,000
        actual_emi = calculate_emi(1_000_000, 10.5, tenure)
        assert actual_emi <= 25_000

    def test_tenure_impossible_if_emi_less_than_interest(self):
        # Monthly interest on 10 Lakhs at 12% is 10,000.
        # If target EMI is 8,000, loan cannot amortize.
        tenure = calculate_tenure_for_target_emi(1_000_000, 12.0, 8_000)
        assert tenure is None

    def test_foir_remediation_suggests_lower_amount(self):
        # Income 30,000, max FOIR 50% -> max EMI 15,000.
        # Asking for 15 Lakhs on 36 months PL (EMI ~49,821 > 15,000)
        advice = calculate_remediation_options(
            requested_amount=1_500_000,
            monthly_income=30_000,
            existing_emi=0,
            tenure_months=36,
            annual_rate_pct=12.0,
            max_foir=0.50,
            max_tenure_months=60,
        )
        assert advice.feasible is True
        assert advice.max_affordable_loan is not None
        assert advice.max_affordable_loan < 1_500_000
        assert any("Reduce requested loan" in note for note in advice.remediation_notes)

    def test_ltv_remediation_suggests_higher_down_payment(self):
        # Collateral 50 Lakhs, requested 45 Lakhs -> LTV 90%
        # Policy max LTV is 80% (40 Lakhs max loan)
        advice = calculate_remediation_options(
            requested_amount=4_500_000,
            monthly_income=200_000,
            existing_emi=0,
            tenure_months=240,
            annual_rate_pct=8.5,
            max_foir=0.55,
            max_tenure_months=360,
            property_value=5_000_000,
            max_ltv=0.80,
        )
        assert advice.feasible is True
        assert advice.suggested_down_payment == pytest.approx(500_000, abs=1000)
        assert any("Increase down payment" in note for note in advice.remediation_notes)

    def test_co_applicant_income_suggestion(self):
        # Applicant income 25k, wants loan requiring 40k income
        advice = calculate_remediation_options(
            requested_amount=1_000_000,
            monthly_income=25_000,
            existing_emi=0,
            tenure_months=36,
            annual_rate_pct=12.0,
            max_foir=0.50,
            max_tenure_months=60,
        )
        assert advice.suggested_co_applicant_income is not None
        assert advice.suggested_co_applicant_income > 0
        assert any("co-borrower" in note for note in advice.remediation_notes)


class TestRulesEngineRemediationIntegration:
    def test_ineligible_hl_gets_remediation_advice(self):
        # Home loan with FOIR failure (low income for 80L loan)
        p = ApplicantProfile(
            loan_type="home_loan",
            age=32,
            employment_type="salaried",
            employment_duration_months=36,
            monthly_net_income=50_000,
            credit_score=750,
            existing_emi=5_000,
            requested_amount=8_000_000,
            requested_tenure_months=240,
            property_value=10_000_000,
            down_payment=2_000_000,
            property_type="apartment",
        )
        calcs = run_all_calculators(
            p.requested_amount,
            p.monthly_net_income,
            p.existing_emi,
            p.requested_tenure_months,
            HOME_LOAN_CONFIG["ANNUAL_RATE_PCT"],
            HOME_LOAN_CONFIG["MAX_FOIR"],
            property_value=p.property_value,
        )
        result = run_rules_engine(p, calcs)
        assert result.decision == Decision.NOT_ELIGIBLE
        assert result.remediation is not None
        assert len(result.remediation_notes) > 0
        # Check that remediation dictionary is populated
        d = result.to_dict()
        assert "remediation" in d
        assert "remediation_notes" in d

    def test_eligible_case_has_empty_remediation_notes(self):
        # Perfect profile
        p = ApplicantProfile(
            loan_type="home_loan",
            age=32,
            employment_type="salaried",
            employment_duration_months=36,
            monthly_net_income=150_000,
            credit_score=780,
            existing_emi=0,
            requested_amount=3_000_000,
            requested_tenure_months=240,
            property_value=5_000_000,
            down_payment=2_000_000,
            property_type="apartment",
        )
        calcs = run_all_calculators(
            p.requested_amount,
            p.monthly_net_income,
            p.existing_emi,
            p.requested_tenure_months,
            HOME_LOAN_CONFIG["ANNUAL_RATE_PCT"],
            HOME_LOAN_CONFIG["MAX_FOIR"],
            property_value=p.property_value,
        )
        result = run_rules_engine(p, calcs)
        assert result.decision == Decision.POTENTIALLY_ELIGIBLE
        assert result.remediation is None
        assert len(result.remediation_notes) == 0
