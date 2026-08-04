"""Boundary tests for the progressive, customer-safe tax-health report."""

from datetime import date
from dataclasses import replace
import json
from zipfile import ZipFile
import unittest

from open_tax_check.tax_models import (
    AuditEvent,
    CalculationRecord,
    DateRange,
    FactGap,
    Limitation,
    Question,
    RuleSource,
    TaxCheckRequest,
    TaxCheckSnapshot,
    TaxResult,
    TaxStatus,
)
from open_tax_check.tax_registry import TAX_DEFINITIONS
from open_tax_check.report_view import build_report_view
from open_tax_check.tax_reporting import (
    effective_limitations,
    render_tax_health_report,
    render_report_view,
    tax_snapshot_payload,
    write_tax_health_report,
    write_tax_health_snapshot,
    write_tax_health_report_docx,
    write_tax_health_report_pdf,
)


PERIOD = DateRange(date(2026, 1, 1), date(2026, 1, 31))
SOURCE = RuleSource("国家税务总局", "https://www.chinatax.gov.cn/example", date(2026, 1, 1))
EXPECTED_TAX_HEADINGS = (
    ("vat", "增值税"),
    ("consumption_tax", "消费税"),
    ("enterprise_income_tax", "企业所得税"),
    ("individual_income_tax", "个人所得税"),
    ("resource_tax", "资源税"),
    ("urban_maintenance_construction_tax", "城市维护建设税"),
    ("house_property_tax", "房产税"),
    ("stamp_tax", "印花税"),
    ("urban_land_use_tax", "城镇土地使用税"),
    ("land_appreciation_tax", "土地增值税"),
    ("vehicle_purchase_tax", "车辆购置税"),
    ("vehicle_vessel_tax", "车船税"),
    ("vessel_tonnage_tax", "船舶吨税"),
    ("cultivated_land_occupation_tax", "耕地占用税"),
    ("deed_tax", "契税"),
    ("tobacco_tax", "烟叶税"),
    ("environmental_protection_tax", "环境保护税"),
    ("customs_duty", "关税"),
    ("education_surcharge", "教育费附加"),
    ("local_education_surcharge", "地方教育附加"),
)


def snapshot_with_every_status() -> TaxCheckSnapshot:
    statuses = tuple(TaxStatus)
    results = []
    for index, definition in enumerate(TAX_DEFINITIONS):
        status = statuses[index % len(statuses)]
        gaps = ()
        diagnostics = ()
        pack_ids = ()
        pack_versions = ()
        sources = ()
        evidence_refs = ()
        if status is TaxStatus.NEEDS_EVIDENCE:
            gaps = (FactGap("entity.registration", "missing_fact", "choice", ("营业执照",), 90, True),)
        elif status is TaxStatus.NEEDS_LOCAL_POLICY:
            gaps = (FactGap("local.rate", "missing_local_policy", "number", ("地方政策",), 80, True),)
        elif status is TaxStatus.BLOCKED:
            diagnostics = ("rule_evaluator_unavailable",)
            pack_ids = ("vat-cn-2026",)
            pack_versions = ("2026.1",)
            sources = (SOURCE,)
        elif status is TaxStatus.NOT_APPLICABLE:
            evidence_refs = ("evidence-not-applicable",)
        calculation = (
            CalculationRecord(
                "generic.base_minus_deductions_times_rate.v1",
                "1000.00", "100.00", "900.00", "0.13", "117.00", ("vat-return-2026",),
                "sale_contract", "\u4e70\u5356\u5408\u540c",
            )
            if status is TaxStatus.CALCULATED and definition.tax_id == "vat"
            else None
        )
        results.append(TaxResult(
            definition.tax_id, status, pack_ids, diagnostics, gaps, evidence_refs, sources,
            calculation,
            pack_versions,
        ))
    request = TaxCheckRequest(PERIOD, "CN", input_hashes=("a" * 64, "b" * 64))
    questions = (
        Question(
            "question-registration", "entity.registration", ("vat",), ("missing_fact",),
            "choice", ("营业执照",), 90, True,
        ),
    )
    return TaxCheckSnapshot(
        request=request,
        results=tuple(results),
        questions=questions,
        audit_events=(AuditEvent("check_started", "2026-07-28T10:00:00+08:00"),),
        revision=3,
    )


class TaxHealthReportingTests(unittest.TestCase):
    def test_markdown_places_owner_report_before_professional_appendix(self) -> None:
        report = render_tax_health_report(snapshot_with_every_status())
        headings = [line for line in report.splitlines() if line.startswith("## ")]

        self.assertEqual(headings[:5], [
            "## 管理层摘要",
            "## 老板先看这些事",
            "## 待补资料及影响",
            "## 建议行动顺序",
            "## 检查范围和限制",
        ])
        self.assertGreater(
            headings.index("## 专业附录"),
            headings.index("## 检查范围和限制"),
        )

    def test_owner_report_hides_internal_codes_and_uses_five_part_issues(self) -> None:
        snapshot = snapshot_with_every_status()
        view = build_report_view(snapshot, reviewed_findings=({
            "title_zh": "收入勾稽差异",
            "status": "suspected",
            "review_status": "reviewed",
            "report_status": "approved",
            "source_kind": "deterministic_discrepancy",
            "observed_zh": "账簿收入与申报口径存在差异。",
            "why_it_matters_zh": "可能影响相关申报口径。",
            "cannot_conclude_zh": "尚不能据此认定少缴税款。",
            "required_materials_zh": ["收入明细", "申报表"],
            "next_action_zh": "逐月核对差异来源。",
        },))

        report = render_report_view(view)
        owner = report.split("## 专业附录", 1)[0]

        for prohibited in (
            "calculated", "needs_evidence", "generic.base_times_rate",
            "enterprise_income_tax.taxable_income", "unreviewed_candidate",
        ):
            self.assertNotIn(prohibited, owner)
        for label in (
            "发现了什么", "为什么需要关注", "目前不能下什么结论",
            "需要补什么资料", "建议下一步",
        ):
            self.assertIn(label, owner)

    def test_twelve_pending_items_create_visible_limitations(self) -> None:
        snapshot = snapshot_with_every_status()
        snapshot = replace(snapshot, questions=snapshot.questions * 12)

        limitations = effective_limitations(snapshot)

        limitation = next(item for item in limitations if item.code == "pending_customer_evidence")
        self.assertIn("12", limitation.message_zh)

    def test_reserved_limitation_codes_are_machine_derived(self) -> None:
        snapshot = snapshot_with_every_status()
        snapshot = replace(
            snapshot,
            limitations=(Limitation(
                "pending_customer_evidence",
                "No additional evidence is required.",
            ),),
        )

        limitations = effective_limitations(snapshot)

        pending = [
            item for item in limitations if item.code == "pending_customer_evidence"
        ]
        self.assertEqual(len(pending), 1)
        self.assertIn("1", pending[0].message_zh)
        self.assertNotIn("No additional evidence", pending[0].message_zh)

    def test_snapshot_payload_includes_rule_pack_versions(self) -> None:
        payload = tax_snapshot_payload(snapshot_with_every_status())

        result = next(item for item in payload["results"] if item["rule_pack_ids"])
        self.assertEqual(result["rule_pack_versions"], ["2026.1"])

    def test_report_uses_the_public_chinese_names_for_all_twenty_items(self) -> None:
        report = render_tax_health_report(snapshot_with_every_status())

        for tax_id, name_zh in EXPECTED_TAX_HEADINGS:
            with self.subTest(tax_id=tax_id):
                self.assertIn(f"### {name_zh}", report)

    def test_report_exposes_progressive_states_and_safe_boundaries(self) -> None:
        report = render_tax_health_report(snapshot_with_every_status())

        self.assertTrue(report.startswith("# 税务健康检查报告"))
        self.assertIn("2026-01-01 至 2026-01-31", report)
        self.assertIn("修订版本: 3", report)
        self.assertIn("输入哈希数量: 2", report)
        for definition in TAX_DEFINITIONS:
            self.assertIn(definition.name_zh, report)
        for label in ("已完成计算", "经资料确认不适用", "待补充资料", "待确认当地政策", "暂时无法判断"):
            self.assertIn(label, report)
        self.assertIn("需要补充与该事项相关的资料", report)
        self.assertIn("需要补充适用的当地政策参数", report)
        self.assertIn(SOURCE.url, report)
        self.assertIn("117.00", report)
        self.assertIn("计税基础减扣除额后乘税率", report)
        self.assertIn("generic.base_minus_deductions_times_rate.v1", report)
        self.assertIn("\u9002\u7528\u7a0e\u76ee: \u4e70\u5356\u5408\u540c", report)
        self.assertIn("\u9002\u7528\u7a0e\u7387: 13%", report)
        self.assertNotIn("sale_contract", report)
        self.assertIn("## 待确认客户问题", report)
        self.assertNotIn("model hidden reasoning", report)
        self.assertNotIn("signature", report.lower())
        self.assertNotIn("seal", report.lower())
        self.assertNotIn("approver", report.lower())
        self.assertNotIn("professional-review", report.lower())
        self.assertNotIn("已全面合规", report)
        self.assertNotIn("不存在税务风险", report)
        for prohibited in ("calculated", "not_applicable", "needs_evidence", "needs_local_policy", "blocked", "fact_key", "question-", "rule_evaluator_unavailable", "vat-cn-2026"):
            self.assertNotIn(prohibited, report)

    def test_vat_formula_labels_deductions_as_input_tax_credits(self) -> None:
        snapshot = TaxCheckSnapshot(
            request=TaxCheckRequest(PERIOD, "CN"),
            results=(
                TaxResult(
                    "vat",
                    TaxStatus.CALCULATED,
                    calculation=CalculationRecord(
                        "generic.base_times_rate_minus_credits.v1",
                        "100000.00",
                        "8000.00",
                        "100000.00",
                        "0.13",
                        "5000.00",
                        ("vat-return",),
                    ),
                ),
            ),
            questions=(),
            audit_events=(),
            revision=1,
        )

        report = render_tax_health_report(snapshot)

        self.assertIn("- 允许抵扣进项税额: 8000.00", report)
        self.assertNotIn("- 扣除额: 8000.00", report)

    def test_progressive_formula_labels_quick_deduction_in_chinese(self) -> None:
        snapshot = TaxCheckSnapshot(
            request=TaxCheckRequest(PERIOD, "CN"),
            results=(
                TaxResult(
                    "individual_income_tax",
                    TaxStatus.CALCULATED,
                    calculation=CalculationRecord(
                        "generic.base_times_rate_minus_quick_deduction.v1",
                        "100000.00",
                        "2520.00",
                        "100000.00",
                        "0.10",
                        "7480.00",
                        ("annual-return",),
                    ),
                ),
            ),
            questions=(),
            audit_events=(),
            revision=1,
        )

        report = render_tax_health_report(snapshot)

        self.assertIn("计税基础乘税率后减速算扣除数", report)
        self.assertIn("- 速算扣除数: 2520.00", report)

    def test_quantity_formula_labels_unit_amount_without_percentage_conversion(self) -> None:
        snapshot = TaxCheckSnapshot(
            request=TaxCheckRequest(PERIOD, "CN"),
            results=(
                TaxResult(
                    "urban_land_use_tax",
                    TaxStatus.CALCULATED,
                    calculation=CalculationRecord(
                        "generic.quantity_times_unit_amount.v1",
                        "1250.50",
                        "0.00",
                        "1250.50",
                        "12.00",
                        "15006.00",
                        ("land-workpaper", "local-tariff"),
                    ),
                ),
            ),
            questions=(),
            audit_events=(),
            revision=1,
        )

        report = render_tax_health_report(snapshot)

        self.assertIn("计税数量乘适用单位税额", report)
        self.assertIn("- 适用单位税额: 12.00", report)
        self.assertNotIn("- 适用税率: 1200%", report)

    def test_applicable_parameter_formula_does_not_assume_percentage_or_unit_semantics(self) -> None:
        snapshot = TaxCheckSnapshot(
            request=TaxCheckRequest(PERIOD, "CN"),
            results=(
                TaxResult(
                    "resource_tax",
                    TaxStatus.CALCULATED,
                    calculation=CalculationRecord(
                        "generic.base_times_applicable_parameter.v1",
                        "1000.00",
                        "0.00",
                        "1000.00",
                        "12.00",
                        "12000.00",
                        ("resource-return", "local-tariff"),
                    ),
                ),
            ),
            questions=(),
            audit_events=(),
            revision=1,
        )

        report = render_tax_health_report(snapshot)

        self.assertIn("已确认计税依据乘适用计税参数", report)
        self.assertIn("- 适用计税参数: 12.00", report)
        self.assertNotIn("- 适用税率: 1200%", report)

    def test_confirmed_deduction_formula_uses_customer_safe_chinese_labels(self) -> None:
        snapshot = TaxCheckSnapshot(
            request=TaxCheckRequest(PERIOD, "CN"),
            results=(
                TaxResult(
                    "land_appreciation_tax",
                    TaxStatus.CALCULATED,
                    calculation=CalculationRecord(
                        "generic.base_times_rate_minus_confirmed_deduction.v1",
                        "500000.00",
                        "50000.00",
                        "500000.00",
                        "0.40",
                        "150000.00",
                        ("lat-return", "lat-workpaper"),
                        "ratio_over_50_up_to_100",
                        "增值率超过50%且不超过100%（税率40%）",
                    ),
                ),
            ),
            questions=(),
            audit_events=(),
            revision=1,
        )

        report = render_tax_health_report(snapshot)

        self.assertIn("增值额乘确认税率后减确认速算扣除额", report)
        self.assertIn("- 确认速算扣除额: 50000.00", report)
        self.assertIn("增值率超过50%且不超过100%", report)

    def test_report_contains_the_fixed_self_use_disclaimer(self) -> None:
        report = render_tax_health_report(snapshot_with_every_status())

        for line in (
            "仅供客户内部自查",
            "不属于涉税鉴证、专业税务意见或纳税申报结论",
            "取决于客户提供的资料、确认事实和规则版本",
            "资料或政策不足的项目未形成确定结论；客户自行决定是否申报、调整或另行咨询专业机构",
        ):
            self.assertIn(line, report)

    def test_report_exports_are_atomic_and_machine_payload_is_allowlisted(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown_path = root / "report.md"
            json_path = root / "snapshot.json"

            self.assertEqual(write_tax_health_report(snapshot_with_every_status(), markdown_path), markdown_path)
            self.assertEqual(write_tax_health_snapshot(snapshot_with_every_status(), json_path), json_path)
            self.assertTrue(markdown_path.read_text(encoding="utf-8").startswith("# "))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(payload),
                {"schema_version", "revision", "request", "results", "questions", "audit_events", "limitations"},
            )
            self.assertEqual(
                {item["code"] for item in payload["limitations"]},
                {
                    "pending_customer_evidence",
                    "unresolved_tax_scope",
                    "interrupted_tax_scope",
                },
            )
            calculation = next(
                item["calculation"] for item in payload["results"] if item["calculation"] is not None
            )
            self.assertEqual(calculation["rate_selector"], "sale_contract")
            self.assertEqual(calculation["rate_selector_label_zh"], "\u4e70\u5356\u5408\u540c")
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_report_docx_export_contains_explicit_styles_and_report_title(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.docx"
            self.assertEqual(write_tax_health_report_docx(snapshot_with_every_status(), path), path)
            with ZipFile(path) as archive:
                document = archive.read("word/document.xml").decode("utf-8")
                styles = archive.read("word/styles.xml").decode("utf-8")
                self.assertIn("税务健康检查报告", document)
                self.assertIn('w:styleId="Heading1"', styles)
                self.assertIn('w:sz w:val="32"', styles)
                for style_id in ("Heading2", "Heading3"):
                    style = styles.split(
                        f'<w:style w:type="paragraph" w:styleId="{style_id}">',
                        1,
                    )[1].split("</w:style>", 1)[0]
                    self.assertIn("<w:keepNext/>", style)

    def test_pdf_export_reports_missing_renderer_explicitly(self) -> None:
        from open_tax_check import tax_reporting
        from unittest.mock import patch

        with patch.object(tax_reporting.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "pdf_renderer_unavailable"):
                write_tax_health_report_pdf(snapshot_with_every_status(), "report.pdf")

    def test_pdf_export_uses_ascii_temporary_names_for_windows_renderers(self) -> None:
        import subprocess
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from open_tax_check import tax_reporting

        def render(command, **kwargs):
            source = Path(command[-1])
            if source.name.isascii():
                source.with_suffix(".pdf").write_bytes(b"%PDF-1.7\n")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "税务健康检查报告.pdf"
            with (
                patch.object(tax_reporting.shutil, "which", return_value="soffice"),
                patch.object(tax_reporting.subprocess, "run", side_effect=render),
            ):
                self.assertEqual(
                    write_tax_health_report_pdf(snapshot_with_every_status(), path),
                    path,
                )
            self.assertEqual(path.read_bytes(), b"%PDF-1.7\n")

    def test_pdf_export_reports_renderer_timeout_explicitly(self) -> None:
        import subprocess
        from unittest.mock import patch

        from open_tax_check import tax_reporting

        with (
            patch.object(tax_reporting.shutil, "which", return_value="soffice"),
            patch.object(
                tax_reporting.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("soffice", 120),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "pdf_render_timeout"):
                write_tax_health_report_pdf(
                    snapshot_with_every_status(),
                    "税务健康检查报告.pdf",
                )


if __name__ == "__main__":
    unittest.main()
