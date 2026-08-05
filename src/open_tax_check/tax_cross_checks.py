"""Immutable contracts and exact arithmetic for ledger/tax cross-checks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from calendar import monthrange
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, localcontext
from enum import Enum, StrEnum
import hashlib
import json
import re
import unicodedata
from typing import Any

from .account_classification import (
    AccountClassificationReview,
    aggregate_classified_amount,
)
from .rules import business_entertainment_adjustment
from .structured_documents import FinancialStatement, TaxReturn


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
EXPLANATION_CATEGORIES = (
        "unbilled_revenue",
        "deemed_sale",
        "non_taxable_or_exempt_revenue",
        "tax_inclusive_conversion",
        "timing_difference",
        "amended_return",
        "book_adjustment",
        "other",
)
_EXPLANATION_CATEGORIES = frozenset(EXPLANATION_CATEGORIES)
_RESOLUTION_STATUSES = frozenset(
    {"unresolved", "explained_pending_verification", "closed"}
)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_DECIMAL_CONTEXT = Context(prec=28)
_AI_IDENTITIES = frozenset(
    {
        "ai",
        "agent",
        "host_ai",
        "model",
        "openai",
        "chatgpt",
        "gpt",
        "claude",
        "deepseek",
        "qwen",
        "hermes",
        "copilot",
        "qoder",
        "luna",
        "glm",
        "seed",
    }
)


class CrossCheckStatus(StrEnum):
    MATCHED = "matched"
    WITHIN_TOLERANCE = "within_tolerance"
    DIFFERENCE_FOUND = "difference_found"
    NOT_PERFORMED = "not_performed"


@dataclass(frozen=True, slots=True)
class DateRange:
    start: str
    end: str

    def __post_init__(self) -> None:
        _period(self.start, self.end)


@dataclass(frozen=True, slots=True)
class CrossCheckContext:
    period_start: str
    period_end: str
    entity_name: str
    currency: str
    unit_multiplier: str = "1"
    blocked_issue_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _period(self.period_start, self.period_end)
        _text(self.entity_name)
        _text(self.currency)
        if self.unit_multiplier not in {"1", "1000", "10000"}:
            raise _invalid()
        if any(not isinstance(item, str) or not item.strip() for item in self.blocked_issue_refs):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class SourcedAmount:
    amount: Decimal
    source_refs: tuple[CrossCheckSourceRef, ...]

    def __post_init__(self) -> None:
        _finite_decimal(self.amount)
        if not self.source_refs or any(
            not isinstance(item, CrossCheckSourceRef) for item in self.source_refs
        ):
            raise _invalid()


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
    with localcontext(_DECIMAL_CONTEXT):
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
class AmountBridgeItem:
    amount: Decimal
    label_zh: str
    evidence_refs: tuple[str, ...]
    component_id: str | None = None

    def __post_init__(self) -> None:
        _finite_decimal(self.amount)
        if (
            not _CJK_RE.search(_text(self.label_zh))
            or not self.evidence_refs
            or any(not isinstance(item, str) or not item.strip() for item in self.evidence_refs)
            or (self.component_id is not None and not _text(self.component_id))
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class CrossCheckExplanation:
    check_id: str
    category: str
    explanation_zh: str
    confirmed_by: str
    confirmed_at: str
    evidence_refs: tuple[str, ...]
    bridge_items: tuple[AmountBridgeItem, ...] = ()

    def __post_init__(self) -> None:
        if self.check_id not in CHECK_ORDER or self.category not in _EXPLANATION_CATEGORIES:
            raise _invalid()
        confirmer = _text(self.confirmed_by)
        if not confirmer.casefold().startswith("human:"):
            raise _invalid()
        identity = confirmer.split(":", 1)[1].strip()
        if not identity or any(token in _AI_IDENTITIES for token in re.split(r"[^a-z0-9_]+", identity.casefold())):
            raise _invalid()
        if (
            not _CJK_RE.search(_text(self.explanation_zh))
            or not self.evidence_refs
            or any(not isinstance(item, str) or not item.strip() for item in self.evidence_refs)
            or any(not isinstance(item, AmountBridgeItem) for item in self.bridge_items)
        ):
            raise _invalid()
        try:
            confirmed_at = datetime.fromisoformat(_text(self.confirmed_at))
        except ValueError:
            raise _invalid() from None
        if confirmed_at.tzinfo is None or confirmed_at.utcoffset() is None:
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
    resolution_status: str = "unresolved"
    explanations: tuple[CrossCheckExplanation, ...] = ()

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
            or self.resolution_status not in _RESOLUTION_STATUSES
            or any(not isinstance(item, CrossCheckExplanation) for item in self.explanations)
            or any(item.check_id != self.check_id for item in self.explanations)
        ):
            raise _invalid()
        if self.resolution_status != "unresolved" and (
            self.status is not CrossCheckStatus.DIFFERENCE_FOUND or not self.explanations
        ):
            raise _invalid()
        if self.resolution_status == "closed" and not _bridge_closes_result(
            self, self.explanations[-1].bridge_items
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
        if self.components:
            component_statuses = tuple(
                compare_amounts(item.ledger_amount, item.comparison_amount)[0]
                for item in self.components
            )
            expected_status = max(
                component_statuses,
                key=(
                    CrossCheckStatus.MATCHED,
                    CrossCheckStatus.WITHIN_TOLERANCE,
                    CrossCheckStatus.DIFFERENCE_FOUND,
                ).index,
            )
            expected_difference = _sum_decimals(
                item.difference_amount for item in self.components
            )
            expected_ratio = max(item.difference_ratio for item in self.components)
            if (
                self.status is not expected_status
                or self.difference_amount != expected_difference
                or self.difference_ratio != expected_ratio
            ):
                raise _invalid()
            return
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


def _scaled_amount(value: str, multiplier: str) -> Decimal:
    amount = Decimal(value)
    scale = Decimal(multiplier)
    if not amount.is_finite() or not scale.is_finite() or scale <= 0:
        raise _invalid()
    with localcontext(_DECIMAL_CONTEXT):
        return amount * scale


def _sum_decimals(values: Iterable[Decimal]) -> Decimal:
    with localcontext(_DECIMAL_CONTEXT):
        return sum(values, Decimal("0"))


def _signed_difference(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(_DECIMAL_CONTEXT):
        return right - left


def _bridge_closes_result(
    result: CrossCheckResult,
    bridge_items: Sequence[AmountBridgeItem],
) -> bool:
    items = tuple(bridge_items)
    if not items:
        return False
    if result.components:
        expected = {
            component.component_id: _signed_difference(
                component.ledger_amount, component.comparison_amount
            )
            for component in result.components
        }
        if any(item.component_id not in expected for item in items):
            return False
        actual = {
            component_id: _sum_decimals(
                item.amount for item in items if item.component_id == component_id
            )
            for component_id in expected
        }
        return actual == expected
    if any(item.component_id is not None for item in items):
        return False
    assert result.ledger_amount is not None and result.comparison_amount is not None
    return _sum_decimals(item.amount for item in items) == _signed_difference(
        result.ledger_amount, result.comparison_amount
    )


def statement_total(
    statements: Sequence[FinancialStatement],
    statement_type: str,
    total_key: str,
) -> SourcedAmount | None:
    """Read one confirmed statement total without inferring a missing line."""

    matching = tuple(item for item in statements if item.statement_type == statement_type)
    if len(matching) != 1:
        return None
    statement = matching[0]
    totals = dict(statement.totals)
    lines = tuple(
        line
        for line in statement.lines
        if line.line_code == total_key and line.confirmation_state == "confirmed"
    )
    if total_key not in totals or len(lines) != 1:
        return None
    line = lines[0]
    if line.current_amount is None or Decimal(line.current_amount) != Decimal(totals[total_key]):
        return None
    return SourcedAmount(
        _scaled_amount(totals[total_key], statement.unit_multiplier),
        (
            CrossCheckSourceRef(
                source_id=statement.source_id,
                source_type="financial_statement_line",
                locator=f"{statement.statement_id}:{line.line_code}",
            ),
        ),
    )


def _selected_return_for_period(items: Sequence[TaxReturn]) -> TaxReturn | None:
    if len(items) == 1:
        return items[0]
    timestamped: list[tuple[datetime, TaxReturn]] = []
    for item in items:
        if item.filed_at is None:
            continue
        try:
            filed_at = datetime.fromisoformat(item.filed_at)
        except ValueError:
            continue
        if filed_at.tzinfo is None or filed_at.utcoffset() is None:
            continue
        timestamped.append((filed_at, item))
    if not timestamped:
        return None
    latest_time = max(item[0] for item in timestamped)
    latest = tuple(item for filed_at, item in timestamped if filed_at == latest_time)
    if len(latest) != 1 or latest[0].filing_status != "amended":
        return None
    return latest[0]


def _valid_vat_filing_period(start: date, end: date) -> bool:
    if start.day != 1 or start.year != end.year:
        return False
    monthly_end = date(start.year, start.month, monthrange(start.year, start.month)[1])
    if end == monthly_end:
        return True
    if start.month not in {1, 4, 7, 10}:
        return False
    quarter_end_month = start.month + 2
    return end == date(
        start.year,
        quarter_end_month,
        monthrange(start.year, quarter_end_month)[1],
    )


def tax_fact_total(
    returns: Sequence[TaxReturn],
    tax_id: str,
    fact_key: str,
    period: DateRange,
) -> SourcedAmount | None:
    """Sum a confirmed tax fact only across an unambiguous complete period."""

    relevant = tuple(
        item
        for item in returns
        if item.tax_id == tax_id
        and item.filing_status in {"filed", "amended"}
    )
    if not relevant:
        return None
    if (
        len({item.entity_name for item in relevant}) != 1
        or len({item.currency for item in relevant}) != 1
        or len({item.unit_multiplier for item in relevant}) != 1
    ):
        return None
    grouped: dict[tuple[str, str], list[TaxReturn]] = {}
    for item in relevant:
        grouped.setdefault((item.period_start, item.period_end), []).append(item)
    selected: list[TaxReturn] = []
    for key in sorted(grouped):
        chosen = _selected_return_for_period(grouped[key])
        if chosen is None:
            return None
        selected.append(chosen)
    expected_start = date.fromisoformat(period.start)
    expected_end = date.fromisoformat(period.end)
    cursor = expected_start
    for item in selected:
        item_start = date.fromisoformat(item.period_start)
        item_end = date.fromisoformat(item.period_end)
        if (
            item_start != cursor
            or item_end < item_start
            or item_end > expected_end
            or (tax_id == "vat" and not _valid_vat_filing_period(item_start, item_end))
        ):
            return None
        cursor = item_end + timedelta(days=1)
    if cursor != expected_end + timedelta(days=1):
        return None
    total = Decimal("0")
    refs: list[CrossCheckSourceRef] = []
    for item in selected:
        lines = tuple(
            line
            for line in item.lines
            if line.fact_key == fact_key
            and line.confirmation_state == "confirmed"
            and line.value_type == "decimal"
            and isinstance(line.value, str)
        )
        if len(lines) != 1:
            return None
        line = lines[0]
        total = _sum_decimals((total, _scaled_amount(line.value, item.unit_multiplier)))
        refs.append(
            CrossCheckSourceRef(
                source_id=item.source_id,
                source_type="tax_return_line",
                locator=f"{item.return_id}:{line.form_code}:{line.line_number}",
                form_code=line.form_code,
                line_number=line.line_number,
            )
        )
    return SourcedAmount(total, tuple(refs))


def classified_ledger_total(
    rows: Sequence[Mapping[str, object]],
    review: AccountClassificationReview,
    category: str,
) -> SourcedAmount | None:
    """Aggregate a controlled ledger category and retain its actual sources."""

    aggregated = aggregate_classified_amount(rows, review, category)
    if aggregated is None:
        return None
    amount, account_refs = aggregated
    source_refs: list[CrossCheckSourceRef] = []
    for account_ref in account_refs:
        source_ids = sorted(
            {
                str(row["field_lineage"]["account_code"]["source_id"])
                for row in rows
                if row.get("account_code") == account_ref.account_code
                and isinstance(row.get("account_name"), str)
                and unicodedata.normalize("NFKC", row["account_name"]).strip()
                == account_ref.account_name
                and isinstance(row.get("field_lineage"), Mapping)
            }
        )
        for source_id in source_ids:
            source_refs.append(
                CrossCheckSourceRef(
                    source_id=source_id,
                    source_type="ledger_account",
                    locator=f"{account_ref.account_code}|{account_ref.account_name}",
                    account_category=category,
                )
            )
    if not source_refs:
        return None
    if category == "business_entertainment":
        amount = -amount
    return SourcedAmount(amount, tuple(source_refs))


def _combined_amount(*items: SourcedAmount) -> SourcedAmount:
    return SourcedAmount(
        _sum_decimals(item.amount for item in items),
        tuple(ref for item in items for ref in item.source_refs),
    )


def _missing_result(
    check_id: str,
    tax_ids: tuple[str, ...],
    title_zh: str,
    context: CrossCheckContext,
    reason_codes: tuple[str, ...],
    refs: tuple[CrossCheckSourceRef, ...] = (),
) -> CrossCheckResult:
    return CrossCheckResult(
        check_id,
        tax_ids,
        CrossCheckStatus.NOT_PERFORMED,
        None,
        None,
        None,
        None,
        ABSOLUTE_TOLERANCE,
        RELATIVE_TOLERANCE,
        context.period_start,
        context.period_end,
        refs,
        title_zh,
        reason_codes,
        None,
        (),
        (),
    )


def _amount_result(
    check_id: str,
    tax_ids: tuple[str, ...],
    title_zh: str,
    context: CrossCheckContext,
    left: SourcedAmount,
    right: SourcedAmount,
    *,
    components: tuple[CrossCheckComponent, ...] = (),
) -> CrossCheckResult:
    if components:
        status = max(
            (compare_amounts(item.ledger_amount, item.comparison_amount)[0] for item in components),
            key=(
                CrossCheckStatus.MATCHED,
                CrossCheckStatus.WITHIN_TOLERANCE,
                CrossCheckStatus.DIFFERENCE_FOUND,
            ).index,
        )
        difference = _sum_decimals(item.difference_amount for item in components)
        ratio = max(item.difference_ratio for item in components)
    else:
        status, difference, ratio = compare_amounts(left.amount, right.amount)
    return CrossCheckResult(
        check_id,
        tax_ids,
        status,
        left.amount,
        right.amount,
        difference,
        ratio,
        ABSOLUTE_TOLERANCE,
        RELATIVE_TOLERANCE,
        context.period_start,
        context.period_end,
        left.source_refs + right.source_refs,
        title_zh,
        (
            _ALLOWED_EXPLANATION_REASON_CODES
            if status is CrossCheckStatus.DIFFERENCE_FOUND
            else ()
        ),
        None,
        components,
        (),
    )


def _component(component_id: str, left: SourcedAmount, right: SourcedAmount) -> CrossCheckComponent:
    _, difference, ratio = compare_amounts(left.amount, right.amount)
    return CrossCheckComponent(
        component_id,
        left.amount,
        right.amount,
        difference,
        ratio,
        left.source_refs + right.source_refs,
    )


_CHECK_TITLES = {
    "BOOK-FS-REV-001": "账套收入与利润表营业收入核对",
    "FS-VAT-SALES-001": "利润表营业收入与增值税申报销售额核对",
    "BOOK-VAT-OUTPUT-001": "账套销项税额与增值税申报销项税额核对",
    "FS-CIT-REV-PROFIT-001": "利润表收入及利润总额与企业所得税申报核对",
    "BOOK-CIT-BE-001": "账套业务招待费与企业所得税纳税调整核对",
}
_ALLOWED_EXPLANATION_REASON_CODES = (
    "unbilled_revenue",
    "deemed_sale",
    "non_taxable_or_exempt_revenue",
    "tax_inclusive_conversion",
    "timing_difference",
    "amended_return",
    "book_adjustment",
    "other",
)


def _question_for(result: CrossCheckResult, priority: int) -> CrossCheckQuestion:
    identity = json.dumps(
        {
            "check_id": result.check_id,
            "period": (result.period_start, result.period_end),
            "sources": tuple(
                (ref.source_id, ref.source_type, ref.locator) for ref in result.source_refs
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    question_id = "cross-check-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    if result.status is CrossCheckStatus.NOT_PERFORMED:
        question_zh = f"为完成“{result.title_zh}”，请补充或确认对应账套、报表及申报资料。"
    else:
        question_zh = f"“{result.title_zh}”存在金额差异，请说明口径并提供可核验资料。"
    return CrossCheckQuestion(
        question_id,
        result.check_id,
        result.period_start,
        result.period_end,
        result.source_refs,
        question_zh,
        "请逐项列明金额、期间、资料来源和确认人，不要只提供口头说明。",
        ("ledger", "financial_statement", "tax_return", "supporting_document"),
        priority,
    )


def _attach_questions(
    results: tuple[CrossCheckResult, ...],
) -> tuple[tuple[CrossCheckResult, ...], tuple[CrossCheckQuestion, ...]]:
    candidates = tuple(
        item
        for item in results
        if item.status in {CrossCheckStatus.NOT_PERFORMED, CrossCheckStatus.DIFFERENCE_FOUND}
    )
    ordered = sorted(
        candidates,
        key=lambda item: (
            0 if item.status is CrossCheckStatus.NOT_PERFORMED else 1,
            -(item.difference_amount or Decimal("0")),
            -(item.difference_ratio or Decimal("0")),
            CHECK_ORDER.index(item.check_id),
        ),
    )
    selected: list[CrossCheckQuestion] = []
    seen: set[tuple[object, ...]] = set()
    for item in ordered:
        key = (
            item.check_id,
            item.period_start,
            item.period_end,
            tuple(item.source_refs),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(_question_for(item, 1000 - len(selected)))
        if len(selected) == 3:
            break
    by_check = {item.check_id: item for item in selected}
    return (
        tuple(replace(item, question=by_check.get(item.check_id)) for item in results),
        tuple(selected),
    )


def _context_issue_refs(
    financial_statements: Sequence[FinancialStatement],
    tax_returns: Sequence[TaxReturn],
    context: CrossCheckContext,
) -> tuple[str, ...]:
    context_issues = list(context.blocked_issue_refs)
    for item in financial_statements:
        if item.entity_name != context.entity_name:
            context_issues.append("entity_mismatch")
        if item.currency != context.currency:
            context_issues.append("currency_mismatch")
        if item.unit_multiplier != context.unit_multiplier:
            context_issues.append("unit_multiplier_mismatch")
        if (item.period_start, item.period_end) != (
            context.period_start,
            context.period_end,
        ):
            context_issues.append("period_mismatch")
    period_start = date.fromisoformat(context.period_start)
    period_end = date.fromisoformat(context.period_end)
    for item in tax_returns:
        if item.entity_name != context.entity_name:
            context_issues.append("entity_mismatch")
        if item.currency != context.currency:
            context_issues.append("currency_mismatch")
        if item.unit_multiplier != context.unit_multiplier:
            context_issues.append("unit_multiplier_mismatch")
        if (
            date.fromisoformat(item.period_start) < period_start
            or date.fromisoformat(item.period_end) > period_end
        ):
            context_issues.append("period_mismatch")
    return tuple(dict.fromkeys(context_issues))


def _execute_cross_check(
    check_id: str,
    rows: Sequence[Mapping[str, object]],
    classifications: AccountClassificationReview | None,
    financial_statements: Sequence[FinancialStatement],
    tax_returns: Sequence[TaxReturn],
    context: CrossCheckContext,
) -> CrossCheckResult:
    if check_id not in CHECK_ORDER:
        raise _invalid()
    period = DateRange(context.period_start, context.period_end)

    def statement_amount(key: str) -> SourcedAmount | None:
        return statement_total(financial_statements, "income_statement", key)

    def tax_amount(tax_id: str, key: str) -> SourcedAmount | None:
        identifiers = (tax_id, "enterprise_income_tax") if tax_id == "cit" else (tax_id,)
        for identifier in identifiers:
            value = tax_fact_total(tax_returns, identifier, key, period)
            if value is not None:
                return value
        return None

    def ledger_amount(category: str) -> SourcedAmount | None:
        if classifications is None:
            return None
        return classified_ledger_total(rows, classifications, category)

    title = _CHECK_TITLES[check_id]
    if check_id == CHECK_ORDER[0]:
        main_revenue = ledger_amount("main_business_revenue")
        other_revenue = ledger_amount("other_business_revenue")
        income_revenue = statement_amount("revenue")
        if main_revenue is None or other_revenue is None or income_revenue is None:
            return _missing_result(check_id, ("cit", "vat"), title, context, ("missing_controlled_revenue_or_statement",))
        return _amount_result(check_id, ("cit", "vat"), title, context, _combined_amount(main_revenue, other_revenue), income_revenue)
    if check_id == CHECK_ORDER[1]:
        income_revenue = statement_amount("revenue")
        vat_sales = tax_amount("vat", "vat.taxable_sales")
        if income_revenue is None or vat_sales is None:
            return _missing_result(check_id, ("vat", "cit"), title, context, ("missing_income_statement_or_vat_sales",))
        return _amount_result(check_id, ("vat", "cit"), title, context, income_revenue, vat_sales)
    if check_id == CHECK_ORDER[2]:
        ledger_output = ledger_amount("vat_output_tax")
        vat_output = tax_amount("vat", "vat.output_tax")
        if ledger_output is None or vat_output is None:
            return _missing_result(check_id, ("vat",), title, context, ("missing_controlled_output_tax_or_vat_return",))
        return _amount_result(check_id, ("vat",), title, context, ledger_output, vat_output)
    if check_id == CHECK_ORDER[3]:
        income_revenue = statement_amount("revenue")
        total_profit = statement_amount("total_profit")
        cit_revenue = tax_amount("cit", "cit.revenue")
        cit_profit = tax_amount("cit", "cit.total_profit")
        if any(item is None for item in (income_revenue, total_profit, cit_revenue, cit_profit)):
            return _missing_result(check_id, ("cit",), title, context, ("missing_income_statement_or_cit_facts",))
        assert income_revenue and total_profit and cit_revenue and cit_profit
        components = (
            _component("revenue", income_revenue, cit_revenue),
            _component("total_profit", total_profit, cit_profit),
        )
        return _amount_result(check_id, ("cit",), title, context, _combined_amount(income_revenue, total_profit), _combined_amount(cit_revenue, cit_profit), components=components)

    entertainment = ledger_amount("business_entertainment")
    be_book = tax_amount("cit", "cit.business_entertainment.book_amount")
    be_tax = tax_amount("cit", "cit.business_entertainment.tax_amount")
    cit_revenue = tax_amount("cit", "cit.revenue")
    if any(item is None for item in (entertainment, be_book, be_tax, cit_revenue)):
        return _missing_result(check_id, ("cit",), title, context, ("missing_entertainment_or_cit_facts",))
    assert entertainment and be_book and be_tax and cit_revenue
    calculation = business_entertainment_adjustment(entertainment.amount, cit_revenue.amount)
    deductible = SourcedAmount(Decimal(calculation["deductible_amount"]), entertainment.source_refs + cit_revenue.source_refs)
    tax_add_back = SourcedAmount(Decimal(calculation["tax_adjustment"]), entertainment.source_refs + cit_revenue.source_refs)
    declared_add_back = SourcedAmount(
        _signed_difference(be_tax.amount, be_book.amount),
        be_book.source_refs + be_tax.source_refs,
    )
    components = (
        _component("declared_book_amount", entertainment, be_book),
        _component("calculated_deductible", deductible, be_tax),
        _component("calculated_tax_add_back", tax_add_back, declared_add_back),
    )
    return _amount_result(check_id, ("cit",), title, context, _combined_amount(entertainment, deductible, tax_add_back), _combined_amount(be_book, be_tax, declared_add_back), components=components)


def run_tax_cross_checks(
    rows: Sequence[Mapping[str, object]],
    classifications: AccountClassificationReview | None,
    financial_statements: Sequence[FinancialStatement],
    tax_returns: Sequence[TaxReturn],
    context: CrossCheckContext,
    source_manifest_sha256: str,
    explanations: Sequence[object] = (),
) -> TaxCrossCheckRun:
    """Execute the five fixed cross-checks without inferring missing facts."""

    if explanations:
        raise _invalid()
    context_issues = _context_issue_refs(financial_statements, tax_returns, context)
    if context_issues:
        return TaxCrossCheckRun(
            TAX_CROSS_CHECK_SCHEMA_VERSION,
            "blocked_data_quality",
            (),
            (),
            source_manifest_sha256,
            (),
            context_issues,
        )
    results = tuple(
        _execute_cross_check(
            check_id,
            rows,
            classifications,
            financial_statements,
            tax_returns,
            context,
        )
        for check_id in CHECK_ORDER
    )
    final_results, questions = _attach_questions(results)
    return TaxCrossCheckRun(
        TAX_CROSS_CHECK_SCHEMA_VERSION,
        "completed",
        final_results,
        questions,
        source_manifest_sha256,
        (),
        (),
    )


def rerun_tax_cross_check(
    run: TaxCrossCheckRun,
    check_id: str,
    rows: Sequence[Mapping[str, object]],
    classifications: AccountClassificationReview | None,
    financial_statements: Sequence[FinancialStatement],
    tax_returns: Sequence[TaxReturn],
    context: CrossCheckContext,
    source_manifest_sha256: str,
) -> TaxCrossCheckRun:
    """Rerun one affected check and rebuild the bounded global question queue."""

    if (
        not isinstance(run, TaxCrossCheckRun)
        or run.status != "completed"
        or check_id not in CHECK_ORDER
        or source_manifest_sha256 != run.source_manifest_sha256
        or _context_issue_refs(financial_statements, tax_returns, context)
    ):
        raise _invalid()
    replacement = _execute_cross_check(
        check_id,
        rows,
        classifications,
        financial_statements,
        tax_returns,
        context,
    )
    results = tuple(
        replacement if item.check_id == check_id else item for item in run.results
    )
    final_results, questions = _attach_questions(results)
    return replace(run, results=final_results, questions=questions)


def apply_cross_check_explanation(
    run: TaxCrossCheckRun,
    explanation: CrossCheckExplanation,
    *,
    bridge: Sequence[AmountBridgeItem] = (),
    verified_evidence_refs: Sequence[str],
) -> TaxCrossCheckRun:
    """Attach a verified explanation without changing the original finding."""

    if not isinstance(run, TaxCrossCheckRun) or not isinstance(
        explanation, CrossCheckExplanation
    ):
        raise _invalid()
    bridge_items = tuple(bridge)
    if any(not isinstance(item, AmountBridgeItem) for item in bridge_items):
        raise _invalid()
    verified = frozenset(verified_evidence_refs)
    required = set(explanation.evidence_refs)
    required.update(ref for item in bridge_items for ref in item.evidence_refs)
    if not required or not required.issubset(verified):
        raise ValueError("unverified_cross_check_evidence")
    matches = [
        (index, item)
        for index, item in enumerate(run.results)
        if item.check_id == explanation.check_id
    ]
    if len(matches) != 1:
        raise _invalid()
    index, original = matches[0]
    if original.status is not CrossCheckStatus.DIFFERENCE_FOUND:
        raise ValueError("cross_check_explanation_not_applicable")
    assert original.ledger_amount is not None and original.comparison_amount is not None
    resolution = (
        "closed"
        if _bridge_closes_result(original, bridge_items)
        else "explained_pending_verification"
    )
    recorded = replace(explanation, bridge_items=bridge_items)
    event_payload = json.dumps(
        _json_value(recorded), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    audit_ref = "cross-check-explanation:" + hashlib.sha256(
        event_payload.encode("utf-8")
    ).hexdigest()
    updated_result = replace(
        original,
        question=None if resolution == "closed" else original.question,
        audit_refs=original.audit_refs + (audit_ref,),
        resolution_status=resolution,
        explanations=original.explanations + (recorded,),
    )
    results = list(run.results)
    results[index] = updated_result
    questions = tuple(
        item for item in run.questions if not (
            resolution == "closed" and item.check_id == explanation.check_id
        )
    )
    return replace(
        run,
        results=tuple(results),
        questions=questions,
        audit_events=run.audit_events + (audit_ref,),
    )


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


def _payload_decimal(value: object, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise _invalid()
    try:
        parsed = Decimal(value)
    except Exception:
        raise _invalid() from None
    if not parsed.is_finite():
        raise _invalid()
    return parsed


def _source_ref_from_payload(value: object) -> CrossCheckSourceRef:
    if not isinstance(value, Mapping) or set(value) != {
        "source_id", "source_type", "locator", "account_category", "form_code", "line_number"
    }:
        raise _invalid()
    return CrossCheckSourceRef(
        source_id=value.get("source_id"),
        source_type=value.get("source_type"),
        locator=value.get("locator"),
        account_category=value.get("account_category"),
        form_code=value.get("form_code"),
        line_number=value.get("line_number"),
    )


def _bridge_from_payload(value: object) -> AmountBridgeItem:
    if not isinstance(value, Mapping) or set(value) not in (
        {"amount", "label_zh", "evidence_refs"},
        {"amount", "label_zh", "evidence_refs", "component_id"},
    ):
        raise _invalid()
    refs = value.get("evidence_refs")
    if not isinstance(refs, list):
        raise _invalid()
    return AmountBridgeItem(
        amount=_payload_decimal(value.get("amount")),
        label_zh=value.get("label_zh"),
        evidence_refs=tuple(refs),
        component_id=value.get("component_id"),
    )


def _explanation_from_payload(value: object) -> CrossCheckExplanation:
    if not isinstance(value, Mapping) or set(value) != {
        "check_id", "category", "explanation_zh", "confirmed_by", "confirmed_at",
        "evidence_refs", "bridge_items"
    }:
        raise _invalid()
    refs = value.get("evidence_refs")
    bridges = value.get("bridge_items", [])
    if not isinstance(refs, list) or not isinstance(bridges, list):
        raise _invalid()
    return CrossCheckExplanation(
        check_id=value.get("check_id"),
        category=value.get("category"),
        explanation_zh=value.get("explanation_zh"),
        confirmed_by=value.get("confirmed_by"),
        confirmed_at=value.get("confirmed_at"),
        evidence_refs=tuple(refs),
        bridge_items=tuple(_bridge_from_payload(item) for item in bridges),
    )


def _question_from_payload(value: object) -> CrossCheckQuestion:
    if not isinstance(value, Mapping) or set(value) != {
        "question_id", "check_id", "period_start", "period_end", "source_refs",
        "question_zh", "help_zh", "evidence_types", "priority"
    }:
        raise _invalid()
    refs = value.get("source_refs")
    evidence_types = value.get("evidence_types")
    if not isinstance(refs, list) or not isinstance(evidence_types, list):
        raise _invalid()
    return CrossCheckQuestion(
        question_id=value.get("question_id"),
        check_id=value.get("check_id"),
        period_start=value.get("period_start"),
        period_end=value.get("period_end"),
        source_refs=tuple(_source_ref_from_payload(item) for item in refs),
        question_zh=value.get("question_zh"),
        help_zh=value.get("help_zh"),
        evidence_types=tuple(evidence_types),
        priority=value.get("priority"),
    )


def _component_from_payload(value: object) -> CrossCheckComponent:
    if (
        not isinstance(value, Mapping)
        or set(value) != {
            "component_id", "ledger_amount", "comparison_amount", "difference_amount",
            "difference_ratio", "source_refs"
        }
        or not isinstance(value.get("source_refs"), list)
    ):
        raise _invalid()
    return CrossCheckComponent(
        component_id=value.get("component_id"),
        ledger_amount=_payload_decimal(value.get("ledger_amount")),
        comparison_amount=_payload_decimal(value.get("comparison_amount")),
        difference_amount=_payload_decimal(value.get("difference_amount")),
        difference_ratio=_payload_decimal(value.get("difference_ratio")),
        source_refs=tuple(_source_ref_from_payload(item) for item in value["source_refs"]),
    )


def _result_from_payload(value: object) -> CrossCheckResult:
    if not isinstance(value, Mapping):
        raise _invalid()
    base_fields = {
        "check_id", "tax_ids", "status", "ledger_amount", "comparison_amount",
        "difference_amount", "difference_ratio", "absolute_tolerance", "relative_tolerance",
        "period_start", "period_end", "source_refs", "title_zh", "reason_codes", "question",
        "components", "audit_refs",
    }
    if frozenset(value) not in {
        frozenset(base_fields),
        frozenset(base_fields | {"resolution_status", "explanations"}),
    }:
        raise _invalid()
    sequence_fields = ("tax_ids", "source_refs", "reason_codes", "components", "audit_refs")
    if any(not isinstance(value.get(field), list) for field in sequence_fields):
        raise _invalid()
    explanations = value.get("explanations", [])
    if not isinstance(explanations, list):
        raise _invalid()
    question_payload = value.get("question")
    try:
        status = CrossCheckStatus(value.get("status"))
    except (TypeError, ValueError):
        raise _invalid() from None
    return CrossCheckResult(
        check_id=value.get("check_id"),
        tax_ids=tuple(value["tax_ids"]),
        status=status,
        ledger_amount=_payload_decimal(value.get("ledger_amount"), optional=True),
        comparison_amount=_payload_decimal(value.get("comparison_amount"), optional=True),
        difference_amount=_payload_decimal(value.get("difference_amount"), optional=True),
        difference_ratio=_payload_decimal(value.get("difference_ratio"), optional=True),
        absolute_tolerance=_payload_decimal(value.get("absolute_tolerance")),
        relative_tolerance=_payload_decimal(value.get("relative_tolerance")),
        period_start=value.get("period_start"),
        period_end=value.get("period_end"),
        source_refs=tuple(_source_ref_from_payload(item) for item in value["source_refs"]),
        title_zh=value.get("title_zh"),
        reason_codes=tuple(value["reason_codes"]),
        question=None if question_payload is None else _question_from_payload(question_payload),
        components=tuple(_component_from_payload(item) for item in value["components"]),
        audit_refs=tuple(value["audit_refs"]),
        resolution_status=value.get("resolution_status", "unresolved"),
        explanations=tuple(_explanation_from_payload(item) for item in explanations),
    )


def parse_tax_cross_check_payload(payload: object) -> TaxCrossCheckRun:
    """Parse a persisted run back into the strict immutable contract."""

    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version", "status", "results", "questions", "source_manifest_sha256",
        "audit_events", "blocking_issue_refs"
    }:
        raise _invalid()
    results = payload.get("results")
    questions = payload.get("questions")
    events = payload.get("audit_events")
    issues = payload.get("blocking_issue_refs")
    if any(not isinstance(item, list) for item in (results, questions, events, issues)):
        raise _invalid()
    return TaxCrossCheckRun(
        schema_version=payload.get("schema_version"),
        status=payload.get("status"),
        results=tuple(_result_from_payload(item) for item in results),
        questions=tuple(_question_from_payload(item) for item in questions),
        source_manifest_sha256=payload.get("source_manifest_sha256"),
        audit_events=tuple(events),
        blocking_issue_refs=tuple(issues),
    )


__all__ = [
    "ABSOLUTE_TOLERANCE",
    "CHECK_ORDER",
    "RELATIVE_TOLERANCE",
    "TAX_CROSS_CHECK_SCHEMA_VERSION",
    "CrossCheckComponent",
    "CrossCheckExplanation",
    "CrossCheckContext",
    "CrossCheckQuestion",
    "CrossCheckResult",
    "CrossCheckSourceRef",
    "CrossCheckStatus",
    "EXPLANATION_CATEGORIES",
    "DateRange",
    "SourcedAmount",
    "TaxCrossCheckRun",
    "AmountBridgeItem",
    "apply_cross_check_explanation",
    "classified_ledger_total",
    "compare_amounts",
    "run_tax_cross_checks",
    "rerun_tax_cross_check",
    "parse_tax_cross_check_payload",
    "statement_total",
    "tax_fact_total",
    "tax_cross_check_payload",
]
