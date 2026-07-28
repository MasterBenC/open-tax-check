"""Boundary tests for the progressive, customer-safe tax-health report."""

from datetime import date
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
from open_tax_check.tax_reporting import render_tax_health_report


PERIOD = DateRange(date(2026, 1, 1), date(2026, 1, 31))
SOURCE = RuleSource("国家税务总局", "https://www.chinatax.gov.cn/example", date(2026, 1, 1))


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
    request = TaxCheckRequest(PERIOD, "CN", input_hashes=("sha256:input-a", "sha256:input-b"))
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
    def test_report_exposes_progressive_states_and_safe_boundaries(self) -> None:
        report = render_tax_health_report(snapshot_with_every_status())

        self.assertTrue(report.startswith("# 税务健康检查报告"))
        self.assertIn("2026-01-01 至 2026-01-31", report)
        self.assertIn("修订版本: 3", report)
        self.assertIn("输入哈希数量: 2", report)
        for definition in TAX_DEFINITIONS:
            self.assertIn(definition.name_zh, report)
        for status in TaxStatus:
            self.assertIn(status.value, report)
        self.assertIn("缺少资料", report)
        self.assertIn("尚缺地方政策参数", report)
        self.assertIn("rule_evaluator_unavailable", report)
        self.assertIn("vat-cn-2026", report)
        self.assertIn(SOURCE.url, report)
        self.assertIn("## 待确认客户问题", report)
        self.assertNotIn("model hidden reasoning", report)
        self.assertNotIn("signature", report.lower())
        self.assertNotIn("seal", report.lower())
        self.assertNotIn("approver", report.lower())
        self.assertNotIn("professional-review", report.lower())
        self.assertNotIn("已全面合规", report)
        self.assertNotIn("不存在税务风险", report)

    def test_report_contains_the_fixed_self_use_disclaimer(self) -> None:
        report = render_tax_health_report(snapshot_with_every_status())

        for line in (
            "仅供客户内部自查",
            "不属于涉税鉴证、专业税务意见或纳税申报结论",
            "取决于客户提供的资料、确认事实和规则版本",
            "资料或政策不足的项目未形成确定结论；客户自行决定是否申报、调整或另行咨询专业机构",
        ):
            self.assertIn(line, report)


if __name__ == "__main__":
    unittest.main()
