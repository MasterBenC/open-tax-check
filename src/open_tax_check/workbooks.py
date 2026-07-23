"""Bounded, read-only adapters for supported Excel workbook formats."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from importlib import import_module
from itertools import zip_longest
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterator
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile


MAX_COMPRESSION_RATIO = 100
MAX_ZIP_MEMBERS = 10_000
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_VISITED_CELLS = 5_000_000
MAX_WORKSHEETS = 200
OLE_COMPOUND_HEADER = bytes.fromhex("D0CF11E0A1B11AE1")
_MISSING = object()


class WorkbookError(Exception):
    """Base class for stable workbook diagnostics."""


class WorkbookSafetyError(WorkbookError):
    """Raised when a workbook exceeds a safety boundary."""


class ExcelExtraRequired(WorkbookError):
    """Raised when the optional Excel reader dependencies are unavailable."""


class UnsupportedWorkbook(WorkbookError):
    """Raised for workbook formats outside the supported contract."""


@dataclass(frozen=True, slots=True)
class CellValue:
    row: int
    column: int
    coordinate: str
    value: Any
    formula: str | None
    cached_value: Any
    data_type: str
    value_origin: str


@dataclass(frozen=True, slots=True)
class SheetMeta:
    sheet_id: str
    index: int
    name: str
    visibility: str
    max_row: int
    max_column: int


class WorkbookReader:
    """Context-managed workbook reader interface."""

    def __enter__(self) -> WorkbookReader:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def sheets(self) -> tuple[SheetMeta, ...]:
        raise NotImplementedError

    def iter_rows(
        self,
        sheet_id: str,
        min_row: int,
        max_row: int,
        max_col: int,
    ) -> Iterator[tuple[CellValue, ...]]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class OpenPyxlReader(WorkbookReader):
    def __init__(
        self,
        formula_workbook: Any,
        cached_workbook: Any,
        formula_stream: Any = None,
        cached_stream: Any = None,
    ) -> None:
        self._formula_workbook = formula_workbook
        self._cached_workbook = cached_workbook
        self._formula_stream = formula_stream
        self._cached_stream = cached_stream

    def sheets(self) -> tuple[SheetMeta, ...]:
        return tuple(
            SheetMeta(
                sheet_id=f"sheet-{index:04d}",
                index=index,
                name=sheet.title,
                visibility=sheet.sheet_state,
                max_row=sheet.max_row,
                max_column=sheet.max_column,
            )
            for index, sheet in enumerate(self._formula_workbook.worksheets, start=1)
        )

    def _worksheet(self, sheet_id: str, *, cached: bool = False) -> Any:
        workbook = self._cached_workbook if cached else self._formula_workbook
        index = _sheet_index(sheet_id, len(workbook.worksheets))
        return workbook.worksheets[index]

    def iter_rows(
        self,
        sheet_id: str,
        min_row: int,
        max_row: int,
        max_col: int,
    ) -> Iterator[tuple[CellValue, ...]]:
        worksheet = self._worksheet(sheet_id)
        cached_worksheet = self._worksheet(sheet_id, cached=True)
        row_count = max(0, max_row - min_row + 1)
        if row_count * max_col > MAX_VISITED_CELLS:
            raise WorkbookSafetyError("visited_cell_limit_exceeded")
        try:
            iteration_options = {
                "min_row": min_row,
                "max_row": max_row,
                "min_col": 1,
                "max_col": max_col,
            }
            formula_rows = worksheet.iter_rows(**iteration_options)
            cached_rows = cached_worksheet.iter_rows(**iteration_options)
            visited_rows = 0
            for row_index, (formula_row, cached_row) in enumerate(
                zip_longest(
                    formula_rows,
                    cached_rows,
                    fillvalue=_MISSING,
                ),
                start=min_row,
            ):
                if formula_row is _MISSING or cached_row is _MISSING:
                    raise WorkbookError("corrupt_workbook")
                formula_cells = tuple(formula_row)
                cached_cells = tuple(cached_row)
                if (
                    len(formula_cells) != max_col
                    or len(cached_cells) != max_col
                ):
                    raise WorkbookError("corrupt_workbook")
                values = []
                for column_index, (cell, cached_cell) in enumerate(
                    zip(formula_cells, cached_cells),
                    start=1,
                ):
                    coordinate = _coordinate(row_index, column_index)
                    formula_coordinate = getattr(
                        cell,
                        "coordinate",
                        coordinate,
                    )
                    cached_coordinate = getattr(
                        cached_cell,
                        "coordinate",
                        coordinate,
                    )
                    if (
                        formula_coordinate != coordinate
                        or cached_coordinate != coordinate
                    ):
                        raise WorkbookError("corrupt_workbook")
                    value = getattr(cell, "value", None)
                    data_type = getattr(cell, "data_type", "n")
                    formula = value if data_type == "f" else None
                    cached_value = (
                        getattr(cached_cell, "value", None)
                        if formula is not None
                        else None
                    )
                    values.append(
                        CellValue(
                            row=row_index,
                            column=column_index,
                            coordinate=coordinate,
                            value=value,
                            formula=formula,
                            cached_value=cached_value,
                            data_type=data_type,
                            value_origin=(
                                "formula"
                                if formula is not None
                                else "literal"
                            ),
                        )
                    )
                yield tuple(values)
                visited_rows += 1
            if visited_rows != row_count:
                raise WorkbookError("corrupt_workbook")
        except (ParseError, UnicodeDecodeError):
            raise WorkbookError("corrupt_workbook") from None

    def close(self) -> None:
        first_error = None
        for resource in (
            self._formula_workbook,
            self._cached_workbook,
            self._formula_stream,
            self._cached_stream,
        ):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error


class XlrdReader(WorkbookReader):
    _DATA_TYPES = {
        0: "empty",
        1: "text",
        2: "number",
        3: "date",
        4: "boolean",
        5: "error",
        6: "blank",
    }
    _VISIBILITY = {
        0: "visible",
        1: "hidden",
        2: "veryHidden",
    }

    def __init__(self, workbook: Any) -> None:
        self._workbook = workbook
        self._closed = False

    def sheets(self) -> tuple[SheetMeta, ...]:
        metadata = []
        visibility = getattr(self._workbook, "sheet_visibility", ())
        for index in range(self._workbook.nsheets):
            sheet = self._workbook.sheet_by_index(index)
            visibility_code = visibility[index] if index < len(visibility) else 0
            metadata.append(
                SheetMeta(
                    sheet_id=f"sheet-{index + 1:04d}",
                    index=index + 1,
                    name=sheet.name,
                    visibility=self._VISIBILITY.get(
                        visibility_code,
                        "unknown",
                    ),
                    max_row=sheet.nrows,
                    max_column=sheet.ncols,
                )
            )
        return tuple(metadata)

    def iter_rows(
        self,
        sheet_id: str,
        min_row: int,
        max_row: int,
        max_col: int,
    ) -> Iterator[tuple[CellValue, ...]]:
        index = _sheet_index(sheet_id, self._workbook.nsheets)
        row_count = max(0, max_row - min_row + 1)
        if row_count * max_col > MAX_VISITED_CELLS:
            raise WorkbookSafetyError("visited_cell_limit_exceeded")
        sheet = self._workbook.sheet_by_index(index)
        try:
            for row_number in range(min_row, max_row + 1):
                values = []
                for column_number in range(1, max_col + 1):
                    if (
                        row_number <= sheet.nrows
                        and column_number <= sheet.ncols
                    ):
                        cell = sheet.cell(
                            row_number - 1,
                            column_number - 1,
                        )
                        value = cell.value
                        data_type = self._DATA_TYPES.get(
                            cell.ctype,
                            "unknown",
                        )
                        if data_type in {"empty", "blank"}:
                            value = None
                    else:
                        value = None
                        data_type = "empty"
                    values.append(
                        CellValue(
                            row=row_number,
                            column=column_number,
                            coordinate=_coordinate(
                                row_number,
                                column_number,
                            ),
                            value=value,
                            formula=None,
                            cached_value=None,
                            data_type=data_type,
                            value_origin="stored_value",
                        )
                    )
                yield tuple(values)
        finally:
            with suppress(Exception):
                self._workbook.unload_sheet(index)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._workbook.release_resources()


def _sheet_index(sheet_id: str, sheet_count: int) -> int:
    if (
        not isinstance(sheet_id, str)
        or len(sheet_id) != len("sheet-0001")
        or not sheet_id.startswith("sheet-")
        or not sheet_id[6:].isdigit()
    ):
        raise WorkbookError("worksheet_not_found")
    index = int(sheet_id[6:]) - 1
    if index < 0 or index >= sheet_count:
        raise WorkbookError("worksheet_not_found")
    return index


def _coordinate(row: int, column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row}"


def _required_module(name: str) -> Any:
    try:
        return import_module(name)
    except (ImportError, ModuleNotFoundError):
        raise ExcelExtraRequired("install_excel_extra") from None


def _open_binary_stream(path: Path) -> Any:
    try:
        return path.open("rb")
    except OSError:
        raise WorkbookError("workbook_io_error") from None


def _close_suppressing_errors(*resources: Any) -> None:
    for resource in resources:
        with suppress(Exception):
            resource.close()


def _raise_openpyxl_error(error: Exception) -> None:
    message = str(error).casefold()
    explicit_encryption_error = isinstance(
        error,
        RuntimeError,
    ) or error.__class__.__name__ == "InvalidFileException"
    if explicit_encryption_error and (
        "encrypt" in message or "password" in message
    ):
        raise WorkbookError("encrypted_workbook") from None
    if isinstance(error, OSError):
        raise WorkbookError("workbook_io_error") from None
    raise WorkbookError("corrupt_workbook") from None


def _raise_xlrd_error(error: Exception) -> None:
    message = str(error).casefold()
    if error.__class__.__name__ == "XLRDError" and (
        "encrypt" in message or "password" in message
    ):
        raise WorkbookError("encrypted_workbook") from None
    if isinstance(error, OSError):
        raise WorkbookError("workbook_io_error") from None
    raise WorkbookError("corrupt_workbook") from None


def preflight_ooxml(path: Path) -> None:
    try:
        with path.open("rb") as source:
            if source.read(len(OLE_COMPOUND_HEADER)) == OLE_COMPOUND_HEADER:
                raise WorkbookError("encrypted_workbook")
        with ZipFile(path) as package:
            members = package.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise WorkbookSafetyError("ooxml_member_limit_exceeded")
            if (
                sum(item.file_size for item in members)
                > MAX_UNCOMPRESSED_BYTES
            ):
                raise WorkbookSafetyError(
                    "ooxml_uncompressed_limit_exceeded"
                )
            for item in members:
                normalised_name = item.filename.replace("\\", "/")
                parts = PurePosixPath(normalised_name).parts
                has_drive = bool(PureWindowsPath(item.filename).drive)
                if (
                    normalised_name.startswith("/")
                    or ".." in parts
                    or has_drive
                ):
                    raise WorkbookSafetyError("ooxml_unsafe_member_name")
                ratio = item.file_size / max(item.compress_size, 1)
                if ratio > MAX_COMPRESSION_RATIO:
                    raise WorkbookSafetyError(
                        "ooxml_compression_ratio_exceeded"
                    )
    except (BadZipFile, UnicodeDecodeError):
        raise WorkbookError("corrupt_workbook") from None
    except OSError:
        raise WorkbookError("workbook_io_error") from None


def open_workbook(path: Path) -> WorkbookReader:
    suffix = path.suffix.lower()
    if suffix == ".xls":
        xlrd = _required_module("xlrd")
        try:
            workbook = xlrd.open_workbook(path, on_demand=True)
        except Exception as error:
            _raise_xlrd_error(error)
        if workbook.nsheets > MAX_WORKSHEETS:
            with suppress(Exception):
                workbook.release_resources()
            raise WorkbookSafetyError("worksheet_limit_exceeded")
        return XlrdReader(workbook)
    if suffix not in {".xlsx", ".xlsm"}:
        raise UnsupportedWorkbook("unsupported_workbook_format")

    preflight_ooxml(path)
    _required_module("defusedxml")
    openpyxl = _required_module("openpyxl")
    formula_stream = _open_binary_stream(path)
    try:
        formula_workbook = openpyxl.load_workbook(
            formula_stream,
            read_only=True,
            data_only=False,
            keep_links=False,
            keep_vba=False,
        )
    except Exception as error:
        _close_suppressing_errors(formula_stream)
        _raise_openpyxl_error(error)
    if len(formula_workbook.worksheets) > MAX_WORKSHEETS:
        _close_suppressing_errors(formula_workbook, formula_stream)
        raise WorkbookSafetyError("worksheet_limit_exceeded")
    try:
        cached_stream = _open_binary_stream(path)
    except WorkbookError:
        _close_suppressing_errors(formula_workbook, formula_stream)
        raise
    try:
        try:
            cached_workbook = openpyxl.load_workbook(
                cached_stream,
                read_only=True,
                data_only=True,
                keep_links=False,
                keep_vba=False,
            )
        except Exception as error:
            _raise_openpyxl_error(error)
    except Exception:
        _close_suppressing_errors(
            cached_stream,
            formula_workbook,
            formula_stream,
        )
        raise
    return OpenPyxlReader(
        formula_workbook,
        cached_workbook,
        formula_stream,
        cached_stream,
    )
