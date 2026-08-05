from __future__ import annotations

import argparse
from calendar import monthrange
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from open_tax_check.case import init_case, write_json
from open_tax_check.case_orchestration import (
    apply_case_confirmations,
    build_case_customer_tasks,
    case_status_payload,
    run_case_self_check,
)
from open_tax_check.delivery import build_case_delivery
from open_tax_check.customer_guide import build_case_guide, start_case_from_profile
from open_tax_check.pipeline import inspect_case, run_case
from open_tax_check.account_classification import confirm_account_classifications
from open_tax_check.reviews import confirm_mapping
from open_tax_check.reviews import (
    current_source_manifest_sha256,
    effective_journal_mapping_fingerprint,
)
from open_tax_check.structured_document_providers import (
    persist_financial_statement,
    persist_tax_return,
)
from open_tax_check.tax_models import (
    Confirmation,
    DateRange,
    FactState,
    Limitation,
    TaxCheckRequest,
    TaxFact,
)

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

COMPLETE_TRIAL_BALANCE_ROWS = (
    TRIAL_BALANCE_ROWS[0],
    ("1002", "银行存款", 0, 0, 1_060_000, 25_000, 1_035_000, 0),
    ("6001", "主营业务收入", 0, 0, 0, 1_000_000, 0, 1_000_000),
    ("6051", "其他业务收入", 0, 0, 0, 0, 0, 0),
    ("2221", "销项税额", 0, 0, 0, 60_000, 0, 60_000),
    ("6602", "管理费用-业务招待费", 0, 0, 25_000, 0, 25_000, 0),
)
UNPARSED_EVIDENCE_FILES = (
    "balance-sheet.txt",
    "income-statement.txt",
    "vat-return.txt",
    "cit-return.txt",
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
    delivery_status: str | None = None
    delivery_revision: int | None = None
    journey_stages: tuple[str, ...] = ()
    cross_check_payload: dict[str, object] | None = None


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def create_fictional_workbook(path: Path, *, rows: tuple[tuple, ...] | None = None) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.title = "Journal"
        if rows is None:
            with SOURCE.open(newline="", encoding="utf-8") as source:
                rows = tuple(tuple(row) for row in csv.reader(source))
        for row in rows:
            sheet.append(row)
        workbook.save(path)
    finally:
        workbook.close()


def create_fictional_trial_balance(
    path: Path, *, rows: tuple[tuple, ...] = TRIAL_BALANCE_ROWS
) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.title = "Trial Balance"
        for row in rows:
            sheet.append(row)
        workbook.save(path)
    finally:
        workbook.close()


def _complete_journal_rows() -> tuple[tuple[str, ...], ...]:
    with SOURCE.open(newline="", encoding="utf-8") as source:
        rows = [tuple(row) for row in csv.reader(source)]
    rows.extend(
        (
            ("记-004", "2025-01-02", "1002", "银行存款", "确认销项税额", "60000", "0"),
            ("记-004", "2025-01-02", "2221", "销项税额", "确认销项税额", "0", "60000"),
            ("记-005", "2025-01-02", "6051", "其他业务收入", "无其他业务收入", "0", "0"),
        )
    )
    return tuple(rows)


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
    cross_check_path = case_dir / "work" / "tax-cross-checks.json"
    cross_check_payload = (
        json.loads(cross_check_path.read_text(encoding="utf-8"))
        if cross_check_path.is_file()
        else None
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
        cross_check_payload=cross_check_payload,
    )


def _run_fictional_case(case_dir: Path, format: str) -> FictionalCaseResult:
    init_case(case_dir, "虚构科技有限公司", "2025-01-01", "2025-12-31")
    if format == "csv":
        (case_dir / "input" / "journal.csv").write_bytes(SOURCE.read_bytes())
        code, result = run_case(case_dir, "off")
    else:
        create_fictional_workbook(case_dir / "input" / "journal.xlsx")
        create_fictional_trial_balance(case_dir / "input" / "trial-balance.xlsx")
        for filename in UNPARSED_EVIDENCE_FILES:
            (case_dir / "input" / filename).write_text(
                "Fictional placeholder supplied for intake detection only.\n",
                encoding="utf-8",
            )
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


COMPLETE_PERIOD_START = "2023-01-01"
COMPLETE_PERIOD_END = "2026-12-31"
COMPLETE_VAT_FILENAMES = (
    "vat-return.txt",
    *tuple(
        f"vat-return-{year}-q{quarter}.txt"
        for year in range(2023, 2027)
        for quarter in range(1, 5)
        if not (year == 2023 and quarter == 1)
    ),
)
COMPLETE_STRUCTURED_SOURCES = {
    "balance-sheet.txt": "虚构资产负债表：资产总计975000元，负债和所有者权益总计975000元。\n",
    "income-statement.txt": "虚构利润表：营业收入1000000元，费用25000元，净利润975000元。\n",
    "cash-flow-statement.txt": "虚构现金流量表：期末现金及现金等价物余额975000元。\n",
    "surcharge-return.txt": "虚构附加税费申报表：增值税及消费税税额基础60000元，教育费附加1800元。\n",
    "cit-return.txt": "虚构企业所得税申报表：营业收入1000000元，应纳税所得额975000元，应纳税额243750元。\n",
    "iit-return.txt": "虚构个人所得税申报表：年度综合所得应纳税所得额5000元，应纳税额150元。\n",
}


def _complete_vat_periods() -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            f"{year:04d}-{month:02d}-01",
            f"{year:04d}-{month + 2:02d}-{monthrange(year, month + 2)[1]:02d}",
        )
        for year in range(2023, 2027)
        for month in (1, 4, 7, 10)
    )


def _complete_structured_sources() -> dict[str, str]:
    sources = dict(COMPLETE_STRUCTURED_SOURCES)
    for index, filename in enumerate(COMPLETE_VAT_FILENAMES):
        sources[filename] = (
            "虚构增值税申报表：季度序号"
            f"{index + 1}，应税销售额62500元，销项税额3750元，进项税额0元。\n"
        )
    return sources


def _source_id_by_name(case_dir: Path, filename: str) -> str:
    manifest = json.loads(
        (case_dir / "work" / "input_manifest.json").read_text(encoding="utf-8")
    )
    for source in manifest.get("sources", []):
        if source.get("filename") == filename:
            return source["source_id"]
    raise RuntimeError(f"fictional source missing: {filename}")


def _anchor(source_id: str, *, page: int = 1) -> dict:
    return {
        "source_id": source_id,
        "page_number": page,
        "sheet_name": None,
        "cell": None,
        "coordinates": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.2], [0.1, 0.2]],
    }


def _statement_payload(
    source_id: str,
    statement_id: str,
    statement_type: str,
    totals: dict[str, str],
    line_code: str,
    line_name: str,
    amount: str,
) -> dict:
    return {
        "schema_version": "financial-statement/v1",
        "statement_id": statement_id,
        "source_id": source_id,
        "entity_name": "虚构科技有限公司",
        "statement_type": statement_type,
        "period_start": COMPLETE_PERIOD_START,
        "period_end": COMPLETE_PERIOD_END,
        "as_of_date": COMPLETE_PERIOD_END,
        "currency": "CNY",
        "unit_multiplier": "1",
        "lines": [{
            "line_code": line_code,
            "original_name": line_name,
            "standard_name": line_name,
            "current_amount": amount,
            "prior_amount": "0.00",
            "source_anchor": _anchor(source_id),
            "confidence_source": "template",
            "confirmation_state": "confirmed",
        }],
        "totals": totals,
        "issues": [],
    }


def _return_payload(
    source_id: str,
    return_id: str,
    tax_id: str,
    form_code: str,
    fact_key: str | None,
    value: str,
    *,
    tax_payable: str | None = None,
    period_start: str = COMPLETE_PERIOD_START,
    period_end: str = COMPLETE_PERIOD_END,
    filing_status: str = "filed",
    filed_at: str = "2027-02-10T09:00:00+08:00",
) -> dict:
    return {
        "schema_version": "tax-return/v1",
        "return_id": return_id,
        "source_id": source_id,
        "entity_name": "虚构科技有限公司",
        "tax_id": tax_id,
        "return_type": "quarterly_return" if tax_id == "vat" else "annual_return",
        "period_start": period_start,
        "period_end": period_end,
        "filing_status": filing_status,
        "filed_at": filed_at,
        "currency": "CNY",
        "unit_multiplier": "1",
        "lines": [{
            "form_code": form_code,
            "line_number": "1",
            "original_name": "虚构申报项目",
            "fact_key": fact_key,
            "value": value,
            "value_type": "decimal",
            "source_anchor": _anchor(source_id),
            "confidence_source": "template",
            "confirmation_state": "confirmed",
        }],
        "totals": {"tax_payable": tax_payable if tax_payable is not None else value},
        "issues": [],
    }


def _write_complete_structured_documents(
    case_dir: Path,
    *,
    vat_sales: str = "62500.00",
    vat_output: str = "3750.00",
    cit_revenue: str = "1000000.00",
    cit_total_profit: str = "975000.00",
) -> None:
    statement_specs = (
        ("balance-sheet.txt", "balance-sheet-2023-2026", "balance_sheet",
            {"assets": "1035000.00", "liabilities_and_equity": "1035000.00", "cash": "1035000.00"},
            "BS-ASSETS-TOTAL", "资产总计", "1035000.00"),
        ("income-statement.txt", "income-statement-2023-2026", "income_statement",
            {"revenue": "1000000.00", "cost": "0.00", "expenses": "25000.00", "taxes": "0.00", "net_profit": "975000.00", "total_profit": "975000.00"},
            "IS-NET-PROFIT", "净利润", "975000.00"),
        ("cash-flow-statement.txt", "cash-flow-2023-2026", "cash_flow_statement",
            {"ending_cash": "1035000.00"},
            "CF-END-CASH", "期末现金", "1035000.00"),
    )
    for filename, statement_id, statement_type, totals, line_code, line_name, amount in statement_specs:
        source_id = _source_id_by_name(case_dir, filename)
        payload = _statement_payload(
            source_id,
            statement_id,
            statement_type,
            totals,
            line_code,
            line_name,
            amount,
        )
        if statement_type == "income_statement":
            for line_code, line_name, amount in (
                ("revenue", "营业收入", "1000000.00"),
                ("total_profit", "利润总额", "975000.00"),
            ):
                payload["lines"].append({
                    "line_code": line_code,
                    "original_name": line_name,
                    "standard_name": line_name,
                    "current_amount": amount,
                    "prior_amount": "0.00",
                    "source_anchor": _anchor(source_id),
                    "confidence_source": "template",
                    "confirmation_state": "confirmed",
                })
        persist_financial_statement(case_dir, source_id, payload)
    return_specs = (
        ("surcharge-return.txt", "surcharge-return-2023-2026", "education_surcharge", "EDU-MAIN", "surcharge.vat_consumption_tax_base", "60000.00", "1800.00"),
        ("cit-return.txt", "cit-return-2023-2026", "enterprise_income_tax", "CIT-MAIN", "cit.revenue", cit_revenue, "243750.00"),
        ("iit-return.txt", "iit-return-2023-2026", "individual_income_tax", "IIT-MAIN", "individual_income_tax.annual_comprehensive_taxable_income", "5000.00", "150.00"),
    )
    returns = []
    for index, (filename, (period_start, period_end)) in enumerate(
        zip(COMPLETE_VAT_FILENAMES, _complete_vat_periods(), strict=True),
        start=1,
    ):
        source_id = _source_id_by_name(case_dir, filename)
        returns.append((source_id, _return_payload(
            source_id,
            f"vat-return-2023-2026-q{index}",
            "vat",
            "VAT-MAIN",
            "vat.taxable_sales",
            vat_sales,
            tax_payable=vat_output,
            period_start=period_start,
            period_end=period_end,
            filed_at=f"{period_end}T09:00:00+08:00",
        )))
    for source_id, payload in returns:
        if payload["tax_id"] != "vat":
            continue
        payload["lines"].append({
            "form_code": "VAT-MAIN",
            "line_number": "2",
            "original_name": "进项税额",
            "fact_key": "vat.input_tax_credits",
            "value": "0.00",
            "value_type": "decimal",
            "source_anchor": _anchor(source_id),
            "confidence_source": "template",
            "confirmation_state": "confirmed",
        })
        payload["lines"].append({
            "form_code": "VAT-MAIN",
            "line_number": "3",
            "original_name": "销项税额",
            "fact_key": "vat.output_tax",
            "value": vat_output,
            "value_type": "decimal",
            "source_anchor": _anchor(source_id),
            "confidence_source": "template",
            "confirmation_state": "confirmed",
        })
    for filename, return_id, tax_id, form_code, fact_key, value, tax_payable in return_specs:
        source_id = _source_id_by_name(case_dir, filename)
        returns.append((source_id, _return_payload(
            source_id, return_id, tax_id, form_code, fact_key, value,
            tax_payable=tax_payable,
        )))
    cit_source_id, cit_return = next(item for item in returns if item[1]["tax_id"] == "enterprise_income_tax")
    cit_return["lines"].append({
        "form_code": "CIT-MAIN",
        "line_number": "2",
        "original_name": "利润总额",
        "fact_key": "cit.total_profit",
        "value": cit_total_profit,
        "value_type": "decimal",
        "source_anchor": _anchor(cit_source_id),
        "confidence_source": "template",
        "confirmation_state": "confirmed",
    })
    cit_return["lines"].append({
        "form_code": "CIT-MAIN",
        "line_number": "3",
        "original_name": "应纳税所得额",
        "fact_key": "enterprise_income_tax.taxable_income",
        "value": cit_total_profit,
        "value_type": "decimal",
        "source_anchor": _anchor(cit_source_id),
        "confidence_source": "template",
        "confirmation_state": "confirmed",
    })
    cit_return["lines"].extend((
        {
            "form_code": "CIT-MAIN",
            "line_number": "4",
            "original_name": "业务招待费账载金额",
            "fact_key": "cit.business_entertainment.book_amount",
            "value": "20000.00",
            "value_type": "decimal",
            "source_anchor": _anchor(cit_source_id),
            "confidence_source": "template",
            "confirmation_state": "confirmed",
        },
        {
            "form_code": "CIT-MAIN",
            "line_number": "5",
            "original_name": "业务招待费税前扣除金额",
            "fact_key": "cit.business_entertainment.tax_amount",
            "value": "5000.00",
            "value_type": "decimal",
            "source_anchor": _anchor(cit_source_id),
            "confidence_source": "template",
            "confirmation_state": "confirmed",
        },
    ))
    for source_id, payload in returns:
        persist_tax_return(case_dir, source_id, payload)


def _confirm_complete_account_classifications(case_dir: Path) -> None:
    manifest_sha256 = current_source_manifest_sha256(case_dir)
    mapping_fingerprint = effective_journal_mapping_fingerprint(case_dir)
    if manifest_sha256 is None or mapping_fingerprint is None:
        raise RuntimeError("fictional account-classification binding unavailable")
    rows = [
        json.loads(line)
        for line in (case_dir / "work" / "normalized_journal.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    category_by_code = {
        "6001": "main_business_revenue",
        "6051": "other_business_revenue",
        "2221": "vat_output_tax",
        "6602": "business_entertainment",
    }
    classifications = []
    seen = set()
    for row in rows:
        code = row.get("account_code")
        if code in category_by_code and code not in seen:
            classifications.append({
                "account_code": code,
                "account_name": row["account_name"],
                "category": category_by_code[code],
            })
            seen.add(code)
    confirm_account_classifications(
        case_dir,
        {
            "schema_version": "account-classification/v1",
            "source_manifest_sha256": manifest_sha256,
            "mapping_fingerprint": mapping_fingerprint,
            "reviewer": "human:Chen Li",
            "confirmed_at": "2027-02-15T09:30:00+08:00",
            "classifications": classifications,
        },
    )


def _prepare_complete_pipeline_case(
    case_dir: Path,
    *,
    vat_sales: str = "62500.00",
    vat_output: str = "3750.00",
    cit_revenue: str = "1000000.00",
    cit_total_profit: str = "975000.00",
    include_structured: bool = True,
    confirm_classification: bool = True,
) -> tuple[int, dict]:
    start_case_from_profile(
        case_dir,
        {
            "entity_name": "虚构科技有限公司",
            "province_city": "广东省深圳市",
            "primary_business": "软件开发与技术服务",
            "period_start": COMPLETE_PERIOD_START,
            "period_end": COMPLETE_PERIOD_END,
            "vat_taxpayer_type": "general",
        },
    )
    create_fictional_workbook(
        case_dir / "input" / "journal.xlsx", rows=_complete_journal_rows()
    )
    create_fictional_trial_balance(
        case_dir / "input" / "trial-balance.xlsx", rows=COMPLETE_TRIAL_BALANCE_ROWS
    )
    if include_structured:
        for filename, content in _complete_structured_sources().items():
            (case_dir / "input" / filename).write_text(content, encoding="utf-8")
    inspect_code, _ = inspect_case(case_dir)
    if inspect_code not in (0, 4):
        raise RuntimeError("fictional acceptance inspection failed")
    first_code, _ = run_case(case_dir, "off")
    if first_code != 3:
        raise RuntimeError("fictional acceptance did not require mapping confirmation")
    for payload in _mapping_payloads(case_dir):
        confirm_mapping(case_dir, payload)
    if include_structured:
        _write_complete_structured_documents(
            case_dir,
            vat_sales=vat_sales,
            vat_output=vat_output,
            cit_revenue=cit_revenue,
            cit_total_profit=cit_total_profit,
        )
    code, response = run_case(case_dir, "off")
    if code != 0:
        raise RuntimeError("fictional acceptance did not persist deterministic inputs")
    if confirm_classification:
        _confirm_complete_account_classifications(case_dir)
        code, response = run_case(case_dir, "off")
    return code, response


def _fictional_answer(task):
    if task.answer_type == "boolean":
        return task.fact_key in {
            "activity.has_vat_taxable_transaction",
            "activity.has_individual_income_tax_duty",
            "entity.has_enterprise_income_tax_scope",
            "tax.has_vat_or_consumption_tax_payable",
        }
    if task.answer_type == "choice":
        choices = {
            "vat.rate_path": "general_taxpayer_6",
            "enterprise_income_tax.rate_path": "standard_resident_enterprise",
            "individual_income_tax.calculation_path": "resident_annual_comprehensive_income",
            "urban_maintenance_construction_tax.rate_path": "urban_area_standard",
            "education_surcharge.rate_path": "standard",
            "local_education_surcharge.rate_path": "standard",
        }
        return choices.get(task.fact_key, task.choices[0].value)
    if task.answer_type == "integer":
        return 0
    if task.answer_type == "decimal":
        return {
            "vat.taxable_sales": "1000000.00",
            "vat.input_tax_credits": "0.00",
            "enterprise_income_tax.taxable_income": "975000.00",
            "individual_income_tax.annual_comprehensive_taxable_income": "5000.00",
            "surcharge.vat_consumption_tax_base": "60000.00",
        }.get(task.fact_key, "0.00")
    if task.answer_type == "date":
        return COMPLETE_PERIOD_END
    return "客户确认无其他相关应税事实"


def _confirmation_source(case_dir: Path, fact_key: str) -> str | None:
    source_name = None
    if fact_key == "activity.has_vat_taxable_transaction" or fact_key.startswith("vat."):
        source_name = "vat-return.txt"
    elif fact_key == "tax.has_vat_or_consumption_tax_payable" or fact_key.startswith((
        "surcharge.",
        "urban_maintenance_construction_tax.",
        "education_surcharge.",
        "local_education_surcharge.",
    )):
        source_name = "surcharge-return.txt"
    elif fact_key == "entity.has_enterprise_income_tax_scope" or fact_key.startswith(
        "enterprise_income_tax."
    ):
        source_name = "cit-return.txt"
    elif fact_key == "activity.has_individual_income_tax_duty" or fact_key.startswith(
        "individual_income_tax."
    ):
        source_name = "iit-return.txt"
    return _source_id_by_name(case_dir, source_name) if source_name else None


def _confirm_task(case_dir: Path, task, value, occurred_at: datetime) -> None:
    source_id = _confirmation_source(case_dir, task.fact_key)
    evidence_refs = [f"customer-confirmation:{task.question_id}"]
    if source_id is not None:
        evidence_refs.append(source_id)
    apply_case_confirmations(
        case_dir,
        (
            Confirmation(
                task.question_id,
                task.fact_key,
                value,
                "customer",
                occurred_at.isoformat(),
                tuple(evidence_refs),
            ),
        ),
    )


def _run_complete_fictional_case(case_dir: Path) -> FictionalCaseResult:
    journey = [build_case_guide(case_dir)]
    start_case_from_profile(
        case_dir,
        {
            "entity_name": "虚构科技有限公司",
            "province_city": "广东省深圳市",
            "primary_business": "软件开发与技术服务",
            "period_start": COMPLETE_PERIOD_START,
            "period_end": COMPLETE_PERIOD_END,
            "vat_taxpayer_type": "general",
        },
    )
    journey.append(build_case_guide(case_dir))
    create_fictional_workbook(
        case_dir / "input" / "journal.xlsx", rows=_complete_journal_rows()
    )
    create_fictional_trial_balance(
        case_dir / "input" / "trial-balance.xlsx", rows=COMPLETE_TRIAL_BALANCE_ROWS
    )
    fixture_root = ROOT / "tests" / "fixtures" / "ocr"
    shutil.copyfile(fixture_root / "fictional-tax-note.png", case_dir / "input" / "fictional-tax-note.png")
    shutil.copyfile(fixture_root / "fictional-digital.pdf", case_dir / "input" / "fictional-digital.pdf")
    for filename, content in _complete_structured_sources().items():
        (case_dir / "input" / filename).write_text(content, encoding="utf-8")
    inspect_code, _ = inspect_case(case_dir)
    if inspect_code not in (0, 4):
        raise RuntimeError("complete fictional inspection failed")
    first_code, _ = run_case(case_dir, "off")
    if first_code != 3:
        raise RuntimeError("complete fictional case did not require mapping confirmation")
    mapping_payloads = _mapping_payloads(case_dir)
    for payload in mapping_payloads:
        confirm_mapping(case_dir, payload)
    _write_complete_structured_documents(case_dir)
    unclassified_code, _ = run_case(case_dir, "off")
    if unclassified_code != 0:
        raise RuntimeError("complete fictional case did not persist normalized inputs")
    _confirm_complete_account_classifications(case_dir)
    code, response = run_case(case_dir, "off")
    if code != 0 or response["status"] != "needs_professional_review":
        raise RuntimeError("complete fictional case did not pass deterministic checks")
    journey.append(build_case_guide(case_dir))

    manifest = json.loads((case_dir / "work" / "input_manifest.json").read_text(encoding="utf-8"))
    hashes = tuple(item["sha256"] for item in manifest["sources"] if item.get("sha256"))
    request = TaxCheckRequest(
        DateRange(date.fromisoformat(COMPLETE_PERIOD_START), date.fromisoformat(COMPLETE_PERIOD_END)),
        "CN",
        facts=(TaxFact("activity.has_vat_taxable_transaction", True, FactState.OBSERVED, ()),),
        input_hashes=hashes,
    )
    limitation = Limitation(
        "special_tax_path_not_covered",
        "零税率、免税及不动产特殊增值税路径未纳入本次确定性计算。",
    )
    run_case_self_check(
        case_dir,
        request,
        occurred_at="2027-02-15T10:00:00+08:00",
        limitations=(limitation,),
    )
    journey.append(build_case_guide(case_dir))
    occurred_at = datetime.fromisoformat("2027-02-15T10:01:00+08:00")
    conflict_task = next(
        item
        for item in build_case_customer_tasks(case_dir)
        if item.answer_type == "boolean"
    )
    for value in (False, True, False):
        _confirm_task(case_dir, conflict_task, value, occurred_at)
        occurred_at += timedelta(seconds=1)

    for _ in range(200):
        tasks = build_case_customer_tasks(case_dir)
        if not tasks:
            break
        task = tasks[0]
        _confirm_task(case_dir, task, _fictional_answer(task), occurred_at)
        occurred_at += timedelta(seconds=1)
    else:
        raise RuntimeError("complete fictional customer task loop did not converge")
    final_status = case_status_payload(case_dir)
    if final_status["status"] != "completed_with_limitations":
        raise RuntimeError("complete fictional case did not close with limitations")
    journey.append(build_case_guide(case_dir))
    stages = tuple(item["stage"] for item in journey)
    expected_stages = (
        "welcome",
        "awaiting_documents",
        "check_in_progress",
        "awaiting_customer_input",
        "draft_report_ready",
    )
    if stages != expected_stages or "welcome" in stages[1:]:
        raise RuntimeError(f"complete fictional customer journey changed: {stages!r}")
    write_json(
        case_dir / "work" / "customer-journey.json",
        {
            "schema_version": "customer-journey/v1",
            "stages": list(stages),
            "profile_digest": journey[1]["profile_digest"],
            "document_groups": journey[1]["document_groups"],
        },
    )
    delivery = build_case_delivery(case_dir)
    pdf_path = case_dir / delivery["revision_path"] / "税务健康检查报告.pdf"
    if not pdf_path.is_file():
        raise RuntimeError("official fictional PDF was not published")
    result = _result(case_dir, final_status["status"])
    return FictionalCaseResult(
        **{
            **result.__dict__,
            "delivery_status": delivery["status"],
            "delivery_revision": delivery["revision"],
            "journey_stages": stages,
        }
    )


def run_fictional_case(
    *, format: str, output: Path | None = None, complete: bool = False
) -> FictionalCaseResult:
    if format not in {"csv", "excel"}:
        raise ValueError("format must be csv or excel")
    if output is None:
        with tempfile.TemporaryDirectory() as temporary:
            return _run_complete_fictional_case(Path(temporary)) if complete else _run_fictional_case(Path(temporary), format)
    return _run_complete_fictional_case(output) if complete else _run_fictional_case(output, format)


def main() -> int:
    _configure_utf8_output()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--format", choices=("csv", "excel"), default="excel")
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and (
        not args.output.is_dir() or any(args.output.iterdir())
    ):
        parser.error("output directory must not exist or must be empty")
    result = run_fictional_case(format=args.format, output=args.output, complete=args.complete)
    print(
        json.dumps(
            {"entity_name": "虚构科技有限公司", **result.__dict__},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
