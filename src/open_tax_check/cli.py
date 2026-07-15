from __future__ import annotations

import argparse
import json
from pathlib import Path

from .case import init_case


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opentaxcheck",
        description="OpenTaxCheck｜开源税务健康检查",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="Create a resumable case directory")
    init_parser.add_argument("--case", required=True, type=Path)
    init_parser.add_argument("--entity", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "init":
        print(json.dumps(init_case(args.case, args.entity), ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
