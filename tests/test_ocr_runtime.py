import json
from pathlib import Path

import pytest

from open_tax_check import ocr_runtime


def test_missing_ocr_dependency_has_stable_error():
    def missing_import(name):
        raise ModuleNotFoundError(name)

    with pytest.raises(ocr_runtime.OcrDependencyError, match="local_ocr_dependency_missing"):
        ocr_runtime.load_ocr_dependencies(importer=missing_import)


def test_prepare_and_load_model_manifest_records_deterministic_hashes(tmp_path):
    def initializer(model_dir):
        (model_dir / "det.model").write_bytes(b"det")
        (model_dir / "rec.model").write_bytes(b"rec")

    manifest = ocr_runtime.prepare_ocr_models(tmp_path, initializer=initializer)

    assert manifest.provider == "local-paddleocr"
    assert [item.name for item in manifest.files] == ["det.model", "rec.model"]
    assert ocr_runtime.load_ocr_model_manifest(tmp_path) == manifest


def test_model_manifest_rejects_changed_file(tmp_path):
    def initializer(model_dir):
        (model_dir / "model.bin").write_bytes(b"original")

    ocr_runtime.prepare_ocr_models(tmp_path, initializer=initializer)
    (tmp_path / "model.bin").write_bytes(b"changed")

    with pytest.raises(ocr_runtime.OcrDependencyError, match="local_ocr_model_unavailable"):
        ocr_runtime.load_ocr_model_manifest(tmp_path)


def test_package_declares_exact_ocr_extra():
    import tomllib

    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["optional-dependencies"]["ocr"] == [
        "pypdf>=6.14.2,<7",
        "pypdfium2>=5.12.1,<6",
        "Pillow>=12.3,<13",
        "paddleocr>=3.7,<4",
        "paddlepaddle>=3.3.1,<4",
    ]
