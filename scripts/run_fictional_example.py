from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

from open_tax_check.case import init_case
from open_tax_check.pipeline import inspect_case


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "fictional-company" / "input" / "journal.csv"


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def create_fictional_workbook(path: Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.title = "Journal"
        with SOURCE.open(newline="", encoding="utf-8") as source:
            for row in csv.reader(source):
                sheet.append(row)
        workbook.save(path)
    finally:
        workbook.close()


def main() -> int:
    _configure_utf8_output()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() and (
        not args.output.is_dir() or any(args.output.iterdir())
    ):
        parser.error("output directory must not exist or must be empty")
    init_case(
        args.output,
        "虚构科技有限公司",
        "2025-01-01",
        "2025-12-31",
    )
    create_fictional_workbook(args.output / "input" / "journal.xlsx")
    code, result = inspect_case(args.output)
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
