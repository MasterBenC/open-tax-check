"""Trusted case-source boundary for structured financial documents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from .case import resolve_safe_case_path
from .intake import MAX_FILE_BYTES
from .source_paths import resolve_safe_source
from .structured_documents import (
    FinancialStatement,
    SourceAnchor,
    StructuredIssue,
    TaxReturn,
    financial_statement_from_dict,
    financial_statement_to_dict,
    tax_return_from_dict,
    tax_return_to_dict,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHUNK_BYTES = 1024 * 1024
STRUCTURED_DOCUMENT_INDEX_NAME = "structured-document-index.json"
STRUCTURED_DOCUMENT_INDEX_SCHEMA_VERSION = "structured-document-index/v1"
STRUCTURED_DOCUMENT_INDEX_MAX_BYTES = 2 * 1024 * 1024
STRUCTURED_DOCUMENT_INDEX_MAX_DOCUMENTS = 100
STRUCTURED_DOCUMENT_MAX_BYTES = 20 * 1024 * 1024
STRUCTURED_DOCUMENT_CASE_METADATA_MAX_BYTES = 2 * 1024 * 1024


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


def _case_source(case_dir: Path, source_id: str) -> StructuredDocumentSource:
    try:
        case_dir = resolve_safe_case_path(case_dir)
        work_dir = _managed_directory(case_dir / "work")
        state_path = resolve_safe_source(case_dir / "state.json", case_dir)
        manifest_path = resolve_safe_source(
            work_dir / "input_manifest.json", work_dir
        )
        state = json.loads(
            _bounded_bytes(
                state_path,
                STRUCTURED_DOCUMENT_CASE_METADATA_MAX_BYTES,
                "source_mismatch",
            ).decode("utf-8")
        )
        manifest = json.loads(
            _bounded_bytes(
                manifest_path,
                STRUCTURED_DOCUMENT_CASE_METADATA_MAX_BYTES,
                "source_mismatch",
            ).decode("utf-8")
        )
        sources = manifest["sources"]
        matches = [
            item for item in sources
            if isinstance(item, Mapping) and item.get("source_id") == source_id
        ]
        period = state["review_period"]
        if len(matches) != 1 or not isinstance(period, Mapping):
            raise ValueError
        item = matches[0]
        filename = item["filename"]
        digest = item["sha256"]
        entity_name = state["entity_name"]
        period_start = period["start"]
        period_end = period["end"]
        if (
            not isinstance(filename, str)
            or not filename
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or item.get("status") not in {"accepted", "present_unparsed"}
            or not all(
                isinstance(value, str) and value
                for value in (entity_name, period_start, period_end)
            )
        ):
            raise ValueError
    except StructuredDocumentImportError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise _fail("source_mismatch") from None
    path = case_dir / "input" / filename
    return StructuredDocumentSource(
        source_id=source_id,
        path=path,
        media_type=path.suffix.casefold(),
        input_root=case_dir / "input",
        sha256=digest,
        entity_name=entity_name,
        period_start=period_start,
        period_end=period_end,
    )


def _managed_metadata(path: Path, *, directory: bool) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError:
        raise _fail("unsafe_structured_document_path") from None
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        stat.S_ISLNK(metadata.st_mode)
        or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
        or not expected(metadata.st_mode)
    ):
        raise _fail("unsafe_structured_document_path")
    return metadata


def _managed_directory(path: Path, *, create: bool = False) -> Path:
    if create:
        try:
            path.mkdir(exist_ok=True)
        except OSError:
            raise _fail("unsafe_structured_document_path") from None
    _managed_metadata(path, directory=True)
    return path


def _managed_file_or_missing(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise _fail("unsafe_structured_document_path") from None
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
        or not stat.S_ISREG(metadata.st_mode)
    ):
        raise _fail("unsafe_structured_document_path")


def _atomic_bytes(path: Path, content: bytes) -> None:
    parent_before = _managed_metadata(path.parent, directory=True)
    _managed_file_or_missing(path)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        parent_after = _managed_metadata(path.parent, directory=True)
        if (parent_before.st_dev, parent_before.st_ino) != (
            parent_after.st_dev,
            parent_after.st_ino,
        ):
            raise _fail("unsafe_structured_document_path")
        _managed_file_or_missing(path)
        os.replace(temporary, path)
        temporary = None
        _managed_metadata(path, directory=False)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _bounded_bytes(path: Path, max_bytes: int, too_large_code: str) -> bytes:
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
                raise _fail(too_large_code)
            content = handle.read(max_bytes + 1)
            after = os.fstat(handle.fileno())
    except StructuredDocumentImportError:
        raise
    except OSError:
        raise _fail("unsafe_structured_document_path") from None
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if (
        identity_before != identity_after
        or len(content) != before.st_size
        or len(content) > max_bytes
    ):
        raise _fail(
            too_large_code if len(content) > max_bytes else "unsafe_structured_document_path"
        )
    return content


def _read_index(path: Path, work_dir: Path) -> dict[str, object]:
    try:
        safe_path = resolve_safe_source(path, work_dir)
    except ValueError:
        try:
            path.lstat()
        except FileNotFoundError:
            return {
                "schema_version": STRUCTURED_DOCUMENT_INDEX_SCHEMA_VERSION,
                "documents": [],
            }
        except OSError:
            raise _fail("unsafe_structured_document_path") from None
        raise _fail("unsafe_structured_document_path") from None
    try:
        raw = _bounded_bytes(
            safe_path,
            STRUCTURED_DOCUMENT_INDEX_MAX_BYTES,
            "structured_document_index_limit_exceeded",
        )
        payload = json.loads(raw.decode("utf-8"))
        documents = payload["documents"]
        if len(documents) > STRUCTURED_DOCUMENT_INDEX_MAX_DOCUMENTS:
            raise _fail("structured_document_index_limit_exceeded")
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != STRUCTURED_DOCUMENT_INDEX_SCHEMA_VERSION
            or not isinstance(documents, list)
            or any(not isinstance(item, dict) for item in documents)
        ):
            raise ValueError
        return payload
    except StructuredDocumentImportError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise _fail("structured_document_index_invalid") from None


def _persist_document(
    case_dir: Path,
    source: StructuredDocumentSource,
    *,
    kind: str,
    document_id: str,
    payload: Mapping[str, object],
) -> Path:
    canonical = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if len(canonical) > STRUCTURED_DOCUMENT_MAX_BYTES:
        raise _fail("structured_document_limit_exceeded")
    document_sha256 = hashlib.sha256(canonical).hexdigest()
    name_digest = hashlib.sha256(f"{kind}:{document_id}".encode("utf-8")).hexdigest()[:16]
    suffix = "fs.json" if kind == "financial_statement" else "tr.json"
    relative_path = f"{name_digest}.{suffix}"
    try:
        case_dir = resolve_safe_case_path(case_dir)
    except ValueError:
        raise _fail("unsafe_structured_document_path") from None
    work_dir = _managed_directory(case_dir / "work")
    directory = _managed_directory(work_dir / "structured-documents", create=True)
    path = directory / relative_path
    index_path = work_dir / STRUCTURED_DOCUMENT_INDEX_NAME
    index = _read_index(index_path, work_dir)
    documents = [
        item
        for item in index["documents"]
        if not (
            item.get("kind") == kind and item.get("document_id") == document_id
        )
    ]
    documents.append(
        {
            "kind": kind,
            "document_id": document_id,
            "relative_path": relative_path,
            "source_id": source.source_id,
            "source_sha256": source.sha256,
            "document_sha256": document_sha256,
        }
    )
    if len(documents) > STRUCTURED_DOCUMENT_INDEX_MAX_DOCUMENTS:
        raise _fail("structured_document_index_limit_exceeded")
    index["documents"] = sorted(
        documents, key=lambda item: (str(item.get("kind")), str(item.get("document_id")))
    )
    index_bytes = (
        json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    if len(index_bytes) > STRUCTURED_DOCUMENT_INDEX_MAX_BYTES:
        raise _fail("structured_document_index_limit_exceeded")
    _atomic_bytes(path, canonical)
    _atomic_bytes(index_path, index_bytes)
    return path


def persist_financial_statement(
    case_dir: Path, source_id: str, payload: Mapping[str, object]
) -> Path:
    """Import through the case manifest boundary and persist a verifiable receipt."""

    source = _case_source(Path(case_dir), source_id)
    value = import_financial_statement(source, payload)
    return _persist_document(
        Path(case_dir),
        source,
        kind="financial_statement",
        document_id=value.statement_id,
        payload=financial_statement_to_dict(value),
    )


def persist_tax_return(
    case_dir: Path, source_id: str, payload: Mapping[str, object]
) -> Path:
    """Import through the case manifest boundary and persist a verifiable receipt."""

    source = _case_source(Path(case_dir), source_id)
    value = import_tax_return(source, payload)
    return _persist_document(
        Path(case_dir),
        source,
        kind="tax_return",
        document_id=value.return_id,
        payload=tax_return_to_dict(value),
    )
