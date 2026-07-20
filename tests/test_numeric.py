import unittest
from decimal import Decimal, localcontext

from open_tax_check.numeric import (
    AGGREGATE_ABSOLUTE_TOTAL_LIMIT,
    EXACT_CONTEXT_PRECISION,
    MAX_FRACTION_DIGITS,
    MAX_INTEGER_DIGITS,
    exact_add,
    exact_multiply,
    exact_subtract,
    parse_decimal,
)


class NumericContractTests(unittest.TestCase):
    def test_phase_one_value_bounds_are_explicit_and_fail_closed(self):
        self.assertEqual(MAX_INTEGER_DIGITS, 18)
        self.assertEqual(MAX_FRACTION_DIGITS, 2)
        self.assertEqual(EXACT_CONTEXT_PRECISION, 50)
        self.assertEqual(
            AGGREGATE_ABSOLUTE_TOTAL_LIMIT,
            Decimal("99999999999999999999.99"),
        )
        self.assertEqual(
            parse_decimal("  +999,999,999,999,999,999.99  "),
            Decimal("999999999999999999.99"),
        )

        for supplied in (
            "1000000000000000000",
            "100000000000000000000000000",
            "999999999999999999.999",
        ):
            with self.subTest(supplied=supplied):
                with self.assertRaisesRegex(ValueError, "^invalid decimal lexical form$"):
                    parse_decimal(supplied)

    def test_exact_helpers_ignore_a_low_ambient_decimal_precision(self):
        maximum = Decimal("999999999999999999.99")
        with localcontext() as context:
            context.prec = 6
            doubled = exact_add(maximum, maximum)
            restored = exact_subtract(doubled, maximum)
            percentage = exact_multiply(maximum, Decimal("0.005"))

        self.assertEqual(doubled, Decimal("1999999999999999999.98"))
        self.assertEqual(restored, maximum)
        self.assertEqual(percentage, Decimal("4999999999999999.99995"))


if __name__ == "__main__":
    unittest.main()
