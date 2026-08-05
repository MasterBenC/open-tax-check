"""Strict, immutable structured financial-document contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
import json
import re

from .numeric import parse_decimal
from .tax_registry import TAX_DEFINITIONS


_ERROR_CODE = "invalid_structured_document"
_CELL_PATTERN = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
_FINANCIAL_STATEMENT_KEYS = {
    "schema_version", "statement_id", "source_id", "entity_name",
    "statement_type", "period_start", "period_end", "as_of_date",
    "currency", "unit_multiplier", "lines", "totals", "issues",
}
_LINE_KEYS = {
    "line_code", "original_name", "standard_name", "current_amount",
    "prior_amount", "source_anchor", "confidence_source", "confirmation_state",
}
_ANCHOR_KEYS = {"source_id", "page_number", "sheet_name", "cell", "coordinates"}
_ISSUE_KEYS = {"code", "severity", "message_zh", "line_code", "source_anchor"}
_TAX_RETURN_KEYS = {
    "schema_version", "return_id", "source_id", "entity_name", "tax_id",
    "return_type", "period_start", "period_end", "filing_status", "filed_at",
    "currency", "unit_multiplier", "lines", "totals", "issues",
}
_TAX_RETURN_LINE_KEYS = {
    "form_code", "line_number", "original_name", "fact_key", "value", "value_type",
    "source_anchor", "confidence_source", "confirmation_state",
}
_STATEMENT_TYPES = {"balance_sheet", "income_statement", "cash_flow_statement"}
_CONFIDENCE_SOURCES = {"template", "host_ai", "customer", "native"}
_CONFIRMATION_STATES = {"proposed", "confirmed", "rejected"}
_FILING_STATUSES = {"draft", "filed", "amended", "unknown"}
_VALUE_TYPES = {"decimal", "text", "choice", "boolean", "empty"}
_FACT_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
CONTROLLED_CROSS_CHECK_FACT_KEYS = frozenset(
    {
        "vat.taxable_sales",
        "vat.output_tax",
        "cit.revenue",
        "cit.total_profit",
        "cit.business_entertainment.book_amount",
        "cit.business_entertainment.tax_amount",
    }
)
_TAX_IDS = frozenset(item.tax_id for item in TAX_DEFINITIONS)

_MAX_LINES = 10_000
_MAX_ISSUES = 1_000
_MAX_SOURCE_ANCHORS = 10_000
_MAX_JSON_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SourceAnchor:
    source_id: str
    page_number: int | None
    sheet_name: str | None
    cell: str | None
    coordinates: tuple[tuple[float, float], ...] | None


@dataclass(frozen=True, slots=True)
class StructuredIssue:
    code: str
    severity: str
    message_zh: str
    line_code: str | None
    source_anchor: SourceAnchor | None


@dataclass(frozen=True, slots=True)
class FinancialStatementLine:
    line_code: str
    original_name: str
    standard_name: str
    current_amount: str | None
    prior_amount: str | None
    source_anchor: SourceAnchor
    confidence_source: str
    confirmation_state: str


@dataclass(frozen=True, slots=True)
class FinancialStatement:
    schema_version: str
    statement_id: str
    source_id: str
    entity_name: str
    statement_type: str
    period_start: str
    period_end: str
    as_of_date: str | None
    currency: str
    unit_multiplier: str
    lines: tuple[FinancialStatementLine, ...]
    totals: tuple[tuple[str, str], ...]
    issues: tuple[StructuredIssue, ...]


@dataclass(frozen=True, slots=True)
class TaxReturnLine:
    form_code: str
    line_number: str
    original_name: str
    fact_key: str | None
    value: str | bool | None
    value_type: str
    source_anchor: SourceAnchor
    confidence_source: str
    confirmation_state: str


@dataclass(frozen=True, slots=True)
class TaxReturn:
    schema_version: str
    return_id: str
    source_id: str
    entity_name: str
    tax_id: str
    return_type: str
    period_start: str
    period_end: str
    filing_status: str
    filed_at: str | None
    currency: str
    unit_multiplier: str
    lines: tuple[TaxReturnLine, ...]
    totals: tuple[tuple[str, str], ...]
    issues: tuple[StructuredIssue, ...]


def _invalid() -> ValueError:
    return ValueError(_ERROR_CODE)


def _mapping(value: object, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _invalid()
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid()
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value)


def _date(value: object) -> str:
    text = _text(value)
    try:
        date.fromisoformat(text)
    except ValueError:
        raise _invalid() from None
    return text


def _amount(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid()
    try:
        parsed = parse_decimal(value)
    except (TypeError, ValueError):
        raise _invalid() from None
    if len(format(parsed, "f").partition(".")[2]) > 2:
        raise _invalid()
    return value


def _optional_amount(value: object) -> str | None:
    if value is None:
        return None
    return _amount(value)


def _validate_json_size(payload: Mapping[str, object]) -> None:
    try:
        size = len(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        raise _invalid() from None
    if size > _MAX_JSON_BYTES:
        raise _invalid()


def _source_anchor(value: object) -> SourceAnchor:
    payload = _mapping(value, _ANCHOR_KEYS)
    source_id = _text(payload["source_id"])
    page_number = payload["page_number"]
    sheet_name = _optional_text(payload["sheet_name"])
    cell = _optional_text(payload["cell"])
    coordinates = payload["coordinates"]

    if isinstance(page_number, bool) or (page_number is not None and not isinstance(page_number, int)):
        raise _invalid()
    if page_number is not None and page_number < 1:
        raise _invalid()

    has_sheet_locator = sheet_name is not None or cell is not None
    has_page_locator = page_number is not None or coordinates is not None
    if has_sheet_locator == has_page_locator:
        raise _invalid()
    if has_sheet_locator:
        if sheet_name is None or cell is None or _CELL_PATTERN.fullmatch(cell) is None:
            raise _invalid()
        return SourceAnchor(source_id, None, sheet_name, cell, None)

    if page_number is None or not isinstance(coordinates, list) or len(coordinates) != 4:
        raise _invalid()
    parsed_coordinates: list[tuple[float, float]] = []
    for point in coordinates:
        if not isinstance(point, list) or len(point) != 2:
            raise _invalid()
        x, y = point
        if (
            isinstance(x, bool) or isinstance(y, bool)
            or not isinstance(x, (int, float)) or not isinstance(y, (int, float))
            or not 0 <= x <= 1 or not 0 <= y <= 1
        ):
            raise _invalid()
        parsed_coordinates.append((float(x), float(y)))
    return SourceAnchor(source_id, page_number, None, None, tuple(parsed_coordinates))


def _line(value: object) -> FinancialStatementLine:
    payload = _mapping(value, _LINE_KEYS)
    confidence_source = _text(payload["confidence_source"])
    confirmation_state = _text(payload["confirmation_state"])
    if (
        confidence_source not in _CONFIDENCE_SOURCES
        or confirmation_state not in _CONFIRMATION_STATES
        or (confidence_source == "host_ai" and confirmation_state == "confirmed")
    ):
        raise _invalid()
    return FinancialStatementLine(
        line_code=_text(payload["line_code"]),
        original_name=_text(payload["original_name"]),
        standard_name=_text(payload["standard_name"]),
        current_amount=_optional_amount(payload["current_amount"]),
        prior_amount=_optional_amount(payload["prior_amount"]),
        source_anchor=_source_anchor(payload["source_anchor"]),
        confidence_source=confidence_source,
        confirmation_state=confirmation_state,
    )


def _issue(value: object) -> StructuredIssue:
    payload = _mapping(value, _ISSUE_KEYS)
    source_anchor = payload["source_anchor"]
    return StructuredIssue(
        code=_text(payload["code"]),
        severity=_text(payload["severity"]),
        message_zh=_text(payload["message_zh"]),
        line_code=_optional_text(payload["line_code"]),
        source_anchor=None if source_anchor is None else _source_anchor(source_anchor),
    )


def _fact_key(value: object) -> str | None:
    if value is None:
        return None
    text = _text(value)
    if _FACT_KEY_PATTERN.fullmatch(text) is None:
        raise _invalid()
    return text


def _tax_return_value(value: object, value_type: str) -> str | bool | None:
    if value_type == "decimal":
        return _amount(value)
    if value_type in {"text", "choice"}:
        return _text(value)
    if value_type == "boolean":
        if not isinstance(value, bool):
            raise _invalid()
        return value
    if value is not None:
        raise _invalid()
    return None


def _tax_return_line(value: object) -> TaxReturnLine:
    payload = _mapping(value, _TAX_RETURN_LINE_KEYS)
    confidence_source = _text(payload["confidence_source"])
    confirmation_state = _text(payload["confirmation_state"])
    value_type = _text(payload["value_type"])
    if (
        confidence_source not in _CONFIDENCE_SOURCES
        or confirmation_state not in _CONFIRMATION_STATES
        or value_type not in _VALUE_TYPES
        or (confidence_source == "host_ai" and confirmation_state == "confirmed")
    ):
        raise _invalid()
    return TaxReturnLine(
        form_code=_text(payload["form_code"]),
        line_number=_text(payload["line_number"]),
        original_name=_text(payload["original_name"]),
        fact_key=_fact_key(payload["fact_key"]),
        value=_tax_return_value(payload["value"], value_type),
        value_type=value_type,
        source_anchor=_source_anchor(payload["source_anchor"]),
        confidence_source=confidence_source,
        confirmation_state=confirmation_state,
    )


def _filed_at(value: object) -> str | None:
    if value is None:
        return None
    text = _text(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise _invalid() from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _invalid()
    return text


def financial_statement_from_dict(payload: Mapping[str, object]) -> FinancialStatement:
    """Validate a financial-statement/v1 JSON payload without trusting its data."""

    try:
        _validate_json_size(payload)
        document = _mapping(payload, _FINANCIAL_STATEMENT_KEYS)
        if document["schema_version"] != "financial-statement/v1":
            raise _invalid()
        statement_type = _text(document["statement_type"])
        if statement_type not in _STATEMENT_TYPES:
            raise _invalid()
        if (
            document["currency"] != "CNY"
            or not isinstance(document["unit_multiplier"], str)
            or document["unit_multiplier"] not in {"1", "1000", "10000"}
        ):
            raise _invalid()
        lines_value = document["lines"]
        issues_value = document["issues"]
        if not isinstance(lines_value, list) or len(lines_value) > _MAX_LINES:
            raise _invalid()
        if not isinstance(issues_value, list) or len(issues_value) > _MAX_ISSUES:
            raise _invalid()
        lines = tuple(_line(item) for item in lines_value)
        issues = tuple(_issue(item) for item in issues_value)
        source_anchor_count = len(lines) + sum(issue.source_anchor is not None for issue in issues)
        if source_anchor_count > _MAX_SOURCE_ANCHORS:
            raise _invalid()
        totals_value = document["totals"]
        if not isinstance(totals_value, Mapping):
            raise _invalid()
        totals = tuple(sorted((_text(key), _amount(amount)) for key, amount in totals_value.items()))
        if len({key for key, _ in totals}) != len(totals):
            raise _invalid()
        as_of_date = document["as_of_date"]
        return FinancialStatement(
            schema_version="financial-statement/v1",
            statement_id=_text(document["statement_id"]),
            source_id=_text(document["source_id"]),
            entity_name=_text(document["entity_name"]),
            statement_type=statement_type,
            period_start=_date(document["period_start"]),
            period_end=_date(document["period_end"]),
            as_of_date=None if as_of_date is None else _date(as_of_date),
            currency="CNY",
            unit_multiplier=document["unit_multiplier"],
            lines=lines,
            totals=totals,
            issues=issues,
        )
    except ValueError as exc:
        if str(exc) == _ERROR_CODE:
            raise
        raise _invalid() from None


def tax_return_from_dict(payload: Mapping[str, object]) -> TaxReturn:
    """Validate a tax-return/v1 JSON payload without trusting its data."""

    try:
        _validate_json_size(payload)
        document = _mapping(payload, _TAX_RETURN_KEYS)
        if document["schema_version"] != "tax-return/v1":
            raise _invalid()
        tax_id = _text(document["tax_id"])
        filing_status = _text(document["filing_status"])
        if tax_id not in _TAX_IDS or filing_status not in _FILING_STATUSES:
            raise _invalid()
        if (
            document["currency"] != "CNY"
            or not isinstance(document["unit_multiplier"], str)
            or document["unit_multiplier"] not in {"1", "1000", "10000"}
        ):
            raise _invalid()
        lines_value = document["lines"]
        issues_value = document["issues"]
        if not isinstance(lines_value, list) or len(lines_value) > _MAX_LINES:
            raise _invalid()
        if not isinstance(issues_value, list) or len(issues_value) > _MAX_ISSUES:
            raise _invalid()
        lines = tuple(_tax_return_line(item) for item in lines_value)
        issues = tuple(_issue(item) for item in issues_value)
        if len(lines) + sum(issue.source_anchor is not None for issue in issues) > _MAX_SOURCE_ANCHORS:
            raise _invalid()
        line_numbers = {(line.form_code, line.line_number) for line in lines}
        fact_keys = {line.fact_key for line in lines if line.fact_key is not None}
        if len(line_numbers) != len(lines) or len(fact_keys) != sum(
            line.fact_key is not None for line in lines
        ):
            raise _invalid()
        totals_value = document["totals"]
        if not isinstance(totals_value, Mapping):
            raise _invalid()
        totals = tuple(sorted((_text(key), _amount(amount)) for key, amount in totals_value.items()))
        period_start = _date(document["period_start"])
        period_end = _date(document["period_end"])
        if date.fromisoformat(period_start) > date.fromisoformat(period_end):
            raise _invalid()
        filed_at = _filed_at(document["filed_at"])
        if filing_status in {"filed", "amended"} and filed_at is None:
            raise _invalid()
        return TaxReturn(
            schema_version="tax-return/v1",
            return_id=_text(document["return_id"]),
            source_id=_text(document["source_id"]),
            entity_name=_text(document["entity_name"]),
            tax_id=tax_id,
            return_type=_text(document["return_type"]),
            period_start=period_start,
            period_end=period_end,
            filing_status=filing_status,
            filed_at=filed_at,
            currency="CNY",
            unit_multiplier=document["unit_multiplier"],
            lines=lines,
            totals=totals,
            issues=issues,
        )
    except ValueError as exc:
        if str(exc) == _ERROR_CODE:
            raise
        raise _invalid() from None


def _anchor_to_dict(value: SourceAnchor) -> dict[str, object]:
    return {
        "source_id": value.source_id,
        "page_number": value.page_number,
        "sheet_name": value.sheet_name,
        "cell": value.cell,
        "coordinates": None if value.coordinates is None else [list(point) for point in value.coordinates],
    }


def financial_statement_to_dict(value: FinancialStatement) -> dict[str, object]:
    """Serialize a validated financial statement into its JSON contract shape."""

    if not isinstance(value, FinancialStatement):
        raise TypeError("expected FinancialStatement")
    return {
        "schema_version": value.schema_version,
        "statement_id": value.statement_id,
        "source_id": value.source_id,
        "entity_name": value.entity_name,
        "statement_type": value.statement_type,
        "period_start": value.period_start,
        "period_end": value.period_end,
        "as_of_date": value.as_of_date,
        "currency": value.currency,
        "unit_multiplier": value.unit_multiplier,
        "lines": [
            {
                "line_code": line.line_code,
                "original_name": line.original_name,
                "standard_name": line.standard_name,
                "current_amount": line.current_amount,
                "prior_amount": line.prior_amount,
                "source_anchor": _anchor_to_dict(line.source_anchor),
                "confidence_source": line.confidence_source,
                "confirmation_state": line.confirmation_state,
            }
            for line in value.lines
        ],
        "totals": dict(value.totals),
        "issues": [
            {
                "code": issue.code,
                "severity": issue.severity,
                "message_zh": issue.message_zh,
                "line_code": issue.line_code,
                "source_anchor": None if issue.source_anchor is None else _anchor_to_dict(issue.source_anchor),
            }
            for issue in value.issues
        ],
    }


def tax_return_to_dict(value: TaxReturn) -> dict[str, object]:
    """Serialize a validated tax return into its JSON contract shape."""

    if not isinstance(value, TaxReturn):
        raise TypeError("expected TaxReturn")
    return {
        "schema_version": value.schema_version,
        "return_id": value.return_id,
        "source_id": value.source_id,
        "entity_name": value.entity_name,
        "tax_id": value.tax_id,
        "return_type": value.return_type,
        "period_start": value.period_start,
        "period_end": value.period_end,
        "filing_status": value.filing_status,
        "filed_at": value.filed_at,
        "currency": value.currency,
        "unit_multiplier": value.unit_multiplier,
        "lines": [
            {
                "form_code": line.form_code,
                "line_number": line.line_number,
                "original_name": line.original_name,
                "fact_key": line.fact_key,
                "value": line.value,
                "value_type": line.value_type,
                "source_anchor": _anchor_to_dict(line.source_anchor),
                "confidence_source": line.confidence_source,
                "confirmation_state": line.confirmation_state,
            }
            for line in value.lines
        ],
        "totals": dict(value.totals),
        "issues": [
            {
                "code": issue.code,
                "severity": issue.severity,
                "message_zh": issue.message_zh,
                "line_code": issue.line_code,
                "source_anchor": None if issue.source_anchor is None else _anchor_to_dict(issue.source_anchor),
            }
            for issue in value.issues
        ],
    }
