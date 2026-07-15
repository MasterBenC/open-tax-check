import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from open_tax_check.cli import build_parser


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "open_tax_check.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_deep_ai_mode_is_accepted_by_cli_parser():
    args = build_parser().parse_args(["run", "--case", "case-a", "--ai-mode", "deep"])
    assert args.ai_mode == "deep"


class PipelineTests(unittest.TestCase):
    def test_run_creates_deterministic_findings_and_resumable_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case-a"
            init_proc = run_cli("init", "--case", str(case_dir), "--entity", "示例企业")
            self.assertEqual(init_proc.returncode, 0, init_proc.stderr)

            input_file = case_dir / "input" / "journal.csv"
            with input_file.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(["凭证号", "记账日期", "科目编码", "科目名称", "业务说明", "借方发生额", "贷方发生额"])
                writer.writerows(
                    [
                        ["记-001", "2025-01-02", "1002", "银行存款", "确认销售收入", "1000000", "0"],
                        ["记-001", "2025-01-02", "6001", "主营业务收入", "确认销售收入", "0", "1000000"],
                        ["记-002", "2025-03-08", "6602", "管理费用-业务招待费", "客户接待", "20000", "0"],
                        ["记-002", "2025-03-08", "1002", "银行存款", "客户接待", "0", "20000"],
                        ["记-003", "2025-09-18", "6602", "管理费用-办公费", "购买中秋礼品及高档茶叶", "5000", "0"],
                        ["记-003", "2025-09-18", "1002", "银行存款", "购买中秋礼品及高档茶叶", "0", "5000"],
                    ]
                )

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 0, run_proc.stderr)
            response = json.loads(run_proc.stdout)
            findings = json.loads((case_dir / "output" / "findings.json").read_text(encoding="utf-8"))
            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            rule_ids = [item["rule_id"] for item in findings]

            self.assertEqual(response["status"], "needs_professional_review")
            self.assertEqual(state["next_action"], "review_findings")
            self.assertIn("CIT-BE-001", rule_ids)
            self.assertIn("SEM-EXP-001", rule_ids)
            entertainment = next(item for item in findings if item["rule_id"] == "CIT-BE-001")
            self.assertEqual(entertainment["tax_adjustment"], "15000.00")
            self.assertTrue((case_dir / "work" / "normalized_journal.jsonl").exists())
            self.assertTrue((case_dir / "output" / "report.md").exists())

    def test_run_stops_when_header_mapping_is_unresolved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case-b"
            run_cli("init", "--case", str(case_dir), "--entity", "示例企业")
            input_file = case_dir / "input" / "journal.csv"
            input_file.write_text("凭证号,日期,神秘借方,神秘贷方\n记-001,2025-01-01,100,100\n", encoding="utf-8-sig")

            run_proc = run_cli("run", "--case", str(case_dir), "--ai-mode", "off")

            self.assertEqual(run_proc.returncode, 3)
            state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            mapping_review = json.loads((case_dir / "work" / "mapping_review.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "needs_mapping_review")
            self.assertIn("神秘借方", mapping_review["unresolved"])


if __name__ == "__main__":
    unittest.main()
