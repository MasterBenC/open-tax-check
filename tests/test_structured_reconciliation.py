from dataclasses import replace

from open_tax_check.structured_documents import (
    StructuredIssue,
    financial_statement_from_dict,
    tax_return_from_dict,
)
from open_tax_check.structured_reconciliation import reconcile_structured_documents


ENTITY = "Example Co"
PERIOD = ("2026-01-01", "2026-12-31")


def _anchor(source, cell):
    return {
        "source_id": source,
        "page_number": None,
        "sheet_name": "Template",
        "cell": cell,
        "coordinates": None,
    }


def _statement(statement_id, statement_type, totals, *, period=PERIOD, issues=None):
    lines = []
    for index, (code, amount) in enumerate(totals.items(), start=1):
        lines.append({
            "line_code": code,
            "original_name": code,
            "standard_name": code,
            "current_amount": amount,
            "prior_amount": "0.00",
            "source_anchor": _anchor(statement_id, f"A{index + 1}"),
            "confidence_source": "template",
            "confirmation_state": "confirmed",
        })
    payload = {
        "schema_version": "financial-statement/v1",
        "statement_id": statement_id,
        "source_id": statement_id,
        "entity_name": ENTITY,
        "statement_type": statement_type,
        "period_start": period[0],
        "period_end": period[1],
        "as_of_date": period[1],
        "currency": "CNY",
        "unit_multiplier": "1",
        "lines": lines,
        "totals": totals,
        "issues": issues or [],
    }
    return financial_statement_from_dict(payload)


def _tax(*, value="1000.00", period=PERIOD, issues=None):
    payload = {
        "schema_version": "tax-return/v1",
        "return_id": "vat-2026",
        "source_id": "vat-source",
        "entity_name": ENTITY,
        "tax_id": "vat",
        "return_type": "annual_return",
        "period_start": period[0],
        "period_end": period[1],
        "filing_status": "draft",
        "filed_at": None,
        "currency": "CNY",
        "unit_multiplier": "1",
        "lines": [{
            "form_code": "VAT-MAIN",
            "line_number": "1",
            "original_name": "Taxable sales",
            "fact_key": "vat.taxable_sales",
            "value": value,
            "value_type": "decimal",
            "source_anchor": _anchor("vat-source", "A2"),
            "confidence_source": "template",
            "confirmation_state": "confirmed",
        }],
        "totals": {},
        "issues": issues or [],
    }
    return tax_return_from_dict(payload)


def _balanced_documents():
    balance_sheet = _statement(
        "bs", "balance_sheet",
        {"assets": "1000.00", "liabilities_and_equity": "1000.00", "cash": "300.00"},
    )
    income_statement = _statement(
        "is", "income_statement",
        {
            "revenue": "1000.00",
            "cost": "400.00",
            "expenses": "100.00",
            "taxes": "100.00",
            "net_profit": "400.00",
        },
    )
    cash_flow = _statement("cf", "cash_flow_statement", {"ending_cash": "300.00"})
    return (balance_sheet, income_statement, cash_flow), (_tax(),)


def test_balanced_documents_reconcile_without_guessing():
    statements, returns = _balanced_documents()

    result = reconcile_structured_documents(statements, returns)

    assert result.status == "reconciled"
    assert result.passed is True
    assert result.issues == ()
    assert any(item["check"] == "tax_fact" and item["status"] == "passed" for item in result.checks)


def test_balance_sheet_imbalance_blocks_data_quality():
    statements, returns = _balanced_documents()
    statements = (replace(statements[0], totals=(("assets", "1000.00"), ("cash", "300.00"), ("liabilities_and_equity", "900.00"))), *statements[1:])

    result = reconcile_structured_documents(statements, returns)

    assert result.status == "blocked_data_quality"
    assert result.passed is False
    assert any(issue.code == "balance_sheet_unbalanced" for issue in result.issues)


def test_income_statement_and_cash_flow_mismatches_are_blocking():
    statements, returns = _balanced_documents()
    statements = (
        statements[0],
        replace(statements[1], totals=(("cost", "450.00"), ("expenses", "100.00"), ("net_profit", "400.00"), ("revenue", "1000.00"), ("taxes", "100.00"))),
        replace(statements[2], totals=(("ending_cash", "301.00"),)),
    )

    result = reconcile_structured_documents(statements, returns)

    assert {issue.code for issue in result.issues} >= {"income_statement_unbalanced", "cash_flow_unbalanced"}


def test_period_mismatch_blocks_before_cross_document_checks():
    statements, returns = _balanced_documents()
    returns = (_tax(period=("2026-01-01", "2026-06-30")),)

    result = reconcile_structured_documents(statements, returns)

    assert result.status == "blocked_data_quality"
    assert any(issue.code == "period_mismatch" for issue in result.issues)


def test_tax_fact_mismatch_is_blocking_and_missing_fact_is_not_performed():
    statements, returns = _balanced_documents()
    returns = (_tax(value="999.00"),)

    result = reconcile_structured_documents(statements, returns)

    assert any(issue.code == "tax_fact_mismatch" for issue in result.issues)
    assert any(item.get("status") == "not_performed_missing_fact" for item in result.checks)


def test_existing_document_issue_blocks_reconciliation():
    statements, _ = _balanced_documents()
    statements = (
        replace(
            statements[0],
            issues=(StructuredIssue("source_warning", "warning", "来源需要补充", "assets", None),),
        ),
        *statements[1:],
    )

    result = reconcile_structured_documents(statements, ())

    assert any(issue.code == "source_document_issue" for issue in result.issues)
