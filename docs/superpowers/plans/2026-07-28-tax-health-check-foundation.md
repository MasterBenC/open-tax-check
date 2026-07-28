# Tax Health Check Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic, model-independent foundation that produces a progressive 《税务健康检查报告》 snapshot for all 20 registered tax items and exposes structured questions and customer confirmations.

**Architecture:** Add immutable tax-domain contracts, a JSON-manifest rule registry, a deterministic screening coordinator, an incremental confirmation workflow, and a separate tax-health report renderer. Keep the existing Phase 2B pipeline and its formal-report review gate unchanged; the new customer-self-use report is an independent API path that later rule-pack phases can extend.

**Tech Stack:** Python 3.11–3.13 standard library, frozen dataclasses, `enum.StrEnum`, `importlib.resources`, JSON manifests, `unittest`, setuptools package data.

## Global Constraints

- Python support remains exactly `>=3.11,<3.14`.
- The core package keeps zero required runtime dependencies; do not add an LLM SDK or another dependency.
- The report name is exactly `税务健康检查报告`.
- The core never calls a model and never records model hidden reasoning.
- Register exactly 18 current taxes plus education surcharge and local education surcharge.
- Every tax result uses exactly one of `calculated`, `not_applicable`, `needs_evidence`, `needs_local_policy`, or `blocked`.
- Prototype packs do not count as 2023–2026 screening or calculation coverage.
- Missing formal packs remain visible as `blocked` with `rule_pack_unavailable`.
- Missing local parameters use `needs_local_policy`; do not infer a rate.
- Only `calculated` and evidence-backed `not_applicable` may form a definite per-tax conclusion.
- Existing Phase 2B CLI, report-review workflow, and tests remain backward compatible.
- Existing debit-equals-credit accounting invariants remain unchanged.
- Use strict red-green-refactor TDD and commit after every task.

---

## File Map

- `src/open_tax_check/tax_models.py`: immutable public contracts and validation shared by all tax-health modules.
- `src/open_tax_check/tax_registry.py`: the 20-item registry, manifest loading and validation, and period/jurisdiction pack selection.
- `src/open_tax_check/tax_packs/`: packaged JSON manifests; first batch contains only an explicitly marked prototype.
- `src/open_tax_check/tax_screening.py`: fact sufficiency, evaluator dispatch, five-state results, and question de-duplication.
- `src/open_tax_check/tax_workflow.py`: initial snapshots, confirmation validation, audit events, and incremental reruns.
- `src/open_tax_check/tax_reporting.py`: machine-readable payloads and Markdown rendering for 《税务健康检查报告》.
- `src/open_tax_check/tax_health.py`: small public façade for host Agents.
- `src/open_tax_check/schemas.py`: extend the existing generic serializer for enums and dates without changing `SCHEMA_VERSION`.
- `pyproject.toml`: include JSON rule packs in wheels and sdists.
- `README.md` and `DISCLAIMER.md`: document the experimental self-use interface and conclusion boundary.
- New focused test modules mirror each new production module; existing test modules continue protecting the old workflow.

### Task 1: Immutable tax-domain contracts and serialization

**Files:**
- Create: `src/open_tax_check/tax_models.py`
- Create: `tests/test_tax_models.py`
- Modify: `src/open_tax_check/schemas.py`
- Modify: `tests/test_schemas.py`

**Interfaces:**
- Consumes: existing `open_tax_check.schemas.to_dict(value: Any) -> Any`.
- Produces: `TAX_SCHEMA_VERSION`, `FactState`, `TaxStatus`, `PackMaturity`, `TaxDefinition`, `RuleSource`, `FactRequirement`, `TaxFact`, `DateRange`, `TaxPack`, `PackSegment`, `PackSelection`, `FactGap`, `EvaluationOutcome`, `TaxResult`, `Question`, `Confirmation`, `AuditEvent`, `TaxCheckRequest`, and `TaxCheckSnapshot`.
- `FactValue` is restricted to `str | int | bool | None`; tax money and rates must use strings rather than binary floats.

- [ ] **Step 1: Write failing enum and record tests**

Create `tests/test_tax_models.py` with focused tests equivalent to:

```python
import json
import unittest
from dataclasses import FrozenInstanceError
from datetime import date

from open_tax_check.schemas import to_dict
from open_tax_check.tax_models import (
    DateRange,
    FactState,
    TaxFact,
    TaxStatus,
)


class TaxModelTests(unittest.TestCase):
    def test_tax_status_contract_is_exact(self):
        self.assertEqual(
            {item.value for item in TaxStatus},
            {
                "calculated",
                "not_applicable",
                "needs_evidence",
                "needs_local_policy",
                "blocked",
            },
        )

    def test_tax_fact_is_immutable_and_json_serializable(self):
        fact = TaxFact(
            key="entity.vat_registration_status",
            value="general",
            state=FactState.CONFIRMED,
            evidence_refs=("source-001",),
            confirmed_by="customer",
            confirmed_at="2026-07-28T10:00:00+08:00",
        )
        self.assertEqual(to_dict(fact)["state"], "confirmed")
        json.dumps(to_dict(fact), ensure_ascii=False)
        with self.assertRaises(FrozenInstanceError):
            fact.value = "small_scale"

    def test_date_range_rejects_reverse_dates(self):
        with self.assertRaisesRegex(ValueError, "invalid_date_range"):
            DateRange(date(2026, 2, 1), date(2026, 1, 1))
```

Also test these invariants:

- a confirmed fact requires non-empty `confirmed_by` and an ISO-8601 timezone-aware `confirmed_at`;
- an inferred fact cannot contain confirmation metadata;
- `float` values raise `ValueError("invalid_fact_value")`;
- `TaxResult(status=NOT_APPLICABLE)` requires at least one evidence reference;
- `Question.priority` must be an integer from 0 through 100;
- tuples are canonicalized in every frozen record.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tax_models -v
```

Expected: import failure because `open_tax_check.tax_models` does not exist.

- [ ] **Step 3: Implement the exact public contracts**

Create `tax_models.py` with `TAX_SCHEMA_VERSION = "1.0.0"` and `StrEnum` values:

```python
class FactState(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    CONFIRMED = "confirmed"


class TaxStatus(StrEnum):
    CALCULATED = "calculated"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_EVIDENCE = "needs_evidence"
    NEEDS_LOCAL_POLICY = "needs_local_policy"
    BLOCKED = "blocked"


class PackMaturity(StrEnum):
    PROTOTYPE = "prototype"
    SCREENING = "screening"
    CALCULATION = "calculation"
```

Use `@dataclass(frozen=True, slots=True)` for all records. Use these exact field shapes:

```python
TaxDefinition(tax_id: str, name_zh: str, category: str)
RuleSource(title: str, url: str, published_on: date)
FactRequirement(
    fact_key: str,
    answer_type: str,
    evidence_types: tuple[str, ...],
    priority: int,
    blocking: bool,
    critical: bool,
)
TaxFact(
    key: str,
    value: str | int | bool | None,
    state: FactState,
    evidence_refs: tuple[str, ...] = (),
    confirmed_by: str | None = None,
    confirmed_at: str | None = None,
)
DateRange(start: date, end: date)
TaxPack(
    pack_id: str,
    tax_id: str,
    jurisdiction: str,
    effective_period: DateRange,
    maturity: PackMaturity,
    requirements: tuple[FactRequirement, ...],
    local_parameter_keys: tuple[str, ...],
    evaluator_id: str | None,
    sources: tuple[RuleSource, ...],
)
PackSegment(period: DateRange, pack: TaxPack)
PackSelection(segments: tuple[PackSegment, ...], uncovered_periods: tuple[DateRange, ...])
FactGap(
    fact_key: str,
    reason_code: str,
    answer_type: str,
    evidence_types: tuple[str, ...],
    priority: int,
    blocking: bool,
)
EvaluationOutcome(
    status: TaxStatus,
    diagnostics: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
)
TaxResult(
    tax_id: str,
    status: TaxStatus,
    rule_pack_ids: tuple[str, ...] = (),
    diagnostics: tuple[str, ...] = (),
    gaps: tuple[FactGap, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    rule_sources: tuple[RuleSource, ...] = (),
)
Question(
    question_id: str,
    fact_key: str,
    tax_ids: tuple[str, ...],
    reason_codes: tuple[str, ...],
    answer_type: str,
    evidence_types: tuple[str, ...],
    priority: int,
    blocking: bool,
)
Confirmation(
    question_id: str,
    fact_key: str,
    value: str | int | bool | None,
    confirmed_by: str,
    confirmed_at: str,
    evidence_refs: tuple[str, ...] = (),
)
AuditEvent(
    event_type: str,
    occurred_at: str,
    tax_ids: tuple[str, ...] = (),
    fact_keys: tuple[str, ...] = (),
    confirmation_digest: str | None = None,
)
TaxCheckRequest(
    period: DateRange,
    jurisdiction: str,
    facts: tuple[TaxFact, ...] = (),
    input_hashes: tuple[str, ...] = (),
)
TaxCheckSnapshot(
    request: TaxCheckRequest,
    results: tuple[TaxResult, ...],
    questions: tuple[Question, ...],
    audit_events: tuple[AuditEvent, ...],
    revision: int,
    schema_version: str = TAX_SCHEMA_VERSION,
)
```

Reject empty identifiers, duplicate fact keys, duplicate result tax IDs, invalid timestamps, reverse date ranges, booleans passed as priorities, and non-JSON scalar fact values with stable `ValueError` codes.

- [ ] **Step 4: Extend generic serialization**

In `schemas.to_dict`, before the dataclass branch, add handling for `Enum` and `date`:

```python
if isinstance(value, Enum):
    return value.value
if isinstance(value, date):
    return value.isoformat()
```

Import `date` and `Enum`. Do not change the existing `SCHEMA_VERSION = "0.2.0"`.

- [ ] **Step 5: Verify GREEN and existing schema compatibility**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tax_models tests.test_schemas -v
```

Expected: all tests pass, including existing Excel-intake serialization tests.

- [ ] **Step 6: Commit Task 1**

```powershell
git add src/open_tax_check/tax_models.py src/open_tax_check/schemas.py tests/test_tax_models.py tests/test_schemas.py
git commit -m "feat: add tax health domain contracts"
```

### Task 2: Twenty-item registry and versioned JSON pack selection

**Files:**
- Create: `src/open_tax_check/tax_registry.py`
- Create: `src/open_tax_check/tax_packs/__init__.py`
- Create: `src/open_tax_check/tax_packs/vat-cn-2023-2026.prototype.json`
- Create: `tests/test_tax_registry.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: all pack and period records from Task 1.
- Produces:
  - `TAX_DEFINITIONS: tuple[TaxDefinition, ...]`
  - `TaxPackError(code: str)`
  - `validate_pack_payload(payload: object) -> TaxPack`
  - `validate_pack_set(packs: tuple[TaxPack, ...]) -> None`
  - `load_builtin_tax_packs() -> tuple[TaxPack, ...]`
  - `select_pack_segments(packs, *, tax_id, jurisdiction, period, include_prototypes=False) -> PackSelection`

- [ ] **Step 1: Write failing registry and loader tests**

Create tests that assert the exact ID/name pairs:

```python
EXPECTED = {
    "vat": "增值税",
    "consumption_tax": "消费税",
    "enterprise_income_tax": "企业所得税",
    "individual_income_tax": "个人所得税",
    "resource_tax": "资源税",
    "urban_maintenance_construction_tax": "城市维护建设税",
    "house_property_tax": "房产税",
    "stamp_tax": "印花税",
    "urban_land_use_tax": "城镇土地使用税",
    "land_appreciation_tax": "土地增值税",
    "vehicle_purchase_tax": "车辆购置税",
    "vehicle_vessel_tax": "车船税",
    "vessel_tonnage_tax": "船舶吨税",
    "cultivated_land_occupation_tax": "耕地占用税",
    "deed_tax": "契税",
    "tobacco_tax": "烟叶税",
    "environmental_protection_tax": "环境保护税",
    "customs_duty": "关税",
    "education_surcharge": "教育费附加",
    "local_education_surcharge": "地方教育附加",
}
```

Test that:

- registry length is exactly 20 and IDs are unique;
- the packaged prototype loads but normal selection excludes it;
- `include_prototypes=True` selects it on both `2023-01-01` and `2026-12-31`;
- a 2022 or 2027 request remains uncovered;
- a `CN-11` pack overrides a `CN` pack only for its own effective segment;
- uncovered intervals are returned explicitly;
- overlapping non-prototype packs for the same tax and jurisdiction raise `TaxPackError("overlapping_tax_packs")`;
- invalid dates, unknown tax IDs, non-HTTPS sources, missing sources, and a formal pack without `evaluator_id` have stable error codes.

- [ ] **Step 2: Run registry tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tax_registry -v
```

Expected: import failure because `tax_registry.py` does not exist.

- [ ] **Step 3: Add the registry and strict manifest parser**

Implement the exact 20-item tuple above. `category` is `"tax"` for the first 18 entries and `"surcharge"` for the last two.

`validate_pack_payload` must:

1. require exactly these top-level keys: `schema_version`, `pack_id`, `tax_id`, `jurisdiction`, `effective_from`, `effective_to`, `maturity`, `requirements`, `local_parameter_keys`, `evaluator_id`, and `sources`;
2. require `schema_version == "1.0.0"`;
3. accept jurisdiction only as `CN` or `CN-` followed by two digits;
4. parse ISO dates with `date.fromisoformat`;
5. reject unknown tax IDs;
6. require at least one HTTPS source;
7. permit `evaluator_id: null` only for `prototype`;
8. require every `local_parameter_keys` entry to name one of the pack's `requirements`;
9. convert all lists to tuples and return a validated `TaxPack`.

`TaxPackError` stores a public `.code` and uses that code as its exception message. Never include the source payload or local path in the message.

- [ ] **Step 4: Add deterministic period and jurisdiction selection**

Implement `select_pack_segments` with this exact priority:

1. exclude prototypes unless `include_prototypes=True`;
2. discard packs that do not intersect the request period;
3. split the requested period at every candidate start date and at one day after every candidate end date;
4. for each inclusive segment, select an exact-jurisdiction pack before `CN`;
5. leave the segment uncovered when neither exists;
6. merge adjacent segments that select the same pack;
7. merge adjacent uncovered segments;
8. return segments and uncovered periods sorted by date.

Call `validate_pack_set` first. Do not silently choose between overlapping packs at the same jurisdiction priority.

- [ ] **Step 5: Add one packaged prototype manifest**

Create `vat-cn-2023-2026.prototype.json`:

```json
{
  "schema_version": "1.0.0",
  "pack_id": "vat-cn-2023-2026-prototype",
  "tax_id": "vat",
  "jurisdiction": "CN",
  "effective_from": "2023-01-01",
  "effective_to": "2026-12-31",
  "maturity": "prototype",
  "requirements": [
    {
      "fact_key": "entity.vat_registration_status",
      "answer_type": "choice",
      "evidence_types": ["tax_registration_record"],
      "priority": 100,
      "blocking": true,
      "critical": true
    }
  ],
  "local_parameter_keys": [],
  "evaluator_id": null,
  "sources": [
    {
      "title": "China Tax System - Types of Taxes",
      "url": "https://www.chinatax.gov.cn/eng/c101270/c101272/c5157954/content.html",
      "published_on": "2020-10-26"
    }
  ]
}
```

This is only a registry fixture and contains no rate or liability rule.

- [ ] **Step 6: Package JSON manifests**

Add:

```toml
[tool.setuptools.package-data]
"open_tax_check.tax_packs" = ["*.json"]
```

Load manifests with `importlib.resources.files("open_tax_check.tax_packs")`, sort by filename, and validate every JSON object before returning it.

- [ ] **Step 7: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tax_registry -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 2**

```powershell
git add pyproject.toml src/open_tax_check/tax_registry.py src/open_tax_check/tax_packs tests/test_tax_registry.py
git commit -m "feat: add versioned tax pack registry"
```

### Task 3: Deterministic screening and de-duplicated question queue

**Files:**
- Create: `src/open_tax_check/tax_screening.py`
- Create: `tests/test_tax_screening.py`

**Interfaces:**
- Consumes:
  - `select_pack_segments(...) -> PackSelection`
  - `TaxCheckRequest`
  - `TaxPack.evaluator_id`
- Produces:
  - `TaxEvaluationContext(tax_id, request, packs, facts)`
  - `RuleEvaluator = Callable[[TaxEvaluationContext], EvaluationOutcome]`
  - `screen_tax_items(request, packs, evaluators, tax_ids=None) -> tuple[TaxResult, ...]`
  - `build_question_queue(results) -> tuple[Question, ...]`

- [ ] **Step 1: Write failing screening-state tests**

Use in-memory formal packs and fake evaluators. Tests must establish:

```python
def calculated(context):
    return EvaluationOutcome(
        status=TaxStatus.CALCULATED,
        diagnostics=("demo_check_complete",),
        evidence_refs=("source-001",),
    )
```

Assert:

- no formal pack yields `blocked` plus `rule_pack_unavailable`;
- partial period coverage also yields `blocked`;
- a missing normal requirement yields `needs_evidence`;
- a missing key listed in `local_parameter_keys` yields `needs_local_policy`;
- an inferred critical fact is insufficient;
- an observed critical fact with an evidence reference is sufficient;
- a confirmed critical fact is sufficient without a file reference;
- a selected pack with no registered evaluator yields `blocked` plus `rule_evaluator_unavailable`;
- an evidence-backed fake evaluator can return `calculated`;
- `not_applicable` without evidence is converted to `blocked` plus `invalid_evaluator_outcome`.

- [ ] **Step 2: Write failing question-merging tests**

Create two `TaxResult` values with gaps for the same fact key and assert one question is emitted with:

- sorted union of both tax IDs;
- sorted union of reason codes and evidence types;
- maximum priority;
- `blocking=True` when either gap blocks;
- deterministic ID `question-` plus the first 16 hex characters of SHA-256 over the UTF-8 fact key.

Also assert questions sort by descending priority and then fact key.

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tax_screening -v
```

Expected: import failure because `tax_screening.py` does not exist.

- [ ] **Step 4: Implement fact sufficiency and state precedence**

For each selected pack requirement:

- no fact, or `value is None`, creates `FactGap(reason_code="missing_fact")`;
- `INFERRED` is insufficient when `critical=True`;
- `OBSERVED` is sufficient for a critical requirement only with at least one evidence reference;
- `CONFIRMED` is sufficient after Task 1 validation;
- missing local parameter keys use `FactGap(reason_code="missing_local_policy")`.

Apply result precedence exactly:

1. incomplete formal period coverage → `blocked`;
2. missing local parameter → `needs_local_policy`;
3. other missing evidence → `needs_evidence`;
4. missing evaluator → `blocked`;
5. otherwise call the evaluator once.

Deduplicate rule pack IDs and sources while preserving chronological pack order.

- [ ] **Step 5: Implement safe evaluator dispatch**

Create `TaxEvaluationContext` as a frozen dataclass in `tax_screening.py`. The evaluator mapping key is the manifest `evaluator_id`.

Catch evaluator exceptions and return:

```python
TaxResult(
    tax_id=tax_id,
    status=TaxStatus.BLOCKED,
    rule_pack_ids=pack_ids,
    diagnostics=("rule_evaluator_failed",),
    rule_sources=sources,
)
```

Do not include exception text, customer values, or stack traces in the result. Validate that `NOT_APPLICABLE` has evidence and that evaluators only return `CALCULATED` or `NOT_APPLICABLE`; otherwise use `invalid_evaluator_outcome`.

- [ ] **Step 6: Implement deterministic question merging**

Build questions exclusively from `TaxResult.gaps`. A single fact key always creates at most one question, even when it affects multiple taxes.

- [ ] **Step 7: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tax_screening -v
```

Expected: all screening and question tests pass.

- [ ] **Step 8: Commit Task 3**

```powershell
git add src/open_tax_check/tax_screening.py tests/test_tax_screening.py
git commit -m "feat: add deterministic tax screening"
```

### Task 4: Customer confirmations, privacy-safe audit, and incremental reruns

**Files:**
- Create: `src/open_tax_check/tax_workflow.py`
- Create: `tests/test_tax_workflow.py`

**Interfaces:**
- Consumes: `screen_tax_items`, `build_question_queue`, request/snapshot/confirmation records.
- Produces:
  - `start_tax_health_check(request, *, packs, evaluators, occurred_at) -> TaxCheckSnapshot`
  - `apply_confirmations(snapshot, confirmations, *, packs, evaluators) -> TaxCheckSnapshot`
  - `ConfirmationError(code: str)`

- [ ] **Step 1: Write failing initial-snapshot tests**

Assert that `start_tax_health_check`:

- returns exactly 20 unique results in registry order;
- works with an empty evaluator mapping;
- emits questions for evidence gaps;
- sets `revision == 1`;
- records one `check_started` audit event using the supplied timezone-aware `occurred_at`;
- preserves only supplied source hashes, never file paths or source contents.

- [ ] **Step 2: Write failing confirmation and incremental tests**

Use two fake evaluators that increment separate counters. Confirm a question affecting only VAT and assert:

- only the VAT counter increases;
- the unrelated tax result remains value-equal;
- the matching fact becomes `CONFIRMED`;
- the resolved question disappears;
- revision increments once;
- audit contains the fact key and SHA-256 digest of the canonical JSON value, not the raw answer.

Also test:

- unknown question ID → `ConfirmationError("unknown_question")`;
- mismatched fact key → `ConfirmationError("confirmation_fact_mismatch")`;
- invalid timestamp → Task 1 validation error before workflow mutation;
- an identical repeated confirmation is idempotent and does not rerun;
- a different value for an already confirmed fact preserves the old fact, marks affected taxes `blocked` with `confirmation_conflict`, retains a question, and records a `confirmation_conflict` audit event.

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tax_workflow -v
```

Expected: import failure because `tax_workflow.py` does not exist.

- [ ] **Step 4: Implement initial snapshots**

`start_tax_health_check` first validates the supplied `occurred_at` as a timezone-aware ISO-8601 timestamp, then calls `screen_tax_items` for all registry items, builds the question queue, and creates:

```python
AuditEvent(
    event_type="check_started",
    occurred_at=occurred_at,
)
```

Do not call the system clock inside `tax_workflow.py`; tests and host façades must supply the event time.

- [ ] **Step 5: Validate an entire confirmation batch before applying it**

First map snapshot questions by ID and reject structural errors before changing any fact or result. Convert confirmation values to canonical JSON with:

```python
json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

Hash that string with SHA-256 for audit use. Never place the raw value in `AuditEvent`.

- [ ] **Step 6: Implement idempotency, conflicts, and targeted reruns**

- Replace or add a fact only for a valid, changed confirmation.
- Obtain affected tax IDs from the original question.
- Call `screen_tax_items(..., tax_ids=affected_tax_ids)` only for changed facts.
- Merge updated results into the original registry order.
- Rebuild questions after merging.
- Preserve the existing confirmed fact on conflict and replace only affected results with `blocked/confirmation_conflict`.
- Attach a `FactGap` copied from the original question, with `reason_code="confirmation_conflict"`, so rebuilding the queue retains a corrective question.
- Increment the revision only when a valid change or conflict event is recorded.

- [ ] **Step 7: Verify GREEN and privacy assertions**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tax_workflow -v
```

Expected: all tests pass and serialized audit events contain no confirmation value.

- [ ] **Step 8: Commit Task 4**

```powershell
git add src/open_tax_check/tax_workflow.py tests/test_tax_workflow.py
git commit -m "feat: add incremental customer confirmations"
```

### Task 5: Progressive report renderer and host-Agent façade

**Files:**
- Create: `src/open_tax_check/tax_reporting.py`
- Create: `src/open_tax_check/tax_health.py`
- Create: `tests/test_tax_health_reporting.py`
- Create: `tests/test_tax_health.py`

**Interfaces:**
- Consumes: Task 4 workflow functions and `load_builtin_tax_packs`.
- Produces:
  - `tax_snapshot_payload(snapshot) -> dict[str, object]`
  - `render_tax_health_report(snapshot) -> str`
  - `run_tax_health_check(request, *, packs=None, evaluators=None, occurred_at=None) -> TaxCheckSnapshot`
  - `continue_tax_health_check(snapshot, confirmations, *, packs=None, evaluators=None) -> TaxCheckSnapshot`

- [ ] **Step 1: Write failing report-boundary tests**

Construct a snapshot containing all five statuses and assert the Markdown:

- begins with `# 税务健康检查报告`;
- contains the revision and review period;
- lists all 20 tax names and their exact machine status;
- shows missing evidence, local policy, blocked diagnostics, rule pack IDs, and official source URLs where present;
- has a separate unresolved-question section;
- does not contain model hidden reasoning;
- does not contain signature, seal, approver, or professional-review fields;
- does not say `已全面合规` or `不存在税务风险`.

Assert the fixed disclaimer contains all of:

```text
仅供客户内部自查
不属于涉税鉴证、专业税务意见或纳税申报结论
取决于客户提供的资料、确认事实和规则版本
资料或政策不足的项目未形成确定结论
客户自行决定是否申报、调整或另行咨询专业机构
```

- [ ] **Step 2: Write failing façade tests**

Call `run_tax_health_check` with no host Agent and no evaluator mapping. Assert:

- a snapshot is returned rather than an exception;
- all 20 tax items remain visible;
- the packaged prototype does not count as coverage;
- VAT is `blocked/rule_pack_unavailable`;
- `tax_snapshot_payload` passes `json.dumps(..., ensure_ascii=False)`.

Pass a valid confirmation to `continue_tax_health_check` using injected in-memory formal packs/evaluators and assert the revision and report update.

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tax_health_reporting tests.test_tax_health -v
```

Expected: import failure because the two modules do not exist.

- [ ] **Step 4: Implement machine-readable output**

`tax_snapshot_payload` delegates to `schemas.to_dict` and returns a dictionary with no model, token, prompt, or hidden-reasoning fields. Keep tuple ordering stable.

- [ ] **Step 5: Implement the Markdown report**

Render sections in this exact order:

1. title and internal-self-use notice;
2. check period, jurisdiction, revision, and input-hash count;
3. five-state summary counts;
4. one subsection per registry item;
5. unresolved customer questions;
6. rule sources and versions;
7. limitations and the fixed disclaimer.

For unresolved states, use neutral text such as `尚需资料` or `尚缺地方政策参数`. Never translate them as passed.

- [ ] **Step 6: Implement the public façade**

In `tax_health.py`, default `packs` to `load_builtin_tax_packs()` and default `evaluators` to an empty mapping. Do not import or call an LLM library.

Keep function signatures keyword-only after the request/snapshot:

```python
def run_tax_health_check(
    request: TaxCheckRequest,
    *,
    packs: tuple[TaxPack, ...] | None = None,
    evaluators: Mapping[str, RuleEvaluator] | None = None,
    occurred_at: str | None = None,
) -> TaxCheckSnapshot: ...


def continue_tax_health_check(
    snapshot: TaxCheckSnapshot,
    confirmations: tuple[Confirmation, ...],
    *,
    packs: tuple[TaxPack, ...] | None = None,
    evaluators: Mapping[str, RuleEvaluator] | None = None,
) -> TaxCheckSnapshot: ...
```

When `occurred_at` is `None`, the façade supplies
`datetime.now(timezone.utc).isoformat()` to the workflow. Tests pass a fixed value.

- [ ] **Step 7: Verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tax_health_reporting tests.test_tax_health -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 5**

```powershell
git add src/open_tax_check/tax_reporting.py src/open_tax_check/tax_health.py tests/test_tax_health_reporting.py tests/test_tax_health.py
git commit -m "feat: add progressive tax health reports"
```

### Task 6: Public contract, packaged-manifest verification, and release evidence

**Files:**
- Modify: `src/open_tax_check/__init__.py`
- Modify: `README.md`
- Modify: `DISCLAIMER.md`
- Modify: `tests/test_package_contract.py`
- Modify: `tests/test_public_repo_contract.py`
- Modify: `scripts/validate_release.py`
- Modify: `tests/test_release_validation.py`

**Interfaces:**
- Consumes: the public functions and packaged manifest from Tasks 2–5.
- Produces: documented self-use API and release validation that rejects distributions missing the built-in manifest.

- [ ] **Step 1: Write failing public-contract tests**

Add assertions that:

- the README names `税务健康检查报告`;
- the README shows imports for `run_tax_health_check`, `continue_tax_health_check`, and `render_tax_health_report`;
- the README states that the foundation ships no complete 2023–2026 tax rule coverage;
- the README and disclaimer both state that the report is for internal self-check and is not a tax assurance, professional opinion, or filing conclusion;
- neither document promises tax-professional approval for this new report;
- `open_tax_check.tax_packs` exposes the packaged prototype through `importlib.resources`.

Do not remove existing public-contract assertions for Phase 2B.

- [ ] **Step 2: Write failing distribution-content tests**

Add a release-validator test that creates a tiny wheel ZIP without the manifest and expects diagnostic:

```text
missing required package data: open_tax_check/tax_packs/vat-cn-2023-2026.prototype.json
```

Add the same member and assert that this specific check passes. For an sdist, require:

```text
open_tax_check-0.1.0/src/open_tax_check/tax_packs/vat-cn-2023-2026.prototype.json
```

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_package_contract tests.test_public_repo_contract tests.test_release_validation -v
```

Expected: failures because documentation and distribution checks are absent.

- [ ] **Step 4: Document the experimental foundation accurately**

Add a concise README subsection under the existing AI/workflow documentation:

```python
from open_tax_check.tax_health import (
    continue_tax_health_check,
    run_tax_health_check,
)
from open_tax_check.tax_reporting import render_tax_health_report
```

Explain that the host Agent supplies structured confirmations, the Python core is model-independent, unresolved items remain in the progressive report, and the first batch has registry/framework coverage only.

Append the approved self-use boundary to `DISCLAIMER.md`. Update only the package docstring in `__init__.py` to:

```python
"""OpenTaxCheck deterministic accounting and tax-health-check core."""
```

Do not change `__version__`.

- [ ] **Step 5: Validate required package data**

In `scripts/validate_release.py`, inspect wheels and sdists with `zipfile`/`tarfile` and require the exact paths from Step 2 whenever distributions are required. Emit only relative archive member names, never local absolute paths.

- [ ] **Step 6: Verify focused tests GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_package_contract tests.test_public_repo_contract tests.test_release_validation -v
```

Expected: all focused tests pass.

- [ ] **Step 7: Run complete source-tree validation**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git diff --check
```

Expected: no failures. Report Windows privilege-dependent skips as skips, not passes.

- [ ] **Step 8: Build clean distributions and run strict validation**

Remove only the two known build artifacts after verifying their resolved paths are inside this repository's `dist` directory, then run:

```powershell
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe scripts\validate_release.py --require-distributions
```

Expected: wheel and sdist build successfully, contain the JSON manifest, and strict validation passes.

- [ ] **Step 9: Verify the installed wheel in an isolated environment**

Create a temporary virtual environment outside the repository, install the newly built wheel plus the existing `excel` optional dependencies, then run the complete test suite with that interpreter and `PYTHONPATH` unset.

Expected: the same pass/fail/skip outcome as the source-tree suite, and `load_builtin_tax_packs()` reads the manifest from the wheel.

- [ ] **Step 10: Commit Task 6**

```powershell
git add src/open_tax_check/__init__.py README.md DISCLAIMER.md tests/test_package_contract.py tests/test_public_repo_contract.py scripts/validate_release.py tests/test_release_validation.py
git commit -m "docs: publish tax health foundation contract"
```

- [ ] **Step 11: Independent whole-change review**

Review the complete branch against:

- `docs/superpowers/specs/2026-07-28-tax-health-check-foundation-design.md`;
- all five statuses and failure-closed behavior;
- no direct model dependency or hidden reasoning;
- no professional-approval requirement in the new report;
- no regression to the old Phase 2B workflow;
- package-data presence in source, wheel, and sdist;
- no Critical or Important findings.

Fix any Critical or Important finding with a focused failing test, rerun the affected and full validations, and commit the fix before handoff.

## Final Handoff Boundary

After Task 6:

1. report exact source-tree, installed-wheel, build, strict-validation, and review evidence;
2. show branch name and commit range;
3. do not push, merge, tag, or release without the user's separate explicit choice.
