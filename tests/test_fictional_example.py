import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "examples/fictional-company/input/journal.csv"
EXPECTED_JOURNAL_ROWS = [
    ["凭证号", "记账日期", "科目编码", "科目名称", "业务说明", "借方发生额", "贷方发生额"],
    ["记-001", "2025-01-02", "1002", "银行存款", "确认销售收入", "1000000", "0"],
    ["记-001", "2025-01-02", "6001", "主营业务收入", "确认销售收入", "0", "1000000"],
    ["记-002", "2025-03-08", "6602", "管理费用-业务招待费", "客户接待", "20000", "0"],
    ["记-002", "2025-03-08", "1002", "银行存款", "客户接待", "0", "20000"],
    ["记-003", "2025-09-18", "6602", "管理费用-办公费", "购买中秋礼品及高档茶叶", "5000", "0"],
    ["记-003", "2025-09-18", "1002", "银行存款", "购买中秋礼品及高档茶叶", "0", "5000"],
]


class FictionalExampleTests(unittest.TestCase):
    def test_mapping_payloads_use_preset_reviewer_mappings_not_proposals(self):
        """Changing proposal heuristics must not change the fictional review decision."""
        from scripts.run_fictional_example import _mapping_payloads

        proposals = {
            "proposals": [
                {
                    "role": "journal",
                    "source_id": "runtime-journal-source",
                    "sheet_id": "sheet-runtime-1",
                    "header_rows": [7],
                    "mapping": {"proposal-only": "wrong"},
                    "composites": {"proposal-only": {"wrong": "wrong"}},
                },
                {
                    "role": "trial_balance",
                    "source_id": "runtime-tb-source",
                    "sheet_id": "sheet-runtime-2",
                    "header_rows": [9],
                    "mapping": {"proposal-only": "wrong"},
                    "composites": {"proposal-only": {"wrong": "wrong"}},
                },
            ]
        }
        expected = {
            "journal": {
                "凭证号": "voucher_no",
                "记账日期": "date",
                "科目编码": "account_code",
                "科目名称": "account_name",
                "业务说明": "summary",
                "借方发生额": "debit",
                "贷方发生额": "credit",
            },
            "trial_balance": {
                "科目编码": "account_code",
                "科目名称": "account_name",
                "期初借方": "opening_debit",
                "期初贷方": "opening_credit",
                "本期借方": "period_debit",
                "本期贷方": "period_credit",
                "期末借方": "closing_debit",
                "期末贷方": "closing_credit",
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            (case / "work").mkdir()
            (case / "work/mapping_review.json").write_text(
                json.dumps(proposals), encoding="utf-8"
            )

            payloads = _mapping_payloads(case)

        self.assertEqual({item["role"] for item in payloads}, set(expected))
        for payload in payloads:
            self.assertEqual(payload["mapping"], expected[payload["role"]])
            self.assertEqual(payload["composites"], {})
            self.assertEqual(payload["exclusions"], {})
            self.assertEqual(payload["reviewer"], "host:fictional-example")
            self.assertTrue(payload["source_id"].startswith("runtime-"))

    def test_journal_matches_documented_balanced_fictional_case(self):
        with JOURNAL.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))

        self.assertEqual(rows, EXPECTED_JOURNAL_ROWS)
        vouchers = {}
        for row in rows[1:]:
            debit, credit = vouchers.setdefault(row[0], [0, 0])
            vouchers[row[0]] = [debit + int(row[5]), credit + int(row[6])]
        self.assertTrue(all(debit == credit for debit, credit in vouchers.values()))

    def test_example_readme_declares_complete_fictional_and_license_boundary(self):
        text = (ROOT / "examples/fictional-company/README.md").read_text(encoding="utf-8")

        self.assertIn("公司、交易、凭证、金额、人物和结论均为完全虚构", text)
        self.assertIn("不包含任何真实客户信息", text)
        self.assertIn("随本仓库按 Apache License 2.0 许可", text)
        self.assertIn("`--output` 必须指向不存在或空目录", text)

    def test_fictional_excel_and_csv_findings_match(self):
        """Removing Excel mapping confirmation or changing its input changes results."""
        from scripts.run_fictional_example import run_fictional_case

        csv_result = run_fictional_case(format="csv")
        excel_result = run_fictional_case(format="excel")

        self.assertEqual(csv_result.rule_amounts, excel_result.rule_amounts)
        self.assertEqual(csv_result.rule_amounts, {"CIT-BE-001": "15000.00"})
        self.assertEqual(excel_result.status, "needs_professional_review")
        self.assertTrue(excel_result.trial_balance_passed)
        self.assertTrue(excel_result.journal_to_trial_balance_passed)

    def test_example_generates_and_runs_fictional_xlsx_with_confirmed_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_before = JOURNAL.read_bytes()
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_fictional_example.py"), "--output", temp_dir],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            generated = Path(temp_dir) / "input/journal.xlsx"
            self.assertTrue(generated.is_file())
            self.assertTrue((Path(temp_dir) / "input/trial-balance.xlsx").is_file())
            for filename in (
                "balance-sheet.txt",
                "income-statement.txt",
                "vat-return.txt",
                "cit-return.txt",
            ):
                self.assertTrue((Path(temp_dir) / "input" / filename).is_file())
            state = json.loads(
                (Path(temp_dir) / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "needs_professional_review")
            self.assertTrue(
                (Path(temp_dir) / "work/workbook_inventory.json").is_file()
            )
            self.assertTrue(
                (Path(temp_dir) / "work/sheet_selection.json").is_file()
            )
            self.assertTrue(
                (Path(temp_dir) / "work/coverage_matrix.json").is_file()
            )
            coverage = json.loads(
                (Path(temp_dir) / "work/coverage_matrix.json").read_text(
                    encoding="utf-8"
                )
            )["items"]
            by_procedure = {item["procedure"]: item for item in coverage}
            self.assertEqual(
                by_procedure["financial_statement_reconciliation"]["status"],
                "present_unparsed",
            )
            self.assertEqual(
                by_procedure["vat_return_reconciliation"]["status"],
                "present_unparsed",
            )
            self.assertEqual(
                by_procedure["cit_return_reconciliation"]["status"],
                "present_unparsed",
            )
            self.assertTrue((Path(temp_dir) / "work/normalized_journal.jsonl").is_file())
            self.assertTrue((Path(temp_dir) / "work/normalized_trial_balance.jsonl").is_file())
            self.assertTrue((Path(temp_dir) / "work/reconciliation.json").is_file())
            self.assertTrue((Path(temp_dir) / "output/findings.json").is_file())
            self.assertTrue((Path(temp_dir) / "output/report.md").is_file())
            confirmations = json.loads(
                (Path(temp_dir) / "reviews/mapping_review.json").read_text(
                    encoding="utf-8"
                )
            )["confirmations"]
            journal = next(item for item in confirmations if item["role"] == "journal")
            self.assertEqual(
                journal["mapping"]["业务说明"],
                "summary",
            )
            self.assertEqual(journal["composites"], {})
            self.assertEqual(journal["exclusions"], {})
            self.assertEqual(journal["reviewer"], "host:fictional-example")
            self.assertEqual(JOURNAL.read_bytes(), source_before)

    def test_example_emits_utf8_when_inherited_encoding_cannot_encode_chinese(self):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "cp1252"
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "case"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/run_fictional_example.py"),
                    "--output",
                    str(output_dir),
                ],
                capture_output=True,
                env=env,
            )

        self.assertEqual(
            proc.returncode,
            0,
            proc.stderr.decode("utf-8", errors="replace"),
        )
        payload = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(payload["entity_name"], "虚构科技有限公司")

    def test_example_refuses_nonempty_output_without_changing_existing_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            state = output_dir / "state.json"
            journal = output_dir / "input/journal.xlsx"
            marker = output_dir / "keep.me"
            journal.parent.mkdir()
            state.write_text("sentinel state", encoding="utf-8")
            journal.write_text("sentinel journal", encoding="utf-8")
            marker.write_text("sentinel marker", encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_fictional_example.py"), "--output", temp_dir],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(proc.returncode, 2)
            self.assertIn("output directory must not exist or must be empty", proc.stderr)
            self.assertEqual(state.read_text(encoding="utf-8"), "sentinel state")
            self.assertEqual(journal.read_text(encoding="utf-8"), "sentinel journal")
            self.assertEqual(marker.read_text(encoding="utf-8"), "sentinel marker")
            self.assertFalse((output_dir / "output").exists())

    def test_root_readme_runs_the_example_without_claiming_it_is_missing(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        section = text.split("## 运行虚构示例", 1)[1].split("## 输出", 1)[0]

        self.assertIn(r".\.venv\Scripts\python.exe scripts\run_fictional_example.py", section)
        self.assertIn("python scripts/run_fictional_example.py", section)
        self.assertIn("coverage_matrix.json", section)
        self.assertIn("normalized_trial_balance.jsonl", section)
        self.assertIn("reconciliation.json", section)
        self.assertIn("needs_professional_review", section)
        self.assertIn("`--output` 必须指向不存在或空目录", section)
        self.assertNotIn("尚未提供", section)


if __name__ == "__main__":
    unittest.main()
