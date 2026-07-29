"""Opt-in local OCR smoke gate used by CI and release verification."""

import os
from pathlib import Path
from typing import Any

from .document_providers import ExtractionOptions, LocalDocumentSource
from .local_ocr import LocalOcrProvider, PaddleOcrBackend
from .ocr_runtime import OcrDependencyError, load_ocr_dependencies


def _downloadable_smoke_backend(dependencies: object) -> PaddleOcrBackend:
    """Create the CI-only backend whose official mobile models may be downloaded."""
    pipeline = dependencies.paddleocr.PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="PP-OCRv5_mobile_rec",
        device="cpu",
        enable_mkldnn=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    backend = object.__new__(PaddleOcrBackend)
    backend._dependencies = dependencies
    backend._pipeline = pipeline
    return backend


def run_ocr_smoke(
    *,
    fixture: Path | None = None,
    dependencies: Any = None,
    backend: Any = None,
) -> dict[str, object]:
    if os.environ.get("OPEN_TAX_CHECK_RUN_OCR_SMOKE") != "1":
        return {
            "status": "skipped",
            "reason": "OPEN_TAX_CHECK_RUN_OCR_SMOKE_not_enabled",
        }
    fixture = fixture or (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "ocr"
        / "fictional-tax-note.png"
    )
    if not fixture.is_file():
        return {"status": "failed", "reason": "ocr_fixture_missing"}
    try:
        dependencies = dependencies or load_ocr_dependencies()
        backend = backend or _downloadable_smoke_backend(dependencies)
        result = LocalOcrProvider().extract(
            LocalDocumentSource(
                "fictional-ocr-smoke",
                fixture,
                "image/png",
                fixture.parent,
            ),
            ExtractionOptions(dependencies=dependencies, backend=backend),
        )
    except OcrDependencyError:
        return {"status": "failed", "reason": "local_ocr_dependency_missing"}
    except Exception:
        return {"status": "failed", "reason": "ocr_runtime_initialization_failed"}
    blocks = tuple(
        block
        for block in result.blocks
        if block.text.strip()
        and block.page_number == 1
        and len(block.coordinates) == 4
        and block.confidence_source == "model"
    )
    if result.status != "completed" or not blocks:
        return {"status": "failed", "reason": "ocr_text_not_detected"}
    return {
        "status": "completed",
        "block_count": len(blocks),
        "text_characters": sum(len(block.text) for block in blocks),
    }
