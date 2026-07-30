from pathlib import Path
from dataclasses import asdict
import os

import pytest

from open_tax_check.document_extraction import AuthorizationRecord, DocumentPage, DocumentExtraction
from open_tax_check import document_providers
from open_tax_check.document_providers import ExtractionOptions, HostProvider, LocalDocumentSource, OffProvider


def source(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    path = root / "a.pdf"
    path.write_bytes(b"x")
    return LocalDocumentSource("src-1", path, "application/pdf", root)


def payload():
    auth = AuthorizationRecord("host", "1", "text", "client", "2026-07-29T00:00:00+00:00", "file", "none")
    return {
        "schema_version": "document-extraction/v1", "document_id": "doc", "source_id": "forged",
        "filename": "forged.pdf", "media_type": "application/pdf", "provider": "host", "provider_version": "1",
        "executed_at": "2026-07-29T00:00:00+00:00", "authorization": asdict(auth),
        "status": "completed", "pages": [], "blocks": [], "tables": [], "issues": [], "source_refs": ["forged"],
    }


def test_off_provider_does_not_read_content(tmp_path):
    result = OffProvider().extract(source(tmp_path), ExtractionOptions())
    assert result.status == "not_performed"


def test_host_provider_rebinds_source_and_filename(tmp_path):
    result = HostProvider().import_result(source(tmp_path), payload())
    assert result.source_id == "src-1"
    assert result.filename == "a.pdf"
    assert result.source_refs == ("src-1",)


def test_host_provider_rejects_unsafe_path(tmp_path):
    item = source(tmp_path)
    unsafe = LocalDocumentSource(item.source_id, tmp_path / "outside.pdf", item.media_type, item.case_input_dir)
    result = HostProvider().import_result(unsafe, payload())
    assert result.status == "failed"
    assert result.issues[0].code == "unsafe_source_path"


def test_host_provider_rejects_symlink_inside_case_input(tmp_path):
    item = source(tmp_path)
    link = item.case_input_dir / "linked.pdf"
    try:
        os.symlink(item.path, link)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    linked = LocalDocumentSource(item.source_id, link, item.media_type, item.case_input_dir)

    result = HostProvider().import_result(linked, payload())

    assert result.status == "failed"
    assert result.issues[0].code == "unsafe_source_path"


def test_host_provider_maps_shared_safe_path_failure(tmp_path, monkeypatch):
    item = source(tmp_path)

    def reject_path(path, root):
        raise ValueError("unsafe_source_path")

    monkeypatch.setattr(document_providers, "resolve_safe_source", reject_path)

    result = HostProvider().import_result(item, payload())

    assert result.status == "failed"
    assert result.issues[0].code == "unsafe_source_path"
