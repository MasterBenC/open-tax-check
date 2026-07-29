"""Boundary tests for the progressive, customer-safe tax-health report."""

from datetime import date
import json
from zipfile import ZipFile
import unittest

from open_tax_check.tax_models import (
    AuditEvent,
    DateRange,
    FactGap,
    Question,
    RuleSource,
    TaxCheckRequest,
    TaxCheckSnapshot,
    TaxResult,
    TaxStatus,
)
from open_tax_check.tax_registry import TAX_DEFINITIONS
from open_tax_check.tax_reporting import (
    render_tax_health_report,
    write_tax_health_report,
    write_tax_health_snapshot,
    write_tax_health_report_docx,
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
        sources = ()
        evidence_refs = ()
        if status is TaxStatus.NEEDS_EVIDENCE:
            gaps = (FactGap("entity.registration", "missing_fact", "choice", ("营业执照",), 90, True),)
        elif status is TaxStatus.NEEDS_LOCAL_POLICY:
            gaps = (FactGap("local.rate", "missing_local_policy", "number", ("地方政策",), 80, True),)
        elif status is TaxStatus.BLOCKED:
            diagnostics = ("rule_evaluator_unavailable",)
            pack_ids = ("vat-cn-2026",)
            sources = (SOURCE,)
        elif status is TaxStatus.NOT_APPLICABLE:
            evidence_refs = ("evidence-not-applicable",)
        results.append(TaxResult(
            definition.tax_id, status, pack_ids, diagnostics, gaps, evidence_refs, sources
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
            self.assertEqual(set(payload), {"schema_version", "revision", "request", "results", "questions", "audit_events"})
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


if __name__ == "__main__":
    unittest.main()
