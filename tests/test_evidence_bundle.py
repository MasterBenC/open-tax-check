import hashlib
import os
from datetime import date
from pathlib import Path

from open_tax_check.evidence_bundle import (
    EvidenceBundleError,
    EvidenceManifest,
    EvidenceManifestItem,
    VerifiedEvidenceItem,
    VerifiedEvidenceSet,
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
