import hashlib
import json
import os
import re
from dataclasses import replace
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
    AuditEvent,
    CalculationRecord,
    DateRange,
    FactState,
    RuleSource,
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
        assert {info.external_attr >> 16 for info in archive.infolist()} == {0o100644}


def test_archived_bytes_and_manifest_hashes_use_immutable_staged_sources(
    tmp_path,
    monkeypatch,
):
    destination = tmp_path / "bundle.zip"
    verified = make_verified_files(tmp_path, "source")
    artifacts = make_report_artifacts(tmp_path)
    evidence_path = verified.items[0].source_path
    artifact_path = artifacts[0].path
    expected_evidence = evidence_path.read_bytes()
    expected_artifact = artifact_path.read_bytes()
    original_json_bytes = __import__(
        "open_tax_check.evidence_bundle",
        fromlist=["_json_bytes"],
    )._json_bytes

    def replace_sources_after_manifest_hashes(payload):
        evidence_replacement = tmp_path / "evidence-replacement.bin"
        artifact_replacement = tmp_path / "artifact-replacement.md"
        evidence_replacement.write_bytes(b"changed evidence")
        artifact_replacement.write_bytes(b"changed artifact")
        os.replace(evidence_replacement, evidence_path)
        os.replace(artifact_replacement, artifact_path)
        return original_json_bytes(payload)

    monkeypatch.setattr(
        "open_tax_check.evidence_bundle._json_bytes",
        replace_sources_after_manifest_hashes,
    )

    build_evidence_bundle(
        make_snapshot(),
        verified,
        artifacts,
        destination,
        include_evidence_files=True,
    )

    with ZipFile(destination) as archive:
        manifest = json.loads(archive.read("bundle-manifest.json"))
        archived_evidence = archive.read("evidence/0001/original.bin")
        archived_artifact = archive.read("税务健康检查报告.md")
        archived_artifacts = {
            item["archive_path"]: archive.read(item["archive_path"])
            for item in manifest["artifacts"]
        }
    assert archived_evidence == expected_evidence
    assert archived_artifact == expected_artifact
    assert manifest["evidence_items"][0]["sha256"] == hashlib.sha256(
        archived_evidence,
    ).hexdigest()
    artifact_metadata = next(
        item
        for item in manifest["artifacts"]
        if item["archive_path"] == "税务健康检查报告.md"
    )
    assert artifact_metadata["sha256"] == hashlib.sha256(archived_artifact).hexdigest()
    assert artifact_metadata["size_bytes"] == len(archived_artifact)
    assert all(
        item["sha256"]
        == hashlib.sha256(archived_artifacts[item["archive_path"]]).hexdigest()
        and item["size_bytes"] == len(archived_artifacts[item["archive_path"]])
        for item in manifest["artifacts"]
    )


def test_staged_source_tampering_fails_without_replacing_destination(
    tmp_path,
    monkeypatch,
):
    destination = tmp_path / "bundle.zip"
    destination.write_bytes(b"previous-valid-bundle")
    evidence_bundle = __import__(
        "open_tax_check.evidence_bundle",
        fromlist=["_stage_source"],
    )
    original_stage_source = evidence_bundle._stage_source

    def tamper_after_staging(archive_path, source_path, staged_path):
        staged = original_stage_source(archive_path, source_path, staged_path)
        staged_path.write_bytes(b"tampered after hashing")
        return staged

    monkeypatch.setattr(
        "open_tax_check.evidence_bundle._stage_source",
        tamper_after_staging,
    )

    with pytest.raises(EvidenceBundleError, match="^evidence_bundle_write_failed$"):
        build_evidence_bundle(
            make_snapshot(),
            make_verified_files(tmp_path, "source"),
            make_report_artifacts(tmp_path),
            destination,
            include_evidence_files=True,
        )

    assert destination.read_bytes() == b"previous-valid-bundle"
    assert not list(tmp_path.glob(".bundle.zip.*.tmp"))
    assert not list(tmp_path.glob(".bundle.zip.stage.*"))


@pytest.mark.parametrize(
    "archive_path",
    [
        "../payload.txt",
        "/payload.txt",
        "nested\\payload.txt",
        "C:/payload.txt",
        "C:payload.txt",
        "report.txt:secret",
        "CON",
        "aux.txt",
        "nested/LPT1.json",
        "COM¹.txt",
        "nested/LPT².json",
        "report.",
        "report ",
    ],
)
def test_bundle_rejects_windows_qualified_or_reserved_artifact_paths(
    tmp_path,
    archive_path,
):
    source = tmp_path / "report.txt"
    source.write_text("safe generated report", encoding="utf-8")

    with pytest.raises(EvidenceBundleError, match="^evidence_bundle_write_failed$"):
        build_evidence_bundle(
            make_snapshot(),
            make_verified_files(tmp_path, "source"),
            (BundleArtifact("report", source, archive_path),),
            tmp_path / "bundle.zip",
        )


@pytest.mark.parametrize(
    ("first_path", "second_path"),
    [
        ("same.txt", "same.txt"),
        ("Report.txt", "report.TXT"),
        ("nested/Snapshot.json", "NESTED/snapshot.JSON"),
    ],
)
def test_bundle_rejects_windows_equivalent_archive_member_collisions(
    tmp_path,
    first_path,
    second_path,
):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    with pytest.raises(EvidenceBundleError, match="^evidence_bundle_write_failed$"):
        build_evidence_bundle(
            make_snapshot(),
            make_verified_files(tmp_path, "source"),
            (
                BundleArtifact("first", first, first_path),
                BundleArtifact("second", second, second_path),
            ),
            tmp_path / "bundle.zip",
        )


def test_manifest_nested_payloads_are_allowlisted_and_zip_hash_stays_external(tmp_path):
    destination = tmp_path / "bundle.zip"
    snapshot = make_snapshot(
        confirmed_fact_refs=("customer-contract",),
        calculation_refs=("tax-return",),
    )
    source = RuleSource(
        "Official rule",
        "https://example.test/rule",
        date(2026, 1, 1),
        issuer="Tax authority",
        document_number="Doc 1",
        article_refs=("Article 2",),
        verified_on=date(2026, 1, 2),
    )
    result = replace(
        snapshot.results[0],
        rule_pack_ids=("pack-b", "pack-a", "pack-b"),
        rule_pack_versions=("2", "1", "2"),
        rule_sources=(source, source),
    )
    snapshot = replace(
        snapshot,
        results=(result,),
        audit_events=(
            AuditEvent(
                "confirmation_applied",
                "2026-01-03T04:05:06+08:00",
                tax_ids=("vat",),
                fact_keys=("confirmed.tax_base",),
                confirmation_digest="a" * 64,
            ),
        ),
    )
    result = build_evidence_bundle(
        snapshot,
        make_verified_files(tmp_path, "customer-contract", "tax-return"),
        make_report_artifacts(tmp_path),
        destination,
    )

    with ZipFile(destination) as archive:
        manifest_bytes = archive.read("bundle-manifest.json")
        manifest = json.loads(manifest_bytes)
    assert set(manifest) == {
        "schema_version",
        "generator_version",
        "period",
        "jurisdiction",
        "snapshot_schema_version",
        "snapshot_revision",
        "includes_evidence_files",
        "evidence_items",
        "confirmed_fact_bindings",
        "calculations",
        "rule_packs",
        "rule_sources",
        "artifacts",
        "audit_events",
        "disclaimer_zh",
    }
    assert set(manifest["period"]) == {"start", "end"}
    assert all(
        set(item)
        == {
            "source_id",
            "relative_path",
            "bundle_path",
            "sha256",
            "size_bytes",
            "media_type",
            "status",
        }
        for item in manifest["evidence_items"]
    )
    assert manifest["confirmed_fact_bindings"] == [
        {
            "fact_key": "confirmed.tax_base",
            "evidence_refs": ["customer-contract"],
        },
    ]
    assert manifest["calculations"] == [
        {
            "tax_id": "vat",
            "formula_id": "generic.base_times_rate.v1",
            "amount": "130.00",
            "evidence_refs": ["tax-return"],
        },
    ]
    assert manifest["rule_packs"] == [
        {"pack_id": "pack-a", "rule_version": "1"},
        {"pack_id": "pack-b", "rule_version": "2"},
    ]
    assert manifest["rule_sources"] == [
        {
            "title": "Official rule",
            "url": "https://example.test/rule",
            "issuer": "Tax authority",
            "document_number": "Doc 1",
            "article_refs": ["Article 2"],
            "published_on": "2026-01-01",
            "verified_on": "2026-01-02",
        },
    ]
    assert all(
        set(item) == {"kind", "archive_path", "sha256", "size_bytes"}
        for item in manifest["artifacts"]
    )
    assert manifest["audit_events"] == [
        {
            "event_type": "confirmation_applied",
            "tax_ids": ["vat"],
            "fact_keys": ["confirmed.tax_base"],
            "confirmation_digest": "a" * 64,
        },
    ]
    serialized = manifest_bytes.decode("utf-8")
    assert "confirmed_by" not in serialized
    assert "confirmed_at" not in serialized
    assert "occurred_at" not in serialized
    assert "2026-01-03T04:05:06+08:00" not in serialized
    assert result.sha256 == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert result.sha256 not in serialized


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
    assert not list(tmp_path.glob(".bundle.zip.stage.*"))


def test_unexpected_write_exception_preserves_destination_and_cleans_temp(
    tmp_path,
    monkeypatch,
):
    destination = tmp_path / "bundle.zip"
    destination.write_bytes(b"previous-valid-bundle")
    monkeypatch.setattr(
        "open_tax_check.evidence_bundle._write_zip_members",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unexpected")),
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
    assert not list(tmp_path.glob(".bundle.zip.stage.*"))


def test_zip_hash_failure_preserves_destination_and_cleans_temp(
    tmp_path,
    monkeypatch,
):
    destination = tmp_path / "bundle.zip"
    destination.write_bytes(b"previous-valid-bundle")
    evidence_bundle = __import__(
        "open_tax_check.evidence_bundle",
        fromlist=["_sha256_file"],
    )
    original_sha256_file = evidence_bundle._sha256_file

    def fail_for_zip_temporary(path):
        if path.name.startswith(".bundle.zip."):
            raise RuntimeError("hash failed")
        return original_sha256_file(path)

    monkeypatch.setattr(
        "open_tax_check.evidence_bundle._sha256_file",
        fail_for_zip_temporary,
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
    assert not list(tmp_path.glob(".bundle.zip.stage.*"))


def test_bundle_replace_failure_preserves_destination_and_cleans_work_files(
    tmp_path,
    monkeypatch,
):
    destination = tmp_path / "bundle.zip"
    destination.write_bytes(b"previous-valid-bundle")
    original_replace = os.replace

    def fail_destination_replace(source, target):
        if Path(target) == destination:
            raise OSError("replace failed")
        return original_replace(source, target)

    monkeypatch.setattr(
        "open_tax_check.evidence_bundle.os.replace",
        fail_destination_replace,
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
    assert not list(tmp_path.glob(".bundle.zip.stage.*"))


def test_fdopen_failure_closes_descriptor_preserves_destination_and_cleans_temp(
    tmp_path,
    monkeypatch,
):
    destination = tmp_path / "bundle.zip"
    destination.write_bytes(b"previous-valid-bundle")
    captured_descriptors = []

    def fail_fdopen(descriptor, *args, **kwargs):
        captured_descriptors.append(descriptor)
        raise RuntimeError("fdopen failed")

    monkeypatch.setattr("open_tax_check.evidence_bundle.os.fdopen", fail_fdopen)

    with pytest.raises(EvidenceBundleError, match="^evidence_bundle_write_failed$"):
        build_evidence_bundle(
            make_snapshot(),
            make_verified_files(tmp_path, "source"),
            make_report_artifacts(tmp_path),
            destination,
        )

    assert len(captured_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(captured_descriptors[0])
    assert destination.read_bytes() == b"previous-valid-bundle"
    assert not list(tmp_path.glob(".bundle.zip.*.tmp"))
    assert not list(tmp_path.glob(".bundle.zip.stage.*"))


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


def test_binding_accepts_host_attested_customer_confirmation_without_file_evidence():
    snapshot = make_snapshot(
        confirmed_fact_refs=("customer-confirmation:question-1",),
    )

    validate_evidence_bindings(snapshot, make_verified_ids())
