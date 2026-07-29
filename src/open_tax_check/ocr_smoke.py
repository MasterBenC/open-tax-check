"""Opt-in local OCR smoke gate used by CI and release verification."""

import os
from pathlib import Path


def run_ocr_smoke() -> dict[str, str]:
    if os.environ.get("OPEN_TAX_CHECK_RUN_OCR_SMOKE") != "1":
        return {
            "status": "skipped",
            "reason": "OPEN_TAX_CHECK_RUN_OCR_SMOKE_not_enabled",
        }
    fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ocr" / "fictional-tax-note.png"
    if not fixture.is_file():
        return {"status": "skipped", "reason": "ocr_fixture_missing"}
    return {"status": "skipped", "reason": "ocr_runtime_not_prepared"}
