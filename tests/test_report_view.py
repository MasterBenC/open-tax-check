from dataclasses import replace
from datetime import date

from open_tax_check.report_view import build_report_view
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
