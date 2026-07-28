import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROADMAP_MILESTONES = (
    "1. 阶段 1：仓库基础与确定性 CSV 核心 — 已完成",
    "2. 阶段 2A：Excel 安全盘点与人工选表 — 已完成",
    "3. 阶段 2B：Excel 业务行标准化、勾稽与规则 — 已完成（正式客户报告未实现）",
    "4. 阶段 3：PDF、图片 OCR 与版本化税务规则包 — 计划中",
    "5. 阶段 4：AI assist/deep 编排、证据验证与专业交付物 — 计划中",
    "6. 阶段 5：联网企业数据适配、隐私加固、完整虚构案例与公开 v1 — 计划中",
)
UNSUPPORTED_SCOPE_SENTENCE = "PDF、图片 OCR 和联网企业数据尚未实现。"


def current_release_scope(text):
    return text.split("## 当前版本范围", 1)[1].split("## 工作方式", 1)[0]


def assert_roadmap_milestones(test_case, text):
    lines = [line for line in text.splitlines() if line.strip()]
    test_case.assertEqual(lines[0], "# 路线图")
    test_case.assertEqual(tuple(lines[1:]), ROADMAP_MILESTONES)


def assert_changelog_is_unreleased(test_case, text):
    test_case.assertIn("https://keepachangelog.com/en/1.1.0/", text)
    test_case.assertIn("## [未发布]", text)
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
    test_case.assertIn("当前仓库处于未发布的 **v0.1 / Phase 2B**", scope)
    test_case.assertIn("`.xlsx`、`.xlsm`、`.xls`", scope)
    test_case.assertIn("CSV 和已确认映射的 Excel", scope)
    test_case.assertIn("confirm-mapping", scope)
    test_case.assertIn("present_unparsed", scope)
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
            "## 当前版本范围",
            "## 工作方式",
            "## 快速开始",
            "## 运行虚构示例",
            "## 输出",
            "## AI 模式",
            "## 隐私与云端授权",
            "## 规则覆盖与限制",
            "## 贡献税务规则",
            "## 社区与税争协作",
            "## 免责声明",
            "## 许可证",
        )
        positions = [text.find(section) for section in sections]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_readme_contains_contact_data_warning_and_current_scope(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("微信：PosterNote；添加时请备注 GitHub。", text)
        self.assertIn("请勿通过 GitHub Issue、Discussion 或微信发送未经脱敏的客户资料。", text)
        current_scope = current_release_scope(text)
        self.assertIn("v0.1 / Phase 2B", current_scope)
        self.assertIn("CSV 和已确认映射的 Excel", current_scope)
        self.assertIn("`.xlsx`、`.xlsm`、`.xls`", current_scope)
        self.assertIn("PDF、图片 OCR 和联网企业数据尚未实现", current_scope)
        self.assertIn("正式客户报告", current_scope)
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
        self.assertIn("ROUND_HALF_UP", text)

    def test_readme_documents_excel_inspection_and_confirmed_normalization(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn('pip install -e ".[excel]"', text)
        self.assertIn("opentaxcheck inspect", text)
        self.assertIn("opentaxcheck select-sheet", text)
        self.assertIn("`.xlsx`、`.xlsm`、`.xls`", text)
        self.assertIn("confirm-mapping", text)
        self.assertIn("normalized_trial_balance.jsonl", text)
        self.assertIn("隐藏工作表不得自动选择", text)
        self.assertIn("隐藏行必须明确选择 `include` 或 `exclude`", text)
        self.assertIn("hidden_row_exclusion_unavailable", text)
        self.assertIn("`exclude` 不会过滤后继续处理，而是阻断", text)
        self.assertIn("复核后改为 `include`，或停止处理", text)
        self.assertIn("资料覆盖矩阵", text)
        self.assertIn("CSV 和已确认映射的 Excel", text)
        self.assertIn("PDF、图片 OCR 和联网企业数据尚未实现", text)

    def test_readme_documents_mapping_confirmation_and_reconciliation(self):
        """Dropping a 2B gate or risk boundary from the public workflow is a regression."""
        text = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("confirm-mapping", text)
        self.assertIn("normalized_trial_balance.jsonl", text)
        self.assertIn("reconciliation.json", text)
        self.assertIn("勾稽通过不表示不存在税务风险", text)

    def test_phase_2b_public_docs_are_chinese_first_and_keep_review_boundary(self):
        roadmap = (ROOT / "docs/roadmap.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        example = (ROOT / "examples/fictional-company/README.md").read_text(
            encoding="utf-8"
        )

        self.assertTrue(roadmap.startswith("# 路线图\n"))
        self.assertIn("阶段 2A：Excel 安全盘点与人工选表 — 已完成", roadmap)
        self.assertIn(
            "阶段 2B：Excel 业务行标准化、勾稽与规则 — 已完成（正式客户报告未实现）",
            roadmap,
        )
        self.assertTrue(changelog.startswith("# 更新日志\n"))
        self.assertIn("### 新增", changelog)
        self.assertIn("运行时生成", example)
        self.assertIn("不提交 `.xlsx` 二进制", example)

    def test_fictional_example_script_generates_xlsx_at_runtime(self):
        text = (ROOT / "scripts/run_fictional_example.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("journal.xlsx", text)
        self.assertIn("Workbook", text)
        self.assertIn("inspect_case", text)
        self.assertNotIn("shutil.copy2", text)

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
