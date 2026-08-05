"""Immutable contracts and exact arithmetic for ledger/tax cross-checks."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from decimal import Decimal
from enum import Enum, StrEnum
import re
from typing import Any


ABSOLUTE_TOLERANCE = Decimal("10.00")
RELATIVE_TOLERANCE = Decimal("0.0001")
CHECK_ORDER = (
    "BOOK-FS-REV-001",
    "FS-VAT-SALES-001",
    "BOOK-VAT-OUTPUT-001",
    "FS-CIT-REV-PROFIT-001",
    "BOOK-CIT-BE-001",
)
TAX_CROSS_CHECK_SCHEMA_VERSION = "tax-cross-checks/v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_TYPES = frozenset(
    {"ledger_account", "financial_statement_line", "tax_return_line"}
)


class CrossCheckStatus(StrEnum):
    MATCHED = "matched"
    WITHIN_TOLERANCE = "within_tolerance"
    DIFFERENCE_FOUND = "difference_found"
    NOT_PERFORMED = "not_performed"


def _invalid() -> ValueError:
    return ValueError("invalid_tax_cross_check")


def _finite_decimal(value: object, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise _invalid()
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid()
    return value


def _period(start: object, end: object) -> None:
    try:
        parsed_start = date.fromisoformat(_text(start))
        parsed_end = date.fromisoformat(_text(end))
    except ValueError:
        raise _invalid() from None
    if parsed_start > parsed_end:
        raise _invalid()


def compare_amounts(
    left: Decimal,
    right: Decimal,
) -> tuple[CrossCheckStatus, Decimal, Decimal]:
    """Compare exact amounts using the immutable dual tolerance."""

    left = _finite_decimal(left)
    right = _finite_decimal(right)
    assert left is not None and right is not None
    difference = abs(left - right)
    if difference == 0:
        return CrossCheckStatus.MATCHED, difference, Decimal("0")
    denominator = max(abs(left), abs(right))
    ratio = Decimal("1") if denominator == 0 else difference / denominator
    status = (
        CrossCheckStatus.WITHIN_TOLERANCE
        if difference <= ABSOLUTE_TOLERANCE
        and ratio <= RELATIVE_TOLERANCE
        else CrossCheckStatus.DIFFERENCE_FOUND
    )
    return status, difference, ratio


@dataclass(frozen=True, slots=True)
class CrossCheckSourceRef:
    source_id: str
    source_type: str
    locator: str
    account_category: str | None = None
    form_code: str | None = None
    line_number: str | None = None

    def __post_init__(self) -> None:
        _text(self.source_id)
        _text(self.locator)
        if self.source_type not in _SOURCE_TYPES:
            raise _invalid()
        optional_values = (
            self.account_category,
            self.form_code,
            self.line_number,
        )
        if any(value is not None and not _text(value) for value in optional_values):
            raise _invalid()
        if self.source_type == "ledger_account" and self.account_category is None:
            raise _invalid()
        if self.source_type == "tax_return_line" and (
            self.form_code is None or self.line_number is None
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class CrossCheckComponent:
    component_id: str
    ledger_amount: Decimal
    comparison_amount: Decimal
    difference_amount: Decimal
    difference_ratio: Decimal
    source_refs: tuple[CrossCheckSourceRef, ...]

    def __post_init__(self) -> None:
        _text(self.component_id)
        left = _finite_decimal(self.ledger_amount)
        right = _finite_decimal(self.comparison_amount)
        difference = _finite_decimal(self.difference_amount)
        ratio = _finite_decimal(self.difference_ratio)
        if (
            not self.source_refs
            or any(not isinstance(item, CrossCheckSourceRef) for item in self.source_refs)
        ):
            raise _invalid()
        assert left is not None and right is not None
        _, expected_difference, expected_ratio = compare_amounts(left, right)
        if difference != expected_difference or ratio != expected_ratio:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class CrossCheckQuestion:
    question_id: str
    check_id: str
    period_start: str
    period_end: str
    source_refs: tuple[CrossCheckSourceRef, ...]
    question_zh: str
    help_zh: str
    evidence_types: tuple[str, ...]
    priority: int

    def __post_init__(self) -> None:
        _text(self.question_id)
        if self.check_id not in CHECK_ORDER:
            raise _invalid()
        _period(self.period_start, self.period_end)
        _text(self.question_zh)
        _text(self.help_zh)
        if (
            not self.evidence_types
            or any(not isinstance(item, str) or not item.strip() for item in self.evidence_types)
            or len(set(self.evidence_types)) != len(self.evidence_types)
            or not isinstance(self.priority, int)
            or isinstance(self.priority, bool)
            or self.priority < 0
            or any(not isinstance(item, CrossCheckSourceRef) for item in self.source_refs)
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class CrossCheckResult:
    check_id: str
    tax_ids: tuple[str, ...]
    status: CrossCheckStatus
    ledger_amount: Decimal | None
    comparison_amount: Decimal | None
    difference_amount: Decimal | None
    difference_ratio: Decimal | None
    absolute_tolerance: Decimal
    relative_tolerance: Decimal
    period_start: str
    period_end: str
    source_refs: tuple[CrossCheckSourceRef, ...]
    title_zh: str
    reason_codes: tuple[str, ...]
    question: CrossCheckQuestion | None
    components: tuple[CrossCheckComponent, ...]
    audit_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.check_id not in CHECK_ORDER or not isinstance(
            self.status, CrossCheckStatus
        ):
            raise _invalid()
        if (
            not self.tax_ids
            or any(not isinstance(item, str) or not item.strip() for item in self.tax_ids)
            or len(set(self.tax_ids)) != len(self.tax_ids)
        ):
            raise _invalid()
        _period(self.period_start, self.period_end)
        _text(self.title_zh)
        if (
            self.absolute_tolerance != ABSOLUTE_TOLERANCE
            or self.relative_tolerance != RELATIVE_TOLERANCE
            or any(not isinstance(item, CrossCheckSourceRef) for item in self.source_refs)
            or any(not isinstance(item, CrossCheckComponent) for item in self.components)
            or any(not isinstance(item, str) or not item.strip() for item in self.reason_codes)
            or any(not isinstance(item, str) or not item.strip() for item in self.audit_refs)
        ):
            raise _invalid()
        _finite_decimal(self.absolute_tolerance)
        _finite_decimal(self.relative_tolerance)
        amounts = (
            self.ledger_amount,
            self.comparison_amount,
            self.difference_amount,
            self.difference_ratio,
        )
        for value in amounts:
            _finite_decimal(value, optional=True)
        if self.question is not None and (
            not isinstance(self.question, CrossCheckQuestion)
            or self.question.check_id != self.check_id
        ):
            raise _invalid()
        if self.status is CrossCheckStatus.NOT_PERFORMED:
            if any(value is not None for value in amounts) or not self.reason_codes:
                raise _invalid()
            return
        if any(value is None for value in amounts) or not self.source_refs:
            raise _invalid()
        assert self.ledger_amount is not None and self.comparison_amount is not None
        expected_status, expected_difference, expected_ratio = compare_amounts(
            self.ledger_amount,
            self.comparison_amount,
        )
        if (
            self.status is not expected_status
            or self.difference_amount != expected_difference
            or self.difference_ratio != expected_ratio
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class TaxCrossCheckRun:
    schema_version: str
    status: str
    results: tuple[CrossCheckResult, ...]
    questions: tuple[CrossCheckQuestion, ...]
    source_manifest_sha256: str
    audit_events: tuple[str, ...]
    blocking_issue_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.schema_version != TAX_CROSS_CHECK_SCHEMA_VERSION
            or self.status not in {"completed", "blocked_data_quality"}
            or _SHA256_RE.fullmatch(self.source_manifest_sha256) is None
            or any(not isinstance(item, CrossCheckResult) for item in self.results)
            or any(not isinstance(item, CrossCheckQuestion) for item in self.questions)
            or len(self.questions) > 3
            or any(not isinstance(item, str) or not item.strip() for item in self.audit_events)
            or any(
                not isinstance(item, str) or not item.strip()
                for item in self.blocking_issue_refs
            )
        ):
            raise _invalid()
        if self.status == "blocked_data_quality":
            if self.results or self.questions or not self.blocking_issue_refs:
                raise _invalid()
            return
        if self.blocking_issue_refs or tuple(
            result.check_id for result in self.results
        ) != CHECK_ORDER:
            raise _invalid()
        question_ids = tuple(item.question_id for item in self.questions)
        if len(set(question_ids)) != len(question_ids):
            raise _invalid()


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, str | int | bool):
        return value
    raise _invalid()


def tax_cross_check_payload(run: TaxCrossCheckRun) -> dict[str, Any]:
    """Return a deterministic JSON-safe representation of one immutable run."""

    if not isinstance(run, TaxCrossCheckRun):
        raise _invalid()
    return _json_value(run)


__all__ = [
    "ABSOLUTE_TOLERANCE",
    "CHECK_ORDER",
    "RELATIVE_TOLERANCE",
    "TAX_CROSS_CHECK_SCHEMA_VERSION",
    "CrossCheckComponent",
    "CrossCheckQuestion",
    "CrossCheckResult",
    "CrossCheckSourceRef",
    "CrossCheckStatus",
    "TaxCrossCheckRun",
    "compare_amounts",
    "tax_cross_check_payload",
]
