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


if __name__ == "__main__":
    unittest.main()
