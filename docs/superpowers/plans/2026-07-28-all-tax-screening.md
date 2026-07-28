# 2023–2026 All-Tax Screening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship evidence-gated, Chinese-first national screening packs for all 20 registered tax items covering 2023-01-01 through 2026-12-31, with one generic evaluator and host-neutral Agent integration guidance.

**Architecture:** Extend the existing immutable tax contracts with a backward-compatible screening schema and a packaged Chinese fact catalog. A single deterministic evaluator interprets confirmed boolean applicability facts; independent JSON packs contain versioned requirements and official source metadata. The core remains host-neutral, while the existing Agent Skill documents local Agent Skills, MCP, and HTTP integration boundaries without duplicating tax logic.

**Tech Stack:** Python 3.11–3.13 standard library, frozen dataclasses, strict JSON manifests, `unittest`, setuptools package data, Markdown Agent Skill documentation.

## Global Constraints

- Preserve the accounting invariant that every voucher and supplied trial balance must remain debit-credit balanced; this phase does not alter accounting normalization.
- Cover exactly the 20 IDs in `TAX_DEFINITIONS` for every date from `2023-01-01` through `2026-12-31`.
- Screening packs never return `calculated`; possible applicability remains `needs_evidence` until a later calculation pack exists.
- `not_applicable` requires explicit confirmed negative facts or documentary evidence; absence of transactions in a ledger is never sufficient.
- Missing required local parameters return `needs_local_policy`; never borrow another jurisdiction's values.
- Customer-facing questions and report text are Chinese. Stable English codes remain only in machine payloads and audit records.
- Runtime execution is offline and model-independent. Do not add network calls, model SDKs, host SDKs, executable rule expressions, subprocess sandboxes, or new dependencies.
- Enforce at most 64 rule manifests, 256 KiB per manifest, one fact catalog no larger than 512 KiB, and at most 256 fact prompts before JSON object construction can grow without bound.
- Official rule sources must be HTTPS pages from the National People's Congress, State Council, Ministry of Finance, State Taxation Administration, General Administration of Customs, or the relevant provincial government/tax authority.
- GitHub and community projects may identify scenarios but may not appear as rule authorities.
- Preserve schema `1.0.0` prototype loading and the existing Phase 2B professional-review workflow.
- Public ecosystem targets are Codex, Hermes, WorkBuddy, 扣子, Qoder, Claude Code, and OpenClaw. Do not claim native compatibility that has not been verified against current official platform documentation.
- Do not push, merge, tag, publish, or deploy an MCP/HTTP gateway as part of this plan.

## File Structure

- `src/open_tax_check/tax_models.py`: immutable fact-prompt, source, pack, result, and question contracts.
- `src/open_tax_check/tax_registry.py`: schema `1.0.0`/`1.1.0` parsing, strict validation, packaged pack loading, and period selection.
- `src/open_tax_check/tax_prompts.py`: packaged Chinese fact-catalog loading and cross-pack prompt validation.
- `src/open_tax_check/tax_screening.py`: staged gaps, generic applicability evaluation, safe outcome mapping, and question aggregation.
- `src/open_tax_check/tax_health.py`: public façade that wires built-in packs, prompts, and evaluators.
- `src/open_tax_check/tax_reporting.py`: Chinese customer report and machine payload display labels.
- `src/open_tax_check/tax_packs/fact-catalog.zh-CN.v1.json`: shared Chinese prompts and evidence guidance.
- `src/open_tax_check/tax_packs/*.screening.json`: independent national screening manifests.
- `tests/test_tax_prompts.py`: fact-catalog validation and packaged loading.
- `tests/test_builtin_tax_screening_catalog.py`: complete 20-item coverage, source, period, and scenario matrix.
- `tests/test_tax_models.py`, `tests/test_tax_registry.py`, `tests/test_tax_screening.py`, `tests/test_tax_health.py`, `tests/test_tax_health_reporting.py`: focused contract and behavior tests.
- `skills/open-tax-check/SKILL.md` and `skills/open-tax-check/references/host-integrations.md`: universal Agent behavior and host routing.
- `README.md`, `DISCLAIMER.md`, `docs/roadmap.md`: truthful public scope.

---

### Task 1: Add Screening Schema 1.1 and the Chinese Fact Catalog

**Files:**
- Modify: `src/open_tax_check/tax_models.py`
- Modify: `src/open_tax_check/tax_registry.py`
- Create: `src/open_tax_check/tax_prompts.py`
- Create: `src/open_tax_check/tax_packs/fact-catalog.zh-CN.v1.json`
- Modify: `tests/test_tax_models.py`
- Modify: `tests/test_tax_registry.py`
- Create: `tests/test_tax_prompts.py`

**Interfaces:**
- Produces: `PromptChoice`, `FactPrompt`, expanded `RuleSource`, expanded `TaxPack`, `validate_fact_catalog_payload(payload)`, and `load_builtin_fact_catalog()`.
- Preserves: `validate_pack_payload(payload) -> TaxPack` for schema `1.0.0` prototype manifests.
- Consumed by Tasks 2–8.

- [ ] **Step 1: Write failing immutable-contract tests**

Add tests proving the new contracts and backward compatibility:

```python
def test_screening_pack_carries_versioned_applicability_contract(self):
    pack = TaxPack(
        pack_id="deed-tax-cn-2023-2026-screening-v1",
        tax_id="deed_tax",
        jurisdiction="CN",
        effective_period=DateRange(date(2023, 1, 1), date(2026, 12, 31)),
        maturity=PackMaturity.SCREENING,
        requirements=(requirement("activity.acquired_land_or_building_rights"),),
        local_parameter_keys=(),
        evaluator_id="generic.applicability.screening.v1",
        sources=(source(),),
        rule_version="2026.1",
        fact_catalog_version="zh-CN.v1",
        applicability_fact_keys=("activity.acquired_land_or_building_rights",),
    )
    self.assertEqual(pack.applicability_fact_keys, ("activity.acquired_land_or_building_rights",))

def test_question_presentation_defaults_preserve_existing_callers(self):
    question = Question(
        "question-1", "entity.registration", ("vat",), ("missing_fact",),
        "boolean", (), 100, True,
    )
    self.assertEqual(question.question_zh, "")

def test_rule_source_extended_metadata_is_immutable(self):
    source = RuleSource(
        title="中华人民共和国契税法",
        url="https://www.gov.cn/",
        published_on=date(2020, 8, 11),
        issuer="全国人民代表大会常务委员会",
        document_number="中华人民共和国主席令第五十二号",
        article_refs=("第一条",),
        verified_on=date(2026, 7, 28),
    )
    self.assertEqual(source.article_refs, ("第一条",))
```

- [ ] **Step 2: Run the contract tests and verify red**

Run:

```powershell
python -m unittest tests.test_tax_models -v
```

Expected: FAIL because `TaxPack`, `RuleSource`, and `Question` do not expose the new fields.

- [ ] **Step 3: Implement the minimal immutable fields**

Add these exact backward-compatible fields after existing required fields:

```python
@dataclass(frozen=True, slots=True)
class PromptChoice:
    value: str | bool
    label_zh: str

@dataclass(frozen=True, slots=True)
class FactPrompt:
    fact_key: str
    question_zh: str
    help_zh: str
    answer_type: str
    choices: tuple[PromptChoice, ...]
    evidence_help_zh: str

@dataclass(frozen=True, slots=True)
class RuleSource:
    title: str
    url: str
    published_on: date
    issuer: str = ""
    document_number: str = ""
    article_refs: tuple[str, ...] = ()
    verified_on: date | None = None

# Append to TaxPack:
rule_version: str = "1.0.0"
fact_catalog_version: str = ""
applicability_fact_keys: tuple[str, ...] = ()

# Append to Question:
question_zh: str = ""
help_zh: str = ""
evidence_help_zh: str = ""
choices: tuple[PromptChoice, ...] = ()
```

Validate non-empty Chinese presentation strings in `PromptChoice` and `FactPrompt`; validate its answer type against the same supported answer types as `FactRequirement`. Boolean prompts must contain exactly `true/是` and `false/否`; choice prompts must have unique non-empty string values; other answer types must have no choices. Coerce all collection fields to tuples. For screening packs, require non-empty `rule_version`, `fact_catalog_version`, and `applicability_fact_keys`; require every applicability key to name a boolean `FactRequirement` in that pack. Prototype and calculation callers retain defaults.

- [ ] **Step 4: Write failing schema and catalog tests**

In `tests/test_tax_registry.py`, add a complete schema `1.1.0` payload and assert:

```python
pack = validate_pack_payload(payload)
self.assertEqual(pack.rule_version, "2026.1")
self.assertEqual(pack.fact_catalog_version, "zh-CN.v1")
self.assertEqual(pack.applicability_fact_keys, ("activity.acquired_land_or_building_rights",))
```

Reject these exact conditions with stable `TaxPackError` codes:

```text
unsupported_schema_version
missing_rule_version
missing_fact_catalog_version
invalid_applicability_fact_keys
unknown_applicability_fact_key
non_boolean_applicability_fact
invalid_screening_evaluator
invalid_source_metadata
pack_manifest_too_large
too_many_pack_manifests
```

In `tests/test_tax_prompts.py`, assert that the catalog rejects unknown keys, duplicate fact keys, unsupported answer types, empty Chinese text, unexpected top-level fields, and a catalog version other than `zh-CN.v1`.
Also assert that a catalog larger than 512 KiB, more than 256 prompts, a manifest larger than 256 KiB, or more than 64 manifests fails with its stable safe code before parsing the oversized collection.

- [ ] **Step 5: Run schema tests and verify red**

Run:

```powershell
python -m unittest tests.test_tax_registry tests.test_tax_prompts -v
```

Expected: FAIL because schema `1.1.0` and the fact-catalog loader do not exist.

- [ ] **Step 6: Implement strict dual-schema parsing and catalog loading**

Use separate exact key sets for schema `1.0.0` and `1.1.0`; do not weaken the existing whitelist. Schema `1.1.0` source objects have exactly:

```json
{
  "title": "中华人民共和国契税法",
  "url": "https://www.gov.cn/",
  "published_on": "2020-08-11",
  "issuer": "全国人民代表大会常务委员会",
  "document_number": "中华人民共和国主席令第五十二号",
  "article_refs": ["第一条"],
  "verified_on": "2026-07-28"
}
```

Create the catalog with this exact envelope and one initial prompt; later data tasks extend `prompts`:

```json
{
  "schema_version": "1.0.0",
  "catalog_version": "zh-CN.v1",
  "locale": "zh-CN",
  "prompts": [
    {
      "fact_key": "activity.has_vat_taxable_transaction",
      "question_zh": "2023—2026 年是否发生过销售货物、服务、无形资产、不动产或者进口货物？",
      "help_zh": "请按实际业务回答；仅凭账套未发现相关科目不能自动视为没有发生。",
      "answer_type": "boolean",
      "choices": [
        {"value": true, "label_zh": "是"},
        {"value": false, "label_zh": "否"}
      ],
      "evidence_help_zh": "如有，请提供相关申报表、合同、发票或海关资料；如无，请由客户明确确认。"
    }
  ]
}
```

`load_builtin_tax_packs()` must ignore `fact-catalog*.json`; `load_builtin_fact_catalog()` loads exactly one matching catalog and returns an immutable `Mapping[str, FactPrompt]`.

- [ ] **Step 7: Run focused tests and verify green**

Run:

```powershell
python -m unittest tests.test_tax_models tests.test_tax_registry tests.test_tax_prompts -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```powershell
git add src/open_tax_check/tax_models.py src/open_tax_check/tax_registry.py src/open_tax_check/tax_prompts.py src/open_tax_check/tax_packs/fact-catalog.zh-CN.v1.json tests/test_tax_models.py tests/test_tax_registry.py tests/test_tax_prompts.py
git commit -m "feat: add versioned screening catalog contracts"
```

---

### Task 2: Implement the Generic Evidence-Gated Screening Evaluator

**Files:**
- Modify: `src/open_tax_check/tax_screening.py`
- Modify: `src/open_tax_check/tax_health.py`
- Modify: `src/open_tax_check/tax_workflow.py`
- Modify: `tests/test_tax_screening.py`
- Modify: `tests/test_tax_health.py`
- Modify: `tests/test_tax_workflow.py`

**Interfaces:**
- Consumes: `TaxPack.applicability_fact_keys` and schema `1.1.0` contracts from Task 1.
- Produces: `GENERIC_SCREENING_EVALUATOR_ID`, `generic_screening_evaluator(context)`, `builtin_tax_evaluators()`, and prompt-aware question building.

- [ ] **Step 1: Write failing evaluator tests for all decision branches**

Add tests using one formal screening pack with a boolean applicability requirement and one ordinary evidence requirement:

```python
def test_confirmed_negative_applicability_is_not_applicable(self):
    result = screen_tax_items(
        request_with_confirmed_boolean(False),
        (screening_pack(),),
        builtin_tax_evaluators(),
        tax_ids=("vat",),
    )[0]
    self.assertEqual(result.status, TaxStatus.NOT_APPLICABLE)
    self.assertTrue(result.evidence_refs)

def test_inferred_negative_applicability_still_needs_evidence(self):
    result = screen_one(fact=TaxFact("activity.has_vat_taxable_transaction", False, FactState.INFERRED))
    self.assertEqual(result.status, TaxStatus.NEEDS_EVIDENCE)

def test_confirmed_positive_without_calculation_pack_never_calculates(self):
    result = screen_one(fact=confirmed(True), include_required_evidence=True)
    self.assertEqual(result.status, TaxStatus.NEEDS_EVIDENCE)
    self.assertEqual(result.diagnostics, ("calculation_rule_unavailable",))

def test_negative_applicability_does_not_request_irrelevant_local_parameters(self):
    result = screen_one(fact=confirmed(False), local_parameter_missing=True)
    self.assertEqual(result.status, TaxStatus.NOT_APPLICABLE)
    self.assertEqual(result.gaps, ())
```

Also test positive applicability with a missing local parameter returns `needs_local_policy`, a confirmation without external evidence produces only an evidence reference matching `confirmation-[0-9a-f]{16}`, and schema `1.0.0` custom evaluators retain their existing behavior.

- [ ] **Step 2: Run evaluator tests and verify red**

Run:

```powershell
python -m unittest tests.test_tax_screening -v
```

Expected: FAIL because built-in generic screening evaluation and staged requirements do not exist.

- [ ] **Step 3: Implement staged gap evaluation**

Use this order in `_screen_one_tax`:

```text
1. Reject uncovered periods.
2. For screening packs, collect only applicability gaps.
3. If applicability is unresolved, return needs_evidence with only those gaps.
4. If every applicability fact is safely false, run the generic evaluator immediately.
5. If any applicability fact is true, collect remaining ordinary and local gaps.
6. Local gaps take needs_local_policy precedence; ordinary gaps use needs_evidence.
7. If no gaps remain, run the evaluator; screening returns calculation_rule_unavailable, never calculated.
```

Implement:

```python
GENERIC_SCREENING_EVALUATOR_ID = "generic.applicability.screening.v1"

def builtin_tax_evaluators() -> Mapping[str, RuleEvaluator]:
    return MappingProxyType({GENERIC_SCREENING_EVALUATOR_ID: generic_screening_evaluator})
```

`generic_screening_evaluator` must reject non-boolean, missing, or insufficient applicability facts through `_EvaluationOutcomeValidationError`. For an evidence-backed all-false decision it returns `NOT_APPLICABLE`; otherwise it returns `NEEDS_EVIDENCE` with only `calculation_rule_unavailable`. Generate a fallback confirmation reference by hashing the fact key, confirmed timestamp, and confirmed-by value; never expose those raw inputs.

Change safe-outcome validation so `NEEDS_EVIDENCE` is allowed only when every selected pack is `SCREENING` and the diagnostic tuple is exactly `("calculation_rule_unavailable",)`. Continue to reject evaluator-supplied gaps, unsafe diagnostic strings, and unsafe evidence references.

- [ ] **Step 4: Write failing façade and prompt-wiring tests**

Assert `run_tax_health_check(request)` uses built-in evaluators and the built-in fact catalog without caller injection. Assert custom `packs=` still require matching caller evaluators unless their evaluator ID is the exact generic ID. Assert `start_tax_health_check` and `apply_confirmations` pass the same prompt mapping into `build_question_queue`.

- [ ] **Step 5: Run façade/workflow tests and verify red**

Run:

```powershell
python -m unittest tests.test_tax_health tests.test_tax_workflow -v
```

Expected: FAIL because the façade currently supplies an empty evaluator mapping and workflow questions do not use prompts.

- [ ] **Step 6: Wire built-ins without overriding caller intent**

When `packs is None` and `evaluators is None`, load built-in packs, built-in prompts, and `builtin_tax_evaluators()`. When callers supply packs or evaluators, use exactly their explicit values. Add an optional immutable `prompts` mapping to internal workflow functions; existing tests may pass `{}`.

- [ ] **Step 7: Run focused tests and verify green**

```powershell
python -m unittest tests.test_tax_screening tests.test_tax_health tests.test_tax_workflow -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```powershell
git add src/open_tax_check/tax_screening.py src/open_tax_check/tax_health.py src/open_tax_check/tax_workflow.py tests/test_tax_screening.py tests/test_tax_health.py tests/test_tax_workflow.py
git commit -m "feat: add evidence-gated generic tax screening"
```

---

### Task 3: Make Customer Questions and Reports Fully Chinese

**Files:**
- Modify: `src/open_tax_check/tax_screening.py`
- Modify: `src/open_tax_check/tax_workflow.py`
- Modify: `src/open_tax_check/tax_reporting.py`
- Modify: `tests/test_tax_screening.py`
- Modify: `tests/test_tax_workflow.py`
- Modify: `tests/test_tax_health_reporting.py`
- Modify: `tests/test_tax_health.py`

**Interfaces:**
- Consumes: `FactPrompt` mappings from Task 1.
- Produces: self-contained Chinese `Question` records, customer-safe Markdown, and machine payload display labels.

- [ ] **Step 1: Write failing Chinese question tests**

Test that shared fact gaps across deed tax and stamp tax produce one question with both tax IDs and the exact catalog text. Reject a missing prompt for a built-in formal screening gap with `ValueError("missing_fact_prompt")`. Reject a prompt whose answer type differs from the merged gap with `ValueError("incompatible_prompt_answer_type")`.

- [ ] **Step 2: Run the question tests and verify red**

```powershell
python -m unittest tests.test_tax_screening.QuestionQueueTests -v
```

Expected: FAIL because `build_question_queue` does not attach Chinese text.

- [ ] **Step 3: Implement prompt-aware aggregation**

Set these fields from the catalog on every built-in question:

```python
question_zh=prompt.question_zh
help_zh=prompt.help_zh
evidence_help_zh=prompt.evidence_help_zh
choices=prompt.choices
```

Keep `question_id` derived only from `fact_key` so text corrections do not invalidate confirmation identity. Continue merging tax IDs, reason codes, evidence types, priority, and blocking flags deterministically.
When a question carries choices, `_confirmation_value_is_valid` must require the submitted value to equal one of the declared machine values. Preserve the existing type checks before membership checks.

- [ ] **Step 4: Write failing customer-report boundary tests**

Render a snapshot containing every status and assert the Markdown contains these exact labels:

```text
已完成计算
经资料确认不适用
待补充资料
待确认当地政策
暂时无法判断
```

Assert customer Markdown does not contain `calculated`, `not_applicable`, `needs_evidence`, `needs_local_policy`, `blocked`, `fact_key`, `question-`, internal diagnostics, absolute paths, or raw evidence references. Assert `tax_snapshot_payload` still contains stable status codes plus `status_label_zh`, and question objects contain both machine fields and Chinese display fields.

- [ ] **Step 5: Run report tests and verify red**

```powershell
python -m unittest tests.test_tax_health_reporting tests.test_tax_health -v
```

Expected: FAIL because the current report intentionally prints machine statuses, IDs, fact keys, evidence types, and diagnostics.

- [ ] **Step 6: Implement separate customer and machine representations**

Use this exact customer status map:

```python
_STATUS_LABELS = {
    TaxStatus.CALCULATED: "已完成计算",
    TaxStatus.NOT_APPLICABLE: "经资料确认不适用",
    TaxStatus.NEEDS_EVIDENCE: "待补充资料",
    TaxStatus.NEEDS_LOCAL_POLICY: "待确认当地政策",
    TaxStatus.BLOCKED: "暂时无法判断",
}
```

The customer report names taxes only in Chinese, renders `question_zh`, `help_zh`, and `evidence_help_zh`, and shows official source titles and metadata. It does not render machine IDs, pack IDs, fact keys, raw diagnostic codes, raw evidence references, or backticks around internal values. The JSON payload remains allowlisted and adds Chinese display fields without removing stable codes.

- [ ] **Step 7: Run focused tests and verify green**

```powershell
python -m unittest tests.test_tax_screening tests.test_tax_workflow tests.test_tax_health_reporting tests.test_tax_health -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

```powershell
git add src/open_tax_check/tax_screening.py src/open_tax_check/tax_workflow.py src/open_tax_check/tax_reporting.py tests/test_tax_screening.py tests/test_tax_workflow.py tests/test_tax_health_reporting.py tests/test_tax_health.py
git commit -m "feat: render customer-safe Chinese tax screening"
```

---

### Task 4: Add Turnover, Income, and Surcharge Screening Packs

**Files:**
- Modify: `src/open_tax_check/tax_packs/fact-catalog.zh-CN.v1.json`
- Create: `src/open_tax_check/tax_packs/vat-cn-2023-2025.screening.json`
- Create: `src/open_tax_check/tax_packs/vat-cn-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/consumption-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/enterprise-income-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/individual-income-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/urban-maintenance-construction-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/education-surcharge-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/local-education-surcharge-cn-2023-2026.screening.json`
- Create: `tests/test_builtin_tax_screening_catalog.py`

**Interfaces:**
- Consumes: schema, prompt catalog, and generic evaluator from Tasks 1–3.
- Produces: formal national coverage for 7 tax IDs.

- [ ] **Step 1: Write failing pack inventory tests**

Create a table-driven test with these exact applicability facts:

```python
EXPECTED = {
    "vat": "activity.has_vat_taxable_transaction",
    "consumption_tax": "activity.has_consumption_tax_goods",
    "enterprise_income_tax": "entity.has_enterprise_income_tax_scope",
    "individual_income_tax": "activity.has_individual_income_tax_duty",
    "urban_maintenance_construction_tax": "tax.has_vat_or_consumption_tax_payable",
    "education_surcharge": "tax.has_vat_or_consumption_tax_payable",
    "local_education_surcharge": "tax.has_vat_or_consumption_tax_payable",
}
```

Assert every selected segment uses maturity `SCREENING`, evaluator `generic.applicability.screening.v1`, catalog `zh-CN.v1`, and a non-empty extended official source. Assert VAT uses contiguous 2023–2025 and 2026 segments because the Value-Added Tax Law takes effect on 2026-01-01; all other items cover the full period with no gap.

- [ ] **Step 2: Run inventory tests and verify red**

```powershell
python -m unittest tests.test_builtin_tax_screening_catalog -v
```

Expected: FAIL because the seven formal packs and prompts are absent.

- [ ] **Step 3: Add prompts and manifests**

For each applicability fact, add a boolean Chinese prompt that asks about business activity rather than asking the customer to interpret tax law. Use this exact evidence map:

```text
vat -> tax_return, sales_ledger, purchase_ledger, invoice_record, customs_declaration
consumption_tax -> tax_return, production_ledger, sales_ledger
enterprise_income_tax -> tax_return, general_ledger, financial_statement
individual_income_tax -> payroll_record, tax_return, payment_record
urban_maintenance_construction_tax -> tax_return, payment_record
education_surcharge -> tax_return, payment_record
local_education_surcharge -> tax_return, payment_record
```

Every source must identify the controlling national law or regulation, the publishing authority, the relevant applicability articles, and a verified official URL. Use the official versions of 《中华人民共和国增值税暂行条例》 for 2023–2025, 《中华人民共和国增值税法》 for 2026, 《中华人民共和国消费税暂行条例》, 《中华人民共和国企业所得税法》, 《中华人民共和国个人所得税法》, 《中华人民共和国城市维护建设税法》, 《征收教育费附加的暂行规定》, and the controlling Ministry of Finance document for local education surcharge. The three surcharge-related packs share the payable VAT/consumption-tax fact; only local education surcharge declares `policy.local_education_surcharge_rule` as a local parameter.

- [ ] **Step 4: Write and run four scenario tests for the group**

For each tax ID, run the generic scenario helper and assert:

```python
self.assertEqual(run_case(tax_id, confirmed(False)).status, TaxStatus.NOT_APPLICABLE)
self.assertEqual(run_case(tax_id, confirmed(True)).status, expected_open_status)
self.assertEqual(run_case(tax_id, no_fact()).status, TaxStatus.NEEDS_EVIDENCE)
```

For local education surcharge, positive applicability without the local rule returns `NEEDS_LOCAL_POLICY`. Confirm all negative cases carry safe evidence references.

Run:

```powershell
python -m unittest tests.test_builtin_tax_screening_catalog -v
```

Expected: PASS for the seven-item group.

- [ ] **Step 5: Commit Task 4**

```powershell
git add src/open_tax_check/tax_packs tests/test_builtin_tax_screening_catalog.py
git commit -m "feat: add core turnover and income screening packs"
```

---

### Task 5: Add Property, Land, Contract, and Vehicle Screening Packs

**Files:**
- Modify: `src/open_tax_check/tax_packs/fact-catalog.zh-CN.v1.json`
- Create: `src/open_tax_check/tax_packs/house-property-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/stamp-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/urban-land-use-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/land-appreciation-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/vehicle-purchase-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/vehicle-vessel-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/cultivated-land-occupation-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/deed-tax-cn-2023-2026.screening.json`
- Modify: `tests/test_builtin_tax_screening_catalog.py`

**Interfaces:**
- Produces: formal national coverage for 8 more tax IDs, bringing the total to 15.

- [ ] **Step 1: Write failing inventory tests for the group**

Use these exact applicability facts:

```python
EXPECTED.update({
    "house_property_tax": "assets.has_owned_taxable_house",
    "stamp_tax": "activity.has_stamp_taxable_instrument_or_securities_transaction",
    "urban_land_use_tax": "assets.uses_urban_taxable_land",
    "land_appreciation_tax": "activity.has_real_estate_transfer",
    "vehicle_purchase_tax": "activity.acquired_taxable_vehicle",
    "vehicle_vessel_tax": "assets.owns_or_manages_taxable_vehicle_or_vessel",
    "cultivated_land_occupation_tax": "activity.occupies_cultivated_land_for_construction",
    "deed_tax": "activity.acquired_land_or_building_rights",
})
```

Assert the deed-tax and stamp-tax packs share acquisition/contract evidence requirements without producing duplicate questions for the same fact key.

- [ ] **Step 2: Run the group tests and verify red**

```powershell
python -m unittest tests.test_builtin_tax_screening_catalog -v
```

Expected: FAIL because the eight manifests and prompts are absent.

- [ ] **Step 3: Add prompts, manifests, sources, and local-parameter declarations**

Use business-language questions covering owned houses, urban land use, taxable instruments, real-estate transfers, vehicle acquisitions/ownership, cultivated-land occupation, and land/building-right acquisitions. Use this exact evidence map:

```text
house_property_tax -> asset_register, property_title, contract_register, tax_return
stamp_tax -> contract_register, accounting_voucher, tax_return
urban_land_use_tax -> asset_register, land_title, tax_return
land_appreciation_tax -> property_title, contract_register, payment_record, tax_return
vehicle_purchase_tax -> vehicle_register, invoice_record, payment_record, tax_return
vehicle_vessel_tax -> vehicle_register, tax_return
cultivated_land_occupation_tax -> land_approval, land_title, payment_record, tax_return
deed_tax -> property_title, land_title, contract_register, payment_record, tax_return
```

Declare local parameters only where a provincial or municipal amount/rate is required for a safe result:

```text
house_property_tax -> policy.house_property_tax_local_rule
urban_land_use_tax -> policy.urban_land_use_tax_local_rule
vehicle_vessel_tax -> policy.vehicle_vessel_tax_local_rule
cultivated_land_occupation_tax -> policy.cultivated_land_occupation_tax_local_rule
deed_tax -> policy.deed_tax_local_rule
```

Stamp tax, land appreciation tax, and vehicle purchase tax remain national screening gates in this phase. Sources must cite the controlling official laws/regulations and relevant applicability articles.
Use the official versions of 《中华人民共和国房产税暂行条例》, 《中华人民共和国印花税法》, 《中华人民共和国城镇土地使用税暂行条例》, 《中华人民共和国土地增值税暂行条例》, 《中华人民共和国车辆购置税法》, 《中华人民共和国车船税法》, 《中华人民共和国耕地占用税法》, and 《中华人民共和国契税法》.

- [ ] **Step 4: Add scenario and shared-question tests**

For all eight items, assert positive, negative, and missing-fact behavior. For every declared local parameter, assert positive applicability plus missing local policy returns `NEEDS_LOCAL_POLICY`, while confirmed negative applicability returns `NOT_APPLICABLE` without asking for that local parameter. Assert the shared fact queue is deterministic and Chinese.

- [ ] **Step 5: Run the group tests and verify green**

```powershell
python -m unittest tests.test_builtin_tax_screening_catalog tests.test_tax_screening -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

```powershell
git add src/open_tax_check/tax_packs tests/test_builtin_tax_screening_catalog.py
git commit -m "feat: add property and transaction screening packs"
```

---

### Task 6: Add Resource, Environmental, Tobacco, Tonnage, and Customs Packs

**Files:**
- Modify: `src/open_tax_check/tax_models.py`
- Modify: `src/open_tax_check/tax_registry.py`
- Modify: `src/open_tax_check/tax_screening.py`
- Modify: `src/open_tax_check/tax_health.py`
- Modify: `src/open_tax_check/tax_packs/fact-catalog.zh-CN.v1.json`
- Create: `src/open_tax_check/tax_packs/resource-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/environmental-protection-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/tobacco-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/vessel-tonnage-tax-cn-2023-2026.screening.json`
- Create: `src/open_tax_check/tax_packs/customs-duty-cn-2023-2026.screening.json`
- Modify: `tests/test_builtin_tax_screening_catalog.py`
- Modify: `tests/test_tax_registry.py`
- Modify: `tests/test_tax_screening.py`
- Modify: `tests/test_tax_health.py`

**Interfaces:**
- Produces: formal national coverage for the final 5 tax IDs and complete 20-item coverage.

- [ ] **Step 1: Write failing final inventory tests**

Add these exact facts:

```python
EXPECTED.update({
    "resource_tax": "activity.extracts_or_sells_taxable_resources",
    "environmental_protection_tax": "activity.directly_discharges_taxable_pollutants",
    "tobacco_tax": "activity.purchases_tobacco_leaves",
    "vessel_tonnage_tax": "activity.has_foreign_vessel_entering_china_port",
    "customs_duty": "activity.imports_or_exports_dutiable_goods",
})
```

Assert `set(EXPECTED) == {definition.tax_id for definition in TAX_DEFINITIONS}` and every tax has complete selected segments across the inclusive review period.

- [ ] **Step 2: Run the complete inventory and verify red**

```powershell
python -m unittest tests.test_builtin_tax_screening_catalog -v
```

Expected: FAIL because five manifests and prompts are absent and full coverage is incomplete.

- [ ] **Step 3: Add the final prompts and manifests**

Use this exact evidence map:

```text
resource_tax -> resource_license, production_ledger, sales_ledger, tax_return
environmental_protection_tax -> pollutant_monitoring_record, environmental_tax_return
tobacco_tax -> tobacco_purchase_record, payment_record, tax_return
vessel_tonnage_tax -> port_entry_record, vessel_certificate, payment_record
customs_duty -> customs_declaration, import_export_contract, payment_record
```

Declare:

```text
resource_tax -> policy.resource_tax_local_rule
environmental_protection_tax -> policy.environmental_protection_tax_local_rule
```

Tobacco tax, vessel tonnage tax, and customs duty use national screening gates. Use the official versions of 《中华人民共和国资源税法》, 《中华人民共和国环境保护税法》, 《中华人民共和国烟叶税法》, 《中华人民共和国船舶吨税法》, and 《中华人民共和国进出口关税条例》, with the relevant applicability articles for every pack.

- [ ] **Step 4: Add complete coverage, isolation, and scenario tests**

Assert all 20 items meet the three core scenarios; all declared local-parameter packs meet the fourth local-policy scenario. Corrupt one manifest in a temporary package fixture and verify runtime loading records a `TaxPackIssue(tax_id, "invalid_pack_json")` for that tax while returning the other valid packs; no manifest content, exception text, or customer path may appear. Run a request with that issue and assert only its `tax_id` becomes `blocked/rule_pack_invalid`; unaffected tax results remain intact.

Add these runtime-isolation interfaces while preserving strict release loading:

```python
@dataclass(frozen=True, slots=True)
class TaxPackIssue:
    tax_id: str
    code: str

@dataclass(frozen=True, slots=True)
class TaxPackBundle:
    packs: tuple[TaxPack, ...]
    issues: tuple[TaxPackIssue, ...]

def load_builtin_tax_pack_bundle() -> TaxPackBundle: ...
```

`load_builtin_tax_packs()` remains strict and raises for release validation and direct callers. The public façade uses the bundle loader and passes `unavailable_tax_ids` into `screen_tax_items`; safe filename-to-tax-ID routing is an explicit constant covering every packaged formal filename. If the fact catalog itself is invalid, fail the public call with stable `invalid_fact_catalog` because customer-safe questions cannot be rendered.

- [ ] **Step 5: Run complete catalog tests and verify green**

```powershell
python -m unittest tests.test_builtin_tax_screening_catalog tests.test_tax_registry tests.test_tax_screening -v
```

Expected: PASS with 20-item coverage and no prototype counted as formal coverage.

- [ ] **Step 6: Commit Task 6**

```powershell
git add src/open_tax_check/tax_models.py src/open_tax_check/tax_registry.py src/open_tax_check/tax_screening.py src/open_tax_check/tax_health.py src/open_tax_check/tax_packs tests/test_builtin_tax_screening_catalog.py tests/test_tax_registry.py tests/test_tax_screening.py tests/test_tax_health.py
git commit -m "feat: complete national all-tax screening packs"
```

---

### Task 7: Publish the Seven-Ecosystem Agent Integration Contract

**Files:**
- Modify: `skills/open-tax-check/SKILL.md`
- Create: `skills/open-tax-check/references/host-integrations.md`
- Modify: `skills/open-tax-check/references/rule-catalog.md`
- Create: `tests/fixtures/tax-health-host-contract.json`
- Modify: `README.md`
- Modify: `tests/test_skill_contract.py`
- Modify: `tests/test_public_repo_contract.py`

**Interfaces:**
- Consumes: public `run_tax_health_check`, `continue_tax_health_check`, `tax_snapshot_payload`, and `render_tax_health_report` APIs.
- Produces: one host-neutral operation sequence and a truthful capability matrix for seven named ecosystems.

- [ ] **Step 1: Write failing public contract tests**

Assert the integration reference names exactly these hosts:

```python
HOSTS = ("Codex", "Hermes", "WorkBuddy", "扣子", "Qoder", "Claude Code", "OpenClaw")
```

Assert it contains the three routes `Python / CLI / JSON 核心`, `Agent Skills`, and `MCP / HTTP`; says customer-facing output is Chinese; says remote transfer requires disclosure and authorization; and explicitly states that MCP/HTTP deployment, authentication, and tenant isolation are not implemented in this phase.

Assert `SKILL.md` instructs agents to ask `question_zh`, never translate an unresolved status into “无风险”, never invent confirmations, and upgrade the OpenTaxCheck package rather than asking customers to install individual packs.

Add a host-conformance fixture containing one fixed 2023–2026 `CN` request, one confirmed-negative applicability answer, and the expected allowlisted snapshot fields. Test that the Python façade produces the fixture's exact machine status, Chinese label, question identity, revision, and affected-tax rerun set. This fixture is the required contract input for future Agent Skill, MCP, or HTTP adapters; adapters may change transport envelopes but not these business fields.

- [ ] **Step 2: Run public contract tests and verify red**

```powershell
python -m unittest tests.test_skill_contract tests.test_public_repo_contract -v
```

Expected: FAIL because the seven-host integration contract is absent.

- [ ] **Step 3: Write the host-neutral operation sequence**

Document this exact sequence:

```text
1. Verify Python 3.11–3.13 and install OpenTaxCheck with the Excel extra when workbook input is required.
2. Build a TaxCheckRequest for one legal entity, jurisdiction, and 2023–2026 review period.
3. Call run_tax_health_check without a model provider.
4. Present only question_zh/help_zh/evidence_help_zh to the customer.
5. Submit customer answers through Confirmation with confirmer and timestamp.
6. Call continue_tax_health_check; do not rerun unrelated taxes.
7. Render the Chinese report and retain the machine payload for audit.
8. Upgrade the OpenTaxCheck package to receive newer official built-in packs.
```

The matrix routes local-command-capable hosts to Agent Skills first, routes 扣子 to MCP/HTTP, and marks WorkBuddy as capability-dependent. Every row must say “需按当前官方文档核验”; do not claim a platform certification.

- [ ] **Step 4: Update public rule-maintenance guidance**

Explain that official packs are bundled, immutable by version, selected by tax/jurisdiction/period, and updated with the package. State that local policy gaps remain visible and third-party packs are not auto-trusted. Keep the self-use disclaimer separate from the existing Phase 2B professional-report gate.

- [ ] **Step 5: Run public contract tests and verify green**

```powershell
python -m unittest tests.test_skill_contract tests.test_public_repo_contract -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 7**

```powershell
git add skills/open-tax-check README.md tests/fixtures/tax-health-host-contract.json tests/test_skill_contract.py tests/test_public_repo_contract.py
git commit -m "docs: publish host-neutral tax screening integration"
```

---

### Task 8: Harden Packaging, Release Validation, and Whole-Feature Evidence

**Files:**
- Modify: `pyproject.toml`
- Modify: `scripts/validate_release.py`
- Modify: `tests/test_package_contract.py`
- Modify: `tests/test_release_validation.py`
- Modify: `README.md`
- Modify: `DISCLAIMER.md`
- Modify: `docs/roadmap.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Verifies all outputs from Tasks 1–7 in source, wheel, sdist, and isolated installs.

- [ ] **Step 1: Write failing distribution-contract tests**

Assert wheel and sdist include exactly one fact catalog, the prototype, and every formal screening manifest. Assert formal pack tax IDs equal `TAX_DEFINITIONS`; formal coverage is continuous for 2023–2026; every formal source has issuer, document number, article references, publication date, verification date, and an HTTPS official URL.

Assert release validation fails safely for a missing pack, duplicate pack, period gap, period overlap, missing prompt, conflicting prompt type, community source URL, non-HTTPS source, or an undeclared package-data file.

- [ ] **Step 2: Run release tests and verify red**

```powershell
python -m unittest tests.test_package_contract tests.test_release_validation -v
```

Expected: FAIL because release validation does not enforce full screening coverage or the fact catalog.

- [ ] **Step 3: Implement strict release checks**

Extend release validation to load packaged data through production validators. Error messages may name the stable tax ID and safe category but must not echo manifest contents, customer values, filesystem exception text, or absolute paths. Keep current binary, secret, size, and symlink checks unchanged.

Ensure setuptools package data includes both `*.json` manifests and the catalog without adding runtime dependencies.

- [ ] **Step 4: Update truthful public status**

Document that national 2023–2026 applicability screening is implemented, tax calculation remains incomplete, local-policy gaps are explicit, PDF/OCR remains unsupported, and seven ecosystems have a common integration contract rather than seven certified native plugins. Keep the report name exactly `税务健康检查报告` and preserve the self-use disclaimer.

- [ ] **Step 5: Run all focused tests**

```powershell
python -m unittest tests.test_tax_models tests.test_tax_registry tests.test_tax_prompts tests.test_tax_screening tests.test_tax_workflow tests.test_tax_health tests.test_tax_health_reporting tests.test_builtin_tax_screening_catalog tests.test_skill_contract tests.test_public_repo_contract tests.test_package_contract tests.test_release_validation -v
```

Expected: PASS with only explicitly documented platform/privilege-dependent skips.

- [ ] **Step 6: Run the complete source suite**

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass; Windows symlink/POSIX-only tests may skip with their existing explicit reasons. Record exact totals; do not call skips passes.

- [ ] **Step 7: Build fresh distributions and run strict validation**

Remove only the repository's verified `dist/` contents through the existing build workflow, then run:

```powershell
python -m build
python scripts/validate_release.py --require-distributions
```

Expected: one wheel and one sdist; strict validation exits 0.

- [ ] **Step 8: Verify an isolated installed wheel**

Create a fresh temporary virtual environment outside the repository, install the wheel with the Excel extra, copy the test suite without the source tree, and run:

```powershell
python -m unittest discover -s tests -v
```

Expected: the installed-wheel suite matches the source-suite pass/fail/skip result. Load all built-in packs and the fact catalog from `importlib.resources` inside the isolated environment.

- [ ] **Step 9: Request independent whole-change review**

Review the diff from the branch fork point through `HEAD` against the approved design. Any Critical or Important finding must be fixed with a failing regression test and independently re-reviewed. Minor findings may be deferred only when documented with scope and risk.

- [ ] **Step 10: Commit Task 8**

```powershell
git add pyproject.toml scripts/validate_release.py tests README.md DISCLAIMER.md docs/roadmap.md CHANGELOG.md
git commit -m "chore: verify all-tax screening distributions"
```

- [ ] **Step 11: Stop at the publication boundary**

Confirm the worktree is clean and report the branch name, final commit, source-suite totals, installed-wheel totals, distribution filenames, byte sizes, and SHA-256 digests. Do not push, merge, tag, publish, deploy, or mark a PR ready without a separate user choice.
