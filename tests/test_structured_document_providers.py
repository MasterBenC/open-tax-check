from copy import deepcopy
import hashlib

import pytest

from open_tax_check.structured_document_providers import (
    StructuredDocumentImportError,
    StructuredDocumentSource,
    import_financial_statement,
    import_tax_return,
)
from test_structured_documents import financial_payload, tax_return_payload


def source(tmp_path, *, digest=None):
    root = tmp_path / "input"
    root.mkdir()
    path = root / "statement.pdf"
    path.write_bytes(b"fictional source")
    return StructuredDocumentSource(
        source_id="trusted-source",
        path=path,
        media_type="application/pdf",
        input_root=root,
        sha256=digest or hashlib.sha256(path.read_bytes()).hexdigest(),
        entity_name="完全虚构科技有限公司",
        period_start="2026-01-01",
        period_end="2026-12-31",
    )


@pytest.mark.parametrize(
    ("payload_factory", "importer"),
    [(financial_payload, import_financial_statement), (tax_return_payload, import_tax_return)],
)
def test_import_rebinds_all_source_anchors_without_mutating_payload(
    tmp_path, payload_factory, importer
):
    payload = payload_factory()
    payload["issues"] = [{
        "code": "notice",
        "severity": "low",
        "message_zh": "虚构提示",
        "line_code": None,
        "source_anchor": deepcopy(payload["lines"][0]["source_anchor"]),
    }]
    original = deepcopy(payload)
    value = importer(source(tmp_path), payload)

    assert value.source_id == "trusted-source"
    assert all(line.source_anchor.source_id == "trusted-source" for line in value.lines)
    assert value.issues[0].source_anchor.source_id == "trusted-source"
    assert payload == original


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("invalid_digest", "source_mismatch"),
        ("mismatched_digest", "source_mismatch"),
        ("unsafe_path", "unsafe_source_path"),
        ("wrong_entity", "entity_mismatch"),
        ("outside_period", "period_mismatch"),
        ("malformed", "invalid_structured_document"),
        ("oversized", "invalid_structured_document"),
    ],
)
def test_financial_import_returns_only_stable_error_codes(tmp_path, change, expected):
    trusted = source(tmp_path)
    payload = financial_payload()
    if change == "invalid_digest":
        trusted = StructuredDocumentSource(
            trusted.source_id, trusted.path, trusted.media_type, trusted.input_root,
            "A" * 64, trusted.entity_name, trusted.period_start, trusted.period_end,
        )
    elif change == "mismatched_digest":
        trusted = StructuredDocumentSource(
            trusted.source_id, trusted.path, trusted.media_type, trusted.input_root,
            "0" * 64, trusted.entity_name, trusted.period_start, trusted.period_end,
        )
    elif change == "unsafe_path":
        outside = tmp_path / "outside.pdf"
        outside.write_bytes(b"fictional source")
        trusted = StructuredDocumentSource(
            trusted.source_id, outside, trusted.media_type, trusted.input_root,
            hashlib.sha256(outside.read_bytes()).hexdigest(), trusted.entity_name,
            trusted.period_start, trusted.period_end,
        )
    elif change == "wrong_entity":
        payload["entity_name"] = "另一虚构企业"
    elif change == "outside_period":
        payload["period_start"] = "2025-01-01"
    elif change == "malformed":
        payload["currency"] = "USD"
    else:
        payload["entity_name"] = "x" * (20 * 1024 * 1024)

    with pytest.raises(StructuredDocumentImportError) as captured:
        import_financial_statement(trusted, payload)

    assert captured.value.code == expected
    assert str(captured.value) == expected
