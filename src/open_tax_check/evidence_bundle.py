from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import tempfile
from typing import BinaryIO
import unicodedata
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .evidence import verify_evidence_file
from .tax_models import FactState, TaxCheckSnapshot


MANIFEST_SCHEMA_VERSION = "tax-evidence-manifest/v1"
BUNDLE_SCHEMA_VERSION = "tax-evidence-bundle/v1"
MAX_EVIDENCE_ITEMS = 500
MAX_EVIDENCE_FILE_BYTES = 50 * 1024 * 1024
MAX_EVIDENCE_TOTAL_BYTES = 512 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MANIFEST_KEYS = {"schema_version", "items"}
_ITEM_KEYS = {"source_id", "relative_path", "sha256", "media_type"}
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_FIXED_DISCLAIMER_ZH = (
    "本证据包仅用于客户内部税务健康检查资料链路复核；"
    "文件哈希验证不等于税务事实真实、申报正确或不存在风险；"
    "本证据包不属于涉税鉴证、专业税务意见或纳税申报结论。"
)
_WINDOWS_FORBIDDEN_ARCHIVE_CHARACTERS = frozenset('<>:"\\|?*')
_WINDOWS_RESERVED_ARCHIVE_NAMES = frozenset({
    "aux",
    "clock$",
    "con",
    "conin$",
    "conout$",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
    *(f"com{index}" for index in "¹²³"),
    *(f"lpt{index}" for index in "¹²³"),
})


class EvidenceBundleError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EvidenceManifestItem:
    source_id: str
    relative_path: str
    sha256: str
    media_type: str


@dataclass(frozen=True, slots=True)
class EvidenceManifest:
    schema_version: str
    items: tuple[EvidenceManifestItem, ...]


@dataclass(frozen=True, slots=True)
class VerifiedEvidenceItem:
    source_id: str
    relative_path: str
    sha256: str
    media_type: str
    size_bytes: int
    source_path: Path


@dataclass(frozen=True, slots=True)
class VerifiedEvidenceSet:
    items: tuple[VerifiedEvidenceItem, ...]
    total_size_bytes: int


@dataclass(frozen=True, slots=True)
class BundleArtifact:
    kind: str
    path: Path
    archive_path: str


@dataclass(frozen=True, slots=True)
class EvidenceBundleResult:
    path: Path
    sha256: str
    includes_evidence_files: bool


@dataclass(frozen=True, slots=True)
class _StagedMember:
    archive_path: str
    path: Path
    sha256: str
    size_bytes: int


def parse_evidence_manifest(payload: object) -> EvidenceManifest:
    if not isinstance(payload, dict) or set(payload) != _MANIFEST_KEYS:
        raise EvidenceBundleError("invalid_evidence_manifest")
    if payload["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise EvidenceBundleError("invalid_evidence_manifest")
    raw_items = payload["items"]
    if not isinstance(raw_items, list) or len(raw_items) > MAX_EVIDENCE_ITEMS:
        raise EvidenceBundleError("invalid_evidence_manifest")
    items: list[EvidenceManifestItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict) or set(raw) != _ITEM_KEYS:
            raise EvidenceBundleError("invalid_evidence_manifest")
        source_id = raw["source_id"]
        relative_path = raw["relative_path"]
        sha256 = raw["sha256"]
        media_type = raw["media_type"]
        if (
            not isinstance(source_id, str)
            or not source_id.strip()
            or not isinstance(relative_path, str)
            or not relative_path.strip()
            or not isinstance(sha256, str)
            or _SHA256.fullmatch(sha256) is None
            or not isinstance(media_type, str)
            or not media_type.strip()
        ):
            raise EvidenceBundleError("invalid_evidence_manifest")
        items.append(EvidenceManifestItem(source_id, relative_path, sha256, media_type))
    if len({item.source_id for item in items}) != len(items):
        raise EvidenceBundleError("invalid_evidence_manifest")
    normalized_paths = {
        item.relative_path.replace("\\", "/").casefold() for item in items
    }
    if len(normalized_paths) != len(items):
        raise EvidenceBundleError("invalid_evidence_manifest")
    return EvidenceManifest(MANIFEST_SCHEMA_VERSION, tuple(items))


def verify_evidence_manifest(
    manifest: EvidenceManifest,
    evidence_root: Path,
) -> VerifiedEvidenceSet:
    verified_items: list[VerifiedEvidenceItem] = []
    total = 0
    for item in manifest.items:
        try:
            result = verify_evidence_file(
                evidence_root,
                item.relative_path,
                item.sha256,
                max_file_bytes=MAX_EVIDENCE_FILE_BYTES,
            )
        except ValueError as error:
            if str(error) == "unsafe_evidence_path":
                raise EvidenceBundleError("unsafe_evidence_path") from None
            raise EvidenceBundleError("invalid_evidence_manifest") from None
        if (
            result.status != "verified"
            or result.sha256 is None
            or result.size_bytes is None
            or result.verified_path is None
        ):
            raise EvidenceBundleError("evidence_verification_failed")
        total += result.size_bytes
        if total > MAX_EVIDENCE_TOTAL_BYTES:
            raise EvidenceBundleError("evidence_bundle_size_limit_exceeded")
        verified_items.append(VerifiedEvidenceItem(
            source_id=item.source_id,
            relative_path=item.relative_path,
            sha256=result.sha256,
            media_type=item.media_type,
            size_bytes=result.size_bytes,
            source_path=result.verified_path,
        ))
    return VerifiedEvidenceSet(tuple(verified_items), total)


def validate_evidence_bindings(
    snapshot: TaxCheckSnapshot,
    verified: VerifiedEvidenceSet,
) -> None:
    verified_ids = {item.source_id for item in verified.items}
    required = {
        reference
        for fact in snapshot.request.facts
        if fact.state is FactState.CONFIRMED
        for reference in fact.evidence_refs
        if not reference.startswith("customer-confirmation:")
    }
    required.update(
        reference
        for result in snapshot.results
        if result.calculation is not None
        for reference in result.calculation.evidence_refs
        if not reference.startswith("customer-confirmation:")
    )
    if not required.issubset(verified_ids):
        raise EvidenceBundleError("unverified_evidence_reference")


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_evidence_path(index: int, item: VerifiedEvidenceItem) -> str:
    suffix = item.source_path.suffix.lower()
    safe_suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ".bin"
    return f"evidence/{index:04d}/original{safe_suffix}"


def _portable_archive_key(archive_path: str) -> str:
    if (
        not isinstance(archive_path, str)
        or not archive_path
        or "\\" in archive_path
        or "\0" in archive_path
    ):
        raise ValueError("invalid_bundle_archive_path")
    raw_parts = archive_path.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("invalid_bundle_archive_path")
    path = PurePosixPath(archive_path)
    if path.is_absolute() or path.as_posix() != archive_path:
        raise ValueError("invalid_bundle_archive_path")
    canonical_parts = []
    for part in raw_parts:
        if (
            part.endswith((".", " "))
            or any(
                ord(character) < 32
                or character in _WINDOWS_FORBIDDEN_ARCHIVE_CHARACTERS
                for character in part
            )
            or part.split(".", 1)[0].casefold()
            in _WINDOWS_RESERVED_ARCHIVE_NAMES
        ):
            raise ValueError("invalid_bundle_archive_path")
        canonical_parts.append(unicodedata.normalize("NFC", part).casefold())
    return "/".join(canonical_parts)


def _validate_archive_paths(archive_paths: list[str]) -> None:
    canonical_paths = [_portable_archive_key(path) for path in archive_paths]
    if len(set(canonical_paths)) != len(canonical_paths):
        raise ValueError("invalid_bundle_archive_path")


def _evidence_item_payloads(
    verified: VerifiedEvidenceSet,
    *,
    include_evidence_files: bool,
) -> tuple[
    list[dict[str, object]],
    list[tuple[str, Path, str, int]],
]:
    payloads: list[dict[str, object]] = []
    members: list[tuple[str, Path, str, int]] = []
    for index, item in enumerate(
        sorted(verified.items, key=lambda candidate: candidate.source_id),
        start=1,
    ):
        bundle_path = (
            _archive_evidence_path(index, item) if include_evidence_files else None
        )
        payloads.append({
            "source_id": item.source_id,
            "relative_path": item.relative_path,
            "bundle_path": bundle_path,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
            "media_type": item.media_type,
            "status": "verified",
        })
        if bundle_path is not None:
            members.append(
                (bundle_path, item.source_path, item.sha256, item.size_bytes),
            )
    return payloads, members


def _confirmed_fact_bindings(snapshot: TaxCheckSnapshot) -> list[dict[str, object]]:
    return [
        {
            "fact_key": fact.key,
            "evidence_refs": sorted(fact.evidence_refs),
        }
        for fact in sorted(snapshot.request.facts, key=lambda item: item.key)
        if fact.state is FactState.CONFIRMED
    ]


def _calculation_payloads(snapshot: TaxCheckSnapshot) -> list[dict[str, object]]:
    return [
        {
            "tax_id": result.tax_id,
            "formula_id": result.calculation.formula_id,
            "amount": result.calculation.amount,
            "evidence_refs": sorted(result.calculation.evidence_refs),
        }
        for result in sorted(snapshot.results, key=lambda item: item.tax_id)
        if result.calculation is not None
    ]


def _rule_pack_payloads(snapshot: TaxCheckSnapshot) -> list[dict[str, str]]:
    rule_packs = {
        (pack_id, rule_version)
        for result in snapshot.results
        for pack_id, rule_version in zip(
            result.rule_pack_ids,
            result.rule_pack_versions,
            strict=True,
        )
    }
    return [
        {"pack_id": pack_id, "rule_version": rule_version}
        for pack_id, rule_version in sorted(rule_packs)
    ]


def _rule_source_payloads(snapshot: TaxCheckSnapshot) -> list[dict[str, object]]:
    sources = []
    for result in snapshot.results:
        for source in result.rule_sources:
            if source not in sources:
                sources.append(source)
    ordered = sorted(
        sources,
        key=lambda source: (source.title, source.url, source.published_on),
    )
    return [
        {
            "title": source.title,
            "url": source.url,
            "issuer": source.issuer,
            "document_number": source.document_number,
            "article_refs": list(source.article_refs),
            "published_on": source.published_on.isoformat(),
            "verified_on": (
                source.verified_on.isoformat()
                if source.verified_on is not None
                else None
            ),
        }
        for source in ordered
    ]


def _audit_event_payloads(snapshot: TaxCheckSnapshot) -> list[dict[str, object]]:
    return [
        {
            "event_type": event.event_type,
            "tax_ids": list(event.tax_ids),
            "fact_keys": list(event.fact_keys),
            "confirmation_digest": event.confirmation_digest,
        }
        for event in snapshot.audit_events
    ]


def _stage_source(
    archive_path: str,
    source_path: Path,
    staged_path: Path,
) -> _StagedMember:
    digest = hashlib.sha256()
    size_bytes = 0
    with source_path.open("rb") as source, staged_path.open("xb") as target:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            target.write(chunk)
            digest.update(chunk)
            size_bytes += len(chunk)
        target.flush()
        os.fsync(target.fileno())
    return _StagedMember(
        archive_path=archive_path,
        path=staged_path,
        sha256=digest.hexdigest(),
        size_bytes=size_bytes,
    )


def _stage_members(
    members: list[tuple[str, Path]],
    staging_directory: Path,
) -> dict[str, _StagedMember]:
    staged = {}
    for index, (archive_path, source_path) in enumerate(
        sorted(members, key=lambda member: member[0]),
        start=1,
    ):
        staged[archive_path] = _stage_source(
            archive_path,
            source_path,
            staging_directory / f"{index:04d}.stage",
        )
    return staged


def _artifact_payloads(
    artifacts: tuple[BundleArtifact, ...],
    staged_members: dict[str, _StagedMember],
) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for artifact in sorted(artifacts, key=lambda item: item.archive_path):
        staged = staged_members[artifact.archive_path]
        payloads.append({
            "kind": artifact.kind,
            "archive_path": artifact.archive_path,
            "sha256": staged.sha256,
            "size_bytes": staged.size_bytes,
        })
    return payloads


def _manifest_payload(
    snapshot: TaxCheckSnapshot,
    verified: VerifiedEvidenceSet,
    artifacts: tuple[BundleArtifact, ...],
    staging_directory: Path,
    *,
    include_evidence_files: bool,
) -> tuple[dict[str, object], list[tuple[str, _StagedMember]]]:
    evidence_items, evidence_members = _evidence_item_payloads(
        verified,
        include_evidence_files=include_evidence_files,
    )
    artifact_members = [
        (artifact.archive_path, artifact.path)
        for artifact in artifacts
    ]
    archive_paths = [
        *(archive_path for archive_path, _ in artifact_members),
        *(archive_path for archive_path, _, _, _ in evidence_members),
        "bundle-manifest.json",
    ]
    _validate_archive_paths(archive_paths)
    staged_members = _stage_members(
        [
            *artifact_members,
            *(
                (archive_path, source_path)
                for archive_path, source_path, _, _ in evidence_members
            ),
        ],
        staging_directory,
    )
    for archive_path, _, expected_sha256, expected_size_bytes in evidence_members:
        staged = staged_members[archive_path]
        if (
            staged.sha256 != expected_sha256
            or staged.size_bytes != expected_size_bytes
        ):
            raise ValueError("verified_evidence_source_changed")
    artifact_payloads = _artifact_payloads(artifacts, staged_members)
    payload = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "generator_version": importlib.metadata.version("open-tax-check"),
        "period": {
            "start": snapshot.request.period.start.isoformat(),
            "end": snapshot.request.period.end.isoformat(),
        },
        "jurisdiction": snapshot.request.jurisdiction,
        "snapshot_schema_version": snapshot.schema_version,
        "snapshot_revision": snapshot.revision,
        "includes_evidence_files": include_evidence_files,
        "evidence_items": evidence_items,
        "confirmed_fact_bindings": _confirmed_fact_bindings(snapshot),
        "calculations": _calculation_payloads(snapshot),
        "rule_packs": _rule_pack_payloads(snapshot),
        "rule_sources": _rule_source_payloads(snapshot),
        "artifacts": artifact_payloads,
        "audit_events": _audit_event_payloads(snapshot),
        "disclaimer_zh": _FIXED_DISCLAIMER_ZH,
    }
    return payload, [
        (archive_path, staged)
        for archive_path, staged in staged_members.items()
    ]


def _zip_info(archive_path: str) -> ZipInfo:
    info = ZipInfo(archive_path, _ZIP_TIMESTAMP)
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _write_stream(
    archive: ZipFile,
    archive_path: str,
    source: BinaryIO,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with archive.open(_zip_info(archive_path), "w") as target:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            target.write(chunk)
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


def _write_zip_members(
    archive: ZipFile,
    snapshot: TaxCheckSnapshot,
    verified: VerifiedEvidenceSet,
    artifacts: tuple[BundleArtifact, ...],
    staging_directory: Path,
    *,
    include_evidence_files: bool,
) -> None:
    manifest, path_members = _manifest_payload(
        snapshot,
        verified,
        artifacts,
        staging_directory,
        include_evidence_files=include_evidence_files,
    )
    members: list[tuple[str, _StagedMember | bytes]] = [
        *path_members,
        ("bundle-manifest.json", _json_bytes(manifest)),
    ]
    for archive_path, source in sorted(members, key=lambda member: member[0]):
        if isinstance(source, bytes):
            archive.writestr(_zip_info(archive_path), source)
        else:
            with source.path.open("rb") as input_file:
                archived_sha256, archived_size_bytes = _write_stream(
                    archive,
                    archive_path,
                    input_file,
                )
            if (
                archived_sha256 != source.sha256
                or archived_size_bytes != source.size_bytes
            ):
                raise ValueError("staged_bundle_source_changed")


def build_evidence_bundle(
    snapshot: TaxCheckSnapshot,
    verified: VerifiedEvidenceSet,
    artifacts: tuple[BundleArtifact, ...],
    destination: Path,
    *,
    include_evidence_files: bool = False,
) -> EvidenceBundleResult:
    validate_evidence_bindings(snapshot, verified)
    destination = Path(destination)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        with tempfile.TemporaryDirectory(
            prefix=f".{destination.name}.stage.",
            dir=destination.parent,
        ) as staging_name:
            output = os.fdopen(descriptor, "w+b")
            descriptor = None
            with output:
                with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
                    _write_zip_members(
                        archive,
                        snapshot,
                        verified,
                        artifacts,
                        Path(staging_name),
                        include_evidence_files=include_evidence_files,
                    )
                output.flush()
                os.fsync(output.fileno())
        bundle_sha256 = _sha256_file(temporary)
        os.replace(temporary, destination)
    except Exception:
        raise EvidenceBundleError("evidence_bundle_write_failed") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return EvidenceBundleResult(
        path=destination,
        sha256=bundle_sha256,
        includes_evidence_files=include_evidence_files,
    )
