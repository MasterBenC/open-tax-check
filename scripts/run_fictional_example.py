from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from open_tax_check.case import init_case
from open_tax_check.pipeline import run_case


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "fictional-company" / "input" / "journal.csv"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() and (
        not args.output.is_dir() or any(args.output.iterdir())
    ):
        parser.error("output directory must not exist or must be empty")
    init_case(args.output, "开源示例科技有限公司")
    shutil.copy2(SOURCE, args.output / "input" / "journal.csv")
    code, result = run_case(args.output, "off")
    print(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
