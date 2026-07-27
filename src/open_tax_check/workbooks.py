"""Bounded, read-only adapters for supported Excel workbook formats."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from importlib import import_module
from io import BytesIO
from itertools import zip_longest
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterator
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile, ZipInfo


MAX_COMPRESSION_RATIO = 100
MAX_ZIP_MEMBERS = 10_000
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_VISITED_CELLS = 5_000_000
MAX_WORKSHEETS = 200
MAX_METADATA_MEMBER_BYTES = 64 * 1024 * 1024
MAX_METADATA_TOTAL_BYTES = 128 * 1024 * 1024
MAX_METADATA_TOTAL_NODES = 1_000_000
MAX_METADATA_NODES = 5_500_000
# Metadata allowance plus four start nodes per maximum cell covers the
# common c/f/v and c/is/r/t shapes while bounding the whole worksheet.
MAX_WORKSHEET_XML_NODES = 25_500_000
# Rich cell payloads stay bounded independently of the worksheet total.
MAX_CELL_SUBTREE_NODES = 1_024
MAX_MERGED_RANGES = 100_000
MAX_MERGE_RANGE_LENGTH = 128
MAX_WORKBOOK_RELATIONSHIPS = 10_000
MAX_RELATIONSHIP_TARGET_LENGTH = 1024
WORKBOOK_ADAPTER_VERSION = "0.2.0"
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
    has_formulas: bool | None = None
    has_merged_ranges: bool | None = None
    has_hidden_data: bool | None = None
    merged_ranges: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _SheetFeatures:
    has_formulas: bool | None
    merged_ranges: tuple[str, ...]
    has_hidden_data: bool | None

    @property
    def has_merged_ranges(self) -> bool:
        return bool(self.merged_ranges)


class WorkbookReader:
    """Context-managed workbook reader interface."""

    def __enter__(self) -> WorkbookReader:
        return self

    def __exit__(
        self,
        exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> bool | None:
        if exc_type is None:
            self.close()
            return None
        try:
            self.close()
        except BaseException:
            pass
        return False

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
        sheet_features: tuple[_SheetFeatures, ...] = (),
    ) -> None:
        self._formula_workbook = formula_workbook
        self._cached_workbook = cached_workbook
        self._formula_stream = formula_stream
        self._cached_stream = cached_stream
        self._sheet_features = sheet_features

    def sheets(self) -> tuple[SheetMeta, ...]:
        metadata = []
        for index, sheet in enumerate(
            self._formula_workbook.worksheets,
            start=1,
        ):
            features = (
                self._sheet_features[index - 1]
                if index <= len(self._sheet_features)
                else _SheetFeatures(None, (), None)
            )
            metadata.append(
                SheetMeta(
                sheet_id=f"sheet-{index:04d}",
                index=index,
                name=sheet.title,
                visibility=sheet.sheet_state,
                max_row=sheet.max_row,
                max_column=sheet.max_column,
                has_formulas=features.has_formulas,
                has_merged_ranges=features.has_merged_ranges,
                has_hidden_data=(
                    features.has_hidden_data
                    or sheet.sheet_state != "visible"
                    if features.has_hidden_data is not None
                    else None
                ),
                merged_ranges=features.merged_ranges,
            )
            )
        return tuple(metadata)

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
        close_failed = False
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
            except Exception:
                close_failed = True
        if close_failed:
            raise WorkbookError("workbook_io_error") from None


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
        try:
            metadata = []
            visibility = getattr(self._workbook, "sheet_visibility", ())
            for index in range(self._workbook.nsheets):
                sheet = self._workbook.sheet_by_index(index)
                visibility_code = (
                    visibility[index] if index < len(visibility) else 0
                )
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
                        has_formulas=None,
                        has_merged_ranges=bool(
                            getattr(sheet, "merged_cells", ())
                        ),
                        has_hidden_data=(
                            visibility_code != 0
                            or any(
                                bool(getattr(item, "hidden", False))
                                for item in getattr(
                                    sheet,
                                    "rowinfo_map",
                                    {},
                                ).values()
                            )
                            or any(
                                bool(getattr(item, "hidden", False))
                                for item in getattr(
                                    sheet,
                                    "colinfo_map",
                                    {},
                                ).values()
                            )
                        ),
                        merged_ranges=tuple(
                            _xlrd_merged_range(item)
                            for item in getattr(
                                sheet,
                                "merged_cells",
                                (),
                            )
                        ),
                    )
                )
            return tuple(metadata)
        except WorkbookError:
            raise
        except Exception as error:
            _raise_xlrd_error(error)

    def iter_rows(
        self,
        sheet_id: str,
        min_row: int,
        max_row: int,
        max_col: int,
    ) -> Iterator[tuple[CellValue, ...]]:
        sheet = None
        index = None
        try:
            index = _sheet_index(sheet_id, self._workbook.nsheets)
            row_count = max(0, max_row - min_row + 1)
            if row_count * max_col > MAX_VISITED_CELLS:
                raise WorkbookSafetyError("visited_cell_limit_exceeded")
            sheet = self._workbook.sheet_by_index(index)
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
                        elif data_type == "date":
                            value = _required_module("xlrd").xldate_as_datetime(
                                value,
                                self._workbook.datemode,
                            )
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
        except WorkbookError:
            raise
        except Exception as error:
            _raise_xlrd_error(error)
        finally:
            if sheet is not None and index is not None:
                with suppress(Exception):
                    self._workbook.unload_sheet(index)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._workbook.release_resources()
            except Exception:
                raise WorkbookError("workbook_io_error") from None


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


def _xlrd_merged_range(bounds: tuple[int, int, int, int]) -> str:
    row_start, row_end, column_start, column_end = bounds
    return (
        f"{_coordinate(row_start + 1, column_start + 1)}:"
        f"{_coordinate(row_end, column_end)}"
    )


def _xml_local_name(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _relationship_member(target: str) -> str:
    if len(target) > MAX_RELATIONSHIP_TARGET_LENGTH:
        raise WorkbookSafetyError(
            "ooxml_relationship_target_limit_exceeded"
        )
    normalised = target.replace("\\", "/")
    parts = PurePosixPath(normalised).parts
    if ".." in parts or bool(PureWindowsPath(target).drive):
        raise WorkbookSafetyError("ooxml_unsafe_relationship_target")
    if normalised.startswith("/"):
        member = normalised.lstrip("/")
    else:
        member = (PurePosixPath("xl") / normalised).as_posix()
    if not member.startswith("xl/"):
        raise WorkbookSafetyError("ooxml_unsafe_relationship_target")
    return member


def _ooxml_metadata_members(package: ZipFile) -> tuple[ZipInfo, ...]:
    members = []
    for item in package.infolist():
        if item.is_dir():
            continue
        normalised = item.filename.replace("\\", "/")
        casefolded = normalised.casefold()
        suffix = PurePosixPath(casefolded).suffix
        worksheet_xml = (
            casefolded.startswith("xl/worksheets/")
            and suffix == ".xml"
            and not casefolded.startswith("xl/worksheets/_rels/")
        )
        if suffix in {".xml", ".rels"} and not worksheet_xml:
            members.append(item)
    return tuple(members)


def _bounded_ooxml_metadata_members(
    package: ZipFile,
) -> tuple[ZipInfo, ...]:
    members = _ooxml_metadata_members(package)
    if any(item.file_size > MAX_METADATA_MEMBER_BYTES for item in members):
        raise WorkbookSafetyError("ooxml_metadata_member_too_large")
    if sum(item.file_size for item in members) > MAX_METADATA_TOTAL_BYTES:
        raise WorkbookSafetyError("ooxml_metadata_total_too_large")
    return members


def _validate_ooxml_metadata(
    package: ZipFile,
    element_tree: Any,
) -> None:
    nodes = 0
    for metadata in _bounded_ooxml_metadata_members(package):
        for event, element in _iter_xml_elements(
            package,
            metadata,
            element_tree,
            bounded_index_member=False,
        ):
            if event == "start":
                nodes += 1
                if nodes > MAX_METADATA_TOTAL_NODES:
                    raise WorkbookSafetyError(
                        "ooxml_metadata_node_limit_exceeded"
                    )
            else:
                element.clear()


def _iter_xml_elements(
    package: ZipFile,
    member: str | ZipInfo,
    element_tree: Any,
    *,
    bounded_index_member: bool,
) -> Iterator[tuple[str, Any]]:
    metadata = package.getinfo(member) if isinstance(member, str) else member
    if (
        bounded_index_member
        and metadata.file_size > MAX_METADATA_MEMBER_BYTES
    ):
        raise WorkbookSafetyError("ooxml_metadata_member_too_large")
    with package.open(metadata) as source:
        yield from element_tree.iterparse(
            source,
            events=("start", "end"),
        )


def _ooxml_relationships(
    package: ZipFile,
    element_tree: Any,
) -> dict[str, tuple[str, str]]:
    relationships = {}
    relationship_count = 0
    worksheet_relationships = 0
    nodes = 0
    for event, element in _iter_xml_elements(
        package,
        "xl/_rels/workbook.xml.rels",
        element_tree,
        bounded_index_member=True,
    ):
        if event == "start":
            nodes += 1
            if nodes > MAX_METADATA_NODES:
                raise WorkbookSafetyError(
                    "ooxml_metadata_node_limit_exceeded"
                )
            if _xml_local_name(element.tag) != "Relationship":
                continue
            relationship_count += 1
            if relationship_count > MAX_WORKBOOK_RELATIONSHIPS:
                raise WorkbookSafetyError(
                    "ooxml_relationship_limit_exceeded"
                )
            relationship_id = element.attrib.get("Id")
            relationship_type = str(element.attrib.get("Type", ""))
            target = element.attrib.get("Target")
            if (
                not relationship_id
                or target is None
                or relationship_id in relationships
            ):
                raise WorkbookError("corrupt_workbook")
            if len(str(target)) > MAX_RELATIONSHIP_TARGET_LENGTH:
                raise WorkbookSafetyError(
                    "ooxml_relationship_target_limit_exceeded"
                )
            relationships[str(relationship_id)] = (
                relationship_type,
                str(target),
            )
            if relationship_type.endswith("/worksheet"):
                worksheet_relationships += 1
                if worksheet_relationships > MAX_WORKSHEETS:
                    raise WorkbookSafetyError(
                        "worksheet_limit_exceeded"
                    )
        else:
            element.clear()
    return relationships


def _ooxml_worksheet_members(
    package: ZipFile,
    element_tree: Any,
    relationships: dict[str, tuple[str, str]],
) -> tuple[str, ...]:
    members = []
    nodes = 0
    for event, element in _iter_xml_elements(
        package,
        "xl/workbook.xml",
        element_tree,
        bounded_index_member=True,
    ):
        if event == "start":
            nodes += 1
            if nodes > MAX_METADATA_NODES:
                raise WorkbookSafetyError(
                    "ooxml_metadata_node_limit_exceeded"
                )
            if _xml_local_name(element.tag) != "sheet":
                continue
            relationship_id = next(
                (
                    value
                    for key, value in element.attrib.items()
                    if _xml_local_name(key) == "id"
                ),
                None,
            )
            relationship = relationships.get(str(relationship_id))
            if relationship is None:
                raise WorkbookError("corrupt_workbook")
            relationship_type, target = relationship
            if not relationship_type.endswith("/worksheet"):
                continue
            members.append(_relationship_member(target))
            if len(members) > MAX_WORKSHEETS:
                raise WorkbookSafetyError("worksheet_limit_exceeded")
        else:
            element.clear()
    return tuple(members)


def _ooxml_sheet_features(
    source: Any,
    element_tree: Any,
) -> tuple[_SheetFeatures, ...]:
    try:
        with ZipFile(source) as package:
            relationships = _ooxml_relationships(
                package,
                element_tree,
            )
            members = _ooxml_worksheet_members(
                package,
                element_tree,
                relationships,
            )
            return tuple(
                _ooxml_single_sheet_features(
                    package,
                    member,
                    element_tree,
                )
                for member in members
            )
    except WorkbookError:
        raise
    except (BadZipFile, KeyError, ParseError, UnicodeDecodeError):
        raise WorkbookError("corrupt_workbook") from None
    except OSError:
        raise WorkbookError("workbook_io_error") from None
    except Exception:
        raise WorkbookError("corrupt_workbook") from None


def _ooxml_single_sheet_features(
    package: ZipFile,
    member: str,
    element_tree: Any,
) -> _SheetFeatures:
    has_formulas = False
    has_hidden_data = False
    merged_ranges = []
    visited_cells = 0
    metadata_nodes = 0
    worksheet_nodes = 0
    cell_depth = 0
    cell_subtree_nodes = 0
    merge_count = 0
    for event, element in _iter_xml_elements(
        package,
        member,
        element_tree,
        bounded_index_member=False,
    ):
        local_name = _xml_local_name(element.tag)
        if event == "start":
            worksheet_nodes += 1
            if worksheet_nodes > MAX_WORKSHEET_XML_NODES:
                raise WorkbookSafetyError(
                    "ooxml_worksheet_node_limit_exceeded"
                )
            if cell_depth:
                cell_subtree_nodes += 1
                if local_name == "c":
                    cell_depth += 1
                if cell_subtree_nodes > MAX_CELL_SUBTREE_NODES:
                    raise WorkbookSafetyError(
                        "ooxml_cell_node_limit_exceeded"
                    )
            elif local_name == "c":
                visited_cells += 1
                cell_depth = 1
                cell_subtree_nodes = 1
                if cell_subtree_nodes > MAX_CELL_SUBTREE_NODES:
                    raise WorkbookSafetyError(
                        "ooxml_cell_node_limit_exceeded"
                    )
                if visited_cells > MAX_VISITED_CELLS:
                    raise WorkbookSafetyError(
                        "visited_cell_limit_exceeded"
                    )
            else:
                metadata_nodes += 1
                if metadata_nodes > MAX_METADATA_NODES:
                    raise WorkbookSafetyError(
                        "ooxml_metadata_node_limit_exceeded"
                    )
            if local_name == "f":
                has_formulas = True
            elif local_name == "mergeCell":
                merge_count += 1
                if merge_count > MAX_MERGED_RANGES:
                    raise WorkbookSafetyError(
                        "ooxml_merge_range_limit_exceeded"
                    )
                reference = element.attrib.get("ref")
                if not reference:
                    raise WorkbookError("corrupt_workbook")
                if len(str(reference)) > MAX_MERGE_RANGE_LENGTH:
                    raise WorkbookSafetyError(
                        "ooxml_merge_range_limit_exceeded"
                    )
                merged_ranges.append(str(reference))
            elif (
                local_name in {"row", "col"}
                and str(element.attrib.get("hidden", "")).casefold()
                in {"1", "true"}
            ):
                has_hidden_data = True
        else:
            if local_name == "c" and cell_depth:
                cell_depth -= 1
            element.clear()
    return _SheetFeatures(
        has_formulas=has_formulas,
        merged_ranges=tuple(sorted(set(merged_ranges))),
        has_hidden_data=has_hidden_data,
    )


def _required_module(name: str) -> Any:
    try:
        return import_module(name)
    except (ImportError, ModuleNotFoundError):
        raise ExcelExtraRequired("install_excel_extra") from None


def _defused_element_tree() -> Any:
    _required_module("defusedxml")
    candidate = _required_module("defusedxml.ElementTree")
    if hasattr(candidate, "iterparse"):
        return candidate
    try:
        return __import__(
            "defusedxml.ElementTree",
            fromlist=("ElementTree",),
        )
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


def _validate_ooxml_package(package: ZipFile) -> None:
    members = package.infolist()
    if len(members) > MAX_ZIP_MEMBERS:
        raise WorkbookSafetyError("ooxml_member_limit_exceeded")
    if sum(item.file_size for item in members) > MAX_UNCOMPRESSED_BYTES:
        raise WorkbookSafetyError("ooxml_uncompressed_limit_exceeded")
    for item in members:
        normalised_name = item.filename.replace("\\", "/")
        parts = PurePosixPath(normalised_name).parts
        has_drive = bool(PureWindowsPath(item.filename).drive)
        if normalised_name.startswith("/") or ".." in parts or has_drive:
            raise WorkbookSafetyError("ooxml_unsafe_member_name")
        ratio = item.file_size / max(item.compress_size, 1)
        if ratio > MAX_COMPRESSION_RATIO:
            raise WorkbookSafetyError("ooxml_compression_ratio_exceeded")


def preflight_ooxml(path: Path, source_bytes: bytes | None = None) -> None:
    try:
        if source_bytes is None:
            with path.open("rb") as source:
                header = source.read(len(OLE_COMPOUND_HEADER))
            package_source: Any = path
        else:
            header = source_bytes[: len(OLE_COMPOUND_HEADER)]
            package_source = BytesIO(source_bytes)
        if header == OLE_COMPOUND_HEADER:
            if source_bytes is not None:
                package_source.close()
            raise WorkbookError("encrypted_workbook")
        try:
            with ZipFile(package_source) as package:
                _validate_ooxml_package(package)
                _validate_ooxml_metadata(package, _defused_element_tree())
        finally:
            if source_bytes is not None:
                package_source.close()
    except WorkbookError:
        raise
    except (BadZipFile, KeyError, ParseError, UnicodeDecodeError):
        raise WorkbookError("corrupt_workbook") from None
    except OSError:
        raise WorkbookError("workbook_io_error") from None
    except Exception:
        raise WorkbookError("corrupt_workbook") from None


def open_workbook(path: Path, source_bytes: bytes | None = None) -> WorkbookReader:
    suffix = path.suffix.lower()
    if suffix == ".xls":
        xlrd = _required_module("xlrd")
        try:
            if source_bytes is None:
                workbook = xlrd.open_workbook(
                    path,
                    on_demand=True,
                    formatting_info=True,
                )
            else:
                workbook = xlrd.open_workbook(
                    file_contents=source_bytes,
                    on_demand=True,
                    formatting_info=True,
                )
        except Exception as error:
            _raise_xlrd_error(error)
        if workbook.nsheets > MAX_WORKSHEETS:
            with suppress(Exception):
                workbook.release_resources()
            raise WorkbookSafetyError("worksheet_limit_exceeded")
        return XlrdReader(workbook)
    if suffix not in {".xlsx", ".xlsm"}:
        raise UnsupportedWorkbook("unsupported_workbook_format")

    preflight_ooxml(path, source_bytes)
    element_tree = _defused_element_tree()
    if source_bytes is None:
        sheet_features = _ooxml_sheet_features(path, element_tree)
    else:
        with BytesIO(source_bytes) as metadata_stream:
            sheet_features = _ooxml_sheet_features(
                metadata_stream,
                element_tree,
            )
    openpyxl = _required_module("openpyxl")
    formula_stream = (
        _open_binary_stream(path)
        if source_bytes is None
        else BytesIO(source_bytes)
    )
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
        cached_stream = (
            _open_binary_stream(path)
            if source_bytes is None
            else BytesIO(source_bytes)
        )
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
        sheet_features,
    )
