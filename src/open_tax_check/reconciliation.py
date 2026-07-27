"""Exact, provenance-safe reconciliation of canonical accounting rows."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .numeric import exact_add, exact_subtract, parse_decimal


_ZERO = Decimal("0")
_JOURNAL_LINEAGE_FIELDS = ("account_code", "account_name", "debit", "credit")
_TRIAL_BALANCE_LINEAGE_FIELDS = (
    "account_code",
    "account_name",
    "opening_debit",
    "opening_credit",
    "period_debit",
    "period_credit",
    "closing_debit",
    "closing_credit",
)


@dataclass(frozen=True, slots=True)
class ReconciliationItem:
    """One account-level reconciliation outcome with source locations only."""

    account_code: str | None
    account_name: str | None
    status: str
    match_method: str | None
    journal_debit: Decimal
    journal_credit: Decimal
    trial_balance_debit: Decimal
    trial_balance_credit: Decimal
    difference: Decimal
    debit_difference: Decimal
    credit_difference: Decimal
    journal_lineage: tuple[dict, ...]
    trial_balance_lineage: tuple[dict, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationIssue:
    """A reconciliation control failure with exact totals and relevant lineage."""

    code: str
    account_code: str | None
    account_name: str | None
    journal_debit: Decimal
    journal_credit: Decimal
    trial_balance_debit: Decimal
    trial_balance_credit: Decimal
    difference: Decimal
    journal_lineage: tuple[dict, ...]
    trial_balance_lineage: tuple[dict, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """The deterministic output of an identity or journal-to-TB control."""

    passed: bool
    items: tuple[ReconciliationItem, ...]
    issues: tuple[ReconciliationIssue, ...]


@dataclass(frozen=True, slots=True)
class _AccountAggregate:
    code: str | None
    name: str | None
    debit: Decimal
    credit: Decimal
    lineage: tuple[dict, ...]


def signed(debit: str, credit: str) -> Decimal:
    """Return a canonical debit-minus-credit amount independently of context."""

    return exact_subtract(parse_decimal(debit), parse_decimal(credit))


def row_identity(row: dict) -> Decimal:
    """Return opening balance plus movement less closing balance for one row."""

    opening = signed(row["opening_debit"], row["opening_credit"])
    movement = signed(row["period_debit"], row["period_credit"])
    closing = signed(row["closing_debit"], row["closing_credit"])
    return exact_subtract(exact_add(opening, movement), closing)


def _identifier(row: dict, field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    identifier = str(value).strip()
    return identifier or None


def _restricted_lineage(row: dict, fields: tuple[str, ...]) -> dict:
    lineage = row.get("field_lineage")
    if not isinstance(lineage, dict):
        return {}
    return {
        field: dict(lineage[field])
        for field in fields
        if isinstance(lineage.get(field), dict)
    }


def _account_from_journal(row: dict) -> _AccountAggregate:
    return _AccountAggregate(
        _identifier(row, "account_code"),
        _identifier(row, "account_name"),
        parse_decimal(row.get("debit")),
        parse_decimal(row.get("credit")),
        (_restricted_lineage(row, _JOURNAL_LINEAGE_FIELDS),),
    )


def _account_from_trial_balance(row: dict) -> _AccountAggregate:
    return _AccountAggregate(
        _identifier(row, "account_code"),
        _identifier(row, "account_name"),
        parse_decimal(row.get("period_debit")),
        parse_decimal(row.get("period_credit")),
        (_restricted_lineage(row, _TRIAL_BALANCE_LINEAGE_FIELDS),),
    )


def _aggregate_journal(rows: Iterable[dict]) -> tuple[_AccountAggregate, ...]:
    aggregates: dict[tuple[str, str | int], _AccountAggregate] = {}
    for index, row in enumerate(rows):
        account = _account_from_journal(row)
        if account.code:
            key: tuple[str, str | int] = ("account_code", account.code)
        elif account.name:
            key = ("account_name", account.name)
        else:
            key = ("source_row", index)
        previous = aggregates.get(key)
        if previous is None:
            aggregates[key] = account
            continue
        aggregates[key] = _AccountAggregate(
            previous.code,
            previous.name,
            exact_add(previous.debit, account.debit),
            exact_add(previous.credit, account.credit),
            previous.lineage + account.lineage,
        )
    return tuple(aggregates.values())


def _unique_index(
    accounts: Iterable[_AccountAggregate], attribute: str
) -> dict[str, _AccountAggregate]:
    candidates: dict[str, list[_AccountAggregate]] = {}
    for account in accounts:
        value = getattr(account, attribute)
        if value:
            candidates.setdefault(value, []).append(account)
    return {value: entries[0] for value, entries in candidates.items() if len(entries) == 1}


def _value_counts(accounts: Iterable[_AccountAggregate], attribute: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for account in accounts:
        value = getattr(account, attribute)
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _item(
    journal: _AccountAggregate | None,
    trial_balance: _AccountAggregate | None,
    *,
    status: str,
    match_method: str | None,
) -> ReconciliationItem:
    journal_debit = journal.debit if journal else _ZERO
    journal_credit = journal.credit if journal else _ZERO
    trial_balance_debit = trial_balance.debit if trial_balance else _ZERO
    trial_balance_credit = trial_balance.credit if trial_balance else _ZERO
    debit_difference = exact_subtract(journal_debit, trial_balance_debit)
    credit_difference = exact_subtract(journal_credit, trial_balance_credit)
    difference = exact_subtract(
        signed(journal_debit, journal_credit),
        signed(trial_balance_debit, trial_balance_credit),
    )
    account = journal or trial_balance
    return ReconciliationItem(
        account.code if account else None,
        account.name if account else None,
        status,
        match_method,
        journal_debit,
        journal_credit,
        trial_balance_debit,
        trial_balance_credit,
        difference,
        debit_difference,
        credit_difference,
        journal.lineage if journal else (),
        trial_balance.lineage if trial_balance else (),
    )


def _issue_from_trial_balance(
    code: str,
    trial_balance: _AccountAggregate,
    difference: Decimal,
) -> ReconciliationIssue:
    return ReconciliationIssue(
        code,
        trial_balance.code,
        trial_balance.name,
        _ZERO,
        _ZERO,
        trial_balance.debit,
        trial_balance.credit,
        difference,
        (),
        trial_balance.lineage,
    )


def reconcile_trial_balance(rows: Iterable[dict]) -> ReconciliationResult:
    """Check signed row roll-forwards and the cross-account movement control."""

    materialized_rows = tuple(rows)
    accounts = tuple(_account_from_trial_balance(row) for row in materialized_rows)
    issues: list[ReconciliationIssue] = []

    total_debit = _ZERO
    total_credit = _ZERO
    for account in accounts:
        total_debit = exact_add(total_debit, account.debit)
        total_credit = exact_add(total_credit, account.credit)
    movement_difference = exact_subtract(total_debit, total_credit)
    if movement_difference != _ZERO:
        aggregate_lineage = tuple(account.lineage[0] for account in accounts)
        issues.append(
            ReconciliationIssue(
                "trial_balance_movement_unbalanced",
                None,
                None,
                _ZERO,
                _ZERO,
                total_debit,
                total_credit,
                movement_difference,
                (),
                aggregate_lineage,
            )
        )

    items: list[ReconciliationItem] = []
    for row, account in zip(materialized_rows, accounts):
        difference = row_identity(row)
        if difference == _ZERO:
            continue
        issues.append(
            _issue_from_trial_balance("trial_balance_identity_mismatch", account, difference)
        )
        items.append(
            _item(
                None,
                account,
                status="identity_mismatch",
                match_method=None,
            )
        )
    return ReconciliationResult(not issues, tuple(items), tuple(issues))


def reconcile_journal_to_trial_balance(
    journal: Iterable[dict], trial_balance: Iterable[dict]
) -> ReconciliationResult:
    """Match canonical accounts, then compare exact period debit and credit totals."""

    journal_accounts = _aggregate_journal(journal)
    trial_balance_accounts = tuple(_account_from_trial_balance(row) for row in trial_balance)
    trial_balance_by_code = _unique_index(trial_balance_accounts, "code")
    journal_by_name = _unique_index(journal_accounts, "name")
    trial_balance_by_name = _unique_index(trial_balance_accounts, "name")
    journal_name_counts = _value_counts(journal_accounts, "name")
    trial_balance_name_counts = _value_counts(trial_balance_accounts, "name")

    items: list[ReconciliationItem] = []
    matched_trial_balances: set[int] = set()
    for account in journal_accounts:
        matched: _AccountAggregate | None = None
        method: str | None = None
        if account.code and account.code in trial_balance_by_code:
            matched = trial_balance_by_code[account.code]
            method = "account_code"
        elif (
            account.name
            and account.name in journal_by_name
            and account.name in trial_balance_by_name
        ):
            matched = trial_balance_by_name[account.name]
            method = "unique_account_name"

        if matched is None:
            ambiguous = bool(
                account.name
                and (
                    journal_name_counts.get(account.name, 0) > 1
                    or trial_balance_name_counts.get(account.name, 0) > 1
                )
            )
            items.append(
                _item(
                    account,
                    None,
                    status="ambiguous_account" if ambiguous else "missing_trial_balance",
                    match_method=None,
                )
            )
            continue

        matched_trial_balances.add(id(matched))
        same_amounts = (
            account.debit == matched.debit and account.credit == matched.credit
        )
        items.append(
            _item(
                account,
                matched,
                status="matched" if same_amounts else "amount_mismatch",
                match_method=method,
            )
        )

    for account in trial_balance_accounts:
        if id(account) not in matched_trial_balances:
            items.append(
                _item(
                    None,
                    account,
                    status="missing_journal",
                    match_method=None,
                )
            )

    issues = tuple(
        ReconciliationIssue(
            item.status,
            item.account_code,
            item.account_name,
            item.journal_debit,
            item.journal_credit,
            item.trial_balance_debit,
            item.trial_balance_credit,
            item.difference,
            item.journal_lineage,
            item.trial_balance_lineage,
        )
        for item in items
        if item.status != "matched"
    )
    return ReconciliationResult(not issues, tuple(items), issues)
