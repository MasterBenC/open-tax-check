from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_tax_check.case import init_case
from open_tax_check.cli import build_parser
from open_tax_check import pipeline
from open_tax_check.pipeline import inspect_case
from open_tax_check.schemas import SCHEMA_VERSION
from open_tax_check.workbooks import WorkbookError


JOURNAL_ROWS = (
    ("凭证号", "日期", "科目名称", "借方", "贷方"),
    ("记-001", "2026-01-02", "银行存款", 100, 0),
)
TRIAL_BALANCE_ROWS = (
    (
        "科目名称",
        "期初借方余额",
        "期初贷方余额",
        "本期借方发生额",
        "本期贷方发生额",
        "期末借方余额",
        "期末贷方余额",
    ),
    ("银行存款", 100, 0, 50, 20, 130, 0),
)


def make_xlsx(path: Path, *, sheets: tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]) -> Path:
    from openpyxl import Workbook

    workbook = Workbook()
    for index, (name, rows) in enumerate(sheets):
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        sheet.title = name
        for row in rows:
            sheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


class InspectPipelineTests(unittest.TestCase):
    def init_case(self, root: Path, *, period: bool) -> Path:
        case = root / "case"
        if period:
            init_case(case, "虚构企业", "2026-01-01", "2026-12-31")
        else:
            init_case(case, "虚构企业")
        return case

    def test_cli_parser_accepts_inspect_case(self):
        args = build_parser().parse_args(["inspect", "--case", "case-a"])

        self.assertEqual(args.command, "inspect")
        self.assertEqual(args.case, Path("case-a"))

    def test_inspect_without_period_stops_before_opening_workbook(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=False)
            make_xlsx(
                case / "input" / "journal.xlsx",
                sheets=(("Journal", JOURNAL_ROWS),),
            )

            with patch(
                "open_tax_check.pipeline.open_workbook",
                side_effect=AssertionError("workbook must remain unopened"),
            ):
                code, result = inspect_case(case)

        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "needs_intake_review")
        self.assertEqual(result["next_action"], "set_case_period")

    def test_inspect_auto_selects_one_visible_high_confidence_journal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            make_xlsx(
                case / "input" / "journal.xlsx",
                sheets=(("Journal", JOURNAL_ROWS),),
            )

            code, result = inspect_case(case)

            selection = json.loads(
                (case / "work" / "sheet_selection.json").read_text(
                    encoding="utf-8"
                )
            )
            artifact_names = {
                path.name for path in (case / "work").iterdir()
            }
            artifacts = {
                name: json.loads(
                    (case / "work" / name).read_text(encoding="utf-8")
                )
                for name in artifact_names
            }

        self.assertEqual(code, 0)
        self.assertEqual(
            selection["journal"][0]["decision_source"],
            "automatic",
        )
        self.assertEqual(result["status"], "partial_scope_ready")
        self.assertEqual(
            artifact_names,
            {
                "input_manifest.json",
                "workbook_inventory.json",
                "sheet_selection.json",
                "coverage_matrix.json",
            },
        )
        self.assertTrue(
            all(
                artifact["schema_version"] == SCHEMA_VERSION
                for artifact in artifacts.values()
            )
        )
        self.assertEqual(
            set(artifacts["input_manifest.json"]),
            {"schema_version", "sources", "issues"},
        )
        self.assertEqual(
            set(artifacts["workbook_inventory.json"]),
            {"schema_version", "workbooks", "issues"},
        )
        self.assertEqual(
            set(artifacts["sheet_selection.json"]),
            {"schema_version", "journal", "trial_balance"},
        )
        self.assertEqual(
            set(artifacts["coverage_matrix.json"]),
            {"schema_version", "items"},
        )
        self.assertEqual(
            set(artifacts["input_manifest.json"]["sources"][0]),
            {
                "schema_version",
                "source_id",
                "filename",
                "sha256",
                "size_bytes",
                "extension",
                "status",
            },
        )
        workbook_record = artifacts["workbook_inventory.json"][
            "workbooks"
        ][0]
        self.assertEqual(
            set(workbook_record),
            {
                "schema_version",
                "source_id",
                "filename",
                "sha256",
                "size_bytes",
                "extension",
                "adapter_version",
                "status",
                "diagnostic_code",
                "sheets",
            },
        )
        self.assertEqual(
            set(workbook_record["sheets"][0]),
            {
                "schema_version",
                "source_id",
                "sheet_id",
                "index",
                "name",
                "visibility",
                "used_range",
                "visited_cell_estimate",
                "header_rows",
                "candidate_header_rows",
                "role_scores",
                "has_formulas",
                "has_merged_ranges",
                "has_hidden_data",
                "merged_ranges",
            },
        )
        self.assertEqual(
            set(workbook_record["sheets"][0]["role_scores"][0]),
            {"role", "score", "signals"},
        )
        self.assertEqual(
            set(artifacts["coverage_matrix.json"]["items"][0]),
            {
                "schema_version",
                "evidence_role",
                "tier",
                "status",
                "procedure_status",
                "source_ids",
                "trigger_rule_id",
                "requested_at",
                "request_status",
            },
        )

    def test_inspect_requires_selection_for_multiple_high_confidence_journals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            make_xlsx(
                case / "input" / "journals.xlsx",
                sheets=(
                    ("Journal A", JOURNAL_ROWS),
                    ("Journal B", JOURNAL_ROWS),
                ),
            )

            code, result = inspect_case(case)

            selection = json.loads(
                (case / "work" / "sheet_selection.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "needs_sheet_selection")
        self.assertEqual(result["next_action"], "select_journal_sheet")
        self.assertEqual(len(selection["journal"]), 2)
        self.assertTrue(
            all(
                candidate["decision_source"] == "manual"
                for candidate in selection["journal"]
            )
        )

    def test_cli_inspect_prints_json_and_returns_pipeline_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            make_xlsx(
                case / "input" / "journal.xlsx",
                sheets=(("Journal", JOURNAL_ROWS),),
            )

            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "open_tax_check.cli",
                    "inspect",
                    "--case",
                    str(case),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(
            json.loads(process.stdout)["status"],
            "partial_scope_ready",
        )

    def test_expected_workbook_error_uses_stable_inventory_diagnostic(self):
        sensitive = "PRIVATE-CELL-VALUE"
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            (case / "input" / "journal.xlsx").write_bytes(b"not-a-workbook")

            with patch(
                "open_tax_check.pipeline.open_workbook",
                side_effect=WorkbookError("corrupt_workbook"),
            ):
                code, result = inspect_case(case)

            inventory_text = (
                case / "work" / "workbook_inventory.json"
            ).read_text(encoding="utf-8")
            inventory = json.loads(inventory_text)

        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "needs_intake_review")
        self.assertEqual(result["next_action"], "resolve_workbook_issues")
        self.assertEqual(inventory["issues"][0]["code"], "corrupt_workbook")
        self.assertEqual(len(inventory["workbooks"]), 1)
        workbook = inventory["workbooks"][0]
        self.assertEqual(
            set(workbook),
            {
                "schema_version",
                "source_id",
                "filename",
                "sha256",
                "size_bytes",
                "extension",
                "adapter_version",
                "status",
                "diagnostic_code",
                "sheets",
            },
        )
        self.assertEqual(workbook["status"], "error")
        self.assertEqual(workbook["diagnostic_code"], "corrupt_workbook")
        self.assertEqual(workbook["extension"], ".xlsx")
        self.assertEqual(len(workbook["sha256"]), 64)
        self.assertEqual(workbook["size_bytes"], len(b"not-a-workbook"))
        self.assertNotIn(
            sensitive,
            inventory_text + json.dumps(result),
        )

    def test_successful_workbook_inventory_has_complete_source_lineage_and_adapter_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            source = make_xlsx(
                case / "input" / "journal.xlsx",
                sheets=(("Journal", JOURNAL_ROWS),),
            )
            source_size = source.stat().st_size

            code, _ = inspect_case(case)

            inventory = json.loads(
                (
                    case / "work" / "workbook_inventory.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(code, 0)
        workbook = inventory["workbooks"][0]
        self.assertEqual(workbook["schema_version"], SCHEMA_VERSION)
        self.assertEqual(workbook["filename"], source.name)
        self.assertEqual(workbook["size_bytes"], source_size)
        self.assertEqual(workbook["extension"], ".xlsx")
        self.assertEqual(len(workbook["sha256"]), 64)
        self.assertEqual(workbook["adapter_version"], SCHEMA_VERSION)
        self.assertEqual(workbook["status"], "inspected")
        self.assertIsNone(workbook["diagnostic_code"])
        self.assertEqual(len(workbook["sheets"]), 1)

    def test_visible_journal_with_hidden_row_is_not_automatically_selected_and_metadata_is_real(self):
        from openpyxl import load_workbook

        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            path = make_xlsx(
                case / "input" / "journal.xlsx",
                sheets=(("Journal", JOURNAL_ROWS),),
            )
            workbook = load_workbook(path)
            sheet = workbook.active
            sheet["G1"] = "formula"
            sheet["G2"] = "=1+1"
            sheet.merge_cells("H1:I1")
            sheet.row_dimensions[2].hidden = True
            workbook.save(path)
            workbook.close()

            code, result = inspect_case(case)

            inventory = json.loads(
                (
                    case / "work" / "workbook_inventory.json"
                ).read_text(encoding="utf-8")
            )
            selection = json.loads(
                (
                    case / "work" / "sheet_selection.json"
                ).read_text(encoding="utf-8")
            )

        sheet_record = inventory["workbooks"][0]["sheets"][0]
        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "needs_sheet_selection")
        self.assertIs(sheet_record["has_formulas"], True)
        self.assertIs(sheet_record["has_merged_ranges"], True)
        self.assertIs(sheet_record["has_hidden_data"], True)
        self.assertIn("H1:I1", sheet_record["merged_ranges"])
        self.assertEqual(
            selection["journal"][0]["decision_source"],
            "manual",
        )

    def test_review_band_journal_requires_manual_sheet_selection(self):
        review_rows = (
            ("凭证号", "日期", "科目名称", "借方"),
            ("记-001", "2026-01-02", "银行存款", 100),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            make_xlsx(
                case / "input" / "journal.xlsx",
                sheets=(("Journal", review_rows),),
            )

            code, result = inspect_case(case)

            selection = json.loads(
                (
                    case / "work" / "sheet_selection.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(code, 4)
        self.assertEqual(result["next_action"], "select_journal_sheet")
        self.assertEqual(selection["journal"][0]["confidence"], "review")
        self.assertEqual(selection["journal"][0]["decision_source"], "manual")

    def test_hidden_high_confidence_journal_requires_manual_sheet_selection(self):
        from openpyxl import load_workbook

        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            path = make_xlsx(
                case / "input" / "journal.xlsx",
                sheets=(
                    ("Visible Notes", (("note",),)),
                    ("Hidden Journal", JOURNAL_ROWS),
                ),
            )
            workbook = load_workbook(path)
            workbook["Hidden Journal"].sheet_state = "hidden"
            workbook.save(path)
            workbook.close()

            code, result = inspect_case(case)

            selection = json.loads(
                (
                    case / "work" / "sheet_selection.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(code, 4)
        self.assertEqual(result["next_action"], "select_journal_sheet")
        self.assertEqual(
            selection["journal"][0]["visibility"],
            "hidden",
        )
        self.assertEqual(selection["journal"][0]["decision_source"], "manual")

    def test_visible_high_confidence_trial_balance_is_automatically_selected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            make_xlsx(
                case / "input" / "trial-balance.xlsx",
                sheets=(("Trial Balance", TRIAL_BALANCE_ROWS),),
            )

            code, result = inspect_case(case)

            selection = json.loads(
                (
                    case / "work" / "sheet_selection.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "partial_scope_ready")
        self.assertEqual(
            selection["trial_balance"][0]["decision_source"],
            "automatic",
        )

    def test_intake_review_has_priority_over_ambiguous_sheet_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            make_xlsx(
                case / "input" / "journals.xlsx",
                sheets=(
                    ("Journal A", JOURNAL_ROWS),
                    ("Journal B", JOURNAL_ROWS),
                ),
            )
            (case / "input" / "broken.xlsx").write_bytes(
                b"not-a-workbook"
            )

            code, result = inspect_case(case)

        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "needs_intake_review")
        self.assertEqual(result["next_action"], "resolve_workbook_issues")

    def test_unexpected_inspection_error_preserves_last_successful_current_view(self):
        sensitive = "PRIVATE-CELL-VALUE"
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            make_xlsx(
                case / "input" / "journal.xlsx",
                sheets=(("Journal", JOURNAL_ROWS),),
            )
            first_code, _ = inspect_case(case)
            self.assertEqual(first_code, 0)
            current_before = (
                case / "work" / "sheet_selection.json"
            ).read_bytes()
            first_run_artifact = (
                case
                / "runs"
                / "run-0001"
                / "work"
                / "sheet_selection.json"
            ).read_bytes()

            with patch(
                "open_tax_check.pipeline.detect_sheet",
                side_effect=RuntimeError(sensitive),
            ):
                code, result = inspect_case(case)

            state = json.loads(
                (case / "state.json").read_text(encoding="utf-8")
            )
            failed_manifest = (
                case / "runs" / "run-0002" / "manifest.json"
            ).read_text(encoding="utf-8")
            current_after = (
                case / "work" / "sheet_selection.json"
            ).read_bytes()
            first_run_artifact_after = (
                case
                / "runs"
                / "run-0001"
                / "work"
                / "sheet_selection.json"
            ).read_bytes()

        self.assertEqual(code, 4)
        self.assertEqual(result["error_type"], "inspect_processing_error")
        self.assertEqual(result["next_action"], "inspect_processing_error")
        self.assertEqual(state["current_run"], "run-0001")
        self.assertEqual(current_after, current_before)
        self.assertEqual(first_run_artifact_after, first_run_artifact)
        self.assertIn('"error_type": "inspect_processing_error"', failed_manifest)
        self.assertNotIn(
            sensitive,
            failed_manifest + json.dumps(result, ensure_ascii=False),
        )

    def test_atomic_publish_failure_preserves_previous_current_view(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            make_xlsx(
                case / "input" / "journal.xlsx",
                sheets=(("Journal", JOURNAL_ROWS),),
            )
            first_code, _ = inspect_case(case)
            self.assertEqual(first_code, 0)
            current_before = (
                case / "work" / "sheet_selection.json"
            ).read_bytes()

            with patch.object(
                pipeline.shutil,
                "copytree",
                side_effect=OSError("PRIVATE-PUBLISH-DETAIL"),
            ):
                code, result = inspect_case(case)

            state = json.loads(
                (case / "state.json").read_text(encoding="utf-8")
            )
            current_after = (
                case / "work" / "sheet_selection.json"
            ).read_bytes()

        self.assertEqual(code, 4)
        self.assertEqual(result["error_type"], "inspect_processing_error")
        self.assertEqual(result["next_action"], "inspect_processing_error")
        self.assertEqual(state["current_run"], "run-0001")
        self.assertEqual(current_after, current_before)

    def test_missing_period_publish_failure_is_safely_finalized_without_replacing_current(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            make_xlsx(
                case / "input" / "journal.xlsx",
                sheets=(("Journal", JOURNAL_ROWS),),
            )
            first_code, _ = inspect_case(case)
            self.assertEqual(first_code, 0)
            current_before = (
                case / "work" / "sheet_selection.json"
            ).read_bytes()
            state_path = case / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["review_period"] = None
            state_path.write_text(
                json.dumps(state, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.object(
                pipeline.shutil,
                "copytree",
                side_effect=OSError("PRIVATE-NO-PERIOD-PUBLISH"),
            ):
                code, result = inspect_case(case)

            state_after = json.loads(
                state_path.read_text(encoding="utf-8")
            )
            current_after = (
                case / "work" / "sheet_selection.json"
            ).read_bytes()
            failed_manifest = (
                case / "runs" / "run-0002" / "manifest.json"
            ).read_text(encoding="utf-8")

        self.assertEqual(code, 4)
        self.assertEqual(result["error_type"], "inspect_processing_error")
        self.assertEqual(result["next_action"], "inspect_processing_error")
        self.assertEqual(state_after["current_run"], "run-0001")
        self.assertEqual(current_after, current_before)
        self.assertIn('"error_type": "inspect_processing_error"', failed_manifest)
        self.assertNotIn("PRIVATE-NO-PERIOD-PUBLISH", failed_manifest)

    def test_cli_malformed_state_returns_safe_inspection_failure_json(self):
        sensitive = "PRIVATE-MALFORMED-STATE"
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.init_case(Path(temp_dir), period=True)
            (case / "state.json").write_text(
                "{not-json:" + sensitive,
                encoding="utf-8",
            )

            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "open_tax_check.cli",
                    "inspect",
                    "--case",
                    str(case),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            failed_manifest = (
                case / "runs" / "run-0001" / "manifest.json"
            ).read_text(encoding="utf-8")

        self.assertEqual(process.returncode, 4, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["error_type"], "inspect_processing_error")
        self.assertEqual(result["next_action"], "inspect_processing_error")
        self.assertNotIn(
            sensitive,
            process.stdout + process.stderr + failed_manifest,
        )


if __name__ == "__main__":
    unittest.main()
