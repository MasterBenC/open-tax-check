import unittest

from open_tax_check.mapping import map_headers, propose_mapping, required_fields


class HeaderMappingTests(unittest.TestCase):
    def test_trial_balance_split_columns_map_without_conflict(self):
        proposal = propose_mapping(
            "trial_balance",
            [
                "科目编码",
                "科目名称",
                "期初余额|借方",
                "期初余额|贷方",
                "本期发生额|借方",
                "本期发生额|贷方",
                "期末余额|借方",
                "期末余额|贷方",
            ],
        )

        self.assertEqual(proposal.mapping["本期发生额|借方"], "period_debit")
        self.assertEqual(proposal.missing_required, ())
        self.assertEqual(proposal.conflicts, {})
        self.assertTrue(proposal.ready_for_confirmation)

    def test_unknown_header_requires_explicit_exclusion(self):
        proposal = propose_mapping(
            "journal",
            ["凭证号", "日期", "科目名称", "借方", "贷方", "内部标签"],
        )

        self.assertEqual(proposal.unresolved, ("内部标签",))
        self.assertFalse(proposal.ready_for_confirmation)

    def test_required_fields_are_role_specific_and_immutable(self):
        self.assertEqual(
            required_fields("journal"),
            frozenset({"voucher_no", "date", "account_name", "debit", "credit"}),
        )
        self.assertIn("opening_debit", required_fields("trial_balance"))
        with self.assertRaisesRegex(ValueError, "unknown_mapping_role"):
            required_fields("unknown")

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
