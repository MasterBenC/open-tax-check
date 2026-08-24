from dataclasses import replace
from datetime import date
from pathlib import Path
import re

from open_tax_check.html_report import render_report_html, write_report_html
from open_tax_check.report_view import build_report_view_v3
from open_tax_check.scenario_map import scenario_catalog
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


def _scenario_map_view():
    view = _view()
    coverage_items = []
    for item in view.scenario_coverage.items:
        if item.total_count == 4:
            coverage_items.append(
                replace(
                    item,
                    executed_clear_count=1,
                    executed_risk_count=1,
                    awaiting_evidence_count=1,
                    received_unparsed_count=1,
                    not_applicable_count=0,
                    not_performed_count=0,
                )
            )
        elif item.total_count == 1:
            coverage_items.append(
                replace(
                    item,
                    executed_clear_count=0,
                    executed_risk_count=0,
                    awaiting_evidence_count=0,
                    received_unparsed_count=0,
                    not_applicable_count=0,
                    not_performed_count=1,
                )
            )
        else:
            coverage_items.append(item)
    coverage = replace(view.scenario_coverage, items=tuple(coverage_items))
    return replace(view, scenario_coverage=coverage)


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

    html = render_report_html(_scenario_map_view())

    assert 'id="scenario-map"' in html
    assert html.index('href="#scenario-map"') < html.index('href="#priority-actions"')
    assert html.index('<section id="risk-overview"') < html.index('<section id="scenario-map"')
    assert html.index('<section id="scenario-map"') < html.index('<section id="priority-actions"')
    assert "待补资料、已收到待解析和未执行均不计入已执行或已通过。" in html
    rows = re.findall(r'<tr data-scenario-id="([^"]+)">(.*?)</tr>', html)
    assert len(rows) == 5
    assert [scenario_id for scenario_id, _row in rows] == [
        item.scenario_id for item in scenario_catalog()
    ]
    row_cells = {
        scenario_id: re.findall(r"<td[^>]*>([^<]*)</td>", row)
        for scenario_id, row in rows
    }
    assert row_cells["revenue_and_turnover"] == ["4", "1", "1", "1", "1", "0", "0"]
    assert row_cells["costs_and_expenses"] == ["1", "0", "0", "0", "0", "0", "1"]
    for label in ("场景", "合计", "已执行且未见异常", "已执行需关注", "待补资料", "已收到待解析", "不适用", "未执行"):
        assert label in html


def test_html_escapes_scenario_display_name_and_keeps_appendix_ids_in_appendix():
    view = _view()
    special_items = tuple(
        replace(item, name_zh="<收入&测试>") if index == 0 else item
        for index, item in enumerate(view.scenario_coverage.items)
    )
    special_view = replace(
        view,
        scenario_coverage=replace(view.scenario_coverage, items=special_items),
        appendix_executed_checks=(
            "BOOK-FS-REV-001",
            "FS-VAT-SALES-001",
            "BOOK-VAT-OUTPUT-001",
            "FS-CIT-REV-PROFIT-001",
            "BOOK-CIT-BE-001",
        ),
    )

    html = render_report_html(special_view)

    assert "<收入&测试>" not in html
    assert html.count("&lt;收入&amp;测试&gt;") == 1
    appendix_start = html.index('<section id="appendix"')
    for check_id in special_view.appendix_executed_checks:
        assert html.index(f"<li>{check_id}</li>") > appendix_start


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
