import unittest

from open_tax_check.quality import validate_voucher_balance


class DataQualityTests(unittest.TestCase):
    def test_reports_unbalanced_voucher_with_source_rows(self):
        rows = [
            {"voucher_no": "记-001", "debit": "100.00", "credit": "0", "source_row": 2},
            {"voucher_no": "记-001", "debit": "0", "credit": "100.00", "source_row": 3},
            {"voucher_no": "记-002", "debit": "80", "credit": "0", "source_row": 4},
            {"voucher_no": "记-002", "debit": "0", "credit": "70", "source_row": 5},
        ]

        findings = validate_voucher_balance(rows)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["voucher_no"], "记-002")
        self.assertEqual(findings[0]["difference"], "10.00")
        self.assertEqual(findings[0]["source_rows"], [4, 5])


if __name__ == "__main__":
    unittest.main()
