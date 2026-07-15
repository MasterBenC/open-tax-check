import unittest
from decimal import Decimal

from open_tax_check.rules import (
    business_entertainment_adjustment,
    shareholder_loan_candidate,
)


class HardRuleTests(unittest.TestCase):
    def test_business_entertainment_adjustment_uses_lower_deduction_limit(self):
        result = business_entertainment_adjustment(
            actual_expense=Decimal("100000"),
            revenue=Decimal("10000000"),
        )

        self.assertEqual(result["deductible_amount"], "50000.00")
        self.assertEqual(result["tax_adjustment"], "50000.00")
        self.assertEqual(result["rule_id"], "CIT-BE-001")

    def test_shareholder_loan_is_candidate_not_automatic_conclusion(self):
        result = shareholder_loan_candidate(
            ending_balance=Decimal("1200000"),
            is_individual_shareholder=True,
            outstanding_at_year_end=True,
            business_use_confirmed=False,
        )

        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["scenario_tax_exposure"], "240000.00")
        self.assertIn("business_purpose_evidence", result["required_evidence"])

    def test_business_use_evidence_suppresses_shareholder_loan_candidate(self):
        result = shareholder_loan_candidate(
            ending_balance=Decimal("1200000"),
            is_individual_shareholder=True,
            outstanding_at_year_end=True,
            business_use_confirmed=True,
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
