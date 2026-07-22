"""Deterministic tax-rule calculations."""

from __future__ import annotations

from decimal import Decimal

from .numeric import exact_multiply, exact_subtract, format_money


def _money(value: Decimal) -> str:
    return format_money(value)


def business_entertainment_adjustment(actual_expense: Decimal, revenue: Decimal) -> dict:
    actual_expense = max(Decimal("0"), Decimal(actual_expense))
    revenue = max(Decimal("0"), Decimal(revenue))
    sixty_percent = exact_multiply(actual_expense, Decimal("0.60"))
    revenue_cap = exact_multiply(revenue, Decimal("0.005"))
    deductible = min(sixty_percent, revenue_cap)
    adjustment = exact_subtract(actual_expense, deductible)
    return {
        "rule_id": "CIT-BE-001",
        "rule_version": "2026.2",
        "status": "calculated",
        "actual_expense": _money(actual_expense),
        "deductible_amount": _money(deductible),
        "tax_adjustment": _money(adjustment),
        "formula": "actual - min(actual * 60%, revenue * 0.5%)",
    }


def shareholder_loan_candidate(
    *,
    ending_balance: Decimal,
    is_individual_shareholder: bool,
    outstanding_at_year_end: bool,
    business_use_confirmed: bool,
) -> dict | None:
    ending_balance = Decimal(ending_balance)
    if (
        ending_balance <= 0
        or not is_individual_shareholder
        or not outstanding_at_year_end
        or business_use_confirmed
    ):
        return None

    return {
        "rule_id": "IIT-SHL-001",
        "rule_version": "2026.1",
        "status": "candidate",
        "review_status": "unreviewed",
        "ending_balance": _money(ending_balance),
        "scenario_tax_exposure": _money(
            exact_multiply(ending_balance, Decimal("0.20"))
        ),
        "required_evidence": [
            "shareholder_identity",
            "year_end_repayment_evidence",
            "business_purpose_evidence",
            "subsequent_repayment_evidence",
        ],
        "conclusion_boundary": "Scenario estimate only; professional review required.",
    }
