"""Human-confirmed account categories used by deterministic cross-checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import re
import unicodedata
from pathlib import Path

from .numeric import exact_add, exact_subtract, parse_decimal
from .reviews import (
    ReviewError,
    _atomic_write_review,
    _safe_path_stat,
    _validate_review_paths,
    current_source_manifest_sha256,
    effective_journal_mapping_fingerprint,
)


ACCOUNT_CLASSIFICATION_SCHEMA_VERSION = "account-classification/v1"
ALLOWED_ACCOUNT_CATEGORIES = frozenset(
    {
        "main_business_revenue",
        "other_business_revenue",
        "vat_output_tax",
        "business_entertainment",
    }
)
_REVIEW_FIELDS = frozenset(
    {
        "schema_version",
        "source_manifest_sha256",
        "mapping_fingerprint",
        "reviewer",
        "confirmed_at",
        "classifications",
    }
)
_CLASSIFICATION_FIELDS = frozenset(
    {"account_code", "account_name", "category"}
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_NON_HUMAN_REVIEWER_RE = re.compile(
    r"(?:openai|chatgpt|deepseek|qwen|gemini|doubao|kimi|claude|hermes|"
    r"workbuddy|coze|qodor|openclaw|glm|gpt|"
    r"(?:^|[\s:_-])(?:ai|agent|host_ai|model)(?:$|[\s:_-]))"
)
_COORDINATE_RE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")
_LINEAGE_FIELDS = frozenset(
    {
        "source_id",
        "sheet_id",
        "row",
        "column",
        "coordinate",
        "original_type",
        "value_origin",
        "mapping_version",
    }
)


@dataclass(frozen=True, slots=True)
class AccountClassification:
    account_code: str
    account_name: str
    category: str


@dataclass(frozen=True, slots=True)
class AccountClassificationReview:
    schema_version: str
    source_manifest_sha256: str
    mapping_fingerprint: str
    reviewer: str
    confirmed_at: str
    classifications: tuple[AccountClassification, ...]


@dataclass(frozen=True, slots=True)
class AccountSourceRef:
    account_code: str
    account_name: str
    category: str


def _invalid() -> ValueError:
    return ValueError("invalid_account_classification")


def _normalized_account_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    return normalized or None


def _valid_hash(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _valid_confirmation_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _valid_human_reviewer(value: object) -> bool:
    if not isinstance(value, str):
        return False
    reviewer = value.strip()
    if not reviewer.casefold().startswith("human:"):
        return False
    identity = reviewer.split(":", 1)[1].strip()
    return bool(identity) and _NON_HUMAN_REVIEWER_RE.search(identity.casefold()) is None


def parse_account_classification_review(payload: dict) -> AccountClassificationReview:
    """Parse only the exact, human-confirmed account-category contract."""

    if (
        not isinstance(payload, dict)
        or set(payload) != _REVIEW_FIELDS
        or payload.get("schema_version") != ACCOUNT_CLASSIFICATION_SCHEMA_VERSION
        or not _valid_hash(payload.get("source_manifest_sha256"))
        or not _valid_hash(payload.get("mapping_fingerprint"))
        or not _valid_human_reviewer(payload.get("reviewer"))
        or not _valid_confirmation_timestamp(payload.get("confirmed_at"))
        or not isinstance(payload.get("classifications"), list)
    ):
        raise _invalid()

    classifications: list[AccountClassification] = []
    identities: set[tuple[str, str]] = set()
    for raw_classification in payload["classifications"]:
        if (
            not isinstance(raw_classification, dict)
            or set(raw_classification) != _CLASSIFICATION_FIELDS
        ):
            raise _invalid()
        code = raw_classification.get("account_code")
        name = _normalized_account_name(raw_classification.get("account_name"))
        category = raw_classification.get("category")
        if (
            not isinstance(code, str)
            or not code
            or name is None
            or not isinstance(category, str)
            or category not in ALLOWED_ACCOUNT_CATEGORIES
        ):
            raise _invalid()
        identity = (code, name)
        if identity in identities:
            raise _invalid()
        identities.add(identity)
        classifications.append(AccountClassification(code, name, category))

    return AccountClassificationReview(
        schema_version=payload["schema_version"],
        source_manifest_sha256=payload["source_manifest_sha256"],
        mapping_fingerprint=payload["mapping_fingerprint"],
        reviewer=payload["reviewer"],
        confirmed_at=payload["confirmed_at"],
        classifications=tuple(classifications),
    )


def _column_number(label: str) -> int:
    number = 0
    for character in label:
        number = number * 26 + ord(character) - ord("A") + 1
    return number


def _valid_lineage_location(value: object) -> bool:
    if not isinstance(value, Mapping) or not _LINEAGE_FIELDS.issubset(value):
        return False
    source_id = value.get("source_id")
    sheet_id = value.get("sheet_id")
    row = value.get("row")
    column = value.get("column")
    coordinate = value.get("coordinate")
    coordinate_match = (
        _COORDINATE_RE.fullmatch(coordinate)
        if isinstance(coordinate, str)
        else None
    )
    return (
        isinstance(source_id, str)
        and bool(source_id)
        and isinstance(sheet_id, str)
        and bool(sheet_id)
        and isinstance(row, int)
        and not isinstance(row, bool)
        and row > 0
        and isinstance(column, int)
        and not isinstance(column, bool)
        and column > 0
        and coordinate_match is not None
        and int(coordinate_match.group(2)) == row
        and _column_number(coordinate_match.group(1)) == column
        and all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in ("original_type", "value_origin", "mapping_version")
        )
    )


def _has_required_lineage(row: Mapping[str, object]) -> bool:
    lineage = row.get("field_lineage")
    fields = ("account_code", "account_name", "debit", "credit")
    if not isinstance(lineage, Mapping) or not all(
        _valid_lineage_location(lineage.get(field)) for field in fields
    ):
        return False
    locations = tuple(lineage[field] for field in fields)
    return len(
        {
            (
                location["source_id"],
                location["sheet_id"],
                location["row"],
                location["mapping_version"],
            )
            for location in locations
        }
    ) == 1


def aggregate_classified_amount(
    rows: Sequence[Mapping[str, object]],
    review: AccountClassificationReview,
    category: str,
) -> tuple[Decimal, tuple[AccountSourceRef, ...]] | None:
    """Aggregate credit less debit for one explicitly confirmed category."""

    selected = tuple(
        classification
        for classification in review.classifications
        if classification.category == category
    )
    if not selected:
        return None

    classifications_by_identity = {
        (classification.account_code, classification.account_name): classification
        for classification in selected
    }
    amount = Decimal("0")
    matched = False
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("invalid_account_classification_row")
        account_code = row.get("account_code")
        account_name = _normalized_account_name(row.get("account_name"))
        classification = classifications_by_identity.get((account_code, account_name))
        if classification is None:
            continue
        if not _has_required_lineage(row):
            raise ValueError("invalid_account_classification_lineage")
        try:
            debit = parse_decimal(row.get("debit"))
            credit = parse_decimal(row.get("credit"))
        except (TypeError, ValueError) as error:
            raise ValueError("invalid_account_classification_amount") from error
        amount = exact_add(amount, exact_subtract(credit, debit))
        matched = True

    references = tuple(
        AccountSourceRef(
            classification.account_code,
            classification.account_name,
            classification.category,
        )
        for classification in selected
    )
    return (amount if matched else Decimal("0"), references)


def _review_payload(review: AccountClassificationReview) -> dict:
    return {
        "schema_version": review.schema_version,
        "source_manifest_sha256": review.source_manifest_sha256,
        "mapping_fingerprint": review.mapping_fingerprint,
        "reviewer": review.reviewer,
        "confirmed_at": review.confirmed_at,
        "classifications": [
            {
                "account_code": classification.account_code,
                "account_name": classification.account_name,
                "category": classification.category,
            }
            for classification in review.classifications
        ],
    }


def _current_bindings(case_dir: Path) -> tuple[str | None, str | None]:
    try:
        return (
            current_source_manifest_sha256(case_dir),
            effective_journal_mapping_fingerprint(case_dir),
        )
    except ReviewError as error:
        if str(error) == "unsafe_review_path":
            raise
        return None, None


def confirm_account_classifications(case_dir: Path, payload: dict) -> dict:
    """Validate current bindings, then atomically replace the active review."""

    case_dir = _validate_review_paths(case_dir, require_inventory=False)
    review = parse_account_classification_review(payload)
    current_manifest, current_mapping = _current_bindings(case_dir)
    if (
        current_manifest is None
        or current_mapping is None
        or review.source_manifest_sha256 != current_manifest
        or review.mapping_fingerprint != current_mapping
    ):
        raise ValueError("account_classification_binding_mismatch")
    _atomic_write_review(
        case_dir / "reviews" / "account_classification.json",
        _review_payload(review),
    )
    return _review_payload(review)


def load_active_account_classifications(
    case_dir: Path,
    source_manifest_sha256: str,
) -> AccountClassificationReview | None:
    """Return only an active review bound to the current source and mapping."""

    if not _valid_hash(source_manifest_sha256):
        return None
    case_dir = _validate_review_paths(case_dir, require_inventory=False)
    review_path = case_dir / "reviews" / "account_classification.json"
    if _safe_path_stat(review_path) is None:
        return None
    try:
        import json

        payload = json.loads(review_path.read_text(encoding="utf-8"))
        review = parse_account_classification_review(payload)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    current_manifest, current_mapping = _current_bindings(case_dir)
    if (
        current_manifest != source_manifest_sha256
        or review.source_manifest_sha256 != source_manifest_sha256
        or current_mapping is None
        or review.mapping_fingerprint != current_mapping
    ):
        return None
    return review
