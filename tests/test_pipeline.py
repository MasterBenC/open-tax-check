import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from open_tax_check.cli import build_parser


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "open_tax_check.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class PipelineTests(unittest.TestCase):
    def _init_case(self, root: str, name: str = "case-a") -> Path:
        case_dir = Path(root) / name
        init_proc = run_cli("init", "--case", str(case_dir), "--entity", "示例企业")
        self.assertEqual(init_proc.returncode, 0, init_proc.stderr)
        return case_dir

    def _write_balanced_journal(self, path: Path) -> None:
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["凭证号", "记账日期", "科目编码", "科目名称", "业务说明", "借方发生额", "贷方发生额"])
            writer.writerows(
                [
                    ["记-001", "2025-01-02", "1002", "银行存款", "确认销售收入", "1000000", "0"],
                    ["记-001", "2025-01-02", "6001", "主营业务收入", "确认销售收入", "0", "1000000"],
                    ["记-002", "2025-03-08", "6602", "管理费用-业务招待费", "客户接待", "20000", "0"],
                    ["记-002", "2025-03-08", "1002", "银行存款", "客户接待", "0", "20000"],
                    ["记-003", "2025-09-18", "6602", "管理费用-办公费", "购买中秋礼品及高档茶叶", "5000", "0"],
                    ["记-003", "2025-09-18", "1002", "银行存款", "购买中秋礼品及高档茶叶", "0", "5000"],
                ]
            )

    def test_deep_ai_mode_is_accepted_by_cli_parser(self):
        args = build_parser().parse_args(["run", "--case", "case-a", "--ai-mode", "deep"])
        self.assertEqual(args.ai_mode, "deep")

    def test_run_creates_deterministic_findings_and_resumable_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case-a"
            init_proc = run_cli("init", "--case", str(case_dir), "--entity", "示例企业")
            self.assertEqual(init_proc.returncode, 0, init_proc.stderr)

            input_file = case_dir / "input" / "journal.csv"
            with input_file.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(["凭证号", "记账日期", "科目编码", "科目名称", "业务说明", "借方发生额", "贷方发生额"])
                writer.writerows(
                    [
                        ["记-001", "2025-01-02", "1002", "银行存款", "确认销售收入", "1000000", "0"],
                        ["记-001", "2025-01-02", "6001", "主营业务收入", "确认销售收入", "0", "1000000"],
                        ["记-002", "2025-03-08", "6602", "管理费用-业务招待费", "客户接待", "20000", "0"],
                        ["记-002", "2025-03-08", "1002", "银行存款", "客户接待", "0", "20000"],
                        ["记-003", "2025-09-18", "6602", "管理费用-办公费", "购买中秋礼品及高档茶叶", "5000", "0"],
                        ["记-003", "2025-09-18", "1002", "银行存款", "购买中秋礼品及高档茶叶", "0", "5000"],
                    ]
                )

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 0, run_proc.stderr)
            response = json.loads(run_proc.stdout)
            findings = json.loads((case_dir / "output" / "findings.json").read_text(encoding="utf-8"))
            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            rule_ids = [item["rule_id"] for item in findings]

            self.assertEqual(response["status"], "needs_professional_review")
            self.assertEqual(state["next_action"], "review_findings")
            self.assertIn("CIT-BE-001", rule_ids)
            self.assertIn("SEM-EXP-001", rule_ids)
            entertainment = next(item for item in findings if item["rule_id"] == "CIT-BE-001")
            self.assertEqual(entertainment["tax_adjustment"], "15000.00")
            self.assertEqual(entertainment["revenue_basis"], "1000000.00")
            calculation_inputs = entertainment["calculation_inputs"]
            self.assertEqual(calculation_inputs["actual_expense"]["value"], "20000.00")
            self.assertEqual(calculation_inputs["revenue_basis"]["value"], "1000000.00")
            self.assertEqual(
                calculation_inputs["actual_expense"]["lineage"][0]["contribution"],
                "20000.00",
            )
            self.assertEqual(
                calculation_inputs["revenue_basis"]["lineage"][0],
                {
                    "source_file": "journal.csv",
                    "source_sheet": "CSV",
                    "source_row": 3,
                    "voucher_no": "记-001",
                    "contribution": "1000000.00",
                },
            )
            self.assertTrue((case_dir / "work" / "normalized_journal.jsonl").exists())
            self.assertTrue((case_dir / "output" / "report.md").exists())

            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            manifest = json.loads(
                (case_dir / "runs" / "run-0001" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["current_run"], "run-0001")
            self.assertEqual(state["run_history"][0]["run_id"], "run-0001")
            self.assertEqual(manifest["status"], "needs_professional_review")
            self.assertEqual(manifest["ai_mode"], "off")
            self.assertEqual(
                manifest["input_files"][0]["sha256"], hashlib.sha256(input_file.read_bytes()).hexdigest()
            )
            self.assertTrue(
                (case_dir / "runs" / "run-0001" / "output" / "findings.json").is_file()
            )

    def test_run_stops_when_header_mapping_is_unresolved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case-b"
            run_cli("init", "--case", str(case_dir), "--entity", "示例企业")
            input_file = case_dir / "input" / "journal.csv"
            input_file.write_text("凭证号,日期,神秘借方,神秘贷方\n记-001,2025-01-01,100,100\n", encoding="utf-8-sig")

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 3)
            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            mapping_review = json.loads((case_dir / "work" / "mapping_review.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "needs_mapping_review")
            self.assertIn("神秘借方", mapping_review["unresolved"])

    def test_blocked_rerun_preserves_history_and_hides_stale_current_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir)
            input_file = case_dir / "input" / "journal.csv"
            self._write_balanced_journal(input_file)
            first = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")
            self.assertEqual(first.returncode, 0, first.stderr)
            first_findings = case_dir / "runs" / "run-0001" / "output" / "findings.json"
            first_manifest = case_dir / "runs" / "run-0001" / "manifest.json"
            findings_before = first_findings.read_bytes()
            manifest_before = first_manifest.read_bytes()

            input_file.write_text(
                "凭证号,日期,神秘借方,神秘贷方\n记-001,2025-01-01,100,100\n",
                encoding="utf-8-sig",
            )
            second = run_cli("run", "--case", str(case_dir), "--ai-mode", "assist")

            self.assertEqual(second.returncode, 3, second.stderr)
            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            second_manifest = json.loads(
                (case_dir / "runs" / "run-0002" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["current_run"], "run-0002")
            self.assertEqual([run["run_id"] for run in state["run_history"]], ["run-0001", "run-0002"])
            self.assertEqual(second_manifest["status"], "needs_mapping_review")
            self.assertEqual(first_findings.read_bytes(), findings_before)
            self.assertEqual(first_manifest.read_bytes(), manifest_before)
            self.assertFalse((case_dir / "output" / "findings.json").exists())
            self.assertFalse((case_dir / "output" / "report.md").exists())
            self.assertTrue((case_dir / "work" / "mapping_review.json").is_file())

    def test_processing_error_becomes_a_traceable_failed_run_without_stale_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir)
            input_file = case_dir / "input" / "journal.csv"
            self._write_balanced_journal(input_file)
            first = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")
            self.assertEqual(first.returncode, 0, first.stderr)
            first_findings = case_dir / "runs" / "run-0001" / "output" / "findings.json"
            first_manifest = case_dir / "runs" / "run-0001" / "manifest.json"
            findings_before = first_findings.read_bytes()
            manifest_before = first_manifest.read_bytes()

            with input_file.open("r", newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.reader(handle))
            invalid_value = "not-a-number-sensitive-90210"
            rows[1][5] = invalid_value
            with input_file.open("w", newline="", encoding="utf-8-sig") as handle:
                csv.writer(handle).writerows(rows)

            second = run_cli("run", "--case", str(case_dir), "--ai-mode", "assist")

            self.assertEqual(second.returncode, 5)
            self.assertNotIn("Traceback", second.stderr)
            self.assertNotIn(invalid_value, second.stdout + second.stderr)
            response = json.loads(second.stdout)
            self.assertEqual(response["status"], "blocked_processing_error")
            self.assertEqual(response["next_action"], "inspect_processing_error")
            self.assertEqual(response["error_type"], "invalid_numeric_value")

            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            second_manifest_path = case_dir / "runs" / "run-0002" / "manifest.json"
            second_manifest_text = second_manifest_path.read_text(encoding="utf-8")
            second_manifest = json.loads(second_manifest_text)
            self.assertEqual(state["status"], "blocked_processing_error")
            self.assertEqual(state["current_run"], "run-0002")
            self.assertEqual([run["run_id"] for run in state["run_history"]], ["run-0001", "run-0002"])
            self.assertEqual(second_manifest["status"], "blocked_processing_error")
            self.assertEqual(second_manifest["error_type"], "invalid_numeric_value")
            self.assertNotIn(invalid_value, second_manifest_text)
            self.assertIn("work/input_selection.json", second_manifest["artifacts"])
            self.assertIn("work/mapping_review.json", second_manifest["artifacts"])
            self.assertIn("work/normalized_journal.jsonl", second_manifest["artifacts"])
            self.assertTrue((case_dir / "work" / "processing_error.json").is_file())
            self.assertFalse((case_dir / "output" / "findings.json").exists())
            self.assertFalse((case_dir / "output" / "report.md").exists())
            self.assertEqual(first_findings.read_bytes(), findings_before)
            self.assertEqual(first_manifest.read_bytes(), manifest_before)

    def test_run_with_multiple_csv_files_blocks_with_explicit_input_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir)
            self._write_balanced_journal(case_dir / "input" / "a.csv")
            self._write_balanced_journal(case_dir / "input" / "b.csv")

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 4, run_proc.stderr)
            response = json.loads(run_proc.stdout)
            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            selection = json.loads(
                (case_dir / "work" / "input_selection.json").read_text(encoding="utf-8")
            )
            self.assertEqual(response["status"], "blocked_data_quality")
            self.assertEqual(state["current_run"], "run-0001")
            self.assertEqual(state["next_action"], "provide_exactly_one_csv_journal")
            self.assertEqual(selection["candidate_count"], 2)
            self.assertEqual(selection["status"], "blocked")
            self.assertEqual(selection["candidates"], ["a.csv", "b.csv"])
            self.assertFalse((case_dir / "output" / "findings.json").exists())
            self.assertTrue((case_dir / "runs" / "run-0001" / "manifest.json").is_file())

    def test_run_with_no_csv_file_blocks_with_explicit_input_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir)

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 4, run_proc.stderr)
            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            selection = json.loads(
                (case_dir / "work" / "input_selection.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "blocked_data_quality")
            self.assertEqual(state["next_action"], "provide_exactly_one_csv_journal")
            self.assertEqual(selection["candidate_count"], 0)
            self.assertEqual(selection["candidates"], [])
            self.assertFalse((case_dir / "output" / "findings.json").exists())


if __name__ == "__main__":
    unittest.main()
