"""Strict decimal parsing shared by quality checks and tax calculations."""

from __future__ import annotations

from decimal import Decimal
import re


_DECIMAL_LEXEME = re.compile(
    r"^[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?$"
)


def parse_decimal(value) -> Decimal:
    """Parse a deliberately small, deterministic currency lexical contract."""

    supplied = str(value or "").strip()
    if not supplied:
        return Decimal("0")
    if not _DECIMAL_LEXEME.fullmatch(supplied):
        raise ValueError("invalid decimal lexical form")
    return Decimal(supplied.replace(",", ""))
