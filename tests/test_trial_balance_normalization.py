import unittest

from open_tax_check.workbooks import CellValue, SheetMeta, WorkbookReader


SOURCE_ID = "source-0002"
SPLIT_HEADERS = (
    "account-code",
    "account",
    "opening-debit",
    "opening-credit",
    "period-debit",
    "period-credit",
    "closing-debit",
    "closing-credit",
)
SELECTION = {
    "source_id": SOURCE_ID,
    "sheet_id": "sheet-0002",
    "sheet_name": "Trial Balance",
    "header_rows": [1],
    "hidden_rows": "exclude",
}
SPLIT_MAPPING = {
    "mapping": {
        "account-code": "account_code",
        "account": "account_name",
        "opening-debit": "opening_debit",
        "opening-credit": "opening_credit",
        "period-debit": "period_debit",
        "period-credit": "period_credit",
        "closing-debit": "closing_debit",
        "closing-credit": "closing_credit",
    },
    "binding": {"mapping_schema_version": "0.2.0"},
}
DIRECTION_HEADERS = (
    "account",
    "opening-amount",
    "opening-direction",
    "period-debit",
    "period-credit",
    "closing-amount",
    "closing-direction",
)
DIRECTION_MAPPING = {
    "mapping": {
        "account": "account_name",
        "period-debit": "period_debit",
        "period-credit": "period_credit",
    },
    "composites": {
        "opening": {
            "amount_field": "opening-amount",
            "direction_field": "opening-direction",
            "debit_field": "opening_debit",
            "credit_field": "opening_credit",
        },
        "closing": {
            "amount_field": "closing-amount",
            "direction_field": "closing-direction",
            "debit_field": "closing_debit",
            "credit_field": "closing_credit",
        },
    },
    "binding": {"mapping_schema_version": "0.2.0"},
}


class MemoryReader(WorkbookReader):
    def __init__(self, rows):
        self._rows = tuple(rows)

    def sheets(self):
        return (
            SheetMeta(
                sheet_id="sheet-0002",
                index=1,
                name="Trial Balance",
                visibility="visible",
                max_row=len(self._rows),
                max_column=len(self._rows[0]),
            ),
        )

    def iter_rows(self, sheet_id, min_row, max_row, max_col):
        return iter(self._rows[min_row - 1 : max_row])

    def close(self):
        pass


def cells(row, *, row_number):
    return tuple(
        CellValue(
            row=row_number,
            column=column,
            coordinate=f"{chr(64 + column)}{row_number}",
            value=value,
            formula=None,
            cached_value=None,
            data_type="number" if isinstance(value, (int, float)) else "text",
            value_origin="literal",
        )
        for column, value in enumerate(row, start=1)
    )


def split_reader(row):
    return MemoryReader((cells(SPLIT_HEADERS, row_number=1), cells(row, row_number=2)))


def direction_reader(row):
    return MemoryReader((cells(DIRECTION_HEADERS, row_number=1), cells(row, row_number=2)))


class TrialBalanceNormalizationTests(unittest.TestCase):
    def test_split_columns_normalize_required_and_optional_fields_with_lineage(self):
        from open_tax_check.normalization import normalize_trial_balance

        result = normalize_trial_balance(
            split_reader(("1001", "Cash", 250, 0, 100, 25, 325, 0)),
            SELECTION,
            SPLIT_MAPPING,
        )

        self.assertEqual(
            result.rows,
            (
                {
                    "account_code": "1001",
                    "account_name": "Cash",
                    "opening_debit": "250.00",
                    "opening_credit": "0.00",
                    "period_debit": "100.00",
                    "period_credit": "25.00",
                    "closing_debit": "325.00",
                    "closing_credit": "0.00",
                    "field_lineage": {
                        "account_code": {
                            "source_id": SOURCE_ID,
                            "sheet_id": "sheet-0002",
                            "sheet_name": "Trial Balance",
                            "row": 2,
                            "column": 1,
                            "coordinate": "A2",
                            "original_type": "text",
                            "value_origin": "literal",
                            "mapping_version": "0.2.0",
                        },
                        "account_name": {
                            "source_id": SOURCE_ID,
                            "sheet_id": "sheet-0002",
                            "sheet_name": "Trial Balance",
                            "row": 2,
                            "column": 2,
                            "coordinate": "B2",
                            "original_type": "text",
                            "value_origin": "literal",
                            "mapping_version": "0.2.0",
                        },
                        "opening_debit": {
                            "source_id": SOURCE_ID,
                            "sheet_id": "sheet-0002",
                            "sheet_name": "Trial Balance",
                            "row": 2,
                            "column": 3,
                            "coordinate": "C2",
                            "original_type": "number",
                            "value_origin": "literal",
                            "mapping_version": "0.2.0",
                        },
                        "opening_credit": {
                            "source_id": SOURCE_ID,
                            "sheet_id": "sheet-0002",
                            "sheet_name": "Trial Balance",
                            "row": 2,
                            "column": 4,
                            "coordinate": "D2",
                            "original_type": "number",
                            "value_origin": "literal",
                            "mapping_version": "0.2.0",
                        },
                        "period_debit": {
                            "source_id": SOURCE_ID,
                            "sheet_id": "sheet-0002",
                            "sheet_name": "Trial Balance",
                            "row": 2,
                            "column": 5,
                            "coordinate": "E2",
                            "original_type": "number",
                            "value_origin": "literal",
                            "mapping_version": "0.2.0",
                        },
                        "period_credit": {
                            "source_id": SOURCE_ID,
                            "sheet_id": "sheet-0002",
                            "sheet_name": "Trial Balance",
                            "row": 2,
                            "column": 6,
                            "coordinate": "F2",
                            "original_type": "number",
                            "value_origin": "literal",
                            "mapping_version": "0.2.0",
                        },
                        "closing_debit": {
                            "source_id": SOURCE_ID,
                            "sheet_id": "sheet-0002",
                            "sheet_name": "Trial Balance",
                            "row": 2,
                            "column": 7,
                            "coordinate": "G2",
                            "original_type": "number",
                            "value_origin": "literal",
                            "mapping_version": "0.2.0",
                        },
                        "closing_credit": {
                            "source_id": SOURCE_ID,
                            "sheet_id": "sheet-0002",
                            "sheet_name": "Trial Balance",
                            "row": 2,
                            "column": 8,
                            "coordinate": "H2",
                            "original_type": "number",
                            "value_origin": "literal",
                            "mapping_version": "0.2.0",
                        },
                    },
                },
            ),
        )
        self.assertEqual(result.issues, ())

    def test_amount_direction_is_split_and_traced_as_derived(self):
        from open_tax_check.normalization import normalize_trial_balance

        result = normalize_trial_balance(
            direction_reader(("Payables", 250, "贷", 100, 25, 175, "贷")),
            SELECTION,
            DIRECTION_MAPPING,
        )

        row = result.rows[0]
        self.assertEqual(row["opening_debit"], "0.00")
        self.assertEqual(row["opening_credit"], "250.00")
        self.assertEqual(row["closing_debit"], "0.00")
        self.assertEqual(row["closing_credit"], "175.00")
        self.assertEqual(row["period_debit"], "100.00")
        self.assertEqual(
            row["field_lineage"]["opening_credit"]["value_origin"],
            "derived_direction_split",
        )
        self.assertEqual(row["field_lineage"]["opening_credit"]["coordinate"], "B2")
        self.assertEqual(row["field_lineage"]["period_debit"]["value_origin"], "literal")
        self.assertEqual(result.issues, ())

    def test_unknown_direction_blocks_without_guessing(self):
        from open_tax_check.normalization import normalize_trial_balance

        result = normalize_trial_balance(
            direction_reader(("Payables", 250, "平", 100, 25, 175, "贷")),
            SELECTION,
            DIRECTION_MAPPING,
        )

        self.assertEqual(result.rows, ())
        self.assertEqual(result.issues[0].code, "invalid_balance_direction")
        self.assertEqual(result.issues[0].row, 2)
        self.assertNotIn("平", str(result.issues))

    def test_negative_composite_amount_cannot_reverse_an_explicit_direction(self):
        from open_tax_check.normalization import normalize_trial_balance

        result = normalize_trial_balance(
            direction_reader(("Payables", -250, "贷", 100, 25, -175, "贷")),
            SELECTION,
            DIRECTION_MAPPING,
        )

        row = result.rows[0]
        self.assertEqual(row["opening_debit"], "0.00")
        self.assertEqual(row["opening_credit"], "250.00")
        self.assertEqual(row["closing_debit"], "0.00")
        self.assertEqual(row["closing_credit"], "175.00")

    def test_total_row_is_excluded_with_a_location_only_issue(self):
        from open_tax_check.normalization import normalize_trial_balance

        result = normalize_trial_balance(
            split_reader(("9999", "资产合计", 250, 0, 100, 25, 325, 0)),
            SELECTION,
            SPLIT_MAPPING,
        )

        self.assertEqual(result.rows, ())
        self.assertEqual(result.issues[0].code, "excluded_total_row")
        self.assertEqual(result.issues[0].row, 2)
        self.assertNotIn("资产合计", str(result.issues))


if __name__ == "__main__":
    unittest.main()
