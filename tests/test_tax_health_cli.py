import json

from open_tax_check import cli


def request_payload():
    return {
        "period": {"start": "2026-01-01", "end": "2026-12-31"},
        "jurisdiction": "CN",
        "facts": [],
        "input_hashes": [],
    }


def test_tax_health_check_cli_writes_customer_report_and_machine_snapshot(monkeypatch, tmp_path, capsys):
    request_path = tmp_path / "request.json"
    output_dir = tmp_path / "output"
    request_path.write_text(json.dumps(request_payload()), encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck", "tax-health-check", "--request", str(request_path), "--output-dir", str(output_dir)
    ])

    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    report_path = output_dir / "税务健康检查报告.md"
    snapshot_path = output_dir / "tax-health-snapshot.json"
    assert result["status"] == "completed"
    assert report_path.is_file()
    assert snapshot_path.is_file()
    assert report_path.read_text(encoding="utf-8").startswith("# ")
    assert json.loads(snapshot_path.read_text(encoding="utf-8"))["revision"] == 1


def test_tax_health_check_cli_rejects_unknown_request_fields(monkeypatch, tmp_path, capsys):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps({**request_payload(), "unexpected": True}), encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck", "tax-health-check", "--request", str(request_path), "--output-dir", str(tmp_path / "output")
    ])

    assert cli.main() == 4
    assert json.loads(capsys.readouterr().out)["error_code"] == "invalid_tax_health_request"
