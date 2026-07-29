"""Immutable, strictly validated document extraction contract."""

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Mapping

DOCUMENT_EXTRACTION_SCHEMA_VERSION = "document-extraction/v1.1"
LEGACY_DOCUMENT_EXTRACTION_SCHEMA_VERSION = "document-extraction/v1"
_SUPPORTED_SCHEMA_VERSIONS = {
    DOCUMENT_EXTRACTION_SCHEMA_VERSION,
    LEGACY_DOCUMENT_EXTRACTION_SCHEMA_VERSION,
}
_STATUSES = {"not_requested", "not_performed", "completed", "partial", "failed"}
_PROVIDERS = {"off", "host", "cloud"}
_MAX_TEXT = 1024 * 1024
_MAX_BLOCKS = 100_000
_MAX_PAGES = 10_000
_MAX_JSON = 20 * 1024 * 1024


def _coordinates(value):
    try:
        result = tuple(tuple(float(v) for v in point) for point in value)
    except (TypeError, ValueError):
        raise ValueError("invalid_coordinates") from None
    if len(result) != 4 or any(len(point) != 2 for point in result):
        raise ValueError("invalid_coordinates")
    return result


def _confidence(value):
    if isinstance(value, bool):
        raise ValueError("invalid_confidence")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError("invalid_confidence") from None
    if not 0 <= value <= 1:
        raise ValueError("invalid_confidence")
    return value


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError("invalid_timestamp")
    return value


@dataclass(frozen=True, slots=True)
class DocumentPage:
    page_number: int
    width: float
    height: float
    unit: str
    rotation: int = 0

    def __post_init__(self):
        if isinstance(self.page_number, bool) or self.page_number < 1: raise ValueError("invalid_page_number")
        if self.width <= 0 or self.height <= 0: raise ValueError("invalid_page_size")
        if self.rotation not in (0, 90, 180, 270): raise ValueError("invalid_rotation")


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    page_number: int
    coordinates: tuple[tuple[float, float], ...]
    confidence: float | None
    confidence_source: str = "model"
    language: str | None = None

    def __post_init__(self):
        if not isinstance(self.text, str) or len(self.text.encode("utf-8")) > _MAX_TEXT: raise ValueError("text_too_long")
        if self.page_number < 1: raise ValueError("invalid_page_number")
        object.__setattr__(self, "coordinates", _coordinates(self.coordinates))
        if self.confidence_source not in {"model", "native_unscored"}:
            raise ValueError("invalid_confidence_source")
        if self.confidence_source == "native_unscored":
            if self.confidence is not None:
                raise ValueError("native_confidence_must_be_none")
        else:
            object.__setattr__(self, "confidence", _confidence(self.confidence))


@dataclass(frozen=True, slots=True)
class TableCell:
    text: str
    page_number: int
    coordinates: tuple[tuple[float, float], ...]
    confidence: float | None
    row_index: int
    column_index: int
    confidence_source: str = "model"

    def __post_init__(self):
        if not isinstance(self.text, str) or len(self.text.encode("utf-8")) > _MAX_TEXT: raise ValueError("text_too_long")
        if self.page_number < 1 or self.row_index < 0 or self.column_index < 0: raise ValueError("invalid_index")
        object.__setattr__(self, "coordinates", _coordinates(self.coordinates))
        if self.confidence_source not in {"model", "native_unscored"}:
            raise ValueError("invalid_confidence_source")
        if self.confidence_source == "native_unscored":
            if self.confidence is not None:
                raise ValueError("native_confidence_must_be_none")
        else:
            object.__setattr__(self, "confidence", _confidence(self.confidence))


@dataclass(frozen=True, slots=True)
class TableRegion:
    page_number: int
    coordinates: tuple[tuple[float, float], ...]
    confidence: float
    cells: tuple[TableCell, ...]

    def __post_init__(self):
        if self.page_number < 1: raise ValueError("invalid_page_number")
        object.__setattr__(self, "coordinates", _coordinates(self.coordinates))
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(self, "cells", tuple(self.cells))


@dataclass(frozen=True, slots=True)
class ExtractionIssue:
    code: str
    severity: str
    message: str
    page_number: int | None = None
    area: tuple[tuple[float, float], ...] | None = None

    def __post_init__(self):
        if self.page_number is not None and self.page_number < 1: raise ValueError("invalid_page_number")
        if self.area is not None: object.__setattr__(self, "area", _coordinates(self.area))


@dataclass(frozen=True, slots=True)
class AuthorizationRecord:
    provider: str
    provider_version: str
    transfer_scope: str
    subject: str
    authorized_at: str
    scope: str
    cache_retention: str
    revoked: bool = False

    def __post_init__(self):
        if self.provider not in _PROVIDERS: raise ValueError("invalid_provider")
        object.__setattr__(self, "authorized_at", _timestamp(self.authorized_at))


@dataclass(frozen=True, slots=True)
class DocumentExtraction:
    schema_version: str
    document_id: str
    source_id: str
    filename: str
    media_type: str
    provider: str
    provider_version: str
    executed_at: str
    authorization: AuthorizationRecord
    status: str
    pages: tuple[DocumentPage, ...]
    blocks: tuple[TextBlock, ...]
    tables: tuple[TableRegion, ...]
    issues: tuple[ExtractionIssue, ...]
    source_refs: tuple[str, ...]

    def __post_init__(self):
        if self.schema_version not in _SUPPORTED_SCHEMA_VERSIONS: raise ValueError("invalid_schema_version")
        if self.provider not in _PROVIDERS: raise ValueError("invalid_provider")
        if self.status not in _STATUSES: raise ValueError("invalid_status")
        if self.status == "completed" and (self.provider == "off" or any(issue.code in {"off", "provider_off"} for issue in self.issues)): raise ValueError("invalid_completed_status")
        object.__setattr__(self, "executed_at", _timestamp(self.executed_at))
        for name in ("pages", "blocks", "tables", "issues", "source_refs"): object.__setattr__(self, name, tuple(getattr(self, name)))
        if len(self.pages) > _MAX_PAGES or len(self.blocks) > _MAX_BLOCKS: raise ValueError("count_limit_exceeded")
        numbers = [page.page_number for page in self.pages]
        if len(numbers) != len(set(numbers)): raise ValueError("duplicate_page_number")


def document_extraction_to_dict(value: DocumentExtraction) -> dict:
    if not isinstance(value, DocumentExtraction): raise TypeError("expected DocumentExtraction")
    def convert(item):
        if hasattr(item, "__dataclass_fields__"):
            return {name: convert(getattr(item, name)) for name in item.__dataclass_fields__}
        if isinstance(item, tuple): return [convert(v) for v in item]
        return item
    result = convert(value)
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > _MAX_JSON: raise ValueError("payload_too_large")
    return result


def document_extraction_from_dict(payload: Mapping[str, object]) -> DocumentExtraction:
    if not isinstance(payload, Mapping): raise ValueError("invalid_payload")
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > _MAX_JSON: raise ValueError("payload_too_large")
    allowed = {"schema_version", "document_id", "source_id", "filename", "media_type", "provider", "provider_version", "executed_at", "authorization", "status", "pages", "blocks", "tables", "issues", "source_refs"}
    if set(payload) != allowed: raise ValueError("unknown_or_missing_field")
    def require_map(value, fields):
        if not isinstance(value, Mapping) or set(value) != set(fields): raise ValueError("unknown_or_missing_field")
        return value
    a = require_map(payload["authorization"], ("provider", "provider_version", "transfer_scope", "subject", "authorized_at", "scope", "cache_retention", "revoked"))
    auth = AuthorizationRecord(**a)
    pages = tuple(DocumentPage(**require_map(v, ("page_number", "width", "height", "unit", "rotation"))) for v in payload["pages"])
    schema_version = payload["schema_version"]
    if schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError("invalid_schema_version")
    block_fields = ("text", "page_number", "coordinates", "confidence", "language")
    cell_fields = ("text", "page_number", "coordinates", "confidence", "row_index", "column_index")
    if schema_version == DOCUMENT_EXTRACTION_SCHEMA_VERSION:
        block_fields += ("confidence_source",)
        cell_fields += ("confidence_source",)
    def parse_block(value):
        record = dict(require_map(value, block_fields))
        if schema_version == LEGACY_DOCUMENT_EXTRACTION_SCHEMA_VERSION:
            record["confidence_source"] = "model"
        return TextBlock(**record)
    def parse_cell(value):
        record = dict(require_map(value, cell_fields))
        if schema_version == LEGACY_DOCUMENT_EXTRACTION_SCHEMA_VERSION:
            record["confidence_source"] = "model"
        return TableCell(**record)
    blocks = tuple(parse_block(v) for v in payload["blocks"])
    tables = tuple((lambda v: (require_map(v, ("page_number", "coordinates", "confidence", "cells"))))(v) for v in payload["tables"])
    tables = tuple(TableRegion(page_number=v["page_number"], coordinates=v["coordinates"], confidence=v["confidence"], cells=tuple(parse_cell(c) for c in v["cells"])) for v in tables)
    issues = tuple(ExtractionIssue(**require_map(v, ("code", "severity", "message", "page_number", "area"))) for v in payload["issues"])
    return DocumentExtraction(schema_version=payload["schema_version"], document_id=payload["document_id"], source_id=payload["source_id"], filename=payload["filename"], media_type=payload["media_type"], provider=payload["provider"], provider_version=payload["provider_version"], executed_at=payload["executed_at"], authorization=auth, status=payload["status"], pages=pages, blocks=blocks, tables=tables, issues=issues, source_refs=tuple(payload["source_refs"]))
