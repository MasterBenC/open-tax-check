"""Strict decimal parsing shared by quality checks and tax calculations."""

from __future__ import annotations

from decimal import Decimal, DecimalException, Inexact, Rounded, localcontext
import re


_DECIMAL_LEXEME = re.compile(
    r"^[+-]?(?P<integer>[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)"
    r"(?:\.(?P<fraction>[0-9]+))?$"
)

MAX_INTEGER_DIGITS = 18
MAX_FRACTION_DIGITS = 2
EXACT_CONTEXT_PRECISION = 50
AGGREGATE_ABSOLUTE_TOTAL_LIMIT = Decimal("99999999999999999999.99")


def parse_decimal(value) -> Decimal:
    """Parse the bounded Phase 1 currency lexical contract."""

    supplied = str(value or "").strip()
    if not supplied:
        return Decimal("0")
    matched = _DECIMAL_LEXEME.fullmatch(supplied)
    if matched is None:
        raise ValueError("invalid decimal lexical form")
    integer_digits = matched.group("integer").replace(",", "")
    fraction_digits = matched.group("fraction") or ""
    if (
        len(integer_digits) > MAX_INTEGER_DIGITS
        or len(fraction_digits) > MAX_FRACTION_DIGITS
    ):
        raise ValueError("invalid decimal lexical form")
    return Decimal(supplied.replace(",", ""))


def _exact_binary(left: Decimal, right: Decimal, operation) -> Decimal:
    left = Decimal(left)
    right = Decimal(right)
    if not left.is_finite() or not right.is_finite():
        raise ValueError("non-finite decimal operand")
    try:
        with localcontext() as context:
            context.prec = EXACT_CONTEXT_PRECISION
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            result = operation(left, right)
    except DecimalException as exc:
        raise ValueError("decimal operation exceeds exact context") from exc
    if not result.is_finite():
        raise ValueError("non-finite decimal result")
    return result


def exact_add(left: Decimal, right: Decimal) -> Decimal:
    """Add finite decimals without inheriting ambient precision."""

    return _exact_binary(left, right, lambda first, second: first + second)


def exact_subtract(left: Decimal, right: Decimal) -> Decimal:
    """Subtract finite decimals without inheriting ambient precision."""

    return _exact_binary(left, right, lambda first, second: first - second)


def exact_multiply(left: Decimal, right: Decimal) -> Decimal:
    """Multiply finite decimals without inheriting ambient precision."""

    return _exact_binary(left, right, lambda first, second: first * second)


def format_money(value: Decimal) -> str:
    """Format a bounded finite amount with two fractional digits."""

    value = Decimal(value)
    if not value.is_finite():
        raise ValueError("non-finite decimal operand")
    with localcontext() as context:
        context.prec = EXACT_CONTEXT_PRECISION
        rounded = value.quantize(Decimal("0.01"))
    return f"{rounded:.2f}"
