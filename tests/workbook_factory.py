from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


def make_xlsx(
    path: Path,
    *,
    formula: str | None = None,
    cached_value: int | float | None = None,
) -> Path:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Journal"
    sheet.append(("value", "description"))
    sheet.append((formula if formula is not None else 2, "fictional"))
    workbook.save(path)
    workbook.close()
    if formula is not None and cached_value is not None:
        _set_formula_cached_value(path, "A2", cached_value)
    return path


def _set_formula_cached_value(
    path: Path,
    coordinate: str,
    cached_value: int | float,
) -> None:
    sheet_member = "xl/worksheets/sheet1.xml"
    replacement = (
        f'<c r="{coordinate}"><f>1+1</f><v>{cached_value}</v></c>'
    ).encode("ascii")
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with ZipFile(path, "r") as source:
        with ZipFile(temporary_path, "w") as target:
            for item in source.infolist():
                content = source.read(item)
                if item.filename == sheet_member:
                    content = content.replace(
                        f'<c r="{coordinate}"><f>1+1</f><v /></c>'.encode(
                            "ascii"
                        ),
                        replacement,
                        1,
                    )
                target.writestr(item, content)
    temporary_path.replace(path)


def make_xlsx_with_sheet_count(path: Path, *, sheet_count: int) -> Path:
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.title = "Sheet 1"
    for index in range(2, sheet_count + 1):
        workbook.create_sheet(f"Sheet {index}")
    workbook.save(path)
    workbook.close()
    return path


def make_xlsx_with_hidden_sheet(path: Path) -> Path:
    from openpyxl import Workbook

    workbook = Workbook()
    visible = workbook.active
    visible.title = "Journal"
    visible.append(("date", "debit"))
    visible.append(("2026-01-01", 100))
    hidden = workbook.create_sheet("Notes")
    hidden.sheet_state = "hidden"
    hidden["C3"] = "fictional note"
    workbook.save(path)
    workbook.close()
    return path


def make_zip_ratio_fixture(path: Path, *, ratio: int) -> Path:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as package:
        package.writestr("xl/worksheets/sheet1.xml", b"0" * (ratio * 1024))
    return path


def make_zip_member_count_fixture(path: Path, *, member_count: int) -> Path:
    with ZipFile(path, "w", compression=ZIP_STORED) as package:
        for index in range(member_count):
            package.writestr(f"xl/items/item-{index:05d}.xml", b"")
    return path


def make_zip_uncompressed_fixture(
    path: Path,
    *,
    member_sizes: tuple[int, ...],
) -> Path:
    with ZipFile(path, "w", compression=ZIP_STORED) as package:
        for index, size in enumerate(member_sizes):
            package.writestr(f"xl/items/item-{index}.xml", b"x" * size)
    return path


def make_zip_member_name_fixture(path: Path, *, member_name: str) -> Path:
    with ZipFile(path, "w", compression=ZIP_STORED) as package:
        package.writestr(member_name, b"x")
    return path
