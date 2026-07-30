"""Trusted case-source boundary for structured financial documents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
import hashlib
import os
from pathlib import Path
import re

from .intake import MAX_FILE_BYTES
from .source_paths import resolve_safe_source
from .structured_documents import (
    FinancialStatement,
    SourceAnchor,
    StructuredIssue,
    TaxReturn,
    financial_statement_from_dict,
    tax_return_from_dict,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class StructuredDocumentSource:
    source_id: str
    path: Path
    media_type: str
    input_root: Path
    sha256: str
    entity_name: str
    period_start: str
    period_end: str


class StructuredDocumentImportError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> StructuredDocumentImportError:
    return StructuredDocumentImportError(code)


def _stream_digest(path: Path) -> str:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if before.st_size > MAX_FILE_BYTES:
                raise _fail("source_mismatch")
            for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise _fail("source_mismatch")
                digest.update(chunk)
            after = os.fstat(handle.fileno())
    except StructuredDocumentImportError:
        raise
    except OSError:
        raise _fail("source_mismatch") from None
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or size != before.st_size:
        raise _fail("source_mismatch")
    return digest.hexdigest()


def _validate_source(source: StructuredDocumentSource) -> Path:
    try:
        path = resolve_safe_source(source.path, source.input_root)
    except (OSError, ValueError):
        raise _fail("unsafe_source_path") from None
    if _SHA256.fullmatch(source.sha256) is None or _stream_digest(path) != source.sha256:
        raise _fail("source_mismatch")
    return path


def _trusted_anchor(anchor: SourceAnchor, source_id: str) -> SourceAnchor:
    return replace(anchor, source_id=source_id)


def _trusted_issue(issue: StructuredIssue, source_id: str) -> StructuredIssue:
    if issue.source_anchor is None:
        return issue
    return replace(issue, source_anchor=_trusted_anchor(issue.source_anchor, source_id))


def _validate_identity_and_period(
    source: StructuredDocumentSource,
    entity_name: str,
    period_start: str,
    period_end: str,
) -> None:
    if entity_name.strip() != source.entity_name.strip():
        raise _fail("entity_mismatch")
    try:
        source_start = date.fromisoformat(source.period_start)
        source_end = date.fromisoformat(source.period_end)
        document_start = date.fromisoformat(period_start)
        document_end = date.fromisoformat(period_end)
    except ValueError:
        raise _fail("period_mismatch") from None
    if source_start > source_end or document_start < source_start or document_end > source_end:
        raise _fail("period_mismatch")


def import_financial_statement(
    source: StructuredDocumentSource, payload: Mapping[str, object]
) -> FinancialStatement:
    _validate_source(source)
    try:
        value = financial_statement_from_dict(payload)
    except (TypeError, ValueError):
        raise _fail("invalid_structured_document") from None
    _validate_identity_and_period(source, value.entity_name, value.period_start, value.period_end)
    return replace(
        value,
        source_id=source.source_id,
        entity_name=source.entity_name.strip(),
        lines=tuple(
            replace(line, source_anchor=_trusted_anchor(line.source_anchor, source.source_id))
            for line in value.lines
        ),
        issues=tuple(_trusted_issue(issue, source.source_id) for issue in value.issues),
    )


def import_tax_return(
    source: StructuredDocumentSource, payload: Mapping[str, object]
) -> TaxReturn:
    _validate_source(source)
    try:
        value = tax_return_from_dict(payload)
    except (TypeError, ValueError):
        raise _fail("invalid_structured_document") from None
    _validate_identity_and_period(source, value.entity_name, value.period_start, value.period_end)
    return replace(
        value,
        source_id=source.source_id,
        entity_name=source.entity_name.strip(),
        lines=tuple(
            replace(line, source_anchor=_trusted_anchor(line.source_anchor, source.source_id))
            for line in value.lines
        ),
        issues=tuple(_trusted_issue(issue, source.source_id) for issue in value.issues),
    )
