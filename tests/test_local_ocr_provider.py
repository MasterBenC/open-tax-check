from pathlib import Path
from types import SimpleNamespace

from open_tax_check.document_providers import ExtractionOptions, LocalDocumentSource
import pytest

from open_tax_check.local_ocr import LocalOcrProvider, OcrLine, PaddleOcrBackend
from open_tax_check.ocr_runtime import OcrDependencyError


class FakeBackend:
    def __init__(self, lines):
        self.lines = tuple(lines)
        self.calls = []

    def predict(self, image):
        self.calls.append(image)
        return self.lines


class FakeRenderer:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.calls = []

    def render(self, page_number, *, dpi):
        self.calls.append((page_number, dpi))
        if page_number in self.failures:
            raise RuntimeError("render failed")
        return f"page-{page_number}"


def image_source(tmp_path):
    path = tmp_path / "note.png"
    path.write_bytes(b"png")
    return LocalDocumentSource("src-image", path, "image/png", tmp_path)


def pdf_source(tmp_path):
    path = tmp_path / "mixed.pdf"
    path.write_bytes(b"pdf")
    return LocalDocumentSource("src-pdf", path, "application/pdf", tmp_path)


def image_dependencies():
    image = SimpleNamespace(width=100, height=100, n_frames=1, close=lambda: None)
    return SimpleNamespace(pillow=SimpleNamespace(Image=SimpleNamespace(open=lambda path: image)))


def pdf_dependencies():
    native = SimpleNamespace(
        mediabox=SimpleNamespace(width=100, height=200),
        get_contents=lambda: SimpleNamespace(get_data=lambda: b""),
        extract_text=lambda: "native text",
    )
    scanned = SimpleNamespace(
        mediabox=SimpleNamespace(width=100, height=200),
        get_contents=lambda: SimpleNamespace(get_data=lambda: b""),
        extract_text=lambda: "",
    )
    reader = SimpleNamespace(is_encrypted=False, pages=[native, scanned])
    return SimpleNamespace(
        pypdf=SimpleNamespace(PdfReader=lambda *args, **kwargs: reader),
    )


def test_image_ocr_returns_model_scored_block(tmp_path):
    backend = FakeBackend([OcrLine("中文税务", ((0, 0), (80, 0), (80, 20), (0, 20)), 0.97)])
    result = LocalOcrProvider().extract(
        image_source(tmp_path),
        ExtractionOptions(dependencies=image_dependencies(), backend=backend),
    )

    assert result.status == "completed"
    assert result.blocks[0].confidence_source == "model"
    assert result.blocks[0].confidence == 0.97


def test_mixed_pdf_prefers_native_text_and_ocr_only_scanned_page(tmp_path):
    backend = FakeBackend([OcrLine("扫描页", ((0, 0), (80, 0), (80, 20), (0, 20)), 0.91)])
    renderer = FakeRenderer()
    result = LocalOcrProvider().extract(
        pdf_source(tmp_path),
        ExtractionOptions(dependencies=pdf_dependencies(), backend=backend, renderer=renderer),
    )

    assert result.status == "completed"
    assert [block.text for block in result.blocks] == ["native text", "扫描页"]
    assert result.blocks[0].confidence_source == "native_unscored"
    assert renderer.calls == [(2, 200)]
    assert len(backend.calls) == 1


def test_failed_scanned_page_produces_partial_result(tmp_path):
    backend = FakeBackend([OcrLine("unused", ((0, 0), (1, 0), (1, 1), (0, 1)), 0.9)])
    renderer = FakeRenderer(failures=(2,))
    result = LocalOcrProvider().extract(
        pdf_source(tmp_path),
        ExtractionOptions(dependencies=pdf_dependencies(), backend=backend, renderer=renderer),
    )

    assert result.status == "partial"
    assert any(issue.code == "pdf_page_render_failed" for issue in result.issues)


def test_missing_local_ocr_dependencies_are_not_performed(monkeypatch, tmp_path):
    def missing_dependencies():
        raise OcrDependencyError("local_ocr_dependency_missing")

    monkeypatch.setattr(
        "open_tax_check.local_ocr.load_ocr_dependencies",
        missing_dependencies,
    )
    result = LocalOcrProvider().extract(image_source(tmp_path), ExtractionOptions())

    assert result.status == "not_performed"
    assert result.issues[0].code == "local_ocr_dependency_missing"


def test_paddle_backend_uses_separate_detection_and_recognition_model_dirs(tmp_path):
    det_dir = tmp_path / "det"
    rec_dir = tmp_path / "rec"
    det_dir.mkdir()
    rec_dir.mkdir()
    calls = []

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def predict(self, image):
            return []

    dependencies = SimpleNamespace(paddleocr=SimpleNamespace(PaddleOCR=FakePaddleOCR))

    PaddleOcrBackend(dependencies, tmp_path)

    assert calls == [{
        "text_detection_model_dir": str(det_dir),
        "text_recognition_model_dir": str(rec_dir),
        "device": "cpu",
        "enable_mkldnn": False,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
    }]


def test_paddle_backend_rejects_missing_model_layout(tmp_path):
    (tmp_path / "det").mkdir()

    with pytest.raises(OcrDependencyError, match="local_ocr_model_unavailable"):
        PaddleOcrBackend(SimpleNamespace(paddleocr=SimpleNamespace(PaddleOCR=lambda **_: None)), tmp_path)


def test_paddle_backend_accepts_json_method_result(tmp_path):
    class Result:
        def json(self):
            return {
                "res": {
                    "rec_texts": ["tax"],
                    "rec_scores": [0.88],
                    "rec_polys": [[ [1, 2], [11, 2], [11, 12], [1, 12] ]],
                }
            }

    class Pipeline:
        def predict(self, image):
            return [Result()]

    backend = object.__new__(PaddleOcrBackend)
    backend._pipeline = Pipeline()

    assert backend.predict("image") == (
        OcrLine("tax", ((1.0, 2.0), (11.0, 2.0), (11.0, 12.0), (1.0, 12.0)), 0.88),
    )


def test_paddle_backend_converts_path_input_to_string(tmp_path):
    received = []

    class Pipeline:
        def predict(self, image):
            received.append(image)
            return []

    backend = object.__new__(PaddleOcrBackend)
    backend._pipeline = Pipeline()

    backend.predict(tmp_path / "note.png")

    assert received == [str(tmp_path / "note.png")]
