from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

from open_tax_check.document_extraction import AuthorizationRecord
from open_tax_check import cli
from open_tax_check.document_extraction import DocumentExtraction


def extraction_payload():
    authorization = AuthorizationRecord(
        "host", "1", "text", "client", "2026-07-29T00:00:00+00:00", "file", "none"
    )
    return {
        "schema_version": "document-extraction/v1",
        "document_id": "doc-1",
        "source_id": "forged",
        "filename": "forged.pdf",
        "media_type": "application/pdf",
        "provider": "host",
        "provider_version": "1",
        "executed_at": "2026-07-29T00:00:00+00:00",
        "authorization": asdict(authorization),
        "status": "completed",
        "pages": [],
        "blocks": [],
        "tables": [],
        "issues": [],
        "source_refs": ["forged"],
    }


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "open_tax_check.cli", *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_validate_extraction_reports_schema_version(tmp_path):
    input_path = tmp_path / "result.json"
    input_path.write_text(json.dumps(extraction_payload()), encoding="utf-8")

    completed = run_cli("validate-extraction", "--input", input_path)

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "valid": True,
        "schema_version": "document-extraction/v1",
    }


def test_import_extraction_rebinds_source_and_writes_atomically(tmp_path):
    case_dir = tmp_path / "case"
    input_dir = case_dir / "input"
    work_dir = case_dir / "work"
    input_dir.mkdir(parents=True)
    work_dir.mkdir()
    source_path = input_dir / "evidence.pdf"
    source_path.write_bytes(b"pdf")
    source_id = "a" * 16
    (work_dir / "workbook_inventory.json").write_text(
        json.dumps({"sources": [{"source_id": source_id, "filename": source_path.name}]}),
        encoding="utf-8",
    )
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(extraction_payload()), encoding="utf-8")

    completed = run_cli(
        "import-extraction", "--case", case_dir, "--source", source_id, "--input", result_path
    )

    assert completed.returncode == 0
    imported_path = work_dir / "document-extractions" / f"{source_id}.json"
    imported = json.loads(imported_path.read_text(encoding="utf-8"))
    assert imported["source_id"] == source_id
    assert imported["filename"] == source_path.name
    assert imported["source_refs"] == [source_id]
    assert list(imported_path.parent.glob("*.tmp")) == []


def test_atomic_json_writer_replaces_completed_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / "result.json"
    replacements = []

    def record_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        Path(source).rename(destination)

    monkeypatch.setattr("open_tax_check.cli.os.replace", record_replace)

    cli._write_json_atomic(target, {"status": "completed"})

    assert len(replacements) == 1
    assert replacements[0][1] == target
    assert replacements[0][0].parent == target.parent
    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "completed"}


def minimal_extraction(source_id, filename):
    authorization = AuthorizationRecord(
        "local-paddleocr", "3.7", "none", "local", "2026-07-29T00:00:00+00:00", "local", "none"
    )
    return DocumentExtraction(
        "document-extraction/v1.1", "doc", source_id, filename, "image/png",
        "local-paddleocr", "3.7", "2026-07-29T00:00:00+00:00", authorization,
        "completed", (), (), (), (), (source_id,),
    )


def test_prepare_ocr_cli_prints_manifest_without_running_case(monkeypatch, tmp_path, capsys):
    class Manifest:
        def __dict__(self):
            return {}

    monkeypatch.setattr(cli, "prepare_ocr_models", lambda path: {"provider": "local-paddleocr"}, raising=False)
    monkeypatch.setattr(cli.sys, "argv", ["opentaxcheck", "prepare-ocr", "--model-dir", str(tmp_path)])

    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["provider"] == "local-paddleocr"


def test_extract_document_cli_persists_local_result_and_uses_existing_model(monkeypatch, tmp_path, capsys):
    case_dir = tmp_path / "case"
    input_dir = case_dir / "input"
    work_dir = case_dir / "work"
    input_dir.mkdir(parents=True)
    work_dir.mkdir()
    source_id = "b" * 16
    source_path = input_dir / "note.png"
    source_path.write_bytes(b"image")
    (work_dir / "workbook_inventory.json").write_text(
        json.dumps({"sources": [{"source_id": source_id, "filename": source_path.name}]}),
        encoding="utf-8",
    )
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    calls = []

    class FakeProvider:
        def extract(self, source, options):
            calls.append((source, options))
            return minimal_extraction(source_id, source_path.name)

    monkeypatch.setattr(cli, "LocalOcrProvider", lambda: FakeProvider(), raising=False)
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck", "extract-document", "--case", str(case_dir), "--source", source_id,
        "--provider", "local-paddleocr", "--model-dir", str(model_dir),
    ])

    assert cli.main() == 0
    output = json.loads(capsys.readouterr().out)
    persisted = json.loads((work_dir / "document-extractions" / f"{source_id}.json").read_text(encoding="utf-8"))
    assert output["status"] == "completed"
    assert persisted["source_id"] == source_id
    assert calls[0][1].model_dir == model_dir
