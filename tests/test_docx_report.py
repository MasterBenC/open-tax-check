from zipfile import ZipFile

from test_tax_health_reporting import snapshot_with_every_status

from open_tax_check.tax_reporting import write_tax_health_report_docx


def _parts(path):
    with ZipFile(path) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist() if name.endswith(".xml")}


def test_docx_contains_customer_report_structure(tmp_path):
    path = write_tax_health_report_docx(
        snapshot_with_every_status(), tmp_path / "report.docx"
    )
    parts = _parts(path)

    assert "word/header1.xml" in parts
    assert "word/footer1.xml" in parts
    assert "word/settings.xml" in parts
    assert parts["word/document.xml"].count("<w:tbl>") >= 3
    assert "PAGE" in parts["word/footer1.xml"]
    assert "专业附录" in parts["word/document.xml"]


def test_docx_has_named_styles_page_break_and_repeating_headers(tmp_path):
    path = write_tax_health_report_docx(
        snapshot_with_every_status(), tmp_path / "report.docx"
    )
    parts = _parts(path)
    styles = parts["word/styles.xml"]
    document = parts["word/document.xml"]

    for style_id in (
        "Title", "Heading1", "Heading2", "Heading3", "BodyText",
        "TableHeader", "IssueState", "Disclaimer",
    ):
        assert f'w:styleId="{style_id}"' in styles
    assert '<w:br w:type="page"/>' in document
    assert "<w:tblHeader/>" in document
    assert '<w:tblW w:w="9360" w:type="dxa"/>' in document
    assert '<w:tblInd w:w="120" w:type="dxa"/>' in document
    assert "health score" not in document.lower()
