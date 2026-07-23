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
from open_tax_check.workbooks import WorkbookError


JOURNAL_ROWS = (
    ("凭证号", "日期", "科目名称", "借方", "贷方"),
    ("记-001", "2026-01-02", "银行存款", 100, 0),
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

            inventory = (
                case / "work" / "workbook_inventory.json"
            ).read_text(encoding="utf-8")

        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "needs_intake_review")
        self.assertEqual(result["next_action"], "resolve_workbook_issues")
        self.assertIn('"code": "corrupt_workbook"', inventory)
        self.assertNotIn(sensitive, inventory + json.dumps(result))

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
        self.assertEqual(state["current_run"], "run-0001")
        self.assertEqual(current_after, current_before)


if __name__ == "__main__":
    unittest.main()
