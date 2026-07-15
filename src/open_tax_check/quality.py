"""Data-quality gates executed before tax rules."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation


def _decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid monetary value: {value!r}") from exc


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def validate_voucher_balance(rows: list[dict], tolerance: Decimal = Decimal("0.01")) -> list[dict]:
    grouped: dict[str, dict] = defaultdict(
        lambda: {"debit": Decimal("0"), "credit": Decimal("0"), "source_rows": []}
    )
    for row in rows:
        voucher_no = str(row.get("voucher_no") or "").strip()
        if not voucher_no:
            continue
        grouped[voucher_no]["debit"] += _decimal(row.get("debit"))
        grouped[voucher_no]["credit"] += _decimal(row.get("credit"))
        if row.get("source_row") is not None:
            grouped[voucher_no]["source_rows"].append(row["source_row"])

    findings = []
    for voucher_no, totals in grouped.items():
        difference = totals["debit"] - totals["credit"]
        if abs(difference) > tolerance:
            findings.append(
                {
                    "rule_id": "DQ-VOUCHER-001",
                    "rule_version": "0.1.0",
                    "status": "blocking",
                    "voucher_no": voucher_no,
                    "debit_total": _money(totals["debit"]),
                    "credit_total": _money(totals["credit"]),
                    "difference": _money(difference),
                    "source_rows": totals["source_rows"],
                }
            )
    return findings

