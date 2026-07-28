"""Deterministic, bounded worksheet header and evidence-role detection."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Sequence
import unicodedata

from open_tax_check.mapping import HEADER_ALIASES
from open_tax_check.schemas import RoleScore
from open_tax_check.workbooks import CellValue, SheetMeta, WorkbookReader


MAX_HEADER_COLUMNS = 256
MAX_NONEMPTY_HEADER_ROWS = 100
NORMALIZATION_ROLES = frozenset({"journal", "trial_balance"})
ROLE_ORDER = (
    "journal",
    "trial_balance",
    "balance_sheet",
    "income_statement",
    "vat_return",
    "cit_return",
    "cash_flow_statement",
    "chart_of_accounts",
    "supporting_schedule",
)

_HEADER_PUNCTUATION = re.compile(r"[\s_\-—（）()\[\]【】]+")
_A1_RANGE = re.compile(
    r"^\$?([A-Za-z]+)\$?([1-9]\d*)"
    r"(?::\$?([A-Za-z]+)\$?([1-9]\d*))?$"
)


def normalize_header(value: object) -> str:
    """Return the stable comparison form used by header detection."""

    source = "" if value is None else str(value)
    text = unicodedata.normalize("NFKC", source).strip().casefold()
    return _HEADER_PUNCTUATION.sub("", text)


def confidence_band(score: int) -> str:
    """Map a numeric role score to the public confidence band."""

    if score >= 85:
        return "high"
    if score >= 60:
        return "review"
    return "none"


@dataclass(frozen=True, slots=True)
class SheetDetection:
    """The best header candidate and deterministic role evidence for a sheet."""

    header_rows: tuple[int, ...]
    combined_headers: tuple[str, ...]
    role_scores: tuple[RoleScore, ...]
    visibility: str
    auto_selectable: bool
    candidate_header_rows: tuple[tuple[int, ...], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "header_rows", tuple(self.header_rows))
        object.__setattr__(
            self,
            "combined_headers",
            tuple(self.combined_headers),
        )
        object.__setattr__(self, "role_scores", tuple(self.role_scores))
        object.__setattr__(
            self,
            "candidate_header_rows",
            tuple(tuple(rows) for rows in self.candidate_header_rows),
        )

    def score_for(self, role: str) -> int:
        """Return a role's score, or zero for an unsupported role."""

        for item in self.role_scores:
            if item.role == role:
                return item.score
        return 0

    def auto_selectable_for_normalization(self, role: str) -> bool:
        """Whether this visible sheet can normalize the requested role."""

        return (
            role in NORMALIZATION_ROLES
            and self.visibility == "visible"
            and self.score_for(role) >= 85
        )


@dataclass(frozen=True, slots=True)
class _ObservedRow:
    number: int
    values: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    header_rows: tuple[int, ...]
    combined_headers: tuple[str, ...]
    role_scores: tuple[RoleScore, ...]

    @property
    def highest_score(self) -> int:
        return max((item.score for item in self.role_scores), default=0)


_NORMALIZED_JOURNAL_ALIASES = {
    canonical: frozenset(normalize_header(alias) for alias in aliases)
    for canonical, aliases in HEADER_ALIASES.items()
}

_ACCOUNT_NAME_ALIASES = _NORMALIZED_JOURNAL_ALIASES["account_name"]
_OPENING_SCOPES = (
    "期初",
    "年初",
    "opening",
    "beginning",
)
_PERIOD_SCOPES = (
    "本期",
    "期间",
    "period",
    "currentperiod",
)
_CLOSING_SCOPES = (
    "期末",
    "年末",
    "closing",
    "ending",
)
_DEBIT_MARKERS = ("借方", "debit")
_CREDIT_MARKERS = ("贷方", "credit")
_DIRECTION_MARKERS = ("方向", "direction", "drcr", "借贷")
_AMOUNT_MARKERS = ("金额", "余额", "amount", "balance")
_TB_FALLBACK_CHILD_HEADERS = frozenset(
    normalize_header(item)
    for item in (
        "借方",
        "贷方",
        "金额",
        "方向",
        "借贷方向",
        "余额方向",
        "借方金额",
        "贷方金额",
        "debit",
        "credit",
        "amount",
        "direction",
        "drcr",
        "debit amount",
        "credit amount",
    )
)


_INVENTORY_RULES = {
    "balance_sheet": (
        ("document_title", 65, ("资产负债表", "balancesheet")),
        ("asset_header", 15, ("资产", "assets")),
        ("liability_header", 15, ("负债", "liabilities")),
        ("equity_header", 15, ("所有者权益", "股东权益", "equity")),
        ("balance_period", 10, ("期末余额", "年初余额")),
    ),
    "income_statement": (
        (
            "document_title",
            65,
            ("利润表", "损益表", "incomestatement", "profitandloss"),
        ),
        ("revenue_header", 15, ("营业收入", "主营业务收入", "revenue")),
        ("cost_header", 10, ("营业成本", "主营业务成本", "cost")),
        ("profit_header", 15, ("利润总额", "净利润", "profit")),
    ),
    "vat_return": (
        (
            "document_title",
            70,
            ("增值税纳税申报表", "增值税申报表", "vatreturn"),
        ),
        ("output_vat", 20, ("销项税额", "outputvat")),
        ("input_vat", 20, ("进项税额", "inputvat")),
        ("vat_payable", 10, ("应纳税额", "vatpayable")),
    ),
    "cit_return": (
        (
            "document_title",
            70,
            (
                "企业所得税年度纳税申报表",
                "企业所得税纳税申报表",
                "corporateincometaxreturn",
                "citreturn",
            ),
        ),
        (
            "taxable_income",
            20,
            ("应纳税所得额", "纳税调整后所得", "taxableincome"),
        ),
        ("income_tax_payable", 20, ("应纳所得税额", "incometaxpayable")),
    ),
    "cash_flow_statement": (
        (
            "document_title",
            65,
            ("现金流量表", "cashflowstatement"),
        ),
        (
            "operating_cash_flow",
            15,
            ("经营活动现金流入", "经营活动产生的现金流量"),
        ),
        (
            "cash_increase",
            20,
            ("现金及现金等价物净增加额", "netincreaseincash"),
        ),
    ),
    "chart_of_accounts": (
        (
            "document_title",
            65,
            ("会计科目表", "科目表", "chartofaccounts"),
        ),
        ("account_code", 15, ("科目编码", "科目代码")),
        ("account_name", 15, ("科目名称", "会计科目")),
        ("account_level", 10, ("科目级次", "科目层级")),
    ),
    "supporting_schedule": (
        (
            "schedule_title",
            60,
            ("明细表", "明细账", "台账", "清单", "schedule", "register"),
        ),
        (
            "schedule_subject",
            30,
            (
                "固定资产",
                "账龄",
                "关联交易",
                "工资薪金",
                "研发费用",
                "业务招待",
                "广告费",
            ),
        ),
        (
            "schedule_detail",
            15,
            ("累计折旧", "资产原值", "账面余额", "发生金额"),
        ),
    ),
}


def detect_rows(
    rows: Iterable[Sequence[object]],
    *,
    visibility: str = "visible",
    merged_ranges: Sequence[str] = (),
) -> SheetDetection:
    """Detect the best 1-3 row header candidate from a bounded row stream."""

    observed = _first_nonempty_rows(rows)
    candidates = _evaluate_candidates(observed, merged_ranges)
    if candidates:
        best = min(
            candidates,
            key=lambda item: (
                -item.highest_score,
                len(item.header_rows),
                item.header_rows[0],
                item.header_rows,
            ),
        )
        header_rows = best.header_rows
        combined_headers = best.combined_headers
        role_scores = best.role_scores
    else:
        header_rows = ()
        combined_headers = ()
        role_scores = _score_roles(())

    auto_selectable = (
        visibility == "visible"
        and any(
            item.role in NORMALIZATION_ROLES and item.score >= 85
            for item in role_scores
        )
    )
    return SheetDetection(
        header_rows=header_rows,
        combined_headers=combined_headers,
        role_scores=role_scores,
        visibility=visibility,
        auto_selectable=auto_selectable,
        candidate_header_rows=tuple(
            item.header_rows for item in candidates
        ),
    )


def detect_sheet(
    reader: WorkbookReader,
    sheet: SheetMeta,
    *,
    merged_ranges: Sequence[str] = (),
) -> SheetDetection:
    """Detect a sheet using only the Task 4 read-only row interface."""

    max_col = min(max(sheet.max_column, 0), MAX_HEADER_COLUMNS)
    if sheet.max_row <= 0 or max_col <= 0:
        return detect_rows(
            (),
            visibility=sheet.visibility,
            merged_ranges=merged_ranges,
        )
    rows = reader.iter_rows(
        sheet.sheet_id,
        1,
        sheet.max_row,
        max_col,
    )
    return detect_rows(
        rows,
        visibility=sheet.visibility,
        merged_ranges=merged_ranges,
    )


def selected_headers(
    reader: WorkbookReader,
    sheet: SheetMeta,
    header_rows: Sequence[int],
) -> tuple[str, ...]:
    """Return combined headers from the explicitly selected worksheet rows."""

    return tuple(
        header
        for _, header in selected_header_columns(reader, sheet, header_rows)
    )


def selected_header_columns(
    reader: WorkbookReader,
    sheet: SheetMeta,
    header_rows: Sequence[int],
) -> tuple[tuple[int, str], ...]:
    """Return each nonempty selected header with its physical worksheet column."""

    rows = tuple(header_rows)
    if (
        not rows
        or any(row < 1 for row in rows)
        or any(current != previous + 1 for previous, current in zip(rows, rows[1:]))
    ):
        raise ValueError("invalid_header_rows")
    max_col = min(max(sheet.max_column, 0), MAX_HEADER_COLUMNS)
    if rows[-1] > sheet.max_row or max_col < 1:
        return ()
    source_rows = {}
    selected = []
    for row_number, row in enumerate(
        reader.iter_rows(sheet.sheet_id, 1, rows[-1], max_col),
        start=1,
    ):
        values = tuple(_cell_value(cell) for cell in row[:MAX_HEADER_COLUMNS])
        source_rows[row_number] = values
        if row_number in rows:
            selected.append(_ObservedRow(row_number, values))
    if len(selected) != len(rows):
        raise ValueError("invalid_header_rows")
    parsed_merges = tuple(
        parsed
        for item in sheet.merged_ranges
        if (parsed := _parse_merged_range(item)) is not None
    )
    return _combine_header_columns(tuple(selected), source_rows, parsed_merges)


def _first_nonempty_rows(
    rows: Iterable[Sequence[object]],
) -> tuple[_ObservedRow, ...]:
    observed = []
    iterator = iter(rows)
    try:
        for row_number, row in enumerate(iterator, start=1):
            values = tuple(
                _cell_value(cell)
                for cell in tuple(row)[:MAX_HEADER_COLUMNS]
            )
            if not any(normalize_header(value) for value in values):
                continue
            observed.append(_ObservedRow(row_number, values))
            if len(observed) == MAX_NONEMPTY_HEADER_ROWS:
                break
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()
    return tuple(observed)


def _cell_value(cell: object) -> object:
    if isinstance(cell, CellValue):
        return cell.value
    return cell


def _evaluate_candidates(
    observed: Sequence[_ObservedRow],
    merged_ranges: Sequence[str],
) -> tuple[_Candidate, ...]:
    parsed_merges = tuple(
        parsed
        for item in merged_ranges
        if (parsed := _parse_merged_range(item)) is not None
    )
    source_rows = {row.number: row.values for row in observed}
    candidates = []
    for start in range(len(observed)):
        for length in range(1, 4):
            selected = observed[start : start + length]
            if len(selected) != length:
                break
            if any(
                right.number != left.number + 1
                for left, right in zip(selected, selected[1:])
            ):
                break
            combined = _combine_headers(
                selected,
                source_rows,
                parsed_merges,
            )
            candidates.append(
                _Candidate(
                    header_rows=tuple(row.number for row in selected),
                    combined_headers=combined,
                    role_scores=_score_roles(combined),
                )
            )
    return tuple(candidates)


def _combine_headers(
    selected: Sequence[_ObservedRow],
    source_rows: dict[int, tuple[object, ...]],
    merged_ranges: Sequence[tuple[int, int, int, int]],
) -> tuple[str, ...]:
    return tuple(
        header
        for _, header in _combine_header_columns(
            selected,
            source_rows,
            merged_ranges,
        )
    )


def _combine_header_columns(
    selected: Sequence[_ObservedRow],
    source_rows: dict[int, tuple[object, ...]],
    merged_ranges: Sequence[tuple[int, int, int, int]],
) -> tuple[tuple[int, str], ...]:
    selected_numbers = {row.number for row in selected}
    width = max((len(row.values) for row in selected), default=0)
    for min_row, min_col, max_row, max_col in merged_ranges:
        if selected_numbers.intersection(range(min_row, max_row + 1)):
            width = max(width, min(max_col, MAX_HEADER_COLUMNS))
    headers = []
    fallback_parents: list[object | None] = [None] * len(selected)
    for column in range(1, min(width, MAX_HEADER_COLUMNS) + 1):
        components = []
        normalized_components = set()
        for row_index, row in enumerate(selected):
            value = _header_cell_value(
                row.number,
                column,
                source_rows,
                merged_ranges,
            )
            normalized = normalize_header(value)
            if normalized:
                fallback_parents[row_index] = (
                    value
                    if row_index < len(selected) - 1
                    and _balance_scope(normalized) is not None
                    else None
                )
            elif row_index < len(selected) - 1:
                lower_headers = _lower_header_components(
                    selected,
                    row_index,
                    column,
                    source_rows,
                    merged_ranges,
                )
                if (
                    lower_headers
                    and all(
                        item in _TB_FALLBACK_CHILD_HEADERS
                        for item in lower_headers
                    )
                ):
                    value = fallback_parents[row_index]
                else:
                    fallback_parents[row_index] = None
            normalized = normalize_header(value)
            if not normalized or normalized in normalized_components:
                continue
            components.append(str(value).strip())
            normalized_components.add(normalized)
        if components:
            headers.append((column, "|".join(components)))
    return tuple(headers)


def _lower_header_components(
    selected: Sequence[_ObservedRow],
    row_index: int,
    column: int,
    source_rows: dict[int, tuple[object, ...]],
    merged_ranges: Sequence[tuple[int, int, int, int]],
) -> tuple[str, ...]:
    return tuple(
        normalized
        for lower in selected[row_index + 1 :]
        if (
            normalized := normalize_header(
                _header_cell_value(
                    lower.number,
                    column,
                    source_rows,
                    merged_ranges,
                )
            )
        )
    )


def _header_cell_value(
    row: int,
    column: int,
    source_rows: dict[int, tuple[object, ...]],
    merged_ranges: Sequence[tuple[int, int, int, int]],
) -> object:
    for min_row, min_col, max_row, max_col in merged_ranges:
        if min_row <= row <= max_row and min_col <= column <= max_col:
            return _source_value(source_rows, min_row, min_col)
    return _source_value(source_rows, row, column)


def _source_value(
    source_rows: dict[int, tuple[object, ...]],
    row: int,
    column: int,
) -> object:
    values = source_rows.get(row, ())
    if column <= len(values):
        return values[column - 1]
    return None


def _parse_merged_range(
    value: str,
) -> tuple[int, int, int, int] | None:
    matched = _A1_RANGE.fullmatch(str(value).strip())
    if matched is None:
        return None
    start_col = _column_number(matched.group(1))
    start_row = int(matched.group(2))
    end_col = _column_number(matched.group(3) or matched.group(1))
    end_row = int(matched.group(4) or matched.group(2))
    return (
        min(start_row, end_row),
        min(start_col, end_col),
        max(start_row, end_row),
        max(start_col, end_col),
    )


def _column_number(letters: str) -> int:
    number = 0
    for letter in letters.upper():
        number = number * 26 + ord(letter) - ord("A") + 1
    return number


def _score_roles(headers: Sequence[str]) -> tuple[RoleScore, ...]:
    scores = {
        "journal": _score_journal(headers),
        "trial_balance": _score_trial_balance(headers),
    }
    scores.update(
        {
            role: _score_inventory_role(role, headers)
            for role in _INVENTORY_RULES
        }
    )
    return tuple(scores[role] for role in ROLE_ORDER)


def _normalized_components(header: str) -> tuple[str, ...]:
    return tuple(
        normalized
        for component in str(header).split("|")
        if (normalized := normalize_header(component))
    )


def _score_journal(headers: Sequence[str]) -> RoleScore:
    hits = {canonical: [] for canonical in HEADER_ALIASES}
    for index, header in enumerate(headers):
        components = set(_normalized_components(header))
        normalized = normalize_header(header)
        for canonical, aliases in _NORMALIZED_JOURNAL_ALIASES.items():
            if normalized in aliases or components.intersection(aliases):
                hits[canonical].append(index)

    required = ("voucher_no", "date", "account_name", "debit", "credit")
    optional = ("account_code", "summary", "counterparty")
    required_present = sum(bool(hits[field]) for field in required)
    optional_unique = sum(len(hits[field]) == 1 for field in optional)
    conflicts = (
        any(len(indexes) > 1 for indexes in hits.values())
        or _has_source_target_conflict(hits, required)
    )
    complete = all(len(hits[field]) == 1 for field in required)
    score = min(100, required_present * 18 + optional_unique * 2)
    signals = [
        f"journal_field:{field}"
        for field in required + optional
        if hits[field]
    ]
    if complete and not conflicts:
        signals.append("required_headers_complete")
    if conflicts:
        score = min(score, 84)
        signals.append("conflicting_header_signal")
    return RoleScore("journal", score, tuple(signals))


def _score_trial_balance(headers: Sequence[str]) -> RoleScore:
    target_hits: dict[str, list[int]] = {
        "account_name": [],
        "opening_debit": [],
        "opening_credit": [],
        "opening_amount": [],
        "opening_direction": [],
        "period_debit": [],
        "period_credit": [],
        "closing_debit": [],
        "closing_credit": [],
        "closing_amount": [],
        "closing_direction": [],
    }
    for index, header in enumerate(headers):
        components = _normalized_components(header)
        normalized = "".join(components)
        if any(component in _ACCOUNT_NAME_ALIASES for component in components):
            target_hits["account_name"].append(index)
        scope = _balance_scope(normalized)
        if scope is None:
            continue
        has_debit = _contains_any(normalized, _DEBIT_MARKERS)
        has_credit = _contains_any(normalized, _CREDIT_MARKERS)
        if has_debit:
            target_hits[f"{scope}_debit"].append(index)
        if has_credit:
            target_hits[f"{scope}_credit"].append(index)
        if scope in {"opening", "closing"} and not (
            has_debit or has_credit
        ):
            if _contains_any(normalized, _DIRECTION_MARKERS):
                target_hits[f"{scope}_direction"].append(index)
            elif _contains_any(normalized, _AMOUNT_MARKERS):
                target_hits[f"{scope}_amount"].append(index)

    unique = {
        target: len(indexes) == 1
        for target, indexes in target_hits.items()
    }
    opening_split = unique["opening_debit"] and unique["opening_credit"]
    opening_pair = unique["opening_amount"] and unique["opening_direction"]
    closing_split = unique["closing_debit"] and unique["closing_credit"]
    closing_pair = unique["closing_amount"] and unique["closing_direction"]
    opening_complete = opening_split or opening_pair
    closing_complete = closing_split or closing_pair
    period_complete = unique["period_debit"] and unique["period_credit"]
    alternative_conflict = (
        (opening_split and opening_pair)
        or (closing_split and closing_pair)
    )
    duplicate_conflict = any(
        len(indexes) > 1 for indexes in target_hits.values()
    )
    source_target_conflict = _has_source_target_conflict(target_hits)
    conflicts = (
        alternative_conflict
        or duplicate_conflict
        or source_target_conflict
    )

    score = 10 if target_hits["account_name"] else 0
    score += 15 if target_hits["period_debit"] else 0
    score += 15 if target_hits["period_credit"] else 0
    score += _balance_representation_score(
        target_hits,
        "opening",
        opening_complete,
    )
    score += _balance_representation_score(
        target_hits,
        "closing",
        closing_complete,
    )
    score = min(score, 100)

    complete = (
        unique["account_name"]
        and period_complete
        and opening_complete
        and closing_complete
        and not conflicts
    )
    signals = [
        f"trial_balance_field:{target}"
        for target, indexes in target_hits.items()
        if indexes
    ]
    if complete:
        signals.append("required_headers_complete")
    if conflicts:
        score = min(score, 84)
        signals.append("conflicting_header_signal")
    return RoleScore("trial_balance", score, tuple(signals))


def _has_source_target_conflict(
    hits: dict[str, list[int]],
    targets: Sequence[str] | None = None,
) -> bool:
    source_targets: dict[int, set[str]] = {}
    for target in targets or hits:
        for source_index in hits[target]:
            source_targets.setdefault(source_index, set()).add(target)
    return any(len(mapped_targets) > 1 for mapped_targets in source_targets.values())


def _balance_scope(normalized: str) -> str | None:
    for scope, markers in (
        ("opening", _OPENING_SCOPES),
        ("period", _PERIOD_SCOPES),
        ("closing", _CLOSING_SCOPES),
    ):
        if _contains_any(normalized, markers):
            return scope
    return None


def _contains_any(value: str, markers: Sequence[str]) -> bool:
    return any(normalize_header(marker) in value for marker in markers)


def _balance_representation_score(
    hits: dict[str, list[int]],
    scope: str,
    complete: bool,
) -> int:
    if complete:
        return 25
    represented = sum(
        bool(hits[f"{scope}_{part}"])
        for part in ("debit", "credit", "amount", "direction")
    )
    return min(16, represented * 8)


def _score_inventory_role(
    role: str,
    headers: Sequence[str],
) -> RoleScore:
    normalized_headers = tuple(
        normalize_header(header).replace("|", "")
        for header in headers
    )
    score = 0
    signals = []
    for signal, weight, markers in _INVENTORY_RULES[role]:
        normalized_markers = tuple(normalize_header(item) for item in markers)
        if any(
            marker in header
            for marker in normalized_markers
            for header in normalized_headers
        ):
            score += weight
            signals.append(signal)
    return RoleScore(role, min(score, 100), tuple(signals))
