from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .case import init_case, set_case_scope
from .document_extraction import document_extraction_from_dict, document_extraction_to_dict
from .document_providers import HostProvider, LocalDocumentSource
from .pipeline import inspect_case, run_case
from .reviews import (
    MappingReviewError,
    ReviewError,
    confirm_mapping,
    confirm_sheet_selection,
)


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opentaxcheck",
        description="OpenTaxCheck｜开源税务健康检查",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="Create a resumable case directory")
    init_parser.add_argument("--case", required=True, type=Path)
    init_parser.add_argument("--entity", required=True)
    init_parser.add_argument("--period-start")
    init_parser.add_argument("--period-end")
    set_scope_parser = subparsers.add_parser("set-scope", help="Set the case review period")
    set_scope_parser.add_argument("--case", required=True, type=Path)
    set_scope_parser.add_argument("--period-start", required=True)
    set_scope_parser.add_argument("--period-end", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Inventory workbook sheets and evidence coverage",
    )
    inspect_parser.add_argument("--case", required=True, type=Path)
    select_parser = subparsers.add_parser(
        "select-sheet",
        help="Confirm a source-bound worksheet selection",
    )
    select_parser.add_argument("--case", required=True, type=Path)
    select_parser.add_argument("--role", required=True)
    select_parser.add_argument("--source", required=True)
    select_parser.add_argument("--sheet", required=True)
    select_parser.add_argument(
        "--header-row",
        required=True,
        action="append",
        type=int,
    )
    select_parser.add_argument("--hidden-rows", required=True)
    select_parser.add_argument("--reviewer", required=True)
    mapping_parser = subparsers.add_parser(
        "confirm-mapping",
        help="Confirm a complete source-bound field mapping",
    )
    mapping_parser.add_argument("--case", required=True, type=Path)
    mapping_parser.add_argument("--mapping", required=True, type=Path)
    run_parser = subparsers.add_parser(
        "run",
        help="Normalize selected CSV/Excel accounting sources and run screening",
    )
    run_parser.add_argument("--case", required=True, type=Path)
    run_parser.add_argument("--ai-mode", choices=["off", "assist", "deep"], default="assist")
    validate_parser = subparsers.add_parser("validate-extraction", help="Validate document extraction JSON")
    validate_parser.add_argument("--input", required=True, type=Path)
    import_parser = subparsers.add_parser("import-extraction", help="Import host extraction JSON into a case")
    import_parser.add_argument("--case", required=True, type=Path)
    import_parser.add_argument("--source", required=True)
    import_parser.add_argument("--input", required=True, type=Path)
    return parser


def main() -> int:
    _configure_utf8_output()
    args = build_parser().parse_args()
    if args.command == "init":
        print(
            json.dumps(
                init_case(args.case, args.entity, args.period_start, args.period_end),
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "set-scope":
        print(json.dumps(set_case_scope(args.case, args.period_start, args.period_end), ensure_ascii=False))
        return 0
    if args.command == "inspect":
        code, result = inspect_case(args.case)
        print(json.dumps(result, ensure_ascii=False))
        return code
    if args.command == "select-sheet":
        try:
            decision = confirm_sheet_selection(
                args.case,
                role=args.role,
                source_id=args.source,
                sheet_id=args.sheet,
                header_rows=tuple(args.header_row),
                hidden_rows=args.hidden_rows,
                reviewer=args.reviewer,
            )
        except ReviewError as error:
            print(
                json.dumps(
                    {
                        "status": "needs_sheet_selection",
                        "error_type": str(error),
                    },
                    ensure_ascii=False,
                )
            )
            return 4
        except Exception:
            print(
                json.dumps(
                    {
                        "status": "needs_sheet_selection",
                        "error_type": "select_sheet_failed",
                    },
                    ensure_ascii=False,
                )
            )
            return 4
        print(
            json.dumps(
                {
                    "status": "sheet_selection_confirmed",
                    "selection": decision,
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "confirm-mapping":
        try:
            mapping_payload = json.loads(args.mapping.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            print(
                json.dumps(
                    {
                        "status": "needs_mapping_review",
                        "error_type": "invalid_mapping_file",
                    },
                    ensure_ascii=False,
                )
            )
            return 3
        try:
            confirm_mapping(args.case, mapping_payload)
        except MappingReviewError as error:
            print(
                json.dumps(
                    {
                        "status": "needs_mapping_review",
                        "error_type": str(error),
                    },
                    ensure_ascii=False,
                )
            )
            return 3
        except Exception:
            print(
                json.dumps(
                    {
                        "status": "needs_mapping_review",
                        "error_type": "confirm_mapping_failed",
                    },
                    ensure_ascii=False,
                )
            )
            return 3
        print(json.dumps({"status": "mapping_confirmed"}, ensure_ascii=False))
        return 0
    if args.command == "run":
        code, result = run_case(args.case, args.ai_mode)
        print(json.dumps(result, ensure_ascii=False))
        return code
    if args.command == "validate-extraction":
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            value = document_extraction_from_dict(payload)
        except Exception:
            print(json.dumps({"valid": False, "error_code": "invalid_document_extraction"}, ensure_ascii=False))
            return 2
        print(json.dumps({"valid": True, "schema_version": value.schema_version}, ensure_ascii=False))
        return 0
    if args.command == "import-extraction":
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            inventory = json.loads((args.case / "work" / "workbook_inventory.json").read_text(encoding="utf-8"))
            source = next(item for item in inventory.get("sources", []) if item.get("source_id") == args.source)
            extension = Path(source["filename"]).suffix.casefold()
            media_type = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(extension, "application/octet-stream")
            local = LocalDocumentSource(args.source, args.case / "input" / source["filename"], media_type, args.case / "input")
            value = HostProvider().import_result(local, payload)
            target = args.case / "work" / "document-extractions" / f"{args.source}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(document_extraction_to_dict(value), ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        except Exception:
            print(json.dumps({"status": "failed", "error_code": "invalid_document_extraction_import"}, ensure_ascii=False))
            return 2
        print(json.dumps({"status": value.status, "path": str(target)}, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
