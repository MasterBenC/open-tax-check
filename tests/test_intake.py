import os
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
