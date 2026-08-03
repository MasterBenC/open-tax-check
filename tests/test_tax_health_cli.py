import json
import hashlib
import os
from pathlib import Path
import shutil
from zipfile import ZipFile
import pytest

from open_tax_check import cli
from open_tax_check.evidence_bundle import EvidenceBundleError


def request_payload():
    return {
        "period": {"start": "2026-01-01", "end": "2026-12-31"},
        "jurisdiction": "CN",
        "facts": [],
        "input_hashes": [],
    }


def write_request(tmp_path, *, facts=()):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps({
        **request_payload(),
        "facts": list(facts),
    }), encoding="utf-8")
    return request_path


def invoke_tax_health_check(monkeypatch, request_path, output_dir, *extra_args):
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck",
        "tax-health-check",
        "--request", str(request_path),
        "--output-dir", str(output_dir),
        *extra_args,
    ])


def write_bound_evidence_case(tmp_path):
    evidence_root = tmp_path / "customer-evidence"
    evidence_root.mkdir()
    source = evidence_root / "customer-document.txt"
    source.write_text("fictional verified evidence\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest_path = tmp_path / "evidence-manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": "tax-evidence-manifest/v1",
        "items": [{
            "source_id": "customer-document",
            "relative_path": source.name,
            "sha256": digest,
            "media_type": "text/plain",
        }],
    }), encoding="utf-8")
    request_path = write_request(tmp_path, facts=({
        "key": "case.confirmed_document",
        "value": "provided",
        "state": "confirmed",
        "evidence_refs": ["customer-document"],
        "confirmed_by": "customer",
        "confirmed_at": "2026-01-02T00:00:00+08:00",
    },))
    return evidence_root, manifest_path, request_path


def write_verified_sources(tmp_path, *source_ids):
    evidence_root = tmp_path / "verified-sources"
    evidence_root.mkdir()
    items = []
    for index, source_id in enumerate(source_ids):
        source = evidence_root / f"source-{index}.txt"
        source.write_text(f"fictional evidence for {source_id}\n", encoding="utf-8")
        items.append({
            "source_id": source_id,
            "relative_path": source.name,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "media_type": "text/plain",
        })
    manifest_path = tmp_path / "verified-sources-manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": "tax-evidence-manifest/v1",
        "items": items,
    }), encoding="utf-8")
    return evidence_root, manifest_path


def test_tax_health_check_cli_keeps_null_bundle_fields_when_not_enabled(monkeypatch, tmp_path, capsys):
    request_path = write_request(tmp_path)
    invoke_tax_health_check(monkeypatch, request_path, tmp_path / "output")

    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["evidence_bundle_status"] is None
    assert result["evidence_bundle_path"] is None
    assert result["evidence_bundle_sha256"] is None
    assert result["includes_evidence_files"] is False


def test_cli_rejects_confirmed_fact_without_verified_manifest(monkeypatch, tmp_path, capsys):
    request_path = write_request(tmp_path, facts=({
        "key": "enterprise_income_tax.taxable_income",
        "value": "4733877.28",
        "state": "confirmed",
        "evidence_refs": ["journal-2025"],
        "confirmed_by": "customer",
        "confirmed_at": "2026-08-02T16:50:00+08:00",
    },))
    invoke_tax_health_check(monkeypatch, request_path, tmp_path / "output")

    assert cli.main() == 4
    assert json.loads(capsys.readouterr().out)["error_code"] == (
        "unverified_confirmation_evidence"
    )


def test_tax_health_check_cli_uses_shared_delivery_payload(monkeypatch, tmp_path, capsys):
    request_path = write_request(tmp_path)
    invoke_tax_health_check(monkeypatch, request_path, tmp_path / "output")
    sentinel = {
        "evidence_bundle_status": "shared-helper",
        "evidence_bundle_path": "sentinel-path",
        "evidence_bundle_sha256": "sentinel-sha256",
        "includes_evidence_files": True,
    }
    monkeypatch.setattr(cli, "evidence_bundle_delivery_payload", lambda bundle: sentinel)

    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert {key: result[key] for key in sentinel} == sentinel


@pytest.mark.parametrize("extra_args", [
    ["--evidence-manifest", "manifest.json"],
    ["--evidence-root", "evidence"],
    ["--include-evidence-files"],
])
def test_tax_health_check_cli_requires_manifest_and_root_together(monkeypatch, tmp_path, capsys, extra_args):
    request_path = write_request(tmp_path)
    invoke_tax_health_check(monkeypatch, request_path, tmp_path / "output", *extra_args)

    assert cli.main() == 4
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "error_code": "invalid_evidence_manifest",
    }


def test_tax_health_check_cli_builds_verified_bundle_without_originals_by_default(monkeypatch, tmp_path, capsys):
    evidence_root, manifest_path, request_path = write_bound_evidence_case(tmp_path)
    output_dir = tmp_path / "output"
    invoke_tax_health_check(
        monkeypatch,
        request_path,
        output_dir,
        "--evidence-manifest", str(manifest_path),
        "--evidence-root", str(evidence_root),
        "--include-docx",
    )

    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    bundle_path = Path(result["evidence_bundle_path"])
    assert result["evidence_bundle_status"] == "verified"
    assert result["includes_evidence_files"] is False
    assert hashlib.sha256(bundle_path.read_bytes()).hexdigest() == result["evidence_bundle_sha256"]
    with ZipFile(bundle_path) as archive:
        assert "\u7a0e\u52a1\u5065\u5eb7\u68c0\u67e5\u62a5\u544a.md" in archive.namelist()
        assert "\u7a0e\u52a1\u5065\u5eb7\u68c0\u67e5\u62a5\u544a.docx" in archive.namelist()
        assert "tax-health-snapshot.json" in archive.namelist()
        assert not any(name.startswith("evidence/") for name in archive.namelist())


def test_real_fictional_case_builds_a_reopenable_bundle_with_verified_original(
    monkeypatch,
    tmp_path,
    capsys,
):
    fixture = Path(
        "tests/fixtures/evidence_bundle/confirmed-vat-summary.txt"
    ).resolve()
    evidence_root = tmp_path / "customer-evidence"
    evidence_root.mkdir()
    copied = evidence_root / fixture.name
    shutil.copyfile(fixture, copied)
    digest = hashlib.sha256(copied.read_bytes()).hexdigest()
    manifest_path = tmp_path / "evidence-manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": "tax-evidence-manifest/v1",
        "items": [{
            "source_id": "vat-return-2026-01",
            "relative_path": fixture.name,
            "sha256": digest,
            "media_type": "text/plain",
        }],
    }), encoding="utf-8")
    request_path = write_request(tmp_path, facts=({
        "key": "case.confirmed_vat_summary",
        "value": "provided",
        "state": "confirmed",
        "evidence_refs": ["vat-return-2026-01"],
        "confirmed_by": "customer",
        "confirmed_at": "2026-02-01T00:00:00+08:00",
    },))
    output_dir = tmp_path / "output"
    invoke_tax_health_check(
        monkeypatch,
        request_path,
        output_dir,
        "--evidence-manifest", str(manifest_path),
        "--evidence-root", str(evidence_root),
        "--include-evidence-files",
        "--include-docx",
    )

    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    bundle_path = Path(result["evidence_bundle_path"])
    assert (
        hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        == result["evidence_bundle_sha256"]
    )
    with ZipFile(bundle_path) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("bundle-manifest.json"))
        evidence_item = manifest["evidence_items"][0]
        assert evidence_item["source_id"] == "vat-return-2026-01"
        assert evidence_item["sha256"] == digest
        assert (
            hashlib.sha256(archive.read(evidence_item["bundle_path"])).hexdigest()
            == digest
        )
        assert any(
            item["kind"] == "markdown_report"
            for item in manifest["artifacts"]
        )
        assert any(
            item["kind"] == "docx_report"
            for item in manifest["artifacts"]
        )


@pytest.mark.parametrize("expected_code", [
    "invalid_evidence_manifest",
    "unsafe_evidence_path",
    "evidence_verification_failed",
    "unverified_evidence_reference",
    "evidence_bundle_size_limit_exceeded",
])
def test_tax_health_check_cli_returns_stable_non_sensitive_evidence_errors(
    monkeypatch, tmp_path, capsys, expected_code,
):
    evidence_root, manifest_path, request_path = write_bound_evidence_case(tmp_path)
    invoke_tax_health_check(
        monkeypatch,
        request_path,
        tmp_path / "output",
        "--evidence-manifest", str(manifest_path),
        "--evidence-root", str(evidence_root),
    )
    patched_name = (
        "parse_evidence_manifest"
        if expected_code == "invalid_evidence_manifest"
        else "verify_evidence_manifest"
        if expected_code in {
            "unsafe_evidence_path",
            "evidence_verification_failed",
            "evidence_bundle_size_limit_exceeded",
        }
        else "build_evidence_bundle"
    )
    monkeypatch.setattr(
        cli,
        patched_name,
        lambda *args, **kwargs: (_ for _ in ()).throw(EvidenceBundleError(expected_code)),
    )

    assert cli.main() == 4
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "error_code": expected_code,
    }


def test_tax_health_check_cli_maps_bundle_write_failure_to_operational_error(monkeypatch, tmp_path, capsys):
    evidence_root, manifest_path, request_path = write_bound_evidence_case(tmp_path)
    invoke_tax_health_check(
        monkeypatch,
        request_path,
        tmp_path / "output",
        "--evidence-manifest", str(manifest_path),
        "--evidence-root", str(evidence_root),
    )
    monkeypatch.setattr(
        cli,
        "build_evidence_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            EvidenceBundleError("evidence_bundle_write_failed")
        ),
    )

    assert cli.main() == 3
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "error_code": "evidence_bundle_write_failed",
    }


def test_tax_health_check_cli_does_not_echo_unknown_manifest_error_codes(monkeypatch, tmp_path, capsys):
    evidence_root, manifest_path, request_path = write_bound_evidence_case(tmp_path)
    invoke_tax_health_check(
        monkeypatch,
        request_path,
        tmp_path / "output",
        "--evidence-manifest", str(manifest_path),
        "--evidence-root", str(evidence_root),
    )
    monkeypatch.setattr(
        cli,
        "parse_evidence_manifest",
        lambda payload: (_ for _ in ()).throw(EvidenceBundleError("customer-secret")),
    )

    assert cli.main() == 4
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "error_code": "invalid_evidence_manifest",
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
    evidence_root, manifest_path = write_verified_sources(
        tmp_path, "vat-return", "vat-rule"
    )
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck", "tax-health-check", "--request", str(request_path), "--output-dir", str(tmp_path / "output"), "--pack", str(pack_path),
        "--evidence-manifest", str(manifest_path), "--evidence-root", str(evidence_root),
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
                "key": "stamp_tax.tax_base",
                "value": "1000.00",
                "state": "confirmed",
                "evidence_refs": ["sale-contract"],
                "confirmed_by": "customer",
                "confirmed_at": "2026-01-02T00:00:00+08:00",
            },
        ],
    }), encoding="utf-8")
    evidence_root, manifest_path = write_verified_sources(
        tmp_path, "contract-register", "sale-contract"
    )
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck", "tax-health-check", "--request", str(request_path),
        "--output-dir", str(output_dir),
        "--evidence-manifest", str(manifest_path), "--evidence-root", str(evidence_root),
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


def test_tax_health_check_cli_calculates_deed_tax_only_with_confirmed_local_rate(monkeypatch, tmp_path, capsys):
    request_path = tmp_path / "request.json"
    output_dir = tmp_path / "output"
    timestamp = "2026-01-02T00:00:00+08:00"
    request_path.write_text(json.dumps({
        **request_payload(),
        "facts": [
            {"key": "activity.acquired_land_or_building_rights", "value": True, "state": "confirmed", "evidence_refs": ["title-transfer"], "confirmed_by": "customer", "confirmed_at": timestamp},
            {"key": "policy.deed_tax_local_rule", "value": "当地现行契税具体适用税率文件", "state": "confirmed", "evidence_refs": ["provincial-policy"], "confirmed_by": "customer", "confirmed_at": timestamp},
            {"key": "deed_tax.applicable_rate", "value": "0.03", "state": "confirmed", "evidence_refs": ["provincial-policy"], "confirmed_by": "customer", "confirmed_at": timestamp},
            {"key": "deed_tax.tax_base", "value": "100000.00", "state": "confirmed", "evidence_refs": ["property-contract"], "confirmed_by": "customer", "confirmed_at": timestamp},
        ],
    }), encoding="utf-8")
    evidence_root, manifest_path = write_verified_sources(
        tmp_path, "title-transfer", "provincial-policy", "property-contract"
    )
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck", "tax-health-check", "--request", str(request_path),
        "--output-dir", str(output_dir),
        "--evidence-manifest", str(manifest_path), "--evidence-root", str(evidence_root),
    ])

    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    snapshot = json.loads((output_dir / "tax-health-snapshot.json").read_text(encoding="utf-8"))
    deed_tax = next(item for item in snapshot["results"] if item["tax_id"] == "deed_tax")
    assert deed_tax["calculation"]["amount"] == "3000.00"
    assert deed_tax["calculation"]["rate"] == "0.03"
    report = (output_dir / "\u7a0e\u52a1\u5065\u5eb7\u68c0\u67e5\u62a5\u544a.md").read_text(encoding="utf-8")
    assert "\u9002\u7528\u7a0e\u7387: 3%" in report


def test_tax_health_check_cli_calculates_three_vat_consumption_tax_addons(monkeypatch, tmp_path, capsys):
    request_path = tmp_path / "request.json"
    output_dir = tmp_path / "output"
    timestamp = "2026-01-02T00:00:00+08:00"
    request_path.write_text(json.dumps({
        **request_payload(),
        "facts": [
            {"key": "tax.has_vat_or_consumption_tax_payable", "value": True, "state": "confirmed", "evidence_refs": ["tax-return"], "confirmed_by": "customer", "confirmed_at": timestamp},
            {"key": "policy.local_education_surcharge_rule", "value": "当地现行地方教育附加政策", "state": "confirmed", "evidence_refs": ["local-policy"], "confirmed_by": "customer", "confirmed_at": timestamp},
            {"key": "urban_maintenance_construction_tax.rate_path", "value": "urban_area_standard", "state": "confirmed", "evidence_refs": ["registration"], "confirmed_by": "customer", "confirmed_at": timestamp},
            {"key": "education_surcharge.rate_path", "value": "standard", "state": "confirmed", "evidence_refs": ["qualification"], "confirmed_by": "customer", "confirmed_at": timestamp},
            {"key": "local_education_surcharge.rate_path", "value": "standard", "state": "confirmed", "evidence_refs": ["qualification"], "confirmed_by": "customer", "confirmed_at": timestamp},
            {"key": "surcharge.vat_consumption_tax_base", "value": "10000.00", "state": "confirmed", "evidence_refs": ["tax-return"], "confirmed_by": "customer", "confirmed_at": timestamp},
        ],
    }), encoding="utf-8")
    evidence_root, manifest_path = write_verified_sources(
        tmp_path, "tax-return", "local-policy", "registration", "qualification"
    )
    monkeypatch.setattr(cli.sys, "argv", [
        "opentaxcheck", "tax-health-check", "--request", str(request_path),
        "--output-dir", str(output_dir),
        "--evidence-manifest", str(manifest_path), "--evidence-root", str(evidence_root),
    ])

    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    snapshot = json.loads((output_dir / "tax-health-snapshot.json").read_text(encoding="utf-8"))
    results = {item["tax_id"]: item for item in snapshot["results"]}
    assert results["urban_maintenance_construction_tax"]["calculation"]["amount"] == "700.00"
    assert results["education_surcharge"]["calculation"]["amount"] == "300.00"
    assert results["local_education_surcharge"]["calculation"]["amount"] == "200.00"


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
