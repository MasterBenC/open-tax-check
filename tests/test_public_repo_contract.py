import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROADMAP_MILESTONES = (
    "1. 阶段 1：仓库基础与确定性 CSV 核心 — 已完成",
    "2. 阶段 2A：Excel 安全盘点与人工选表 — 已完成",
    "3. 阶段 2B：Excel 业务行标准化、勾稽与规则 — 已完成",
    "4. 阶段 3：PDF、图片 OCR 与版本化税务规则包 — 本地 OCR、20 项全国筛查及每项至少一条受控正式计算路径已完成，优惠、复合计税、预征清算及其他特殊路径仍在进行",
    "5. 阶段 4：AI assist/deep 编排、证据验证与专业交付物 — 财务报表/纳税申报表合同、常见模板解析、跨文档勾稽、可恢复案件状态、宿主 Agent 中文补件、《税务健康检查报告》基础交付、DOCX/PDF 页面级版式验收、可验证证据包、Excel 工作底稿和 `tax-delivery/v1` 统一交付快照已完成",
    "6. 阶段 5：联网企业数据适配、隐私加固与完整虚构案例 — 安全压缩包、provider 合同离线回放和完整虚构案例已完成；七宿主仅完成 host-neutral 合同回放及宿主专属目标布局/发现标记校验，真实宿主运行时验证仍 pending",
    "7. 阶段 6：公开 v1 发布 — 隔离 wheel 验收门禁及 CI 3.11–3.13 矩阵合同已加入；真实跨平台 CI 证据、GitHub tag/Release 仍待验证和授权",
)
LOCAL_OCR_SCOPE_SENTENCE = "PDF、图片已提供可选的本地离线 OCR；联网企业数据和复杂版式理解尚未实现。"


def current_release_scope(text):
    return text.split("## 当前版本范围", 1)[1].split("## 工作方式", 1)[0]


def experimental_report_scope(text):
    _, separator, remainder = text.partition("### 实验性的客户内部自查 API")
    if not separator:
        return ""
    return remainder.split(
        "## 隐私与云端授权", 1
    )[0]


def experimental_disclaimer_scope(text):
    _, separator, remainder = text.partition(
        "## 实验性《税务健康检查报告》的内部自用边界"
    )
    return remainder if separator else ""


def assert_roadmap_milestones(test_case, text):
    lines = [line for line in text.splitlines() if line.strip()]
    test_case.assertEqual(lines[0], "# 路线图")
    test_case.assertEqual(tuple(lines[1:]), ROADMAP_MILESTONES)


def assert_changelog_is_unreleased(test_case, text):
    test_case.assertIn("https://keepachangelog.com/en/1.1.0/", text)
    test_case.assertIn("## [未发布]", text)
    test_case.assertIn("1.0.0", text)
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
    test_case.assertIn("当前仓库处于未发布的 **self-service v1**", scope)
    test_case.assertIn("`.xlsx`、`.xlsm`、`.xls`", scope)
    test_case.assertIn("CSV 和已确认映射的 Excel", scope)
    test_case.assertIn("confirm-mapping", scope)
    test_case.assertIn("present_unparsed", scope)
    test_case.assertIn("安全压缩包", scope)
    test_case.assertIn("离线 provider 回放", scope)
    test_case.assertIn("完整虚构案例", scope)
    test_case.assertIn("真实宿主运行时验证仍 pending", scope)
    test_case.assertIn("公开路线不代表已经交付的能力", scope)
    test_case.assertIn("[docs/roadmap.md](docs/roadmap.md)", scope)
    test_case.assertIn(LOCAL_OCR_SCOPE_SENTENCE, scope)


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
        self.assertIn("self-service v1", current_scope)
        self.assertIn("CSV 和已确认映射的 Excel", current_scope)
        self.assertIn("`.xlsx`、`.xlsm`、`.xls`", current_scope)
        self.assertIn(LOCAL_OCR_SCOPE_SENTENCE, current_scope)
        self.assertIn("正式客户报告", current_scope)
        self.assertIn("公开路线不代表已经交付的能力", current_scope)
        self.assertIn("replay_external_providers.py", text)
        self.assertIn("replay_host_integrations.py", text)

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
        self.assertIn(LOCAL_OCR_SCOPE_SENTENCE, text)

    def test_readme_documents_mapping_confirmation_and_reconciliation(self):
        """Dropping a 2B gate or risk boundary from the public workflow is a regression."""
        text = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("confirm-mapping", text)
        self.assertIn("normalized_trial_balance.jsonl", text)
        self.assertIn("reconciliation.json", text)
        self.assertIn("勾稽通过不表示不存在税务风险", text)

    def test_readme_documents_experimental_tax_health_report_api(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        report_scope = experimental_report_scope(text)

        self.assertIn("《税务健康检查报告》", report_scope)
        self.assertIn(
            "from open_tax_check.tax_health import (\n"
            "    continue_tax_health_check,\n"
            "    run_tax_health_check,\n"
            ")",
            report_scope,
        )
        self.assertIn(
            "from open_tax_check.tax_reporting import render_tax_health_report",
            report_scope,
        )
        self.assertIn(
            "全国 20 个税费项目的 2023–2026 年适用性筛查规则包已经随版本内置",
            report_scope,
        )
        self.assertIn("宿主 Agent", report_scope)
        self.assertIn("结构化确认", report_scope)
        self.assertIn("Python 核心不依赖任何模型", report_scope)
        self.assertIn("未解决项目", report_scope)
        self.assertIn("渐进式报告", report_scope)
        self.assertIn("适用性筛查规则包已经随版本内置", report_scope)

    def test_readme_documents_private_by_default_evidence_bundles(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("--evidence-manifest", text)
        self.assertIn("--evidence-root", text)
        self.assertIn("--include-evidence-files", text)
        self.assertIn("默认不复制客户原始证据文件", text)
        self.assertIn("SHA-256 只验证文件身份和完整性", text)

    def test_readme_documents_revisioned_case_delivery(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for value in (
            "opentaxcheck case-delivery",
            "tax-delivery/v1",
            "work/deliveries/revision-0001",
            "workpaper.xlsx",
            "pdf_status",
            "partial",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_host_contract_names_all_supported_ecosystems_and_common_bundle_fields(self):
        text = (
            ROOT / "skills/open-tax-check/references/host-integrations.md"
        ).read_text(encoding="utf-8")

        for host in (
            "Codex",
            "Hermes",
            "WorkBuddy",
            "扣子",
            "Qoder",
            "Claude Code",
            "OpenClaw",
        ):
            with self.subTest(host=host):
                self.assertIn(host, text)
        for field in (
            "evidence_bundle_status",
            "evidence_bundle_path",
            "evidence_bundle_sha256",
            "includes_evidence_files",
        ):
            with self.subTest(field=field):
                self.assertIn(field, text)
        self.assertIn("未经客户明确授权", text)

    def test_host_contract_documents_case_delivery_and_customer_statement_boundary(self):
        text = (
            ROOT / "skills/open-tax-check/references/host-integrations.md"
        ).read_text(encoding="utf-8")
        for value in (
            "case-delivery",
            "tax-delivery/v1",
            "delivery_options",
            "workpaper.xlsx",
            "customer-confirmation:<question_id>",
            "宿主代客户声明",
            "不等于文件证据",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_experimental_report_has_self_use_boundary_without_approval_gate(self):
        readme = experimental_report_scope(
            (ROOT / "README.md").read_text(encoding="utf-8")
        )
        disclaimer = experimental_disclaimer_scope(
            (ROOT / "DISCLAIMER.md").read_text(encoding="utf-8")
        )
        boundary = (
            "该报告仅供客户内部自查，不属于涉税鉴证、"
            "专业税务意见或纳税申报结论。"
        )

        for document in (readme, disclaimer):
            with self.subTest(document=document[:20]):
                self.assertIn(boundary, document)
                self.assertIn(
                    "不以税务专业人员复核、签字或盖章作为生成条件",
                    document,
                )
                self.assertNotIn("必须由税务专业人员批准", document)

    def test_phase_2b_public_docs_are_chinese_first_and_keep_review_boundary(self):
        roadmap = (ROOT / "docs/roadmap.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        example = (ROOT / "examples/fictional-company/README.md").read_text(
            encoding="utf-8"
        )

        self.assertTrue(roadmap.startswith("# 路线图\n"))
        self.assertIn("阶段 2A：Excel 安全盘点与人工选表 — 已完成", roadmap)
        self.assertIn("阶段 2B：Excel 业务行标准化、勾稽与规则 — 已完成", roadmap)
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

    def test_document_extraction_contract_is_public_and_truthful(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        contract = (ROOT / "docs/document-extraction-contract.md").read_text(encoding="utf-8")

        self.assertIn("docs/document-extraction-contract.md", readme)
        for required in (
            "document-extraction/v1",
            "not_performed",
            "partial",
            "failed",
            "逐文件或逐页",
            "host",
            "off",
            "document-extraction/v1.1",
        ):
            self.assertIn(required, contract)

    def test_structured_document_contract_is_public_and_truthful(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        contract = (ROOT / "docs/document-extraction-contract.md").read_text(encoding="utf-8")
        for text in (readme, contract):
            for required in (
                "financial-statement/v1",
                "tax-return/v1",
                "validate-structured-document",
                "import-structured-document",
                "proposed",
                "confirmed",
                "SHA-256",
                "source_id",
            ):
                self.assertIn(required, text)
            self.assertIn("不确定的宿主 AI 输出不得进入税务计算", text)
        for fixture in (
            "tests/fixtures/structured_documents/financial-statement.json",
            "tests/fixtures/structured_documents/tax-return.json",
        ):
            payload = (ROOT / fixture).read_text(encoding="utf-8")
            self.assertIn("example.invalid", payload)
            self.assertNotIn("@", payload)

    def test_public_docs_describe_local_ocr_boundary(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        contract = (ROOT / "docs/document-extraction-contract.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs/roadmap.md").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        for text in (readme, contract):
            self.assertIn('pip install -e ".[ocr]"', text)
            self.assertIn("prepare-ocr", text)
            self.assertIn("extract-document", text)
            self.assertIn("local-paddleocr", text)
        self.assertIn("OPEN_TAX_CHECK_RUN_OCR_SMOKE", readme)
        self.assertIn("official mobile models", contract)
        self.assertIn('PADDLE_PDX_MODEL_SOURCE: "BOS"', workflow)
        self.assertIn("test_real_ocr_runtime_smoke", workflow)


if __name__ == "__main__":
    unittest.main()
