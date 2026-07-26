import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from open_tax_check import case
from open_tax_check.case import init_case


class CaseTests(unittest.TestCase):
    def _make_directory_symlink(self, link: Path, target: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=True)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"directory symlinks are unavailable: {error}")

    def test_init_creates_resumable_case(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case-a"
            state = init_case(case_dir, "示例企业")

            saved = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "ready_for_input")
            self.assertEqual(saved["entity_name"], "示例企业")
            self.assertEqual(saved["next_action"], "add_source_files")
            self.assertTrue((case_dir / "input").is_dir())
            self.assertTrue((case_dir / "work").is_dir())
            self.assertTrue((case_dir / "output").is_dir())

    def test_init_refuses_an_existing_nonempty_case_without_changing_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case-a"
            init_case(case_dir, "原企业")
            marker = case_dir / "input" / "keep.me"
            marker.write_text("original input", encoding="utf-8")
            original_state = (case_dir / "state.json").read_bytes()

            with self.assertRaisesRegex(FileExistsError, "case directory must be empty"):
                init_case(case_dir, "新企业")

            self.assertEqual((case_dir / "state.json").read_bytes(), original_state)
            self.assertEqual(marker.read_text(encoding="utf-8"), "original input")

    def test_init_records_review_period(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case"

            state = init_case(case_dir, "虚构企业", "2025-01-01", "2025-12-31")

            self.assertEqual(
                state["review_period"],
                {"start": "2025-01-01", "end": "2025-12-31"},
            )

    def test_init_allows_symlinked_parent_and_uses_resolved_case_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            actual_parent = root / "actual"
            actual_parent.mkdir()
            alias_parent = root / "alias"
            self._make_directory_symlink(alias_parent, actual_parent)

            state = init_case(alias_parent / "case", "虚构企业")

            self.assertEqual(state["status"], "ready_for_input")
            self.assertTrue((actual_parent / "case" / "state.json").is_file())

    @unittest.skipUnless(os.name == "nt", "Windows junctions are only available on Windows")
    def test_init_allows_parent_junction_and_uses_resolved_case_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            actual_parent = root / "actual"
            actual_parent.mkdir()
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
                self.skipTest(f"junctions are unavailable: {result.stderr.strip()}")
            try:
                state = init_case(alias_parent / "case", "虚构企业")

                self.assertEqual(state["status"], "ready_for_input")
                self.assertTrue((actual_parent / "case" / "state.json").is_file())
            finally:
                subprocess.run(
                    ["cmd", "/c", "rmdir", str(alias_parent)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )

    def test_scope_rejects_reversed_period_without_changing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case"
            init_case(case_dir, "虚构企业")
            before = (case_dir / "state.json").read_bytes()

            with self.assertRaisesRegex(ValueError, "invalid_review_period"):
                case.set_case_scope(case_dir, "2025-12-31", "2025-01-01")

            self.assertEqual((case_dir / "state.json").read_bytes(), before)

    def test_init_rejects_incomplete_review_period(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case"

            with self.assertRaisesRegex(ValueError, "incomplete_review_period"):
                init_case(case_dir, "虚构企业", "2025-01-01")

    def test_init_rejects_empty_directory_symlink_without_changing_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            target = temp_path / "target"
            target.mkdir()
            case_dir = temp_path / "case"
            self._make_directory_symlink(case_dir, target)

            with self.assertRaisesRegex(ValueError, "unsafe_case_path"):
                init_case(case_dir, "虚构企业")

            self.assertEqual(list(target.iterdir()), [])

    def test_scope_rejects_directory_symlink_without_changing_target_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            target = temp_path / "target"
            init_case(target, "虚构企业")
            before = (target / "state.json").read_bytes()
            case_dir = temp_path / "case"
            self._make_directory_symlink(case_dir, target)

            with self.assertRaisesRegex(ValueError, "unsafe_case_path"):
                case.set_case_scope(case_dir, "2025-01-01", "2025-12-31")

            self.assertEqual((target / "state.json").read_bytes(), before)

    @unittest.skipUnless(os.name == "nt", "Windows junctions are only available on Windows")
    def test_scope_rejects_junction_without_changing_target_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            target = temp_path / "target"
            init_case(target, "虚构企业")
            before = (target / "state.json").read_bytes()
            case_dir = temp_path / "case"
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(case_dir), str(target)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if result.returncode != 0:
                self.skipTest(f"junctions are unavailable: {result.stderr.strip()}")
            self.addCleanup(
                subprocess.run,
                ["cmd", "/c", "rmdir", str(case_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unsafe_case_path"):
                case.set_case_scope(case_dir, "2025-01-01", "2025-12-31")

            self.assertEqual((target / "state.json").read_bytes(), before)

    def test_scope_rejects_state_symlink_without_changing_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            case_dir = temp_path / "case"
            init_case(case_dir, "虚构企业")
            target = temp_path / "state-target.json"
            target.write_bytes((case_dir / "state.json").read_bytes())
            (case_dir / "state.json").unlink()
            try:
                (case_dir / "state.json").symlink_to(target)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"file symlinks are unavailable: {error}")

            before = target.read_bytes()
            with self.assertRaisesRegex(ValueError, "unsafe_case_path"):
                case.set_case_scope(case_dir, "2025-01-01", "2025-12-31")

            self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
