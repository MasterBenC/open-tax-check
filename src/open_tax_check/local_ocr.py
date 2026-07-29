"""Bounded PDF/image inspection primitives for the local OCR provider."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable

from .document_extraction import (
    AuthorizationRecord,
    DocumentExtraction,
    ExtractionIssue,
    TableRegion,
    TextBlock,
)
from .document_providers import ExtractionOptions, LocalDocumentSource
from .ocr_runtime import OcrDependencyError, load_ocr_dependencies, load_ocr_model_manifest


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


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    quadrilateral: tuple[tuple[float, float], ...]
    confidence: float


class LocalOcrInspectionError(ValueError):
    pass


class PaddleOcrBackend:
    def __init__(self, dependencies: object, model_dir: Path):
        detection_dir, recognition_dir = _resolve_model_dirs(model_dir)
        self._dependencies = dependencies
        self._pipeline = dependencies.paddleocr.PaddleOCR(
            text_detection_model_dir=str(detection_dir),
            text_recognition_model_dir=str(recognition_dir),
            device="cpu",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    def predict(self, image: object) -> tuple[OcrLine, ...]:
        lines: list[OcrLine] = []
        for result in self._pipeline.predict(image):
            payload = result.json if hasattr(result, "json") else result
            if callable(payload):
                payload = payload()
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = None
            if isinstance(payload, dict) and "res" in payload:
                payload = payload["res"]
            if not isinstance(payload, dict):
                continue
            texts = payload.get("rec_texts") or payload.get("texts") or ()
            scores = payload.get("rec_scores") or payload.get("scores") or ()
            polygons = payload.get("rec_polys") or payload.get("rec_boxes") or ()
            for text, score, polygon in zip(texts, scores, polygons):
                points = tuple(tuple(float(value) for value in point) for point in polygon)
                if len(points) == 2:
                    x1, y1 = points[0]
                    x2, y2 = points[1]
                    points = ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
                if len(points) == 4 and str(text).strip():
                    lines.append(OcrLine(str(text), points, float(score)))
        return tuple(lines)


def _resolve_model_dirs(model_dir: Path) -> tuple[Path, Path]:
    model_dir = model_dir.resolve()
    detection_dir = model_dir / "det"
    recognition_dir = model_dir / "rec"
    has_detection = detection_dir.is_dir()
    has_recognition = recognition_dir.is_dir()
    if has_detection and has_recognition:
        return detection_dir, recognition_dir
    if has_detection or has_recognition:
        raise OcrDependencyError("local_ocr_model_unavailable")
    try:
        has_root_files = any(path.is_file() for path in model_dir.iterdir())
    except OSError as error:
        raise OcrDependencyError("local_ocr_model_unavailable") from error
    if not has_root_files:
        raise OcrDependencyError("local_ocr_model_unavailable")
    return model_dir, model_dir


class PypdfiumRenderer:
    def __init__(self, path: Path, pdfium: object):
        self._document = pdfium.PdfDocument(str(path))

    def render(self, page_number: int, *, dpi: int):
        page = self._document.get_page(page_number - 1)
        bitmap = page.render(scale=dpi / 72.0)
        return bitmap.to_pil()


class LocalOcrProvider:
    provider_id = "local-paddleocr"
    provider_version = "3.7"

    def extract(self, source: LocalDocumentSource, options: ExtractionOptions) -> DocumentExtraction:
        budgets = options.budgets or OcrBudgets()
        try:
            dependencies = options.dependencies or load_ocr_dependencies()
            if options.backend is None:
                if options.model_dir is None:
                    raise OcrDependencyError("local_ocr_model_unavailable")
                load_ocr_model_manifest(options.model_dir)
                backend = PaddleOcrBackend(dependencies, options.model_dir)
            else:
                backend = options.backend
        except OcrDependencyError as error:
            return self._result(source, "not_performed", str(error))
        try:
            if source.media_type == "application/pdf":
                return self._extract_pdf(source, dependencies, backend, options.renderer, budgets)
            if source.media_type in {"image/png", "image/jpeg"}:
                inspect_image(source, dependencies, budgets)
                return self._extract_image(source, backend)
            return self._result(source, "failed", "unsupported_document_media_type")
        except LocalOcrInspectionError as error:
            return self._result(source, "failed", str(error))
        except Exception:
            return self._result(source, "failed", "local_ocr_failed")

    def _extract_image(self, source, backend):
        blocks = tuple(self._block(line, 1) for line in backend.predict(source.path))
        status = "completed" if blocks else "failed"
        issues = () if blocks else (ExtractionIssue("page_has_no_text", "warning", "page_has_no_text", 1),)
        return self._result(source, status, None, blocks=blocks, issues=issues)

    def _extract_pdf(self, source, dependencies, backend, renderer, budgets):
        pages = inspect_pdf(source, dependencies, budgets)
        renderer = renderer or PypdfiumRenderer(source.path, dependencies.pypdfium2)
        blocks = []
        issues = []
        successful_pages = 0
        failed_pages = 0
        rendered_pixels = 0
        for page in pages:
            for fragment in page.native_fragments:
                blocks.append(TextBlock(fragment.text, page.page_number, fragment.coordinates, None, "native_unscored"))
            if page.native_fragments:
                successful_pages += 1
            for code in page.issues:
                issues.append(ExtractionIssue(code, "error", code, page.page_number))
                failed_pages += 1
            if not page.needs_ocr:
                continue
            pixels = int(page.width * budgets.render_dpi / 72 * page.height * budgets.render_dpi / 72)
            if rendered_pixels + pixels > budgets.max_total_rendered_pixels:
                issues.append(ExtractionIssue("render_pixel_limit_exceeded", "error", "render_pixel_limit_exceeded", page.page_number))
                failed_pages += 1
                continue
            rendered_pixels += pixels
            try:
                lines = tuple(backend.predict(renderer.render(page.page_number, dpi=budgets.render_dpi)))
            except Exception:
                issues.append(ExtractionIssue("pdf_page_render_failed", "error", "pdf_page_render_failed", page.page_number))
                failed_pages += 1
                continue
            if not lines:
                issues.append(ExtractionIssue("page_has_no_text", "warning", "page_has_no_text", page.page_number))
                failed_pages += 1
                continue
            blocks.extend(self._block(line, page.page_number) for line in lines)
            successful_pages += 1
        if failed_pages and successful_pages:
            status = "partial"
        elif failed_pages:
            status = "failed"
        else:
            status = "completed"
        return self._result(source, status, None, blocks=tuple(blocks), issues=tuple(issues))

    @staticmethod
    def _block(line: OcrLine, page_number: int) -> TextBlock:
        return TextBlock(line.text, page_number, line.quadrilateral, line.confidence, "model")

    def _result(self, source, status, issue_code=None, *, blocks=(), issues=()):
        if issue_code:
            issues = tuple(issues) + (ExtractionIssue(issue_code, "error", issue_code),)
        return DocumentExtraction(
            "document-extraction/v1.1", f"{self.provider_id}-{source.source_id}", source.source_id,
            source.path.name, source.media_type, self.provider_id, self.provider_version,
            datetime.now(timezone.utc).isoformat(),
            AuthorizationRecord(self.provider_id, self.provider_version, "none", "local", datetime.now(timezone.utc).isoformat(), "local", "none"),
            status, (), tuple(blocks), (), tuple(issues), (source.source_id,),
        )


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
