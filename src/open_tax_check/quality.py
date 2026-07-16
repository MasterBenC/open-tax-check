"""Data-quality gates executed before tax rules."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import re


def _blank(value) -> bool:
    return value is None or not str(value).strip()


def _decimal(value) -> Decimal:
    if _blank(value):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid monetary value: {value!r}") from exc


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


_ISO_DATE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
_FLEXIBLE_DATE = re.compile(
    r"^([0-9]{4})([/.])([0-9]{1,2})\2([0-9]{1,2})$"
)


def _normalize_date(value) -> tuple[str, str, str]:
    supplied = str(value or "").strip()
    iso_match = _ISO_DATE.fullmatch(supplied)
    flexible_match = _FLEXIBLE_DATE.fullmatch(supplied)
    if iso_match:
        year, month, day = iso_match.groups()
    elif flexible_match:
        year, _, month, day = flexible_match.groups()
    else:
        raise ValueError("unsupported posting date")
    parsed = date(int(year), int(month), int(day))
    return parsed.isoformat(), f"{parsed.year:04d}-{parsed.month:02d}", f"{parsed.year:04d}"


def _location(row: dict) -> dict:
    return {
        key: row[key]
        for key in ("source_file", "source_sheet", "source_row")
        if row.get(key) is not None
    }


def validate_voucher_balance(rows: list[dict], tolerance: Decimal = Decimal("0.01")) -> list[dict]:
    grouped: dict[tuple[str | None, str], dict] = defaultdict(
        lambda: {"debit": Decimal("0"), "credit": Decimal("0"), "source_rows": []}
    )
    for row in rows:
        voucher_no = str(row.get("voucher_no") or "").strip()
        if not voucher_no:
            continue
        period = row.get("period")
        if period is None and row.get("date") not in (None, ""):
            try:
                _, period, _ = _normalize_date(row.get("date"))
            except ValueError:
                continue
        key = (period, voucher_no)
        grouped[key]["debit"] += _decimal(row.get("debit"))
        grouped[key]["credit"] += _decimal(row.get("credit"))
        if row.get("source_row") is not None:
            grouped[key]["source_rows"].append(row["source_row"])

    findings = []
    for (period, voucher_no), totals in grouped.items():
        difference = totals["debit"] - totals["credit"]
        if abs(difference) > tolerance:
            findings.append(
                {
                    "rule_id": "DQ-VOUCHER-001",
                    "rule_version": "0.1.0",
                    "status": "blocking",
                    "period": period,
                    "voucher_no": voucher_no,
                    "debit_total": _money(totals["debit"]),
                    "credit_total": _money(totals["credit"]),
                    "difference": _money(difference),
                    "source_rows": totals["source_rows"],
                }
            )
    return findings


def validate_journal_quality(rows: list[dict]) -> list[dict]:
    """Normalize supported dates and return all blocking pre-calculation findings."""

    findings: list[dict] = []
    balance_rows: list[dict] = []
    for row in rows:
        missing = [
            field
            for field in ("voucher_no", "date", "account_name")
            if not str(row.get(field) or "").strip()
        ]
        if _blank(row.get("debit")) and _blank(row.get("credit")):
            missing.extend(["debit", "credit"])
        if missing:
            findings.append(
                {
                    "rule_id": "DQ-ROW-001",
                    "rule_version": "0.1.0",
                    "status": "blocking",
                    "fields": missing,
                    **_location(row),
                }
            )

        date_valid = False
        if "date" not in missing:
            try:
                normalized, period, tax_year = _normalize_date(row.get("date"))
            except (TypeError, ValueError):
                findings.append(
                    {
                        "rule_id": "DQ-DATE-001",
                        "rule_version": "0.1.0",
                        "status": "blocking",
                        "fields": ["date"],
                        **_location(row),
                    }
                )
            else:
                row.update({"date": normalized, "period": period, "tax_year": tax_year})
                date_valid = True

        if not missing and date_valid:
            balance_rows.append(row)

    findings.extend(validate_voucher_balance(balance_rows))
    return findings
