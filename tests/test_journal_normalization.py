from datetime import date, datetime
from dataclasses import replace
import types
import unittest
from unittest import mock

from open_tax_check.workbooks import CellValue, SheetMeta, WorkbookReader, XlrdReader


SOURCE_ID = "source-0001"
REVIEW_PERIOD = {"start": "2025-01-01", "end": "2025-12-31"}
HEADERS = (
    "voucher",
    "date",
    "account_code",
    "account",
    "summary",
    "debit",
    "credit",
    "counterparty",
)
SELECTION = {
    "source_id": SOURCE_ID,
    "sheet_id": "sheet-0001",
    "sheet_name": "Journal",
    "header_rows": [1],
    "hidden_rows": "exclude",
}
MAPPING = {
    "mapping": {
        "voucher": "voucher_no",
        "date": "date",
        "account_code": "account_code",
        "account": "account_name",
        "summary": "summary",
        "debit": "debit",
        "credit": "credit",
        "counterparty": "counterparty",
    },
    "binding": {"mapping_schema_version": "0.2.0"},
}


class MemoryReader(WorkbookReader):
    def __init__(self, rows):
        self._rows = tuple(rows)
        self.iterated = False

    def sheets(self):
        return (
            SheetMeta(
                sheet_id="sheet-0001",
                index=1,
                name="Journal",
                visibility="visible",
                max_row=len(self._rows),
                max_column=max((len(row) for row in self._rows), default=0),
            ),
        )

    def iter_rows(self, sheet_id, min_row, max_row, max_col):
        self.iterated = True
        return iter(self._rows[min_row - 1 : max_row])

    def close(self):
        pass


def cells(row, *, row_number, formula_columns=()):
    values = []
    for column, value in enumerate(row, start=1):
        formula = "=1" if column in formula_columns else None
        values.append(
            CellValue(
                row=row_number,
                column=column,
                coordinate=f"{chr(64 + column)}{row_number}",
                value=formula if formula else value,
                formula=formula,
                cached_value=value if formula else None,
                data_type="f" if formula else (
                    "number" if isinstance(value, (int, float)) else "text"
                ),
                value_origin="formula" if formula else "literal",
            )
        )
    return tuple(values)


class JournalNormalizationTests(unittest.TestCase):
    def reader(self, row, *, formula_columns=()):
        return MemoryReader(
            (
                cells(HEADERS, row_number=1),
                cells(row, row_number=2, formula_columns=formula_columns),
            )
        )

    def test_journal_normalization_records_lineage_for_each_field(self):
        from open_tax_check.normalization import normalize_journal

        reader = self.reader(
            (
                "V-1",
                45658,
                "1001",
                "Cash",
                "opening entry",
                100,
                0,
                "Customer A",
            ),
            formula_columns=(7,),
        )

        result = normalize_journal(reader, SELECTION, MAPPING, REVIEW_PERIOD)

        row = result.rows[0]
        self.assertEqual(row["date"], "2025-01-01")
        self.assertEqual(row["debit"], "100.00")
        self.assertEqual(row["credit"], "0.00")
        self.assertEqual(row["period"], "2025-01")
        self.assertEqual(row["tax_year"], "2025")
        self.assertEqual(
            row["field_lineage"]["debit"],
            {
                "source_id": SOURCE_ID,
                "sheet_id": "sheet-0001",
                "sheet_name": "Journal",
                "row": 2,
                "column": 6,
                "coordinate": "F2",
                "original_type": "number",
                "value_origin": "literal",
                "mapping_version": "0.2.0",
            },
        )
        self.assertEqual(set(row["field_lineage"]), set(MAPPING["mapping"].values()))
        self.assertEqual(row["field_lineage"]["credit"]["value_origin"], "formula")
        self.assertEqual(result.issues, ())

    def test_internal_blank_header_keeps_later_fields_on_physical_columns(self):
        from open_tax_check.normalization import normalize_journal

        headers = (
            "voucher",
            "date",
            None,
            "account_code",
            "account",
            "summary",
            "debit",
            "credit",
            "counterparty",
        )
        reader = MemoryReader(
            (
                cells(headers, row_number=1),
                cells(
                    (
                        "V-1",
                        "2025-01-01",
                        None,
                        "1001",
                        "Cash",
                        "opening entry",
                        100,
                        0,
                        "Customer A",
                    ),
                    row_number=2,
                ),
            )
        )

        result = normalize_journal(reader, SELECTION, MAPPING, REVIEW_PERIOD)

        self.assertEqual(result.issues, ())
        self.assertEqual(result.rows[0]["account_code"], "1001")
        self.assertEqual(result.rows[0]["account_name"], "Cash")
        self.assertEqual(result.rows[0]["debit"], "100.00")
        self.assertEqual(result.rows[0]["field_lineage"]["debit"]["coordinate"], "G2")

    def test_xls_date_systems_produce_the_same_period_tax_year_and_lineage(self):
        from open_tax_check.normalization import normalize_journal

        headers = ("voucher", "date", "account", "debit", "credit")
        mapping = {
            "mapping": {
                "voucher": "voucher_no",
                "date": "date",
                "account": "account_name",
                "debit": "debit",
                "credit": "credit",
            },
            "binding": {"mapping_schema_version": "0.2.0"},
        }
        for datemode, serial in ((0, 45658), (1, 44196)):
            with self.subTest(datemode=datemode):
                source_rows = (
                    tuple(types.SimpleNamespace(value=value, ctype=1) for value in headers),
                    (
                        types.SimpleNamespace(value="V-1", ctype=1),
                        types.SimpleNamespace(value=serial, ctype=3),
                        types.SimpleNamespace(value="Cash", ctype=1),
                        types.SimpleNamespace(value=100, ctype=2),
                        types.SimpleNamespace(value=0, ctype=2),
                    ),
                )
                sheet = types.SimpleNamespace(
                    name="Journal",
                    nrows=2,
                    ncols=5,
                    merged_cells=(),
                    rowinfo_map={},
                    colinfo_map={},
                    cell=lambda row, column: source_rows[row][column],
                )
                workbook = types.SimpleNamespace(
                    nsheets=1,
                    datemode=datemode,
                    sheet_visibility=(0,),
                    sheet_by_index=mock.Mock(return_value=sheet),
                    unload_sheet=mock.Mock(),
                    release_resources=mock.Mock(),
                )

                result = normalize_journal(
                    XlrdReader(workbook),
                    SELECTION,
                    mapping,
                    REVIEW_PERIOD,
                )

                self.assertEqual(result.issues, ())
                row = result.rows[0]
                self.assertEqual(row["date"], "2025-01-01")
                self.assertEqual(row["period"], "2025-01")
                self.assertEqual(row["tax_year"], "2025")
                self.assertEqual(row["field_lineage"]["date"]["original_type"], "date")
                self.assertEqual(row["field_lineage"]["date"]["value_origin"], "stored_value")

    def test_required_formula_without_cached_value_blocks(self):
        from open_tax_check.normalization import normalize_journal

        reader = self.reader(
            ("V-1", "2025-01-01", "1001", "Cash", "entry", 100, 0, "Customer A")
        )
        formula_cell = reader._rows[1][5]
        reader._rows = (
            reader._rows[0],
            reader._rows[1][:5]
            + (replace(
                formula_cell,
                value="=SUM(A1:A1)",
                formula="=SUM(A1:A1)",
                cached_value=None,
                data_type="f",
                value_origin="formula",
            ),)
            + reader._rows[1][6:],
        )

        result = normalize_journal(reader, SELECTION, MAPPING, REVIEW_PERIOD)

        self.assertEqual(result.rows, ())
        self.assertEqual([issue.code for issue in result.issues], ["missing_cached_formula_value"])
        self.assertNotIn("=SUM(A1:A1)", str(result.issues))

    def test_normalizes_supported_date_objects_and_blocks_dates_outside_review_period(self):
        from open_tax_check.normalization import normalize_journal

        inside = normalize_journal(
            self.reader(("V-1", datetime(2025, 1, 2, 13), "1001", "Cash", "entry", 1, 0, "A")),
            SELECTION,
            MAPPING,
            REVIEW_PERIOD,
        )
        outside = normalize_journal(
            self.reader(("V-2", date(2026, 1, 1), "1001", "Cash", "entry", 1, 0, "A")),
            SELECTION,
            MAPPING,
            REVIEW_PERIOD,
        )

        self.assertEqual(inside.rows[0]["date"], "2025-01-02")
        self.assertEqual([issue.code for issue in outside.issues], ["date_outside_review_period"])

    def test_unconfirmed_hidden_row_policy_blocks_before_data_iteration(self):
        from open_tax_check.normalization import normalize_journal

        reader = self.reader(("V-1", "2025-01-01", "1001", "Cash", "entry", 1, 0, "A"))
        selection = {key: value for key, value in SELECTION.items() if key != "hidden_rows"}

        result = normalize_journal(reader, selection, MAPPING, REVIEW_PERIOD)

        self.assertFalse(reader.iterated)
        self.assertEqual([issue.code for issue in result.issues], ["hidden_row_decision_required"])
