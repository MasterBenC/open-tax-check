"""Lazy OCR dependencies and tamper-evident local model manifests."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
from typing import Callable

MODEL_MANIFEST_NAME = "open-tax-check-ocr-models.json"
MODEL_MANIFEST_VERSION = "local-paddleocr/v1"
REQUIRED_MODULES = ("pypdf", "pypdfium2", "PIL", "paddleocr", "paddle")


class OcrDependencyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OcrDependencies:
    pypdf: object
    pypdfium2: object
    pillow: object
    paddleocr: object
    paddle: object


@dataclass(frozen=True, slots=True)
class OcrModelFile:
    name: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class OcrModelManifest:
    schema_version: str
    provider: str
    provider_version: str
    model_dir: str
    files: tuple[OcrModelFile, ...]
    prepared_at: str


def load_ocr_dependencies(*, importer: Callable[[str], object] = importlib.import_module) -> OcrDependencies:
    modules = {}
    try:
        for name in REQUIRED_MODULES:
            modules[name] = importer(name)
    except (ImportError, ModuleNotFoundError) as error:
        raise OcrDependencyError("local_ocr_dependency_missing") from error
    return OcrDependencies(
        pypdf=modules["pypdf"],
        pypdfium2=modules["pypdfium2"],
        pillow=modules["PIL"],
        paddleocr=modules["paddleocr"],
        paddle=modules["paddle"],
    )


def _file_records(model_dir: Path) -> tuple[OcrModelFile, ...]:
    records = []
    for path in sorted(model_dir.rglob("*")):
        if not path.is_file() or path.name == MODEL_MANIFEST_NAME:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(OcrModelFile(path.relative_to(model_dir).as_posix(), digest, path.stat().st_size))
    if not records:
        raise OcrDependencyError("local_ocr_model_unavailable")
    return tuple(records)


def _manifest_payload(manifest: OcrModelManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "provider": manifest.provider,
        "provider_version": manifest.provider_version,
        "model_dir": manifest.model_dir,
        "files": [
            {"name": item.name, "sha256": item.sha256, "size_bytes": item.size_bytes}
            for item in manifest.files
        ],
        "prepared_at": manifest.prepared_at,
    }


def prepare_ocr_models(
    model_dir: Path,
    *,
    initializer: Callable[[Path], None] | None = None,
) -> OcrModelManifest:
    model_dir = model_dir.resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    if initializer is None:
        try:
            has_model_files = any(
                path.is_file() and path.name != MODEL_MANIFEST_NAME
                for path in model_dir.rglob("*")
            )
        except OSError as error:
            raise OcrDependencyError("local_ocr_model_unavailable") from error
        if not has_model_files:
            raise OcrDependencyError("local_ocr_model_unavailable")
    else:
        initializer(model_dir)
    manifest = OcrModelManifest(
        MODEL_MANIFEST_VERSION,
        "local-paddleocr",
        "3.7",
        str(model_dir),
        _file_records(model_dir),
        datetime.now(timezone.utc).isoformat(),
    )
    (model_dir / MODEL_MANIFEST_NAME).write_text(
        json.dumps(_manifest_payload(manifest), ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def load_ocr_model_manifest(model_dir: Path) -> OcrModelManifest:
    model_dir = model_dir.resolve()
    try:
        payload = json.loads((model_dir / MODEL_MANIFEST_NAME).read_text(encoding="utf-8"))
        files = tuple(OcrModelFile(**item) for item in payload["files"])
        manifest = OcrModelManifest(
            payload["schema_version"], payload["provider"], payload["provider_version"],
            payload["model_dir"], files, payload["prepared_at"],
        )
        if manifest.schema_version != MODEL_MANIFEST_VERSION or manifest.provider != "local-paddleocr":
            raise ValueError
        if manifest.model_dir != str(model_dir) or manifest.files != _file_records(model_dir):
            raise ValueError
        return manifest
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise OcrDependencyError("local_ocr_model_unavailable") from error
