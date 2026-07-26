"""Canonical, lineage-preserving normalization for confirmed journal sheets."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator

from .mapping import MAPPING_VERSION, ROLE_FIELDS
from .numeric import format_money, parse_decimal
from .quality import _normalize_date
from .sheet_detection import normalize_header, selected_headers
from .workbooks import CellValue, SheetMeta, WorkbookReader


@dataclass(frozen=True, slots=True)
class RowIssue:
    """A location-only normalization diagnostic that never exposes cell values."""

    code: str
    source_id: str | None = None
    sheet_id: str | None = None
    row: int | None = None
    fields: tuple[str, ...] = ()

    def with_location(
        self,
        source_id: str,
        sheet_id: str,
        row: int,
    ) -> RowIssue:
        return replace(self, source_id=source_id, sheet_id=sheet_id, row=row)


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    rows: tuple[dict, ...]
    issues: tuple[RowIssue, ...]


class _RowBlocked(Exception):
    def __init__(self, code: str, *fields: str) -> None:
        self.issue = RowIssue(code, fields=tuple(fields))


def field_lineage(
    cell: CellValue,
    *,
    source_id: str,
    sheet: SheetMeta,
    mapping_version: str,
    value_origin: str | None = None,
) -> dict:
    """Return the exact immutable source location for a canonical field."""

    return {
        "source_id": source_id,
        "sheet_id": sheet.sheet_id,
        "sheet_name": sheet.name,
        "row": cell.row,
        "column": cell.column,
        "coordinate": cell.coordinate,
        "original_type": cell.data_type,
        "value_origin": value_origin or cell.value_origin,
        "mapping_version": mapping_version,
    }


def _effective_value(cell: CellValue, *, field: str) -> Any:
    if cell.formula is not None:
        if cell.cached_value is None:
            raise _RowBlocked("missing_cached_formula_value", field)
        return cell.cached_value
    return cell.value


def normalize_money(cell: CellValue, *, required: bool) -> tuple[str, str]:
    """Normalize a literal or cached formula amount without leaking its source."""

    value = _effective_value(cell, field="amount")
    if value in (None, ""):
        if required:
            raise _RowBlocked("missing_required_value", "amount")
        return "0.00", cell.value_origin
    try:
        return format_money(parse_decimal(value)), cell.value_origin
    except (InvalidOperation, TypeError, ValueError):
        raise _RowBlocked("invalid_money_value", "amount") from None


def _selection_value(selection: dict, key: str) -> Any:
    binding = selection.get("binding") if isinstance(selection, dict) else None
    if key in selection:
        return selection[key]
    return binding.get(key) if isinstance(binding, dict) else None


def _selection_source_id(selection: dict) -> str | None:
    source_id = _selection_value(selection, "source_id")
    if isinstance(source_id, str) and source_id:
        return source_id
    digest = _selection_value(selection, "source_sha256")
    return digest[:16] if isinstance(digest, str) and len(digest) >= 16 else None


def _mapping_version(mapping: dict) -> str | None:
    binding = mapping.get("binding") if isinstance(mapping, dict) else None
    version = (
        binding.get("mapping_schema_version")
        if isinstance(binding, dict)
        else mapping.get("mapping_version") if isinstance(mapping, dict) else None
    )
    return version if isinstance(version, str) and version else None


def _configuration_issue(
    selection: dict,
    mapping: dict,
    review_period: dict,
) -> RowIssue | None:
    if not isinstance(selection, dict) or _selection_value(selection, "hidden_rows") not in {"include", "exclude"}:
        return RowIssue("hidden_row_decision_required")
    if (
        not isinstance(_selection_source_id(selection), str)
        or not isinstance(_selection_value(selection, "sheet_id"), str)
        or not isinstance(_selection_value(selection, "header_rows"), list)
        or not _selection_value(selection, "header_rows")
    ):
        return RowIssue("invalid_sheet_selection")
    if not isinstance(mapping, dict) or not isinstance(mapping.get("mapping"), dict):
        return RowIssue("invalid_mapping_confirmation")
    if _mapping_version(mapping) is None:
        return RowIssue("invalid_mapping_confirmation")
    if not isinstance(review_period, dict) or set(review_period) != {"start", "end"}:
        return RowIssue("invalid_review_period")
    try:
        _normalize_date(review_period["start"])
        _normalize_date(review_period["end"])
    except (TypeError, ValueError):
        return RowIssue("invalid_review_period")
    return None


def _selected_sheet(reader: WorkbookReader, selection: dict) -> SheetMeta | None:
    sheet_id = _selection_value(selection, "sheet_id")
    sheet_name = _selection_value(selection, "sheet_name")
    for sheet in reader.sheets():
        if sheet.sheet_id == sheet_id and (sheet_name is None or sheet.name == sheet_name):
            return sheet
    return None


def _source_columns(headers: tuple[str, ...], mapping: dict) -> dict[str, int] | None:
    source_mapping = mapping["mapping"]
    targets = set(ROLE_FIELDS["journal"]["required"]) | set(ROLE_FIELDS["journal"]["optional"])
    columns: dict[str, int] = {}
    for index, header in enumerate(headers, start=1):
        canonical = source_mapping.get(header)
        if canonical not in targets:
            continue
        if canonical in columns:
            return None
        columns[canonical] = index
    if not set(ROLE_FIELDS["journal"]["required"]).issubset(columns):
        return None
    return columns


def iter_data_rows(
    reader: WorkbookReader,
    selection: dict,
    sheet: SheetMeta,
) -> Iterator[tuple[CellValue, ...]]:
    """Iterate only the non-header data range of an already confirmed sheet."""

    header_rows = _selection_value(selection, "header_rows")
    yield from reader.iter_rows(
        sheet.sheet_id,
        max(header_rows) + 1,
        sheet.max_row,
        sheet.max_column,
    )


def _is_blank_business_row(cells: dict[str, CellValue]) -> bool:
    for cell in cells.values():
        if cell.formula is not None or cell.value not in (None, ""):
            return False
    return True


def _normalize_date_cell(cell: CellValue) -> tuple[str, str, str]:
    value = _effective_value(cell, field="date")
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        parsed = value
        return parsed.isoformat(), f"{parsed.year:04d}-{parsed.month:02d}", f"{parsed.year:04d}"
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        try:
            parsed = (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
        except (OverflowError, ValueError):
            raise _RowBlocked("unsupported_posting_date", "date") from None
        return parsed.isoformat(), f"{parsed.year:04d}-{parsed.month:02d}", f"{parsed.year:04d}"
    try:
        return _normalize_date(value)
    except (TypeError, ValueError):
        raise _RowBlocked("unsupported_posting_date", "date") from None


def _text_value(cell: CellValue, field: str) -> str:
    value = _effective_value(cell, field=field)
    return "" if value is None else str(value).strip()


def _build_journal_row(
    source_row: tuple[CellValue, ...],
    columns: dict[str, int],
    *,
    source_id: str,
    sheet: SheetMeta,
    mapping_version: str,
    review_period: dict,
) -> dict:
    cells = {field: source_row[index - 1] for field, index in columns.items()}
    if _is_blank_business_row(cells):
        return {}
    normalized = {}
    lineage = {}
    for field, cell in cells.items():
        if field in {"debit", "credit"}:
            try:
                value, _ = normalize_money(cell, required=False)
            except _RowBlocked as error:
                raise _RowBlocked(error.issue.code, field) from None
        elif field == "date":
            value, period, tax_year = _normalize_date_cell(cell)
            normalized["period"] = period
            normalized["tax_year"] = tax_year
        else:
            value = _text_value(cell, field)
        normalized[field] = value
        lineage[field] = field_lineage(
            cell,
            source_id=source_id,
            sheet=sheet,
            mapping_version=mapping_version,
        )
    start, _, _ = _normalize_date(review_period["start"])
    end, _, _ = _normalize_date(review_period["end"])
    if not start <= normalized["date"] <= end:
        raise _RowBlocked("date_outside_review_period", "date")
    normalized["field_lineage"] = lineage
    return normalized


def normalize_journal(
    reader: WorkbookReader,
    selection: dict,
    mapping: dict,
    review_period: dict,
) -> NormalizationResult:
    """Normalize one explicitly selected journal sheet with per-field lineage."""

    configuration_error = _configuration_issue(selection, mapping, review_period)
    if configuration_error is not None:
        return NormalizationResult((), (configuration_error,))
    sheet = _selected_sheet(reader, selection)
    if sheet is None:
        return NormalizationResult((), (RowIssue("selected_sheet_unavailable"),))
    try:
        headers = selected_headers(reader, sheet, _selection_value(selection, "header_rows"))
    except (TypeError, ValueError):
        return NormalizationResult((), (RowIssue("selected_headers_unavailable"),))
    columns = _source_columns(headers, mapping)
    if columns is None:
        return NormalizationResult((), (RowIssue("mapping_headers_unavailable"),))

    source_id = _selection_source_id(selection)
    rows: list[dict] = []
    issues: list[RowIssue] = []
    for source_row in iter_data_rows(reader, selection, sheet):
        try:
            normalized = _build_journal_row(
                source_row,
                columns,
                source_id=source_id,
                sheet=sheet,
                mapping_version=_mapping_version(mapping) or MAPPING_VERSION,
                review_period=review_period,
            )
        except _RowBlocked as error:
            row_number = source_row[0].row if source_row else None
            if row_number is not None:
                issues.append(error.issue.with_location(source_id, sheet.sheet_id, row_number))
            else:
                issues.append(error.issue)
            continue
        if normalized:
            rows.append(normalized)
    return NormalizationResult(tuple(rows), tuple(issues))


def _trial_balance_configuration_issue(selection: dict, mapping: dict) -> RowIssue | None:
    if (
        not isinstance(selection, dict)
        or _selection_value(selection, "hidden_rows") not in {"include", "exclude"}
    ):
        return RowIssue("hidden_row_decision_required")
    if (
        not isinstance(_selection_source_id(selection), str)
        or not isinstance(_selection_value(selection, "sheet_id"), str)
        or not isinstance(_selection_value(selection, "header_rows"), list)
        or not _selection_value(selection, "header_rows")
    ):
        return RowIssue("invalid_sheet_selection")
    if (
        not isinstance(mapping, dict)
        or not isinstance(mapping.get("mapping"), dict)
        or _mapping_version(mapping) is None
    ):
        return RowIssue("invalid_mapping_confirmation")
    return None


_DIRECTION_ALIASES = {
    "借": "debit",
    "借方": "debit",
    "d": "debit",
    "贷": "credit",
    "贷方": "credit",
    "c": "credit",
}


def _trial_balance_source_columns(
    headers: tuple[str, ...],
    mapping: dict,
) -> tuple[dict[str, int], dict[str, tuple[int, int, str, str]]] | None:
    targets = set(ROLE_FIELDS["trial_balance"]["required"]) | set(
        ROLE_FIELDS["trial_balance"]["optional"]
    )
    columns: dict[str, int] = {}
    for index, header in enumerate(headers, start=1):
        canonical = mapping["mapping"].get(header)
        if canonical not in targets:
            continue
        if canonical in columns:
            return None
        columns[canonical] = index

    composites = mapping.get("composites", {})
    if not isinstance(composites, dict):
        return None
    composite_columns: dict[str, tuple[int, int, str, str]] = {}
    expected_targets = {
        "opening": ("opening_debit", "opening_credit"),
        "closing": ("closing_debit", "closing_credit"),
    }
    for scope, composite in composites.items():
        if (
            scope not in expected_targets
            or not isinstance(composite, dict)
            or set(composite)
            != {"amount_field", "direction_field", "debit_field", "credit_field"}
        ):
            return None
        amount_field = composite["amount_field"]
        direction_field = composite["direction_field"]
        debit_field = composite["debit_field"]
        credit_field = composite["credit_field"]
        if (
            not all(
                isinstance(value, str) and value
                for value in (amount_field, direction_field, debit_field, credit_field)
            )
            or amount_field == direction_field
            or (debit_field, credit_field) != expected_targets[scope]
            or debit_field in columns
            or credit_field in columns
        ):
            return None
        amount_indexes = [
            index for index, header in enumerate(headers, start=1) if header == amount_field
        ]
        direction_indexes = [
            index
            for index, header in enumerate(headers, start=1)
            if header == direction_field
        ]
        if len(amount_indexes) != 1 or len(direction_indexes) != 1:
            return None
        composite_columns[scope] = (
            amount_indexes[0],
            direction_indexes[0],
            debit_field,
            credit_field,
        )

    derived_targets = {
        target
        for _, _, debit_field, credit_field in composite_columns.values()
        for target in (debit_field, credit_field)
    }
    if not set(ROLE_FIELDS["trial_balance"]["required"]).issubset(
        set(columns) | derived_targets
    ):
        return None
    return columns, composite_columns


def _build_trial_balance_row(
    source_row: tuple[CellValue, ...],
    columns: dict[str, int],
    composites: dict[str, tuple[int, int, str, str]],
    *,
    source_id: str,
    sheet: SheetMeta,
    mapping_version: str,
) -> dict:
    cells = {field: source_row[index - 1] for field, index in columns.items()}
    composite_cells = {
        f"{scope}_{kind}": source_row[index - 1]
        for scope, details in composites.items()
        for kind, index in (("amount", details[0]), ("direction", details[1]))
    }
    if _is_blank_business_row({**cells, **composite_cells}):
        return {}

    account_name = _text_value(cells["account_name"], "account_name")
    if not account_name:
        raise _RowBlocked("missing_required_value", "account_name")
    if any(marker in normalize_header(account_name) for marker in ("合计", "总计")):
        raise _RowBlocked("excluded_total_row", "account_name")

    normalized: dict[str, str] = {}
    lineage: dict[str, dict] = {}
    for field, cell in cells.items():
        if field.endswith("_debit") or field.endswith("_credit"):
            try:
                value, _ = normalize_money(cell, required=False)
            except _RowBlocked as error:
                raise _RowBlocked(error.issue.code, field) from None
        else:
            value = _text_value(cell, field)
        normalized[field] = value
        lineage[field] = field_lineage(
            cell,
            source_id=source_id,
            sheet=sheet,
            mapping_version=mapping_version,
        )
    for amount_index, direction_index, debit_field, credit_field in composites.values():
        amount_cell = source_row[amount_index - 1]
        direction_cell = source_row[direction_index - 1]
        try:
            amount_text, _ = normalize_money(amount_cell, required=False)
            amount = parse_decimal(amount_text)
            side = _DIRECTION_ALIASES.get(
                normalize_header(_effective_value(direction_cell, field=debit_field))
            )
        except _RowBlocked as error:
            raise _RowBlocked(error.issue.code, debit_field, credit_field) from None
        if side is None:
            raise _RowBlocked("invalid_balance_direction", debit_field, credit_field)
        debit = abs(amount) if side == "debit" else Decimal("0")
        credit = abs(amount) if side == "credit" else Decimal("0")
        normalized[debit_field] = format_money(debit)
        normalized[credit_field] = format_money(credit)
        for field in (debit_field, credit_field):
            lineage[field] = field_lineage(
                amount_cell,
                source_id=source_id,
                sheet=sheet,
                mapping_version=mapping_version,
                value_origin="derived_direction_split",
            )
    normalized["field_lineage"] = lineage
    return normalized


def normalize_trial_balance(
    reader: WorkbookReader,
    selection: dict,
    mapping: dict,
) -> NormalizationResult:
    """Normalize one confirmed split debit/credit trial-balance sheet."""

    configuration_error = _trial_balance_configuration_issue(selection, mapping)
    if configuration_error is not None:
        return NormalizationResult((), (configuration_error,))
    sheet = _selected_sheet(reader, selection)
    if sheet is None:
        return NormalizationResult((), (RowIssue("selected_sheet_unavailable"),))
    try:
        headers = selected_headers(reader, sheet, _selection_value(selection, "header_rows"))
    except (TypeError, ValueError):
        return NormalizationResult((), (RowIssue("selected_headers_unavailable"),))
    source_columns = _trial_balance_source_columns(headers, mapping)
    if source_columns is None:
        return NormalizationResult((), (RowIssue("mapping_headers_unavailable"),))
    columns, composites = source_columns

    source_id = _selection_source_id(selection)
    rows: list[dict] = []
    issues: list[RowIssue] = []
    for source_row in iter_data_rows(reader, selection, sheet):
        try:
            normalized = _build_trial_balance_row(
                source_row,
                columns,
                composites,
                source_id=source_id,
                sheet=sheet,
                mapping_version=_mapping_version(mapping) or MAPPING_VERSION,
            )
        except _RowBlocked as error:
            row_number = source_row[0].row if source_row else None
            if row_number is not None:
                issues.append(error.issue.with_location(source_id, sheet.sheet_id, row_number))
            else:
                issues.append(error.issue)
            continue
        if normalized:
            rows.append(normalized)
    return NormalizationResult(tuple(rows), tuple(issues))
