import json
import re
import hashlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from open_tax_check.case import init_case
from open_tax_check.case_orchestration import (
    apply_case_confirmations,
    build_case_customer_handoff,
    build_case_customer_tasks,
    run_case_self_check,
)
from open_tax_check import cli
from open_tax_check.delivery import DeliveryError, build_case_delivery, verified_delivery_artifacts
from open_tax_check.scenario_map import scenario_coverage_payload
from open_tax_check.tax_models import (
    Confirmation,
    DateRange,
    FactState,
    TaxCheckRequest,
    TaxFact,
)
from open_tax_check.tax_cross_checks import tax_cross_check_payload
from test_tax_cross_checks import completed_run, difference_run


@pytest.fixture(autouse=True)
def _fast_report_renderers(monkeypatch):
    from open_tax_check import delivery

    def write_html(view, path):
        risks = "".join(item.title_zh for item in view.risks)
        path.write_text(
            f"<!doctype html><title>税务健康检查报告</title>{view.executive_summary.entity_name}{risks}",
            encoding="utf-8",
        )
        return path

    def write_pdf(_view, path):
        path.write_bytes(b"%PDF-1.7\n% fictional delivery test\n")
        return path

    monkeypatch.setattr(delivery, "write_report_html", write_html, raising=False)
    monkeypatch.setattr(delivery, "write_report_pdf", write_pdf, raising=False)
    monkeypatch.setattr(
        "open_tax_check.evidence_bundle.importlib.metadata.version",
        lambda _name: "test",
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
    assert result["report_schema_version"] == "tax-health-report/v3"
    assert result["revision"] == 1
    assert result["status"] == "verified"
    manifest_path = case / result["manifest_path"]
    pointer_path = case / "work" / "delivery.json"
    assert manifest_path.is_file()
    assert pointer_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["revision"] == 1
    assert manifest["snapshot_revision"] == 1
    assert manifest["report_schema_version"] == "tax-health-report/v3"
    assert {item["kind"] for item in manifest["artifacts"]} >= {
        "html_report", "pdf_report", "workpaper", "machine_snapshot", "evidence_bundle",
    }
    assert "docx_report" not in {item["kind"] for item in manifest["artifacts"]}
    assert "markdown_report" not in {item["kind"] for item in manifest["artifacts"]}
    for item in manifest["artifacts"]:
        assert item["snapshot_revision"] == result["revision"]
        assert item["report_schema_version"] == "tax-health-report/v3"
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        artifact = case / result["revision_path"] / item["relative_path"]
        assert artifact.is_file()
        assert item["sha256"]
    bundle = case / result["revision_path"] / "evidence-bundle.zip"
    with ZipFile(bundle) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read("bundle-manifest.json"))["snapshot_revision"] == 1


def test_case_delivery_renders_bound_entity_and_approved_findings(tmp_path: Path):
    case = _case(tmp_path)
    finding_title = "收入勾稽差异"
    (case / "output" / "findings.json").write_text(
        json.dumps([
            {
                "title_zh": finding_title,
                "status": "suspected",
                "review_status": "reviewed",
                "report_status": "approved",
                "source_kind": "deterministic_discrepancy",
                "observed_zh": "账簿收入与申报口径存在差异。",
                "why_it_matters_zh": "可能影响相关申报口径。",
                "cannot_conclude_zh": "尚不能据此认定少缴税款。",
                "required_materials_zh": ["收入明细", "申报表"],
                "next_action_zh": "逐月核对差异来源。",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    result = build_case_delivery(case)
    revision_dir = case / result["revision_path"]
    html = (revision_dir / "report.html").read_text(encoding="utf-8")
    assert "虚构科技有限公司" in html
    assert finding_title in html
    assert (revision_dir / "税务健康检查报告.pdf").is_file()


def test_case_delivery_renders_persisted_cross_check_difference(tmp_path: Path):
    case = _case(tmp_path)
    manifest_payload = {"sources": []}
    manifest_bytes = json.dumps(manifest_payload).encode("utf-8")
    (case / "work/input_manifest.json").write_bytes(manifest_bytes)
    run = replace(
        difference_run(), source_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest()
    )
    (case / "work/tax-cross-checks.json").write_text(
        json.dumps(tax_cross_check_payload(run), ensure_ascii=False), encoding="utf-8"
    )

    result = build_case_delivery(case)
    html = (case / result["revision_path"] / "report.html").read_text(encoding="utf-8")

    assert run.results[1].title_zh in html


def test_case_delivery_uses_one_scenario_coverage_for_report_and_machine_snapshot(
    tmp_path: Path,
    monkeypatch,
):
    case = _case(tmp_path)
    manifest_payload = {"sources": []}
    manifest_bytes = json.dumps(manifest_payload).encode("utf-8")
    (case / "work/input_manifest.json").write_bytes(manifest_bytes)
    run = replace(
        difference_run(),
        source_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
    (case / "work/tax-cross-checks.json").write_text(
        json.dumps(tax_cross_check_payload(run), ensure_ascii=False),
        encoding="utf-8",
    )

    def write_html(view, path):
        path.write_text(
            "<!doctype html>\n" + json.dumps(
                scenario_coverage_payload(view.scenario_coverage),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def write_pdf(view, path):
        path.write_bytes(
            b"%PDF-1.7\n% fictional delivery test\n"
            + json.dumps(
                scenario_coverage_payload(view.scenario_coverage),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        return path

    monkeypatch.setattr("open_tax_check.delivery.write_report_html", write_html)
    monkeypatch.setattr("open_tax_check.delivery.write_report_pdf", write_pdf)
    from open_tax_check import report_view

    coverage_builds = []
    real_build_scenario_coverage = report_view.build_scenario_coverage

    def count_coverage_builds(**kwargs):
        coverage_builds.append(kwargs)
        return real_build_scenario_coverage(**kwargs)

    monkeypatch.setattr(
        "open_tax_check.report_view.build_scenario_coverage",
        count_coverage_builds,
    )

    result = build_case_delivery(case)
    revision_dir = case / result["revision_path"]
    html_coverage = json.loads(
        (revision_dir / "report.html").read_text(encoding="utf-8").split("\n", 1)[1]
    )
    pdf_coverage = json.loads(
        (revision_dir / "税务健康检查报告.pdf")
        .read_bytes()
        .split(b"\n", 2)[2]
        .decode("utf-8")
    )
    machine = json.loads(
        (revision_dir / "tax-health-snapshot.json").read_text(encoding="utf-8")
    )

    assert html_coverage == pdf_coverage == machine["scenario_coverage"]
    assert html_coverage["revision"] == machine["revision"] == result["revision"]
    assert len(coverage_builds) == 1


def test_case_delivery_uses_only_human_confirmed_scenario_facts(tmp_path: Path):
    case = _case(tmp_path)
    (case / "work/scenario-facts.json").write_text(
        json.dumps({
            "schema_version": "tax-scenario-facts/v1",
            "facts": {
                "revenue_recognition": {
                    "value": False,
                    "confirmed_by": "customer",
                },
                "turnover_scope": {
                    "value": False,
                    "confirmed_by": "human:复核人",
                },
                "tax_return_sales": {
                    "value": False,
                    "confirmed_by": "customer",
                },
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    result = build_case_delivery(case)
    machine = json.loads(
        (
            case
            / result["revision_path"]
            / "tax-health-snapshot.json"
        ).read_text(encoding="utf-8")
    )
    revenue = next(
        item
        for item in machine["scenario_coverage"]["items"]
        if item["scenario_id"] == "revenue_and_turnover"
    )

    assert revenue["not_applicable_count"] == 4
    assert revenue["not_performed_count"] == 0


@pytest.mark.parametrize(
    ("material_source_id", "bind_costs", "received_unparsed_count"),
    [
        ("unique-cit-material", True, 1),
        ("unmatched-material", False, 0),
    ],
)
def test_case_delivery_binds_material_only_by_unique_cross_check_source_ref(
    tmp_path: Path,
    material_source_id: str,
    bind_costs: bool,
    received_unparsed_count: int,
):
    case = _case(tmp_path)
    manifest_payload = {
        "sources": [{
            "source_id": material_source_id,
            "status": "present_unparsed",
            "filename": "企业所得税资料.pdf",
        }],
    }
    manifest_bytes = json.dumps(manifest_payload, ensure_ascii=False).encode("utf-8")
    (case / "work/input_manifest.json").write_bytes(manifest_bytes)
    run = difference_run()
    if bind_costs:
        costs_result = replace(
            run.results[-1],
            source_refs=(
                replace(
                    run.results[-1].source_refs[0],
                    source_id=material_source_id,
                    locator="cit-business-entertainment",
                ),
            ),
        )
        run = replace(run, results=(*run.results[:-1], costs_result))
    run = replace(
        run,
        source_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
    (case / "work/tax-cross-checks.json").write_text(
        json.dumps(tax_cross_check_payload(run), ensure_ascii=False),
        encoding="utf-8",
    )

    result = build_case_delivery(case)
    machine = json.loads(
        (
            case
            / result["revision_path"]
            / "tax-health-snapshot.json"
        ).read_text(encoding="utf-8")
    )
    costs = next(
        item
        for item in machine["scenario_coverage"]["items"]
        if item["scenario_id"] == "costs_and_expenses"
    )

    assert costs["received_unparsed_count"] == received_unparsed_count
    assert costs["executed_clear_count"] == 1 - received_unparsed_count


def test_case_delivery_marks_confirmed_reconciled_workbook_as_reconciled(
    tmp_path: Path,
):
    case = _case(tmp_path)
    manifest_payload = {
        "sources": [{
            "source_id": "source-1",
            "status": "accepted",
            "filename": "journal.xlsx",
        }],
    }
    manifest_bytes = json.dumps(manifest_payload).encode("utf-8")
    (case / "work/input_manifest.json").write_bytes(manifest_bytes)
    (case / "work/reconciliation.json").write_text(
        json.dumps({
            "trial_balance": {"passed": True},
            "journal_to_trial_balance": {"passed": True},
        }),
        encoding="utf-8",
    )
    (case / "reviews").mkdir(exist_ok=True)
    (case / "reviews/mapping_review.json").write_text(
        json.dumps({
            "confirmations": [{
                "role": "journal",
                "source_id": "source-1",
                "reviewer": "host:test",
            }],
        }),
        encoding="utf-8",
    )
    run = replace(
        completed_run(),
        source_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
    (case / "work/tax-cross-checks.json").write_text(
        json.dumps(tax_cross_check_payload(run), ensure_ascii=False),
        encoding="utf-8",
    )

    result = build_case_delivery(case)
    machine = json.loads(
        (
            case
            / result["revision_path"]
            / "tax-health-snapshot.json"
        ).read_text(encoding="utf-8")
    )
    coverage = {
        item["scenario_id"]: item
        for item in machine["scenario_coverage"]["items"]
    }

    assert coverage["revenue_and_turnover"]["executed_clear_count"] == 4
    assert coverage["revenue_and_turnover"]["awaiting_evidence_count"] == 0
    assert coverage["costs_and_expenses"]["executed_clear_count"] == 1
    assert coverage["costs_and_expenses"]["awaiting_evidence_count"] == 0


def test_case_delivery_rejects_cross_check_bound_to_another_source_manifest(tmp_path: Path):
    case = _case(tmp_path)
    (case / "work/input_manifest.json").write_text(json.dumps({"sources": []}), encoding="utf-8")
    (case / "work/tax-cross-checks.json").write_text(
        json.dumps(tax_cross_check_payload(difference_run())), encoding="utf-8"
    )

    with pytest.raises(DeliveryError, match="delivery_case_audit_invalid"):
        build_case_delivery(case)


def test_handoff_lists_only_existing_hashed_artifacts(tmp_path: Path):
    case = _case(tmp_path)
    build_case_delivery(case)

    handoff_path = case / "output" / "税务健康检查交付说明.json"
    payload = json.loads(handoff_path.read_text(encoding="utf-8"))

    assert payload["artifacts"] == list(verified_delivery_artifacts(case))
    assert all((case / item["path"]).is_file() for item in payload["artifacts"])
    assert "request.json" not in {Path(item["path"]).name for item in payload["artifacts"]}
    assert all(item["sha256"] for item in payload["artifacts"])


def test_customer_handoff_does_not_expose_historical_v2_artifacts(tmp_path: Path):
    case = _case(tmp_path)
    result = build_case_delivery(case)
    manifest_path = case / result["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["report_schema_version"] = "tax-health-report/v2"
    for item in manifest["artifacts"]:
        item["report_schema_version"] = "tax-health-report/v2"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    pointer_path = case / "work/delivery.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["report_schema_version"] = "tax-health-report/v2"
    pointer["artifacts"] = manifest["artifacts"]
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    assert verified_delivery_artifacts(case) == ()


def test_handoff_rejects_tampered_delivery_revision_path(tmp_path: Path):
    case = _case(tmp_path)
    build_case_delivery(case)
    pointer_path = case / "work" / "delivery.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["revision_path"] = "output"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(ValueError, match="^delivery_pointer_invalid$"):
        build_case_customer_handoff(case)


def test_case_delivery_bundles_structured_contracts_reconciliations_and_review(tmp_path: Path):
    case = _case(tmp_path)
    manifest_bytes = json.dumps({"sources": []}).encode("utf-8")
    (case / "work/input_manifest.json").write_bytes(manifest_bytes)
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
    (case / "work/tax-cross-checks.json").write_text(
        json.dumps(tax_cross_check_payload(replace(
            completed_run(),
            source_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        ))),
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
            "case-audit/tax-cross-checks.json",
            "case-audit/coverage-matrix.json",
            "case-audit/reviews/mapping-review.json",
        }.issubset(archive.namelist())
    delivery_manifest = json.loads((case / result["manifest_path"]).read_text(encoding="utf-8"))
    assert delivery_manifest["tax_cross_checks"]["source_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert len(delivery_manifest["tax_cross_checks"]["sha256"]) == 64


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


def test_case_delivery_requires_pdf_and_includes_it_in_bundle(tmp_path: Path):
    case = _case(tmp_path)
    result = build_case_delivery(case)

    assert result["pdf_status"] == "verified"
    manifest = json.loads((case / result["manifest_path"]).read_text(encoding="utf-8"))
    assert any(item["kind"] == "pdf_report" for item in manifest["artifacts"])
    with ZipFile(case / result["revision_path"] / "evidence-bundle.zip") as archive:
        assert "税务健康检查报告.pdf" in archive.namelist()


def test_case_delivery_pdf_failure_does_not_publish_revision(tmp_path: Path, monkeypatch):
    case = _case(tmp_path)

    def fail_pdf(_view, _path):
        from open_tax_check.pdf_report import PdfExportUnavailable
        raise PdfExportUnavailable("pdf_renderer_unavailable")

    monkeypatch.setattr("open_tax_check.delivery.write_report_pdf", fail_pdf)

    with pytest.raises(ValueError, match="^pdf_renderer_unavailable$"):
        build_case_delivery(case)

    assert not (case / "work" / "delivery.json").exists()
    assert list((case / "work" / "deliveries").glob("revision-*")) == []


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
        ("delivery_options", {"include_evidence_files": True}),
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
    assert not (case / "work" / "deliveries" / "revision-0001-report-v3").exists()

    retry = build_case_delivery(case)
    assert retry["revision"] == 1


def test_case_delivery_restores_previous_pointer_when_handoff_write_fails(tmp_path: Path):
    case = _case(tmp_path)
    first = build_case_delivery(case)
    pointer_path = case / "work" / "delivery.json"
    original_pointer = pointer_path.read_bytes()
    private_snapshot = case / "work" / "tax-health-snapshot.private.json"
    payload = json.loads(private_snapshot.read_text(encoding="utf-8"))
    payload["revision"] = 2
    private_snapshot.write_text(json.dumps(payload), encoding="utf-8")

    with patch(
        "open_tax_check.delivery.build_case_customer_handoff",
        side_effect=ValueError("handoff_write_failed"),
    ):
        with pytest.raises(ValueError, match="^delivery_write_failed$"):
            build_case_delivery(case)

    assert pointer_path.read_bytes() == original_pointer
    assert not (case / "work/deliveries/revision-0002-report-v3").exists()
    assert first["revision"] == 1


def test_case_delivery_revalidates_manifest_and_cross_checks_before_pointer_publish(
    tmp_path: Path,
):
    case = _case(tmp_path)
    manifest_bytes = json.dumps({"sources": []}).encode("utf-8")
    manifest_path = case / "work/input_manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    run = replace(
        difference_run(),
        source_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
    (case / "work/tax-cross-checks.json").write_text(
        json.dumps(tax_cross_check_payload(run)), encoding="utf-8"
    )
    from open_tax_check import delivery

    original_publish = delivery._publish_pointer_and_handoff

    def mutate_then_publish(root, pointer, **bindings):
        manifest_path.write_text(json.dumps({"sources": [{"changed": True}]}), encoding="utf-8")
        return original_publish(root, pointer, **bindings)

    with patch.object(
        delivery,
        "_publish_pointer_and_handoff",
        side_effect=mutate_then_publish,
    ):
        with pytest.raises(DeliveryError, match="delivery_case_audit_invalid"):
            build_case_delivery(case)

    assert not (case / "work/delivery.json").exists()
    assert not (case / "work/deliveries/revision-0001-report-v3").exists()


def test_pointer_rollback_failure_preserves_revision_referenced_by_new_pointer(
    tmp_path: Path,
):
    case = _case(tmp_path)
    first = build_case_delivery(case)
    private_snapshot = case / "work/tax-health-snapshot.private.json"
    payload = json.loads(private_snapshot.read_text(encoding="utf-8"))
    payload["revision"] = 2
    private_snapshot.write_text(json.dumps(payload), encoding="utf-8")

    with patch(
        "open_tax_check.delivery.build_case_customer_handoff",
        side_effect=ValueError("handoff_write_failed"),
    ), patch(
        "open_tax_check.delivery._atomic_bytes",
        side_effect=OSError("rollback_failed"),
    ):
        with pytest.raises(ValueError, match="^delivery_write_failed$"):
            build_case_delivery(case)

    pointer = json.loads((case / "work/delivery.json").read_text(encoding="utf-8"))
    assert pointer["revision"] == 2
    assert (case / pointer["revision_path"]).is_dir()
    assert first["revision"] == 1


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
