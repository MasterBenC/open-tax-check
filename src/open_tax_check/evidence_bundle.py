from __future__ import annotations

from dataclasses import dataclass
import re


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
