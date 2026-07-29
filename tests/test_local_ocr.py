from pathlib import Path
from types import SimpleNamespace

import pytest

from open_tax_check.document_providers import LocalDocumentSource
from open_tax_check.local_ocr import (
    ImageInput,
    LocalOcrInspectionError,
    NativeFragment,
    OcrBudgets,
    PdfPageInput,
    inspect_image,
    inspect_pdf,
)


def source(tmp_path, suffix=".pdf"):
    path = tmp_path / f"source{suffix}"
    path.write_bytes(b"input")
    return LocalDocumentSource("src-1", path, "application/pdf" if suffix == ".pdf" else "image/png", tmp_path)


def dependencies(reader, image):
    return SimpleNamespace(
        pypdf=SimpleNamespace(PdfReader=lambda *args, **kwargs: reader),
        pillow=SimpleNamespace(Image=SimpleNamespace(open=lambda *args, **kwargs: image)),
    )


def page(text="native", content_size=0, width=612, height=792):
    content = SimpleNamespace(get_data=lambda: b"x" * content_size)
    return SimpleNamespace(
        mediabox=SimpleNamespace(width=width, height=height),
        get_contents=lambda: content,
        extract_text=lambda: text,
    )


def test_pdf_native_text_is_preferred_without_ocr(tmp_path):
    reader = SimpleNamespace(is_encrypted=False, pages=[page("tax note")])

    result = inspect_pdf(source(tmp_path), dependencies(reader, None), OcrBudgets())

    assert result == (
        PdfPageInput(
            page_number=1,
            width=612.0,
            height=792.0,
            native_fragments=(NativeFragment("tax note", ((0.0, 0.0), (612.0, 0.0), (612.0, 792.0), (0.0, 792.0))),),
            needs_ocr=False,
            issues=(),
        ),
    )


def test_pdf_content_stream_budget_stops_page_before_text_parse(tmp_path):
    reader = SimpleNamespace(is_encrypted=False, pages=[page("secret", content_size=33)])

    result = inspect_pdf(
        source(tmp_path), dependencies(reader, None), OcrBudgets(max_page_content_stream_bytes=32)
    )

    assert result[0].issues == ("pdf_content_stream_too_large",)
    assert result[0].needs_ocr is False


def test_encrypted_pdf_fails_closed(tmp_path):
    reader = SimpleNamespace(is_encrypted=True, pages=[])

    with pytest.raises(LocalOcrInspectionError, match="encrypted_pdf"):
        inspect_pdf(source(tmp_path), dependencies(reader, None), OcrBudgets())


def test_image_pixel_and_frame_limits_are_checked(tmp_path):
    image = SimpleNamespace(width=5001, height=10000, n_frames=1)
    with pytest.raises(LocalOcrInspectionError, match="image_pixel_limit_exceeded"):
        inspect_image(
            source(tmp_path, ".png"),
            dependencies(None, image),
            OcrBudgets(max_image_pixels=50_000_000),
        )

    image = SimpleNamespace(width=100, height=100, n_frames=2)
    with pytest.raises(LocalOcrInspectionError, match="multi_frame_image_unsupported"):
        inspect_image(source(tmp_path, ".png"), dependencies(None, image), OcrBudgets())
