import json
import os
import pytest

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
        "opentaxcheck", "tax-health-check", "--request", str(request_path), "--output-dir", str(output_dir), "--include-docx"
    ])

    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    report_path = output_dir / "税务健康检查报告.md"
    snapshot_path = output_dir / "tax-health-snapshot.json"
    docx_path = output_dir / "税务健康检查报告.docx"
    assert result["status"] == "completed"
    assert report_path.is_file()
    assert snapshot_path.is_file()
    assert docx_path.is_file()
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


def test_tax_health_check_cli_accepts_explicit_calculation_pack(monkeypatch, tmp_path, capsys):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps({
        **request_payload(),
        "facts": [
            {"key": "calc.tax_base", "value": "1000.00", "state": "confirmed", "evidence_refs": ["vat-return"], "confirmed_by": "customer", "confirmed_at": "2026-01-02T00:00:00+08:00"},
            {"key": "calc.rate", "value": "0.13", "state": "confirmed", "evidence_refs": ["vat-rule"], "confirmed_by": "customer", "confirmed_at": "2026-01-02T00:00:00+08:00"},
        ],
    }), encoding="utf-8")
    pack_path = tmp_path / "vat-calculation.json"
    pack_path.write_text(json.dumps({
        "schema_version": "1.2.0",
        "pack_id": "vat-calculation-v1",
        "tax_id": "vat",
        "jurisdiction": "CN",
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
        "maturity": "calculation",
        "requirements": [
            {"fact_key": "calc.tax_base", "answer_type": "decimal", "evidence_types": ["vat_return"], "priority": 100, "blocking": True, "critical": True},
            {"fact_key": "calc.rate", "answer_type": "decimal", "evidence_types": ["rule_source"], "priority": 100, "blocking": True, "critical": True},
        ],
        "local_parameter_keys": [],
        "evaluator_id": "generic.calculation.base_minus_deductions_times_rate.v1",
        "sources": [{"title": "Official VAT source", "url": "https://example.test/vat", "published_on": "2026-01-01", "issuer": "Official issuer", "document_number": "DOC-2026", "article_refs": ["Article 1"], "verified_on": "2026-01-01"}],
        "rule_version": "2026.1",
        "fact_catalog_version": "zh-CN.v1",
        "applicability_fact_keys": [],
        "calculation": {"formula_id": "generic.base_minus_deductions_times_rate.v1", "tax_base_fact_key": "calc.tax_base", "rate_fact_key": "calc.rate", "deductions_fact_key": None},
    }), encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck", "tax-health-check", "--request", str(request_path), "--output-dir", str(tmp_path / "output"), "--pack", str(pack_path)
    ])

    assert cli.main() == 0
    snapshot = json.loads((tmp_path / "output" / "tax-health-snapshot.json").read_text(encoding="utf-8"))
    vat = next(item for item in snapshot["results"] if item["tax_id"] == "vat")
    assert vat["calculation"]["amount"] == "130.00"


def test_tax_health_check_cli_runs_builtin_stamp_tax_rate_table(monkeypatch, tmp_path, capsys):
    request_path = tmp_path / "request.json"
    output_dir = tmp_path / "output"
    request_path.write_text(json.dumps({
        **request_payload(),
        "facts": [
            {
                "key": "activity.has_stamp_taxable_instrument_or_securities_transaction",
                "value": True,
                "state": "confirmed",
                "evidence_refs": ["contract-register"],
                "confirmed_by": "customer",
                "confirmed_at": "2026-01-02T00:00:00+08:00",
            },
            {
                "key": "stamp_tax.tax_item",
                "value": "sale_contract",
                "state": "confirmed",
                "evidence_refs": ["sale-contract"],
                "confirmed_by": "customer",
                "confirmed_at": "2026-01-02T00:00:00+08:00",
            },
            {
                "key": "calc.tax_base",
                "value": "1000.00",
                "state": "confirmed",
                "evidence_refs": ["sale-contract"],
                "confirmed_by": "customer",
                "confirmed_at": "2026-01-02T00:00:00+08:00",
            },
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck", "tax-health-check", "--request", str(request_path),
        "--output-dir", str(output_dir),
    ])

    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    snapshot = json.loads((output_dir / "tax-health-snapshot.json").read_text(encoding="utf-8"))
    stamp_tax = next(item for item in snapshot["results"] if item["tax_id"] == "stamp_tax")
    assert stamp_tax["calculation"]["amount"] == "0.30"
    assert stamp_tax["calculation"]["rate"] == "0.0003"
    assert stamp_tax["calculation"]["rate_selector_label_zh"] == "\u4e70\u5356\u5408\u540c"
    report = (output_dir / "\u7a0e\u52a1\u5065\u5eb7\u68c0\u67e5\u62a5\u544a.md").read_text(encoding="utf-8")
    assert "\u9002\u7528\u7a0e\u76ee: \u4e70\u5356\u5408\u540c" in report
    assert "\u9002\u7528\u7a0e\u7387: 0.03%" in report
    assert "sale_contract" not in report


def test_tax_health_check_cli_includes_parameterized_templates_for_all_tax_items(monkeypatch, tmp_path, capsys):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request_payload()), encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck", "tax-health-check", "--request", str(request_path),
        "--output-dir", str(tmp_path / "output"), "--include-parameterized-packs",
    ])

    assert cli.main() == 0
    snapshot = json.loads((tmp_path / "output" / "tax-health-snapshot.json").read_text(encoding="utf-8"))
    assert len(snapshot["results"]) == 20
    assert all(item["rule_pack_ids"] for item in snapshot["results"])


def test_tax_health_check_cli_rejects_invalid_explicit_pack(monkeypatch, tmp_path, capsys):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request_payload()), encoding="utf-8")
    pack_path = tmp_path / "invalid-pack.json"
    pack_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck", "tax-health-check", "--request", str(request_path), "--output-dir", str(tmp_path / "output"), "--pack", str(pack_path)
    ])

    assert cli.main() == 4
    assert json.loads(capsys.readouterr().out)["error_code"] == "invalid_tax_rule_pack"


def test_tax_health_check_cli_rejects_symlink_explicit_pack(monkeypatch, tmp_path, capsys):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request_payload()), encoding="utf-8")
    target = tmp_path / "target-pack.json"
    target.write_text(json.dumps({
        "schema_version": "1.2.0", "pack_id": "vat-calculation-v1", "tax_id": "vat",
        "jurisdiction": "CN", "effective_from": "2026-01-01", "effective_to": "2026-12-31",
        "maturity": "calculation", "requirements": [
            {"fact_key": "calc.tax_base", "answer_type": "decimal", "evidence_types": ["vat_return"], "priority": 100, "blocking": True, "critical": True},
            {"fact_key": "calc.rate", "answer_type": "decimal", "evidence_types": ["rule_source"], "priority": 100, "blocking": True, "critical": True},
        ], "local_parameter_keys": [],
        "evaluator_id": "generic.calculation.base_minus_deductions_times_rate.v1",
        "sources": [{"title": "Official VAT source", "url": "https://example.test/vat", "published_on": "2026-01-01", "issuer": "Official issuer", "document_number": "DOC-2026", "article_refs": ["Article 1"], "verified_on": "2026-01-01"}],
        "rule_version": "2026.1", "fact_catalog_version": "zh-CN.v1", "applicability_fact_keys": [],
        "calculation": {"formula_id": "generic.base_minus_deductions_times_rate.v1", "tax_base_fact_key": "calc.tax_base", "rate_fact_key": "calc.rate", "deductions_fact_key": None},
    }), encoding="utf-8")
    pack_path = tmp_path / "linked-pack.json"
    try:
        os.symlink(target, pack_path)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck", "tax-health-check", "--request", str(request_path),
        "--output-dir", str(tmp_path / "output"), "--pack", str(pack_path),
    ])

    assert cli.main() == 4
    assert json.loads(capsys.readouterr().out)["error_code"] == "invalid_tax_rule_pack"
