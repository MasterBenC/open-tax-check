from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

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
    }
    required.update(
        reference
        for result in snapshot.results
        if result.calculation is not None
        for reference in result.calculation.evidence_refs
    )
    if not required.issubset(verified_ids):
        raise EvidenceBundleError("unverified_evidence_reference")
