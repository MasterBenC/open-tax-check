"""Deterministic tax-rule calculations."""

from __future__ import annotations

from decimal import Decimal


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def business_entertainment_adjustment(actual_expense: Decimal, revenue: Decimal) -> dict:
    actual_expense = max(Decimal("0"), Decimal(actual_expense))
    revenue = max(Decimal("0"), Decimal(revenue))
    sixty_percent = actual_expense * Decimal("0.60")
    revenue_cap = revenue * Decimal("0.005")
    deductible = min(sixty_percent, revenue_cap)
    adjustment = actual_expense - deductible
    return {
        "rule_id": "CIT-BE-001",
        "rule_version": "2026.1",
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
        "scenario_tax_exposure": _money(ending_balance * Decimal("0.20")),
        "required_evidence": [
            "shareholder_identity",
            "year_end_repayment_evidence",
            "business_purpose_evidence",
            "subsequent_repayment_evidence",
        ],
        "conclusion_boundary": "Scenario estimate only; professional review required.",
    }

