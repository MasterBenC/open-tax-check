"""Adapters for bounded common financial and tax templates."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Any

from .numeric import format_money, parse_decimal
from .structured_documents import (
    financial_statement_from_dict,
    tax_return_from_dict,
)


@dataclass(frozen=True, slots=True)
class TemplateIssue:
    code: str
    row: int | None
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TemplateParseResult:
    payload: dict[str, Any] | None
    issues: tuple[TemplateIssue, ...]


_FINANCIAL_FIELDS = (
    "line_code", "line_name", "standard_name", "current_amount", "prior_amount",
)
_TAX_FIELDS = (
    "form_code", "line_number", "line_name", "fact_key", "value", "value_type",
)
_FINANCIAL_TOTAL_CODES = frozenset({
    "assets", "liabilities_and_equity", "revenue", "cost", "expenses", "taxes",
    "net_profit", "cash", "ending_cash",
})
_FORMULA = re.compile(r"^\s*=[^=].*$")
_HEADER_ALIASES = {
    "linecode": "line_code", "code": "line_code", "line": "line_code",
    "linename": "line_name", "name": "line_name", "originalname": "line_name",
    "standardname": "standard_name", "normalizedname": "standard_name",
    "canonicalname": "standard_name",
    "currentamount": "current_amount", "current": "current_amount",
    "currentbalance": "current_amount",
    "prioramount": "prior_amount", "prior": "prior_amount",
    "priorbalance": "prior_amount", "previousamount": "prior_amount",
    "formcode": "form_code", "form": "form_code",
    "linenumber": "line_number", "lineno": "line_number", "number": "line_number",
    "factkey": "fact_key", "fact": "fact_key",
    "value": "value", "valuetype": "value_type", "type": "value_type",
}


def _header_key(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().casefold()
    if not text:
        return None
    compact = re.sub(r"[^a-z0-9]+", "", text)
    return _HEADER_ALIASES.get(compact, compact if compact in {
        "line_code", "line_name", "standard_name", "current_amount", "prior_amount",
        "form_code", "line_number", "fact_key", "value_type",
    } else None)


def _column_name(index: int) -> str:
    index += 1
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _normalise_headers(
    cells: Sequence[object],
    required: Sequence[str],
    row_number: int | None,
) -> tuple[dict[str, int], tuple[TemplateIssue, ...]]:
    columns: dict[str, int] = {}
    issues: list[TemplateIssue] = []
    for index, value in enumerate(cells):
        if _blank(value):
            continue
        canonical = _header_key(value)
        if canonical is None or canonical not in required:
            issues.append(TemplateIssue("unknown_header", row_number, (str(value).strip(),)))
            continue
        if canonical in columns:
            issues.append(TemplateIssue("duplicate_header", row_number, (canonical,)))
            continue
        columns[canonical] = index
    missing = tuple(field for field in required if field not in columns)
    # Header diagnostics are already sufficient to block parsing; avoid
    # duplicating a missing-header report when the row has other header errors.
    if missing and not issues:
        issues.append(TemplateIssue("missing_required_header", row_number, missing))
    return columns, tuple(issues)


def _rows_and_headers(rows: Iterable[tuple[int, Sequence[object]]]) -> tuple[int | None, Sequence[object], list[tuple[int, Sequence[object]]]]:
    materialized = list(rows)
    if not materialized:
        return None, (), []
    header_row, header_cells = materialized[0]
    return header_row, header_cells, materialized[1:]


def _amount(value: object, row: int, field: str, issues: list[TemplateIssue]) -> str | None:
    if _blank(value):
        return None
    if isinstance(value, str) and _FORMULA.fullmatch(value):
        issues.append(TemplateIssue("formula_not_allowed", row, (field,)))
        return None
    try:
        parsed = parse_decimal(value)
        return format_money(Decimal(parsed))
    except (TypeError, ValueError):
        issues.append(TemplateIssue("invalid_amount", row, (field,)))
        return None


def _cell(cells: Sequence[object], index: int | None) -> object:
    if index is None or index >= len(cells):
        return None
    return cells[index]


def _anchor(source_id: str, sheet_name: str, row: int, column: int) -> dict[str, object]:
    return {
        "source_id": source_id,
        "page_number": None,
        "sheet_name": sheet_name,
        "cell": f"{_column_name(column)}{row}",
        "coordinates": None,
    }


def financial_statement_template(
    rows: Iterable[tuple[int, Sequence[object]]],
    *,
    source_id: str,
    entity_name: str,
    statement_id: str,
    statement_type: str,
    period_start: str,
    period_end: str,
    as_of_date: str | None = None,
    sheet_name: str = "Sheet1",
    header_row: int = 1,
) -> TemplateParseResult:
    actual_header_row, header_cells, business_rows = _rows_and_headers(rows)
    issue_row = actual_header_row if actual_header_row is not None else header_row
    columns, header_issues = _normalise_headers(header_cells, _FINANCIAL_FIELDS, issue_row)
    issues = list(header_issues)
    lines: list[dict[str, object]] = []
    totals: dict[str, str] = {}
    seen_codes: set[str] = set()
    for row_number, cells in business_rows:
        if all(_blank(value) for value in cells):
            continue
        line_code_value = _cell(cells, columns.get("line_code"))
        line_name_value = _cell(cells, columns.get("line_name"))
        standard_name_value = _cell(cells, columns.get("standard_name"))
        line_code = "" if _blank(line_code_value) else str(line_code_value).strip()
        line_name = "" if _blank(line_name_value) else str(line_name_value).strip()
        standard_name = "" if _blank(standard_name_value) else str(standard_name_value).strip()
        for field, value in (("line_code", line_code), ("line_name", line_name), ("standard_name", standard_name)):
            if not value:
                issues.append(TemplateIssue("missing_value", row_number, (field,)))
        current_amount = _amount(_cell(cells, columns.get("current_amount")), row_number, "current_amount", issues)
        prior_amount = _amount(_cell(cells, columns.get("prior_amount")), row_number, "prior_amount", issues)
        if not line_code or not line_name or not standard_name:
            continue
        if line_code in seen_codes:
            issues.append(TemplateIssue("duplicate_line_code", row_number, ("line_code",)))
        seen_codes.add(line_code)
        lines.append({
            "line_code": line_code,
            "original_name": line_name,
            "standard_name": standard_name,
            "current_amount": current_amount,
            "prior_amount": prior_amount,
            "source_anchor": _anchor(source_id, sheet_name, row_number, columns["line_code"]),
            "confidence_source": "template",
            "confirmation_state": "confirmed",
        })
        if line_code in _FINANCIAL_TOTAL_CODES and current_amount is not None:
            totals[line_code] = current_amount
    if issues:
        return TemplateParseResult(None, tuple(issues))
    payload: dict[str, object] = {
        "schema_version": "financial-statement/v1",
        "statement_id": statement_id,
        "source_id": source_id,
        "entity_name": entity_name,
        "statement_type": statement_type,
        "period_start": period_start,
        "period_end": period_end,
        "as_of_date": as_of_date,
        "currency": "CNY",
        "unit_multiplier": "1",
        "lines": lines,
        "totals": totals,
        "issues": [],
    }
    try:
        financial_statement_from_dict(payload)
    except (TypeError, ValueError):
        return TemplateParseResult(None, (TemplateIssue("invalid_contract", None, ()),))
    return TemplateParseResult(payload, ())


def tax_return_template(
    rows: Iterable[tuple[int, Sequence[object]]],
    *,
    source_id: str,
    entity_name: str,
    return_id: str,
    tax_id: str,
    return_type: str,
    period_start: str,
    period_end: str,
    filing_status: str = "unknown",
    filed_at: str | None = None,
    sheet_name: str = "Sheet1",
    header_row: int = 1,
) -> TemplateParseResult:
    actual_header_row, header_cells, business_rows = _rows_and_headers(rows)
    issue_row = actual_header_row if actual_header_row is not None else header_row
    columns, header_issues = _normalise_headers(header_cells, _TAX_FIELDS, issue_row)
    issues = list(header_issues)
    lines: list[dict[str, object]] = []
    seen_pairs: set[tuple[str, str]] = set()
    seen_facts: set[str] = set()
    for row_number, cells in business_rows:
        if all(_blank(value) for value in cells):
            continue
        raw_values = {field: _cell(cells, columns.get(field)) for field in _TAX_FIELDS}
        form_code = "" if _blank(raw_values["form_code"]) else str(raw_values["form_code"]).strip()
        line_number = "" if _blank(raw_values["line_number"]) else str(raw_values["line_number"]).strip()
        line_name = "" if _blank(raw_values["line_name"]) else str(raw_values["line_name"]).strip()
        fact_key = None if _blank(raw_values["fact_key"]) else str(raw_values["fact_key"]).strip()
        value_type = "" if _blank(raw_values["value_type"]) else str(raw_values["value_type"]).strip().casefold()
        for field, value in (("form_code", form_code), ("line_number", line_number), ("line_name", line_name), ("value_type", value_type)):
            if not value:
                issues.append(TemplateIssue("missing_value", row_number, (field,)))
        if value_type not in {"decimal", "text", "choice", "boolean", "empty"}:
            if value_type:
                issues.append(TemplateIssue("invalid_value_type", row_number, ("value_type",)))
            continue
        value = raw_values["value"]
        parsed_value: str | bool | None
        if value_type == "decimal":
            parsed_value = _amount(value, row_number, "value", issues)
            if parsed_value is None and not _blank(value):
                continue
            if parsed_value is None:
                issues.append(TemplateIssue("missing_value", row_number, ("value",)))
                continue
        elif value_type in {"text", "choice"}:
            if _blank(value):
                issues.append(TemplateIssue("missing_value", row_number, ("value",)))
                continue
            if not isinstance(value, str):
                issues.append(TemplateIssue("invalid_value", row_number, ("value",)))
                continue
            parsed_value = value.strip()
        elif value_type == "boolean":
            if not isinstance(value, bool):
                issues.append(TemplateIssue("invalid_value", row_number, ("value",)))
                continue
            parsed_value = value
        else:
            if not _blank(value):
                issues.append(TemplateIssue("invalid_value", row_number, ("value",)))
                continue
            parsed_value = None
        if not form_code or not line_number or not line_name:
            continue
        pair = (form_code, line_number)
        if pair in seen_pairs:
            issues.append(TemplateIssue("duplicate_line", row_number, ("form_code", "line_number")))
        seen_pairs.add(pair)
        if fact_key is not None:
            if fact_key in seen_facts:
                issues.append(TemplateIssue("duplicate_fact_key", row_number, ("fact_key",)))
            seen_facts.add(fact_key)
        lines.append({
            "form_code": form_code,
            "line_number": line_number,
            "original_name": line_name,
            "fact_key": fact_key,
            "value": parsed_value,
            "value_type": value_type,
            "source_anchor": _anchor(source_id, sheet_name, row_number, columns["form_code"]),
            "confidence_source": "template",
            "confirmation_state": "confirmed",
        })
    if issues:
        return TemplateParseResult(None, tuple(issues))
    payload: dict[str, object] = {
        "schema_version": "tax-return/v1",
        "return_id": return_id,
        "source_id": source_id,
        "entity_name": entity_name,
        "tax_id": tax_id,
        "return_type": return_type,
        "period_start": period_start,
        "period_end": period_end,
        "filing_status": filing_status,
        "filed_at": filed_at,
        "currency": "CNY",
        "unit_multiplier": "1",
        "lines": lines,
        "totals": {},
        "issues": [],
    }
    try:
        tax_return_from_dict(payload)
    except (TypeError, ValueError):
        return TemplateParseResult(None, (TemplateIssue("invalid_contract", None, ()),))
    return TemplateParseResult(payload, ())


__all__ = [
    "TemplateIssue", "TemplateParseResult", "financial_statement_template", "tax_return_template",
]
