import os

from open_tax_check.ocr_smoke import run_ocr_smoke


def test_ocr_smoke_is_explicitly_skipped_without_opt_in(monkeypatch):
    monkeypatch.delenv("OPEN_TAX_CHECK_RUN_OCR_SMOKE", raising=False)

    result = run_ocr_smoke()

    assert result == {
        "status": "skipped",
        "reason": "OPEN_TAX_CHECK_RUN_OCR_SMOKE_not_enabled",
    }
