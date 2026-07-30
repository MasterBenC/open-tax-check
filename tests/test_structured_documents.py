from copy import deepcopy

import pytest

from open_tax_check.structured_documents import (
    FinancialStatement,
    financial_statement_from_dict,
    financial_statement_to_dict,
)


def financial_payload():
    return {
        "schema_version": "financial-statement/v1",
        "statement_id": "balance-2026-12",
        "source_id": "host-untrusted",
        "entity_name": "瀹屽叏铏氭瀯绉戞妧鏈夐檺鍏徃",
        "statement_type": "balance_sheet",
        "period_start": "2026-01-01",
        "period_end": "2026-12-31",
        "as_of_date": "2026-12-31",
        "currency": "CNY",
        "unit_multiplier": "1",
        "lines": [{
            "line_code": "BS-ASSETS-TOTAL",
            "original_name": "璧勪骇鎬昏",
            "standard_name": "璧勪骇鎬昏",
            "current_amount": "1000.00",
            "prior_amount": "800.00",
            "source_anchor": {
                "source_id": "host-untrusted",
                "page_number": None,
                "sheet_name": "璧勪骇璐熷€鸿〃",
                "cell": "D32",
                "coordinates": None,
            },
            "confidence_source": "template",
            "confirmation_state": "confirmed",
        }],
        "totals": {"assets": "1000.00", "liabilities_and_equity": "1000.00"},
        "issues": [],
    }


def test_financial_statement_v1_round_trips_exactly():
    value = financial_statement_from_dict(financial_payload())
    assert isinstance(value, FinancialStatement)
    assert financial_statement_to_dict(value) == financial_payload()


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(extra=True),
    lambda value: value.pop("currency"),
    lambda value: value["lines"][0].update(extra=True),
    lambda value: value.update(schema_version="financial-statement/v0"),
    lambda value: value.update(currency="USD"),
    lambda value: value.update(unit_multiplier="100"),
    lambda value: value["lines"][0].update(current_amount="1e3"),
    lambda value: value["lines"][0].update(current_amount=1000.0),
    lambda value: value["lines"][0].update(confirmation_state="approved"),
])
def test_financial_statement_rejects_non_contract_payloads(mutate):
    payload = deepcopy(financial_payload())
    mutate(payload)
    with pytest.raises(ValueError):
        financial_statement_from_dict(payload)


def test_financial_statement_rejects_payload_larger_than_twenty_mib():
    payload = financial_payload()
    payload["entity_name"] = "x" * (20 * 1024 * 1024)

    with pytest.raises(ValueError, match="^invalid_structured_document$"):
        financial_statement_from_dict(payload)


def test_financial_statement_rejects_ai_confirmed_line():
    payload = financial_payload()
    payload["lines"][0]["confidence_source"] = "host_ai"

    with pytest.raises(ValueError, match="^invalid_structured_document$"):
        financial_statement_from_dict(payload)


@pytest.mark.parametrize(
    ("amount", "accepted"),
    [
        ("999999999999999999.99", True),
        ("1000000000000000000.00", False),
    ],
)
def test_financial_statement_enforces_amount_integral_digit_boundary(amount, accepted):
    payload = financial_payload()
    payload["lines"][0]["current_amount"] = amount

    if accepted:
        assert financial_statement_from_dict(payload).lines[0].current_amount == amount
    else:
        with pytest.raises(ValueError, match="^invalid_structured_document$"):
            financial_statement_from_dict(payload)


@pytest.mark.parametrize("mutate", [
    lambda value: value["lines"][0]["source_anchor"].update(
        page_number=1,
        coordinates=[[0, 0], [1, 0], [1, 1], [0, 1]],
    ),
    lambda value: value["lines"][0]["source_anchor"].update(sheet_name=None, cell=None),
])
def test_financial_statement_rejects_mixed_or_empty_source_locator(mutate):
    payload = financial_payload()
    mutate(payload)

    with pytest.raises(ValueError, match="^invalid_structured_document$"):
        financial_statement_from_dict(payload)


def test_financial_statement_rejects_line_and_issue_budget_overflows():
    line_overflow = financial_payload()
    line_overflow["lines"] *= 10_001
    with pytest.raises(ValueError, match="^invalid_structured_document$"):
        financial_statement_from_dict(line_overflow)

    issue_overflow = financial_payload()
    issue_overflow["issues"] = [{
        "code": "notice",
        "severity": "low",
        "message_zh": "notice",
        "line_code": None,
        "source_anchor": None,
    }] * 1_001
    with pytest.raises(ValueError, match="^invalid_structured_document$"):
        financial_statement_from_dict(issue_overflow)


def test_financial_statement_rejects_source_anchor_budget_overflow():
    payload = financial_payload()
    payload["lines"] *= 10_000
    payload["issues"] = [{
        "code": "notice",
        "severity": "low",
        "message_zh": "notice",
        "line_code": None,
        "source_anchor": deepcopy(payload["lines"][0]["source_anchor"]),
    }]

    with pytest.raises(ValueError, match="^invalid_structured_document$"):
        financial_statement_from_dict(payload)
