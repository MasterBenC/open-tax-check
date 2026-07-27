from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import tempfile

from open_tax_check.case import init_case
from open_tax_check.pipeline import inspect_case, run_case
from open_tax_check.reviews import confirm_mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "fictional-company" / "input" / "journal.csv"
TRIAL_BALANCE_ROWS = (
    (
        "科目编码",
        "科目名称",
        "期初借方",
        "期初贷方",
        "本期借方",
        "本期贷方",
        "期末借方",
        "期末贷方",
    ),
    ("1002", "银行存款", 0, 0, 1_000_000, 25_000, 975_000, 0),
    ("6001", "主营业务收入", 0, 0, 0, 1_000_000, 0, 1_000_000),
    ("6602", "管理费用-业务招待费", 0, 0, 25_000, 0, 25_000, 0),
)
FICTIONAL_MAPPING_TEMPLATES = {
    "journal": {
        "mapping": {
            "凭证号": "voucher_no",
            "记账日期": "date",
            "科目编码": "account_code",
            "科目名称": "account_name",
            "业务说明": "summary",
            "借方发生额": "debit",
            "贷方发生额": "credit",
        },
        "composites": {},
        "exclusions": {},
    },
    "trial_balance": {
        "mapping": {
            "科目编码": "account_code",
            "科目名称": "account_name",
            "期初借方": "opening_debit",
            "期初贷方": "opening_credit",
            "本期借方": "period_debit",
            "本期贷方": "period_credit",
            "期末借方": "closing_debit",
            "期末贷方": "closing_credit",
        },
        "composites": {},
        "exclusions": {},
    },
}


@dataclass(frozen=True)
class FictionalCaseResult:
    status: str
    rule_amounts: dict[str, str]
    trial_balance_passed: bool
    journal_to_trial_balance_passed: bool


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


def create_fictional_trial_balance(path: Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.title = "Trial Balance"
        for row in TRIAL_BALANCE_ROWS:
            sheet.append(row)
        workbook.save(path)
    finally:
        workbook.close()


def _mapping_payloads(case_dir: Path) -> list[dict]:
    proposals = json.loads(
        (case_dir / "work" / "mapping_review.json").read_text(encoding="utf-8")
    )["proposals"]
    if {proposal["role"] for proposal in proposals} != set(FICTIONAL_MAPPING_TEMPLATES):
        raise RuntimeError("fictional example mapping roles changed")
    payloads = []
    for proposal in proposals:
        template = FICTIONAL_MAPPING_TEMPLATES[proposal["role"]]
        payloads.append(
            {
                "role": proposal["role"],
                "source_id": proposal["source_id"],
                "sheet_id": proposal["sheet_id"],
                "header_rows": proposal["header_rows"],
                "mapping": template["mapping"],
                "composites": template["composites"],
                "exclusions": template["exclusions"],
                "reviewer": "host:fictional-example",
            }
        )
    return payloads


def _result(case_dir: Path, status: str) -> FictionalCaseResult:
    findings = json.loads(
        (case_dir / "output" / "findings.json").read_text(encoding="utf-8")
    )
    reconciliation = json.loads(
        (case_dir / "work" / "reconciliation.json").read_text(encoding="utf-8")
    )
    return FictionalCaseResult(
        status=status,
        rule_amounts={
            finding["rule_id"]: finding["tax_adjustment"]
            for finding in findings
            if finding.get("rule_id") == "CIT-BE-001"
        },
        trial_balance_passed=bool(
            reconciliation["trial_balance"]
            and reconciliation["trial_balance"]["passed"]
        ),
        journal_to_trial_balance_passed=bool(
            reconciliation["journal_to_trial_balance"]
            and reconciliation["journal_to_trial_balance"]["passed"]
        ),
    )


def _run_fictional_case(case_dir: Path, format: str) -> FictionalCaseResult:
    init_case(case_dir, "虚构科技有限公司", "2025-01-01", "2025-12-31")
    if format == "csv":
        (case_dir / "input" / "journal.csv").write_bytes(SOURCE.read_bytes())
        code, result = run_case(case_dir, "off")
    else:
        create_fictional_workbook(case_dir / "input" / "journal.xlsx")
        create_fictional_trial_balance(case_dir / "input" / "trial-balance.xlsx")
        inspect_code, _ = inspect_case(case_dir)
        if inspect_code not in (0, 4):
            raise RuntimeError("fictional example inspection failed")
        first_code, _ = run_case(case_dir, "off")
        if first_code != 3:
            raise RuntimeError("fictional example did not require mapping confirmation")
        mapping_path = case_dir / "fictional-mapping.json"
        mapping_payloads = _mapping_payloads(case_dir)
        mapping_path.write_text(
            json.dumps(mapping_payloads, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for payload in json.loads(mapping_path.read_text(encoding="utf-8")):
            confirm_mapping(case_dir, payload)
        code, result = run_case(case_dir, "off")
    if code != 0 or result["status"] != "needs_professional_review":
        raise RuntimeError("fictional example did not reach professional review")
    return _result(case_dir, result["status"])


def run_fictional_case(
    *, format: str, output: Path | None = None
) -> FictionalCaseResult:
    if format not in {"csv", "excel"}:
        raise ValueError("format must be csv or excel")
    if output is None:
        with tempfile.TemporaryDirectory() as temporary:
            return _run_fictional_case(Path(temporary), format)
    return _run_fictional_case(output, format)


def main() -> int:
    _configure_utf8_output()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--format", choices=("csv", "excel"), default="excel")
    args = parser.parse_args()
    if args.output.exists() and (
        not args.output.is_dir() or any(args.output.iterdir())
    ):
        parser.error("output directory must not exist or must be empty")
    result = run_fictional_case(format=args.format, output=args.output)
    print(
        json.dumps(
            {"entity_name": "虚构科技有限公司", **result.__dict__},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
