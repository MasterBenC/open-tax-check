from copy import deepcopy
import hashlib
import json
from pathlib import Path

from open_tax_check import cli
from test_structured_documents import financial_payload, tax_return_payload


def write_payload(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_case(tmp_path):
    case = tmp_path / "case"
    (case / "input").mkdir(parents=True)
    (case / "work").mkdir()
    source_path = case / "input" / "statement.pdf"
    source_path.write_bytes(b"trusted source")
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    (case / "state.json").write_text(json.dumps({
        "entity_name": "完全虚构科技有限公司",
        "review_period": {"start": "2026-01-01", "end": "2026-12-31"},
    }), encoding="utf-8")
    (case / "work" / "input_manifest.json").write_text(json.dumps({"sources": [{
        "source_id": "trusted-source",
        "filename": source_path.name,
        "media_type": "application/pdf",
        "sha256": digest,
        "status": "present_unparsed",
    }]}), encoding="utf-8")
    return case


def invoke(monkeypatch, *args):
    monkeypatch.setattr(cli.sys, "argv", ["opentaxcheck", *map(str, args)])
    return cli.main()


def test_validate_structured_document_supports_both_kinds(tmp_path, monkeypatch, capsys):
    for kind, payload in [("financial-statement", financial_payload()), ("tax-return", tax_return_payload())]:
        path = tmp_path / f"{kind}.json"
        write_payload(path, payload)
        assert invoke(monkeypatch, "validate-structured-document", "--kind", kind, "--input", path) == 0
        assert json.loads(capsys.readouterr().out) == {
            "valid": True,
            "kind": kind,
            "schema_version": payload["schema_version"],
        }


def test_validate_structured_document_returns_stable_invalid_error(tmp_path, monkeypatch, capsys):
    path = tmp_path / "invalid.json"
    payload = financial_payload()
    payload["currency"] = "USD"
    write_payload(path, payload)

    assert invoke(monkeypatch, "validate-structured-document", "--kind", "financial-statement", "--input", path) == 4
    assert json.loads(capsys.readouterr().out) == {
        "valid": False,
        "error_code": "invalid_structured_document",
    }


def test_structured_document_cli_rejects_unsafe_payload_path(tmp_path, monkeypatch, capsys):
    path = tmp_path / "payload.json"
    write_payload(path, financial_payload())
    monkeypatch.setattr(cli, "resolve_safe_source", lambda path, root: (_ for _ in ()).throw(ValueError("unsafe_source_path")))

    assert invoke(monkeypatch, "validate-structured-document", "--kind", "financial-statement", "--input", path) == 4
    assert json.loads(capsys.readouterr().out) == {
        "valid": False,
        "error_code": "invalid_structured_document",
    }


def test_structured_document_cli_rejects_invalid_kind_and_missing_source(tmp_path, monkeypatch, capsys):
    payload_path = tmp_path / "payload.json"
    write_payload(payload_path, financial_payload())
    assert invoke(
        monkeypatch, "validate-structured-document", "--kind", "other", "--input", payload_path
    ) == 4
    assert json.loads(capsys.readouterr().out) == {"valid": False, "error_code": "invalid_kind"}

    case = make_case(tmp_path)
    assert invoke(
        monkeypatch, "import-structured-document", "--case", case, "--source", "missing",
        "--kind", "financial-statement", "--input", payload_path,
    ) == 4
    assert json.loads(capsys.readouterr().out) == {"status": "failed", "error_code": "invalid_source"}


def test_import_structured_document_rebinds_and_writes_case_output(tmp_path, monkeypatch, capsys):
    case = make_case(tmp_path)
    input_path = tmp_path / "payload.json"
    write_payload(input_path, financial_payload())

    assert invoke(
        monkeypatch, "import-structured-document", "--case", case, "--source", "trusted-source",
        "--kind", "financial-statement", "--input", input_path,
    ) == 0
    output = json.loads(capsys.readouterr().out)
    target = Path(output["output_path"])
    assert output["status"] == "completed"
    assert output["kind"] == "financial-statement"
    assert output["source_id"] == "trusted-source"
    assert Path(output["output_path"]) == target
    assert json.loads(target.read_text(encoding="utf-8"))["source_id"] == "trusted-source"
    receipt = json.loads(
        (case / "work/structured-document-index.json").read_text(encoding="utf-8")
    )
    assert receipt["documents"][0]["relative_path"] == target.name


def test_import_structured_document_preserves_existing_output_on_write_failure(tmp_path, monkeypatch, capsys):
    case = make_case(tmp_path)
    input_path = tmp_path / "payload.json"
    write_payload(input_path, financial_payload())
    target = case / "work" / "structured-documents" / "trusted-source.financial-statement.json"
    target.parent.mkdir()
    target.write_text('{"valid": true}\n', encoding="utf-8")
    monkeypatch.setattr(cli.os, "replace", lambda source, destination: (_ for _ in ()).throw(OSError("no")))

    assert invoke(
        monkeypatch, "import-structured-document", "--case", case, "--source", "trusted-source",
        "--kind", "financial-statement", "--input", input_path,
    ) == 3
    assert json.loads(capsys.readouterr().out) == {"status": "failed", "error_code": "write_failed"}
    assert target.read_text(encoding="utf-8") == '{"valid": true}\n'
