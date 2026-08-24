"""Offline semantic HTML renderer for tax-health-report/v3."""

from __future__ import annotations

from base64 import b64encode
from html import escape
from importlib import resources
from pathlib import Path

from .report_view import ReportViewV3


_ASSET_PACKAGE = "open_tax_check.report_assets"
_FONT_PATH = "fonts/NotoSansSC-VF.ttf"


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _section(section_id: str, title: str, content: str, *, classes: str = "") -> str:
    class_attr = f' class="{_e(classes)}"' if classes else ""
    return f'<section id="{_e(section_id)}"{class_attr}><h2>{_e(title)}</h2>{content}</section>'


def _cover(view: ReportViewV3) -> str:
    summary = view.executive_summary
    return (
        '<header class="cover">'
        '<div><p class="cover-kicker">OPEN TAX CHECK</p>'
        '<h1>税务健康检查报告</h1>'
        f'<p class="report-status">{_e(view.report_status_zh)}</p></div>'
        '<div class="cover-meta">'
        f'<p><strong>检查企业：</strong>{_e(summary.entity_name or "未命名企业")}</p>'
        f'<p><strong>检查期间：</strong>{_e(view.meta.period_zh)}</p>'
        f'<p><strong>报告编号：</strong>{_e(view.meta.report_id)}</p>'
        f'<p><strong>修订号：</strong>{view.meta.revision}</p>'
        '<p class="muted">保密文件 · 仅供企业内部税务健康检查使用</p>'
        '</div></header>'
    )


def _toc() -> str:
    entries = (
        ("reading-notice", "阅读提示"),
        ("executive-summary", "税务健康检查结论"),
        ("risk-overview", "风险全景"),
        ("scenario-map", "本次税务检查地图"),
        ("priority-actions", "优先处理清单"),
        ("risk-details", "重点风险详解"),
        ("tax-coverage", "分税种检查结果"),
        ("material-scope", "资料范围与检查边界"),
        ("appendix", "附录"),
    )
    items = "".join(
        f'<li><a href="#{_e(section_id)}"><span>{index}. {_e(label)}</span>'
        f'<span class="toc-page" data-title="{_e(label)}"></span></a></li>'
        for index, (section_id, label) in enumerate(entries, start=1)
    )
    return f'<nav class="toc page-break" aria-label="目录"><h2>目录</h2><ol>{items}</ol></nav>'


def _reading_notice(view: ReportViewV3) -> str:
    content = (
        '<p>本报告依据当前案件快照、已复核事项和已记录资料状态生成，用于企业内部税务健康检查。</p>'
        f'<p>{_e(view.executive_summary.amount_disclaimer_zh)}</p>'
        '<p>本报告不属于涉税鉴证、纳税申报结论或税务机关认定。资料、事实或规则版本发生变化时，应重新运行检查。</p>'
    )
    return _section("reading-notice", "阅读提示", content)


def _summary(view: ReportViewV3) -> str:
    summary = view.executive_summary
    content = (
        f'<p class="report-status">{_e(summary.status_zh)}</p>'
        '<div class="summary-grid summary-block">'
        f'<div class="metric"><strong>{summary.issue_count}</strong><span>重点事项</span></div>'
        f'<div class="metric"><strong>{summary.material_gap_count}</strong><span>待补资料</span></div>'
        f'<div class="metric"><strong>{summary.trial_amount_count}</strong><span>试算事项</span></div>'
        f'<div class="metric"><strong>{len(view.actions)}</strong><span>建议行动</span></div>'
        '</div>'
        f'<p class="disclaimer">{_e(summary.amount_disclaimer_zh)}</p>'
    )
    return _section("executive-summary", "税务健康检查结论", content)


def _risk_overview(view: ReportViewV3) -> str:
    overview = view.risk_overview
    content = (
        '<div class="overview-grid summary-block">'
        f'<div class="metric severity-high"><strong>{overview.high_count}</strong><span>重点核实</span></div>'
        f'<div class="metric severity-medium"><strong>{overview.medium_count}</strong><span>中等关注</span></div>'
        f'<div class="metric severity-notice"><strong>{overview.notice_count}</strong><span>一般提示</span></div>'
        f'<div class="metric severity-material"><strong>{overview.material_pending_count}</strong><span>资料待补</span></div>'
        '</div>'
    )
    return _section("risk-overview", "风险全景", content)


def _scenario_map(view: ReportViewV3) -> str:
    """Render the non-assurance scenario coverage boundary for customers."""

    rows = "".join(
        '<tr data-scenario-id="{scenario_id}">'
        '<th scope="row">{name}</th>'
        '<td>{total}</td>'
        '<td class="scenario-state scenario-state-clear">{executed_clear}</td>'
        '<td class="scenario-state scenario-state-risk">{executed_risk}</td>'
        '<td class="scenario-state scenario-state-evidence">{awaiting_evidence}</td>'
        '<td class="scenario-state scenario-state-unparsed">{received_unparsed}</td>'
        '<td class="scenario-state scenario-state-na">{not_applicable}</td>'
        '<td class="scenario-state scenario-state-pending">{not_performed}</td>'
        '</tr>'.format(
            scenario_id=_e(item.scenario_id),
            name=_e(item.name_zh),
            total=_e(item.total_count),
            executed_clear=_e(item.executed_clear_count),
            executed_risk=_e(item.executed_risk_count),
            awaiting_evidence=_e(item.awaiting_evidence_count),
            received_unparsed=_e(item.received_unparsed_count),
            not_applicable=_e(item.not_applicable_count),
            not_performed=_e(item.not_performed_count),
        )
        for item in view.scenario_coverage.items
    )
    header = (
        '<thead><tr><th scope="col" style="width:22%">场景</th>'
        '<th scope="col">合计</th>'
        '<th scope="col" class="scenario-state-clear">已执行且未见异常</th>'
        '<th scope="col" class="scenario-state-risk">已执行需关注</th>'
        '<th scope="col" class="scenario-state-evidence">待补资料</th>'
        '<th scope="col" class="scenario-state-unparsed">已收到待解析</th>'
        '<th scope="col" class="scenario-state-na">不适用</th>'
        '<th scope="col" class="scenario-state-pending">未执行</th></tr></thead>'
    )
    content = (
        '<p>按场景汇总本次检查程序的当前状态；待补资料不计入已执行或已通过。</p>'
        f'<table class="scenario-map-table">{header}<tbody>{rows}</tbody></table>'
    )
    return _section("scenario-map", "本次税务检查地图", content, classes="page-break")


def _actions(view: ReportViewV3) -> str:
    rows = "".join(
        f'<tr><td>{item.priority}</td><td>{_e(item.action_zh)}</td></tr>'
        for item in view.actions
    ) or '<tr><td colspan="2" class="empty">当前没有新增优先行动。</td></tr>'
    return _section(
        "priority-actions",
        "优先处理清单",
        '<table><thead><tr><th style="width:15%">顺序</th><th>建议动作</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>',
    )


def _risk_details(view: ReportViewV3) -> str:
    labels = {"high": "重点核实", "medium": "中等关注", "notice": "一般提示"}
    items = []
    for risk in view.risks:
        materials = "、".join(_e(item) for item in risk.required_materials_zh) or "无新增资料要求"
        evidence = "、".join(_e(item) for item in risk.evidence_refs) or "见案件证据清单"
        items.append(
            f'<article class="risk-item" id="risk-{_e(risk.finding_id)}" data-severity="{_e(risk.severity)}">'
            '<div class="risk-heading">'
            f'<p class="muted">{_e(labels[risk.severity])} · {_e(risk.affected_period_zh)}</p>'
            f'<h3>{_e(risk.title_zh)}</h3></div>'
            '<dl>'
            f'<div class="risk-field"><dt>发现了什么</dt><dd>{_e(risk.observed_zh)}</dd></div>'
            f'<div class="risk-field"><dt>为什么值得关注</dt><dd>{_e(risk.why_it_matters_zh)}</dd></div>'
            f'<div class="risk-field"><dt>当前能够得出的结论</dt><dd>{_e(risk.can_conclude_zh)}</dd></div>'
            f'<div class="risk-field"><dt>当前不能得出的结论</dt><dd>{_e(risk.cannot_conclude_zh)}</dd></div>'
            f'<div class="risk-field"><dt>数据与凭证依据</dt><dd>{evidence}</dd></div>'
            f'<div class="risk-field"><dt>尚需补充或确认</dt><dd>{materials}</dd></div>'
            f'<div class="risk-field"><dt>建议处理动作</dt><dd>{_e(risk.next_action_zh)}</dd></div>'
            '</dl></article>'
        )
    content = "".join(items) or '<p class="empty">当前没有经复核后进入正式报告的重点事项。</p>'
    return _section("risk-details", "重点风险详解", content, classes="page-break")


def _tax_coverage(view: ReportViewV3) -> str:
    rows = []
    for item in view.tax_coverage:
        checks = "；".join(_e(value) for value in item.executed_checks_zh)
        limits = "；".join(_e(value) for value in item.material_limitations_zh) or "无新增资料限制"
        amount = _e(item.trial_amount) if item.trial_amount is not None else "—"
        rows.append(
            f'<tr id="tax-{_e(item.tax_id)}"><td>{_e(item.name_zh)}</td><td>{_e(item.client_state)}</td>'
            f'<td>{checks}</td><td>{limits}</td><td>{amount}</td></tr>'
        )
    header = (
        '<thead><tr><th style="width:14%">税种</th><th style="width:14%">状态</th>'
        '<th>已执行程序</th><th>资料限制</th><th style="width:12%">试算金额</th></tr></thead>'
    )
    content = "".join(
        f'<table class="report-table-group">{header}<tbody>{"".join(rows[index:index + 8])}</tbody></table>'
        for index in range(0, len(rows), 8)
    )
    return _section("tax-coverage", "分税种检查结果", content)


def _material_scope(view: ReportViewV3) -> str:
    rows = [
        f'<tr><td>{_e(item.display_name_zh)}</td><td>{_e(item.status_zh)}</td><td>{_e(item.impact_zh)}</td></tr>'
        for item in view.materials
    ]
    if not rows:
        rows = ['<tr><td colspan="3" class="empty">当前没有单独记录的资料处理状态。</td></tr>']
    limitations = "".join(f'<li>{_e(item)}</li>' for item in view.limitations_zh)
    header = '<thead><tr><th style="width:25%">资料</th><th style="width:20%">处理状态</th><th>影响范围</th></tr></thead>'
    tables = "".join(
        f'<table class="report-table-group">{header}<tbody>{"".join(rows[index:index + 4])}</tbody></table>'
        for index in range(0, len(rows), 4)
    )
    content = tables + (
        f'<h3>检查边界</h3><ul>{limitations or "<li>本报告仅覆盖当前快照中已执行的检查程序。</li>"}</ul>'
    )
    return _section("material-scope", "资料范围与检查边界", content)


def _appendix(view: ReportViewV3) -> str:
    checks = "".join(f'<li>{_e(item)}</li>' for item in view.appendix_executed_checks)
    content = (
        '<h3>全部已执行检查项</h3>'
        f'<ul>{checks or "<li>当前快照没有已执行检查项。</li>"}</ul>'
        '<h3>报告修订信息</h3>'
        f'<p>报告协议：{_e(view.meta.schema_version)}；报告编号：{_e(view.meta.report_id)}；修订号：{view.meta.revision}。</p>'
    )
    return _section("appendix", "附录", content, classes="page-break")


def render_report_html(view: ReportViewV3) -> str:
    """Render one self-contained HTML document without network resources."""

    asset_root = resources.files(_ASSET_PACKAGE)
    css = asset_root.joinpath("report.css").read_text(encoding="utf-8")
    font_bytes = asset_root.joinpath(_FONT_PATH).read_bytes()
    font_data = b64encode(font_bytes).decode("ascii")
    font_css = (
        '@font-face { font-family: "OpenTaxCheck Sans"; '
        f'src: url("data:font/ttf;base64,{font_data}") format("truetype"); '
        'font-style: normal; font-weight: 100 900; font-display: block; }'
    )
    body = "".join((
        _cover(view), _toc(), _reading_notice(view), _summary(view),
        _risk_overview(view), _scenario_map(view), _actions(view), _risk_details(view),
        _tax_coverage(view), _material_scope(view), _appendix(view),
    ))
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta name="report-id" content="{_e(view.meta.report_id)}">'
        f'<style>{font_css}\n{css}</style></head><body>{body}</body></html>'
    )


def write_report_html(view: ReportViewV3, path: Path) -> Path:
    """Write the self-contained v3 report master as UTF-8 HTML."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(render_report_html(view), encoding="utf-8", newline="\n")
    temporary.replace(destination)
    return destination


__all__ = ["render_report_html", "write_report_html"]
