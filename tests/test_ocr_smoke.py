import os
from pathlib import Path
import struct
from types import SimpleNamespace

from open_tax_check.ocr_smoke import run_ocr_smoke
from open_tax_check.local_ocr import OcrLine


def test_ocr_smoke_is_explicitly_skipped_without_opt_in(monkeypatch):
    monkeypatch.delenv("OPEN_TAX_CHECK_RUN_OCR_SMOKE", raising=False)

    result = run_ocr_smoke()

    assert result == {
        "status": "skipped",
        "reason": "OPEN_TAX_CHECK_RUN_OCR_SMOKE_not_enabled",
    }


def test_ocr_fixture_is_a_readable_document_image():
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "ocr"
        / "fictional-tax-note.png"
    )
    payload = fixture.read_bytes()

    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", payload[16:24])
    assert width >= 800
    assert height >= 300


def test_opted_in_smoke_executes_provider_and_validates_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_TAX_CHECK_RUN_OCR_SMOKE", "1")
    fixture = tmp_path / "fictional-tax-note.png"
    fixture.write_bytes(b"fixture")
    image = SimpleNamespace(width=1000, height=400, n_frames=1, close=lambda: None)
    dependencies = SimpleNamespace(
        pillow=SimpleNamespace(Image=SimpleNamespace(open=lambda path: image))
    )
    backend = SimpleNamespace(
        predict=lambda path: (
            OcrLine(
                "虚构税务资料 2026",
                ((10, 10), (900, 10), (900, 80), (10, 80)),
                0.97,
            ),
        )
    )

    result = run_ocr_smoke(
        fixture=fixture,
        dependencies=dependencies,
        backend=backend,
    )

    assert result == {
        "status": "completed",
        "block_count": 1,
        "text_characters": 11,
    }


def test_opted_in_smoke_never_reports_missing_fixture_as_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_TAX_CHECK_RUN_OCR_SMOKE", "1")

    result = run_ocr_smoke(fixture=tmp_path / "missing.png")

    assert result == {"status": "failed", "reason": "ocr_fixture_missing"}


def test_real_ocr_runtime_smoke():
    if os.environ.get("OPEN_TAX_CHECK_RUN_OCR_SMOKE") != "1":
        __import__("pytest").skip("real OCR smoke requires explicit opt-in")

    result = run_ocr_smoke()

    assert result["status"] == "completed", result
    assert result["block_count"] >= 1
    assert result["text_characters"] >= 4
