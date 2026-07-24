import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "examples/fictional-company/input/journal.csv"
EXPECTED_JOURNAL_ROWS = [
    ["凭证号", "记账日期", "科目编码", "科目名称", "业务说明", "借方发生额", "贷方发生额"],
    ["记-001", "2025-01-02", "1002", "银行存款", "确认销售收入", "1000000", "0"],
    ["记-001", "2025-01-02", "6001", "主营业务收入", "确认销售收入", "0", "1000000"],
    ["记-002", "2025-03-08", "6602", "管理费用-业务招待费", "客户接待", "20000", "0"],
    ["记-002", "2025-03-08", "1002", "银行存款", "客户接待", "0", "20000"],
    ["记-003", "2025-09-18", "6602", "管理费用-办公费", "购买中秋礼品及高档茶叶", "5000", "0"],
    ["记-003", "2025-09-18", "1002", "银行存款", "购买中秋礼品及高档茶叶", "0", "5000"],
]


class FictionalExampleTests(unittest.TestCase):
    def test_journal_matches_documented_balanced_fictional_case(self):
        with JOURNAL.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))

        self.assertEqual(rows, EXPECTED_JOURNAL_ROWS)
        vouchers = {}
        for row in rows[1:]:
            debit, credit = vouchers.setdefault(row[0], [0, 0])
            vouchers[row[0]] = [debit + int(row[5]), credit + int(row[6])]
        self.assertTrue(all(debit == credit for debit, credit in vouchers.values()))

    def test_example_readme_declares_complete_fictional_and_license_boundary(self):
        text = (ROOT / "examples/fictional-company/README.md").read_text(encoding="utf-8")

        self.assertIn("公司、交易、凭证、金额、人物和结论均为完全虚构", text)
        self.assertIn("不包含任何真实客户信息", text)
        self.assertIn("随本仓库按 Apache License 2.0 许可", text)
        self.assertIn("`--output` 必须指向不存在或空目录", text)

    def test_example_generates_and_inspects_fictional_xlsx_without_running_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_before = JOURNAL.read_bytes()
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_fictional_example.py"), "--output", temp_dir],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            generated = Path(temp_dir) / "input/journal.xlsx"
            self.assertTrue(generated.is_file())
            state = json.loads(
                (Path(temp_dir) / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "partial_scope_ready")
            self.assertTrue(
                (Path(temp_dir) / "work/workbook_inventory.json").is_file()
            )
            self.assertTrue(
                (Path(temp_dir) / "work/sheet_selection.json").is_file()
            )
            self.assertTrue(
                (Path(temp_dir) / "work/coverage_matrix.json").is_file()
            )
            self.assertFalse((Path(temp_dir) / "output/findings.json").exists())
            self.assertFalse((Path(temp_dir) / "output/report.md").exists())
            self.assertEqual(JOURNAL.read_bytes(), source_before)

    def test_example_refuses_nonempty_output_without_changing_existing_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            state = output_dir / "state.json"
            journal = output_dir / "input/journal.xlsx"
            marker = output_dir / "keep.me"
            journal.parent.mkdir()
            state.write_text("sentinel state", encoding="utf-8")
            journal.write_text("sentinel journal", encoding="utf-8")
            marker.write_text("sentinel marker", encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_fictional_example.py"), "--output", temp_dir],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(proc.returncode, 2)
            self.assertIn("output directory must not exist or must be empty", proc.stderr)
            self.assertEqual(state.read_text(encoding="utf-8"), "sentinel state")
            self.assertEqual(journal.read_text(encoding="utf-8"), "sentinel journal")
            self.assertEqual(marker.read_text(encoding="utf-8"), "sentinel marker")
            self.assertFalse((output_dir / "output").exists())

    def test_root_readme_runs_the_example_without_claiming_it_is_missing(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        section = text.split("## 运行虚构示例", 1)[1].split("## 输出", 1)[0]

        self.assertIn(r".\.venv\Scripts\python.exe scripts\run_fictional_example.py", section)
        self.assertIn("python scripts/run_fictional_example.py", section)
        self.assertIn("workbook_inventory.json", section)
        self.assertIn("coverage_matrix.json", section)
        self.assertIn("partial_scope_ready", section)
        self.assertIn("`--output` 必须指向不存在或空目录", section)
        self.assertNotIn("尚未提供", section)


if __name__ == "__main__":
    unittest.main()
