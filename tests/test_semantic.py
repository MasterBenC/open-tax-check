import unittest

from open_tax_check.semantic import detect_semantic_candidates


class SemanticRuleTests(unittest.TestCase):
    def test_keyword_account_mismatch_creates_unreviewed_candidate(self):
        entries = [
            {
                "voucher_no": "记-0045",
                "account_name": "管理费用-办公费",
                "summary": "购买中秋礼品及高档茶叶",
                "debit": "5000",
                "source_file": "journal.xlsx",
                "source_sheet": "序时账",
                "source_row": 46,
            }
        ]

        candidates = detect_semantic_candidates(entries)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["status"], "candidate")
        self.assertEqual(candidates[0]["review_status"], "unreviewed")
        self.assertEqual(candidates[0]["source"]["source_row"], 46)


if __name__ == "__main__":
    unittest.main()
