from datetime import date
from pathlib import Path

from open_tax_check.html_report import render_report_html, write_report_html
from open_tax_check.report_view import build_report_view_v3
from open_tax_check.tax_models import DateRange, TaxCheckRequest, TaxCheckSnapshot


def _view(*, entity_name: str = "虚构科技有限公司"):
    snapshot = TaxCheckSnapshot(
        request=TaxCheckRequest(
            DateRange(date(2026, 1, 1), date(2026, 12, 31)),
            "CN",
        ),
        results=(),
        questions=(),
        audit_events=(),
        revision=7,
    )
    return build_report_view_v3(
        snapshot,
        entity_name=entity_name,
        reviewed_findings=({
            "finding_id": "f-entertainment",
            "title_zh": "业务招待费扣除核对",
            "severity": "high",
            "tax_ids": ("enterprise_income_tax",),
            "affected_period_zh": "2026年度",
            "review_state": "approved",
            "observed_zh": "账载金额需要与销售收入限额重新核对。",
            "required_materials_zh": ("年度申报表",),
            "evidence_refs": ("journal-2026",),
        },),
        material_statuses=({
            "source_id": "cit-return",
            "display_name_zh": "企业所得税申报表",
            "status": "received_pending",
            "tax_ids": ("enterprise_income_tax",),
        },),
    )


def test_html_contains_fixed_sections_in_customer_reading_order():
    html = render_report_html(_view())
    section_ids = (
        "reading-notice",
        "executive-summary",
        "risk-overview",
        "priority-actions",
        "risk-details",
        "tax-coverage",
        "material-scope",
        "appendix",
    )

    positions = [html.index(f'<section id="{section_id}"') for section_id in section_ids]

    assert positions == sorted(positions)
    assert "税务健康检查报告" in html
    assert "THR-20260101-20261231-R0007" in html


def test_html_contains_scenario_map_with_safe_counts():
    """A missing scenario-map section would hide the coverage boundary from customers."""

    html = render_report_html(_view())

    assert 'id="scenario-map"' in html
    assert html.index('href="#scenario-map"') < html.index('href="#priority-actions"')
    assert html.index('<section id="risk-overview"') < html.index('<section id="scenario-map"')
    assert html.index('<section id="scenario-map"') < html.index('<section id="priority-actions"')
    assert "待补资料不计入已执行或已通过" in html
    assert 'data-scenario-id="revenue_and_turnover"' in html
    assert 'data-scenario-id="costs_and_expenses"' in html
    assert 'data-scenario-id="assets_and_real_estate"' in html
    assert 'data-scenario-id="contracts_and_special_transactions"' in html
    assert 'data-scenario-id="people_shareholders_and_related_parties"' in html
    for label in ("场景", "合计", "已执行且未见异常", "已执行需关注", "待补资料", "已收到待解析", "不适用", "未执行"):
        assert label in html


def test_html_escapes_customer_text_and_contains_no_external_assets():
    html = render_report_html(_view(entity_name='<script src="https://evil.example/x.js">'))

    assert "<script" not in html
    assert "&lt;script src=&quot;https://evil.example/x.js&quot;&gt;" in html
    assert "http://" not in html
    assert "https://" not in html.replace("https://evil.example/x.js", "")
    assert 'src="http' not in html
    assert 'href="http' not in html


def test_html_embeds_fonts_and_defines_stable_a4_print_layout():
    html = render_report_html(_view())

    assert html.count("data:font/ttf;base64,") == 1
    assert "@page {" in html
    assert "size: A4" in html
    assert 'font-family: "OpenTaxCheck Sans"' in html
    assert "letter-spacing: 0" in html
    assert "table-layout: fixed" in html
    assert "display: table-header-group" in html
    assert "gradient" not in html.casefold()
    assert "watermark" not in html.casefold()


def test_long_tables_are_chunked_with_repeated_headers():
    html = render_report_html(_view())

    assert html.count("已执行程序") >= 3
    assert html.count('class="report-table-group"') >= 3


def test_write_report_html_creates_utf8_document(tmp_path: Path):
    output = tmp_path / "nested" / "report.html"

    result = write_report_html(_view(), output)

    assert result == output
    assert output.read_text(encoding="utf-8").startswith("<!doctype html>")
