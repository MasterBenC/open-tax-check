"""Immutable, customer-facing view model for tax health reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .reporting import select_reportable_findings
from .tax_models import Limitation, TaxCheckSnapshot, TaxStatus
from .tax_registry import TAX_DEFINITIONS


AMOUNT_DISCLAIMER_ZH = "试算金额不代表应补税额、欠税金额或最终申报金额。"
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
