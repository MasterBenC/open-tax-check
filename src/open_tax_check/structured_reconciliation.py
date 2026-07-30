"""Deterministic reconciliation for trusted structured financial documents."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from .numeric import exact_add, exact_subtract, parse_decimal
from .structured_documents import FinancialStatement, TaxReturn, TaxReturnLine


@dataclass(frozen=True, slots=True)
class DocumentReconciliationIssue:
    code: str
    severity: str
    document_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    line_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DocumentReconciliationResult:
    status: str
    passed: bool
    checks: tuple[dict, ...]
    issues: tuple[DocumentReconciliationIssue, ...]


_ZERO = Decimal("0.00")
_FACT_BINDINGS = {
    "vat.taxable_sales": ("income_statement", "revenue"),
    "cit.revenue": ("income_statement", "revenue"),
    "cit.net_profit": ("income_statement", "net_profit"),
}


def _statement_totals(statement: FinancialStatement) -> dict[str, str]:
    return dict(statement.totals)


def _issue(
    code: str,
    *documents: object,
    line_codes: tuple[str, ...] = (),
) -> DocumentReconciliationIssue:
    document_ids = tuple(
        str(getattr(document, "statement_id", getattr(document, "return_id", "")))
        for document in documents
        if getattr(document, "statement_id", getattr(document, "return_id", ""))
    )
    source_ids = tuple(
        str(getattr(document, "source_id", ""))
        for document in documents
        if getattr(document, "source_id", "")
    )
    return DocumentReconciliationIssue(code, "blocking", document_ids, source_ids, line_codes)


def _decimal(value: str) -> Decimal:
    return parse_decimal(value)


def _same_context(
    documents: Sequence[FinancialStatement | TaxReturn],
    checks: list[dict],
    issues: list[DocumentReconciliationIssue],
) -> None:
    if not documents:
        issues.append(_issue("missing_structured_document"))
        return
    first = documents[0]
    for document in documents[1:]:
        for field, code in (
            ("entity_name", "entity_mismatch"),
            ("currency", "currency_mismatch"),
            ("unit_multiplier", "unit_multiplier_mismatch"),
        ):
            if getattr(document, field) != getattr(first, field):
                issues.append(_issue(code, first, document))
        if (document.period_start, document.period_end) != (
            first.period_start,
            first.period_end,
        ):
            issues.append(_issue("period_mismatch", first, document))
    checks.append({"check": "document_context", "status": "passed" if not issues else "blocked"})


def _existing_issues(
    documents: Sequence[FinancialStatement | TaxReturn],
    issues: list[DocumentReconciliationIssue],
) -> None:
    for document in documents:
        document_id = getattr(document, "statement_id", getattr(document, "return_id", ""))
        for item in document.issues:
            line_codes = (item.line_code,) if item.line_code else ()
            issues.append(_issue("source_document_issue", document, line_codes=line_codes))


def _balance_sheet_checks(
    statements: Sequence[FinancialStatement],
    checks: list[dict],
    issues: list[DocumentReconciliationIssue],
) -> None:
    for statement in statements:
        if statement.statement_type != "balance_sheet":
            continue
        totals = _statement_totals(statement)
        required = ("assets", "liabilities_and_equity")
        missing = tuple(key for key in required if key not in totals)
        if missing:
            issues.append(_issue("missing_statement_total", statement, line_codes=missing))
            checks.append({"check": "balance_sheet_identity", "document_id": statement.statement_id, "status": "blocked"})
            continue
        passed = _decimal(totals["assets"]) == _decimal(totals["liabilities_and_equity"])
        checks.append({"check": "balance_sheet_identity", "document_id": statement.statement_id, "status": "passed" if passed else "blocked"})
        if not passed:
            issues.append(_issue("balance_sheet_unbalanced", statement, line_codes=required))


def _income_statement_checks(
    statements: Sequence[FinancialStatement],
    checks: list[dict],
    issues: list[DocumentReconciliationIssue],
) -> None:
    required = ("revenue", "cost", "expenses", "taxes", "net_profit")
    for statement in statements:
        if statement.statement_type != "income_statement":
            continue
        totals = _statement_totals(statement)
        missing = tuple(key for key in required if key not in totals)
        if missing:
            issues.append(_issue("missing_statement_total", statement, line_codes=missing))
            checks.append({"check": "income_statement_identity", "document_id": statement.statement_id, "status": "blocked"})
            continue
        calculated = _decimal(totals["revenue"])
        for key in ("cost", "expenses", "taxes"):
            calculated = exact_subtract(calculated, _decimal(totals[key]))
        passed = calculated == _decimal(totals["net_profit"])
        checks.append({"check": "income_statement_identity", "document_id": statement.statement_id, "status": "passed" if passed else "blocked"})
        if not passed:
            issues.append(_issue("income_statement_unbalanced", statement, line_codes=required))


def _cash_flow_checks(
    statements: Sequence[FinancialStatement],
    checks: list[dict],
    issues: list[DocumentReconciliationIssue],
) -> None:
    balance_sheets = [item for item in statements if item.statement_type == "balance_sheet"]
    cash_flows = [item for item in statements if item.statement_type == "cash_flow_statement"]
    if not balance_sheets or not cash_flows:
        checks.append({"check": "cash_flow_identity", "status": "not_performed_missing_document"})
        return
    for balance_sheet in balance_sheets:
        for cash_flow in cash_flows:
            bs_totals = _statement_totals(balance_sheet)
            cf_totals = _statement_totals(cash_flow)
            if "cash" not in bs_totals or "ending_cash" not in cf_totals:
                missing = tuple(key for key in ("cash", "ending_cash") if key not in (bs_totals | cf_totals))
                issues.append(_issue("missing_statement_total", balance_sheet, cash_flow, line_codes=missing))
                checks.append({"check": "cash_flow_identity", "status": "blocked"})
                continue
            passed = _decimal(bs_totals["cash"]) == _decimal(cf_totals["ending_cash"])
            checks.append({"check": "cash_flow_identity", "status": "passed" if passed else "blocked"})
            if not passed:
                issues.append(_issue("cash_flow_unbalanced", balance_sheet, cash_flow, line_codes=("cash", "ending_cash")))


def _tax_checks(
    tax_returns: Sequence[TaxReturn],
    statements: Sequence[FinancialStatement],
    checks: list[dict],
    issues: list[DocumentReconciliationIssue],
) -> None:
    by_type = {statement.statement_type: statement for statement in statements}
    for tax_return in tax_returns:
        seen_pairs: set[tuple[str, str]] = set()
        facts: dict[str, list[TaxReturnLine]] = {}
        for line in tax_return.lines:
            pair = (line.form_code, line.line_number)
            if pair in seen_pairs:
                issues.append(_issue("duplicate_tax_line", tax_return, line_codes=(line.line_number,)))
            seen_pairs.add(pair)
            if line.value_type == "decimal" and line.value is None:
                issues.append(_issue("missing_tax_value", tax_return, line_codes=(line.line_number,)))
            if line.fact_key:
                facts.setdefault(line.fact_key, []).append(line)
        for fact_key, (statement_type, total_key) in _FACT_BINDINGS.items():
            statement = by_type.get(statement_type)
            matching = facts.get(fact_key, ())
            if not matching or statement is None or total_key not in _statement_totals(statement):
                checks.append({"check": "tax_fact", "fact_key": fact_key, "status": "not_performed_missing_fact"})
                continue
            values = [_decimal(line.value) for line in matching if line.value is not None]
            if len(values) != len(matching):
                issues.append(_issue("missing_tax_value", tax_return, line_codes=tuple(line.line_number for line in matching)))
                continue
            total = _ZERO
            for value in values:
                total = exact_add(total, value)
            expected = _decimal(_statement_totals(statement)[total_key])
            passed = total == expected
            checks.append({"check": "tax_fact", "fact_key": fact_key, "status": "passed" if passed else "blocked"})
            if not passed:
                issues.append(_issue("tax_fact_mismatch", tax_return, statement, line_codes=(fact_key,)))


def reconcile_structured_documents(
    financial_statements: Sequence[FinancialStatement],
    tax_returns: Sequence[TaxReturn],
) -> DocumentReconciliationResult:
    """Run all declared structured-document identities without inventing facts."""

    statements = tuple(financial_statements)
    returns = tuple(tax_returns)
    documents = statements + returns
    checks: list[dict] = []
    issues: list[DocumentReconciliationIssue] = []
    _same_context(documents, checks, issues)
    _existing_issues(documents, issues)
    _balance_sheet_checks(statements, checks, issues)
    _income_statement_checks(statements, checks, issues)
    _cash_flow_checks(statements, checks, issues)
    _tax_checks(returns, statements, checks, issues)
    return DocumentReconciliationResult(
        status="reconciled" if not issues else "blocked_data_quality",
        passed=not issues,
        checks=tuple(checks),
        issues=tuple(issues),
    )


__all__ = [
    "DocumentReconciliationIssue",
    "DocumentReconciliationResult",
    "reconcile_structured_documents",
]
