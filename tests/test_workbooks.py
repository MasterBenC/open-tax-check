from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

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
    make_zip_member_count_fixture,
    make_zip_member_name_fixture,
    make_zip_ratio_fixture,
    make_zip_uncompressed_fixture,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


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
                ),
                workbooks.SheetMeta(
                    sheet_id="sheet-0002",
                    index=2,
                    name="Notes",
                    visibility="hidden",
                    max_row=3,
                    max_column=3,
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

    def test_close_attempts_both_ooxml_handles_if_first_close_fails(self):
        close_error = OSError("sensitive close detail")
        formula_workbook = types.SimpleNamespace(
            close=mock.Mock(side_effect=close_error)
        )
        cached_workbook = types.SimpleNamespace(close=mock.Mock())
        reader = OpenPyxlReader(formula_workbook, cached_workbook)

        with self.assertRaises(OSError):
            reader.close()

        formula_workbook.close.assert_called_once_with()
        cached_workbook.close.assert_called_once_with()

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
        with tempfile.TemporaryDirectory() as temp_dir:
            path = make_xlsx(Path(temp_dir) / "book.xlsx")

            with mock.patch(
                "open_tax_check.workbooks.import_module",
                return_value=fake_openpyxl,
            ):
                with self.assertRaisesRegex(
                    WorkbookError,
                    "^corrupt_workbook$",
                ) as raised:
                    open_workbook(path)

        formula_workbook.close.assert_called_once_with()
        self.assertNotIn(sensitive_detail, str(raised.exception))
        self.assertNotIn(sensitive_close_detail, str(raised.exception))

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

        self.assertEqual(
            fake_openpyxl.load_workbook.call_args_list,
            [
                mock.call(
                    path,
                    read_only=True,
                    data_only=False,
                    keep_links=False,
                    keep_vba=False,
                ),
                mock.call(
                    path,
                    read_only=True,
                    data_only=True,
                    keep_links=False,
                    keep_vba=False,
                ),
            ],
        )
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
        with mock.patch(
            "open_tax_check.workbooks.import_module",
            side_effect=ModuleNotFoundError("sensitive import detail"),
        ):
            with self.assertRaisesRegex(
                ExcelExtraRequired,
                "^install_excel_extra$",
            ) as raised:
                open_workbook(Path("book.xlsx"))

        self.assertNotIn("sensitive import detail", str(raised.exception))

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


if __name__ == "__main__":
    unittest.main()
