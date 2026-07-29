import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path
from zipfile import ZipFile

from open_tax_check.evidence_bundle import (
    BundleArtifact,
    EvidenceBundleError,
    EvidenceManifest,
    EvidenceManifestItem,
    VerifiedEvidenceItem,
    VerifiedEvidenceSet,
    build_evidence_bundle,
    parse_evidence_manifest,
    validate_evidence_bindings,
    verify_evidence_manifest,
)
from open_tax_check.tax_models import (
    CalculationRecord,
    DateRange,
    FactState,
    TaxCheckRequest,
    TaxCheckSnapshot,
    TaxFact,
    TaxResult,
    TaxStatus,
)
import pytest


def manifest_payload(*items):
    return {"schema_version": "tax-evidence-manifest/v1", "items": list(items)}


def item_payload(
    source_id="vat-return",
    relative_path="returns/vat.pdf",
    sha256="a" * 64,
    media_type="application/pdf",
):
    return {
        "source_id": source_id,
        "relative_path": relative_path,
        "sha256": sha256,
        "media_type": media_type,
    }


def test_parse_evidence_manifest_accepts_only_the_v1_contract():
    manifest = parse_evidence_manifest(manifest_payload(item_payload()))
    assert manifest == EvidenceManifest(
        schema_version="tax-evidence-manifest/v1",
        items=(
            EvidenceManifestItem(
                source_id="vat-return",
                relative_path="returns/vat.pdf",
                sha256="a" * 64,
                media_type="application/pdf",
            ),
        ),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": "tax-evidence-manifest/v0", "items": []},
        {"schema_version": "tax-evidence-manifest/v1", "items": [], "extra": True},
        manifest_payload({**item_payload(), "extra": True}),
        manifest_payload(
            {key: value for key, value in item_payload().items() if key != "media_type"}
        ),
        manifest_payload(item_payload(sha256="A" * 64)),
        manifest_payload(item_payload(media_type="")),
    ],
)
def test_parse_evidence_manifest_rejects_invalid_or_non_strict_payloads(payload):
    with pytest.raises(EvidenceBundleError, match="^invalid_evidence_manifest$"):
        parse_evidence_manifest(payload)


def test_parse_evidence_manifest_rejects_duplicate_ids_paths_and_more_than_500_items():
    duplicate_id = manifest_payload(
        item_payload("same", "a.pdf"),
        item_payload("same", "b.pdf"),
    )
    duplicate_path = manifest_payload(
        item_payload("a", "same.pdf"),
        item_payload("b", "same.pdf"),
    )
    duplicate_windows_path = manifest_payload(
        item_payload("a", "Returns/VAT.pdf"),
        item_payload("b", "returns\\vat.pdf"),
    )
    too_many = manifest_payload(
        *(item_payload(f"source-{index}", f"{index}.pdf") for index in range(501))
    )
    for payload in (duplicate_id, duplicate_path, duplicate_windows_path, too_many):
        with pytest.raises(EvidenceBundleError, match="^invalid_evidence_manifest$"):
            parse_evidence_manifest(payload)


def test_parse_evidence_manifest_rejects_windows_equivalent_paths_on_every_host(
    monkeypatch,
):
    monkeypatch.setattr(os.path, "normcase", lambda path: path)
    payload = manifest_payload(
        item_payload("a", "Returns/VAT.pdf"),
        item_payload("b", "returns\\vat.pdf"),
    )

    with pytest.raises(EvidenceBundleError, match="^invalid_evidence_manifest$"):
        parse_evidence_manifest(payload)


def make_snapshot(
    *,
    observed_fact_refs=(),
    confirmed_fact_refs=(),
    calculation_refs=(),
):
    facts = []
    if observed_fact_refs:
        facts.append(TaxFact(
            "observed.note",
            "unconfirmed",
            FactState.OBSERVED,
            tuple(observed_fact_refs),
        ))
    if confirmed_fact_refs:
        facts.append(TaxFact(
            "confirmed.tax_base",
            "1000.00",
            FactState.CONFIRMED,
            tuple(confirmed_fact_refs),
            "customer",
            "2026-01-02T00:00:00+08:00",
        ))
    calculation = (
        CalculationRecord(
            "generic.base_times_rate.v1",
            "1000.00",
            "0.00",
            "1000.00",
            "0.13",
            "130.00",
            tuple(calculation_refs),
        )
        if calculation_refs
        else None
    )
    return TaxCheckSnapshot(
        request=TaxCheckRequest(
            DateRange(date(2026, 1, 1), date(2026, 12, 31)),
            "CN",
            tuple(facts),
        ),
        results=(
            TaxResult(
                "vat",
                TaxStatus.CALCULATED if calculation else TaxStatus.NEEDS_EVIDENCE,
                calculation=calculation,
            ),
        ),
        questions=(),
        audit_events=(),
        revision=1,
    )


def make_verified_ids(*source_ids):
    items = tuple(
        VerifiedEvidenceItem(
            source_id=source_id,
            relative_path=f"{index}.bin",
            sha256=f"{index + 1:064x}",
            media_type="application/octet-stream",
            size_bytes=1,
            source_path=Path(f"{index}.bin"),
        )
        for index, source_id in enumerate(source_ids)
    )
    return VerifiedEvidenceSet(items, len(items))


def make_verified_files(tmp_path, *source_ids):
    root = tmp_path / "verified-files"
    root.mkdir(exist_ok=True)
    items = []
    for index, source_id in enumerate(source_ids, start=1):
        source_path = root / f"source-{index}.bin"
        source_path.write_bytes(source_id.encode("utf-8"))
        items.append(VerifiedEvidenceItem(
            source_id=source_id,
            relative_path=source_path.name,
            sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
            media_type="application/octet-stream",
            size_bytes=source_path.stat().st_size,
            source_path=source_path.resolve(),
        ))
    return VerifiedEvidenceSet(tuple(items), sum(item.size_bytes for item in items))


def make_report_artifacts(tmp_path):
    markdown = tmp_path / "税务健康检查报告.md"
    snapshot = tmp_path / "tax-health-snapshot.json"
    markdown.write_text("# 税务健康检查报告\n", encoding="utf-8")
    snapshot.write_text('{"revision":1}\n', encoding="utf-8")
    return (
        BundleArtifact("markdown_report", markdown, "税务健康检查报告.md"),
        BundleArtifact("machine_snapshot", snapshot, "tax-health-snapshot.json"),
    )


def test_bundle_excludes_originals_by_default_and_contains_only_allowlisted_metadata(tmp_path):
    destination = tmp_path / "税务健康检查证据包.zip"
    result = build_evidence_bundle(
        make_snapshot(
            confirmed_fact_refs=("customer-contract",),
            calculation_refs=("tax-return",),
        ),
        make_verified_files(tmp_path, "customer-contract", "tax-return"),
        make_report_artifacts(tmp_path),
        destination,
    )

    assert result.path == destination
    assert result.includes_evidence_files is False
    assert re.fullmatch(r"[0-9a-f]{64}", result.sha256)
    with ZipFile(destination) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert not any(name.startswith("evidence/") for name in names)
        manifest = json.loads(archive.read("bundle-manifest.json"))
        assert manifest["schema_version"] == "tax-evidence-bundle/v1"
        assert manifest["includes_evidence_files"] is False
        assert all(item["bundle_path"] is None for item in manifest["evidence_items"])
        serialized = json.dumps(manifest, ensure_ascii=False)
        assert "confirmed fact value" not in serialized
        assert "confirmed_by" not in serialized


def test_explicit_copy_uses_generated_safe_paths_and_only_verified_files(tmp_path):
    destination = tmp_path / "bundle.zip"
    verified = make_verified_files(
        tmp_path,
        "../../unsafe-source-name",
        "中文 source / with spaces",
    )
    build_evidence_bundle(
        make_snapshot(),
        verified,
        make_report_artifacts(tmp_path),
        destination,
        include_evidence_files=True,
    )

    with ZipFile(destination) as archive:
        manifest = json.loads(archive.read("bundle-manifest.json"))
        copied = [item["bundle_path"] for item in manifest["evidence_items"]]
        assert copied == ["evidence/0001/original.bin", "evidence/0002/original.bin"]
        assert all(path in archive.namelist() for path in copied)
        assert not any(".." in path or "\\" in path for path in archive.namelist())


def test_same_inputs_produce_identical_zip_bytes(tmp_path):
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    snapshot = make_snapshot()
    verified = make_verified_files(tmp_path, "source")
    artifacts = make_report_artifacts(tmp_path)

    build_evidence_bundle(snapshot, verified, artifacts, first, include_evidence_files=True)
    build_evidence_bundle(snapshot, verified, artifacts, second, include_evidence_files=True)

    assert first.read_bytes() == second.read_bytes()
    with ZipFile(first) as archive:
        assert {info.date_time for info in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_bundle_write_failure_preserves_existing_valid_destination(
    tmp_path,
    monkeypatch,
):
    destination = tmp_path / "bundle.zip"
    destination.write_bytes(b"previous-valid-bundle")
    monkeypatch.setattr(
        "open_tax_check.evidence_bundle._write_zip_members",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(EvidenceBundleError, match="^evidence_bundle_write_failed$"):
        build_evidence_bundle(
            make_snapshot(),
            make_verified_files(tmp_path, "source"),
            make_report_artifacts(tmp_path),
            destination,
        )

    assert destination.read_bytes() == b"previous-valid-bundle"
    assert not list(tmp_path.glob(".bundle.zip.*.tmp"))


def test_verify_manifest_hashes_every_file_and_enforces_aggregate_limit(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    root.mkdir()
    first = root / "first.txt"
    second = root / "second.txt"
    first.write_bytes(b"1234")
    second.write_bytes(b"5678")
    manifest = parse_evidence_manifest(manifest_payload(
        {**item_payload("first", "first.txt"), "sha256": hashlib.sha256(b"1234").hexdigest()},
        {**item_payload("second", "second.txt"), "sha256": hashlib.sha256(b"5678").hexdigest()},
    ))
    monkeypatch.setattr("open_tax_check.evidence_bundle.MAX_EVIDENCE_TOTAL_BYTES", 7)

    with pytest.raises(EvidenceBundleError, match="^evidence_bundle_size_limit_exceeded$"):
        verify_evidence_manifest(manifest, root)


def test_verify_manifest_maps_unsafe_and_failed_files_to_stable_errors(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    unsafe = parse_evidence_manifest(manifest_payload(item_payload("unsafe", "../outside.pdf")))
    mismatch_file = root / "mismatch.pdf"
    mismatch_file.write_bytes(b"content")
    mismatch = parse_evidence_manifest(manifest_payload(item_payload("mismatch", "mismatch.pdf")))

    with pytest.raises(EvidenceBundleError, match="^unsafe_evidence_path$"):
        verify_evidence_manifest(unsafe, root)
    with pytest.raises(EvidenceBundleError, match="^evidence_verification_failed$"):
        verify_evidence_manifest(mismatch, root)


def test_binding_requires_all_confirmed_fact_and_calculation_references():
    snapshot = make_snapshot(
        confirmed_fact_refs=("customer-contract",),
        calculation_refs=("tax-return", "official-rule"),
    )
    verified = make_verified_ids("customer-contract", "tax-return")

    with pytest.raises(EvidenceBundleError, match="^unverified_evidence_reference$"):
        validate_evidence_bindings(snapshot, verified)


def test_binding_ignores_unconfirmed_fact_references_but_does_not_change_fact_state():
    snapshot = make_snapshot(
        observed_fact_refs=("unconfirmed-note",),
        confirmed_fact_refs=("customer-contract",),
    )
    verified = make_verified_ids("customer-contract")

    validate_evidence_bindings(snapshot, verified)

    assert snapshot.request.facts[0].state.value == "observed"
