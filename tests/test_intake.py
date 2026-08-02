import os
import subprocess
import tempfile
import unittest
import unicodedata
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import open_tax_check.intake as intake_module
from open_tax_check.intake import build_coverage_matrix, inventory_inputs


class IntakeTests(unittest.TestCase):
    def test_inventory_hashes_supported_regular_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            (case / "input").mkdir()
            (case / "input" / "journal.csv").write_text("a,b\n1,2\n", encoding="utf-8")

            result = inventory_inputs(case)

        self.assertEqual(result.sources[0].status, "accepted")
        self.assertEqual(len(result.sources[0].sha256), 64)

    def test_inventory_rejects_more_than_fifty_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            (case / "input").mkdir()
            for index in range(51):
                (case / "input" / f"{index:02d}.csv").write_text("x\n", encoding="utf-8")

            result = inventory_inputs(case)

        self.assertEqual(result.status, "needs_intake_review")
        self.assertIn("too_many_input_entries", [issue.code for issue in result.issues])

    def test_baseline_coverage_never_treats_missing_as_no_risk(self):
        result = build_coverage_matrix(set())
        journal = next(item for item in result if item.evidence_role == "journal")

        self.assertEqual(journal.tier, "required")
        self.assertEqual(journal.status, "missing")
        self.assertEqual(journal.procedure_status, "not_performed_missing_evidence")

    def test_ordinary_unsupported_file_is_hashed_but_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            (case / "input").mkdir()
            (case / "input" / "contracts.pdf").write_bytes(b"not a workbook")

            result = inventory_inputs(case)

        source = result.sources[0]
        self.assertEqual(source.status, "present_unparsed")
        self.assertEqual(len(source.sha256), 64)

    def test_risk_triggered_evidence_is_not_applicable_until_requested(self):
        coverage = build_coverage_matrix(set())
        contracts = next(item for item in coverage if item.evidence_role == "contracts")

        self.assertEqual(contracts.tier, "risk_triggered")
        self.assertEqual(contracts.status, "not_applicable")
        self.assertEqual(contracts.procedure_status, "not_applicable")
        self.assertIsNone(contracts.trigger_rule_id)
        self.assertIsNone(contracts.requested_at)
        self.assertEqual(contracts.request_status, "not_requested")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_symlink_is_rejected_without_hashing_its_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            target = case / "outside.csv"
            target.write_text("outside", encoding="utf-8")
            link = input_dir / "journal.csv"
            try:
                os.symlink(target, link)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")

            result = inventory_inputs(case)

        self.assertEqual(result.sources, ())
        self.assertIn("rejected_symlink", [issue.code for issue in result.issues])

    def test_only_regular_office_lock_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            (case / "input").mkdir()
            (case / "input" / "~$journal.xlsx").write_bytes(b"lock")

            result = inventory_inputs(case)

        self.assertEqual(result.sources[0].status, "ignored_office_lock")
        self.assertEqual(result.sources[0].sha256, "")

    @unittest.skipUnless(os.name == "nt", "Windows junctions are only available on Windows")
    def test_input_directory_junction_is_rejected_without_enumerating_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case = root / "case"
            case.mkdir()
            target = root / "target"
            target.mkdir()
            protected = target / "journal.csv"
            protected.write_text("do not inspect", encoding="utf-8")
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(case / "input"), str(target)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if result.returncode != 0:
                self.skipTest(f"junctions are unavailable: {result.stderr.strip()}")
            self.addCleanup(
                subprocess.run,
                ["cmd", "/c", "rmdir", str(case / "input")],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            intake_result = inventory_inputs(case)

            self.assertEqual(intake_result.sources, ())
            self.assertIn("unsafe_input_directory", [issue.code for issue in intake_result.issues])
            self.assertEqual(protected.read_text(encoding="utf-8"), "do not inspect")

    def test_entry_limit_stops_before_requesting_fifty_second_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()

            def entries():
                for index in range(51):
                    yield input_dir / f"{index:02d}.csv"
                raise AssertionError("enumeration asked for entry 52")

            with patch.object(Path, "iterdir", return_value=entries()):
                result = inventory_inputs(case)

        self.assertIn("too_many_input_entries", [issue.code for issue in result.issues])

    def test_inventory_extracts_safe_zip_members_into_case_scoped_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "customer-materials.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("2025/journal.csv", "借方,贷方\n1,1\n")

            result = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})

            members = [source for source in result.sources if source.filename.endswith("journal.csv")]
            self.assertEqual(result.issues, ())
            self.assertEqual(len(members), 1)
            member = members[0]
            self.assertEqual(member.status, "accepted")
            self.assertTrue(member.filename.startswith(".opentaxcheck-archive-"))
            self.assertTrue((input_dir / member.filename).is_file())
            self.assertEqual((input_dir / member.filename).read_text(encoding="utf-8"), "借方,贷方\n1,1\n")

    def test_inventory_can_reuse_an_unchanged_archive_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "customer-materials.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("journal.csv", "a,b\n1,1\n")

            first = inventory_inputs(case)
            second = inventory_inputs(case)

            self.assertEqual(first.issues, ())
            self.assertEqual(second.issues, ())
            self.assertEqual(
                [source.filename for source in first.sources],
                [source.filename for source in second.sources],
            )

    def test_inventory_accepts_deep_distinct_archive_members(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "deep-members.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                for index in range(64):
                    member = "/".join(
                        [f"branch-{index:03d}", "a", "b", "c", "d", "e", "f", "journal.csv"]
                    )
                    archive.writestr(member, "a,b\n1,1\n")

            first = inventory_inputs(case)
            second = inventory_inputs(case)

            self.assertEqual(first.issues, ())
            self.assertEqual(second.issues, ())
            self.assertEqual(
                sum(source.status == "accepted" for source in first.sources), 64
            )

    def test_inventory_rejects_windows_reserved_name_with_space_before_extension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "reserved-space.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("COM1 .csv", "a,b\n1,1\n")

            result = inventory_inputs(case)

            self.assertIn("archive_unsafe_member_name", [issue.code for issue in result.issues])

    def test_inventory_rejects_zip_path_traversal_without_writing_outside_case(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "unsafe.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("../outside.csv", "not extracted")

            result = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})

            self.assertFalse(any(source.status == "accepted" for source in result.sources))
            self.assertIn("archive_unsafe_member_name", [issue.code for issue in result.issues])
            self.assertFalse((case / "outside.csv").exists())

    def test_inventory_rejects_zip_duplicate_members_after_windows_normalization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "duplicate.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("Reports/Journal.csv", "a,b\n1,1\n")
                archive.writestr("reports\\journal.csv", "a,b\n2,2\n")

            result = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})

            self.assertFalse(any(source.status == "accepted" for source in result.sources))
            self.assertIn("archive_duplicate_member", [issue.code for issue in result.issues])

    def test_inventory_rejects_zip_symlink_members(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "link.zip"
            item = ZipInfo("journal.csv")
            item.create_system = 3
            item.external_attr = (0o120777 << 16) | 0xA000
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr(item, "target")

            result = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})

            self.assertFalse(any(source.status == "accepted" for source in result.sources))
            self.assertIn("archive_link_member", [issue.code for issue in result.issues])

    def test_inventory_rejects_tampered_archive_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "customer-materials.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("journal.csv", "a,b\n1,1\n")

            first = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})
            member = next(source for source in first.sources if source.filename.endswith("journal.csv"))
            (input_dir / member.filename).write_text("tampered", encoding="utf-8")

            second = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})

            self.assertFalse(any(source.status == "accepted" for source in second.sources))
            self.assertIn("archive_cache_changed", [issue.code for issue in second.issues])

    def test_inventory_rejects_archive_file_directory_collision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "collision.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("journal.csv", "a,b\n1,1\n")
                archive.writestr("journal.csv/part.csv", "a,b\n2,2\n")

            result = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})

            self.assertFalse(any(source.status == "accepted" for source in result.sources))
            self.assertIn("archive_member_path_collision", [issue.code for issue in result.issues])

    def test_inventory_accepts_explicit_zip_directory_before_child(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "directories.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("records/", b"")
                archive.writestr("records/journal.csv", "a,b\n1,1\n")

            result = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})

            self.assertEqual(result.issues, ())
            self.assertTrue(any(source.status == "accepted" for source in result.sources))

    def test_inventory_rejects_windows_equivalent_zip_member_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "portable-collision.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("records/journal.csv", "a,b\n1,1\n")
                archive.writestr("records/JOURNAL.CSV. ", "a,b\n2,2\n")

            result = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})

            self.assertFalse(any(source.status == "accepted" for source in result.sources))
            self.assertIn("archive_duplicate_member", [issue.code for issue in result.issues])

    def test_inventory_rejects_windows_reserved_zip_member_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "reserved.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("CON.csv", "a,b\n1,1\n")

            result = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})

            self.assertIn("archive_unsafe_member_name", [issue.code for issue in result.issues])

    def test_inventory_rejects_extended_windows_reserved_zip_member_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "reserved-extended.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("CONIN$.csv", "a,b\n1,1\n")

            result = inventory_inputs(case)

            self.assertIn("archive_unsafe_member_name", [issue.code for issue in result.issues])

    @unittest.skipUnless(os.name == "nt", "Windows junctions are only available on Windows")
    def test_inventory_rejects_exact_archive_cache_junction_without_raising(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case = root / "case"
            input_dir = case / "input"
            input_dir.mkdir(parents=True)
            archive_path = input_dir / "customer-materials.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("journal.csv", "a,b\n1,1\n")
            first = inventory_inputs(case)
            member = next(source for source in first.sources if source.filename.endswith("journal.csv"))
            cache_dir = input_dir / Path(member.filename).parts[0]
            (cache_dir / "journal.csv").unlink()
            cache_dir.rmdir()
            outside = root / "outside"
            outside.mkdir()
            (outside / "journal.csv").write_text("a,b\n9,9\n", encoding="utf-8")
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(cache_dir), str(outside)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if result.returncode != 0:
                self.skipTest(f"junctions are unavailable: {result.stderr.strip()}")
            self.addCleanup(
                subprocess.run,
                ["cmd", "/c", "rmdir", str(cache_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            second = inventory_inputs(case)

            self.assertIn("archive_cache_unsafe", [issue.code for issue in second.issues])

    def test_inventory_rejects_unicode_equivalent_zip_member_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "unicode-collision.zip"
            composed = "caf\u00e9.csv"
            decomposed = unicodedata.normalize("NFD", composed)
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr(composed, "a,b\n1,1\n")
                archive.writestr(decomposed, "a,b\n2,2\n")

            result = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})

            self.assertIn("archive_duplicate_member", [issue.code for issue in result.issues])

    def test_inventory_rejects_windows_forbidden_character_in_zip_member(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "forbidden-character.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("records/journal:2025.csv", "a,b\n1,1\n")

            result = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})

            self.assertIn("archive_unsafe_member_name", [issue.code for issue in result.issues])

    def test_archive_cache_does_not_consume_customer_input_entry_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "customer-materials.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("journal.csv", "a,b\n1,1\n")
            for index in range(49):
                (input_dir / f"evidence-{index:02d}.txt").write_text("x", encoding="utf-8")

            first = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})
            second = inventory_inputs(case, {"start": "2025-01-01", "end": "2025-12-31"})

            self.assertNotIn("too_many_input_entries", [issue.code for issue in first.issues])
            self.assertNotIn("too_many_input_entries", [issue.code for issue in second.issues])

    def test_forged_archive_cache_directory_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            (input_dir / ".opentaxcheck-archive-not-a-digest").mkdir()

            result = inventory_inputs(case)

            self.assertIn("rejected_directory", [issue.code for issue in result.issues])

    def test_archive_member_budget_is_aggregate_across_case(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            for index in range(2):
                with ZipFile(input_dir / f"archive-{index}.zip", "w") as archive:
                    archive.writestr(f"journal-{index}.csv", "a,b\n1,1\n")

            with patch.object(intake_module, "MAX_ARCHIVE_MEMBERS", 1):
                result = inventory_inputs(case)

            self.assertIn("archive_member_limit_exceeded", [issue.code for issue in result.issues])

    def test_archive_uses_the_bytes_that_were_hashed_if_path_is_replaced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "customer-materials.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("journal.csv", "a,b\n1,1\n")
            replacement = case / "replacement.zip"
            with ZipFile(replacement, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("journal.csv", "a,b\n9,9\n")
            original_snapshot = intake_module._snapshot_regular_file

            def snapshot_then_replace(path, metadata):
                snapshot = original_snapshot(path, metadata)
                if path == archive_path:
                    archive_path.write_bytes(replacement.read_bytes())
                return snapshot

            with patch.object(
                intake_module,
                "_snapshot_regular_file",
                side_effect=snapshot_then_replace,
            ):
                result = inventory_inputs(case)

            member = next(source for source in result.sources if source.filename.endswith("journal.csv"))
            self.assertEqual(
                (input_dir / member.filename).read_text(encoding="utf-8"),
                "a,b\n1,1\n",
            )

    def test_inventory_rejects_extra_file_in_archive_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            input_dir = case / "input"
            input_dir.mkdir()
            archive_path = input_dir / "customer-materials.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("journal.csv", "a,b\n1,1\n")
            first = inventory_inputs(case)
            member = next(source for source in first.sources if source.filename.endswith("journal.csv"))
            cache_dir = input_dir / Path(member.filename).parts[0]
            (cache_dir / "extra.csv").write_text("unexpected", encoding="utf-8")

            second = inventory_inputs(case)

            self.assertIn("archive_cache_changed", [issue.code for issue in second.issues])

    @unittest.skipUnless(os.name == "nt", "Windows junctions are only available on Windows")
    def test_inventory_rejects_junction_inside_archive_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case = root / "case"
            input_dir = case / "input"
            input_dir.mkdir(parents=True)
            archive_path = input_dir / "customer-materials.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("records/journal.csv", "a,b\n1,1\n")
            first = inventory_inputs(case)
            member = next(source for source in first.sources if source.filename.endswith("journal.csv"))
            cache_dir = input_dir / Path(member.filename).parts[0]
            records_dir = cache_dir / "records"
            (records_dir / "journal.csv").unlink()
            records_dir.rmdir()
            outside = root / "outside"
            outside.mkdir()
            (outside / "journal.csv").write_text("a,b\n9,9\n", encoding="utf-8")
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(records_dir), str(outside)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if result.returncode != 0:
                self.skipTest(f"junctions are unavailable: {result.stderr.strip()}")
            self.addCleanup(
                subprocess.run,
                ["cmd", "/c", "rmdir", str(records_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            second = inventory_inputs(case)

            self.assertIn("archive_cache_changed", [issue.code for issue in second.issues])


if __name__ == "__main__":
    unittest.main()
