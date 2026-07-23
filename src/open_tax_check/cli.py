from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .case import init_case, set_case_scope
from .pipeline import inspect_case, run_case


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
    run_parser = subparsers.add_parser("run", help="Run deterministic ingestion and screening")
    run_parser.add_argument("--case", required=True, type=Path)
    run_parser.add_argument("--ai-mode", choices=["off", "assist", "deep"], default="assist")
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
    if args.command == "run":
        code, result = run_case(args.case, args.ai_mode)
        print(json.dumps(result, ensure_ascii=False))
        return code
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
