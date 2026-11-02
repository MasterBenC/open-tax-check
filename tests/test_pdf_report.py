from dataclasses import replace
from datetime import date
from pathlib import Path
import re
import unicodedata

import pytest
from pypdf import PdfReader, PdfWriter

from open_tax_check.pdf_report import (
    PdfExportUnavailable,
    _chromium_launch_options,
    _footer_template,
    _header_template,
    sync_playwright,
    validate_report_pdf,
    write_report_pdf,
)
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
        revision=3,
    )
    return build_report_view_v3(
        snapshot,
        entity_name=entity_name,
        reviewed_findings=({
            "finding_id": "f-entertainment",
            "title_zh": "业务招待费扣除核对",
            "severity": "high",
            "tax_ids": ("enterprise_income_tax",),
            "review_state": "approved",
            "observed_zh": "账载金额需要与销售收入限额重新核对。",
        },),
    )


def _require_chromium() -> None:
    if sync_playwright is None:
        pytest.skip("Playwright is not installed")
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(**_chromium_launch_options())
            browser.close()
    except Exception as error:
        pytest.skip(f"Chromium unavailable: {error}")


def _flatten_outline_titles(items: object) -> tuple[str, ...]:
    titles: list[str] = []
    if not isinstance(items, list):
        return ()
    for item in items:
        if isinstance(item, list):
            titles.extend(_flatten_outline_titles(item))
            continue
        title = getattr(item, "title", None)
        if isinstance(title, str):
            titles.append(title)
    return tuple(titles)


def test_renderer_unavailable_leaves_no_customer_file(tmp_path: Path, monkeypatch):
    output = tmp_path / "税务健康检查报告.pdf"
    monkeypatch.setattr("open_tax_check.pdf_report.sync_playwright", None)

    with pytest.raises(PdfExportUnavailable, match="^pdf_renderer_unavailable$"):
        write_report_pdf(_view(), output)

    assert not output.exists()
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_validation_rejects_malformed_pdf(tmp_path: Path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7\nnot-a-pdf")

    with pytest.raises(PdfExportUnavailable, match="^pdf_validation_failed$"):
        validate_report_pdf(path, "THR-20260101-20261231-R0003")


def test_real_chromium_pdf_is_valid_and_contains_customer_identity(tmp_path: Path):
    _require_chromium()
    output = tmp_path / "税务健康检查报告.pdf"

    assert write_report_pdf(_view(), output) == output

    reader = PdfReader(output)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert len(reader.pages) > 0
    assert "税务健康检查报告" in text
    assert "THR-20260101-20261231-R0003" in text
    assert "虚构科技有限公司" in text
    assert reader.outline
    toc_text = reader.pages[1].extract_text() or ""
    assert "1. 1." not in toc_text
    printed_pages = [int(value) for value in re.findall("第\\s*(\\d+)", toc_text)]
    target_pages = []
    for reference in reader.pages[1].get("/Annots", ()):
        annotation = reference.get_object()
        destination = reader.named_destinations[str(annotation["/Dest"])]
        target_pages.append(reader.get_destination_page_number(destination) + 1)
    assert len(printed_pages) == len(target_pages) == 9
    assert printed_pages == target_pages


def _compact_pdf_text(text: str) -> str:
    """Collapse whitespace so soft-wrapped PDF tokens stay matchable."""

    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def test_real_chromium_pdf_contains_the_scenario_map_and_cross_check_boundary(tmp_path: Path):
    """Removing the map or its scope disclaimer would overstate report coverage."""

    _require_chromium()
    output = tmp_path / "税务健康检查报告.pdf"
    cross_check_ids = (
        "BOOK-FS-REV-001",
        "FS-VAT-SALES-001",
        "BOOK-VAT-OUTPUT-001",
        "FS-CIT-REV-PROFIT-001",
        "BOOK-CIT-BE-001",
    )
    view = replace(_view(), appendix_executed_checks=cross_check_ids)

    assert write_report_pdf(view, output) == output

    reader = PdfReader(output)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    normalized_text = unicodedata.normalize("NFKC", text)
    outline_titles = _flatten_outline_titles(reader.outline)
    assert not reader.is_encrypted
    assert "本次税务检查地图" in normalized_text
    assert "待补资料、已收到待解析和未执行均不计入已执行或已通过。" in normalized_text
    for name in ("收入与营业额", "成本与费用", "资产与不动产", "合同与特殊交易", "人员、股东与关联方"):
        assert name in normalized_text
    compact_text = _compact_pdf_text(text)
    for check_id in cross_check_ids:
        assert check_id in compact_text
    assert outline_titles.index("风险全景") < outline_titles.index("本次税务检查地图")
    assert outline_titles.index("本次税务检查地图") < outline_titles.index("优先处理清单")


def test_validation_rejects_pdf_missing_scenario_map_bookmark(tmp_path: Path):
    _require_chromium()
    source = tmp_path / "source.pdf"
    missing_map = tmp_path / "missing-map.pdf"
    write_report_pdf(_view(), source)

    reader = PdfReader(source)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    required_titles = (
        "阅读提示",
        "税务健康检查结论",
        "风险全景",
        "本次税务检查地图",
        "优先处理清单",
        "重点风险详解",
        "分税种检查结果",
        "资料范围与检查边界",
        "附录",
    )
    for title in required_titles:
        if title != "本次税务检查地图":
            writer.add_outline_item(title, 0)
    with missing_map.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(PdfExportUnavailable, match="^pdf_validation_failed$"):
        validate_report_pdf(missing_map, "THR-20260101-20261231-R0003")


def test_renderer_blocks_external_resource_before_publication(tmp_path: Path, monkeypatch):
    _require_chromium()
    output = tmp_path / "report.pdf"
    monkeypatch.setattr(
        "open_tax_check.pdf_report.render_report_html",
        lambda _view: '<html><body><img src="https://evil.example/pixel.png"></body></html>',
    )

    with pytest.raises(PdfExportUnavailable, match="^pdf_external_resource_blocked$"):
        write_report_pdf(_view(), output)

    assert not output.exists()


def test_renderer_blocks_non_http_external_resource_before_publication(tmp_path: Path, monkeypatch):
    _require_chromium()
    output = tmp_path / "report.pdf"
    monkeypatch.setattr(
        "open_tax_check.pdf_report.render_report_html",
        lambda _view: '<html><body><img src="ftp://evil.example/pixel.png"></body></html>',
    )

    with pytest.raises(PdfExportUnavailable, match="^pdf_external_resource_blocked$"):
        write_report_pdf(_view(), output)

    assert not output.exists()


def test_header_and_footer_embed_the_controlled_chinese_font():
    for template in (_header_template(_view()), _footer_template(_view())):
        assert '@font-face' in template
        assert 'data:font/ttf;base64,' in template
        assert 'font-family:"OpenTaxCheck Sans"' in template


def test_renderer_rejects_an_unloadable_embedded_font(tmp_path: Path, monkeypatch):
    _require_chromium()
    from open_tax_check.html_report import render_report_html as render_real_html

    output = tmp_path / "report.pdf"
    html = render_real_html(_view())
    marker = "data:font/ttf;base64,"
    start = html.index(marker) + len(marker)
    end = html.index('"', start)
    broken = html[:start] + "bm90LWEtZm9udA==" + html[end:]
    monkeypatch.setattr("open_tax_check.pdf_report.render_report_html", lambda _view: broken)

    with pytest.raises(PdfExportUnavailable, match="^pdf_font_unavailable$"):
        write_report_pdf(_view(), output)

    assert not output.exists()
