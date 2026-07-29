from decimal import Decimal

import pytest

from open_tax_check.tax_calculation import (
    TaxCalculationRequest,
    calculate_tax_amount,
)


def test_generic_calculation_requires_confirmation_and_evidence():
    request = TaxCalculationRequest(
        tax_id="vat",
        tax_base=Decimal("1000.00"),
        rate=Decimal("0.13"),
        deductions=Decimal("100.00"),
        confirmed=True,
        evidence_refs=("ledger:2026",),
    )

    result = calculate_tax_amount(request)

    assert result.status == "calculated"
    assert result.amount == Decimal("117.00")
    assert result.formula_id == "generic.base_minus_deductions_times_rate.v1"


def test_generic_calculation_rejects_unconfirmed_or_missing_evidence():
    with pytest.raises(ValueError, match="calculation_requires_confirmed_evidence"):
        calculate_tax_amount(
            TaxCalculationRequest(
                tax_id="stamp_tax", tax_base=Decimal("100"), rate=Decimal("0.001"), confirmed=False
            )
        )


def test_generic_calculation_rejects_deduction_above_base_and_invalid_rate():
    with pytest.raises(ValueError, match="deductions_exceed_base"):
        TaxCalculationRequest(
            tax_id="deed_tax", tax_base=Decimal("10"), rate=Decimal("0.03"), deductions=Decimal("11")
        )
    with pytest.raises(ValueError, match="invalid_tax_rate"):
        TaxCalculationRequest(tax_id="vat", tax_base=Decimal("10"), rate=Decimal("1.01"))
