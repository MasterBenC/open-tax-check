from dataclasses import replace
from datetime import date

from dataclasses import asdict, is_dataclass

import pytest

from open_tax_check.report_view import (
    AMOUNT_DISCLAIMER_ZH,
    MATERIAL_STATUS_ZH,
    build_report_view,
    build_report_view_v3,
)
from open_tax_check.tax_models import (
    AuditEvent,
    CalculationRecord,
    DateRange,
    FactGap,
    Limitation,
    Question,
    TaxCheckRequest,
    TaxCheckSnapshot,
    TaxResult,
    TaxStatus,
)
from open_tax_check.tax_registry import TAX_DEFINITIONS
from open_tax_check.tax_cross_checks import CrossCheckStatus
from test_tax_cross_checks import difference_run


def _snapshot() -> TaxCheckSnapshot:
    return TaxCheckSnapshot(
        request=TaxCheckRequest(DateRange(date(2026, 1, 1), date(2026, 12, 31)), "CN"),
        results=(
            TaxResult(
                "vat", TaxStatus.CALCULATED,
                calculation=CalculationRecord(
                    "generic.base_times_rate.v1", "100.00", "0.00", "100.00",
                    "0.13", "13.00", ("source-1",),
                ),
            ),
            TaxResult(
                "enterprise_income_tax", TaxStatus.NEEDS_EVIDENCE, (), (),
                (FactGap(
                    "enterprise_income_tax.taxable_income", "missing_fact",
                    "decimal", ("tax_return",), 90, True,
                ),), (),
            ),
            TaxResult("stamp_tax", TaxStatus.NOT_APPLICABLE, (), (), (), ("source-1",)),
        ),
        questions=(Question(
            "q-cit", "enterprise_income_tax.taxable_income",
            ("enterprise_income_tax",), ("missing_fact",), "decimal",
            ("tax_return",), 90, True,
            "请提供年度企业所得税申报表。", "用于核对计税基础。", "年度申报表",
        ),),
        audit_events=(AuditEvent("check_started", "2026-08-03T10:00:00+08:00"),),
        revision=1,
    )


def _snapshot_with_every_status() -> TaxCheckSnapshot:
    snapshot = _snapshot()
    return replace(
        snapshot,
        results=(
            TaxResult("vat", TaxStatus.CALCULATED),
            TaxResult("enterprise_income_tax", TaxStatus.NEEDS_EVIDENCE),
            TaxResult("individual_income_tax", TaxStatus.NEEDS_LOCAL_POLICY),
            TaxResult("stamp_tax", TaxStatus.BLOCKED),
            TaxResult("house_property_tax", TaxStatus.NOT_APPLICABLE, evidence_refs=("property-record",)),
        ),
        questions=(),
    )


def _customer_text(value: object) -> str:
    if is_dataclass(value):
        return _customer_text(asdict(value))
    if isinstance(value, dict):
        return " ".join(_customer_text(item) for item in value.values())
    if isinstance(value, tuple | list):
        return " ".join(_customer_text(item) for item in value)
    return value if isinstance(value, str) else ""


def test_v3_separates_risk_severity_from_received_material_state():
    view = build_report_view_v3(
        _snapshot_with_every_status(),
        entity_name="虚构科技有限公司",
        reviewed_findings=({
            "finding_id": "f-entertainment",
            "title_zh": "业务招待费扣除核对",
            "severity": "high",
            "review_state": "approved",
        },),
        material_statuses=({
            "source_id": "cit-return",
            "display_name_zh": "企业所得税申报表",
            "status": "received_pending",
        },),
    )

    assert view.risk_overview.high_count == 1
    assert view.materials[0].status_zh == "已收到待处理"
    assert "未提供" not in view.materials[0].status_zh
    assert view.risk_overview.material_pending_count == 0


def test_v3_deduplicates_missing_material_linked_to_existing_question():
    view = build_report_view_v3(
        _snapshot(),
        entity_name="甲公司",
        material_statuses=({
            "source_id": "cit-return",
            "display_name_zh": "企业所得税申报表",
            "status": "not_provided",
            "question_id": "q-cit",
        },),
    )

    assert view.executive_summary.material_gap_count == 1
    assert view.risk_overview.material_pending_count == 1


def test_v3_metadata_coverage_and_ordering_are_deterministic():
    snapshot = _snapshot_with_every_status()
    findings = (
        {
            "finding_id": "notice-z", "title_zh": "一般提示", "severity": "notice",
            "tax_ids": ("vat",), "affected_period_zh": "2026年3月",
            "review_state": "approved",
        },
        {
            "finding_id": "medium-a", "title_zh": "中等关注", "severity": "medium",
            "tax_ids": ("enterprise_income_tax",), "affected_period_zh": "2026年2月",
            "review_state": "approved",
        },
        {
            "finding_id": "high-b", "title_zh": "重点核实", "severity": "high",
            "tax_ids": ("vat",), "affected_period_zh": "2026年1月",
            "review_state": "approved",
        },
    )

    view = build_report_view_v3(snapshot, entity_name="甲公司", reviewed_findings=findings)

    assert view.meta.schema_version == "tax-health-report/v3"
    assert view.meta.revision == snapshot.revision
    assert view.meta.period_start == snapshot.request.period.start
    assert view.meta.period_end == snapshot.request.period.end
    assert [item.severity for item in view.risks] == ["high", "medium", "notice"]
    assert [item.finding_id for item in view.actions[:3]] == ["high-b", "medium-a", "notice-z"]
    assert len(view.tax_coverage) == len(TAX_DEFINITIONS)
    assert {item.tax_id for item in view.tax_coverage} == {item.tax_id for item in TAX_DEFINITIONS}
    assert all(item.executed_checks_zh for item in view.tax_coverage)
    assert all(item.can_conclude_zh and item.cannot_conclude_zh for item in view.risks)
    assert view.executive_summary.amount_disclaimer_zh == AMOUNT_DISCLAIMER_ZH
    assert "无风险" not in _customer_text(view)
    assert view == build_report_view_v3(snapshot, entity_name="甲公司", reviewed_findings=findings)


def test_v3_allowlists_all_six_material_states_and_rejects_unknown_states():
    statuses = tuple(
        {
            "source_id": f"source-{source_id}",
            "display_name_zh": f"资料-{source_id}",
            "status": status,
        }
        for source_id, status in enumerate(MATERIAL_STATUS_ZH)
    )

    view = build_report_view_v3(_snapshot(), entity_name="甲公司", material_statuses=statuses)

    assert [item.status_zh for item in view.materials] == list(MATERIAL_STATUS_ZH.values())
    with pytest.raises(ValueError, match="unknown_material_status"):
        build_report_view_v3(
            _snapshot(), entity_name="甲公司",
            material_statuses=({"source_id": "unexpected", "display_name_zh": "未知文件", "status": "unknown"},),
        )


def test_v3_reconciled_material_is_not_pending_or_a_limitation():
    snapshot = replace(_snapshot(), questions=(), limitations=())
    baseline = build_report_view_v3(snapshot, entity_name="甲公司")
    view = build_report_view_v3(
        snapshot,
        entity_name="甲公司",
        material_statuses=({
            "source_id": "cit-return",
            "display_name_zh": "企业所得税申报表",
            "status": "imported_reconciled",
            "tax_ids": ("enterprise_income_tax",),
        },),
    )

    assert view.materials[0].status_zh == "已导入并核对"
    assert view.risk_overview.material_pending_count == 0
    assert view.report_status_zh == baseline.report_status_zh
    coverage = next(
        item for item in view.tax_coverage
        if item.tax_id == "enterprise_income_tax"
    )
    assert coverage.material_limitations_zh == ()


def test_v3_actions_preserve_material_and_scope_limitation_follow_up():
    snapshot = replace(
        _snapshot(),
        limitations=(Limitation("separate_scope", "境外交易未纳入本次检查。"),),
    )

    view = build_report_view_v3(snapshot, entity_name="甲公司")

    assert any(item.finding_id == "material:q-cit" for item in view.actions)
    assert any(item.finding_id == "limitation:separate_scope" for item in view.actions)


def test_report_view_uses_four_approved_chinese_states():
    view = build_report_view(_snapshot())

    assert {item.client_state for item in view.tax_items} <= {
        "重点核实", "待补资料", "账务勾稽提示", "本次范围内未见异常",
    }
    assert all(item.client_state != "无风险" for item in view.tax_items)
    assert {item.tax_id: item.client_state for item in view.tax_items} == {
        "vat": "重点核实",
        "enterprise_income_tax": "待补资料",
        "stamp_tax": "本次范围内未见异常",
    }


def test_view_excludes_unreviewed_candidates_and_labels_trial_amount():
    findings = (
        {"title_zh": "茶叶发票疑点", "status": "candidate", "review_status": "unreviewed"},
        {
            "title_zh": "收入勾稽差异", "status": "suspected",
            "review_status": "reviewed", "report_status": "approved",
            "source_kind": "deterministic_discrepancy",
            "observed_zh": "账簿收入与申报口径存在差异。",
            "why_it_matters_zh": "可能影响申报口径核对。",
            "cannot_conclude_zh": "尚不能据此认定少缴税款。",
            "required_materials_zh": ["收入明细", "申报表"],
            "next_action_zh": "逐月核对差异来源。",
        },
    )

    view = build_report_view(_snapshot(), reviewed_findings=findings)

    assert [item.title_zh for item in view.issues] == ["收入勾稽差异"]
    assert all(item.source_kind != "unreviewed_candidate" for item in view.issues)
    assert view.executive_summary.amount_disclaimer_zh == (
        "试算金额不代表应补税额、欠税金额或最终申报金额。"
    )
    issue = view.issues[0]
    assert issue.observed_zh and issue.why_it_matters_zh
    assert issue.cannot_conclude_zh and issue.required_materials_zh
    assert issue.next_action_zh


def test_missing_evidence_becomes_material_gap_not_client_issue():
    view = build_report_view(_snapshot())

    assert view.issues == ()
    assert len(view.material_gaps) == 1
    assert view.material_gaps[0].tax_ids == ("enterprise_income_tax",)
    assert "企业所得税申报表" in view.material_gaps[0].material_zh


def test_complete_report_still_has_useful_owner_actions():
    snapshot = _snapshot()
    complete = replace(
        snapshot,
        results=tuple(
            result for result in snapshot.results
            if result.tax_id != "enterprise_income_tax"
        ),
        questions=(),
    )

    view = build_report_view(complete)

    assert view.material_gaps == ()
    assert [item.priority for item in view.actions] == [1, 2, 3]
    assert any("留存" in item.action_zh for item in view.actions)
    assert any("试算" in item.action_zh for item in view.actions)
    assert any("重新运行" in item.action_zh for item in view.actions)


def test_report_with_scope_limitations_is_not_labeled_fully_complete():
    limited = replace(
        _snapshot(),
        questions=(),
        limitations=(Limitation("scope_limit", "部分特殊交易未纳入确定性计算。"),),
    )

    view = build_report_view(limited)

    assert view.report_status_zh == "本次检查受限完成"
    assert "已完成" not in view.report_status_zh
    assert any("范围限制" in item.action_zh for item in view.actions)


def test_blocked_tax_result_adds_machine_limitation_and_prevents_complete_label():
    blocked = replace(
        _snapshot(),
        results=(TaxResult("vat", TaxStatus.BLOCKED),),
        questions=(),
        limitations=(),
    )

    view = build_report_view(blocked)

    assert view.report_status_zh == "本次检查受限完成"
    assert any("阻断" in item for item in view.limitations_zh)
    assert any("范围限制" in item.action_zh for item in view.actions)


def test_complete_report_without_trial_amount_does_not_request_trial_comparison():
    complete = replace(
        _snapshot(),
        results=(TaxResult("stamp_tax", TaxStatus.NOT_APPLICABLE, evidence_refs=("source-1",)),),
        questions=(),
        limitations=(),
    )

    view = build_report_view(complete)

    assert view.report_status_zh == "本次可执行检查程序已完成"
    assert all("试算" not in item.action_zh for item in view.actions)


def test_material_gap_actions_also_address_independent_scope_limitations():
    limited = replace(
        _snapshot(),
        limitations=(Limitation("separate_scope", "境外交易未纳入本次检查。"),),
    )

    view = build_report_view(limited)

    assert any(item.action_zh.startswith("补充：") for item in view.actions)
    assert any("范围限制" in item.action_zh for item in view.actions)


def test_cross_check_difference_is_customer_risk_without_tax_evasion_claim():
    view = build_report_view_v3(
        _snapshot(), entity_name="示例公司", tax_cross_checks=difference_run()
    )

    risk = next(item for item in view.risks if item.finding_id == "FS-VAT-SALES-001")
    assert "账套" in risk.observed_zh and "申报" in risk.observed_zh
    assert "差异" in risk.observed_zh
    assert "漏税" not in risk.observed_zh
    assert "应补税" not in risk.can_conclude_zh


def test_cross_check_not_performed_is_material_gap_not_match():
    run = difference_run()
    missing = replace(
        run.results[1],
        status=CrossCheckStatus.NOT_PERFORMED,
        ledger_amount=None,
        comparison_amount=None,
        difference_amount=None,
        difference_ratio=None,
        source_refs=(),
        reason_codes=("missing_income_statement_or_vat_sales",),
        question=run.questions[0],
    )
    results = list(run.results)
    results[1] = missing
    run = replace(run, results=tuple(results))

    view = build_report_view_v3(_snapshot(), entity_name="示例公司", tax_cross_checks=run)

    assert any("资料不足" in item.impact_zh for item in view.materials)
    assert not any(
        "金额一致" in text
        for text in view.appendix_executed_checks
        if "FS-VAT-SALES-001" in text
    )


def test_multi_component_cross_check_uses_component_values_without_speculation():
    from test_tax_cross_checks import (
        classifications,
        complete_returns,
        context,
        ledger_rows,
        run_tax_cross_checks,
        statement,
        tax_return,
    )

    returns = list(complete_returns())
    returns[1] = tax_return(
        "cit",
        {
            "cit.revenue": "1100.00",
            "cit.total_profit": "200.00",
            "cit.business_entertainment.book_amount": "10.00",
            "cit.business_entertainment.tax_amount": "5.00",
        },
    )
    run = run_tax_cross_checks(
        ledger_rows(), classifications(), (statement(),), tuple(returns), context(), "a" * 64
    )

    view = build_report_view_v3(
        _snapshot(), entity_name="\u793a\u4f8b\u516c\u53f8", tax_cross_checks=run
    )
    risk = next(item for item in view.risks if item.finding_id == "FS-CIT-REV-PROFIT-001")

    assert "\u6536\u5165" in risk.observed_zh
    assert "1000.00" in risk.observed_zh and "1100.00" in risk.observed_zh
    assert "\u5229\u6da6\u603b\u989d" in risk.observed_zh
    assert "\u91d1\u989d\u4e00\u81f4" in risk.observed_zh
    assert "\u53ef\u80fd" not in risk.why_it_matters_zh


def test_closed_cross_check_uses_completed_next_action():
    from open_tax_check.tax_cross_checks import (
        AmountBridgeItem,
        apply_cross_check_explanation,
    )
    from test_tax_cross_checks import explanation
    from decimal import Decimal

    run = apply_cross_check_explanation(
        difference_run(),
        explanation(),
        bridge=(AmountBridgeItem(
            Decimal("100.00"),
            "\u671f\u95f4\u5dee\u5f02",
            ("source-1",),
        ),),
        verified_evidence_refs=("source-1",),
    )
    view = build_report_view_v3(
        _snapshot(), entity_name="\u793a\u4f8b\u516c\u53f8", tax_cross_checks=run
    )
    risk = next(item for item in view.risks if item.finding_id == "FS-VAT-SALES-001")

    assert "\u5df2\u5b8c\u6210" in risk.next_action_zh
    assert "\u91cd\u65b0\u6267\u884c" not in risk.next_action_zh
