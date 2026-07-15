import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def assert_privacy_option_is_required(test_case, text):
    lines = text.splitlines()
    privacy_labels = [
        index
        for index, line in enumerate(lines)
        if "- label:" in line and "不含真实客户数据" in line
    ]
    test_case.assertEqual(len(privacy_labels), 1, "expected one no-client-data checkbox")
    label_index = privacy_labels[0]
    test_case.assertLess(label_index + 1, len(lines), "privacy checkbox has no required setting")
    label_indent = len(lines[label_index]) - len(lines[label_index].lstrip())
    required_line = lines[label_index + 1]
    required_indent = len(required_line) - len(required_line.lstrip())
    test_case.assertGreater(required_indent, label_indent, "required must belong to the privacy option")
    test_case.assertEqual(required_line.strip(), "required: true")


class PublicRepositoryContractTests(unittest.TestCase):
    def test_required_public_files_exist(self):
        for name in (
            "README.md",
            "LICENSE",
            "DISCLAIMER.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "SECURITY.md",
        ):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_readme_sections_are_in_approved_order(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        sections = (
            "# OpenTaxCheck｜开源税务健康检查",
            "## Current release scope",
            "## How it works",
            "## Quick start",
            "## Run the fictional example",
            "## Outputs",
            "## AI modes",
            "## Privacy and cloud authorization",
            "## Rule coverage and limitations",
            "## Contributing tax rules",
            "## Community and tax-dispute collaboration",
            "## Disclaimer",
            "## License",
        )
        positions = [text.find(section) for section in sections]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_readme_contains_contact_data_warning_and_current_scope(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("微信：PosterNote；添加时请备注 GitHub。", text)
        self.assertIn("请勿通过 GitHub Issue、Discussion 或微信发送未经脱敏的客户资料。", text)
        current_scope = text.split("## Current release scope", 1)[1].split("## How it works", 1)[0]
        self.assertIn("v0.1 基础版本", current_scope)
        self.assertIn("CSV 序时账", current_scope)
        self.assertIn("Excel、PDF、图片、OCR", current_scope)
        self.assertIn("云端大模型调用", current_scope)
        self.assertIn("当前版本尚不支持", current_scope)
        self.assertIn("公开路线不代表已经交付的能力", current_scope)

    def test_rule_form_requires_authority_test_case_and_data_confirmation(self):
        text = (ROOT / ".github/ISSUE_TEMPLATE/rule-proposal.yml").read_text(encoding="utf-8")
        self.assertIn("官方法规依据", text)
        self.assertIn("测试案例", text)
        self.assertIn("不含真实客户数据", text)
        assert_privacy_option_is_required(self, text)

    def test_bug_and_rule_proposal_forms_are_separate(self):
        bug = (ROOT / ".github/ISSUE_TEMPLATE/bug.yml").read_text(encoding="utf-8")
        rule = (ROOT / ".github/ISSUE_TEMPLATE/rule-proposal.yml").read_text(encoding="utf-8")
        self.assertIn("程序缺陷", bug)
        self.assertNotIn("官方法规依据", bug)
        self.assertIn("税务规则提案", rule)

    def test_bug_form_requires_data_confirmation(self):
        bug = (ROOT / ".github/ISSUE_TEMPLATE/bug.yml").read_text(encoding="utf-8")
        assert_privacy_option_is_required(self, bug)

    def test_security_routes_sensitive_reports_to_private_reporting(self):
        text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("private vulnerability reporting", text)
        self.assertIn("不要", text)
        self.assertIn("公开 Issue", text)
        self.assertIn("客户数据", text)


if __name__ == "__main__":
    unittest.main()
