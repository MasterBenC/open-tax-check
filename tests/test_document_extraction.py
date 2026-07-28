import pytest

from open_tax_check.document_extraction import (
    DOCUMENT_EXTRACTION_SCHEMA_VERSION,
    AuthorizationRecord,
    DocumentExtraction,
    DocumentPage,
    ExtractionIssue,
    TableCell,
    TableRegion,
    TextBlock,
    document_extraction_from_dict,
    document_extraction_to_dict,
)


def valid_result(**overrides):
    page = DocumentPage(page_number=1, width=100, height=200, unit="pt", rotation=0)
    block = TextBlock(text="hello", page_number=1, coordinates=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)), confidence=0.9, language="en")
    cell = TableCell(text="x", page_number=1, coordinates=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)), confidence=1.0, row_index=0, column_index=0)
    region = TableRegion(page_number=1, coordinates=cell.coordinates, confidence=0.8, cells=(cell,))
    auth = AuthorizationRecord(provider="off", provider_version="1", transfer_scope="none", subject="case", authorized_at="2026-07-29T10:00:00+08:00", scope="none", cache_retention="none", revoked=False)
    values = dict(schema_version=DOCUMENT_EXTRACTION_SCHEMA_VERSION, document_id="doc-1", source_id="src-1", filename="a.pdf", media_type="application/pdf", provider="host", provider_version="1", executed_at="2026-07-29T10:00:00+08:00", authorization=auth, status="completed", pages=(page,), blocks=(block,), tables=(region,), issues=(), source_refs=("src-1",))
    values.update(overrides)
    return DocumentExtraction(**values)


def test_valid_round_trip_and_immutable_models():
    value = valid_result()
    assert document_extraction_from_dict(document_extraction_to_dict(value)) == value
    assert isinstance(value.pages, tuple)
    with pytest.raises(Exception):
        value.status = "failed"


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra=True),
    lambda p: p.pop("schema_version"),
])
def test_unknown_or_missing_top_level_field_rejected(change):
    payload = document_extraction_to_dict(valid_result())
    change(payload)
    with pytest.raises(ValueError):
        document_extraction_from_dict(payload)


def test_invalid_bounds_and_duplicate_pages_rejected():
    payload = document_extraction_to_dict(valid_result())
    payload["status"] = "bogus"
    with pytest.raises(ValueError): document_extraction_from_dict(payload)
    payload = document_extraction_to_dict(valid_result())
    payload["blocks"][0]["coordinates"] = [[0, 0], [1, 1]]
    with pytest.raises(ValueError): document_extraction_from_dict(payload)
    payload = document_extraction_to_dict(valid_result())
    payload["blocks"][0]["confidence"] = 2
    with pytest.raises(ValueError): document_extraction_from_dict(payload)
    payload = document_extraction_to_dict(valid_result())
    payload["pages"].append(payload["pages"][0].copy())
    with pytest.raises(ValueError): document_extraction_from_dict(payload)


def test_text_and_not_performed_semantics():
    payload = document_extraction_to_dict(valid_result())
    payload["blocks"][0]["text"] = "x" * (1024 * 1024 + 1)
    with pytest.raises(ValueError): document_extraction_from_dict(payload)
    result = valid_result(status="not_performed", issues=(ExtractionIssue(code="off", severity="warning", message="not run"),), blocks=(), tables=())
    assert result.status == "not_performed"
    payload = document_extraction_to_dict(result)
    payload["status"] = "completed"
    with pytest.raises(ValueError): document_extraction_from_dict(payload)
