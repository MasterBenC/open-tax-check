import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_tax_check.case import init_case
from open_tax_check.reviews import (
    ReviewError,
    confirm_sheet_selection,
    load_valid_sheet_selections,
)
from open_tax_check.account_classification import confirm_account_classifications
from open_tax_check.pipeline import inspect_case
from open_tax_check.schemas import SCHEMA_VERSION


class SheetSelectionReviewTests(unittest.TestCase):
    def make_directory_symlink(self, link: Path, target: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=True)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"directory symlinks are unavailable: {error}")

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

    def write_review(self, case: Path, review: dict) -> None:
        (case / "reviews").mkdir(exist_ok=True)
        (case / "reviews" / "sheet_selection.json").write_text(
            json.dumps(review),
            encoding="utf-8",
        )

    def resign(self, decision: dict) -> None:
        payload = {
            key: value
            for key, value in decision["binding"].items()
            if key != "fingerprint"
        }
        decision["binding"]["fingerprint"] = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

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
        self.assertEqual(decision["binding"]["role"], "journal")
        self.assertEqual(decision["binding"]["hidden_rows"], "exclude")
        self.assertEqual(decision["binding"]["reviewer"], "host:user")
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

    def test_confirmation_allows_symlinked_case_parent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            actual_parent = root / "actual"
            actual_parent.mkdir()
            case = self.make_case(actual_parent)
            alias_parent = root / "alias"
            self.make_directory_symlink(alias_parent, actual_parent)

            decision = self.confirm(alias_parent / "case")

            self.assertEqual(decision["role"], "journal")
            self.assertTrue(
                (case / "reviews" / "sheet_selection.json").is_file()
            )

    def test_account_classification_rejects_a_reparse_review_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case = self.make_case(root)
            (case / "reviews").mkdir()
            target = root / "outside.json"
            target.write_text("{}", encoding="utf-8")
            review_path = case / "reviews" / "account_classification.json"
            try:
                review_path.symlink_to(target)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"file symlinks are unavailable: {error}")

            with self.assertRaisesRegex(ReviewError, "unsafe_review_path"):
                confirm_account_classifications(case, {})

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-only")
    def test_confirmation_allows_case_parent_junction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            actual_parent = root / "actual"
            actual_parent.mkdir()
            case = self.make_case(actual_parent)
            alias_parent = root / "alias"
            result = subprocess.run(
                [
                    "cmd",
                    "/c",
                    "mklink",
                    "/J",
                    str(alias_parent),
                    str(actual_parent),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if result.returncode != 0:
                self.skipTest(f"junctions unavailable: {result.stderr.strip()}")
            try:
                decision = self.confirm(alias_parent / "case")

                self.assertEqual(decision["role"], "journal")
                self.assertTrue(
                    (case / "reviews" / "sheet_selection.json").is_file()
                )
            finally:
                subprocess.run(
                    ["cmd", "/c", "rmdir", str(alias_parent)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )

    def test_hidden_data_requires_explicit_include_or_exclude(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))

            with self.assertRaisesRegex(
                ReviewError,
                "hidden_row_decision_required",
            ):
                self.confirm(case, hidden_rows="unspecified")
            with self.assertRaisesRegex(ReviewError, "reviewer_required"):
                confirm_sheet_selection(
                    case,
                    role="journal",
                    source_id="a" * 16,
                    sheet_id="sheet-0001",
                    header_rows=(3,),
                    hidden_rows="exclude",
                    reviewer="",
                )

    def test_unknown_role_and_out_of_bounds_or_duplicate_headers_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))

            with self.assertRaisesRegex(ReviewError, "unknown_sheet_role"):
                self.confirm(case, role="receipts")
            with self.assertRaisesRegex(ReviewError, "header_row_out_of_bounds"):
                self.confirm(case, header_rows=(21,))
            with self.assertRaisesRegex(ReviewError, "invalid_header_rows"):
                self.confirm(case, header_rows=(3, 3))
            for invalid in ((3, 5), (5, 3), (1, 2, 3, 4)):
                with self.subTest(header_rows=invalid):
                    with self.assertRaisesRegex(
                        ReviewError,
                        "invalid_header_rows",
                    ):
                        self.confirm(case, header_rows=invalid)

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
            canonical_review_path = review_path.resolve()
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
                self.confirm(
                    case,
                    sheet_id="sheet-0002",
                    header_rows=(1,),
                )

        self.assertEqual(len(replaced), 2)
        self.assertTrue(
            all(
                destination == canonical_review_path
                for _, destination in replaced
            )
        )
        self.assertTrue(
            all(
                source.parent == canonical_review_path.parent
                for source, _ in replaced
            )
        )
        self.assertEqual(len({source.name for source, _ in replaced}), 2)

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

    def test_manual_header_choice_survives_heuristic_change_but_period_does_not(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            decision = self.confirm(case, header_rows=(5, 6))
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

        self.assertEqual(valid["journal"], [decision])
        self.assertEqual(invalid, [])
        self.assertIn(
            "review_period_changed",
            period_invalid[0]["reasons"],
        )

    def test_changed_source_filename_has_stable_invalidation_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            self.confirm(case)
            renamed = self.inventory()
            renamed["workbooks"][0]["filename"] = "renamed-journal.xlsx"

            valid, invalid = load_valid_sheet_selections(case, renamed)

        self.assertEqual(valid, {})
        self.assertIn("source_filename_changed", invalid[0]["reasons"])

    def test_prior_binding_schema_is_invalidated_not_treated_as_malformed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            decision = self.confirm(case)
            decision["binding"]["selection_schema_version"] = "0.1.0"
            self.resign(decision)
            self.write_review(
                case,
                {
                    "schema_version": SCHEMA_VERSION,
                    "selections": [decision],
                },
            )

            valid, invalid = load_valid_sheet_selections(
                case,
                self.inventory(),
            )

        self.assertEqual(valid, {})
        self.assertIn("selection_schema_changed", invalid[0]["reasons"])

    def test_malformed_or_tampered_review_file_has_one_stable_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            decision = self.confirm(case)
            valid_review = {
                "schema_version": SCHEMA_VERSION,
                "selections": [decision],
            }
            malformed = []

            wrong_schema = json.loads(json.dumps(valid_review))
            wrong_schema["schema_version"] = "9.9.9"
            malformed.append(wrong_schema)
            malformed.append(
                {"schema_version": SCHEMA_VERSION, "selections": [None]}
            )
            for field, value in (
                ("role", "receipts"),
                ("role", ["journal"]),
                ("hidden_rows", "unspecified"),
                ("hidden_rows", ["exclude"]),
                ("reviewer", ""),
                ("reviewer", {"host": "user"}),
                ("header_rows", [3, 5]),
            ):
                changed = json.loads(json.dumps(valid_review))
                changed["selections"][0][field] = value
                changed["selections"][0]["binding"][field] = value
                self.resign(changed["selections"][0])
                malformed.append(changed)
            missing_binding_field = json.loads(json.dumps(valid_review))
            del missing_binding_field["selections"][0]["binding"][
                "source_sha256"
            ]
            malformed.append(missing_binding_field)
            invalid_sha = json.loads(json.dumps(valid_review))
            invalid_sha["selections"][0]["binding"]["source_sha256"] = "x" * 64
            self.resign(invalid_sha["selections"][0])
            malformed.append(invalid_sha)
            invalid_period = json.loads(json.dumps(valid_review))
            invalid_period["selections"][0]["binding"]["review_period"] = {
                "start": "2026-01-01"
            }
            self.resign(invalid_period["selections"][0])
            malformed.append(invalid_period)
            bad_fingerprint = json.loads(json.dumps(valid_review))
            bad_fingerprint["selections"][0]["binding"]["fingerprint"] = "0" * 64
            malformed.append(bad_fingerprint)
            top_binding_mismatch = json.loads(json.dumps(valid_review))
            top_binding_mismatch["selections"][0]["reviewer"] = "host:other"
            malformed.append(top_binding_mismatch)
            overlap = json.loads(json.dumps(valid_review))
            duplicate = json.loads(json.dumps(decision))
            duplicate["role"] = "trial_balance"
            duplicate["binding"]["role"] = "trial_balance"
            self.resign(duplicate)
            overlap["selections"].append(duplicate)
            malformed.append(overlap)

            for index, review in enumerate(malformed):
                with self.subTest(index=index):
                    self.write_review(case, review)
                    with self.assertRaisesRegex(
                        ReviewError,
                        "^invalid_sheet_selection_review$",
                    ):
                        load_valid_sheet_selections(
                            case,
                            self.inventory(),
                        )
            with self.assertRaisesRegex(
                ReviewError,
                "^invalid_sheet_selection_review$",
            ):
                self.confirm(case)

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-only")
    def test_reviews_junction_is_rejected_without_external_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case = self.make_case(root)
            external = root / "external"
            external.mkdir()
            reviews = case / "reviews"
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(reviews), str(external)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if result.returncode != 0:
                self.skipTest(f"junctions unavailable: {result.stderr.strip()}")
            try:
                with self.assertRaisesRegex(
                    ReviewError,
                    "unsafe_review_path",
                ):
                    self.confirm(case)
                self.assertEqual(list(external.iterdir()), [])
            finally:
                subprocess.run(
                    ["cmd", "/c", "rmdir", str(reviews)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-only")
    def test_work_junction_is_rejected_before_inventory_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case = self.make_case(root)
            original_work = case / "work"
            external = root / "external-work"
            original_work.rename(external)
            result = subprocess.run(
                [
                    "cmd",
                    "/c",
                    "mklink",
                    "/J",
                    str(original_work),
                    str(external),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if result.returncode != 0:
                external.rename(original_work)
                self.skipTest(f"junctions unavailable: {result.stderr.strip()}")
            try:
                with self.assertRaisesRegex(
                    ReviewError,
                    "unsafe_review_path",
                ):
                    self.confirm(case)
            finally:
                subprocess.run(
                    ["cmd", "/c", "rmdir", str(original_work)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                external.rename(original_work)

    def test_shifted_invalid_sheet_stays_manual_beside_valid_confirmed_sheets(self):
        from openpyxl import Workbook, load_workbook

        def make_journal(path: Path, voucher: str) -> None:
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Journal"
            sheet.append(("凭证号", "日期", "科目名称", "借方", "贷方"))
            sheet.append((voucher, "2026-01-02", "银行存款", 100, 0))
            sheet.row_dimensions[2].hidden = True
            workbook.save(path)
            workbook.close()

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
            first_path = case / "input" / "journal-a.xlsx"
            second_path = case / "input" / "journal-b.xlsx"
            third_path = case / "input" / "journal-c.xlsx"
            make_journal(first_path, "记-001")
            make_journal(second_path, "记-101")
            make_journal(third_path, "记-201")
            inspect_case(case)
            inventory = json.loads(
                (case / "work" / "workbook_inventory.json").read_text(
                    encoding="utf-8"
                )
            )
            for workbook in inventory["workbooks"]:
                confirm_sheet_selection(
                    case,
                    role="journal",
                    source_id=workbook["source_id"],
                    sheet_id="sheet-0001",
                    header_rows=(1,),
                    hidden_rows="exclude",
                    reviewer="host:user",
                )
            workbook = load_workbook(first_path)
            workbook["Journal"].append(
                ("记-002", "2026-01-03", "库存现金", 50, 0)
            )
            for index in range(8):
                notes = workbook.create_sheet(f"Notes {index + 1}", 0)
                notes["A1"] = f"fictional note {index + 1}"
            workbook.save(first_path)
            workbook.close()

            code, result = inspect_case(case)
            selection = json.loads(
                (case / "work" / "sheet_selection.json").read_text(
                    encoding="utf-8"
                )
            )
            by_filename = {
                item["filename"]: item for item in selection["journal"]
            }

        self.assertEqual(code, 4)
        self.assertEqual(
            result["status"],
            "needs_sheet_selection",
            result,
        )
        self.assertEqual(
            by_filename["journal-a.xlsx"]["decision_source"],
            "manual",
        )
        self.assertEqual(
            by_filename["journal-a.xlsx"]["sheet_id"],
            "sheet-0009",
        )
        self.assertEqual(
            by_filename["journal-b.xlsx"]["decision_source"],
            "confirmed",
        )
        self.assertEqual(
            by_filename["journal-c.xlsx"]["decision_source"],
            "confirmed",
        )
        self.assertEqual(len(selection["journal"]), 3)
        self.assertEqual(
            len(
                {
                    (item["source_id"], item["sheet_id"])
                    for item in selection["journal"]
                }
            ),
            3,
        )

    def test_inspect_preserves_new_same_role_candidate_after_existing_confirmation(
        self,
    ):
        from openpyxl import Workbook

        def make_journal(path: Path, voucher: str) -> None:
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Journal"
            sheet.append(("凭证号", "日期", "科目名称", "借方", "贷方"))
            sheet.append((voucher, "2026-01-02", "银行存款", 100, 0))
            workbook.save(path)
            workbook.close()

        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir) / "case"
            init_case(
                case,
                "虚构企业",
                "2026-01-01",
                "2026-12-31",
            )
            make_journal(case / "input" / "journal-a.xlsx", "记-001")

            first_code, _ = inspect_case(case)
            first_inventory = json.loads(
                (case / "work" / "workbook_inventory.json").read_text(
                    encoding="utf-8"
                )
            )
            first_source = first_inventory["workbooks"][0]
            confirm_sheet_selection(
                case,
                role="journal",
                source_id=first_source["source_id"],
                sheet_id="sheet-0001",
                header_rows=(1,),
                hidden_rows="exclude",
                reviewer="host:user",
            )

            make_journal(case / "input" / "journal-b.xlsx", "记-101")
            added_code, added_result = inspect_case(case)
            added_inventory = json.loads(
                (case / "work" / "workbook_inventory.json").read_text(
                    encoding="utf-8"
                )
            )
            added_selection = json.loads(
                (case / "work" / "sheet_selection.json").read_text(
                    encoding="utf-8"
                )
            )
            added_by_filename = {
                item["filename"]: item
                for item in added_selection["journal"]
            }
            second_source = next(
                workbook
                for workbook in added_inventory["workbooks"]
                if workbook["filename"] == "journal-b.xlsx"
            )
            confirm_sheet_selection(
                case,
                role="journal",
                source_id=second_source["source_id"],
                sheet_id="sheet-0001",
                header_rows=(1,),
                hidden_rows="exclude",
                reviewer="host:user",
            )

            confirmed_code, confirmed_result = inspect_case(case)
            confirmed_selection = json.loads(
                (case / "work" / "sheet_selection.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(first_code, 0)
        self.assertEqual(added_code, 4)
        self.assertEqual(added_result["status"], "needs_sheet_selection")
        self.assertEqual(
            added_result["next_action"],
            "select_journal_sheet",
        )
        self.assertEqual(
            [item["filename"] for item in added_selection["journal"]],
            ["journal-a.xlsx", "journal-b.xlsx"],
        )
        self.assertEqual(
            added_by_filename["journal-a.xlsx"]["decision_source"],
            "confirmed",
        )
        self.assertEqual(
            added_by_filename["journal-b.xlsx"]["decision_source"],
            "manual",
        )
        self.assertEqual(
            len(
                {
                    (item["source_id"], item["sheet_id"])
                    for item in added_selection["journal"]
                }
            ),
            2,
        )
        self.assertEqual(confirmed_code, 0)
        self.assertEqual(
            confirmed_result["status"],
            "partial_scope_ready",
        )
        self.assertEqual(
            [item["filename"] for item in confirmed_selection["journal"]],
            ["journal-a.xlsx", "journal-b.xlsx"],
        )
        self.assertTrue(
            all(
                item["decision_source"] == "confirmed"
                for item in confirmed_selection["journal"]
            )
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
