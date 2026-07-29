"""Bounded PDF/image inspection primitives for the local OCR provider."""

from dataclasses import dataclass
from pathlib import Path

from .document_providers import LocalDocumentSource


@dataclass(frozen=True, slots=True)
class OcrBudgets:
    max_file_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 500
    max_page_content_stream_bytes: int = 32 * 1024 * 1024
    max_image_pixels: int = 50_000_000
    max_total_rendered_pixels: int = 500_000_000
    render_dpi: int = 200


@dataclass(frozen=True, slots=True)
class NativeFragment:
    text: str
    coordinates: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class PdfPageInput:
    page_number: int
    width: float
    height: float
    native_fragments: tuple[NativeFragment, ...]
    needs_ocr: bool
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImageInput:
    width: int
    height: int
    pixels: int


class LocalOcrInspectionError(ValueError):
    pass


def _check_file_size(source: LocalDocumentSource, budgets: OcrBudgets) -> None:
    try:
        size = source.path.stat().st_size
    except OSError as error:
        raise LocalOcrInspectionError("source_unreadable") from error
    if size > budgets.max_file_bytes:
        raise LocalOcrInspectionError("file_size_limit_exceeded")


def _page_box(page: object) -> tuple[float, float, tuple[tuple[float, float], ...]]:
    box = page.mediabox
    width = float(box.width)
    height = float(box.height)
    if width <= 0 or height <= 0:
        raise LocalOcrInspectionError("invalid_pdf_page_size")
    return width, height, ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height))


def inspect_pdf(
    source: LocalDocumentSource,
    dependencies: object,
    budgets: OcrBudgets,
) -> tuple[PdfPageInput, ...]:
    _check_file_size(source, budgets)
    try:
        reader = dependencies.pypdf.PdfReader(str(source.path), strict=False)
    except Exception as error:
        raise LocalOcrInspectionError("invalid_pdf") from error
    if bool(getattr(reader, "is_encrypted", False)):
        raise LocalOcrInspectionError("encrypted_pdf")
    pages = tuple(reader.pages)
    if len(pages) > budgets.max_pdf_pages:
        raise LocalOcrInspectionError("pdf_page_limit_exceeded")
    inputs: list[PdfPageInput] = []
    for index, page in enumerate(pages, start=1):
        width, height, page_coordinates = _page_box(page)
        try:
            contents = page.get_contents()
            content_size = len(contents.get_data()) if contents is not None else 0
        except Exception as error:
            inputs.append(PdfPageInput(index, width, height, (), False, ("pdf_content_stream_unreadable",)))
            continue
        if content_size > budgets.max_page_content_stream_bytes:
            inputs.append(PdfPageInput(index, width, height, (), False, ("pdf_content_stream_too_large",)))
            continue
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = text.strip()
        fragments = (NativeFragment(text, page_coordinates),) if text else ()
        inputs.append(PdfPageInput(index, width, height, fragments, not bool(fragments), ()))
    return tuple(inputs)


def inspect_image(
    source: LocalDocumentSource,
    dependencies: object,
    budgets: OcrBudgets,
) -> ImageInput:
    _check_file_size(source, budgets)
    try:
        image = dependencies.pillow.Image.open(source.path)
        frames = int(getattr(image, "n_frames", 1))
        width, height = int(image.width), int(image.height)
    except LocalOcrInspectionError:
        raise
    except Exception as error:
        raise LocalOcrInspectionError("invalid_image") from error
    finally:
        close = locals().get("image")
        if close is not None and callable(getattr(close, "close", None)):
            close.close()
    pixels = width * height
    if frames > 1:
        raise LocalOcrInspectionError("multi_frame_image_unsupported")
    if pixels > budgets.max_image_pixels:
        raise LocalOcrInspectionError("image_pixel_limit_exceeded")
    if width <= 0 or height <= 0:
        raise LocalOcrInspectionError("invalid_image_size")
    return ImageInput(width, height, pixels)
