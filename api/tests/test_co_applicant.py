"""test_co_applicant.py -- unit tests for joint/co-applicant income and obligation pooling."""
import sys
from pathlib import Path

# Allow importing from api/ without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from calculators import run_all_calculators, calculate_foir
from rules_engine import (
    ApplicantProfile,
    Decision,
    RuleResult,
    run_rules_engine,
    HOME_LOAN_CONFIG,
)


class TestCoApplicantRules:
    def test_required_fields_with_co_applicant(self):
        p = ApplicantProfile(loan_type="home_loan", has_co_applicant=True)
        reqs = p.required_fields()
        assert "co_applicant_income" in reqs

    def test_required_fields_without_co_applicant(self):
        p = ApplicantProfile(loan_type="home_loan", has_co_applicant=False)
        reqs = p.required_fields()
        assert "co_applicant_income" not in reqs

    def test_income_rule_passes_via_pooling(self):
        # Home loan requires min income ₹40,000.
        # Primary applicant has ₹30,000 (fails alone).
        # Co-applicant spouse has ₹35,000 (total ₹65,000 -> passes).
        p = ApplicantProfile(
            loan_type="home_loan",
            age=30,
            employment_type="salaried",
            employment_duration_months=36,
            monthly_net_income=30_000,
            credit_score=750,
            existing_emi=0,
            requested_amount=2_000_000,
            requested_tenure_months=240,
            property_value=3_500_000,
            down_payment=1_500_000,
            property_type="apartment",
            has_co_applicant=True,
            co_applicant_income=35_000,
            co_applicant_emi=0,
            co_applicant_relationship="spouse",
        )
        calcs = run_all_calculators(
            p.requested_amount,
            p.monthly_net_income,
            p.existing_emi,
            p.requested_tenure_months,
            HOME_LOAN_CONFIG["ANNUAL_RATE_PCT"],
            HOME_LOAN_CONFIG["MAX_FOIR"],
            property_value=p.property_value,
            co_applicant_income=p.co_applicant_income,
            co_applicant_emi=p.co_applicant_emi,
        )
        result = run_rules_engine(p, calcs)
        
        inc_check = next(c for c in result.rule_checks if c.rule_id == "HL-INC-001")
        assert inc_check.result == RuleResult.PASS
        assert "household ₹65,000" in inc_check.detail
        assert result.decision == Decision.POTENTIALLY_ELIGIBLE

    def test_foir_calculation_pools_incomes_and_obligations(self):
        # Primary income 50,000, existing EMI 10,000
        # Co-applicant income 50,000, existing EMI 15,000
        # Proposed new EMI 20,000
        # Total household income = 100,000
        # Total obligations = 10,000 + 15,000 + 20,000 = 45,000
        # Expected FOIR = 45% (0.45)
        calcs = run_all_calculators(
            requested_amount=2_000_000,
            monthly_income=50_000,
            existing_emi=10_000,
            tenure_months=240,
            annual_rate_pct=8.5,
            max_foir=0.55,
            property_value=4_000_000,
            co_applicant_income=50_000,
            co_applicant_emi=15_000,
        )
        assert calcs.total_household_income == 100_000
        assert calcs.total_household_emi == 25_000
        # Proposed EMI for 20L @ 8.5% for 240m is ~17,356
        # Total EMI = 25,000 + 17,356 = 42,356 -> FOIR ~42.4%
        assert calcs.foir is not None
        assert 0.40 < calcs.foir < 0.45
