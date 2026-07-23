import unittest

from open_tax_check.schemas import RoleScore
from open_tax_check.sheet_detection import (
    SheetDetection,
    confidence_band,
    detect_rows,
    detect_sheet,
    normalize_header,
)
from open_tax_check.workbooks import CellValue, SheetMeta, WorkbookReader


JOURNAL_HEADERS = [
    ["凭证号", "日期", "科目名称", "借方", "贷方"],
]


def role_score(result: SheetDetection, role: str) -> RoleScore:
    return next(item for item in result.role_scores if item.role == role)


class RecordingReader(WorkbookReader):
    def __init__(self, rows):
        self.rows = tuple(rows)
        self.calls = []

    def sheets(self):
        raise AssertionError("detect_sheet must not enumerate worksheets")

    def iter_rows(self, sheet_id, min_row, max_row, max_col):
        self.calls.append((sheet_id, min_row, max_row, max_col))
        return iter(self.rows)

    def close(self):
        pass


class HeaderNormalizationTests(unittest.TestCase):
    def test_normalize_header_applies_nfkc_casefold_and_design_punctuation(self):
        self.assertEqual(
            normalize_header(" ＡＢＣ＿ 本期—（借方）【金额】 "),
            "abc本期借方金额",
        )

    def test_confidence_band_uses_documented_boundaries(self):
        self.assertEqual(confidence_band(59), "none")
        self.assertEqual(confidence_band(60), "review")
        self.assertEqual(confidence_band(84), "review")
        self.assertEqual(confidence_band(85), "high")


class HeaderCandidateTests(unittest.TestCase):
    def test_two_row_trial_balance_header_scores_high_confidence(self):
        rows = [
            [
                "科目编码",
                "科目名称",
                "期初余额",
                None,
                "本期发生额",
                None,
                "期末余额",
                None,
            ],
            [
                None,
                None,
                "借方",
                "贷方",
                "借方",
                "贷方",
                "借方",
                "贷方",
            ],
        ]

        result = detect_rows(rows, visibility="visible")

        self.assertGreaterEqual(result.score_for("trial_balance"), 85)
        self.assertEqual(result.header_rows, (1, 2))
        self.assertIn("本期发生额|借方", result.combined_headers)

    def test_amount_and_direction_trial_balance_representation_scores_high(self):
        rows = [[
            "科目名称",
            "期初余额金额",
            "期初余额方向",
            "本期借方发生额",
            "本期贷方发生额",
            "期末余额金额",
            "期末余额方向",
        ]]

        result = detect_rows(rows)

        self.assertGreaterEqual(result.score_for("trial_balance"), 85)
        self.assertTrue(
            result.auto_selectable_for_normalization("trial_balance")
        )

    def test_incomplete_trial_balance_never_scores_high(self):
        rows = [[
            "科目名称",
            "期初余额",
            "本期借方发生额",
            "本期贷方发生额",
            "期末余额",
        ]]

        result = detect_rows(rows)

        self.assertLess(result.score_for("trial_balance"), 85)

    def test_journal_requires_unique_complete_required_fields_for_high_score(self):
        result = detect_rows(JOURNAL_HEADERS)

        self.assertGreaterEqual(result.score_for("journal"), 85)
        self.assertTrue(result.auto_selectable)
        self.assertTrue(result.auto_selectable_for_normalization("journal"))

    def test_duplicate_journal_target_adds_conflict_and_caps_score(self):
        rows = [[
            "凭证号",
            "日期",
            "科目名称",
            "借方",
            "借方发生额",
            "贷方",
        ]]

        result = detect_rows(rows)

        self.assertLess(result.score_for("journal"), 85)
        self.assertIn(
            "conflicting_header_signal",
            role_score(result, "journal").signals,
        )
        self.assertFalse(result.auto_selectable_for_normalization("journal"))

    def test_duplicate_trial_balance_target_caps_score(self):
        rows = [[
            "科目名称",
            "期初借方余额",
            "期初借方余额",
            "期初贷方余额",
            "本期借方发生额",
            "本期贷方发生额",
            "期末借方余额",
            "期末贷方余额",
        ]]

        result = detect_rows(rows)

        self.assertLess(result.score_for("trial_balance"), 85)
        self.assertIn(
            "conflicting_header_signal",
            role_score(result, "trial_balance").signals,
        )

    def test_hidden_high_confidence_sheet_is_never_auto_selectable(self):
        result = detect_rows(JOURNAL_HEADERS, visibility="hidden")

        self.assertGreaterEqual(result.score_for("journal"), 85)
        self.assertFalse(result.auto_selectable)
        self.assertFalse(result.auto_selectable_for_normalization("journal"))

    def test_merged_two_row_headers_expand_only_for_detection(self):
        rows = [
            ["科目名称", "本期发生额", None],
            [None, "借方", "贷方"],
        ]
        original_rows = [list(row) for row in rows]

        result = detect_rows(
            rows,
            merged_ranges=("B1:C1",),
            visibility="visible",
        )

        self.assertIn("本期发生额|借方", result.combined_headers)
        self.assertIn("本期发生额|贷方", result.combined_headers)
        self.assertEqual(rows, original_rows)

    def test_vertical_merge_does_not_repeat_the_same_header_component(self):
        rows = [
            ["科目名称", "本期发生额", None],
            [None, "借方", "贷方"],
        ]

        result = detect_rows(
            rows,
            merged_ranges=("A1:A2", "B1:C1"),
        )

        self.assertIn("科目名称", result.combined_headers)
        self.assertNotIn("科目名称|科目名称", result.combined_headers)

    def test_candidate_tie_prefers_fewer_rows_then_earlier_physical_row(self):
        rows = [
            [],
            JOURNAL_HEADERS[0],
            ["ordinary data"],
            JOURNAL_HEADERS[0],
        ]

        first = detect_rows(rows)
        second = detect_rows(rows)

        self.assertEqual(first.header_rows, (2,))
        self.assertEqual(first, second)

    def test_scan_stops_after_first_one_hundred_nonempty_rows(self):
        rows = [["noise"] for _ in range(100)]
        rows.append(JOURNAL_HEADERS[0])

        result = detect_rows(rows)

        self.assertEqual(result.score_for("journal"), 0)
        self.assertNotIn((101,), result.candidate_header_rows)

    def test_scan_ignores_columns_after_first_two_hundred_fifty_six(self):
        row = ["date", "科目名称", "借方", "贷方"]
        row.extend([None] * 252)
        row.append("凭证号")

        result = detect_rows([row])

        self.assertLess(result.score_for("journal"), 85)
        self.assertEqual(len(row), 257)


class InventoryRoleTests(unittest.TestCase):
    def test_tax_return_signal_is_inventory_only(self):
        result = detect_rows(
            [["增值税纳税申报表", "销项税额", "进项税额"]],
            visibility="visible",
        )

        self.assertGreaterEqual(result.score_for("vat_return"), 60)
        self.assertFalse(
            result.auto_selectable_for_normalization("vat_return")
        )
        self.assertFalse(result.auto_selectable)

    def test_supported_inventory_roles_are_detected_from_headers(self):
        cases = {
            "balance_sheet": [["资产负债表", "资产", "负债和所有者权益"]],
            "income_statement": [["利润表", "营业收入", "利润总额"]],
            "cit_return": [[
                "企业所得税年度纳税申报表",
                "应纳税所得额",
                "应纳所得税额",
            ]],
            "cash_flow_statement": [[
                "现金流量表",
                "经营活动现金流入",
                "现金及现金等价物净增加额",
            ]],
            "chart_of_accounts": [[
                "会计科目表",
                "科目编码",
                "科目名称",
                "科目级次",
            ]],
            "supporting_schedule": [[
                "固定资产明细表",
                "资产原值",
                "累计折旧",
            ]],
        }

        for role, rows in cases.items():
            with self.subTest(role=role):
                result = detect_rows(rows)
                self.assertGreaterEqual(result.score_for(role), 60)
                self.assertFalse(
                    result.auto_selectable_for_normalization(role)
                )


class ReaderIntegrationTests(unittest.TestCase):
    def test_detect_sheet_consumes_only_bounded_iter_rows_cell_values(self):
        values = JOURNAL_HEADERS[0]
        row = tuple(
            CellValue(
                row=1,
                column=index,
                coordinate=f"C{index}",
                value=value,
                formula=None,
                cached_value=None,
                data_type="s",
                value_origin="literal",
            )
            for index, value in enumerate(values, start=1)
        )
        reader = RecordingReader((row,))
        sheet = SheetMeta(
            sheet_id="sheet-0007",
            index=7,
            name="neutral name",
            visibility="visible",
            max_row=500,
            max_column=999,
        )

        result = detect_sheet(reader, sheet)

        self.assertEqual(
            reader.calls,
            [("sheet-0007", 1, 500, 256)],
        )
        self.assertGreaterEqual(result.score_for("journal"), 85)


if __name__ == "__main__":
    unittest.main()
