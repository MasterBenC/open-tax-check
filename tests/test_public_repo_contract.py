import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROADMAP_MILESTONES = (
    "1. Repository foundation and deterministic CSV core - complete",
    "2. Excel/PDF/image intake and OCR adapters - planned",
    "3. Versioned 2024-2026 core tax-rule packs - planned",
    "4. AI assist/deep orchestration and evidence validation - planned",
    "5. Excel/DOCX/PDF/evidence-pack professional deliverables - planned",
    "6. Fengniao adapter, privacy hardening, fictional full case, and public v1 release - planned",
)
UNSUPPORTED_SCOPE_SENTENCE = "Excel、OCR、DOCX、PDF、扩展税务规则和云端适配器尚未支持。"


def current_release_scope(text):
    return text.split("## Current release scope", 1)[1].split("## How it works", 1)[0]


def assert_roadmap_milestones(test_case, text):
    lines = [line for line in text.splitlines() if line.strip()]
    test_case.assertEqual(lines[0], "# Roadmap")
    test_case.assertEqual(tuple(lines[1:]), ROADMAP_MILESTONES)


def assert_changelog_is_unreleased(test_case, text):
    test_case.assertIn("https://keepachangelog.com/en/1.1.0/", text)
    test_case.assertIn("## [Unreleased]", text)
    test_case.assertIn("0.1.0", text)
    test_case.assertIn("未发布", text)
    test_case.assertIn(
        "remains unpublished until creation of a GitHub tag is explicitly authorized.",
        text,
    )
    test_case.assertIsNone(
        re.search(r"(?m)^## \[\d+\.\d+\.\d+\](?:\s+-.*)?\s*$", text),
        "unreleased changelog must not contain a published version heading",
    )


def assert_readme_phase_scope(test_case, text):
    scope = current_release_scope(text)
    test_case.assertIn(
        "当前仓库处于 **v0.1 基础版本**，仅支持单一法律实体的 "
        "UTF-8/UTF-8-BOM **CSV 序时账**",
        scope,
    )
    test_case.assertIn("当前版本尚不支持", scope)
    test_case.assertIn("公开路线不代表已经交付的能力", scope)
    test_case.assertIn("[docs/roadmap.md](docs/roadmap.md)", scope)
    test_case.assertIn(UNSUPPORTED_SCOPE_SENTENCE, scope)


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
        current_scope = current_release_scope(text)
        self.assertIn("v0.1 基础版本", current_scope)
        self.assertIn("CSV 序时账", current_scope)
        self.assertIn("Excel、PDF、图片、OCR", current_scope)
        self.assertIn("云端大模型调用", current_scope)
        self.assertIn("当前版本尚不支持", current_scope)
        self.assertIn("公开路线不代表已经交付的能力", current_scope)

    def test_readme_links_scope_and_roadmap(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        assert_readme_phase_scope(self, text)
        self.assertTrue((ROOT / "docs/roadmap.md").is_file())

    def test_readme_documents_csv_and_numeric_integrity_gates(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("`.csv` 后缀不区分大小写", text)
        self.assertIn("不支持文件", text)
        self.assertIn("多余或缺失单元格", text)
        self.assertIn("合法三位千分位", text)
        self.assertIn("指数记法", text)
        self.assertIn("18 位整数", text)
        self.assertIn("2 位小数", text)
        self.assertIn("20 位整数", text)
        self.assertIn("绝对金额总额", text)

    def test_roadmap_preserves_exact_phase_statuses(self):
        path = ROOT / "docs/roadmap.md"
        self.assertTrue(path.is_file())
        assert_roadmap_milestones(self, path.read_text(encoding="utf-8"))

    def test_changelog_keeps_version_unreleased_until_tag_authorization(self):
        path = ROOT / "CHANGELOG.md"
        self.assertTrue(path.is_file())
        assert_changelog_is_unreleased(self, path.read_text(encoding="utf-8"))

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
