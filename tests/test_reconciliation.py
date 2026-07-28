import unittest
from decimal import Decimal, getcontext, localcontext

from open_tax_check.reconciliation import (
    reconcile_journal_to_trial_balance,
    reconcile_trial_balance,
)


def _lineage(row, field):
    return {
        "source_id": "source-0001",
        "sheet_id": "sheet-0001",
        "sheet_name": "Journal",
        "row": row,
        "column": 1,
        "coordinate": f"A{row}",
        "original_type": "text",
        "value_origin": "literal",
        "mapping_version": "0.2.0",
    }


def journal_row(
    *,
    code="1001",
    name="Cash",
    debit="0.00",
    credit="0.00",
    row=2,
    summary="ordinary entry",
    counterparty="Customer A",
):
    return {
        "account_code": code or "",
        "account_name": name,
        "debit": debit,
        "credit": credit,
        "summary": summary,
        "counterparty": counterparty,
        "field_lineage": {
            "account_code": _lineage(row, "account_code"),
            "account_name": _lineage(row, "account_name"),
            "debit": _lineage(row, "debit"),
            "credit": _lineage(row, "credit"),
            "summary": _lineage(row, "summary"),
            "counterparty": _lineage(row, "counterparty"),
        },
    }


def tb_row(
    *,
    code="1001",
    name="Cash",
    opening_debit="0.00",
    opening_credit="0.00",
    period_debit="0.00",
    period_credit="0.00",
    closing_debit="0.00",
    closing_credit="0.00",
    row=2,
):
    return {
        "account_code": code or "",
        "account_name": name,
        "opening_debit": opening_debit,
        "opening_credit": opening_credit,
        "period_debit": period_debit,
        "period_credit": period_credit,
        "closing_debit": closing_debit,
        "closing_credit": closing_credit,
        "field_lineage": {
            field: _lineage(row, field)
            for field in (
                "account_code",
                "account_name",
                "opening_debit",
                "opening_credit",
                "period_debit",
                "period_credit",
                "closing_debit",
                "closing_credit",
            )
        },
    }


class ReconciliationTests(unittest.TestCase):
    def test_trial_balance_identity_uses_signed_balances(self):
        result = reconcile_trial_balance(
            [
                tb_row(
                    opening_debit="100.00",
                    opening_credit="0.00",
                    period_debit="20.00",
                    period_credit="50.00",
                    closing_debit="70.00",
                    closing_credit="0.00",
                ),
                tb_row(
                    code="2001",
                    name="Balancing account",
                    period_debit="30.00",
                    closing_debit="30.00",
                    row=3,
                ),
            ]
        )

        self.assertTrue(result.passed)

    def test_single_unbalanced_trial_balance_row_fails_movement_control(self):
        result = reconcile_trial_balance(
            [
                tb_row(
                    period_debit="100.00",
                    closing_debit="100.00",
                )
            ]
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.issues[0].code, "trial_balance_movement_unbalanced")
        self.assertEqual(result.issues[0].difference, Decimal("100.00"))

    def test_identity_mismatch_item_uses_exact_roll_forward_difference(self):
        result = reconcile_trial_balance(
            [
                tb_row(
                    period_debit="100.00",
                    closing_debit="99.99",
                ),
                tb_row(
                    code="2001",
                    name="Balancing account",
                    period_credit="100.00",
                    closing_credit="100.00",
                    row=3,
                ),
            ]
        )

        self.assertEqual(result.issues[0].difference, Decimal("0.01"))
        self.assertEqual(result.items[0].difference, Decimal("0.01"))

    def test_trial_balance_period_debits_must_equal_period_credits(self):
        result = reconcile_trial_balance(
            [
                tb_row(code="1001", period_debit="100.00", period_credit="0.00"),
                tb_row(code="6001", period_debit="0.00", period_credit="99.99", row=3),
            ]
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.issues[0].code, "trial_balance_movement_unbalanced")
        self.assertEqual(result.issues[0].difference, Decimal("0.01"))

    def test_account_code_precedes_unique_name(self):
        result = reconcile_journal_to_trial_balance(
            [journal_row(code="1002", name="Bank deposits", debit="100.00")],
            [tb_row(code="1002", name="Bank deposits", period_debit="100.00")],
        )

        self.assertEqual(result.items[0].match_method, "account_code")
        self.assertEqual(result.items[0].status, "matched")

    def test_unique_account_name_matches_only_when_unique_on_both_sides(self):
        result = reconcile_journal_to_trial_balance(
            [journal_row(code=None, name="Cash", debit="100.00")],
            [tb_row(code=None, name="Cash", period_debit="100.00")],
        )

        self.assertEqual(result.items[0].match_method, "unique_account_name")
        self.assertEqual(result.items[0].status, "matched")

    def test_unique_name_match_preserves_identifiers_from_both_sides(self):
        result = reconcile_journal_to_trial_balance(
            [journal_row(code="1002", name="Bank deposits", debit="100.00")],
            [tb_row(code="1102", name="Bank deposits", period_debit="100.00")],
        )

        item = result.items[0]
        self.assertEqual(item.match_method, "unique_account_name")
        self.assertEqual(item.journal_account_code, "1002")
        self.assertEqual(item.journal_account_name, "Bank deposits")
        self.assertEqual(item.trial_balance_account_code, "1102")
        self.assertEqual(item.trial_balance_account_name, "Bank deposits")

    def test_duplicate_names_are_never_force_matched(self):
        result = reconcile_journal_to_trial_balance(
            [journal_row(code=None, name="Other receivables")],
            [
                tb_row(code="122101", name="Other receivables"),
                tb_row(code="122102", name="Other receivables", row=3),
            ],
        )

        self.assertEqual(result.items[0].status, "ambiguous_account")
        self.assertIsNone(result.items[0].match_method)

    def test_mismatch_keeps_only_contributing_lineage_and_exact_difference(self):
        result = reconcile_journal_to_trial_balance(
            [
                journal_row(
                    debit="100.00",
                    summary="private journal summary",
                    counterparty="private counterparty",
                )
            ],
            [tb_row(period_debit="99.99")],
        )

        item = result.items[0]
        self.assertEqual(item.status, "amount_mismatch")
        self.assertEqual(item.account_code, "1001")
        self.assertEqual(item.journal_debit, Decimal("100.00"))
        self.assertEqual(item.trial_balance_debit, Decimal("99.99"))
        self.assertEqual(item.difference, Decimal("0.01"))
        self.assertEqual(set(item.journal_lineage[0]), {"account_code", "account_name", "debit", "credit"})
        self.assertNotIn("private journal summary", repr(item))
        self.assertNotIn("private counterparty", repr(item))

    def test_reconciliation_serialization_preserves_composite_cell_lineage(self):
        from open_tax_check.pipeline import _json_business_value

        trial = tb_row(period_debit="99.99")
        amount = {**_lineage(2, "period_debit"), "column": 5, "coordinate": "E2"}
        direction = {
            **_lineage(2, "period_debit"),
            "column": 6,
            "coordinate": "F2",
        }
        trial["field_lineage"]["period_debit"] = {
            **amount,
            "value_origin": "derived_direction_split",
            "contributing_cells": {
                "amount": amount,
                "direction": direction,
            },
        }

        payload = _json_business_value(
            reconcile_journal_to_trial_balance(
                [journal_row(debit="100.00")],
                [trial],
            )
        )

        lineage = payload["items"][0]["trial_balance_lineage"][0]["period_debit"]
        self.assertEqual(
            lineage["contributing_cells"]["amount"]["coordinate"],
            "E2",
        )
        self.assertEqual(
            lineage["contributing_cells"]["direction"]["coordinate"],
            "F2",
        )

    def test_low_ambient_precision_is_ignored_without_mutating_global_context(self):
        original = getcontext().copy()
        with localcontext() as context:
            context.prec = 6
            result = reconcile_trial_balance(
                [
                    tb_row(
                        opening_debit="999999999999999900.00",
                        period_debit="99.99",
                        closing_debit="999999999999999999.99",
                    ),
                    tb_row(
                        code="2001",
                        name="Balancing account",
                        period_credit="99.99",
                        closing_credit="99.99",
                        row=3,
                    ),
                ]
            )

        self.assertTrue(result.passed)
        self.assertEqual(getcontext().prec, original.prec)
        self.assertEqual(getcontext().rounding, original.rounding)


if __name__ == "__main__":
    unittest.main()
