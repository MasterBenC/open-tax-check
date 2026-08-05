"""Immutable, customer-facing view model for tax health reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json

from .reporting import select_reportable_findings
from .tax_models import Limitation, TaxCheckSnapshot, TaxStatus
from .tax_registry import TAX_DEFINITIONS
from .tax_cross_checks import CrossCheckStatus, TaxCrossCheckRun


AMOUNT_DISCLAIMER_ZH = "试算金额不代表应补税额、欠税金额或最终申报金额。"
REPORT_SCHEMA_VERSION_V3 = "tax-health-report/v3"
MATERIAL_STATUS_ZH = {
    "not_provided": "截至报告生成时未收到",
    "received_pending": "已收到待处理",
    "extraction_partial": "提取不完整或失败",
    "contract_invalid": "合同校验失败",
    "imported_pending_confirmation": "已导入待确认",
    "imported_reconciled": "已导入并核对",
}
RISK_SEVERITY_ORDER = {"high": 0, "medium": 1, "notice": 2}
_TAX_NAMES = {item.tax_id: item.name_zh for item in TAX_DEFINITIONS}


@dataclass(frozen=True, slots=True)
class ExecutiveSummary:
    entity_name: str | None
    status_zh: str
    issue_count: int
    material_gap_count: int
    trial_amount_count: int
    amount_disclaimer_zh: str


@dataclass(frozen=True, slots=True)
class ClientIssue:
    title_zh: str
    client_state: str
    observed_zh: str
    why_it_matters_zh: str
    cannot_conclude_zh: str
    required_materials_zh: tuple[str, ...]
    next_action_zh: str
    source_kind: str


@dataclass(frozen=True, slots=True)
class MaterialGap:
    material_zh: str
    impact_zh: str
    tax_ids: tuple[str, ...]
    next_action_zh: str


@dataclass(frozen=True, slots=True)
class ActionItem:
    priority: int
    action_zh: str


@dataclass(frozen=True, slots=True)
class AppendixTaxItem:
    tax_id: str
    name_zh: str
    client_state: str
    trial_amount: str | None
    evidence_refs: tuple[str, ...]
    gap_count: int


@dataclass(frozen=True, slots=True)
class ReportView:
    report_status_zh: str
    executive_summary: ExecutiveSummary
    issues: tuple[ClientIssue, ...]
    material_gaps: tuple[MaterialGap, ...]
    actions: tuple[ActionItem, ...]
    tax_items: tuple[AppendixTaxItem, ...]
    limitations_zh: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReportMeta:
    schema_version: str
    report_id: str
    revision: int
    period_start: date
    period_end: date
    period_zh: str


@dataclass(frozen=True, slots=True)
class RiskOverview:
    high_count: int
    medium_count: int
    notice_count: int
    material_pending_count: int


@dataclass(frozen=True, slots=True)
class RiskItem:
    finding_id: str
    title_zh: str
    severity: str
    tax_ids: tuple[str, ...]
    affected_period_zh: str
    observed_zh: str
    why_it_matters_zh: str
    can_conclude_zh: str
    cannot_conclude_zh: str
    required_materials_zh: tuple[str, ...]
    next_action_zh: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaterialStatusItem:
    source_id: str
    display_name_zh: str
    status: str
    status_zh: str
    tax_ids: tuple[str, ...]
    impact_zh: str


@dataclass(frozen=True, slots=True)
class PriorityActionItem:
    priority: int
    finding_id: str
    action_zh: str


@dataclass(frozen=True, slots=True)
class TaxCoverageItem:
    tax_id: str
    name_zh: str
    client_state: str
    executed_checks_zh: tuple[str, ...]
    triggered_finding_ids: tuple[str, ...]
    material_limitations_zh: tuple[str, ...]
    trial_amount: str | None


@dataclass(frozen=True, slots=True)
class ReportViewV3:
    meta: ReportMeta
    report_status_zh: str
    executive_summary: ExecutiveSummary
    risk_overview: RiskOverview
    risks: tuple[RiskItem, ...]
    materials: tuple[MaterialStatusItem, ...]
    actions: tuple[PriorityActionItem, ...]
    tax_coverage: tuple[TaxCoverageItem, ...]
    appendix_executed_checks: tuple[str, ...]
    limitations_zh: tuple[str, ...]


def effective_limitations(snapshot: TaxCheckSnapshot) -> tuple[Limitation, ...]:
    """Combine caller limitations with machine-owned current-state limitations."""

    reserved_codes = {
        "pending_customer_evidence", "unresolved_tax_scope", "interrupted_tax_scope",
    }
    limitations = [
        item for item in snapshot.limitations if item.code not in reserved_codes
    ]
    if snapshot.questions:
        limitations.append(Limitation(
            "pending_customer_evidence",
            f"当前仍有 {len(snapshot.questions)} 项客户确认或补充资料事项，相关税种尚未形成确定结论。",
        ))
    unresolved = tuple(
        result for result in snapshot.results
        if result.status in {TaxStatus.NEEDS_EVIDENCE, TaxStatus.NEEDS_LOCAL_POLICY}
    )
    if unresolved:
        limitations.append(Limitation(
            "unresolved_tax_scope",
            f"当前有 {len(unresolved)} 个税种仍受资料或当地政策限制，本次未形成最终判断。",
        ))
    blocked = tuple(
        result for result in snapshot.results if result.status is TaxStatus.BLOCKED
    )
    if blocked:
        limitations.append(Limitation(
            "interrupted_tax_scope",
            f"当前有 {len(blocked)} 个税种的检查程序被阻断，本次未形成可执行结论。",
        ))
    return tuple(limitations)


def _client_state(status: TaxStatus) -> str:
    if status is TaxStatus.NOT_APPLICABLE:
        return "本次范围内未见异常"
    if status in {TaxStatus.NEEDS_EVIDENCE, TaxStatus.NEEDS_LOCAL_POLICY, TaxStatus.BLOCKED}:
        return "待补资料"
    return "重点核实"


def _text(finding: Mapping[str, object], field: str, default: str) -> str:
    value = finding.get(field)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _materials(finding: Mapping[str, object]) -> tuple[str, ...]:
    value = finding.get("required_materials_zh")
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _v3_text(finding: Mapping[str, object], field: str, default: str) -> str:
    return _text(finding, field, default)


def _v3_tax_ids(item: Mapping[str, object]) -> tuple[str, ...]:
    value = item.get("tax_ids", item.get("tax_id", ()))
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, tuple | list):
        values = tuple(value)
    else:
        return ()
    return tuple(sorted({tax_id.strip() for tax_id in values if isinstance(tax_id, str) and tax_id.strip()}))


def _v3_finding_id(item: Mapping[str, object]) -> str:
    explicit_id = item.get("finding_id", item.get("id"))
    if isinstance(explicit_id, str) and explicit_id.strip():
        return explicit_id.strip()
    canonical = json.dumps(dict(item), ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return f"finding-{sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def _v3_is_approved(item: Mapping[str, object]) -> bool:
    if item.get("review_state") == "approved":
        return True
    return bool(select_reportable_findings([dict(item)]))


def _v3_executed_check(result: object) -> str:
    # TaxResult is deliberately passed as object to keep this formatter private.
    status = result.status  # type: ignore[attr-defined]
    if status is TaxStatus.NOT_APPLICABLE:
        return "已执行适用性判断程序，本次检查范围内未触发异常。"
    if status is TaxStatus.CALCULATED:
        return "已执行确定性检查程序并形成试算记录。"
    if status is TaxStatus.NEEDS_EVIDENCE:
        return "已执行初步检查程序，因资料不足未形成确定结论。"
    if status is TaxStatus.NEEDS_LOCAL_POLICY:
        return "已执行初步检查程序，因当地政策口径待确认未形成确定结论。"
    return "检查程序被阻断，未形成可执行结论。"


def build_report_view_v3(
    snapshot: TaxCheckSnapshot,
    *,
    entity_name: str,
    reviewed_findings: Sequence[Mapping[str, object]] = (),
    material_statuses: Sequence[Mapping[str, object]] = (),
    tax_cross_checks: TaxCrossCheckRun | None = None,
) -> ReportViewV3:
    """Build the immutable, customer-safe tax-health-report/v3 view."""

    period = snapshot.request.period
    period_zh = f"{period.start.isoformat()}至{period.end.isoformat()}"
    risks: list[RiskItem] = []
    cross_check_materials: list[MaterialStatusItem] = []
    cross_check_appendix: list[str] = []
    if tax_cross_checks is not None:
        if not isinstance(tax_cross_checks, TaxCrossCheckRun):
            raise ValueError("invalid_tax_cross_checks")
        for result in tax_cross_checks.results:
            if result.status is CrossCheckStatus.DIFFERENCE_FOUND:
                assert result.ledger_amount is not None
                assert result.comparison_amount is not None
                assert result.difference_amount is not None
                risks.append(RiskItem(
                    finding_id=result.check_id,
                    title_zh=result.title_zh,
                    severity=("notice" if result.resolution_status == "closed" else "medium"),
                    tax_ids=result.tax_ids,
                    affected_period_zh=f"{result.period_start}至{result.period_end}",
                    observed_zh=(
                        "本次账套、财务报表与申报资料核对发现金额差异："
                        f"账套或报表金额 {result.ledger_amount:f} 元，申报或对比金额 "
                        f"{result.comparison_amount:f} 元，绝对差异 {result.difference_amount:f} 元。"
                    ),
                    why_it_matters_zh="该差异可能反映确认期间、含税口径、会计调整或申报口径不同，需要结合原始凭证核实。",
                    can_conclude_zh="可以确认当前已导入的两组金额存在上述差异。",
                    cannot_conclude_zh="不能仅凭该差异认定漏税、应补税、欠税或违法。",
                    required_materials_zh=("相关账套、财务报表、纳税申报表及差异支持凭证",),
                    next_action_zh="请按差异金额逐项提供桥接说明和可核验凭证，再重新执行该项核对。",
                    evidence_refs=tuple(dict.fromkeys(ref.source_id for ref in result.source_refs)),
                ))
            if result.status is CrossCheckStatus.NOT_PERFORMED:
                cross_check_materials.append(MaterialStatusItem(
                    source_id=f"cross-check:{result.check_id}",
                    display_name_zh=result.title_zh,
                    status="not_provided",
                    status_zh=MATERIAL_STATUS_ZH["not_provided"],
                    tax_ids=result.tax_ids,
                    impact_zh="资料不足，未执行该项核对，不能据此表示金额一致或不存在风险。",
                ))
                cross_check_appendix.append(
                    f"{result.check_id}｜{result.title_zh}｜资料不足，未执行该项核对"
                )
                continue
            status_zh = {
                CrossCheckStatus.MATCHED: "金额一致",
                CrossCheckStatus.WITHIN_TOLERANCE: "差异同时位于固定绝对值和相对值容差内",
                CrossCheckStatus.DIFFERENCE_FOUND: "发现金额差异",
            }[result.status]
            cross_check_appendix.append(
                f"{result.check_id}｜{result.title_zh}｜{status_zh}｜"
                f"绝对容差 {result.absolute_tolerance:f} 元｜相对容差 {result.relative_tolerance:f}"
            )
    for item in reviewed_findings:
        if not _v3_is_approved(item):
            continue
        severity = item.get("severity", "notice")
        if severity not in RISK_SEVERITY_ORDER:
            raise ValueError("unknown_risk_severity")
        tax_ids = _v3_tax_ids(item)
        risks.append(RiskItem(
            finding_id=_v3_finding_id(item),
            title_zh=_v3_text(item, "title_zh", "待核实事项"),
            severity=severity,
            tax_ids=tax_ids,
            affected_period_zh=_v3_text(item, "affected_period_zh", period_zh),
            observed_zh=_v3_text(item, "observed_zh", "已记录一项经复核的核对事项。"),
            why_it_matters_zh=_v3_text(item, "why_it_matters_zh", "该事项可能影响相关税务或账务口径。"),
            can_conclude_zh=_v3_text(item, "can_conclude_zh", "该事项需要按报告列示的范围和证据继续核实。"),
            cannot_conclude_zh=_v3_text(item, "cannot_conclude_zh", "现有资料不足以据此认定应补税、欠税或违规。"),
            required_materials_zh=_materials(item),
            next_action_zh=_v3_text(item, "next_action_zh", "补充资料后重新核对。"),
            evidence_refs=_materials({"required_materials_zh": item.get("evidence_refs", ())}),
        ))
    ordered_risks = tuple(sorted(
        risks,
        key=lambda risk: (
            RISK_SEVERITY_ORDER[risk.severity], risk.affected_period_zh,
            risk.tax_ids, risk.finding_id,
        ),
    ))

    materials: list[MaterialStatusItem] = list(cross_check_materials)
    missing_material_keys: set[tuple[str, str]] = {
        ("source", item.source_id) for item in cross_check_materials
    }
    for item in material_statuses:
        status = item.get("status")
        if status not in MATERIAL_STATUS_ZH:
            raise ValueError("unknown_material_status")
        source_id = _v3_text(item, "source_id", "未命名资料")
        materials.append(MaterialStatusItem(
            source_id=source_id,
            display_name_zh=_v3_text(item, "display_name_zh", source_id),
            status=status,
            status_zh=MATERIAL_STATUS_ZH[status],
            tax_ids=_v3_tax_ids(item),
            impact_zh=_v3_text(item, "impact_zh", "该资料状态会限制相关检查程序或结论。"),
        ))
        if status == "not_provided":
            linked_question = item.get("question_id")
            missing_material_keys.add(
                ("question", linked_question.strip())
                if isinstance(linked_question, str) and linked_question.strip()
                else ("source", source_id)
            )
    ordered_materials = tuple(sorted(materials, key=lambda item: (item.source_id, item.display_name_zh)))
    limiting_materials = tuple(
        item for item in ordered_materials if item.status != "imported_reconciled"
    )
    missing_material_count = len(
        {("question", question.question_id) for question in snapshot.questions}
        | missing_material_keys
    )

    action_sources = [
        (risk.finding_id, risk.next_action_zh)
        for risk in ordered_risks
    ]
    action_sources.extend(
        (f"material:{question.question_id}", f"补充：{question.question_zh or '相关证明资料'}")
        for question in sorted(
            snapshot.questions,
            key=lambda question: (question.priority, question.tax_ids, question.question_id),
        )
    )
    results_by_tax = {result.tax_id: result for result in snapshot.results}
    material_limitations = {
        tax_id: tuple(item.status_zh for item in limiting_materials if tax_id in item.tax_ids)
        for tax_id in _TAX_NAMES
    }
    tax_coverage = tuple(
        TaxCoverageItem(
            tax_id=definition.tax_id,
            name_zh=definition.name_zh,
            client_state=(
                _client_state(results_by_tax[definition.tax_id].status)
                if definition.tax_id in results_by_tax else "本次未执行检查"
            ),
            executed_checks_zh=(
                (_v3_executed_check(results_by_tax[definition.tax_id]),)
                if definition.tax_id in results_by_tax else ("本次未执行检查程序。",)
            ),
            triggered_finding_ids=tuple(
                risk.finding_id for risk in ordered_risks if definition.tax_id in risk.tax_ids
            ),
            material_limitations_zh=material_limitations[definition.tax_id],
            trial_amount=(
                results_by_tax[definition.tax_id].calculation.amount
                if definition.tax_id in results_by_tax
                and results_by_tax[definition.tax_id].calculation is not None else None
            ),
        )
        for definition in TAX_DEFINITIONS
    )
    limitations = effective_limitations(snapshot)
    action_sources.extend(
        (f"limitation:{limitation.code}", "处理报告列示的范围限制并取得补充证据后，使用同一规则包重新运行检查。")
        for limitation in sorted(
            limitations,
            key=lambda limitation: limitation.code,
        )
        if limitation.code not in {"pending_customer_evidence", "unresolved_tax_scope"}
    )
    actions = tuple(
        PriorityActionItem(index, finding_id, action_zh)
        for index, (finding_id, action_zh) in enumerate(action_sources, start=1)
    )
    report_status_zh = (
        "初版报告已生成" if snapshot.questions or limiting_materials
        else "本次检查受限完成" if limitations
        else "本次可执行检查程序已完成"
    )
    trial_amount_count = sum(item.trial_amount is not None for item in tax_coverage)
    return ReportViewV3(
        meta=ReportMeta(
            schema_version=REPORT_SCHEMA_VERSION_V3,
            report_id=f"THR-{period.start:%Y%m%d}-{period.end:%Y%m%d}-R{snapshot.revision:04d}",
            revision=snapshot.revision,
            period_start=period.start,
            period_end=period.end,
            period_zh=period_zh,
        ),
        report_status_zh=report_status_zh,
        executive_summary=ExecutiveSummary(
            entity_name=entity_name,
            status_zh=report_status_zh,
            issue_count=len(ordered_risks),
            material_gap_count=missing_material_count,
            trial_amount_count=trial_amount_count,
            amount_disclaimer_zh=AMOUNT_DISCLAIMER_ZH,
        ),
        risk_overview=RiskOverview(
            high_count=sum(risk.severity == "high" for risk in ordered_risks),
            medium_count=sum(risk.severity == "medium" for risk in ordered_risks),
            notice_count=sum(risk.severity == "notice" for risk in ordered_risks),
            material_pending_count=missing_material_count,
        ),
        risks=ordered_risks,
        materials=ordered_materials,
        actions=actions,
        tax_coverage=tax_coverage,
        appendix_executed_checks=tuple(
            f"{item.name_zh}：{check}"
            for item in tax_coverage for check in item.executed_checks_zh
        ) + tuple(cross_check_appendix),
        limitations_zh=tuple(item.message_zh for item in limitations),
    )


def build_report_view(
    snapshot: TaxCheckSnapshot,
    *,
    entity_name: str | None = None,
    reviewed_findings: Sequence[Mapping[str, object]] = (),
) -> ReportView:
    """Map one immutable snapshot to the bounded customer report contract."""

    approved = select_reportable_findings([dict(item) for item in reviewed_findings])
    issues = tuple(
        ClientIssue(
            title_zh=_text(item, "title_zh", "待核实事项"),
            client_state=(
                "账务勾稽提示"
                if item.get("source_kind") == "deterministic_discrepancy"
                else "重点核实"
            ),
            observed_zh=_text(item, "observed_zh", "已记录一项经复核的核对事项。"),
            why_it_matters_zh=_text(item, "why_it_matters_zh", "该事项可能影响相关税务或账务口径。"),
            cannot_conclude_zh=_text(item, "cannot_conclude_zh", "现有资料不足以据此认定应补税、欠税或违规。"),
            required_materials_zh=_materials(item),
            next_action_zh=_text(item, "next_action_zh", "补充资料后重新核对。"),
            source_kind=_text(item, "source_kind", "reviewed_finding"),
        )
        for item in approved
    )
    material_gaps = tuple(
        MaterialGap(
            material_zh=question.question_zh or "请补充相关证明资料。",
            impact_zh=question.help_zh or "资料不足会限制相关税种判断。",
            tax_ids=question.tax_ids,
            next_action_zh=question.evidence_help_zh or "按问题要求提供可核验资料。",
        )
        for question in snapshot.questions
    )
    tax_items = tuple(
        AppendixTaxItem(
            tax_id=result.tax_id,
            name_zh=_TAX_NAMES.get(result.tax_id, result.tax_id),
            client_state=_client_state(result.status),
            trial_amount=result.calculation.amount if result.calculation is not None else None,
            evidence_refs=result.evidence_refs,
            gap_count=len(result.gaps),
        )
        for result in snapshot.results
    )
    limitations = effective_limitations(snapshot)
    trial_amount_count = sum(item.trial_amount is not None for item in tax_items)
    gap_actions = tuple(
        ActionItem(index, f"补充：{gap.material_zh}")
        for index, gap in enumerate(material_gaps, start=1)
    )
    if gap_actions:
        actionable_limitations = tuple(
            item for item in limitations
            if item.code not in {"pending_customer_evidence", "unresolved_tax_scope"}
        )
        actions = gap_actions + (
            (ActionItem(
                len(gap_actions) + 1,
                "处理报告列示的独立范围限制并取得补充证据后，使用同一规则包重新运行检查。",
            ),)
            if actionable_limitations else ()
        )
    else:
        action_texts = ["留存本次报告、原始资料和交付清单，确保后续复查可以追溯。"]
        if trial_amount_count:
            action_texts.append(
                "将报告中的试算项目与当期申报表、完税凭证逐项核对，不把试算金额直接作为申报金额。"
            )
        if limitations:
            action_texts.append(
                "处理报告列示的范围限制并取得补充证据后，使用同一规则包重新运行检查。"
            )
        else:
            action_texts.append(
                "发生重大交易、政策口径变化或新增资料时，使用同一规则包重新运行检查。"
            )
        actions = tuple(
            ActionItem(index, text) for index, text in enumerate(action_texts, start=1)
        )
    if material_gaps:
        status_zh = "初版报告已生成"
    elif limitations:
        status_zh = "本次检查受限完成"
    else:
        status_zh = "本次可执行检查程序已完成"
    return ReportView(
        report_status_zh=status_zh,
        executive_summary=ExecutiveSummary(
            entity_name=entity_name,
            status_zh=status_zh,
            issue_count=len(issues),
            material_gap_count=len(material_gaps),
            trial_amount_count=trial_amount_count,
            amount_disclaimer_zh=AMOUNT_DISCLAIMER_ZH,
        ),
        issues=issues,
        material_gaps=material_gaps,
        actions=actions,
        tax_items=tax_items,
        limitations_zh=tuple(item.message_zh for item in limitations),
    )
