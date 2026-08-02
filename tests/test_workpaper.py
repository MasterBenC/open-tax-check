from dataclasses import replace
from datetime import date
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from open_tax_check.tax_health import run_tax_health_check
from open_tax_check.tax_models import DateRange, FactState, TaxCheckRequest, TaxFact
from open_tax_check.workpaper import write_tax_health_workpaper
from open_tax_check.workpaper import _FORMULA_LABELS_ZH


def _snapshot():
    return run_tax_health_check(
        TaxCheckRequest(
            DateRange(date(2026, 1, 1), date(2026, 12, 31)),
            "CN",
            facts=(TaxFact(
                "activity.has_vat_taxable_transaction",
                True,
                FactState.OBSERVED,
                ("source-001",),
            ),),
            input_hashes=("a" * 64,),
        ),
        occurred_at="2026-07-30T10:00:00+08:00",
    )


def test_workpaper_labels_each_public_formula_in_chinese():
    assert _FORMULA_LABELS_ZH["generic.base_minus_deductions_times_rate.v1"] == (
        "计税基础减扣除额后乘税率"
    )


def test_workpaper_is_deterministic_redacted_and_structurally_complete(tmp_path: Path):
    snapshot = _snapshot()
    first = tmp_path / "first.xlsx"
    second = tmp_path / "second.xlsx"

    write_tax_health_workpaper(snapshot, first)
    write_tax_health_workpaper(snapshot, second)

    assert first.read_bytes() == second.read_bytes()
    with ZipFile(first) as archive:
        assert archive.testzip() is None
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        assert "项目概览" in workbook_xml
        assert "客户问题" in workbook_xml
        core_xml = archive.read("docProps/core.xml").decode("utf-8")
        assert core_xml.count("2026-01-01T00:00:00Z") == 2
        assert "<dcterms:modified" in core_xml
    from openpyxl import load_workbook

    workbook = load_workbook(first, read_only=False, data_only=False)
    try:
        assert workbook.sheetnames == [
            "项目概览", "资料清单", "抽取与映射", "数据质量", "账务勾稽", "财报勾稽",
            "申报勾稽", "税费筛查", "正式计算", "客户问题", "证据索引",
            "规则版本", "报告选取", "运行记录",
        ]
        overview = workbook["项目概览"]
        assert overview["B2"].value == "2026-01-01"
        assert overview["B3"].value == "2026-12-31"
        questions = workbook["客户问题"]
        assert all(cell.value != "secret" for row in questions.iter_rows() for cell in row)
        evidence = workbook["证据索引"]
        assert any(cell.value == "source-001" for row in evidence.iter_rows() for cell in row)
        quality = workbook["数据质量"]
        assert quality["A2"].value == "增值税（vat）"
        assert quality["B2"].value == "待补充资料"
        assert quality["A1"].alignment.horizontal == "center"
        assert workbook["运行记录"]["A2"].value == "检查开始（check_started）"
        assert workbook["税费筛查"].column_dimensions["A"].width >= 30
        assert workbook["正式计算"].column_dimensions["H"].width >= 40
        assert workbook["规则版本"].column_dimensions["E"].width >= 40
    finally:
        workbook.close()


def test_workpaper_rejects_non_snapshot_inputs(tmp_path: Path):
    with pytest.raises(ValueError, match="^invalid_workpaper_snapshot$"):
        write_tax_health_workpaper({"revision": 1}, tmp_path / "bad.xlsx")


def test_workpaper_keeps_customer_confirmation_out_of_file_evidence_index(tmp_path: Path):
    snapshot = _snapshot()
    fact = replace(
        snapshot.request.facts[0],
        state=FactState.CONFIRMED,
        evidence_refs=("customer-confirmation:question-001",),
        confirmed_by="customer",
        confirmed_at="2026-07-30T10:05:00+08:00",
    )
    snapshot = replace(snapshot, request=replace(snapshot.request, facts=(fact,)))
    path = tmp_path / "confirmation.xlsx"

    write_tax_health_workpaper(snapshot, path)

    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        evidence = workbook["证据索引"]
        values = {
            str(cell.value)
            for row in evidence.iter_rows()
            for cell in row
            if cell.value is not None
        }
        assert "customer-confirmation:question-001" not in values
    finally:
        workbook.close()


def test_workpaper_uses_case_reconciliation_results_instead_of_placeholders(tmp_path: Path):
    path = tmp_path / "reconciled.xlsx"
    case_audit = {
        "reconciliation": {
            "trial_balance": {"passed": True, "items": [], "issues": []},
            "journal_to_trial_balance": {
                "passed": True,
                "items": [{"status": "matched"}],
                "issues": [],
            },
        },
        "structured_reconciliation": {
            "passed": True,
            "checks": [
                {"check": "document_set", "document_id": "document_set", "status": "passed"},
                {"check": "balance_sheet_identity", "document_id": "bs", "status": "passed"},
                {"check": "tax_fact", "fact_key": "vat.taxable_sales", "status": "passed"},
            ],
            "issues": [],
        },
    }

    write_tax_health_workpaper(_snapshot(), path, case_audit=case_audit)

    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        accounting = list(workbook["账务勾稽"].values)
        financial = list(workbook["财报勾稽"].values)
        filing = list(workbook["申报勾稽"].values)
        assert ("试算平衡表", "通过", "问题 0 项") in accounting
        assert ("序时账至试算平衡表", "通过", "匹配 1 项；问题 0 项") in accounting
        assert ("资产负债表恒等式", "bs", "通过") in financial
        assert ("文档集合", "案件整体", "通过") in financial
        assert ("申报事实勾稽", "vat.taxable_sales", "通过") in filing
        assert all("未提供" not in str(cell) for rows in (accounting, financial, filing) for row in rows for cell in row)
    finally:
        workbook.close()


def test_workpaper_writes_case_and_contract_strings_as_text_not_formulas(tmp_path: Path):
    path = tmp_path / "formula-safe.xlsx"
    source_manifest = {
        "sources": [{
            "source_id": "source-001",
            "filename": "=HYPERLINK(\"https://example.invalid\")",
            "status": "accepted",
            "sha256": "a" * 64,
        }]
    }
    case_audit = {
        "structured_reconciliation": {
            "checks": [
                {"check": "balance_sheet_identity", "document_id": "+CMD", "status": "passed"},
                {"check": "tax_fact", "fact_key": "@SUM(A1:A2)", "status": "passed"},
            ]
        }
    }

    write_tax_health_workpaper(
        _snapshot(), path, source_manifest=source_manifest, case_audit=case_audit
    )

    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        cells = [
            cell
            for name in ("资料清单", "抽取与映射", "财报勾稽", "申报勾稽")
            for row in workbook[name].iter_rows()
            for cell in row
            if isinstance(cell.value, str)
            and any(marker in cell.value for marker in ("HYPERLINK", "CMD", "SUM"))
        ]
        assert {cell.value for cell in cells} == {
            "'=HYPERLINK(\"https://example.invalid\")",
            "'+CMD",
            "'@SUM(A1:A2)",
        }
        assert all(cell.data_type == "s" for cell in cells)
    finally:
        workbook.close()
