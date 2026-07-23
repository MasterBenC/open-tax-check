"""Versioned, immutable metadata records for the Excel intake boundary."""

from dataclasses import dataclass, fields, is_dataclass
from typing import Any


SCHEMA_VERSION = "0.2.0"


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """Metadata for one source file, without its contents."""

    source_id: str
    filename: str
    sha256: str
    size_bytes: int
    extension: str
    status: str
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_digest(
        cls,
        *,
        filename: str,
        digest: str,
        size_bytes: int,
        extension: str,
        status: str,
    ) -> "SourceRecord":
        return cls(
            source_id=digest[:16],
            filename=filename,
            sha256=digest,
            size_bytes=size_bytes,
            extension=extension,
            status=status,
        )


@dataclass(frozen=True, slots=True)
class RoleScore:
    """A deterministic score and its metadata-only detection signals."""

    role: str
    score: int
    signals: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.score, int) or isinstance(self.score, bool) or not 0 <= self.score <= 100:
            raise ValueError("invalid_role_score")
        object.__setattr__(self, "signals", tuple(self.signals))


@dataclass(frozen=True, slots=True)
class SheetRecord:
    """Inventory metadata for a worksheet; intentionally excludes cell values."""

    source_id: str
    sheet_id: str
    index: int
    name: str
    visibility: str
    used_range: str | None
    visited_cell_estimate: int
    candidate_header_rows: tuple[tuple[int, ...], ...]
    role_scores: tuple[RoleScore, ...]
    has_formulas: bool
    has_merged_ranges: bool
    has_hidden_data: bool
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_header_rows",
            tuple(tuple(rows) for rows in self.candidate_header_rows),
        )
        object.__setattr__(self, "role_scores", tuple(self.role_scores))


@dataclass(frozen=True, slots=True)
class CoverageItem:
    """Metadata-only evidence coverage state for a single evidence role."""

    evidence_role: str
    tier: str
    status: str
    procedure_status: str
    source_ids: tuple[str, ...]
    trigger_rule_id: str | None
    requested_at: str | None
    request_status: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_ids", tuple(self.source_ids))


def to_dict(value: Any) -> Any:
    """Recursively convert record metadata into JSON-friendly built-in values."""

    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple | list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    return value
