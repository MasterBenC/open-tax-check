"""Evidence-gated generic tax amount calculation primitives.

This module deliberately does not supply statutory rates. Callers must provide
the rate and base from confirmed, evidenced facts or an applicable rule pack.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .numeric import exact_multiply, exact_subtract


@dataclass(frozen=True, slots=True)
class TaxCalculationRequest:
    tax_id: str
    tax_base: Decimal
    rate: Decimal
    deductions: Decimal = Decimal("0")
    confirmed: bool = False
    evidence_refs: tuple[str, ...] = ()
    formula_id: str = "generic.base_minus_deductions_times_rate.v1"

    def __post_init__(self) -> None:
        if not isinstance(self.tax_id, str) or not self.tax_id.strip():
            raise ValueError("invalid_tax_id")
        for value in (self.tax_base, self.rate, self.deductions):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("invalid_tax_calculation_operand")
        if self.rate > Decimal("1"):
            raise ValueError("invalid_tax_rate")
        if self.formula_id not in {
            "generic.base_minus_deductions_times_rate.v1",
            "generic.base_times_rate.v1",
            "generic.base_times_rate_minus_credits.v1",
            "generic.base_times_rate_minus_quick_deduction.v1",
        }:
            raise ValueError("invalid_tax_calculation_formula")
        if (
            self.formula_id == "generic.base_minus_deductions_times_rate.v1"
            and self.deductions > self.tax_base
        ):
            raise ValueError("deductions_exceed_base")
        if not isinstance(self.confirmed, bool):
            raise ValueError("invalid_calculation_confirmation")
        refs = tuple(self.evidence_refs)
        if any(not isinstance(reference, str) or not reference.strip() for reference in refs):
            raise ValueError("invalid_calculation_evidence_refs")
        object.__setattr__(self, "evidence_refs", refs)


@dataclass(frozen=True, slots=True)
class TaxCalculationResult:
    tax_id: str
    status: str
    amount: Decimal
    net_base: Decimal
    rate: Decimal
    formula_id: str
    evidence_refs: tuple[str, ...]


def calculate_tax_amount(request: TaxCalculationRequest) -> TaxCalculationResult:
    if not request.confirmed or not request.evidence_refs:
        raise ValueError("calculation_requires_confirmed_evidence")
    if request.formula_id in {
        "generic.base_times_rate_minus_credits.v1",
        "generic.base_times_rate_minus_quick_deduction.v1",
    }:
        net_base = request.tax_base
        gross_tax = exact_multiply(request.tax_base, request.rate)
        amount = max(
            exact_subtract(gross_tax, request.deductions),
            Decimal("0"),
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        net_base = exact_subtract(request.tax_base, request.deductions)
        amount = exact_multiply(net_base, request.rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    return TaxCalculationResult(
        tax_id=request.tax_id,
        status="calculated",
        amount=amount,
        net_base=net_base,
        rate=request.rate,
        formula_id=request.formula_id,
        evidence_refs=request.evidence_refs,
    )
