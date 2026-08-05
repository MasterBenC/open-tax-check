"""Progressive, customer-safe renderers for tax-health-check snapshots."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import tempfile

from .schemas import to_dict
from .pdf_report import PdfExportUnavailable, write_report_pdf
from .report_view import (
    ReportView,
    build_report_view,
    build_report_view_v3,
    effective_limitations,
)
from .tax_models import Question, RuleSource, TaxCheckSnapshot, TaxResult, TaxStatus
from .tax_registry import TAX_DEFINITIONS


_STATUS_LABELS = {
    TaxStatus.CALCULATED: "已完成计算",
    TaxStatus.NOT_APPLICABLE: "经资料确认不适用",
    TaxStatus.NEEDS_EVIDENCE: "待补充资料",
    TaxStatus.NEEDS_LOCAL_POLICY: "待确认当地政策",
    TaxStatus.BLOCKED: "暂时无法判断",
}

_FIXED_DISCLAIMER = (
    "仅供客户内部自查",
    "不属于涉税鉴证、专业税务意见或纳税申报结论",
    "取决于客户提供的资料、确认事实和规则版本",
    "资料或政策不足的项目未形成确定结论；客户自行决定是否申报、调整或另行咨询专业机构",
)

_FORMULA_LABELS_ZH = {
    "generic.base_minus_deductions_times_rate.v1": "计税基础减扣除额后乘税率",
    "generic.base_times_rate.v1": "计税基础乘税率",
    "generic.base_times_rate_minus_credits.v1": "计税基础乘税率后减允许抵扣税额",
    "generic.base_times_rate_minus_quick_deduction.v1": "计税基础乘税率后减速算扣除数",
    "generic.quantity_times_unit_amount.v1": "计税数量乘适用单位税额",
    "generic.base_times_applicable_parameter.v1": "已确认计税依据乘适用计税参数",
    "generic.base_times_rate_minus_confirmed_deduction.v1": "增值额乘确认税率后减确认速算扣除额",
}


def tax_snapshot_payload(snapshot: TaxCheckSnapshot) -> dict[str, object]:
    """Return an allowlisted, JSON-friendly snapshot without customer facts."""
    result_payloads = []
    for result in snapshot.results:
        payload = to_dict(result)
        payload["rule_pack_versions"] = list(result.rule_pack_versions)
        payload["status_label_zh"] = _STATUS_LABELS[result.status]
        result_payloads.append(payload)
    question_payloads = []
    for question in snapshot.questions:
        payload = to_dict(question)
        payload.update({
            "question_zh": question.question_zh,
            "help_zh": question.help_zh,
            "evidence_help_zh": question.evidence_help_zh,
            "choices": to_dict(question.choices),
        })
        question_payloads.append(payload)
    return {
        "schema_version": snapshot.schema_version,
        "revision": snapshot.revision,
        "request": {
            "period": to_dict(snapshot.request.period),
            "jurisdiction": snapshot.request.jurisdiction,
            "input_hashes": list(snapshot.request.input_hashes),
        },
        "results": result_payloads,
        "questions": question_payloads,
        "audit_events": [
            {
                "event_type": event.event_type,
                "tax_ids": list(event.tax_ids),
                "fact_keys": list(event.fact_keys),
                "confirmation_digest": event.confirmation_digest,
            }
            for event in snapshot.audit_events
        ],
        "limitations": [to_dict(item) for item in effective_limitations(snapshot)],
    }


def _sources(results: Iterable[TaxResult]) -> tuple[RuleSource, ...]:
    sources: list[RuleSource] = []
    for result in results:
        for source in result.rule_sources:
            if source not in sources:
                sources.append(source)
    return tuple(sources)


def _format_rate_zh(rate: str) -> str:
    try:
        percentage = Decimal(rate) * Decimal("100")
    except InvalidOperation:
        return rate
    rendered = format(percentage, "f").rstrip("0").rstrip(".")
    return f"{rendered or '0'}%"


def _result_lines(result: TaxResult) -> list[str]:
    lines = [
        f"- 检查结果: {_STATUS_LABELS[result.status]}",
    ]
    if result.calculation is not None:
        calculation = result.calculation
        formula_label = _FORMULA_LABELS_ZH.get(calculation.formula_id, "参数化计算公式")
        deductions_label = {
            "generic.base_times_rate_minus_credits.v1": "允许抵扣进项税额",
            "generic.base_times_rate_minus_quick_deduction.v1": "速算扣除数",
            "generic.base_times_rate_minus_confirmed_deduction.v1": "确认速算扣除额",
        }.get(calculation.formula_id, "扣除额")
        if calculation.rate_selector_label_zh is not None:
            lines.append(f"- 适用税目: {calculation.rate_selector_label_zh}")
        lines.extend(
            (
                f"- 计算公式: {formula_label}（{calculation.formula_id}）",
                f"- \u8ba1\u7a0e\u57fa\u7840: {calculation.tax_base}",
                f"- {deductions_label}: {calculation.deductions}",
                f"- \u51c0\u8ba1\u7a0e\u57fa\u7840: {calculation.net_base}",
                (
                    f"- 适用单位税额: {calculation.rate}"
                    if calculation.formula_id
                    == "generic.quantity_times_unit_amount.v1"
                    else f"- 适用计税参数: {calculation.rate}"
                    if calculation.formula_id
                    == "generic.base_times_applicable_parameter.v1"
                    else f"- \u9002\u7528\u7a0e\u7387: {_format_rate_zh(calculation.rate)}"
                ),
                f"- \u8ba1\u7b97\u91d1\u989d: {calculation.amount}",
                f"- \u8ba1\u7b97\u8bc1\u636e: {', '.join(calculation.evidence_refs)}",
            )
        )
    for gap in result.gaps:
        if gap.reason_code == "missing_local_policy":
            lines.append("- 需要补充适用的当地政策参数")
        else:
            lines.append("- 需要补充与该事项相关的资料")
    for source in result.rule_sources:
        lines.append(f"- 适用规则来源: [{source.title}]({source.url})（发布于 {source.published_on.isoformat()}）")
    return lines


def _question_lines(question: Question) -> list[str]:
    tax_names = {
        definition.tax_id: definition.name_zh for definition in TAX_DEFINITIONS
    }
    lines = [
        f"### {question.question_zh or '请补充相关资料'}",
        f"- 涉及税种: {', '.join(tax_names.get(tax_id, '相关税种') for tax_id in question.tax_ids)}",
    ]
    if question.help_zh:
        lines.append(f"- 说明: {question.help_zh}")
    if question.evidence_help_zh:
        lines.append(f"- 资料提示: {question.evidence_help_zh}")
    return lines


def render_report_view(view: ReportView) -> str:
    """Render the bounded owner report followed by its compact appendix."""

    summary = view.executive_summary
    lines = [
        "# 税务健康检查报告",
        "",
        view.report_status_zh,
        "",
        "## 管理层摘要",
        f"- 当前状态：{summary.status_zh}",
        f"- 经复核重点事项：{summary.issue_count} 项",
        f"- 待补资料事项：{summary.material_gap_count} 项",
        f"- 待核对试算：{summary.trial_amount_count} 项",
        f"- {summary.amount_disclaimer_zh}",
        "",
        "## 老板先看这些事",
    ]
    if summary.entity_name:
        lines.insert(5, f"- 检查企业：{summary.entity_name}")
    if view.issues:
        for issue in view.issues:
            lines.extend((
                f"### {issue.title_zh}",
                f"- 状态：{issue.client_state}",
                f"- 发现了什么：{issue.observed_zh}",
                f"- 为什么需要关注：{issue.why_it_matters_zh}",
                f"- 目前不能下什么结论：{issue.cannot_conclude_zh}",
                "- 需要补什么资料：" + (
                    "、".join(issue.required_materials_zh)
                    if issue.required_materials_zh else "按核对事项补充可验证资料"
                ),
                f"- 建议下一步：{issue.next_action_zh}",
                "",
            ))
    else:
        lines.append("- 当前没有经复核批准后进入客户报告的重点事项。")

    lines.extend(("", "## 待补资料及影响"))
    if view.material_gaps:
        for gap in view.material_gaps:
            lines.extend((
                f"### {gap.material_zh}",
                f"- 影响：{gap.impact_zh}",
                f"- 补充方式：{gap.next_action_zh}",
            ))
    else:
        lines.append("- 当前没有待补资料事项。")

    lines.extend(("", "## 建议行动顺序"))
    if view.actions:
        lines.extend(f"{item.priority}. {item.action_zh}" for item in view.actions)
    else:
        lines.append("1. 保存本次报告及交付清单，后续资料变化时重新检查。")

    lines.extend(("", "## 检查范围和限制"))
    if view.limitations_zh:
        lines.extend(f"- {item}" for item in view.limitations_zh)
    else:
        lines.append("- 本报告仅覆盖当前快照中已执行的检查程序。")
    lines.extend(f"- {item}" for item in _FIXED_DISCLAIMER)
    lines.extend(("", '<div style="page-break-before: always;"></div>', "", "## 专业附录"))
    for item in view.tax_items:
        lines.extend((
            "",
            f"### {item.name_zh}",
            f"- 客户状态：{item.client_state}",
            f"- 资料缺口数量：{item.gap_count}",
        ))
        if item.trial_amount is not None:
            lines.extend((
                f"- 试算金额：{item.trial_amount}",
                f"- {summary.amount_disclaimer_zh}",
            ))
    return "\n".join(lines) + "\n"


def _render_technical_report(snapshot: TaxCheckSnapshot) -> str:
    """Render one progressive report from active snapshot questions only."""
    results_by_tax_id = {result.tax_id: result for result in snapshot.results}
    counts = {status: sum(result.status is status for result in snapshot.results) for status in TaxStatus}
    request = snapshot.request
    lines = [
        "# 税务健康检查报告",
        "",
        "仅供客户内部自查。",
        "",
        "## 检查范围",
        f"- 检查期间: {request.period.start.isoformat()} 至 {request.period.end.isoformat()}",
        f"- 适用地区: {'中国大陆' if request.jurisdiction == 'CN' else request.jurisdiction}",
        f"- 修订版本: {snapshot.revision}",
        f"- 输入哈希数量: {len(request.input_hashes)}",
        "",
        "## 状态汇总",
    ]
    lines.extend(f"- {_STATUS_LABELS[status]}: {counts[status]}" for status in TaxStatus)
    lines.extend(("", "## 税种检查结果"))
    for definition in TAX_DEFINITIONS:
        lines.extend(("", f"### {definition.name_zh}"))
        result = results_by_tax_id.get(definition.tax_id)
        if result is None:
            lines.append("- 检查结果: 暂时无法判断")
        else:
            lines.extend(_result_lines(result))

    lines.extend(("", "## 待确认客户问题"))
    if snapshot.questions:
        for question in snapshot.questions:
            lines.extend(("", *_question_lines(question)))
    else:
        lines.append("- 当前没有待确认问题。")

    lines.extend(("", "## 规则来源与版本"))
    rendered_sources = _sources(snapshot.results)
    if rendered_sources:
        for source in rendered_sources:
            lines.append(f"- [{source.title}]({source.url})（发布日: {source.published_on.isoformat()}）")
    else:
        lines.append("- 当前快照没有可用规则来源。")

    lines.extend(("", "## 明确限制"))
    limitations = effective_limitations(snapshot)
    if limitations:
        lines.extend(
            f"- {item.message_zh}（{item.code}）"
            for item in limitations
        )
    else:
        lines.append("- 当前快照没有另行登记的明确限制。")
    lines.extend(("", "## 局限性与声明"))
    lines.extend(f"- {line}" for line in _FIXED_DISCLAIMER)
    return "\n".join(lines) + "\n"


def render_tax_health_report(
    snapshot: TaxCheckSnapshot,
    *,
    entity_name: str | None = None,
    reviewed_findings: Sequence[Mapping[str, object]] = (),
) -> str:
    """Render the owner report first and retain the full technical appendix."""

    owner = render_report_view(build_report_view(
        snapshot,
        entity_name=entity_name,
        reviewed_findings=reviewed_findings,
    ))
    owner_prefix = owner.split("## 专业附录", 1)[0]
    technical = _render_technical_report(snapshot)
    technical_lines = technical.splitlines()
    if technical_lines and technical_lines[0].startswith("# "):
        technical_lines[0] = "### 技术检查明细"
    return owner_prefix + "## 专业附录\n\n" + "\n".join(technical_lines) + "\n"


def _write_text_atomic(path: Path, content: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return path


def write_tax_health_report(
    snapshot: TaxCheckSnapshot,
    path: Path,
    *,
    entity_name: str | None = None,
    reviewed_findings: Sequence[Mapping[str, object]] = (),
) -> Path:
    """Atomically write the Chinese customer self-check Markdown report."""
    return _write_text_atomic(Path(path), render_tax_health_report(
        snapshot,
        entity_name=entity_name,
        reviewed_findings=reviewed_findings,
    ))


def write_tax_health_snapshot(snapshot: TaxCheckSnapshot, path: Path) -> Path:
    """Atomically write the allowlisted machine snapshot payload as JSON."""
    content = json.dumps(
        tax_snapshot_payload(snapshot), ensure_ascii=False, sort_keys=True
    ) + "\n"
    return _write_text_atomic(Path(path), content)


def write_tax_health_report_pdf(
    snapshot: TaxCheckSnapshot,
    path: Path,
    *,
    entity_name: str | None = None,
    reviewed_findings: Sequence[Mapping[str, object]] = (),
    material_statuses: Sequence[Mapping[str, object]] = (),
) -> Path:
    """Write the official v3 PDF from the deterministic HTML master."""

    view = build_report_view_v3(
        snapshot,
        entity_name=entity_name or "未命名企业",
        reviewed_findings=reviewed_findings,
        material_statuses=material_statuses,
    )
    return write_report_pdf(view, Path(path))
