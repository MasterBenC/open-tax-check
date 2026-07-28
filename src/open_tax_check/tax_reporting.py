"""Progressive, customer-safe renderers for tax-health-check snapshots."""

from __future__ import annotations

from collections.abc import Iterable

from .schemas import to_dict
from .tax_models import Question, RuleSource, TaxCheckSnapshot, TaxResult, TaxStatus
from .tax_registry import TAX_DEFINITIONS


_STATUS_LABELS = {
    TaxStatus.CALCULATED: "已按当前规则完成计算",
    TaxStatus.NOT_APPLICABLE: "当前资料显示不适用",
    TaxStatus.NEEDS_EVIDENCE: "尚需资料",
    TaxStatus.NEEDS_LOCAL_POLICY: "尚缺地方政策参数",
    TaxStatus.BLOCKED: "当前无法完成检查",
}

_FIXED_DISCLAIMER = (
    "仅供客户内部自查",
    "不属于涉税鉴证、专业税务意见或纳税申报结论",
    "取决于客户提供的资料、确认事实和规则版本",
    "资料或政策不足的项目未形成确定结论；客户自行决定是否申报、调整或另行咨询专业机构",
)


def tax_snapshot_payload(snapshot: TaxCheckSnapshot) -> dict[str, object]:
    """Return an allowlisted, JSON-friendly snapshot without customer facts."""
    return {
        "schema_version": snapshot.schema_version,
        "revision": snapshot.revision,
        "request": {
            "period": to_dict(snapshot.request.period),
            "jurisdiction": snapshot.request.jurisdiction,
            "input_hashes": list(snapshot.request.input_hashes),
        },
        "results": to_dict(snapshot.results),
        "questions": to_dict(snapshot.questions),
        "audit_events": to_dict(snapshot.audit_events),
    }


def _sources(results: Iterable[TaxResult]) -> tuple[RuleSource, ...]:
    sources: list[RuleSource] = []
    for result in results:
        for source in result.rule_sources:
            if source not in sources:
                sources.append(source)
    return tuple(sources)


def _result_lines(result: TaxResult) -> list[str]:
    lines = [
        f"- 机器状态: `{result.status.value}`",
        f"- 检查进度: {_STATUS_LABELS[result.status]}",
    ]
    for gap in result.gaps:
        if gap.reason_code == "missing_local_policy":
            lines.append(f"- 尚缺地方政策参数: `{gap.fact_key}`")
        else:
            lines.append(f"- 缺少资料: `{gap.fact_key}`")
        if gap.evidence_types:
            lines.append(f"- 所需佐证类型: {', '.join(gap.evidence_types)}")
    if result.rule_pack_ids:
        lines.append(f"- 规则包: {', '.join(f'`{pack_id}`' for pack_id in result.rule_pack_ids)}")
    if result.status is TaxStatus.BLOCKED and result.diagnostics:
        lines.append(f"- 阻断诊断: {', '.join(f'`{diagnostic}`' for diagnostic in result.diagnostics)}")
    for source in result.rule_sources:
        lines.append(f"- 适用规则来源: [{source.title}]({source.url})（{source.published_on.isoformat()}）")
    return lines


def _question_lines(question: Question) -> list[str]:
    lines = [
        f"### `{question.question_id}`",
        f"- 待确认事实: `{question.fact_key}`",
        f"- 涉及税种: {', '.join(f'`{tax_id}`' for tax_id in question.tax_ids)}",
        f"- 原因: {', '.join(f'`{reason}`' for reason in question.reason_codes)}",
        f"- 优先级: {question.priority}",
        f"- 是否阻断: {'是' if question.blocking else '否'}",
    ]
    if question.evidence_types:
        lines.append(f"- 所需佐证类型: {', '.join(question.evidence_types)}")
    return lines


def render_tax_health_report(snapshot: TaxCheckSnapshot) -> str:
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
        f"- 适用辖区: {request.jurisdiction}",
        f"- 修订版本: {snapshot.revision}",
        f"- 输入哈希数量: {len(request.input_hashes)}",
        "",
        "## 状态汇总",
    ]
    lines.extend(f"- `{status.value}`: {counts[status]}" for status in TaxStatus)
    lines.extend(("", "## 税种检查结果"))
    for definition in TAX_DEFINITIONS:
        lines.extend(("", f"### {definition.name_zh}（`{definition.tax_id}`）"))
        result = results_by_tax_id.get(definition.tax_id)
        if result is None:
            lines.extend(("- 机器状态: `blocked`", "- 检查进度: 当前无法完成检查", "- 阻断诊断: `result_unavailable`"))
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

    lines.extend(("", "## 局限性与声明"))
    lines.extend(f"- {line}" for line in _FIXED_DISCLAIMER)
    return "\n".join(lines) + "\n"
