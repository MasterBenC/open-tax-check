"""Progressive, customer-safe renderers for tax-health-check snapshots."""

from __future__ import annotations

from collections.abc import Iterable

from .schemas import to_dict
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


def tax_snapshot_payload(snapshot: TaxCheckSnapshot) -> dict[str, object]:
    """Return an allowlisted, JSON-friendly snapshot without customer facts."""
    result_payloads = []
    for result in snapshot.results:
        payload = to_dict(result)
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
        f"- 检查结果: {_STATUS_LABELS[result.status]}",
    ]
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

    lines.extend(("", "## 局限性与声明"))
    lines.extend(f"- {line}" for line in _FIXED_DISCLAIMER)
    return "\n".join(lines) + "\n"
