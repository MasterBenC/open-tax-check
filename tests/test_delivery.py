import json
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from open_tax_check.case import init_case
from open_tax_check.case_orchestration import (
    apply_case_confirmations,
    build_case_customer_tasks,
    run_case_self_check,
)
from open_tax_check import cli
from open_tax_check.delivery import DeliveryError, build_case_delivery, verified_delivery_artifacts
from open_tax_check.tax_models import (
    Confirmation,
    DateRange,
    FactState,
    TaxCheckRequest,
    TaxFact,
)


def _request():
    from datetime import date

    return TaxCheckRequest(
        DateRange(date(2026, 1, 1), date(2026, 12, 31)),
        "CN",
        facts=(TaxFact(
            "activity.has_vat_taxable_transaction",
            True,
            FactState.OBSERVED,
            ("source-001",),
        ),),
        input_hashes=("a" * 64,),
    )


def _case(tmp_path: Path) -> Path:
    case = tmp_path / "case"
    init_case(case, "虚构科技有限公司", "2026-01-01", "2026-12-31")
    run_case_self_check(case, _request(), occurred_at="2026-07-30T10:00:00+08:00")
    return case


def test_case_delivery_publishes_one_revision_for_all_artifacts(tmp_path: Path):
    case = _case(tmp_path)

    result = build_case_delivery(case)

    assert result["schema_version"] == "tax-delivery/v1"
    assert result["revision"] == 1
    assert result["status"] == "verified"
    manifest_path = case / result["manifest_path"]
    pointer_path = case / "work" / "delivery.json"
    assert manifest_path.is_file()
    assert pointer_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["revision"] == 1
    assert manifest["snapshot_revision"] == 1
    assert {item["kind"] for item in manifest["artifacts"]} >= {
        "markdown_report", "docx_report", "workpaper", "machine_snapshot", "evidence_bundle",
    }
    for item in manifest["artifacts"]:
        artifact = case / result["revision_path"] / item["relative_path"]
        assert artifact.is_file()
        assert item["sha256"]
    bundle = case / result["revision_path"] / "evidence-bundle.zip"
    with ZipFile(bundle) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read("bundle-manifest.json"))["snapshot_revision"] == 1


def test_handoff_lists_only_existing_hashed_artifacts(tmp_path: Path):
    case = _case(tmp_path)
    build_case_delivery(case)

    handoff_path = case / "output" / "税务健康检查交付说明.json"
    payload = json.loads(handoff_path.read_text(encoding="utf-8"))

    assert payload["artifacts"] == list(verified_delivery_artifacts(case))
    assert all((case / item["path"]).is_file() for item in payload["artifacts"])
    assert "request.json" not in {Path(item["path"]).name for item in payload["artifacts"]}
    assert all(item["sha256"] for item in payload["artifacts"])


def test_case_delivery_bundles_structured_contracts_reconciliations_and_review(tmp_path: Path):
    case = _case(tmp_path)
    structured = case / "work/structured-documents"
    structured.mkdir()
    (structured / "bs.financial-statement.json").write_text(
        json.dumps({"schema_version": "financial-statement/v1"}),
        encoding="utf-8",
    )
    (case / "work/reconciliation.json").write_text(
        json.dumps({"trial_balance": {"passed": True}}),
        encoding="utf-8",
    )
    (case / "work/structured-reconciliation.json").write_text(
        json.dumps({"passed": True, "checks": []}),
        encoding="utf-8",
    )
    (case / "work/coverage_matrix.json").write_text(
        json.dumps({"items": []}),
        encoding="utf-8",
    )
    (case / "reviews").mkdir()
    (case / "reviews/mapping_review.json").write_text(
        json.dumps({"confirmations": [{"reviewer": "host:test"}]}),
        encoding="utf-8",
    )

    result = build_case_delivery(case)

    with ZipFile(case / result["revision_path"] / "evidence-bundle.zip") as archive:
        assert {
            "case-audit/structured-documents/bs.financial-statement.json",
            "case-audit/reconciliation.json",
            "case-audit/structured-reconciliation.json",
            "case-audit/coverage-matrix.json",
            "case-audit/reviews/mapping-review.json",
        }.issubset(archive.namelist())


def test_case_delivery_is_idempotent_for_same_snapshot(tmp_path: Path):
    case = _case(tmp_path)

    first = build_case_delivery(case)
    first_bytes = (case / first["manifest_path"]).read_bytes()
    second = build_case_delivery(case)

    assert second == first
    assert (case / second["manifest_path"]).read_bytes() == first_bytes


def test_case_delivery_rejects_tampering_of_every_published_artifact(tmp_path: Path):
    case = _case(tmp_path)
    first = build_case_delivery(case)
    manifest = json.loads((case / first["manifest_path"]).read_text(encoding="utf-8"))

    for item in manifest["artifacts"]:
        artifact = case / first["revision_path"] / item["relative_path"]
        original = artifact.read_bytes()
        artifact.write_bytes(original + b"tampered")
        try:
            with pytest.raises(ValueError, match="^delivery_artifact_invalid$"):
                build_case_delivery(case)
        finally:
            artifact.write_bytes(original)


def test_case_delivery_rejects_unsafe_artifact_relative_path_before_reuse(tmp_path: Path):
    case = _case(tmp_path)
    first = build_case_delivery(case)
    manifest_path = case / first["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["relative_path"] = "../../state.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="^delivery_artifact_invalid$"):
        build_case_delivery(case)


def test_case_delivery_failure_does_not_publish_pointer_or_partial_revision(tmp_path: Path):
    case = _case(tmp_path)
    with patch(
        "open_tax_check.delivery.write_tax_health_workpaper",
        side_effect=ValueError("workpaper_write_failed"),
    ):
        with pytest.raises(ValueError, match="^delivery_write_failed$"):
            build_case_delivery(case)

    assert not (case / "work" / "delivery.json").exists()
    revisions = list((case / "work" / "deliveries").glob("revision-*"))
    assert revisions == []


def test_case_delivery_cli_returns_revision_payload(tmp_path: Path, monkeypatch, capsys):
    case = _case(tmp_path)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["opentaxcheck", "case-delivery", "--case", str(case)],
    )

    assert cli.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "tax-delivery/v1"
    assert payload["revision"] == 1


def test_case_delivery_includes_pdf_in_bundle_when_renderer_succeeds(tmp_path: Path):
    case = _case(tmp_path)

    def fake_pdf(snapshot, path):
        path.write_bytes(b"%PDF-1.7\n")
        return path

    with patch("open_tax_check.delivery.write_tax_health_report_pdf", side_effect=fake_pdf):
        result = build_case_delivery(case, include_pdf=True)

    assert result["pdf_status"] == "verified"
    manifest = json.loads((case / result["manifest_path"]).read_text(encoding="utf-8"))
    assert any(item["kind"] == "pdf_report" for item in manifest["artifacts"])
    with ZipFile(case / result["revision_path"] / "evidence-bundle.zip") as archive:
        assert "report.pdf" in archive.namelist()


def test_case_delivery_rejects_pointer_manifest_path_escape(tmp_path: Path):
    case = _case(tmp_path)
    pointer = case / "work" / "delivery.json"
    pointer.write_text(json.dumps({"manifest_path": "../../outside.json"}), encoding="utf-8")

    with pytest.raises(ValueError, match="^delivery_pointer_invalid$"):
        build_case_delivery(case)


def test_case_delivery_rejects_symlinked_activity_pointer(tmp_path: Path):
    case = _case(tmp_path)
    first = build_case_delivery(case)
    pointer = case / "work" / "delivery.json"
    outside = tmp_path / "outside-delivery.json"
    outside.write_text(
        json.dumps(json.loads(pointer.read_text(encoding="utf-8"))),
        encoding="utf-8",
    )
    pointer.unlink()
    try:
        pointer.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(ValueError, match="^delivery_pointer_invalid$"):
        build_case_delivery(case)

    assert first["revision"] == 1


def test_case_delivery_rejects_changed_delivery_options_for_same_snapshot(tmp_path: Path):
    case = _case(tmp_path)
    first = build_case_delivery(case)

    with pytest.raises(ValueError, match="^delivery_revision_conflict$"):
        build_case_delivery(case, include_evidence_files=True)

    assert json.loads((case / first["manifest_path"]).read_text(encoding="utf-8"))["revision"] == 1


def test_case_delivery_accepts_host_attested_customer_confirmation(tmp_path: Path):
    case = _case(tmp_path)
    task = build_case_customer_tasks(case)[0]
    value = True if task.answer_type == "boolean" else "confirmed"
    apply_case_confirmations(
        case,
        (
            Confirmation(
                task.question_id,
                task.fact_key,
                value,
                "customer",
                "2026-07-30T10:05:00+08:00",
                (f"customer-confirmation:{task.question_id}",),
            ),
        ),
    )

    result = build_case_delivery(case)

    assert result["status"] == "verified"


def test_case_delivery_publishes_new_revision_after_customer_confirmation(tmp_path: Path):
    case = _case(tmp_path)
    first = build_case_delivery(case)
    task = build_case_customer_tasks(case)[0]
    value = True if task.answer_type == "boolean" else "confirmed"
    apply_case_confirmations(
        case,
        (
            Confirmation(
                task.question_id,
                task.fact_key,
                value,
                "customer",
                "2026-07-30T10:05:00+08:00",
                (f"customer-confirmation:{task.question_id}",),
            ),
        ),
    )

    second = build_case_delivery(case)

    assert first["revision"] == 1
    assert second["revision"] == 2
    assert (case / first["revision_path"]).is_dir()
    assert (case / second["revision_path"]).is_dir()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "tax-delivery/forged"),
        ("revision", 999),
        ("revision_path", "../../outside"),
        ("status", "forged"),
        ("artifacts", []),
        ("delivery_options", {"include_pdf": True, "include_evidence_files": True}),
    ],
)
def test_case_delivery_rejects_tampered_pointer_fields(tmp_path: Path, field: str, value):
    case = _case(tmp_path)
    build_case_delivery(case)
    pointer_path = case / "work" / "delivery.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer[field] = value
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(ValueError, match="^delivery_pointer_invalid$"):
        build_case_delivery(case)


def test_case_delivery_rolls_back_new_revision_when_pointer_write_fails(tmp_path: Path):
    case = _case(tmp_path)
    from open_tax_check import delivery

    original = delivery._atomic_json

    def fail_pointer(path, payload):
        if path.name == "delivery.json":
            raise DeliveryError("delivery_pointer_write_failed")
        return original(path, payload)

    with patch("open_tax_check.delivery._atomic_json", side_effect=fail_pointer):
        with pytest.raises(ValueError, match="^delivery_pointer_write_failed$"):
            build_case_delivery(case)

    assert not (case / "work" / "delivery.json").exists()
    assert not (case / "work" / "deliveries" / "revision-0001").exists()

    retry = build_case_delivery(case)
    assert retry["revision"] == 1


def test_case_delivery_rejects_stale_existing_revision_for_changed_snapshot(tmp_path: Path):
    case = _case(tmp_path)
    first = build_case_delivery(case)
    (case / "work" / "delivery.json").unlink()
    private_snapshot = case / "work" / "tax-health-snapshot.private.json"
    payload = json.loads(private_snapshot.read_text(encoding="utf-8"))
    payload["request"]["input_hashes"] = ["b" * 64]
    private_snapshot.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="^delivery_revision_conflict$"):
        build_case_delivery(case)

    assert first["revision"] == 1


def test_case_delivery_rejects_symlinked_delivery_root(tmp_path: Path):
    case = _case(tmp_path)
    outside = tmp_path / "outside-deliveries"
    outside.mkdir()
    delivery_root = case / "work" / "deliveries"
    try:
        delivery_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(ValueError, match="^delivery_pointer_invalid$"):
        build_case_delivery(case)

    assert list(outside.iterdir()) == []
