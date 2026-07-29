# Verified Tax Evidence Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, locally generated evidence ZIP that binds every confirmed fact and tax calculation reference to a size-bounded, SHA-256-verified file while keeping original client files out of the bundle by default.

**Architecture:** A new focused `evidence_bundle.py` module owns strict manifest parsing, collection-level verification, reference binding, deterministic bundle manifests, and atomic ZIP creation. The existing CLI remains the orchestration boundary: it validates evidence options, runs the deterministic tax check, writes the selected reports, builds the bundle, and emits one allowlisted host-delivery envelope. Existing single-file verification in `evidence.py` remains the only path/hash primitive and is reused rather than duplicated.

**Tech Stack:** Python 3.11+, standard-library `dataclasses`, `hashlib`, `json`, `os`, `pathlib`, `shutil`, `tempfile`, and `zipfile`; pytest; existing OpenTaxCheck tax models, serializers, report writers, and CLI.

## Global Constraints

- The evidence manifest schema is exactly `tax-evidence-manifest/v1`; the bundle manifest schema is exactly `tax-evidence-bundle/v1`.
- Top-level and item-level unknown or missing manifest fields are rejected.
- `source_id` and `relative_path` are each unique; a manifest contains at most 500 items.
- Each evidence file is at most 50 MiB; aggregate verified evidence is at most 512 MiB whether or not originals are copied.
- Absolute paths, `..`, symlinks, and Windows reparse points are rejected.
- Every evidence reference on a `confirmed` fact and every calculation record must bind to a verified manifest item.
- Evidence presence never upgrades `observed` or `inferred` facts to `confirmed`.
- Original client evidence is excluded unless `--include-evidence-files` is explicitly supplied with both manifest and root.
- The bundle never contains the original request JSON, hidden reasoning, credentials, tokens, direct contact details, or payment identifiers.
- ZIP members use fixed timestamps, deterministic JSON, stable ordering, UTF-8 names, and generated safe evidence paths.
- Bundle writing uses a same-directory temporary file, flush, `fsync`, and `os.replace`; failure does not leave or overwrite the target ZIP.
- Customer-facing report names and content remain Chinese, including `税务健康检查报告` and `税务健康检查证据包.zip`.
- AI must not infer client facts, tax rates, local policy, tax classification, or formal tax conclusions.
- An unbalanced ledger or failed reconciliation must not enter a normal conclusion.
- Use TDD for every behavior: observe RED, add the minimum implementation, observe GREEN, then commit.
- Use Windows PowerShell commands and the repository `.venv`; do not require Bash, Docker, network access, or a system-wide LibreOffice installation.
- Do not push, merge, tag, publish, or deploy during this plan.

## File Structure

- Create `src/open_tax_check/evidence_bundle.py`: strict evidence contracts, collection verification, binding validation, deterministic manifest rendering, atomic ZIP writer, and host-neutral result model.
- Modify `src/open_tax_check/evidence.py`: preserve single-file verification and expose the verified source path needed for bounded copying without weakening its current checks.
- Modify `src/open_tax_check/agent_orchestration.py`: produce the shared four-field evidence delivery envelope used by Codex, Hermes, WorkBuddy, 扣子, Qoder, Claude Code, and OpenClaw.
- Modify `src/open_tax_check/cli.py`: add three flags, validate option combinations, invoke verification/bundling, and map stable non-sensitive errors.
- Modify `src/open_tax_check/__init__.py`: export the evidence-bundle public interfaces used by host integrations.
- Create `tests/test_evidence_bundle.py`: focused schema, verification, binding, determinism, privacy, copying, and atomicity coverage.
- Modify `tests/test_evidence_verification.py`: cover the source-path contract added to verified results.
- Modify `tests/test_agent_orchestration.py`: lock the shared delivery envelope.
- Modify `tests/test_tax_health_cli.py`: cover CLI success, null compatibility fields, option validation, and stable errors.
- Modify `tests/test_public_repo_contract.py`: require the public privacy and host-delivery documentation.
- Modify `tests/test_release_validation.py`: require the new module and evidence-bundle documentation in release artifacts.
- Create `tests/fixtures/evidence_bundle/confirmed-vat-summary.txt`: small fictional evidence used by the real end-to-end bundle case.
- Modify `README.md`: document local usage, default exclusion of originals, stable outputs, and limitations.
- Modify `skills/open-tax-check/references/host-integrations.md`: document the common delivery envelope and upload authorization boundary.
- Modify `docs/roadmap.md`: record verified evidence bundles as complete only after the complete verification task passes.

---

### Task 1: Strict Evidence Manifest Contract

**Files:**
- Create: `src/open_tax_check/evidence_bundle.py`
- Create: `tests/test_evidence_bundle.py`

**Interfaces:**
- Consumes: JSON-compatible Python objects produced by `json.loads`.
- Produces:
  - `EvidenceBundleError(code: str)`, whose `str(error)` is the stable public error code.
  - `EvidenceManifestItem(source_id: str, relative_path: str, sha256: str, media_type: str)`.
  - `EvidenceManifest(schema_version: str, items: tuple[EvidenceManifestItem, ...])`.
  - `parse_evidence_manifest(payload: object) -> EvidenceManifest`.

- [ ] **Step 1: Write failing tests for one valid strict manifest**

```python
from open_tax_check.evidence_bundle import (
    EvidenceManifest,
    EvidenceManifestItem,
    parse_evidence_manifest,
)


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
```

- [ ] **Step 2: Run the valid-contract test and observe RED**

Run: `.venv\Scripts\python -m pytest tests/test_evidence_bundle.py::test_parse_evidence_manifest_accepts_only_the_v1_contract -q`

Expected: FAIL during import because `open_tax_check.evidence_bundle` does not exist.

- [ ] **Step 3: Add the immutable contracts and exact-key parser**

```python
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re


MANIFEST_SCHEMA_VERSION = "tax-evidence-manifest/v1"
BUNDLE_SCHEMA_VERSION = "tax-evidence-bundle/v1"
MAX_EVIDENCE_ITEMS = 500
MAX_EVIDENCE_FILE_BYTES = 50 * 1024 * 1024
MAX_EVIDENCE_TOTAL_BYTES = 512 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MANIFEST_KEYS = {"schema_version", "items"}
_ITEM_KEYS = {"source_id", "relative_path", "sha256", "media_type"}


class EvidenceBundleError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EvidenceManifestItem:
    source_id: str
    relative_path: str
    sha256: str
    media_type: str


@dataclass(frozen=True, slots=True)
class EvidenceManifest:
    schema_version: str
    items: tuple[EvidenceManifestItem, ...]


def parse_evidence_manifest(payload: object) -> EvidenceManifest:
    if not isinstance(payload, dict) or set(payload) != _MANIFEST_KEYS:
        raise EvidenceBundleError("invalid_evidence_manifest")
    if payload["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise EvidenceBundleError("invalid_evidence_manifest")
    raw_items = payload["items"]
    if not isinstance(raw_items, list) or len(raw_items) > MAX_EVIDENCE_ITEMS:
        raise EvidenceBundleError("invalid_evidence_manifest")
    items: list[EvidenceManifestItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict) or set(raw) != _ITEM_KEYS:
            raise EvidenceBundleError("invalid_evidence_manifest")
        source_id = raw["source_id"]
        relative_path = raw["relative_path"]
        sha256 = raw["sha256"]
        media_type = raw["media_type"]
        if (
            not isinstance(source_id, str) or not source_id.strip()
            or not isinstance(relative_path, str) or not relative_path.strip()
            or not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None
            or not isinstance(media_type, str) or not media_type.strip()
        ):
            raise EvidenceBundleError("invalid_evidence_manifest")
        items.append(EvidenceManifestItem(source_id, relative_path, sha256, media_type))
    if len({item.source_id for item in items}) != len(items):
        raise EvidenceBundleError("invalid_evidence_manifest")
    normalized_paths = {
        os.path.normcase(str(Path(item.relative_path)))
        for item in items
    }
    if len(normalized_paths) != len(items):
        raise EvidenceBundleError("invalid_evidence_manifest")
    return EvidenceManifest(MANIFEST_SCHEMA_VERSION, tuple(items))
```

- [ ] **Step 4: Add parameterized strict-rejection tests**

```python
import pytest
from open_tax_check.evidence_bundle import EvidenceBundleError


@pytest.mark.parametrize("payload", [
    {},
    {"schema_version": "tax-evidence-manifest/v0", "items": []},
    {"schema_version": "tax-evidence-manifest/v1", "items": [], "extra": True},
    manifest_payload({**item_payload(), "extra": True}),
    manifest_payload({key: value for key, value in item_payload().items() if key != "media_type"}),
    manifest_payload(item_payload(sha256="A" * 64)),
    manifest_payload(item_payload(media_type="")),
])
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
    too_many = manifest_payload(*(
        item_payload(f"source-{index}", f"{index}.pdf")
        for index in range(501)
    ))
    for payload in (duplicate_id, duplicate_path, duplicate_windows_path, too_many):
        with pytest.raises(EvidenceBundleError, match="^invalid_evidence_manifest$"):
            parse_evidence_manifest(payload)
```

- [ ] **Step 5: Run the manifest suite and observe GREEN**

Run: `.venv\Scripts\python -m pytest tests/test_evidence_bundle.py -q`

Expected: all manifest tests PASS.

- [ ] **Step 6: Commit the strict contract**

```powershell
git add src/open_tax_check/evidence_bundle.py tests/test_evidence_bundle.py
git commit -m "feat: add strict evidence manifest contract"
```

---

### Task 2: Collection Verification and Reference Binding

**Files:**
- Modify: `src/open_tax_check/evidence.py`
- Modify: `src/open_tax_check/evidence_bundle.py`
- Modify: `tests/test_evidence_verification.py`
- Modify: `tests/test_evidence_bundle.py`

**Interfaces:**
- Consumes:
  - `verify_evidence_file(root: Path, relative_path: str, expected_sha256: str, *, max_file_bytes: int) -> EvidenceVerification`.
  - `EvidenceManifest` from Task 1.
  - `TaxCheckSnapshot`.
- Produces:
  - `EvidenceVerification.verified_path: Path | None`.
  - `VerifiedEvidenceItem(source_id, relative_path, sha256, media_type, size_bytes, source_path)`.
  - `VerifiedEvidenceSet(items: tuple[VerifiedEvidenceItem, ...], total_size_bytes: int)`.
  - `verify_evidence_manifest(manifest: EvidenceManifest, evidence_root: Path) -> VerifiedEvidenceSet`.
  - `validate_evidence_bindings(snapshot: TaxCheckSnapshot, verified: VerifiedEvidenceSet) -> None`.

- [ ] **Step 1: Write a failing single-file path-result test**

```python
import os


def test_evidence_verification_returns_the_resolved_path_only_after_success(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    source = root / "contract.pdf"
    source.write_bytes(b"verified")
    digest = hashlib.sha256(b"verified").hexdigest()

    result = verify_evidence_file(root, "contract.pdf", digest)

    assert result.status == "verified"
    assert result.verified_path == source.resolve()


def test_evidence_verification_rejects_drive_relative_paths_and_linked_roots(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    with pytest.raises(ValueError, match="^unsafe_evidence_path$"):
        verify_evidence_file(root, "C:outside.pdf", "0" * 64)

    linked_root = tmp_path / "linked-evidence"
    try:
        os.symlink(root, linked_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(ValueError, match="^unsafe_evidence_path$"):
        verify_evidence_file(linked_root, "inside.pdf", "0" * 64)
```

- [ ] **Step 2: Run the path-result test and observe RED**

Run: `.venv\Scripts\python -m pytest tests/test_evidence_verification.py::test_evidence_verification_returns_the_resolved_path_only_after_success -q`

Expected: FAIL because `EvidenceVerification` has no `verified_path`.

- [ ] **Step 3: Extend `EvidenceVerification` without changing failed-result disclosure**

```python
@dataclass(frozen=True, slots=True)
class EvidenceVerification:
    source_id: str
    status: str
    sha256: str | None = None
    size_bytes: int | None = None
    error_code: str | None = None
    verified_path: Path | None = None
```

Change only the successful return in `verify_evidence_file`:

```python
return EvidenceVerification(
    relative.as_posix(),
    "verified",
    sha256=actual,
    size_bytes=size,
    verified_path=resolved,
)
```

Failed returns must keep `verified_path=None`.

Strengthen the current preflight before resolving the candidate:

```python
relative = Path(relative_path)
root = Path(root).absolute()
if (
    relative.is_absolute()
    or bool(relative.drive)
    or ".." in relative.parts
    or _is_reparse_or_link(root)
):
    raise ValueError("unsafe_evidence_path")
```

- [ ] **Step 4: Add failing collection and binding tests**

```python
from datetime import date
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
from open_tax_check.evidence_bundle import (
    EvidenceBundleError,
    VerifiedEvidenceItem,
    VerifiedEvidenceSet,
    validate_evidence_bindings,
    verify_evidence_manifest,
)


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
```

Add snapshot cases using the existing test factories:

```python
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
```

- [ ] **Step 5: Implement collection verification and binding**

```python
from .evidence import verify_evidence_file
from .tax_models import FactState, TaxCheckSnapshot


@dataclass(frozen=True, slots=True)
class VerifiedEvidenceItem:
    source_id: str
    relative_path: str
    sha256: str
    media_type: str
    size_bytes: int
    source_path: Path


@dataclass(frozen=True, slots=True)
class VerifiedEvidenceSet:
    items: tuple[VerifiedEvidenceItem, ...]
    total_size_bytes: int


def verify_evidence_manifest(
    manifest: EvidenceManifest,
    evidence_root: Path,
) -> VerifiedEvidenceSet:
    verified_items: list[VerifiedEvidenceItem] = []
    total = 0
    for item in manifest.items:
        try:
            result = verify_evidence_file(
                evidence_root,
                item.relative_path,
                item.sha256,
                max_file_bytes=MAX_EVIDENCE_FILE_BYTES,
            )
        except ValueError as error:
            if str(error) == "unsafe_evidence_path":
                raise EvidenceBundleError("unsafe_evidence_path") from None
            raise EvidenceBundleError("invalid_evidence_manifest") from None
        if (
            result.status != "verified"
            or result.sha256 is None
            or result.size_bytes is None
            or result.verified_path is None
        ):
            raise EvidenceBundleError("evidence_verification_failed")
        total += result.size_bytes
        if total > MAX_EVIDENCE_TOTAL_BYTES:
            raise EvidenceBundleError("evidence_bundle_size_limit_exceeded")
        verified_items.append(VerifiedEvidenceItem(
            source_id=item.source_id,
            relative_path=item.relative_path,
            sha256=result.sha256,
            media_type=item.media_type,
            size_bytes=result.size_bytes,
            source_path=result.verified_path,
        ))
    return VerifiedEvidenceSet(tuple(verified_items), total)


def validate_evidence_bindings(
    snapshot: TaxCheckSnapshot,
    verified: VerifiedEvidenceSet,
) -> None:
    verified_ids = {item.source_id for item in verified.items}
    required = {
        reference
        for fact in snapshot.request.facts
        if fact.state is FactState.CONFIRMED
        for reference in fact.evidence_refs
    }
    required.update(
        reference
        for result in snapshot.results
        if result.calculation is not None
        for reference in result.calculation.evidence_refs
    )
    if not required.issubset(verified_ids):
        raise EvidenceBundleError("unverified_evidence_reference")
```

- [ ] **Step 6: Run focused verification and binding tests**

Run: `.venv\Scripts\python -m pytest tests/test_evidence_verification.py tests/test_evidence_bundle.py -q`

Expected: PASS, including existing escape, symlink/reparse, unreadable, size, and hash mismatch cases.

- [ ] **Step 7: Commit verified collection binding**

```powershell
git add src/open_tax_check/evidence.py src/open_tax_check/evidence_bundle.py tests/test_evidence_verification.py tests/test_evidence_bundle.py
git commit -m "feat: bind tax evidence to verified files"
```

---

### Task 3: Preserve Selected Rule-Pack Versions in Results

**Files:**
- Modify: `src/open_tax_check/tax_models.py`
- Modify: `src/open_tax_check/tax_screening.py`
- Modify: `src/open_tax_check/tax_reporting.py`
- Modify: `tests/test_tax_models.py`
- Modify: `tests/test_tax_screening.py`
- Modify: `tests/test_tax_health_reporting.py`

**Interfaces:**
- Consumes: selected `TaxPack.pack_id` and `TaxPack.rule_version` values.
- Produces:
  - `TaxResult.rule_pack_versions: tuple[str, ...]`.
  - An invariant that `rule_pack_versions` is empty exactly when `rule_pack_ids` is empty, otherwise both tuples have equal length.
  - Machine snapshots that serialize the version tuple next to `rule_pack_ids`.

- [ ] **Step 1: Write failing model invariant tests**

```python
import pytest


def test_tax_result_keeps_pack_versions_parallel_to_pack_ids():
    result = TaxResult(
        "vat",
        TaxStatus.NEEDS_EVIDENCE,
        rule_pack_ids=("vat-screening", "vat-calculation"),
        rule_pack_versions=("2026.1", "2026.2"),
    )
    assert result.rule_pack_versions == ("2026.1", "2026.2")


@pytest.mark.parametrize("versions", [(), ("2026.1",), ("2026.1", "2026.2", "2026.3")])
def test_tax_result_rejects_missing_or_misaligned_pack_versions(versions):
    with pytest.raises(ValueError, match="^invalid_rule_pack_versions$"):
        TaxResult(
            "vat",
            TaxStatus.NEEDS_EVIDENCE,
            rule_pack_ids=("vat-screening", "vat-calculation"),
            rule_pack_versions=versions,
        )
```

- [ ] **Step 2: Run the model tests and observe RED**

Run: `.venv\Scripts\python -m pytest tests/test_tax_models.py -q`

Expected: FAIL because `TaxResult` does not accept `rule_pack_versions`.

- [ ] **Step 3: Add the backward-compatible field at the end of `TaxResult`**

```python
@dataclass(frozen=True, slots=True)
class TaxResult:
    tax_id: str
    status: TaxStatus
    rule_pack_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    gaps: tuple[FactGap, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    rule_sources: tuple[RuleSource, ...] = ()
    calculation: CalculationRecord | None = None
    rule_pack_versions: tuple[str, ...] = ()

```

Append these lines to the end of the existing `TaxResult.__post_init__`:

```python
object.__setattr__(self, "rule_pack_versions", tuple(self.rule_pack_versions))
if len(self.rule_pack_versions) != len(self.rule_pack_ids):
    raise ValueError("invalid_rule_pack_versions")
_require_identifiers(self.rule_pack_versions)
```

Keeping the field last preserves every existing positional `TaxResult(...)` call. Results without selected packs remain valid with both tuples empty.

- [ ] **Step 4: Write a failing screening provenance test**

```python
def test_screening_result_preserves_each_selected_pack_version():
    selected_pack = pack()
    results = screen_tax_items(
        request(),
        (selected_pack,),
        {"vat.screening": calculated},
        tax_ids=("vat",),
    )
    vat = results[0]
    assert vat.rule_pack_ids == (selected_pack.pack_id,)
    assert vat.rule_pack_versions == (selected_pack.rule_version,)
```

- [ ] **Step 5: Thread versions through every selected-pack result**

Change the helper to return three parallel values:

```python
def _selected_pack_details(
    selected_packs: tuple[TaxPack, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[RuleSource, ...]]:
    return (
        tuple(pack.pack_id for pack in selected_packs),
        tuple(pack.rule_version for pack in selected_packs),
        tuple(_ordered_unique(tuple(
            source for pack in selected_packs for source in pack.sources
        ))),
    )
```

Add `pack_versions` to `_blocked_result`, and pass `rule_pack_versions=pack_versions` in every `TaxResult` created from selected packs. Leave unavailable results that have no selected pack with empty IDs and versions. Update all `_selected_pack_details` unpacking sites together so there is no ID/version drift.

- [ ] **Step 6: Lock machine-snapshot serialization**

```python
def test_snapshot_payload_includes_rule_pack_versions():
    payload = tax_snapshot_payload(snapshot_with_every_status())
    result = next(item for item in payload["results"] if item["rule_pack_ids"])
    assert result["rule_pack_versions"] == ["2026.1"]
```

Update `snapshot_with_every_status` so every constructed result with `rule_pack_ids=("vat-cn-2026",)` also has `rule_pack_versions=("2026.1",)`.

- [ ] **Step 7: Run models, screening, and reporting tests**

Run:

```powershell
.venv\Scripts\python -m pytest `
  tests/test_tax_models.py `
  tests/test_tax_screening.py `
  tests/test_tax_health_reporting.py `
  -q
```

Expected: PASS with every selected pack ID aligned to exactly one version.

- [ ] **Step 8: Commit rule-version provenance**

```powershell
git add src/open_tax_check/tax_models.py src/open_tax_check/tax_screening.py src/open_tax_check/tax_reporting.py tests/test_tax_models.py tests/test_tax_screening.py tests/test_tax_health_reporting.py
git commit -m "feat: preserve tax rule pack versions"
```

---

### Task 4: Deterministic Private-by-Default ZIP Builder

**Files:**
- Modify: `src/open_tax_check/evidence_bundle.py`
- Modify: `tests/test_evidence_bundle.py`

**Interfaces:**
- Consumes:
  - `TaxCheckSnapshot`.
  - `VerifiedEvidenceSet`.
  - generated report/snapshot files supplied as `BundleArtifact`.
- Produces:
  - `BundleArtifact(kind: str, path: Path, archive_path: str)`.
  - `EvidenceBundleResult(path: Path, sha256: str, includes_evidence_files: bool)`.
  - `build_evidence_bundle(snapshot: TaxCheckSnapshot, verified: VerifiedEvidenceSet, artifacts: tuple[BundleArtifact, ...], destination: Path, *, include_evidence_files: bool = False) -> EvidenceBundleResult`.

- [ ] **Step 1: Write failing default-privacy and manifest-content tests**

```python
from zipfile import ZipFile


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
```

- [ ] **Step 2: Write failing explicit-copy, safe-path, and deterministic ZIP tests**

```python
def test_explicit_copy_uses_generated_safe_paths_and_only_verified_files(
    tmp_path,
):
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


def test_same_inputs_produce_identical_zip_bytes(
    tmp_path,
):
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
```

- [ ] **Step 3: Write failing atomic-failure preservation test**

```python
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
```

- [ ] **Step 4: Implement deterministic JSON, metadata, and generated archive paths**

Use these fixed serialization helpers:

```python
from typing import BinaryIO
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo


@dataclass(frozen=True, slots=True)
class BundleArtifact:
    kind: str
    path: Path
    archive_path: str


@dataclass(frozen=True, slots=True)
class EvidenceBundleResult:
    path: Path
    sha256: str
    includes_evidence_files: bool


_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_FIXED_DISCLAIMER_ZH = (
    "本证据包仅用于客户内部税务健康检查资料链路复核；"
    "文件哈希验证不等于税务事实真实、申报正确或不存在风险；"
    "本证据包不属于涉税鉴证、专业税务意见或纳税申报结论。"
)


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _archive_evidence_path(index: int, item: VerifiedEvidenceItem) -> str:
    suffix = item.source_path.suffix.lower()
    safe_suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ".bin"
    return f"evidence/{index:04d}/original{safe_suffix}"
```

Build `bundle-manifest.json` from allowlisted fields only:

```python
{
    "schema_version": BUNDLE_SCHEMA_VERSION,
    "generator_version": importlib.metadata.version("open-tax-check"),
    "period": {
        "start": snapshot.request.period.start.isoformat(),
        "end": snapshot.request.period.end.isoformat(),
    },
    "jurisdiction": snapshot.request.jurisdiction,
    "snapshot_schema_version": snapshot.schema_version,
    "snapshot_revision": snapshot.revision,
    "includes_evidence_files": include_evidence_files,
    "evidence_items": evidence_item_payloads,
    "confirmed_fact_bindings": confirmed_fact_bindings,
    "calculations": calculation_payloads,
    "rule_packs": rule_pack_payloads,
    "rule_sources": rule_source_payloads,
    "artifacts": artifact_payloads,
    "audit_events": audit_event_payloads,
    "disclaimer_zh": _FIXED_DISCLAIMER_ZH,
}
```

Rules for the payload builders:

- `confirmed_fact_bindings` contains only `fact_key` and sorted `evidence_refs`, never the fact value, `confirmed_by`, or `confirmed_at`.
- `calculations` contains `tax_id`, `formula_id`, `amount`, and sorted `evidence_refs`.
- `rule_packs` is built by zipping each result’s `rule_pack_ids` with its parallel `rule_pack_versions`, deduplicating, and sorting by `(pack_id, rule_version)`.
- `rule_sources` is the independently deduplicated official-source union, sorted by `(title, url, published_on)`, and contains `title`, `url`, `issuer`, `document_number`, `article_refs`, `published_on`, and `verified_on`. Do not invent a source-to-pack association that the snapshot does not encode.
- `artifacts` contains `kind`, `archive_path`, `sha256`, and `size_bytes`.
- `audit_events` contains the same public fields already allowed by `tax_snapshot_payload`; omit `occurred_at`.
- `evidence_items` is sorted by `source_id` and contains `source_id`, `relative_path`, generated `bundle_path` or `null`, `sha256`, `size_bytes`, `media_type`, and `"status": "verified"`.

- [ ] **Step 5: Implement fixed-metadata streaming ZIP and atomic replacement**

```python
def _zip_info(archive_path: str) -> ZipInfo:
    info = ZipInfo(archive_path, _ZIP_TIMESTAMP)
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _write_stream(archive: ZipFile, archive_path: str, source: BinaryIO) -> None:
    with archive.open(_zip_info(archive_path), "w") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)


def build_evidence_bundle(
    snapshot: TaxCheckSnapshot,
    verified: VerifiedEvidenceSet,
    artifacts: tuple[BundleArtifact, ...],
    destination: Path,
    *,
    include_evidence_files: bool = False,
) -> EvidenceBundleResult:
    validate_evidence_bindings(snapshot, verified)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w+b") as output:
            with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
                _write_zip_members(
                    archive,
                    snapshot,
                    verified,
                    artifacts,
                    include_evidence_files=include_evidence_files,
                )
            output.flush()
            os.fsync(output.fileno())
        bundle_sha256 = _sha256_file(temporary)
        os.replace(temporary, destination)
    except (OSError, ValueError, BadZipFile):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise EvidenceBundleError("evidence_bundle_write_failed") from None
    return EvidenceBundleResult(
        path=destination,
        sha256=bundle_sha256,
        includes_evidence_files=include_evidence_files,
    )
```

Write members in lexicographically sorted archive-path order. Compute artifact hashes before the manifest is serialized. Do not put the ZIP file’s own SHA-256 inside the ZIP.

- [ ] **Step 6: Run the complete bundle unit suite**

Run: `.venv\Scripts\python -m pytest tests/test_evidence_bundle.py tests/test_evidence_verification.py -q`

Expected: PASS for default privacy, explicit copying, fixed timestamps, deterministic bytes, binding, limits, and atomic failure.

- [ ] **Step 7: Commit the deterministic builder**

```powershell
git add src/open_tax_check/evidence_bundle.py tests/test_evidence_bundle.py
git commit -m "feat: build deterministic tax evidence bundles"
```

---

### Task 5: CLI Orchestration and Stable Error Contract

**Files:**
- Modify: `src/open_tax_check/cli.py`
- Modify: `tests/test_tax_health_cli.py`

**Interfaces:**
- Consumes:
  - `parse_evidence_manifest`, `verify_evidence_manifest`, `build_evidence_bundle`.
  - report paths already generated by `tax-health-check`.
- Produces CLI flags:
  - `--evidence-manifest PATH`
  - `--evidence-root PATH`
  - `--include-evidence-files`
- Produces successful JSON fields:
  - `evidence_bundle_status: "verified" | null`
  - `evidence_bundle_path: str | null`
  - `evidence_bundle_sha256: str | null`
  - `includes_evidence_files: bool`

- [ ] **Step 1: Write failing compatibility and option-combination tests**

```python
import hashlib
from pathlib import Path
from zipfile import ZipFile

from open_tax_check.evidence_bundle import EvidenceBundleError


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


def write_bound_evidence_case(
    tmp_path,
    *,
    source_id="customer-document",
    filename="customer-document.txt",
):
    evidence_root = tmp_path / "customer-evidence"
    evidence_root.mkdir()
    source = evidence_root / filename
    source.write_text("fictional verified evidence\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest_path = tmp_path / "evidence-manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": "tax-evidence-manifest/v1",
        "items": [{
            "source_id": source_id,
            "relative_path": filename,
            "sha256": digest,
            "media_type": "text/plain",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    request_path = write_request(tmp_path, facts=({
        "key": "case.confirmed_document",
        "value": "provided",
        "state": "confirmed",
        "evidence_refs": [source_id],
        "confirmed_by": "customer",
        "confirmed_at": "2026-01-02T00:00:00+08:00",
    },))
    return evidence_root, manifest_path, request_path


def test_tax_health_check_cli_keeps_null_bundle_fields_when_not_enabled(
    monkeypatch,
    tmp_path,
    capsys,
):
    request_path = write_request(tmp_path)
    invoke_tax_health_check(monkeypatch, request_path, tmp_path / "output")

    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["evidence_bundle_status"] is None
    assert result["evidence_bundle_path"] is None
    assert result["evidence_bundle_sha256"] is None
    assert result["includes_evidence_files"] is False


@pytest.mark.parametrize("extra_args", [
    ["--evidence-manifest", "manifest.json"],
    ["--evidence-root", "evidence"],
    ["--include-evidence-files"],
])
def test_tax_health_check_cli_requires_manifest_and_root_together(
    monkeypatch,
    tmp_path,
    capsys,
    extra_args,
):
    request_path = write_request(tmp_path)
    invoke_tax_health_check(
        monkeypatch,
        request_path,
        tmp_path / "output",
        *extra_args,
    )

    assert cli.main() == 4
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "error_code": "invalid_evidence_manifest",
    }
```

- [ ] **Step 2: Write a failing successful-bundle CLI test**

```python
def test_tax_health_check_cli_builds_verified_bundle_without_originals_by_default(
    monkeypatch,
    tmp_path,
    capsys,
):
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
        assert "税务健康检查报告.md" in archive.namelist()
        assert "税务健康检查报告.docx" in archive.namelist()
        assert "tax-health-snapshot.json" in archive.namelist()
        assert not any(name.startswith("evidence/") for name in archive.namelist())
```

- [ ] **Step 3: Write failing stable-error mapping tests**

```python
@pytest.mark.parametrize(("case_name", "expected_code"), [
    ("malformed-json", "invalid_evidence_manifest"),
    ("unsafe-path", "unsafe_evidence_path"),
    ("hash-mismatch", "evidence_verification_failed"),
    ("missing-reference", "unverified_evidence_reference"),
    ("aggregate-limit", "evidence_bundle_size_limit_exceeded"),
])
def test_tax_health_check_cli_returns_stable_non_sensitive_evidence_errors(
    monkeypatch,
    tmp_path,
    capsys,
    case_name,
    expected_code,
):
    request_path = write_request(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"schema_version":"tax-evidence-manifest/v1","items":[]}', encoding="utf-8")
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    invoke_tax_health_check(
        monkeypatch,
        request_path,
        tmp_path / "output",
        "--evidence-manifest", str(manifest_path),
        "--evidence-root", str(evidence_root),
    )
    failure = EvidenceBundleError(expected_code)
    patched_name = (
        "parse_evidence_manifest"
        if case_name == "malformed-json"
        else "verify_evidence_manifest"
        if case_name in {"unsafe-path", "hash-mismatch", "aggregate-limit"}
        else "build_evidence_bundle"
    )
    monkeypatch.setattr(
        cli,
        patched_name,
        lambda *args, **kwargs: (_ for _ in ()).throw(failure),
    )

    assert cli.main() == 4
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "error_code": expected_code,
    }
```

Add the operational failure case:

```python
def test_tax_health_check_cli_maps_bundle_write_failure_to_operational_error(
    monkeypatch,
    tmp_path,
    capsys,
):
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
```

- [ ] **Step 4: Add flags and validate combinations before running the tax engine**

```python
health_parser.add_argument("--evidence-manifest", type=Path)
health_parser.add_argument("--evidence-root", type=Path)
health_parser.add_argument("--include-evidence-files", action="store_true")
```

At the start of the `tax-health-check` branch:

```python
has_manifest = args.evidence_manifest is not None
has_root = args.evidence_root is not None
if has_manifest != has_root or (args.include_evidence_files and not has_manifest):
    print(json.dumps({
        "status": "failed",
        "error_code": "invalid_evidence_manifest",
    }, ensure_ascii=False))
    return 4
```

When enabled, load and verify the manifest before running the tax engine:

```python
verified_evidence = None
if has_manifest:
    try:
        manifest_payload = json.loads(
            args.evidence_manifest.read_text(encoding="utf-8")
        )
        manifest = parse_evidence_manifest(manifest_payload)
        verified_evidence = verify_evidence_manifest(manifest, args.evidence_root)
    except EvidenceBundleError as error:
        print(json.dumps({
            "status": "failed",
            "error_code": error.code,
        }, ensure_ascii=False))
        return 4
    except (OSError, UnicodeError, json.JSONDecodeError):
        print(json.dumps({
            "status": "failed",
            "error_code": "invalid_evidence_manifest",
        }, ensure_ascii=False))
        return 4
```

- [ ] **Step 5: Build after all selected artifacts have been written**

Construct `BundleArtifact` entries only for files actually generated:

```python
artifacts = [
    BundleArtifact("markdown_report", report_path, "税务健康检查报告.md"),
    BundleArtifact("machine_snapshot", snapshot_path, "tax-health-snapshot.json"),
]
if docx_path is not None:
    artifacts.append(BundleArtifact("docx_report", docx_path, "税务健康检查报告.docx"))
if pdf_path is not None:
    artifacts.append(BundleArtifact("pdf_report", pdf_path, "税务健康检查报告.pdf"))
```

Then build:

```python
bundle = None
if verified_evidence is not None:
    bundle = build_evidence_bundle(
        snapshot,
        verified_evidence,
        tuple(artifacts),
        args.output_dir / "税务健康检查证据包.zip",
        include_evidence_files=args.include_evidence_files,
    )
```

Map `EvidenceBundleError.code` directly only when it is one of:

```python
{
    "invalid_evidence_manifest",
    "unsafe_evidence_path",
    "evidence_verification_failed",
    "unverified_evidence_reference",
    "evidence_bundle_size_limit_exceeded",
    "evidence_bundle_write_failed",
}
```

Use exit code `4` for input/verification/binding/size errors and exit code `3` for `evidence_bundle_write_failed`. Never print exception details, source paths, customer values, or file contents. Place this branch before the existing generic pack/input branch:

```python
except EvidenceBundleError as error:
    allowed = {
        "invalid_evidence_manifest",
        "unsafe_evidence_path",
        "evidence_verification_failed",
        "unverified_evidence_reference",
        "evidence_bundle_size_limit_exceeded",
        "evidence_bundle_write_failed",
    }
    code = error.code if error.code in allowed else "evidence_bundle_write_failed"
    print(json.dumps({"status": "failed", "error_code": code}, ensure_ascii=False))
    return 3 if code == "evidence_bundle_write_failed" else 4
```

Keep the existing tax-pack and PDF error mappings unchanged.

- [ ] **Step 6: Run focused CLI tests**

Run: `.venv\Scripts\python -m pytest tests/test_tax_health_cli.py tests/test_evidence_bundle.py -q`

Expected: PASS, including all existing `tax-health-check` behavior.

- [ ] **Step 7: Commit CLI evidence delivery**

```powershell
git add src/open_tax_check/cli.py tests/test_tax_health_cli.py
git commit -m "feat: expose verified evidence bundles in cli"
```

---

### Task 6: Shared Host-Agent Delivery Envelope

**Files:**
- Modify: `src/open_tax_check/agent_orchestration.py`
- Modify: `src/open_tax_check/__init__.py`
- Modify: `tests/test_agent_orchestration.py`

**Interfaces:**
- Consumes: `EvidenceBundleResult | None`.
- Produces: `evidence_bundle_delivery_payload(bundle: EvidenceBundleResult | None) -> dict[str, object]`.

- [ ] **Step 1: Write failing host-envelope tests**

```python
from pathlib import Path
from open_tax_check.agent_orchestration import evidence_bundle_delivery_payload
from open_tax_check.evidence_bundle import EvidenceBundleResult


def test_evidence_bundle_delivery_payload_has_one_allowlisted_contract_for_all_hosts():
    bundle = EvidenceBundleResult(
        path=Path("output/税务健康检查证据包.zip"),
        sha256="a" * 64,
        includes_evidence_files=False,
    )

    assert evidence_bundle_delivery_payload(bundle) == {
        "evidence_bundle_status": "verified",
        "evidence_bundle_path": str(bundle.path),
        "evidence_bundle_sha256": "a" * 64,
        "includes_evidence_files": False,
    }


def test_evidence_bundle_delivery_payload_is_backward_compatible_when_disabled():
    assert evidence_bundle_delivery_payload(None) == {
        "evidence_bundle_status": None,
        "evidence_bundle_path": None,
        "evidence_bundle_sha256": None,
        "includes_evidence_files": False,
    }
```

- [ ] **Step 2: Run host-envelope tests and observe RED**

Run: `.venv\Scripts\python -m pytest tests/test_agent_orchestration.py -q`

Expected: FAIL because `evidence_bundle_delivery_payload` is missing.

- [ ] **Step 3: Implement the allowlisted envelope and use it in CLI output**

```python
def evidence_bundle_delivery_payload(
    bundle: EvidenceBundleResult | None,
) -> dict[str, object]:
    if bundle is None:
        return {
            "evidence_bundle_status": None,
            "evidence_bundle_path": None,
            "evidence_bundle_sha256": None,
            "includes_evidence_files": False,
        }
    return {
        "evidence_bundle_status": "verified",
        "evidence_bundle_path": str(bundle.path),
        "evidence_bundle_sha256": bundle.sha256,
        "includes_evidence_files": bundle.includes_evidence_files,
    }
```

Export this function and the public evidence contracts from `src/open_tax_check/__init__.py`. In CLI success output, merge `evidence_bundle_delivery_payload(bundle)` instead of constructing these fields inline.

- [ ] **Step 4: Run host, CLI, and import tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/test_agent_orchestration.py tests/test_tax_health_cli.py -q
.venv\Scripts\python -c "import open_tax_check; print(open_tax_check.__version__)"
```

Expected: the tests PASS and the import command exits 0.

- [ ] **Step 5: Commit the host contract**

```powershell
git add src/open_tax_check/agent_orchestration.py src/open_tax_check/__init__.py src/open_tax_check/cli.py tests/test_agent_orchestration.py tests/test_tax_health_cli.py
git commit -m "feat: add common evidence delivery envelope"
```

---

### Task 7: Public Documentation and Release Contract

**Files:**
- Modify: `README.md`
- Modify: `skills/open-tax-check/references/host-integrations.md`
- Modify: `docs/roadmap.md`
- Modify: `tests/test_public_repo_contract.py`
- Modify: `tests/test_release_validation.py`

**Interfaces:**
- Consumes: the CLI and host fields completed in Tasks 4–5.
- Produces: public Chinese usage instructions and release-artifact assertions.

- [ ] **Step 1: Write failing public-contract tests**

```python
def test_readme_documents_private_by_default_evidence_bundles():
    text = README.read_text(encoding="utf-8")
    assert "--evidence-manifest" in text
    assert "--evidence-root" in text
    assert "--include-evidence-files" in text
    assert "默认不复制客户原始证据文件" in text
    assert "SHA-256 只验证文件身份和完整性" in text


def test_host_contract_names_all_supported_ecosystems_and_common_bundle_fields():
    text = HOST_INTEGRATIONS.read_text(encoding="utf-8")
    for host in ("Codex", "Hermes", "WorkBuddy", "扣子", "Qoder", "Claude Code", "OpenClaw"):
        assert host in text
    for field in (
        "evidence_bundle_status",
        "evidence_bundle_path",
        "evidence_bundle_sha256",
        "includes_evidence_files",
    ):
        assert field in text
    assert "未经客户明确授权" in text
```

Add release assertions that the wheel contains the Python module and the sdist contains both the module and host document:

```python
wheel_required = {"open_tax_check/evidence_bundle.py"}
sdist_required_suffixes = {
    "src/open_tax_check/evidence_bundle.py",
    "skills/open-tax-check/references/host-integrations.md",
}
```

- [ ] **Step 2: Run public and release-contract tests and observe RED**

Run: `.venv\Scripts\python -m pytest tests/test_public_repo_contract.py tests/test_release_validation.py -q`

Expected: FAIL on the new evidence-bundle documentation assertions.

- [ ] **Step 3: Document exact CLI use and privacy boundaries**

Add this Chinese README example:

```powershell
opentaxcheck tax-health-check `
  --request .\request.json `
  --output-dir .\output `
  --evidence-manifest .\evidence-manifest.json `
  --evidence-root .\customer-evidence `
  --include-docx
```

State explicitly:

- `--evidence-manifest` and `--evidence-root` must appear together.
- The default ZIP does not copy original customer evidence.
- `--include-evidence-files` copies only size-bounded, path-safe, hash-verified originals.
- SHA-256 verifies file identity and integrity only; it does not prove customer assertions, filing correctness, or absence of tax risk.
- The ZIP is a delivery audit package, not a detached full-rerun input and not a tax assurance opinion.
- A host must obtain explicit client authorization before uploading a bundle containing originals.

Add the exact four-field JSON envelope to `host-integrations.md`, followed by the same boundary for all seven named hosts.

Update `docs/roadmap.md` only after focused tests pass, marking the evidence-package item complete and retaining separate incomplete items for financial-statement/tax-return ingestion and full-case acceptance.

- [ ] **Step 4: Run documentation and release-contract tests**

Run: `.venv\Scripts\python -m pytest tests/test_public_repo_contract.py tests/test_release_validation.py -q`

Expected: PASS.

- [ ] **Step 5: Commit public documentation**

```powershell
git add README.md skills/open-tax-check/references/host-integrations.md docs/roadmap.md tests/test_public_repo_contract.py tests/test_release_validation.py
git commit -m "docs: document verified evidence bundle delivery"
```

---

### Task 8: Real Fictional Case and Final Verification

**Files:**
- Create: `tests/fixtures/evidence_bundle/confirmed-vat-summary.txt`
- Modify: `tests/test_tax_health_cli.py`
- Modify only if a verification failure exposes a defect:
  - `src/open_tax_check/evidence_bundle.py`
  - `src/open_tax_check/evidence.py`
  - `src/open_tax_check/agent_orchestration.py`
  - `src/open_tax_check/cli.py`

**Interfaces:**
- Consumes: the complete evidence-bundle workflow.
- Produces: a real on-disk fictional case demonstrating request → report → snapshot → verified ZIP → optional verified original.

- [ ] **Step 1: Add the fictional evidence fixture**

File content:

```text
Fictional VAT return summary
Period: 2026-01
Taxable base: CNY 1000.00
Applicable rate: 13%
This file contains fictional data for OpenTaxCheck tests only.
```

- [ ] **Step 2: Write the end-to-end test before running it**

```python
def test_real_fictional_case_builds_a_reopenable_bundle_with_verified_original(
    monkeypatch,
    tmp_path,
    capsys,
):
    fixture = Path("tests/fixtures/evidence_bundle/confirmed-vat-summary.txt").resolve()
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
    assert hashlib.sha256(bundle_path.read_bytes()).hexdigest() == result["evidence_bundle_sha256"]
    with ZipFile(bundle_path) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("bundle-manifest.json"))
        evidence_item = manifest["evidence_items"][0]
        assert evidence_item["source_id"] == "vat-return-2026-01"
        assert evidence_item["sha256"] == digest
        assert hashlib.sha256(archive.read(evidence_item["bundle_path"])).hexdigest() == digest
        assert any(item["kind"] == "markdown_report" for item in manifest["artifacts"])
        assert any(item["kind"] == "docx_report" for item in manifest["artifacts"])
```

- [ ] **Step 3: Run the real fictional case**

Run: `.venv\Scripts\python -m pytest tests/test_tax_health_cli.py::test_real_fictional_case_builds_a_reopenable_bundle_with_verified_original -q`

Expected: PASS and `ZipFile.testzip()` returns `None`.

- [ ] **Step 4: Run all focused evidence, host, report, and public-contract tests**

Run:

```powershell
.venv\Scripts\python -m pytest `
  tests/test_evidence_verification.py `
  tests/test_evidence_bundle.py `
  tests/test_agent_orchestration.py `
  tests/test_tax_health_reporting.py `
  tests/test_tax_health_cli.py `
  tests/test_public_repo_contract.py `
  tests/test_release_validation.py `
  -q
```

Expected: all selected tests PASS.

- [ ] **Step 5: Run the complete test suite**

Run: `.venv\Scripts\python -m pytest -q`

Expected baseline: at least the prior verified `608 passed, 13 skipped, 348 subtests passed`, plus the new evidence-bundle tests; no new failures. The optional Paddle ccache warning may remain.

- [ ] **Step 6: Build release artifacts**

Run:

```powershell
.venv\Scripts\python -m build
```

Expected: exit code 0 with both wheel and sdist created under `dist`.

- [ ] **Step 7: Run strict release validation**

Run:

```powershell
.venv\Scripts\python scripts/validate_release.py --strict
```

Expected: exit code 0 and `release validation passed`.

- [ ] **Step 8: Re-run the real local OCR smoke gate**

Use the already installed OCR extra, the already cached local Paddle models, and the existing smoke command documented by the repository. Do not download models or reinterpret a skipped smoke as a pass.

Expected: a completed local result with at least one text block and non-zero text characters, matching or exceeding the prior verified evidence:

```json
{"status":"completed","block_count":3,"text_characters":34}
```

- [ ] **Step 9: Re-run DOCX/PDF page-level visual QA**

Generate the fictional report with DOCX and PDF, using the repository’s locally extracted LibreOffice renderer under `work/tools/libreoffice-portable` and the existing Poppler rendering workflow. Inspect every rendered page for clipping, overlap, garbled Chinese, and orphaned headings.

Expected: every page is readable; no clipping, overlap, garbling, or orphaned heading is present. Record the exact page count and renderer version in the completion evidence.

- [ ] **Step 10: Review the diff for privacy and scope**

Run:

```powershell
git status --short
git diff --check
git diff --stat
rg -n "NotImplementedError|hidden reasoning|confirmed_by|confirmed_at" src/open_tax_check/evidence_bundle.py README.md skills/open-tax-check/references/host-integrations.md
```

Expected:

- `git diff --check` has no output.
- Production code contains no unfinished markers.
- `bundle-manifest.json` construction does not serialize fact values, `confirmed_by`, or `confirmed_at`.
- No unrelated user changes are staged.

- [ ] **Step 11: Commit the accepted fictional case**

```powershell
git add tests/fixtures/evidence_bundle/confirmed-vat-summary.txt tests/test_tax_health_cli.py
git commit -m "test: verify fictional tax evidence bundle case"
```

- [ ] **Step 12: Record completion evidence without publishing**

Run:

```powershell
git status --short
git log -7 --oneline
```

Expected: a clean worktree and the seven evidence-bundle commits visible locally. Report test counts, build and strict-release results, OCR evidence, PDF page count, remaining broader-project gaps, and the fact that no push, merge, tag, release, or deployment occurred.
