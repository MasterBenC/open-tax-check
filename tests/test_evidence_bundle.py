import os

from open_tax_check.evidence_bundle import (
    EvidenceBundleError,
    EvidenceManifest,
    EvidenceManifestItem,
    parse_evidence_manifest,
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
