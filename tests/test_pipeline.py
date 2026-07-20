import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import open_tax_check.pipeline as pipeline
from open_tax_check.case import init_case
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

    def _change_entertainment_amount(self, path: Path, amount: str) -> None:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
        rows[3][5] = amount
        rows[4][6] = amount
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            csv.writer(handle).writerows(rows)

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
                    "period": "2025-01",
                    "tax_year": "2025",
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

    def test_business_entertainment_is_calculated_separately_by_tax_year(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir, "case-cross-year")
            input_file = case_dir / "input" / "journal.csv"
            with input_file.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    ["凭证号", "记账日期", "科目名称", "借方发生额", "贷方发生额"]
                )
                writer.writerows(
                    [
                        ["记-001", "2024-01-02", "银行存款", "100000", ""],
                        ["记-001", "2024-01-02", "主营业务收入", " ", "100000"],
                        ["记-002", "2024/3/8", "管理费用-业务招待费", "100", "0"],
                        ["记-002", "2024/3/8", "银行存款", "0", "100"],
                        ["记-001", "2025.01.02", "银行存款", "80000", "0"],
                        ["记-001", "2025.01.02", "主营业务收入", "0", "80000"],
                        ["记-002", "2025-03-08", "管理费用-业务招待费", "1400", "0"],
                        ["记-002", "2025-03-08", "银行存款", "0", "1400"],
                    ]
                )

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 0, run_proc.stderr)
            findings = json.loads(
                (case_dir / "output" / "findings.json").read_text(encoding="utf-8")
            )
            entertainment = [item for item in findings if item["rule_id"] == "CIT-BE-001"]
            self.assertEqual(len(entertainment), 2)
            self.assertEqual(
                [(item["tax_year"], item["tax_adjustment"]) for item in entertainment],
                [("2024", "40.00"), ("2025", "1000.00")],
            )
            self.assertEqual(
                [item["finding_id"] for item in entertainment],
                ["CIT-BE-001-2024", "CIT-BE-001-2025"],
            )
            for finding in entertainment:
                tax_year = finding["tax_year"]
                inputs = finding["calculation_inputs"]
                all_lineage = (
                    inputs["actual_expense"]["lineage"]
                    + inputs["revenue_basis"]["lineage"]
                )
                self.assertTrue(all(item["tax_year"] == tax_year for item in all_lineage))

    def test_invalid_or_incomplete_rows_block_before_rules_without_client_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir, "case-invalid-row")
            input_file = case_dir / "input" / "journal.csv"
            with input_file.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    ["凭证号", "记账日期", "科目名称", "业务说明", "借方发生额", "贷方发生额"]
                )
                writer.writerows(
                    [
                        ["", "2025-01-01", "银行存款", "private detail", "100", "0"],
                        ["记-002", "2025-02-30", "银行存款", "another private detail", "0", "100"],
                        ["记-003", "2025-03-01", "", "third private detail", "", ""],
                    ]
                )

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 4, run_proc.stderr)
            response = json.loads(run_proc.stdout)
            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            quality_text = (case_dir / "work" / "data_quality.json").read_text(
                encoding="utf-8"
            )
            quality_findings = json.loads(quality_text)
            self.assertEqual(response["status"], "blocked_data_quality")
            self.assertEqual(state["next_action"], "resolve_data_quality_findings")
            self.assertIn("DQ-ROW-001", [item["rule_id"] for item in quality_findings])
            self.assertIn("DQ-DATE-001", [item["rule_id"] for item in quality_findings])
            incomplete = [item for item in quality_findings if item["rule_id"] == "DQ-ROW-001"]
            self.assertTrue(any("voucher_no" in item["fields"] for item in incomplete))
            self.assertTrue(any("account_name" in item["fields"] for item in incomplete))
            self.assertNotIn("private detail", quality_text)
            self.assertNotIn("another private detail", quality_text)
            self.assertNotIn("third private detail", quality_text)
            self.assertFalse((case_dir / "output" / "findings.json").exists())
            self.assertFalse((case_dir / "output" / "report.md").exists())

    def test_normalized_journal_is_written_once_after_quality_normalization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir, "case-single-normalized-write")
            self._write_balanced_journal(case_dir / "input" / "journal.csv")

            with patch.object(
                pipeline,
                "_write_jsonl",
                wraps=pipeline._write_jsonl,
            ) as write_jsonl:
                code, _ = pipeline.run_case(case_dir, "off")

            self.assertEqual(code, 0)
            self.assertEqual(write_jsonl.call_count, 1)
            normalized_path = write_jsonl.call_args.args[0]
            self.assertEqual(normalized_path.name, "normalized_journal.jsonl")
            normalized_rows = [
                json.loads(line)
                for line in normalized_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(normalized_rows[0]["period"], "2025-01")
            self.assertEqual(normalized_rows[0]["tax_year"], "2025")

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

    def test_run_stops_when_two_headers_map_to_the_same_canonical_field(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir, "case-conflicting-headers")
            input_file = case_dir / "input" / "journal.csv"
            input_file.write_text(
                "凭证号,日期,科目名称,借方,借方发生额,贷方\n"
                "记-001,2025-01-01,银行存款,100,999,0\n",
                encoding="utf-8-sig",
            )

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 3, run_proc.stderr)
            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            mapping_review = json.loads(
                (case_dir / "work" / "mapping_review.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "needs_mapping_review")
            self.assertEqual(state["next_action"], "confirm_field_mapping")
            self.assertEqual(
                mapping_review["conflicts"], {"debit": ["借方", "借方发生额"]}
            )
            self.assertFalse((case_dir / "work" / "normalized_journal.jsonl").exists())
            self.assertFalse((case_dir / "output" / "findings.json").exists())

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

    def test_invalid_numeric_value_is_a_data_quality_run_without_stale_output(self):
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

            self.assertEqual(second.returncode, 4)
            self.assertNotIn("Traceback", second.stderr)
            self.assertNotIn(invalid_value, second.stdout + second.stderr)
            response = json.loads(second.stdout)
            self.assertEqual(response["status"], "blocked_data_quality")
            self.assertEqual(response["next_action"], "resolve_data_quality_findings")

            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            second_manifest_path = case_dir / "runs" / "run-0002" / "manifest.json"
            second_manifest_text = second_manifest_path.read_text(encoding="utf-8")
            second_manifest = json.loads(second_manifest_text)
            quality_text = (case_dir / "work" / "data_quality.json").read_text(
                encoding="utf-8"
            )
            quality = json.loads(quality_text)
            numeric = [item for item in quality if item["rule_id"] == "DQ-NUMERIC-001"]
            self.assertEqual(len(numeric), 1)
            self.assertEqual(numeric[0]["fields"], ["debit"])
            self.assertNotIn(invalid_value, quality_text)
            self.assertEqual(state["status"], "blocked_data_quality")
            self.assertEqual(state["current_run"], "run-0002")
            self.assertEqual([run["run_id"] for run in state["run_history"]], ["run-0001", "run-0002"])
            self.assertEqual(second_manifest["status"], "blocked_data_quality")
            self.assertNotIn("error_type", second_manifest)
            self.assertNotIn(invalid_value, second_manifest_text)
            self.assertIn("work/input_selection.json", second_manifest["artifacts"])
            self.assertIn("work/mapping_review.json", second_manifest["artifacts"])
            self.assertIn("work/normalized_journal.jsonl", second_manifest["artifacts"])
            self.assertIn("work/data_quality.json", second_manifest["artifacts"])
            self.assertFalse((case_dir / "work" / "processing_error.json").exists())
            self.assertFalse((case_dir / "output" / "findings.json").exists())
            self.assertFalse((case_dir / "output" / "report.md").exists())
            self.assertEqual(first_findings.read_bytes(), findings_before)
            self.assertEqual(first_manifest.read_bytes(), manifest_before)

    def test_single_uppercase_csv_is_selected_and_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir, "case-uppercase-csv")
            self._write_balanced_journal(case_dir / "input" / "JOURNAL.CSV")

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 0, run_proc.stderr)
            selection = json.loads(
                (case_dir / "work" / "input_selection.json").read_text(encoding="utf-8")
            )
            self.assertEqual(selection["status"], "selected")
            self.assertEqual(selection["selected_file"], "JOURNAL.CSV")

    def test_unsupported_input_alongside_csv_blocks_and_manifest_hashes_all_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir, "case-unsupported-input")
            csv_file = case_dir / "input" / "journal.csv"
            pdf_file = case_dir / "input" / "support.pdf"
            self._write_balanced_journal(csv_file)
            pdf_file.write_bytes(b"fictional-pdf-placeholder")

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 4, run_proc.stderr)
            response = json.loads(run_proc.stdout)
            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            selection = json.loads(
                (case_dir / "work" / "input_selection.json").read_text(encoding="utf-8")
            )
            manifest = json.loads(
                (case_dir / "runs" / "run-0001" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(response["status"], "blocked_data_quality")
            self.assertEqual(state["next_action"], "remove_unsupported_input_files")
            self.assertEqual(selection["status"], "blocked")
            self.assertEqual(selection["input_files"], ["journal.csv", "support.pdf"])
            self.assertEqual(selection["unsupported_files"], ["support.pdf"])
            self.assertEqual(selection["selected_file"], None)
            manifest_by_path = {item["path"]: item for item in manifest["input_files"]}
            self.assertEqual(
                set(manifest_by_path), {"input/journal.csv", "input/support.pdf"}
            )
            self.assertEqual(
                manifest_by_path["input/journal.csv"]["sha256"],
                hashlib.sha256(csv_file.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                manifest_by_path["input/support.pdf"]["sha256"],
                hashlib.sha256(pdf_file.read_bytes()).hexdigest(),
            )
            self.assertFalse((case_dir / "work" / "mapping_review.json").exists())
            self.assertFalse((case_dir / "output" / "findings.json").exists())
            self.assertFalse((case_dir / "output" / "report.md").exists())

    def test_extra_csv_cells_block_without_echoing_values_or_client_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir, "case-extra-cell")
            sensitive = "sensitive-extra-cell-90210"
            input_file = case_dir / "input" / "journal.csv"
            input_file.write_text(
                "凭证号,记账日期,科目名称,借方发生额,贷方发生额\n"
                f"记-001,2025-01-02,银行存款,100,100,{sensitive}\n",
                encoding="utf-8-sig",
            )

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 4, run_proc.stderr)
            quality_text = (case_dir / "work" / "data_quality.json").read_text(
                encoding="utf-8"
            )
            findings = json.loads(quality_text)
            structure = [
                item
                for item in findings
                if item["rule_id"] == "DQ-CSV-STRUCTURE-001"
            ]
            self.assertEqual(len(structure), 1)
            self.assertEqual(structure[0]["issue"], "extra_cells")
            self.assertEqual(structure[0]["source_file"], "journal.csv")
            self.assertEqual(structure[0]["source_row"], 2)
            self.assertNotIn(sensitive, quality_text)
            self.assertNotIn(sensitive, run_proc.stdout + run_proc.stderr)
            self.assertFalse((case_dir / "output" / "findings.json").exists())
            self.assertFalse((case_dir / "output" / "report.md").exists())

    def test_missing_trailing_csv_cell_blocks_before_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir, "case-missing-cell")
            input_file = case_dir / "input" / "journal.csv"
            input_file.write_text(
                "凭证号,记账日期,科目名称,借方发生额,贷方发生额\n"
                "记-001,2025-01-02,银行存款,100\n",
                encoding="utf-8-sig",
            )

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 4, run_proc.stderr)
            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            findings = json.loads(
                (case_dir / "work" / "data_quality.json").read_text(encoding="utf-8")
            )
            structure = [
                item
                for item in findings
                if item["rule_id"] == "DQ-CSV-STRUCTURE-001"
            ]
            self.assertEqual(state["next_action"], "resolve_data_quality_findings")
            self.assertEqual(len(structure), 1)
            self.assertEqual(structure[0]["issue"], "missing_cells")
            self.assertFalse((case_dir / "output" / "findings.json").exists())
            self.assertFalse((case_dir / "output" / "report.md").exists())

    def test_quoted_grouped_numeric_values_are_calculated_end_to_end(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir, "case-grouped-numeric")
            input_file = case_dir / "input" / "journal.csv"
            with input_file.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    ["凭证号", "记账日期", "科目名称", "借方发生额", "贷方发生额"]
                )
                writer.writerows(
                    [
                        ["记-001", "2025-01-02", "银行存款", "1,000,000.00", "0"],
                        ["记-001", "2025-01-02", "主营业务收入", "0", "1,000,000.00"],
                        ["记-002", "2025-03-08", "管理费用-业务招待费", "20,000", "0"],
                        ["记-002", "2025-03-08", "银行存款", "0", "20,000"],
                    ]
                )

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 0, run_proc.stderr)
            findings = json.loads(
                (case_dir / "output" / "findings.json").read_text(encoding="utf-8")
            )
            entertainment = next(
                item for item in findings if item["rule_id"] == "CIT-BE-001"
            )
            self.assertEqual(entertainment["revenue_basis"], "1000000.00")
            self.assertEqual(entertainment["tax_adjustment"], "15000.00")

    def test_oversized_or_over_scale_amounts_block_as_numeric_data_quality(self):
        invalid_values = (
            "123456789012345678901234567",
            "999999999999999999.999",
        )
        for index, invalid_value in enumerate(invalid_values):
            with self.subTest(invalid_value=invalid_value):
                with tempfile.TemporaryDirectory() as temp_dir:
                    case_dir = self._init_case(temp_dir, f"case-numeric-bound-{index}")
                    input_file = case_dir / "input" / "journal.csv"
                    input_file.write_text(
                        "凭证号,记账日期,科目名称,借方发生额,贷方发生额\n"
                        f"记-001,2025-01-02,银行存款,{invalid_value},0\n",
                        encoding="utf-8-sig",
                    )

                    run_proc = run_cli(
                        "run", "--case", str(case_dir), "--ai-mode", "off"
                    )

                    self.assertEqual(run_proc.returncode, 4, run_proc.stderr)
                    response = json.loads(run_proc.stdout)
                    quality_text = (
                        case_dir / "work" / "data_quality.json"
                    ).read_text(encoding="utf-8")
                    manifest_text = (
                        case_dir / "runs" / "run-0001" / "manifest.json"
                    ).read_text(encoding="utf-8")
                    quality = json.loads(quality_text)
                    numeric = [
                        item
                        for item in quality
                        if item["rule_id"] == "DQ-NUMERIC-001"
                    ]
                    self.assertEqual(response["status"], "blocked_data_quality")
                    self.assertEqual(numeric[0]["fields"], ["debit"])
                    self.assertNotIn("error_type", response)
                    self.assertNotIn(invalid_value, run_proc.stdout + run_proc.stderr)
                    self.assertNotIn(invalid_value, quality_text)
                    self.assertNotIn(invalid_value, manifest_text)
                    self.assertFalse(
                        (case_dir / "work" / "processing_error.json").exists()
                    )
                    self.assertFalse(
                        (case_dir / "output" / "findings.json").exists()
                    )
                    self.assertFalse((case_dir / "output" / "report.md").exists())

    def test_large_valid_dataset_is_aggregated_exactly_by_hard_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = self._init_case(temp_dir, "case-exact-large-aggregate")
            input_file = case_dir / "input" / "journal.csv"
            maximum = "999999999999999999.99"
            with input_file.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    ["凭证号", "记账日期", "科目名称", "借方发生额", "贷方发生额"]
                )
                for index in range(25):
                    voucher = f"记-revenue-{index:02d}"
                    writer.writerow(
                        [voucher, "2025-01-02", "银行存款", maximum, "0"]
                    )
                    writer.writerow(
                        [voucher, "2025-01-02", "主营业务收入", "0", maximum]
                    )
                writer.writerow(
                    ["记-expense", "2025-02-03", "管理费用-业务招待费", "1.00", "0"]
                )
                writer.writerow(
                    ["记-expense", "2025-02-03", "银行存款", "0", "1.00"]
                )

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 0, run_proc.stderr)
            findings = json.loads(
                (case_dir / "output" / "findings.json").read_text(encoding="utf-8")
            )
            entertainment = next(
                item for item in findings if item["rule_id"] == "CIT-BE-001"
            )
            self.assertEqual(
                entertainment["revenue_basis"], "24999999999999999999.75"
            )
            self.assertEqual(entertainment["tax_adjustment"], "0.40")

    def test_current_view_stays_on_previous_run_when_staging_copy_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case-a"
            init_case(case_dir, "示例企业")
            input_file = case_dir / "input" / "journal.csv"
            self._write_balanced_journal(input_file)
            code, _ = pipeline.run_case(case_dir, "off")
            self.assertEqual(code, 0)
            state_path = case_dir / "state.json"
            current_findings = case_dir / "output" / "findings.json"
            current_normalized = case_dir / "work" / "normalized_journal.jsonl"
            first_findings = case_dir / "runs" / "run-0001" / "output" / "findings.json"
            first_manifest = case_dir / "runs" / "run-0001" / "manifest.json"
            state_before = state_path.read_bytes()
            current_findings_before = current_findings.read_bytes()
            current_normalized_before = current_normalized.read_bytes()
            first_findings_before = first_findings.read_bytes()
            first_manifest_before = first_manifest.read_bytes()
            self._change_entertainment_amount(input_file, "30000")

            with patch.object(
                pipeline.shutil,
                "copytree",
                side_effect=OSError("injected staging copy failure"),
            ):
                with self.assertRaisesRegex(OSError, "injected staging copy failure"):
                    pipeline.run_case(case_dir, "off")

            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertEqual(current_findings.read_bytes(), current_findings_before)
            self.assertEqual(current_normalized.read_bytes(), current_normalized_before)
            self.assertEqual(first_findings.read_bytes(), first_findings_before)
            self.assertEqual(first_manifest.read_bytes(), first_manifest_before)
            self.assertTrue((case_dir / "runs" / "run-0002" / "manifest.json").is_file())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["current_run"], "run-0001")
            self.assertEqual([run["run_id"] for run in state["run_history"]], ["run-0001"])

    def test_current_view_rolls_back_when_atomic_state_write_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case-a"
            init_case(case_dir, "示例企业")
            input_file = case_dir / "input" / "journal.csv"
            self._write_balanced_journal(input_file)
            code, _ = pipeline.run_case(case_dir, "off")
            self.assertEqual(code, 0)
            state_path = case_dir / "state.json"
            current_findings = case_dir / "output" / "findings.json"
            current_normalized = case_dir / "work" / "normalized_journal.jsonl"
            state_before = state_path.read_bytes()
            current_findings_before = current_findings.read_bytes()
            current_normalized_before = current_normalized.read_bytes()
            self._change_entertainment_amount(input_file, "30000")
            original_replace = pipeline.os.replace

            def fail_state_replace(source, destination):
                if Path(destination) == state_path:
                    raise OSError("injected state replace failure")
                return original_replace(source, destination)

            with patch.object(pipeline.os, "replace", side_effect=fail_state_replace):
                with self.assertRaisesRegex(OSError, "injected state replace failure"):
                    pipeline.run_case(case_dir, "off")

            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertEqual(current_findings.read_bytes(), current_findings_before)
            self.assertEqual(current_normalized.read_bytes(), current_normalized_before)
            self.assertTrue((case_dir / "runs" / "run-0002" / "manifest.json").is_file())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["current_run"], "run-0001")
            self.assertEqual([run["run_id"] for run in state["run_history"]], ["run-0001"])

    def test_failed_state_restore_preserves_exact_persistent_state_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case-a"
            init_case(case_dir, "示例企业")
            input_file = case_dir / "input" / "journal.csv"
            self._write_balanced_journal(input_file)
            code, _ = pipeline.run_case(case_dir, "off")
            self.assertEqual(code, 0)
            state_path = case_dir / "state.json"
            current_findings = case_dir / "output" / "findings.json"
            current_normalized = case_dir / "work" / "normalized_journal.jsonl"
            first_manifest = case_dir / "runs" / "run-0001" / "manifest.json"
            state_before = state_path.read_bytes()
            current_findings_before = current_findings.read_bytes()
            current_normalized_before = current_normalized.read_bytes()
            first_manifest_before = first_manifest.read_bytes()
            self._change_entertainment_amount(input_file, "30000")
            input_before_second_run = input_file.read_bytes()
            original_replace = pipeline.os.replace
            state_replace_calls = 0

            def fail_new_state_then_restore(source, destination):
                nonlocal state_replace_calls
                if Path(destination) == state_path:
                    state_replace_calls += 1
                    if state_replace_calls == 1:
                        original_replace(source, destination)
                        raise OSError("injected post-replace state failure")
                    raise OSError("injected state restore failure")
                return original_replace(source, destination)

            with patch.object(
                pipeline.os,
                "replace",
                side_effect=fail_new_state_then_restore,
            ):
                with self.assertRaisesRegex(OSError, "injected post-replace state failure"):
                    pipeline.run_case(case_dir, "off")

            self.assertEqual(current_findings.read_bytes(), current_findings_before)
            self.assertEqual(current_normalized.read_bytes(), current_normalized_before)
            self.assertEqual(first_manifest.read_bytes(), first_manifest_before)
            self.assertEqual(input_file.read_bytes(), input_before_second_run)
            self.assertTrue((case_dir / "runs" / "run-0002" / "manifest.json").is_file())
            backups = list(case_dir.glob(".opentaxcheck-state-backup-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), state_before)

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
