from decimal import Decimal

from open_tax_check.template_documents import (
    TemplateIssue,
    financial_statement_template,
    tax_return_template,
)


def _financial_kwargs():
    return {
        "source_id": "customer-upload-1",
        "entity_name": "Example Co",
        "statement_id": "bs-2026",
        "statement_type": "balance_sheet",
        "period_start": "2026-01-01",
        "period_end": "2026-12-31",
    }


def _tax_kwargs():
    return {
        "source_id": "customer-upload-2",
        "entity_name": "Example Co",
        "return_id": "vat-2026-01",
        "tax_id": "vat",
        "return_type": "monthly_return",
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
    }


def test_financial_statement_template_parses_balanced_table_and_explicit_totals():
    rows = [
        (1, ("Line Code", "Line Name", "Standard Name", "Current Amount", "Prior Amount")),
        (2, ("assets", "Assets", "Assets", "1000", "800")),
        (3, ("liabilities_and_equity", "Liabilities and equity", "Liabilities and equity", Decimal("1000"), 800)),
        (4, (None, None, None, None, None)),
    ]

    result = financial_statement_template(rows, **_financial_kwargs(), sheet_name="Balance", header_row=1)

    assert result.issues == ()
    assert result.payload is not None
    assert result.payload["schema_version"] == "financial-statement/v1"
    assert result.payload["currency"] == "CNY"
    assert result.payload["unit_multiplier"] == "1"
    assert result.payload["totals"] == {"assets": "1000.00", "liabilities_and_equity": "1000.00"}
    assert result.payload["lines"][0]["current_amount"] == "1000.00"
    assert result.payload["lines"][1]["prior_amount"] == "800.00"
    assert result.payload["lines"][0]["source_anchor"] == {
        "source_id": "customer-upload-1",
        "page_number": None,
        "sheet_name": "Balance",
        "cell": "A2",
        "coordinates": None,
    }
    assert result.payload["lines"][0]["confidence_source"] == "template"
    assert result.payload["lines"][0]["confirmation_state"] == "confirmed"


def test_financial_template_reports_missing_required_header_without_payload():
    rows = [
        (1, ("line_code", "line_name", "standard_name", "current_amount")),
        (2, ("assets", "Assets", "Assets", "1000")),
    ]

    result = financial_statement_template(rows, **_financial_kwargs())

    assert result.payload is None
    assert result.issues == (TemplateIssue("missing_required_header", 1, ("prior_amount",)),)


def test_financial_template_reports_duplicate_and_unknown_headers():
    rows = [
        (1, ("line_code", "line_name", "standard_name", "current_amount", "Current Amount", "notes")),
        (2, ("assets", "Assets", "Assets", "1000", "1000", "ignored")),
    ]

    result = financial_statement_template(rows, **_financial_kwargs())

    assert result.payload is None
    assert [issue.code for issue in result.issues] == ["duplicate_header", "unknown_header"]
    assert result.issues[0].row == 1
    assert result.issues[0].fields == ("current_amount",)
    assert result.issues[1].row == 1
    assert result.issues[1].fields == ("notes",)


def test_financial_template_rejects_invalid_and_formula_amounts():
    rows = [
        (1, ("line_code", "line_name", "standard_name", "current_amount", "prior_amount")),
        (2, ("assets", "Assets", "Assets", "100.001", "800")),
        (3, ("liabilities_and_equity", "Liabilities", "Liabilities", "=SUM(D2)", "800")),
    ]

    result = financial_statement_template(rows, **_financial_kwargs())

    assert result.payload is None
    assert result.issues == (
        TemplateIssue("invalid_amount", 2, ("current_amount",)),
        TemplateIssue("formula_not_allowed", 3, ("current_amount",)),
    )


def test_tax_template_preserves_declared_value_types():
    rows = [
        (1, ("form_code", "line_number", "line_name", "fact_key", "value", "value_type")),
        (2, ("VAT", "1", "Taxable sales", "vat.taxable_sales", "1000", "decimal")),
        (3, ("VAT", "2", "Description", "vat.description", "ordinary", "text")),
        (4, ("VAT", "3", "Category", "vat.category", "general", "choice")),
        (5, ("VAT", "4", "Is small taxpayer", "vat.small", True, "boolean")),
        (6, ("VAT", "5", "No value", "vat.empty", None, "empty")),
    ]

    result = tax_return_template(rows, **_tax_kwargs())

    assert result.issues == ()
    assert result.payload is not None
    assert [line["value"] for line in result.payload["lines"]] == [
        "1000.00", "ordinary", "general", True, None,
    ]
    assert [line["value_type"] for line in result.payload["lines"]] == [
        "decimal", "text", "choice", "boolean", "empty",
    ]
    assert result.payload["lines"][3]["source_anchor"]["cell"] == "A5"

