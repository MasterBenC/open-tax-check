from datetime import date
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from open_tax_check.tax_health import run_tax_health_check
from open_tax_check.tax_models import DateRange, FactState, TaxCheckRequest, TaxFact
from open_tax_check.workpaper import write_tax_health_workpaper


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

    workbook = load_workbook(first, read_only=True, data_only=False)
    try:
        assert workbook.sheetnames == [
            "项目概览", "资料清单", "数据质量", "账务勾稽", "财报勾稽",
            "申报勾稽", "税务筛查", "正式计算", "客户问题", "证据索引",
            "规则版本", "运行记录",
        ]
        overview = workbook["项目概览"]
        assert overview["B2"].value == "2026-01-01"
        assert overview["B3"].value == "2026-12-31"
        questions = workbook["客户问题"]
        assert all(cell.value != "secret" for row in questions.iter_rows() for cell in row)
        evidence = workbook["证据索引"]
        assert any(cell.value == "source-001" for row in evidence.iter_rows() for cell in row)
    finally:
        workbook.close()


def test_workpaper_rejects_non_snapshot_inputs(tmp_path: Path):
    with pytest.raises(ValueError, match="^invalid_workpaper_snapshot$"):
        write_tax_health_workpaper({"revision": 1}, tmp_path / "bad.xlsx")
