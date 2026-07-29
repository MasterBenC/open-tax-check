"""Bounded, source-bound evidence file verification for host integrations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re


_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class EvidenceVerification:
    source_id: str
    status: str
    sha256: str | None = None
    size_bytes: int | None = None
    error_code: str | None = None


def _is_reparse_or_link(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def verify_evidence_file(
    root: Path,
    relative_path: str,
    expected_sha256: str,
    *,
    max_file_bytes: int = 50 * 1024 * 1024,
) -> EvidenceVerification:
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        raise ValueError("invalid_evidence_hash")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe_evidence_path")
    root = Path(root).absolute()
    candidate = root / relative
    try:
        current = candidate
        while current != root:
            if _is_reparse_or_link(current):
                raise ValueError("unsafe_evidence_path")
            current = current.parent
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        if resolved_root not in resolved.parents or not resolved.is_file():
            raise ValueError("unsafe_evidence_path")
        size = resolved.stat().st_size
        if size > max_file_bytes:
            return EvidenceVerification(relative.as_posix(), "failed", error_code="evidence_size_limit_exceeded")
        digest = hashlib.sha256()
        with resolved.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
    except ValueError:
        raise
    except OSError:
        return EvidenceVerification(relative.as_posix(), "failed", error_code="evidence_unreadable")
    if actual != expected_sha256:
        return EvidenceVerification(relative.as_posix(), "failed", error_code="evidence_hash_mismatch")
    return EvidenceVerification(relative.as_posix(), "verified", sha256=actual, size_bytes=size)
