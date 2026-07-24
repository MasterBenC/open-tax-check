import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_tax_check.reviews import (
    ReviewError,
    confirm_sheet_selection,
    load_valid_sheet_selections,
)
from open_tax_check.pipeline import inspect_case
from open_tax_check.schemas import SCHEMA_VERSION


class SheetSelectionReviewTests(unittest.TestCase):
    def make_case(self, root: Path) -> Path:
        case = root / "case"
        (case / "work").mkdir(parents=True)
        state = {
            "schema_version": "0.1.0",
            "review_period": {"start": "2026-01-01", "end": "2026-12-31"},
        }
        (case / "state.json").write_text(
            json.dumps(state),
            encoding="utf-8",
        )
        self.write_inventory(case, self.inventory())
        return case

    def inventory(self, *, journal_digest: str = "a" * 64) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "workbooks": [
                {
                    "schema_version": SCHEMA_VERSION,
                    "source_id": journal_digest[:16],
                    "filename": "journal.xlsx",
                    "sha256": journal_digest,
                    "status": "inspected",
                    "sheets": [
                        {
                            "schema_version": SCHEMA_VERSION,
                            "source_id": journal_digest[:16],
                            "sheet_id": "sheet-0001",
                            "index": 1,
                            "name": "Journal",
                            "used_range": "A1:D20",
                            "header_rows": [3],
                            "candidate_header_rows": [[3], [5, 6]],
                        },
                        {
                            "schema_version": SCHEMA_VERSION,
                            "source_id": journal_digest[:16],
                            "sheet_id": "sheet-0002",
                            "index": 2,
                            "name": "Journal 2",
                            "used_range": "A1:D10",
                            "header_rows": [1],
                            "candidate_header_rows": [[1]],
                        },
                    ],
                },
                {
                    "schema_version": SCHEMA_VERSION,
                    "source_id": "b" * 16,
                    "filename": "trial-balance.xlsx",
                    "sha256": "b" * 64,
                    "status": "inspected",
                    "sheets": [
                        {
                            "schema_version": SCHEMA_VERSION,
                            "source_id": "b" * 16,
                            "sheet_id": "sheet-0001",
                            "index": 1,
                            "name": "Trial Balance",
                            "used_range": "A1:F30",
                            "header_rows": [2],
                            "candidate_header_rows": [[2]],
                        }
                    ],
                },
            ],
            "issues": [],
        }

    def write_inventory(self, case: Path, inventory: dict) -> None:
        (case / "work" / "workbook_inventory.json").write_text(
            json.dumps(inventory),
            encoding="utf-8",
        )

    def confirm(
        self,
        case: Path,
        *,
        role: str = "journal",
        source_id: str = "a" * 16,
        sheet_id: str = "sheet-0001",
        header_rows: tuple[int, ...] = (3,),
        hidden_rows: str = "exclude",
    ) -> dict:
        return confirm_sheet_selection(
            case,
            role=role,
            source_id=source_id,
            sheet_id=sheet_id,
            header_rows=header_rows,
            hidden_rows=hidden_rows,
            reviewer="host:user",
        )

    def test_confirmed_selection_is_bound_to_source_headers_period_and_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))

            decision = self.confirm(case)

            persisted = json.loads(
                (case / "reviews" / "sheet_selection.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(decision["binding"]["source_sha256"], "a" * 64)
        self.assertEqual(decision["binding"]["sheet_id"], "sheet-0001")
        self.assertEqual(decision["binding"]["sheet_name"], "Journal")
        self.assertEqual(decision["header_rows"], [3])
        self.assertEqual(
            decision["binding"]["review_period"],
            {"start": "2026-01-01", "end": "2026-12-31"},
        )
        self.assertEqual(
            decision["binding"]["selection_schema_version"],
            SCHEMA_VERSION,
        )
        fingerprint_payload = {
            key: value
            for key, value in decision["binding"].items()
            if key != "fingerprint"
        }
        expected = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(decision["binding"]["fingerprint"], expected)
        self.assertEqual(persisted["selections"], [decision])

    def test_hidden_data_requires_explicit_include_or_exclude(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))

            with self.assertRaisesRegex(
                ReviewError,
                "hidden_row_decision_required",
            ):
                self.confirm(case, hidden_rows="unspecified")

    def test_unknown_role_and_out_of_bounds_or_duplicate_headers_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))

            with self.assertRaisesRegex(ReviewError, "unknown_sheet_role"):
                self.confirm(case, role="receipts")
            with self.assertRaisesRegex(ReviewError, "header_row_out_of_bounds"):
                self.confirm(case, header_rows=(21,))
            with self.assertRaisesRegex(ReviewError, "invalid_header_rows"):
                self.confirm(case, header_rows=(3, 3))

    def test_same_role_can_select_multiple_sheets_but_same_sheet_cannot_overlap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))

            first = self.confirm(case)
            repeated = self.confirm(case)
            second = self.confirm(
                case,
                sheet_id="sheet-0002",
                header_rows=(1,),
            )
            with self.assertRaisesRegex(
                ReviewError,
                "overlapping_sheet_selection",
            ):
                self.confirm(
                    case,
                    role="trial_balance",
                )
            persisted = json.loads(
                (case / "reviews" / "sheet_selection.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(repeated, first)
        self.assertEqual(second["role"], "journal")
        self.assertEqual(len(persisted["selections"]), 2)

    def test_review_file_is_replaced_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            review_path = case / "reviews" / "sheet_selection.json"
            real_replace = __import__("os").replace
            replaced = []

            def recording_replace(source, destination):
                replaced.append((Path(source), Path(destination)))
                real_replace(source, destination)

            with patch(
                "open_tax_check.reviews.os.replace",
                side_effect=recording_replace,
            ):
                self.confirm(case)

        self.assertEqual(len(replaced), 1)
        self.assertEqual(replaced[0][1], review_path)
        self.assertNotEqual(replaced[0][0], review_path)

    def test_changed_digest_invalidates_only_affected_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            self.confirm(case)
            self.confirm(
                case,
                role="trial_balance",
                source_id="b" * 16,
                header_rows=(2,),
            )
            changed = self.inventory(journal_digest="c" * 64)

            valid, invalid = load_valid_sheet_selections(case, changed)

        self.assertNotIn("journal", valid)
        self.assertIn("source_digest_changed", invalid[0]["reasons"])
        self.assertEqual(len(valid["trial_balance"]), 1)

    def test_reconfirmation_after_digest_change_replaces_stale_binding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            self.confirm(case)
            changed = self.inventory(journal_digest="c" * 64)
            self.write_inventory(case, changed)

            current = self.confirm(case, source_id="c" * 16)
            valid, invalid = load_valid_sheet_selections(case, changed)
            persisted = json.loads(
                (case / "reviews" / "sheet_selection.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(
            current["binding"]["source_sha256"],
            "c" * 64,
        )
        self.assertEqual(persisted["selections"], [current])
        self.assertEqual(valid["journal"], [current])
        self.assertEqual(invalid, [])

    def test_sheet_header_and_period_changes_have_stable_invalidation_reasons(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            self.confirm(case)
            changed_header = self.inventory()
            changed_header["workbooks"][0]["sheets"][0]["header_rows"] = [4]

            valid, invalid = load_valid_sheet_selections(
                case,
                changed_header,
            )
            state = json.loads(
                (case / "state.json").read_text(encoding="utf-8")
            )
            state["review_period"] = {
                "start": "2027-01-01",
                "end": "2027-12-31",
            }
            (case / "state.json").write_text(
                json.dumps(state),
                encoding="utf-8",
            )
            _, period_invalid = load_valid_sheet_selections(
                case,
                self.inventory(),
            )

        self.assertEqual(valid, {})
        self.assertIn("header_rows_changed", invalid[0]["reasons"])
        self.assertIn(
            "review_period_changed",
            period_invalid[0]["reasons"],
        )

    def test_inspect_reuses_valid_decisions_and_invalidates_only_changed_source(self):
        from openpyxl import Workbook, load_workbook

        def make_workbook(path: Path, title: str, rows: tuple[tuple, ...]) -> None:
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = title
            for row in rows:
                sheet.append(row)
            sheet.row_dimensions[2].hidden = True
            workbook.save(path)
            workbook.close()

        journal_rows = (
            ("凭证号", "日期", "科目名称", "借方", "贷方"),
            ("记-001", "2026-01-02", "银行存款", 100, 0),
        )
        trial_balance_rows = (
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
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir) / "case"
            (case / "input").mkdir(parents=True)
            (case / "work").mkdir()
            (case / "output").mkdir()
            (case / "state.json").write_text(
                json.dumps(
                    {
                        "schema_version": "0.1.0",
                        "review_period": {
                            "start": "2026-01-01",
                            "end": "2026-12-31",
                        },
                        "completed_steps": ["init"],
                        "artifacts": [],
                        "current_run": None,
                        "run_history": [],
                    }
                ),
                encoding="utf-8",
            )
            journal_path = case / "input" / "journal.xlsx"
            trial_balance_path = case / "input" / "trial-balance.xlsx"
            make_workbook(journal_path, "Journal", journal_rows)
            make_workbook(
                trial_balance_path,
                "Trial Balance",
                trial_balance_rows,
            )

            first_code, _ = inspect_case(case)
            inventory = json.loads(
                (case / "work" / "workbook_inventory.json").read_text(
                    encoding="utf-8"
                )
            )
            by_filename = {
                workbook["filename"]: workbook
                for workbook in inventory["workbooks"]
            }
            journal = by_filename["journal.xlsx"]
            trial_balance = by_filename["trial-balance.xlsx"]
            confirm_sheet_selection(
                case,
                role="journal",
                source_id=journal["source_id"],
                sheet_id="sheet-0001",
                header_rows=(1,),
                hidden_rows="exclude",
                reviewer="host:user",
            )
            confirm_sheet_selection(
                case,
                role="trial_balance",
                source_id=trial_balance["source_id"],
                sheet_id="sheet-0001",
                header_rows=(1,),
                hidden_rows="exclude",
                reviewer="host:user",
            )

            reused_code, reused_result = inspect_case(case)
            reused_selection = json.loads(
                (case / "work" / "sheet_selection.json").read_text(
                    encoding="utf-8"
                )
            )
            workbook = load_workbook(journal_path)
            sheet = workbook.active
            sheet.append(("记-002", "2026-01-03", "库存现金", 50, 0))
            workbook.save(journal_path)
            workbook.close()

            changed_code, changed_result = inspect_case(case)
            changed_selection = json.loads(
                (case / "work" / "sheet_selection.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(first_code, 4)
        self.assertEqual(reused_code, 0)
        self.assertEqual(reused_result["status"], "partial_scope_ready")
        self.assertEqual(
            reused_selection["journal"][0]["decision_source"],
            "confirmed",
        )
        self.assertEqual(
            reused_selection["trial_balance"][0]["decision_source"],
            "confirmed",
        )
        self.assertEqual(changed_code, 4)
        self.assertEqual(changed_result["status"], "needs_sheet_selection")
        self.assertEqual(
            changed_selection["journal"][0]["decision_source"],
            "manual",
        )
        self.assertEqual(
            changed_selection["trial_balance"][0]["decision_source"],
            "confirmed",
        )

    def test_cli_select_sheet_has_stable_json_and_exit_codes_without_details(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            success = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "open_tax_check.cli",
                    "select-sheet",
                    "--case",
                    str(case),
                    "--role",
                    "journal",
                    "--source",
                    "a" * 16,
                    "--sheet",
                    "sheet-0001",
                    "--header-row",
                    "3",
                    "--hidden-rows",
                    "exclude",
                    "--reviewer",
                    "host:user",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            marker = "PRIVATE-REVIEW-DETAIL"
            with patch(
                "open_tax_check.cli.confirm_sheet_selection",
                side_effect=RuntimeError(marker),
            ):
                from open_tax_check.cli import main

                with patch.object(
                    sys,
                    "argv",
                    [
                        "opentaxcheck",
                        "select-sheet",
                        "--case",
                        str(case),
                        "--role",
                        "journal",
                        "--source",
                        "a" * 16,
                        "--sheet",
                        "sheet-0001",
                        "--header-row",
                        "3",
                        "--hidden-rows",
                        "exclude",
                        "--reviewer",
                        "host:user",
                    ],
                ), patch("builtins.print") as output:
                    failure_code = main()
                    failure = json.loads(output.call_args.args[0])

        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(
            json.loads(success.stdout)["status"],
            "sheet_selection_confirmed",
        )
        self.assertEqual(failure_code, 4)
        self.assertEqual(failure["error_type"], "select_sheet_failed")
        self.assertNotIn(marker, json.dumps(failure))


if __name__ == "__main__":
    unittest.main()
