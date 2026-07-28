"""Provider boundaries for importing document extraction results."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping, Protocol

from .document_extraction import (
    DOCUMENT_EXTRACTION_SCHEMA_VERSION,
    AuthorizationRecord,
    DocumentExtraction,
    ExtractionIssue,
    document_extraction_from_dict,
)


@dataclass(frozen=True, slots=True)
class LocalDocumentSource:
    source_id: str
    path: Path
    media_type: str
    case_input_dir: Path


@dataclass(frozen=True, slots=True)
class ExtractionOptions:
    authorization: AuthorizationRecord | None = None


class DocumentProvider(Protocol):
    def extract(self, source: LocalDocumentSource, options: ExtractionOptions) -> DocumentExtraction: ...


def _failed(source: LocalDocumentSource, code: str) -> DocumentExtraction:
    return DocumentExtraction(
        schema_version=DOCUMENT_EXTRACTION_SCHEMA_VERSION,
        document_id=f"failed-{source.source_id}",
        source_id=source.source_id,
        filename=source.path.name,
        media_type=source.media_type,
        provider="host",
        provider_version="1",
        executed_at="1970-01-01T00:00:00+00:00",
        authorization=AuthorizationRecord("host", "1", "none", "system", "1970-01-01T00:00:00+00:00", "none", "none"),
        status="failed",
        pages=(), blocks=(), tables=(),
        issues=(ExtractionIssue(code=code, severity="error", message=code),),
        source_refs=(source.source_id,),
    )


def _safe_path(source: LocalDocumentSource) -> bool:
    try:
        root = source.case_input_dir.resolve(strict=True)
        path = source.path.resolve(strict=True)
    except OSError:
        return False
    if path == root or root not in path.parents:
        return False
    current = path
    while current != root:
        if current.is_symlink():
            return False
        current = current.parent
    return True


class OffProvider:
    def extract(self, source: LocalDocumentSource, options: ExtractionOptions) -> DocumentExtraction:
        return DocumentExtraction(
            schema_version=DOCUMENT_EXTRACTION_SCHEMA_VERSION,
            document_id=f"off-{source.source_id}",
            source_id=source.source_id,
            filename=source.path.name,
            media_type=source.media_type,
            provider="off",
            provider_version="1",
            executed_at="1970-01-01T00:00:00+00:00",
            authorization=options.authorization or AuthorizationRecord("off", "1", "none", "system", "1970-01-01T00:00:00+00:00", "none", "none"),
            status="not_performed",
            pages=(), blocks=(), tables=(),
            issues=(ExtractionIssue(code="provider_off", severity="info", message="provider_off"),),
            source_refs=(source.source_id,),
        )


class HostProvider:
    def import_result(self, source: LocalDocumentSource, payload: Mapping[str, object]) -> DocumentExtraction:
        if not _safe_path(source):
            return _failed(source, "unsafe_source_path")
        try:
            result = document_extraction_from_dict(payload)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return _failed(source, "invalid_provider_payload")
        if result.media_type != source.media_type:
            return _failed(source, "media_type_mismatch")
        return DocumentExtraction(
            schema_version=result.schema_version,
            document_id=result.document_id,
            source_id=source.source_id,
            filename=source.path.name,
            media_type=source.media_type,
            provider="host",
            provider_version=result.provider_version,
            executed_at=result.executed_at,
            authorization=result.authorization,
            status=result.status,
            pages=result.pages,
            blocks=result.blocks,
            tables=result.tables,
            issues=result.issues,
            source_refs=(source.source_id,),
        )

