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

    def test_example_generates_expected_findings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_before = JOURNAL.read_bytes()
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_fictional_example.py"), "--output", temp_dir],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            findings = json.loads(
                (Path(temp_dir) / "output/findings.json").read_text(encoding="utf-8")
            )
            by_rule = {item["rule_id"]: item for item in findings}
            self.assertEqual(by_rule["CIT-BE-001"]["tax_adjustment"], "15000.00")
            self.assertIn("SEM-EXP-001", by_rule)
            state = json.loads((Path(temp_dir) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "needs_professional_review")
            self.assertTrue((Path(temp_dir) / "output/report.md").is_file())
            self.assertEqual(JOURNAL.read_bytes(), source_before)

    def test_root_readme_runs_the_example_without_claiming_it_is_missing(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        section = text.split("## Run the fictional example", 1)[1].split("## Outputs", 1)[0]

        self.assertIn(r".\.venv\Scripts\python.exe scripts\run_fictional_example.py", section)
        self.assertIn("python scripts/run_fictional_example.py", section)
        self.assertIn("output/findings.json", section)
        self.assertIn("output/report.md", section)
        self.assertIn("needs_professional_review", section)
        self.assertNotIn("尚未提供", section)


if __name__ == "__main__":
    unittest.main()
