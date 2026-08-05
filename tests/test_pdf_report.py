from datetime import date
from pathlib import Path
import re

import pytest
from pypdf import PdfReader

from open_tax_check.pdf_report import (
    PdfExportUnavailable,
    _footer_template,
    _header_template,
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
    assert len(printed_pages) == len(target_pages) == 8
    assert printed_pages == target_pages


def test_renderer_blocks_external_resource_before_publication(tmp_path: Path, monkeypatch):
    output = tmp_path / "report.pdf"
    monkeypatch.setattr(
        "open_tax_check.pdf_report.render_report_html",
        lambda _view: '<html><body><img src="https://evil.example/pixel.png"></body></html>',
    )

    with pytest.raises(PdfExportUnavailable, match="^pdf_external_resource_blocked$"):
        write_report_pdf(_view(), output)

    assert not output.exists()


def test_renderer_blocks_non_http_external_resource_before_publication(tmp_path: Path, monkeypatch):
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
