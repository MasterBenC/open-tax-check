from __future__ import annotations

import argparse
import json
from pathlib import Path

from .case import init_case
from .pipeline import run_case


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opentaxcheck",
        description="OpenTaxCheck｜开源税务健康检查",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="Create a resumable case directory")
    init_parser.add_argument("--case", required=True, type=Path)
    init_parser.add_argument("--entity", required=True)
    run_parser = subparsers.add_parser("run", help="Run deterministic ingestion and screening")
    run_parser.add_argument("--case", required=True, type=Path)
    run_parser.add_argument("--ai-mode", choices=["off", "assist", "deep"], default="assist")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "init":
        print(json.dumps(init_case(args.case, args.entity), ensure_ascii=False))
        return 0
    if args.command == "run":
        code, result = run_case(args.case, args.ai_mode)
        print(json.dumps(result, ensure_ascii=False))
        return code
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
