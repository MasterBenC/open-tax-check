import json
import tempfile
import unittest
from pathlib import Path

from open_tax_check.case import init_case


class CaseTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
