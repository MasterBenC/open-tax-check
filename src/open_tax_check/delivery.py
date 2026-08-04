"""Unified, revisioned delivery publication for self-service cases."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import re

from .case import resolve_safe_case_path
from .case_orchestration import (
    HANDOFF_NAME,
    build_case_customer_handoff,
    case_status_payload,
    customer_handoff_payload,
    load_case_snapshot,
)
from .evidence import verify_evidence_file
from .evidence_bundle import (
    BundleArtifact,
    VerifiedEvidenceItem,
    VerifiedEvidenceSet,
    build_evidence_bundle,
)
from .source_paths import resolve_safe_source
from .html_report import write_report_html
from .pdf_report import PdfExportUnavailable, write_report_pdf
from .report_view import REPORT_SCHEMA_VERSION_V3, build_report_view_v3
from .tax_reporting import tax_snapshot_payload, write_tax_health_snapshot
from .tax_models import TaxCheckSnapshot, TaxStatus
from .workpaper import WORKPAPER_NAME, write_tax_health_workpaper


DELIVERY_SCHEMA_VERSION = "tax-delivery/v1"
REPORT_SCHEMA_VERSION = REPORT_SCHEMA_VERSION_V3
_HISTORICAL_REPORT_SCHEMA_VERSION = "tax-health-report/v2"
DELIVERY_POINTER_NAME = "delivery.json"
DELIVERY_DIR_NAME = "deliveries"
_CASE_AUDIT_JSON_LIMIT = 2 * 1024 * 1024
_CASE_AUDIT_DOCUMENT_LIMIT = 100
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DeliveryError(ValueError):
    """Stable delivery publication error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_digest(snapshot: TaxCheckSnapshot) -> str:
    return hashlib.sha256(
        json.dumps(
            tax_snapshot_payload(snapshot),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _is_reparse(metadata: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & flag)


def _managed_directory(
    root: Path,
    parts: tuple[str, ...],
    *,
    create: bool,
) -> Path:
    current = root
    for part in parts:
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            if not create:
                raise DeliveryError("delivery_pointer_invalid") from None
            try:
                current.mkdir()
                metadata = os.lstat(current)
            except OSError:
                raise DeliveryError("delivery_pointer_invalid") from None
        except OSError:
            raise DeliveryError("delivery_pointer_invalid") from None
        if (
            stat.S_ISLNK(metadata.st_mode)
            or _is_reparse(metadata)
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            raise DeliveryError("delivery_pointer_invalid")
    return current


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        raise DeliveryError("delivery_pointer_write_failed") from None
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _case_root(case_dir: Path) -> Path:
    root = resolve_safe_case_path(Path(case_dir))
    if not (root / "state.json").is_file():
        raise DeliveryError("case_state_missing")
    return root


def _read_manifest(root: Path) -> dict[str, object] | None:
    path = root / "work" / "input_manifest.json"
    if not os.path.lexists(path):
        return None
    try:
        safe_path = resolve_safe_source(path, path.parent)
        payload = json.loads(safe_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise DeliveryError("delivery_source_manifest_invalid") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise DeliveryError("delivery_source_manifest_invalid")
    return payload


def _required_file_refs(snapshot: TaxCheckSnapshot) -> tuple[str, ...]:
    refs: set[str] = set()
    for fact in snapshot.request.facts:
        if fact.state.value == "confirmed":
            refs.update(fact.evidence_refs)
    for result in snapshot.results:
        if result.calculation is not None:
            refs.update(result.calculation.evidence_refs)
    return tuple(sorted(ref for ref in refs if not ref.startswith("customer-confirmation:")))


def _verified_evidence(root: Path, snapshot: TaxCheckSnapshot, manifest: dict[str, object] | None) -> VerifiedEvidenceSet:
    refs = _required_file_refs(snapshot)
    if not refs:
        return VerifiedEvidenceSet((), 0)
    if manifest is None:
        raise DeliveryError("delivery_evidence_unverified")
    entries = [item for item in manifest["sources"] if isinstance(item, dict)]
    verified: list[VerifiedEvidenceItem] = []
    total = 0
    for reference in refs:
        matches = [item for item in entries if item.get("source_id") == reference]
        if len(matches) != 1:
            raise DeliveryError("delivery_evidence_unverified")
        item = matches[0]
        filename = item.get("filename")
        expected_sha256 = item.get("sha256")
        media_type = item.get("media_type") or item.get("extension") or "application/octet-stream"
        if item.get("status") not in {"accepted", "present_unparsed"}:
            raise DeliveryError("delivery_evidence_unverified")
        try:
            result = verify_evidence_file(root / "input", filename, expected_sha256)
        except (OSError, TypeError, ValueError):
            raise DeliveryError("delivery_evidence_unverified") from None
        if result.status != "verified" or result.verified_path is None:
            raise DeliveryError("delivery_evidence_unverified")
        size = result.size_bytes or 0
        verified.append(VerifiedEvidenceItem(
            source_id=reference,
            relative_path=Path(filename).as_posix(),
            sha256=result.sha256 or expected_sha256,
            media_type=str(media_type),
            size_bytes=size,
            source_path=result.verified_path,
        ))
        total += size
    return VerifiedEvidenceSet(tuple(verified), total)


def _artifact_record(kind: str, path: Path, root: Path, snapshot_revision: int) -> dict[str, object]:
    return {
        "kind": kind,
        "relative_path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "snapshot_revision": snapshot_revision,
        "report_schema_version": REPORT_SCHEMA_VERSION,
    }


def _case_audit_json(root: Path, path: Path) -> tuple[Path, object] | None:
    if not os.path.lexists(path):
        return None
    try:
        safe_path = resolve_safe_source(path, root)
        if safe_path.stat().st_size > _CASE_AUDIT_JSON_LIMIT:
            raise ValueError("case_audit_too_large")
        payload = json.loads(safe_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise DeliveryError("delivery_case_audit_invalid") from None
    return safe_path, payload


def _case_audit_artifacts(
    root: Path,
) -> tuple[tuple[BundleArtifact, ...], dict[str, object]]:
    artifacts: list[BundleArtifact] = []
    payloads: dict[str, object] = {}
    known = (
        ("reconciliation", root / "work/reconciliation.json", "case-audit/reconciliation.json"),
        (
            "structured_reconciliation",
            root / "work/structured-reconciliation.json",
            "case-audit/structured-reconciliation.json",
        ),
        ("coverage_matrix", root / "work/coverage_matrix.json", "case-audit/coverage-matrix.json"),
        (
            "mapping_review",
            root / "reviews/mapping_review.json",
            "case-audit/reviews/mapping-review.json",
        ),
        (
            "structured_document_index",
            root / "work/structured-document-index.json",
            "case-audit/structured-document-index.json",
        ),
        ("findings", root / "output/findings.json", "case-audit/findings.json"),
    )
    for key, path, archive_path in known:
        loaded = _case_audit_json(root, path)
        if loaded is None:
            continue
        safe_path, payload = loaded
        payloads[key] = payload
        artifacts.append(BundleArtifact("case_audit", safe_path, archive_path))

    structured_root = root / "work/structured-documents"
    if os.path.lexists(structured_root):
        directory = _managed_directory(
            root,
            ("work", "structured-documents"),
            create=False,
        )
        entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        json_entries = [entry for entry in entries if entry.suffix.casefold() == ".json"]
        if len(json_entries) > _CASE_AUDIT_DOCUMENT_LIMIT:
            raise DeliveryError("delivery_case_audit_invalid")
        for entry in json_entries:
            loaded = _case_audit_json(root, entry)
            if loaded is None:
                continue
            safe_path, _ = loaded
            artifacts.append(BundleArtifact(
                "structured_document",
                safe_path,
                f"case-audit/structured-documents/{entry.name}",
            ))
    return tuple(artifacts), payloads


def _case_audit_digest(artifacts: tuple[BundleArtifact, ...]) -> str:
    payload = [
        {"archive_path": item.archive_path, "sha256": _sha256(item.path)}
        for item in sorted(artifacts, key=lambda artifact: artifact.archive_path)
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _report_context(
    root: Path,
    case_audit_payloads: dict[str, object],
) -> tuple[str, tuple[dict[str, object], ...]]:
    try:
        state_path = resolve_safe_source(root / "state.json", root)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        entity_name = state.get("entity_name") if isinstance(state, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise DeliveryError("case_state_invalid") from None
    if not isinstance(entity_name, str) or not entity_name.strip():
        raise DeliveryError("case_state_invalid")
    findings = case_audit_payloads.get("findings", [])
    if not isinstance(findings, list) or not all(isinstance(item, dict) for item in findings):
        raise DeliveryError("delivery_case_audit_invalid")
    return entity_name.strip(), tuple(findings)


def _material_statuses(
    manifest: dict[str, object] | None,
    case_audit_payloads: dict[str, object],
) -> tuple[dict[str, object], ...]:
    """Translate intake state into the report's separate material-status axis."""

    if manifest is None:
        return ()
    structured_index = case_audit_payloads.get("structured_document_index", {})
    documents = (
        structured_index.get("documents", [])
        if isinstance(structured_index, dict)
        else []
    )
    structured_source_ids = {
        item.get("source_id")
        for item in documents
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }
    reconciliation = case_audit_payloads.get("structured_reconciliation", {})
    reconciliation_passed = (
        isinstance(reconciliation, dict) and reconciliation.get("passed") is True
    )
    status_map = {
        "present_unparsed": "received_pending",
        "accepted": "imported_pending_confirmation",
        "processing_error": "extraction_partial",
        "rejected": "contract_invalid",
    }
    statuses: list[dict[str, object]] = []
    for source in manifest.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            continue
        status = status_map.get(source.get("status"), "received_pending")
        if source_id in structured_source_ids:
            status = (
                "imported_reconciled"
                if reconciliation_passed
                else "imported_pending_confirmation"
            )
        display_name = source.get("filename")
        statuses.append({
            "source_id": source_id,
            "display_name_zh": (
                display_name if isinstance(display_name, str) and display_name else source_id
            ),
            "status": status,
            "tax_ids": (),
        })
    return tuple(statuses)


def _manifest_for_directory(path: Path) -> dict[str, object]:
    try:
        manifest_path = resolve_safe_source(path / "delivery-manifest.json", path)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise DeliveryError("delivery_manifest_invalid") from None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != DELIVERY_SCHEMA_VERSION
        or payload.get("report_schema_version") not in {
            _HISTORICAL_REPORT_SCHEMA_VERSION,
            REPORT_SCHEMA_VERSION,
        }
    ):
        raise DeliveryError("delivery_manifest_invalid")
    return payload


def _verify_delivery_artifacts(
    directory: Path, manifest: dict[str, object]
) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise DeliveryError("delivery_artifact_invalid")
    seen: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict):
            raise DeliveryError("delivery_artifact_invalid")
        relative_path = item.get("relative_path")
        size_bytes = item.get("size_bytes")
        expected_sha256 = item.get("sha256")
        snapshot_revision = item.get("snapshot_revision")
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or "\\" in relative_path
            or relative_path in seen
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 0
            or not isinstance(expected_sha256, str)
            or _SHA256_RE.fullmatch(expected_sha256) is None
            or snapshot_revision != manifest.get("snapshot_revision")
            or item.get("report_schema_version") != manifest.get("report_schema_version")
        ):
            raise DeliveryError("delivery_artifact_invalid")
        relative = Path(relative_path)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise DeliveryError("delivery_artifact_invalid")
        seen.add(relative_path)
        try:
            path = resolve_safe_source(directory / relative, directory)
            if path.stat().st_size != size_bytes or _sha256(path) != expected_sha256:
                raise DeliveryError("delivery_artifact_invalid")
        except DeliveryError:
            raise
        except (OSError, ValueError):
                raise DeliveryError("delivery_artifact_invalid") from None


def verified_delivery_artifacts(case_dir: Path) -> tuple[dict[str, object], ...]:
    """Return only existing artifacts whose active delivery hashes still match."""

    root = _case_root(case_dir)
    pointer_path = root / "work" / DELIVERY_POINTER_NAME
    if not pointer_path.is_file():
        return ()
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        manifest_path = resolve_safe_source(root / pointer["manifest_path"], root / "work")
        manifest = _manifest_for_directory(manifest_path.parent)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise DeliveryError("delivery_pointer_invalid") from None
    _verify_delivery_artifacts(manifest_path.parent, manifest)
    if pointer != _pointer_payload(root, manifest, manifest_path.parent):
        raise DeliveryError("delivery_pointer_invalid")
    revision_path = manifest_path.parent.relative_to(root)
    report_schema = manifest["report_schema_version"]
    if report_schema == _HISTORICAL_REPORT_SCHEMA_VERSION:
        return ()
    excluded = {"customer_handoff", "evidence_bundle"}
    return tuple(
        {
            "kind": item["kind"],
            "path": (revision_path / item["relative_path"]).as_posix(),
            "sha256": item["sha256"],
            "size_bytes": item["size_bytes"],
        }
        for item in manifest["artifacts"]
        if item["kind"] not in excluded
        and item["kind"] == "pdf_report"
    )


def _delivery_options(*, include_evidence_files: bool) -> dict[str, bool]:
    return {"include_evidence_files": bool(include_evidence_files)}


def _pointer_payload(root: Path, manifest: dict[str, object], revision_path: Path) -> dict[str, object]:
    return {
        "schema_version": DELIVERY_SCHEMA_VERSION,
        "report_schema_version": manifest["report_schema_version"],
        "revision": manifest["revision"],
        "status": manifest["status"],
        "manifest_path": (Path("work") / DELIVERY_DIR_NAME / revision_path.name / "delivery-manifest.json").as_posix(),
        "revision_path": (Path("work") / DELIVERY_DIR_NAME / revision_path.name).as_posix(),
        "artifacts": manifest["artifacts"],
        "pdf_status": manifest.get("pdf_status", "not_requested"),
        "delivery_options": manifest.get("delivery_options", {}),
    }


def _existing_delivery(
    root: Path,
    snapshot: TaxCheckSnapshot,
    options: dict[str, bool],
    case_audit_digest: str,
) -> dict[str, object] | None:
    pointer_path = root / "work" / DELIVERY_POINTER_NAME
    if not os.path.lexists(pointer_path):
        return None
    try:
        safe_pointer_path = resolve_safe_source(pointer_path, root / "work")
        pointer = json.loads(safe_pointer_path.read_text(encoding="utf-8"))
        if not isinstance(pointer, dict) or not isinstance(pointer.get("manifest_path"), str):
            raise ValueError("invalid_pointer")
        manifest_path = resolve_safe_source(
            root / Path(pointer["manifest_path"]),
            root / "work",
        )
        delivery_root = _managed_directory(
            root,
            ("work", DELIVERY_DIR_NAME),
            create=False,
        )
        if manifest_path.parent.parent != delivery_root:
            raise ValueError("invalid_pointer")
        manifest = _manifest_for_directory(manifest_path.parent)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise DeliveryError("delivery_pointer_invalid") from None
    _verify_delivery_artifacts(manifest_path.parent, manifest)
    expected_pointer = _pointer_payload(root, manifest, manifest_path.parent)
    if pointer != expected_pointer:
        raise DeliveryError("delivery_pointer_invalid")
    if manifest.get("report_schema_version") == _HISTORICAL_REPORT_SCHEMA_VERSION:
        return None
    manifest_revision = manifest.get("revision")
    if not isinstance(manifest_revision, int) or isinstance(manifest_revision, bool):
        raise DeliveryError("delivery_pointer_invalid")
    if manifest_revision < snapshot.revision:
        return None
    if manifest_revision != snapshot.revision:
        raise DeliveryError("delivery_revision_conflict")
    if manifest.get("snapshot_digest") != _snapshot_digest(snapshot):
        raise DeliveryError("delivery_revision_conflict")
    if manifest.get("delivery_options") != options:
        raise DeliveryError("delivery_revision_conflict")
    if manifest.get("case_audit_digest") != case_audit_digest:
        raise DeliveryError("delivery_revision_conflict")
    return pointer


def build_case_delivery(
    case_dir: Path,
    *,
    include_evidence_files: bool = False,
) -> dict[str, object]:
    """Publish all delivery artifacts from one immutable case snapshot."""
    root = _case_root(case_dir)
    snapshot = load_case_snapshot(root)
    if any(result.status is TaxStatus.BLOCKED for result in snapshot.results):
        raise DeliveryError("delivery_blocked_data_quality")
    options = _delivery_options(include_evidence_files=include_evidence_files)
    case_audit_artifacts, case_audit_payloads = _case_audit_artifacts(root)
    case_audit_digest = _case_audit_digest(case_audit_artifacts)
    entity_name, reviewed_findings = _report_context(root, case_audit_payloads)
    existing = _existing_delivery(root, snapshot, options, case_audit_digest)
    if existing is not None:
        build_case_customer_handoff(root)
        return existing

    work_root = _managed_directory(root, ("work",), create=False)
    delivery_root = _managed_directory(
        root,
        ("work", DELIVERY_DIR_NAME),
        create=True,
    )
    final_directory = delivery_root / f"revision-{snapshot.revision:04d}-report-v3"
    if os.path.lexists(final_directory):
        try:
            metadata = os.lstat(final_directory)
        except OSError:
            raise DeliveryError("delivery_pointer_invalid") from None
        if (
            stat.S_ISLNK(metadata.st_mode)
            or _is_reparse(metadata)
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            raise DeliveryError("delivery_pointer_invalid")
        manifest = _manifest_for_directory(final_directory)
        _verify_delivery_artifacts(final_directory, manifest)
        if (
            manifest.get("revision") != snapshot.revision
            or manifest.get("snapshot_digest") != _snapshot_digest(snapshot)
            or manifest.get("delivery_options") != options
            or manifest.get("case_audit_digest") != case_audit_digest
        ):
            raise DeliveryError("delivery_revision_conflict")
        pointer = _pointer_payload(root, manifest, final_directory)
        _atomic_json(work_root / DELIVERY_POINTER_NAME, pointer)
        build_case_customer_handoff(root)
        return pointer

    temporary_directory = Path(tempfile.mkdtemp(prefix=".otcd-", dir=delivery_root))
    manifest: dict[str, object] | None = None
    published_directory = False
    try:
        manifest_payload = _read_manifest(root)
        html_path = temporary_directory / "report.html"
        pdf_path = temporary_directory / "税务健康检查报告.pdf"
        workpaper_path = temporary_directory / WORKPAPER_NAME
        snapshot_path = temporary_directory / "tax-health-snapshot.json"
        material_statuses = _material_statuses(manifest_payload, case_audit_payloads)
        report_view = build_report_view_v3(
            snapshot,
            entity_name=entity_name,
            reviewed_findings=reviewed_findings,
            material_statuses=material_statuses,
        )
        write_report_html(report_view, html_path)
        write_report_pdf(report_view, pdf_path)
        write_tax_health_workpaper(
            snapshot,
            workpaper_path,
            source_manifest=manifest_payload,
            delivery_revision=snapshot.revision,
            case_audit=case_audit_payloads,
        )
        write_tax_health_snapshot(snapshot, snapshot_path)

        artifacts = [
            BundleArtifact("html_report", html_path, "report.html"),
            BundleArtifact("pdf_report", pdf_path, "税务健康检查报告.pdf"),
            BundleArtifact("workpaper", workpaper_path, "workpaper.xlsx"),
            BundleArtifact("machine_snapshot", snapshot_path, "tax-health-snapshot.json"),
            *case_audit_artifacts,
        ]
        pre_handoff_records = [
            _artifact_record(kind, path, temporary_directory, snapshot.revision)
            for kind, path in (("pdf_report", pdf_path),)
        ]
        revision_prefix = Path("work") / DELIVERY_DIR_NAME / final_directory.name
        handoff_artifacts = tuple(
            {
                "kind": item["kind"],
                "path": (revision_prefix / item["relative_path"]).as_posix(),
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            }
            for item in pre_handoff_records
        )
        handoff_path = temporary_directory / HANDOFF_NAME
        _atomic_json(
            handoff_path,
            customer_handoff_payload(
                snapshot,
                case_status_payload(root),
                handoff_artifacts,
            ),
        )
        artifacts.append(BundleArtifact("customer_handoff", handoff_path, HANDOFF_NAME))
        verified = _verified_evidence(root, snapshot, manifest_payload)
        bundle_path = temporary_directory / "evidence-bundle.zip"
        bundle = build_evidence_bundle(
            snapshot,
            verified,
            tuple(artifacts),
            bundle_path,
            include_evidence_files=include_evidence_files,
        )

        snapshot_digest = _snapshot_digest(snapshot)
        artifact_records = [
            _artifact_record(kind, path, temporary_directory, snapshot.revision)
            for kind, path in (
                ("html_report", html_path),
                ("pdf_report", pdf_path),
                ("workpaper", workpaper_path),
                ("machine_snapshot", snapshot_path),
                ("customer_handoff", handoff_path),
                ("evidence_bundle", bundle.path),
            )
        ]
        manifest = {
            "schema_version": DELIVERY_SCHEMA_VERSION,
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "revision": snapshot.revision,
            "snapshot_revision": snapshot.revision,
            "snapshot_digest": snapshot_digest,
            "case_audit_digest": case_audit_digest,
            "delivery_options": options,
            "status": "verified",
            "pdf_status": "verified",
            "artifacts": artifact_records,
            "evidence_bundle": {
                "relative_path": bundle.path.relative_to(temporary_directory).as_posix(),
                "sha256": bundle.sha256,
                "includes_evidence_files": bundle.includes_evidence_files,
            },
        }
        _atomic_json(temporary_directory / "delivery-manifest.json", manifest)
        os.replace(temporary_directory, final_directory)
        published_directory = True
        temporary_directory = None  # type: ignore[assignment]
        pointer = _pointer_payload(root, manifest, final_directory)
        _atomic_json(work_root / DELIVERY_POINTER_NAME, pointer)
        build_case_customer_handoff(root)
        return pointer
    except PdfExportUnavailable as error:
        raise DeliveryError(str(error)) from None
    except DeliveryError:
        if published_directory and final_directory.exists():
            try:
                shutil.rmtree(final_directory)
            except OSError:
                pass
        raise
    except Exception:
        if published_directory and final_directory.exists():
            try:
                shutil.rmtree(final_directory)
            except OSError:
                pass
        raise DeliveryError("delivery_write_failed") from None
    finally:
        if temporary_directory is not None and temporary_directory.exists():
            try:
                shutil.rmtree(temporary_directory)
            except OSError:
                pass


__all__ = [
    "DELIVERY_POINTER_NAME",
    "DELIVERY_SCHEMA_VERSION",
    "DeliveryError",
    "build_case_delivery",
]
