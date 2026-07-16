import unittest

from open_tax_check.mapping import map_headers


class HeaderMappingTests(unittest.TestCase):
    def test_maps_common_chinese_journal_headers(self):
        result = map_headers(
            ["凭证号", "记账日期", "科目编码", "科目名称", "业务说明", "借方发生额", "贷方发生额"]
        )

        self.assertEqual(result["mapping"]["业务说明"], "summary")
        self.assertEqual(result["mapping"]["借方发生额"], "debit")
        self.assertEqual(result["mapping"]["贷方发生额"], "credit")
        self.assertEqual(result["unresolved"], [])

    def test_unknown_required_header_blocks_automatic_calculation(self):
        result = map_headers(["凭证号", "日期", "神秘金额列"])

        self.assertFalse(result["ready_for_calculation"])
        self.assertIn("神秘金额列", result["unresolved"])
        self.assertIn("debit", result["missing_required"])
        self.assertIn("credit", result["missing_required"])

    def test_duplicate_aliases_for_one_canonical_field_require_review(self):
        result = map_headers(
            ["凭证号", "日期", "科目名称", "借方", "借方发生额", "贷方"]
        )

        self.assertFalse(result["ready_for_calculation"])
        self.assertEqual(result["conflicts"], {"debit": ["借方", "借方发生额"]})
        self.assertEqual(result["unresolved"], [])
        self.assertEqual(result["missing_required"], [])

    def test_maps_posting_voucher_number_alias(self):
        result = map_headers(["记账凭证号", "日期", "科目名称", "借方", "贷方"])

        self.assertTrue(result["ready_for_calculation"])
        self.assertEqual(result["mapping"]["记账凭证号"], "voucher_no")
        self.assertEqual(result["conflicts"], {})


if __name__ == "__main__":
    unittest.main()
