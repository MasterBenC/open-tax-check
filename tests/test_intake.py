import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
