from __future__ import annotations

import importlib
import io
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from xml.etree.ElementTree import ParseError
from zipfile import ZIP_DEFLATED, ZipFile

from open_tax_check import workbooks
from open_tax_check.workbooks import (
    ExcelExtraRequired,
    OpenPyxlReader,
    UnsupportedWorkbook,
    WorkbookError,
    WorkbookSafetyError,
    open_workbook,
)
from tests.workbook_factory import (
    make_xlsx,
    make_xlsx_with_hidden_sheet,
    make_xlsx_with_sheet_count,
    make_bad_zip_metadata_fixture,
    make_zip_member_count_fixture,
    make_zip_member_name_fixture,
    make_zip_ratio_fixture,
    make_zip_uncompressed_fixture,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def rewrite_zip_member(path: Path, member_name: str, transform) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with ZipFile(path, "r") as source:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as target:
            for item in source.infolist():
                content = source.read(item)
                if item.filename == member_name:
                    content = transform(content)
                target.writestr(item, content)
    temporary.replace(path)


class WorkbookReaderTests(unittest.TestCase):
    def test_ooxml_limits_match_security_contract(self):
        self.assertEqual(workbooks.MAX_ZIP_MEMBERS, 10_000)
        self.assertEqual(
            workbooks.MAX_UNCOMPRESSED_BYTES,
            1024 * 1024 * 1024,
        )
        self.assertEqual(workbooks.MAX_COMPRESSION_RATIO, 100)
        self.assertEqual(workbooks.MAX_WORKSHEETS, 200)
        self.assertEqual(workbooks.MAX_VISITED_CELLS, 5_000_000)
        self.assertEqual(
            workbooks.MAX_METADATA_MEMBER_BYTES,
            64 * 1024 * 1024,
        )
        self.assertEqual(workbooks.MAX_METADATA_NODES, 5_500_000)
        self.assertEqual(workbooks.MAX_MERGED_RANGES, 100_000)

    def test_xlsx_reader_exposes_formula_metadata_without_recalculation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "book.xlsx", formula="=1+1")

            with open_workbook(path) as book:
                cells = list(book.iter_rows("sheet-0001", 1, 2, 4))

        self.assertEqual(cells[1][0].formula, "=1+1")
        self.assertIsNone(cells[1][0].cached_value)

    def test_xlsx_reader_reads_cached_formula_value_by_coordinate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(
                Path(temp_dir) / "cached.xlsx",
                formula="=1+1",
                cached_value=2,
            )

            with open_workbook(path) as book:
                cells = list(book.iter_rows("sheet-0001", 2, 2, 1))

        cell = cells[0][0]
        self.assertEqual(cell.coordinate, "A2")
        self.assertEqual(cell.value, "=1+1")
        self.assertEqual(cell.formula, "=1+1")
        self.assertEqual(cell.cached_value, 2)
        self.assertEqual(cell.data_type, "f")
        self.assertEqual(cell.value_origin, "formula")

    def test_xlsx_sheet_metadata_reports_formula_merge_and_hidden_row_or_column(self):
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metadata.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet["A1"] = "=1+1"
            sheet.merge_cells("A2:B2")
            sheet.row_dimensions[3].hidden = True
            sheet.column_dimensions["C"].hidden = True
            workbook.save(path)
            workbook.close()

            with open_workbook(path) as book:
                metadata = book.sheets()[0]

        self.assertIs(metadata.has_formulas, True)
        self.assertIs(metadata.has_merged_ranges, True)
        self.assertIs(metadata.has_hidden_data, True)
        self.assertEqual(metadata.merged_ranges, ("A2:B2",))

    def test_xlsx_metadata_scan_respects_visited_cell_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "metadata-limit.xlsx")
            reader = None
            try:
                with mock.patch.object(
                    workbooks,
                    "MAX_VISITED_CELLS",
                    0,
                ):
                    with self.assertRaisesRegex(
                        WorkbookSafetyError,
                        "^visited_cell_limit_exceeded$",
                    ):
                        reader = open_workbook(path)
            finally:
                if reader is not None:
                    reader.close()

    def test_ooxml_metadata_rejects_many_merges_without_cell_nodes(self):
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "many-merges.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.merge_cells("A1:B1")
            sheet.merge_cells("C1:D1")
            workbook.save(path)
            workbook.close()

            with mock.patch.object(
                workbooks,
                "MAX_MERGED_RANGES",
                1,
            ):
                with self.assertRaisesRegex(
                    WorkbookSafetyError,
                    "^ooxml_merge_range_limit_exceeded$",
                ):
                    open_workbook(path)

    def test_ooxml_metadata_rejects_excessive_non_cell_nodes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "many-nodes.xlsx")

            with mock.patch.object(
                workbooks,
                "MAX_METADATA_NODES",
                1,
            ):
                with self.assertRaisesRegex(
                    WorkbookSafetyError,
                    "^ooxml_metadata_node_limit_exceeded$",
                ):
                    open_workbook(path)

    def test_ooxml_metadata_rejects_oversized_key_xml_member(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "large-metadata.xlsx")

            with mock.patch.object(
                workbooks,
                "MAX_METADATA_MEMBER_BYTES",
                1,
            ):
                with self.assertRaisesRegex(
                    WorkbookSafetyError,
                    "^ooxml_metadata_member_too_large$",
                ):
                    open_workbook(path)

    def test_unreferenced_worksheet_relationships_count_toward_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "extra-rel.xlsx")

            def add_unreferenced_worksheet(content: bytes) -> bytes:
                relationship = (
                    b'<Relationship Id="rId-unreferenced" '
                    b'Type="http://schemas.openxmlformats.org/'
                    b'officeDocument/2006/relationships/worksheet" '
                    b'Target="worksheets/unused.xml"/>'
                )
                return content.replace(
                    b"</Relationships>",
                    relationship + b"</Relationships>",
                )

            rewrite_zip_member(
                path,
                "xl/_rels/workbook.xml.rels",
                add_unreferenced_worksheet,
            )

            reader = None
            try:
                with mock.patch.object(workbooks, "MAX_WORKSHEETS", 1):
                    with self.assertRaisesRegex(
                        WorkbookSafetyError,
                        "^worksheet_limit_exceeded$",
                    ):
                        reader = open_workbook(path)
            finally:
                if reader is not None:
                    reader.close()

    def test_worksheet_limit_is_checked_before_openpyxl_load(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx_with_sheet_count(
                Path(temp_dir) / "many-before-load.xlsx",
                sheet_count=201,
            )

            with mock.patch(
                "open_tax_check.workbooks._open_binary_stream",
                side_effect=AssertionError(
                    "openpyxl streams must not open"
                ),
            ) as stream_opener:
                with self.assertRaisesRegex(
                    WorkbookSafetyError,
                    "^worksheet_limit_exceeded$",
                ):
                    open_workbook(path)

        stream_opener.assert_not_called()

    def test_chartsheet_relationship_is_ignored_and_worksheet_order_stays_aligned(self):
        from openpyxl import Workbook
        from openpyxl.chart import PieChart, Reference

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "chart.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Data"
            sheet.append(("value",))
            sheet.append((1,))
            sheet["B1"] = "=1+1"
            chart = PieChart()
            chart.add_data(
                Reference(sheet, min_col=1, min_row=1, max_row=2),
                titles_from_data=True,
            )
            chartsheet = workbook.create_chartsheet("Chart")
            chartsheet.add_chart(chart)
            workbook.save(path)
            workbook.close()

            with open_workbook(path) as reader:
                sheets = reader.sheets()

        self.assertEqual(len(sheets), 1)
        self.assertEqual(sheets[0].name, "Data")
        self.assertIs(sheets[0].has_formulas, True)

    def test_xls_sheet_metadata_uses_unknown_for_unavailable_formula_detection(self):
        row_info = types.SimpleNamespace(hidden=1)
        column_info = types.SimpleNamespace(hidden=0)
        sheet = types.SimpleNamespace(
            name="Journal",
            nrows=3,
            ncols=3,
            merged_cells=[(0, 1, 0, 2)],
            rowinfo_map={2: row_info},
            colinfo_map={0: column_info},
        )
        workbook = types.SimpleNamespace(
            nsheets=1,
            sheet_visibility=(0,),
            sheet_by_index=mock.Mock(return_value=sheet),
            release_resources=mock.Mock(),
        )
        reader = workbooks.XlrdReader(workbook)

        metadata = reader.sheets()[0]

        self.assertIsNone(metadata.has_formulas)
        self.assertIs(metadata.has_merged_ranges, True)
        self.assertIs(metadata.has_hidden_data, True)
        self.assertEqual(metadata.merged_ranges, ("A1:B1",))

    def test_ooxml_formula_and_cached_rows_are_traversed_once_in_lockstep(
        self,
    ):
        formula_cell = types.SimpleNamespace(
            row=1,
            column=1,
            coordinate="A1",
            value="=1+1",
            data_type="f",
        )
        cached_cell = types.SimpleNamespace(
            row=1,
            column=1,
            coordinate="A1",
            value=2,
            data_type="n",
        )
        formula_sheet = types.SimpleNamespace(
            iter_rows=mock.Mock(return_value=iter(((formula_cell,),))),
            cell=mock.Mock(
                side_effect=AssertionError("cell() must not be used")
            ),
        )
        cached_sheet = types.SimpleNamespace(
            iter_rows=mock.Mock(return_value=iter(((cached_cell,),))),
            cell=mock.Mock(
                side_effect=AssertionError("cell() must not be used")
            ),
        )
        reader = OpenPyxlReader(
            types.SimpleNamespace(worksheets=[formula_sheet]),
            types.SimpleNamespace(worksheets=[cached_sheet]),
        )

        rows = tuple(reader.iter_rows("sheet-0001", 1, 1, 1))

        self.assertEqual(rows[0][0].formula, "=1+1")
        self.assertEqual(rows[0][0].cached_value, 2)
        formula_sheet.iter_rows.assert_called_once_with(
            min_row=1,
            max_row=1,
            min_col=1,
            max_col=1,
        )
        cached_sheet.iter_rows.assert_called_once_with(
            min_row=1,
            max_row=1,
            min_col=1,
            max_col=1,
        )
        formula_sheet.cell.assert_not_called()
        cached_sheet.cell.assert_not_called()

    def test_ooxml_lockstep_length_mismatch_maps_to_corrupt_workbook(self):
        formula_cell = types.SimpleNamespace(
            coordinate="A1",
            value="=1+1",
            data_type="f",
        )
        formula_sheet = types.SimpleNamespace(
            iter_rows=mock.Mock(return_value=iter(((formula_cell,),)))
        )
        cached_sheet = types.SimpleNamespace(
            iter_rows=mock.Mock(return_value=iter(((),)))
        )
        reader = OpenPyxlReader(
            types.SimpleNamespace(worksheets=[formula_sheet]),
            types.SimpleNamespace(worksheets=[cached_sheet]),
        )

        with self.assertRaisesRegex(
            WorkbookError,
            "^corrupt_workbook$",
        ):
            tuple(reader.iter_rows("sheet-0001", 1, 1, 1))

    def test_ooxml_lockstep_coordinate_mismatch_is_safe_corruption(self):
        sensitive_coordinate = "PRIVATE-CELL"
        formula_cell = types.SimpleNamespace(
            coordinate="A1",
            value="=1+1",
            data_type="f",
        )
        cached_cell = types.SimpleNamespace(
            coordinate=sensitive_coordinate,
            value=2,
            data_type="n",
        )
        formula_sheet = types.SimpleNamespace(
            iter_rows=mock.Mock(return_value=iter(((formula_cell,),)))
        )
        cached_sheet = types.SimpleNamespace(
            iter_rows=mock.Mock(return_value=iter(((cached_cell,),)))
        )
        reader = OpenPyxlReader(
            types.SimpleNamespace(worksheets=[formula_sheet]),
            types.SimpleNamespace(worksheets=[cached_sheet]),
        )

        with self.assertRaisesRegex(
            WorkbookError,
            "^corrupt_workbook$",
        ) as raised:
            tuple(reader.iter_rows("sheet-0001", 1, 1, 1))

        self.assertNotIn(sensitive_coordinate, str(raised.exception))

    def test_delayed_workbook_safety_error_keeps_original_code(self):
        class SafetyWorksheet:
            def iter_rows(self, **_kwargs):
                raise WorkbookSafetyError("visited_cell_limit_exceeded")

        reader = OpenPyxlReader(
            types.SimpleNamespace(worksheets=[SafetyWorksheet()]),
            types.SimpleNamespace(worksheets=[types.SimpleNamespace()]),
        )

        with self.assertRaisesRegex(
            WorkbookSafetyError,
            "^visited_cell_limit_exceeded$",
        ):
            tuple(reader.iter_rows("sheet-0001", 1, 1, 1))

    def test_xlsx_sheet_metadata_uses_stable_one_based_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx_with_hidden_sheet(
                Path(temp_dir) / "sheets.xlsx"
            )

            with open_workbook(path) as book:
                sheets = book.sheets()

        self.assertEqual(
            sheets,
            (
                workbooks.SheetMeta(
                    sheet_id="sheet-0001",
                    index=1,
                    name="Journal",
                    visibility="visible",
                    max_row=2,
                    max_column=2,
                    has_formulas=False,
                    has_merged_ranges=False,
                    has_hidden_data=False,
                ),
                workbooks.SheetMeta(
                    sheet_id="sheet-0002",
                    index=2,
                    name="Notes",
                    visibility="hidden",
                    max_row=3,
                    max_column=3,
                    has_formulas=False,
                    has_merged_ranges=False,
                    has_hidden_data=True,
                ),
            ),
        )

    def test_sheet_zero_is_rejected_instead_of_selecting_last_sheet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx_with_hidden_sheet(
                Path(temp_dir) / "sheets.xlsx"
            )

            with open_workbook(path) as book:
                with self.assertRaisesRegex(
                    WorkbookError,
                    "^worksheet_not_found$",
                ):
                    tuple(book.iter_rows("sheet-0000", 1, 1, 1))

    def test_ooxml_preflight_rejects_excessive_declared_ratio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_zip_ratio_fixture(
                Path(temp_dir) / "bomb.xlsx",
                ratio=101,
            )

            with self.assertRaisesRegex(
                WorkbookSafetyError,
                "ooxml_compression_ratio_exceeded",
            ):
                open_workbook(path)

    def test_ooxml_preflight_rejects_danger_before_dependency_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_zip_ratio_fixture(
                Path(temp_dir) / "bomb.xlsx",
                ratio=101,
            )

            with mock.patch(
                "open_tax_check.workbooks.import_module",
                side_effect=AssertionError(
                    "dependency import must follow preflight"
                ),
            ) as importer:
                with self.assertRaisesRegex(
                    WorkbookSafetyError,
                    "^ooxml_compression_ratio_exceeded$",
                ):
                    open_workbook(path)

        importer.assert_not_called()

    def test_ooxml_preflight_rejects_more_than_ten_thousand_members(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_zip_member_count_fixture(
                Path(temp_dir) / "many-members.xlsx",
                member_count=10_001,
            )

            with self.assertRaisesRegex(
                WorkbookSafetyError,
                "ooxml_member_limit_exceeded",
            ):
                open_workbook(path)

    def test_ooxml_preflight_rejects_excessive_declared_total_size(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_zip_uncompressed_fixture(
                Path(temp_dir) / "oversized.xlsx",
                member_sizes=(6, 5),
            )

            with mock.patch.object(
                workbooks,
                "MAX_UNCOMPRESSED_BYTES",
                10,
            ):
                with self.assertRaisesRegex(
                    WorkbookSafetyError,
                    "ooxml_uncompressed_limit_exceeded",
                ):
                    open_workbook(path)

    def test_ooxml_preflight_rejects_path_traversal_member_names(self):
        unsafe_names = (
            "../outside.xml",
            r"xl\..\outside.xml",
            "/absolute.xml",
            "C:/absolute.xml",
        )
        for index, unsafe_name in enumerate(unsafe_names):
            with self.subTest(member_name=unsafe_name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    path = make_zip_member_name_fixture(
                        Path(temp_dir) / f"unsafe-{index}.xlsx",
                        member_name=unsafe_name,
                    )

                    with self.assertRaisesRegex(
                        WorkbookSafetyError,
                        "ooxml_unsafe_member_name",
                    ):
                        open_workbook(path)

    def test_ooxml_preflight_rejects_windows_drive_relative_member(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_zip_member_name_fixture(
                Path(temp_dir) / "drive-relative.xlsx",
                member_name="C:relative.xml",
            )

            with self.assertRaisesRegex(
                WorkbookSafetyError,
                "^ooxml_unsafe_member_name$",
            ):
                open_workbook(path)

    def test_corrupt_ooxml_has_stable_safe_diagnostic(self):
        sensitive_detail = "private-cell-value"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "broken.xlsx"
            path.write_bytes(b"not-a-zip-" + sensitive_detail.encode("utf-8"))

            with self.assertRaisesRegex(
                WorkbookError,
                "^corrupt_workbook$",
            ) as raised:
                open_workbook(path)

        self.assertNotIn(sensitive_detail, str(raised.exception))
        self.assertNotIn(str(path), str(raised.exception))

    def test_invalid_zip_metadata_encoding_maps_to_corrupt_workbook(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_bad_zip_metadata_fixture(
                Path(temp_dir) / "bad-metadata.xlsx"
            )

            with self.assertRaisesRegex(
                WorkbookError,
                "^corrupt_workbook$",
            ) as raised:
                open_workbook(path)

        self.assertNotIn(str(path), str(raised.exception))

    def test_ooxml_io_error_has_distinct_stable_safe_diagnostic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "missing.xlsx"

            with self.assertRaisesRegex(
                WorkbookError,
                "^workbook_io_error$",
            ) as raised:
                open_workbook(path)

        self.assertNotIn(str(path), str(raised.exception))

    def test_ooxml_ole_header_maps_to_encrypted_workbook(self):
        sensitive_detail = "private-cell-value"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "encrypted.xlsx"
            path.write_bytes(
                bytes.fromhex("D0CF11E0A1B11AE1")
                + sensitive_detail.encode("utf-8")
            )

            with self.assertRaisesRegex(
                WorkbookError,
                "^encrypted_workbook$",
            ) as raised:
                open_workbook(path)

        self.assertNotIn(sensitive_detail, str(raised.exception))
        self.assertNotIn(str(path), str(raised.exception))

    def test_library_explicit_encryption_error_has_stable_diagnostic(self):
        sensitive_detail = "private-cell-value"
        fake_openpyxl = types.SimpleNamespace(
            load_workbook=mock.Mock(
                side_effect=RuntimeError(
                    "File contains encrypted content " + sensitive_detail
                )
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "book.xlsx")

            with mock.patch(
                "open_tax_check.workbooks.import_module",
                return_value=fake_openpyxl,
            ):
                with self.assertRaisesRegex(
                    WorkbookError,
                    "^encrypted_workbook$",
                ) as raised:
                    open_workbook(path)

        self.assertNotIn(sensitive_detail, str(raised.exception))
        self.assertNotIn(str(path), str(raised.exception))

    def test_openpyxl_invalid_file_encryption_error_maps_to_encrypted(self):
        invalid_file_error = type("InvalidFileException", (Exception,), {})
        fake_openpyxl = types.SimpleNamespace(
            load_workbook=mock.Mock(
                side_effect=invalid_file_error(
                    "Workbook is password protected"
                )
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "book.xlsx")

            with mock.patch(
                "open_tax_check.workbooks.import_module",
                return_value=fake_openpyxl,
            ):
                with self.assertRaisesRegex(
                    WorkbookError,
                    "^encrypted_workbook$",
                ):
                    open_workbook(path)

    def test_reader_rejects_more_than_two_hundred_sheets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx_with_sheet_count(
                Path(temp_dir) / "many.xlsx",
                sheet_count=201,
            )

            with self.assertRaisesRegex(
                WorkbookSafetyError,
                "^worksheet_limit_exceeded$",
            ):
                open_workbook(path)

    def test_selected_sheet_stops_before_visiting_more_than_five_million_cells(
        self,
    ):
        class NeverIterWorksheet:
            def iter_rows(self, **_kwargs):
                raise AssertionError("cell iteration must not begin")

        formula_workbook = types.SimpleNamespace(
            worksheets=[NeverIterWorksheet()],
            close=mock.Mock(),
        )
        cached_workbook = types.SimpleNamespace(
            worksheets=[NeverIterWorksheet()],
            close=mock.Mock(),
        )
        reader = OpenPyxlReader(formula_workbook, cached_workbook)

        with self.assertRaisesRegex(
            WorkbookSafetyError,
            "^visited_cell_limit_exceeded$",
        ):
            tuple(reader.iter_rows("sheet-0001", 1, 20_000, 256))

    def test_delayed_ooxml_parse_error_has_stable_safe_diagnostic(self):
        sensitive_detail = "private-cell-value"

        class FailingWorksheet:
            def iter_rows(self, **_kwargs):
                raise ParseError(sensitive_detail)

        formula_workbook = types.SimpleNamespace(
            worksheets=[FailingWorksheet()],
            close=mock.Mock(),
        )
        cached_workbook = types.SimpleNamespace(
            worksheets=[types.SimpleNamespace()],
            close=mock.Mock(),
        )
        reader = OpenPyxlReader(formula_workbook, cached_workbook)

        with self.assertRaisesRegex(
            WorkbookError,
            "^corrupt_workbook$",
        ) as raised:
            tuple(reader.iter_rows("sheet-0001", 1, 1, 1))

        self.assertNotIn(sensitive_detail, str(raised.exception))

    def test_ooxml_close_has_stable_diagnostic_and_attempts_all_resources(
        self,
    ):
        sensitive_detail = "PRIVATE-OOXML-CLOSE-DETAIL"
        formula_workbook = types.SimpleNamespace(
            close=mock.Mock(side_effect=OSError(sensitive_detail))
        )
        cached_workbook = types.SimpleNamespace(close=mock.Mock())
        formula_stream = types.SimpleNamespace(
            close=mock.Mock(side_effect=OSError("stream close detail"))
        )
        cached_stream = types.SimpleNamespace(close=mock.Mock())
        reader = OpenPyxlReader(
            formula_workbook,
            cached_workbook,
            formula_stream,
            cached_stream,
        )

        with self.assertRaisesRegex(
            WorkbookError,
            "^workbook_io_error$",
        ) as raised:
            reader.close()

        formula_workbook.close.assert_called_once_with()
        cached_workbook.close.assert_called_once_with()
        formula_stream.close.assert_called_once_with()
        cached_stream.close.assert_called_once_with()
        self.assertNotIn(sensitive_detail, str(raised.exception))

    def test_ooxml_context_body_error_has_priority_over_close_error(self):
        sensitive_detail = "PRIVATE-OOXML-CLOSE-DETAIL"
        formula_workbook = types.SimpleNamespace(
            close=mock.Mock(side_effect=OSError(sensitive_detail))
        )
        cached_workbook = types.SimpleNamespace(close=mock.Mock())
        formula_stream = types.SimpleNamespace(close=mock.Mock())
        cached_stream = types.SimpleNamespace(close=mock.Mock())
        reader = OpenPyxlReader(
            formula_workbook,
            cached_workbook,
            formula_stream,
            cached_stream,
        )

        with self.assertRaisesRegex(RuntimeError, "^BODY-ERROR$"):
            with reader:
                raise RuntimeError("BODY-ERROR")

        formula_workbook.close.assert_called_once_with()
        cached_workbook.close.assert_called_once_with()
        formula_stream.close.assert_called_once_with()
        cached_stream.close.assert_called_once_with()

    def test_second_ooxml_open_failure_closes_first_without_leaking_close_error(
        self,
    ):
        sensitive_detail = "private-parser-detail"
        sensitive_close_detail = "private-close-detail"
        formula_workbook = types.SimpleNamespace(
            worksheets=[],
            close=mock.Mock(
                side_effect=OSError(sensitive_close_detail)
            ),
        )
        fake_openpyxl = types.SimpleNamespace(
            load_workbook=mock.Mock(
                side_effect=(
                    formula_workbook,
                    ValueError(sensitive_detail),
                )
            )
        )
        formula_stream = io.BytesIO(b"formula stream")
        cached_stream = io.BytesIO(b"cached stream")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "book.xlsx")

            with mock.patch(
                "open_tax_check.workbooks.import_module",
                return_value=fake_openpyxl,
            ):
                with mock.patch(
                    "open_tax_check.workbooks._open_binary_stream",
                    side_effect=(formula_stream, cached_stream),
                ):
                    with self.assertRaisesRegex(
                        WorkbookError,
                        "^corrupt_workbook$",
                    ) as raised:
                        open_workbook(path)

        formula_workbook.close.assert_called_once_with()
        self.assertTrue(formula_stream.closed)
        self.assertTrue(cached_stream.closed)
        self.assertNotIn(sensitive_detail, str(raised.exception))
        self.assertNotIn(sensitive_close_detail, str(raised.exception))

    def test_first_ooxml_load_failure_closes_owned_binary_stream(self):
        stream = io.BytesIO(b"formula stream")
        fake_openpyxl = types.SimpleNamespace(
            load_workbook=mock.Mock(
                side_effect=ValueError("private parser detail")
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "book.xlsx")

            with mock.patch(
                "open_tax_check.workbooks.import_module",
                return_value=fake_openpyxl,
            ):
                with mock.patch(
                    "open_tax_check.workbooks._open_binary_stream",
                    return_value=stream,
                ):
                    with self.assertRaisesRegex(
                        WorkbookError,
                        "^corrupt_workbook$",
                    ):
                        open_workbook(path)

        self.assertTrue(stream.closed)

    def test_xlsm_uses_two_hardened_read_only_handles(self):
        formula_workbook = types.SimpleNamespace(
            worksheets=[],
            close=mock.Mock(),
        )
        cached_workbook = types.SimpleNamespace(
            worksheets=[],
            close=mock.Mock(),
        )
        fake_openpyxl = types.SimpleNamespace(
            load_workbook=mock.Mock(
                side_effect=(formula_workbook, cached_workbook)
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "book.xlsm")

            with mock.patch(
                "open_tax_check.workbooks.import_module",
                return_value=fake_openpyxl,
            ):
                with open_workbook(path):
                    pass

        calls = fake_openpyxl.load_workbook.call_args_list
        self.assertEqual(len(calls), 2)
        formula_source = calls[0].args[0]
        cached_source = calls[1].args[0]
        self.assertIsNot(formula_source, cached_source)
        self.assertEqual(
            calls[0].kwargs,
            {
                "read_only": True,
                "data_only": False,
                "keep_links": False,
                "keep_vba": False,
            },
        )
        self.assertEqual(
            calls[1].kwargs,
            {
                "read_only": True,
                "data_only": True,
                "keep_links": False,
                "keep_vba": False,
            },
        )
        self.assertTrue(formula_source.closed)
        self.assertTrue(cached_source.closed)
        formula_workbook.close.assert_called_once_with()
        cached_workbook.close.assert_called_once_with()

    def test_ooxml_context_closes_both_handles_on_body_exception(self):
        formula_workbook = types.SimpleNamespace(
            worksheets=[],
            close=mock.Mock(),
        )
        cached_workbook = types.SimpleNamespace(
            worksheets=[],
            close=mock.Mock(),
        )
        fake_openpyxl = types.SimpleNamespace(
            load_workbook=mock.Mock(
                side_effect=(formula_workbook, cached_workbook)
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "book.xlsx")

            with mock.patch(
                "open_tax_check.workbooks.import_module",
                return_value=fake_openpyxl,
            ):
                with self.assertRaisesRegex(RuntimeError, "body failure"):
                    with open_workbook(path):
                        raise RuntimeError("body failure")

        formula_workbook.close.assert_called_once_with()
        cached_workbook.close.assert_called_once_with()

    def test_missing_excel_extra_has_stable_action(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "book.xlsx")
            with mock.patch(
                "open_tax_check.workbooks.import_module",
                side_effect=ModuleNotFoundError("sensitive import detail"),
            ):
                with self.assertRaisesRegex(
                    ExcelExtraRequired,
                    "^install_excel_extra$",
                ) as raised:
                    open_workbook(path)

        self.assertNotIn("sensitive import detail", str(raised.exception))

    def test_missing_defusedxml_has_stable_excel_extra_action(self):
        def import_excel_dependency(name):
            if name == "defusedxml":
                raise ModuleNotFoundError("sensitive defusedxml detail")
            return importlib.import_module(name)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "book.xlsx")
            with mock.patch(
                "open_tax_check.workbooks.import_module",
                side_effect=import_excel_dependency,
            ):
                with self.assertRaisesRegex(
                    ExcelExtraRequired,
                    "^install_excel_extra$",
                ) as raised:
                    open_workbook(path)

        self.assertNotIn(
            "sensitive defusedxml detail",
            str(raised.exception),
        )

    def test_unsupported_workbook_suffix_has_stable_diagnostic(self):
        with self.assertRaisesRegex(
            UnsupportedWorkbook,
            "^unsupported_workbook_format$",
        ):
            open_workbook(Path("book.xlsb"))

    def test_fictional_xls_fixture_is_minimal_balanced_journal(self):
        path = FIXTURES / "fictional-journal.xls"

        with open_workbook(path) as book:
            sheets = book.sheets()
            rows = tuple(book.iter_rows("sheet-0001", 1, 4, 5))

        self.assertEqual(len(sheets), 1)
        self.assertEqual(sheets[0].name, "序时账")
        self.assertEqual(sheets[0].visibility, "visible")
        self.assertEqual(sheets[0].max_row, 4)
        self.assertEqual(sheets[0].max_column, 5)
        self.assertEqual(rows[0][0].value, "虚构企业有限公司")
        self.assertEqual(
            tuple(cell.value for cell in rows[1]),
            ("日期", "凭证号", "科目", "借方金额", "贷方金额"),
        )
        self.assertEqual(rows[2][3].value, 1000.0)
        self.assertEqual(rows[2][4].value, 0.0)
        self.assertEqual(rows[3][3].value, 0.0)
        self.assertEqual(rows[3][4].value, 1000.0)
        self.assertTrue(
            all(
                cell.formula is None
                and cell.cached_value is None
                and cell.value_origin == "stored_value"
                for row in rows
                for cell in row
            )
        )

    def test_xls_opens_on_demand_and_releases_on_body_exception(self):
        workbook = types.SimpleNamespace(
            nsheets=0,
            release_resources=mock.Mock(),
        )
        fake_xlrd = types.SimpleNamespace(
            open_workbook=mock.Mock(return_value=workbook)
        )
        path = Path("fictional.xls")

        with mock.patch(
            "open_tax_check.workbooks.import_module",
            return_value=fake_xlrd,
        ):
            with self.assertRaisesRegex(RuntimeError, "body failure"):
                with open_workbook(path):
                    raise RuntimeError("body failure")

        fake_xlrd.open_workbook.assert_called_once_with(
            path,
            on_demand=True,
            formatting_info=True,
        )
        workbook.release_resources.assert_called_once_with()

    def test_xls_explicit_encryption_error_has_stable_diagnostic(self):
        sensitive_detail = "private-encryption-detail"
        xlrd_error = type("XLRDError", (Exception,), {})
        fake_xlrd = types.SimpleNamespace(
            open_workbook=mock.Mock(
                side_effect=xlrd_error(
                    "Workbook is encrypted " + sensitive_detail
                )
            )
        )

        with mock.patch(
            "open_tax_check.workbooks.import_module",
            return_value=fake_xlrd,
        ):
            with self.assertRaisesRegex(
                WorkbookError,
                "^encrypted_workbook$",
            ) as raised:
                open_workbook(Path("encrypted.xls"))

        self.assertNotIn(sensitive_detail, str(raised.exception))

    def test_corrupt_xls_has_stable_safe_diagnostic(self):
        sensitive_detail = "private-cell-value"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "broken.xls"
            path.write_bytes(sensitive_detail.encode("utf-8"))

            with self.assertRaisesRegex(
                WorkbookError,
                "^corrupt_workbook$",
            ) as raised:
                open_workbook(path)

        self.assertNotIn(sensitive_detail, str(raised.exception))
        self.assertNotIn(str(path), str(raised.exception))

    def test_xls_reader_rejects_too_many_sheets_and_releases_resources(self):
        workbook = types.SimpleNamespace(
            nsheets=201,
            release_resources=mock.Mock(),
        )
        fake_xlrd = types.SimpleNamespace(
            open_workbook=mock.Mock(return_value=workbook)
        )

        with mock.patch(
            "open_tax_check.workbooks.import_module",
            return_value=fake_xlrd,
        ):
            with self.assertRaisesRegex(
                WorkbookSafetyError,
                "^worksheet_limit_exceeded$",
            ):
                open_workbook(Path("many.xls"))

        workbook.release_resources.assert_called_once_with()

    def test_xls_sheets_lazy_parse_error_has_stable_safe_diagnostic(self):
        sensitive_detail = "PRIVATE-LAZY-XLS-DETAIL"
        xlrd_error = type("XLRDError", (Exception,), {})
        workbook = types.SimpleNamespace(
            nsheets=1,
            sheet_visibility=(0,),
            sheet_by_index=mock.Mock(
                side_effect=xlrd_error(sensitive_detail)
            ),
            release_resources=mock.Mock(),
        )
        reader = workbooks.XlrdReader(workbook)

        with self.assertRaisesRegex(
            WorkbookError,
            "^corrupt_workbook$",
        ) as raised:
            reader.sheets()

        self.assertNotIn(sensitive_detail, str(raised.exception))

    def test_xls_generator_lazy_io_error_has_stable_safe_diagnostic(self):
        sensitive_detail = "PRIVATE-LAZY-XLS-DETAIL"
        sheet = types.SimpleNamespace(
            nrows=1,
            ncols=1,
            cell=mock.Mock(side_effect=OSError(sensitive_detail)),
        )
        workbook = types.SimpleNamespace(
            nsheets=1,
            sheet_by_index=mock.Mock(return_value=sheet),
            unload_sheet=mock.Mock(),
            release_resources=mock.Mock(),
        )
        reader = workbooks.XlrdReader(workbook)

        with self.assertRaisesRegex(
            WorkbookError,
            "^workbook_io_error$",
        ) as raised:
            tuple(reader.iter_rows("sheet-0001", 1, 1, 1))

        workbook.unload_sheet.assert_called_once_with(0)
        self.assertNotIn(sensitive_detail, str(raised.exception))

    def test_xls_generator_lazy_parse_error_has_stable_safe_diagnostic(self):
        sensitive_detail = "PRIVATE-LAZY-XLS-DETAIL"
        xlrd_error = type("XLRDError", (Exception,), {})
        workbook = types.SimpleNamespace(
            nsheets=1,
            sheet_by_index=mock.Mock(
                side_effect=xlrd_error(sensitive_detail)
            ),
            unload_sheet=mock.Mock(),
            release_resources=mock.Mock(),
        )
        reader = workbooks.XlrdReader(workbook)

        with self.assertRaisesRegex(
            WorkbookError,
            "^corrupt_workbook$",
        ) as raised:
            tuple(reader.iter_rows("sheet-0001", 1, 1, 1))

        workbook.unload_sheet.assert_not_called()
        self.assertNotIn(sensitive_detail, str(raised.exception))

    def test_xls_generator_preserves_workbook_safety_error(self):
        workbook = types.SimpleNamespace(
            nsheets=1,
            sheet_by_index=mock.Mock(),
            unload_sheet=mock.Mock(),
            release_resources=mock.Mock(),
        )
        reader = workbooks.XlrdReader(workbook)

        with mock.patch.object(workbooks, "MAX_VISITED_CELLS", 0):
            with self.assertRaisesRegex(
                WorkbookSafetyError,
                "^visited_cell_limit_exceeded$",
            ):
                tuple(reader.iter_rows("sheet-0001", 1, 1, 1))

        workbook.sheet_by_index.assert_not_called()

    def test_xls_close_has_stable_diagnostic(self):
        sensitive_detail = "PRIVATE-XLS-CLOSE-DETAIL"
        workbook = types.SimpleNamespace(
            release_resources=mock.Mock(
                side_effect=OSError(sensitive_detail)
            )
        )
        reader = workbooks.XlrdReader(workbook)

        with self.assertRaisesRegex(
            WorkbookError,
            "^workbook_io_error$",
        ) as raised:
            reader.close()

        workbook.release_resources.assert_called_once_with()
        self.assertNotIn(sensitive_detail, str(raised.exception))

    def test_xls_context_body_error_has_priority_over_close_error(self):
        workbook = types.SimpleNamespace(
            release_resources=mock.Mock(
                side_effect=OSError("PRIVATE-XLS-CLOSE-DETAIL")
            )
        )
        reader = workbooks.XlrdReader(workbook)

        with self.assertRaisesRegex(RuntimeError, "^BODY-ERROR$"):
            with reader:
                raise RuntimeError("BODY-ERROR")

        workbook.release_resources.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
