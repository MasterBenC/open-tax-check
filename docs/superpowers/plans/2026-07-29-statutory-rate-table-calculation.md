# Statutory Rate Table Calculation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add auditable statutory rate-table calculations and ship formal 2023—2026 stamp-tax calculation packs that run after applicability screening in one tax-health check.

**Architecture:** Extend the immutable calculation contract with a selector-driven rate table, keep the generic arithmetic engine unchanged, and make the coordinator select screening and calculation maturities in separate stages. Bundle two period-specific stamp-tax calculation packs so the 2023-08-28 securities-rate change is represented without changing ordinary document rates.

**Tech Stack:** Python 3.11—3.13, immutable dataclasses, strict JSON manifests, `Decimal`, pytest/unittest, importlib resources, Markdown/DOCX/PDF report renderers.

## Global Constraints

- Customer-facing questions and report text are Chinese; stable English codes remain only in machine payloads and audit records.
- AI must not infer a tax item, statutory rate, local rate, incentive eligibility, or formal conclusion.
- Calculation inputs must be customer-confirmed and evidence-bound.
- Debit and credit totals must remain balanced before normal tax conclusions.
- Existing schema `1.0.0`—`1.2.0` packs remain readable.
- A specific deed-tax rate is never chosen from the national 3%—5% range.
- No push, merge, tag, publish, or deployment is part of this plan.

---

### Task 1: Immutable Rate-Table Contract

**Files:**
- Modify: `src/open_tax_check/tax_models.py`
- Modify: `src/open_tax_check/schemas.py`
- Test: `tests/test_tax_models.py`

**Interfaces:**
- Produces: `StatutoryRateEntry(selector: str, label_zh: str, rate: str)`
- Produces: `StatutoryRateTable(selector_fact_key: str, entries: tuple[StatutoryRateEntry, ...])`
- Changes: `CalculationSpec.rate_fact_key` becomes optional and adds `rate_table`
- Changes: `CalculationRecord` adds optional `rate_selector` and `rate_selector_label_zh`

- [ ] **Step 1: Write failing model tests**

```python
def test_statutory_rate_table_requires_unique_selectors():
    with pytest.raises(ValueError, match="invalid_statutory_rate_table"):
        StatutoryRateTable(
            "stamp_tax.tax_item",
            (
                StatutoryRateEntry("sale_contract", "买卖合同", "0.0003"),
                StatutoryRateEntry("sale_contract", "重复", "0.0005"),
            ),
        )


def test_calculation_spec_requires_exactly_one_rate_source():
    with pytest.raises(ValueError, match="invalid_calculation_spec"):
        CalculationSpec(
            "generic.base_times_rate.v1",
            "calc.tax_base",
            rate_fact_key="calc.rate",
            rate_table=StatutoryRateTable(
                "stamp_tax.tax_item",
                (StatutoryRateEntry("sale_contract", "买卖合同", "0.0003"),),
            ),
        )
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_tax_models.py -q`

Expected: import or constructor failures because the rate-table types and fields do not exist.

- [ ] **Step 3: Implement strict immutable types**

```python
@dataclass(frozen=True, slots=True)
class StatutoryRateEntry:
    selector: str
    label_zh: str
    rate: str

    def __post_init__(self) -> None:
        _require_identifier(self.selector)
        if not self.label_zh.strip():
            raise ValueError("invalid_statutory_rate_entry")
        parsed = Decimal(self.rate)
        if not parsed.is_finite() or parsed < 0 or parsed > 1:
            raise ValueError("invalid_statutory_rate_entry")


@dataclass(frozen=True, slots=True)
class StatutoryRateTable:
    selector_fact_key: str
    entries: tuple[StatutoryRateEntry, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.selector_fact_key)
        object.__setattr__(self, "entries", tuple(self.entries))
        if not self.entries or len({item.selector for item in self.entries}) != len(self.entries):
            raise ValueError("invalid_statutory_rate_table")
```

Make `CalculationSpec` enforce `(rate_fact_key is None) != (rate_table is None)`. Include the selector fact in its bound keys. Add optional selector fields to `CalculationRecord`; keep `schemas.to_dict` serialization backward compatible by preserving explicit `None`.

- [ ] **Step 4: Run model tests and verify GREEN**

Run: `.\.venv\Scripts\python -m pytest tests/test_tax_models.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/open_tax_check/tax_models.py src/open_tax_check/schemas.py tests/test_tax_models.py
git commit -m "feat: add statutory rate table contracts"
```

---

### Task 2: Schema 1.3 and Maturity-Aware Registry

**Files:**
- Modify: `src/open_tax_check/tax_registry.py`
- Test: `tests/test_tax_registry.py`

**Interfaces:**
- Consumes: `StatutoryRateEntry`, `StatutoryRateTable`
- Changes: `validate_pack_payload(payload)` accepts schema `1.3.0`
- Changes: `select_pack_segments(..., maturity: PackMaturity | None = None)`
- Changes: `validate_pack_set` rejects overlap only within the same maturity

- [ ] **Step 1: Write failing schema and overlap tests**

```python
def test_schema_1_3_parses_statutory_rate_table():
    payload = calculation_payload(schema_version="1.3.0")
    payload["requirements"] = [
        requirement("calc.tax_base", "decimal"),
        requirement("stamp_tax.tax_item", "choice"),
    ]
    payload["calculation"] = {
        "formula_id": "generic.base_times_rate.v1",
        "tax_base_fact_key": "calc.tax_base",
        "rate_fact_key": None,
        "deductions_fact_key": None,
        "rate_table": {
            "selector_fact_key": "stamp_tax.tax_item",
            "entries": [
                {"selector": "sale_contract", "label_zh": "买卖合同", "rate": "0.0003"}
            ],
        },
    }
    pack = validate_pack_payload(payload)
    assert pack.calculation.rate_table.entries[0].rate == "0.0003"


def test_screening_and_calculation_packs_may_overlap():
    validate_pack_set((screening_pack(), calculation_pack()))


def test_same_maturity_overlap_remains_rejected():
    with pytest.raises(TaxPackError, match="overlapping_tax_packs"):
        validate_pack_set((calculation_pack("a"), calculation_pack("b")))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_tax_registry.py -q`

Expected: schema `1.3.0` is rejected and cross-maturity overlap is rejected.

- [ ] **Step 3: Implement parser and selection**

Add `_PACK_V1_3_KEYS = _PACK_V1_1_KEYS | {"calculation"}`. Parse a `rate_table` object with exact keys and non-empty entry dictionaries. Include the selector fact in required bound keys. Require schema `1.3.0`, calculation maturity, and the built-in calculation evaluator.

Change overlap comparison to include:

```python
and first.maturity is second.maturity
```

Add a maturity filter to selection:

```python
if maturity is not None and pack.maturity is not maturity:
    continue
```

- [ ] **Step 4: Run registry tests and verify GREEN**

Run: `.\.venv\Scripts\python -m pytest tests/test_tax_registry.py tests/test_package_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/open_tax_check/tax_registry.py tests/test_tax_registry.py tests/test_package_contract.py
git commit -m "feat: parse maturity-aware statutory rate packs"
```

---

### Task 3: Staged Screening and Automatic Rate Selection

**Files:**
- Modify: `src/open_tax_check/tax_screening.py`
- Test: `tests/test_tax_screening.py`

**Interfaces:**
- Consumes: maturity-filtered `select_pack_segments`
- Produces: one `TaxResult` after screening then calculation
- Changes: `generic_calculation_evaluator` resolves either confirmed `rate_fact_key` or confirmed rate-table selector

- [ ] **Step 1: Write failing evaluator tests**

```python
def test_confirmed_selector_uses_statutory_rate_without_customer_rate():
    result = screen_tax_items(
        request(
            facts=(
                confirmed("stamp_tax.has_taxable_document", True, "contract-register"),
                confirmed("calc.tax_base", "100000.00", "contract-1"),
                confirmed("stamp_tax.tax_item", "sale_contract", "contract-1"),
            )
        ),
        (stamp_screening_pack(), stamp_calculation_pack()),
        builtin_tax_evaluators(),
        tax_ids=("stamp_tax",),
    )[0]
    assert result.status is TaxStatus.CALCULATED
    assert result.calculation.rate == "0.0003"
    assert result.calculation.amount == "30.00"
    assert result.calculation.rate_selector == "sale_contract"


def test_false_applicability_never_asks_calculation_questions():
    result = screen_tax_items(
        request(facts=(confirmed("stamp_tax.has_taxable_document", False, "register"),)),
        (stamp_screening_pack(), stamp_calculation_pack()),
        builtin_tax_evaluators(),
        tax_ids=("stamp_tax",),
    )[0]
    assert result.status is TaxStatus.NOT_APPLICABLE
    assert result.gaps == ()


def test_explicit_schema_1_2_calculation_pack_remains_runnable_without_screening():
    result = screen_tax_items(
        request(
            confirmed("calc.tax_base", "1000.00", "vat-return"),
            confirmed("calc.rate", "0.13", "official-rate"),
        ),
        (legacy_schema_1_2_calculation_pack(),),
        builtin_tax_evaluators(),
        tax_ids=("vat",),
    )[0]
    assert result.status is TaxStatus.CALCULATED
    assert result.calculation.amount == "130.00"


def test_unknown_statutory_selector_is_blocked():
    result = screen_tax_items(
        TaxCheckRequest(
            PERIOD,
            "CN",
            (
                confirmed(
                    "activity.has_stamp_taxable_instrument_or_securities_transaction",
                    True,
                    "contract-register",
                ),
                confirmed("calc.tax_base", "100000.00", "contract-1"),
                confirmed("stamp_tax.tax_item", "other", "contract-1"),
            ),
        ),
        (stamp_screening_pack(), stamp_calculation_pack()),
        builtin_tax_evaluators(),
        tax_ids=("stamp_tax",),
    )[0]
    assert result.status is TaxStatus.BLOCKED
    assert result.diagnostics == ("unknown_statutory_rate_selector",)
```

Add this exact test helper:

```python
def confirmed(key: str, value: str | bool, evidence_ref: str) -> TaxFact:
    return TaxFact(
        key,
        value,
        FactState.CONFIRMED,
        (evidence_ref,),
        confirmed_by="customer",
        confirmed_at="2026-07-29T00:00:00+00:00",
    )
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_tax_screening.py -q`

Expected: pack overlap or missing selector-rate behavior failures.

- [ ] **Step 3: Implement the two-stage coordinator**

Extract `_evaluate_selected_packs(tax_id, request, selected_packs, evaluators, facts) -> TaxResult`, then make `_screen_one_tax` execute:

```python
screening = select_pack_segments(
    packs,
    tax_id=tax_id,
    jurisdiction=request.jurisdiction,
    period=request.period,
    maturity=PackMaturity.SCREENING,
)
if screening.uncovered_periods and not screening.segments:
    calculation_only = select_pack_segments(
        packs,
        tax_id=tax_id,
        jurisdiction=request.jurisdiction,
        period=request.period,
        maturity=PackMaturity.CALCULATION,
    )
    if not calculation_only.uncovered_periods:
        return _evaluate_selected_packs(
            tax_id, request,
            tuple(segment.pack for segment in calculation_only.segments),
            evaluators, facts,
        )
screening_result = _evaluate_selected_packs(
    tax_id, request, tuple(segment.pack for segment in screening.segments),
    evaluators, facts,
)
if screening_result.status is TaxStatus.NOT_APPLICABLE:
    return screening_result
if screening_result.status is not TaxStatus.NEEDS_EVIDENCE or (
    screening_result.diagnostics != ("calculation_rule_unavailable",)
):
    return screening_result
calculation = select_pack_segments(
    packs,
    tax_id=tax_id,
    jurisdiction=request.jurisdiction,
    period=request.period,
    maturity=PackMaturity.CALCULATION,
)
if calculation.uncovered_periods:
    return screening_result
return _evaluate_selected_packs(
    tax_id, request, tuple(segment.pack for segment in calculation.segments),
    evaluators, facts,
)
```

The calculation evaluator reads the confirmed selector string, performs an exact dictionary lookup, and constructs `TaxCalculationRequest` with the selected `Decimal` rate. Convert an unknown selector into an evaluator outcome validation error that maps to stable diagnostic `unknown_statutory_rate_selector`; do not use a default rate.

- [ ] **Step 4: Run focused screening tests and verify GREEN**

Run: `.\.venv\Scripts\python -m pytest tests/test_tax_screening.py tests/test_tax_workflow.py tests/test_tax_health.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/open_tax_check/tax_screening.py tests/test_tax_screening.py tests/test_tax_workflow.py tests/test_tax_health.py
git commit -m "feat: stage applicability and statutory calculations"
```

---

### Task 4: Formal 2023—2026 Stamp-Tax Packs and Chinese Prompts

**Files:**
- Create: `src/open_tax_check/tax_packs/stamp-tax-cn-2023-08-27.calculation.json`
- Create: `src/open_tax_check/tax_packs/stamp-tax-cn-2023-08-28-2026.calculation.json`
- Modify: `src/open_tax_check/tax_packs/fact-catalog.zh-CN.v1.json`
- Modify: `src/open_tax_check/tax_registry.py`
- Test: `tests/test_builtin_tax_screening_catalog.py`
- Test: `tests/test_tax_prompts.py`

**Interfaces:**
- Produces: built-in calculation coverage for `stamp_tax`
- Produces prompt keys: `calc.tax_base`, `stamp_tax.tax_item`

- [ ] **Step 1: Write failing catalog tests**

```python
def test_builtin_stamp_calculation_rates_match_statutory_table():
    packs = tuple(
        pack for pack in load_builtin_tax_packs()
        if pack.tax_id == "stamp_tax" and pack.maturity is PackMaturity.CALCULATION
    )
    assert [(pack.effective_period.start, pack.effective_period.end) for pack in packs] == [
        (date(2023, 1, 1), date(2023, 8, 27)),
        (date(2023, 8, 28), date(2026, 12, 31)),
    ]
    first = {entry.selector: entry.rate for entry in packs[0].calculation.rate_table.entries}
    second = {entry.selector: entry.rate for entry in packs[1].calculation.rate_table.entries}
    assert first["securities_transaction"] == "0.001"
    assert second["securities_transaction"] == "0.0005"
    assert first["sale_contract"] == second["sale_contract"] == "0.0003"
```

Assert all 16 statutory item codes and their Chinese labels. Assert the source metadata includes the Stamp Tax Law for both packs and announcement 2023 No.39 for the second pack.

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_builtin_tax_screening_catalog.py tests/test_tax_prompts.py -q`

Expected: no calculation packs or prompts exist.

- [ ] **Step 3: Add exact manifests**

Both manifests use schema `1.3.0`, calculation maturity, `calc.tax_base`, and `stamp_tax.tax_item`. Use these rate strings:

```text
loan_contract=0.00005
finance_lease_contract=0.00005
sale_contract=0.0003
work_contract=0.0003
construction_contract=0.0003
transport_contract=0.0003
technology_contract=0.0003
lease_contract=0.001
custody_contract=0.001
warehousing_contract=0.001
property_insurance_contract=0.001
land_use_right_grant=0.0005
land_or_building_transfer=0.0005
equity_transfer=0.0005
intellectual_property_transfer=0.0003
business_books=0.00025
```

Add `securities_transaction=0.001` to the first period and `0.0005` to the second. The second pack cites both the law and `财政部 税务总局公告2023年第39号`.

- [ ] **Step 4: Add Chinese prompt choices**

`stamp_tax.tax_item` is a choice prompt with the same stable codes and Chinese labels. Help text tells the host Agent to ask the customer to choose based on the underlying taxable instrument and not classify it autonomously.

- [ ] **Step 5: Run catalog tests and verify GREEN**

Run: `.\.venv\Scripts\python -m pytest tests/test_builtin_tax_screening_catalog.py tests/test_tax_prompts.py tests/test_tax_registry.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/open_tax_check/tax_packs src/open_tax_check/tax_registry.py tests/test_builtin_tax_screening_catalog.py tests/test_tax_prompts.py
git commit -m "feat: bundle formal stamp tax calculation packs"
```

---

### Task 5: Report, Snapshot, CLI, and Host-Agent Handoff

**Files:**
- Modify: `src/open_tax_check/tax_reporting.py`
- Modify: `src/open_tax_check/agent_orchestration.py`
- Modify: `src/open_tax_check/cli.py`
- Test: `tests/test_tax_health_reporting.py`
- Test: `tests/test_agent_orchestration.py`
- Test: `tests/test_tax_health_cli.py`

**Interfaces:**
- Consumes: selector fields in `CalculationRecord`
- Produces: Chinese report line `适用税目：买卖合同`
- Preserves: machine snapshot selector code `sale_contract`

- [ ] **Step 1: Write failing customer-boundary tests**

```python
def test_report_localizes_statutory_rate_selector():
    report = render_tax_health_report(calculated_stamp_snapshot())
    assert "适用税目：买卖合同" in report
    assert "sale_contract" not in report
    assert "适用税率：0.03%" in report


def test_snapshot_preserves_selector_code_and_label():
    payload = tax_snapshot_payload(calculated_stamp_snapshot())
    calculation = payload["results"][0]["calculation"]
    assert calculation["rate_selector"] == "sale_contract"
    assert calculation["rate_selector_label_zh"] == "买卖合同"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_tax_health_reporting.py tests/test_agent_orchestration.py tests/test_tax_health_cli.py -q`

Expected: report omits the selected statutory item.

- [ ] **Step 3: Implement allowlisted display**

Add the selected Chinese label to Markdown/DOCX/PDF through the shared Markdown renderer. Do not render the selector code, pack ID, evaluator ID, or raw diagnostic in customer output. Keep both code and label in the JSON snapshot and common host task payload.

- [ ] **Step 4: Run focused integration tests and verify GREEN**

Run: `.\.venv\Scripts\python -m pytest tests/test_tax_health_reporting.py tests/test_agent_orchestration.py tests/test_tax_health_cli.py tests/test_skill_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/open_tax_check/tax_reporting.py src/open_tax_check/agent_orchestration.py src/open_tax_check/cli.py tests/test_tax_health_reporting.py tests/test_agent_orchestration.py tests/test_tax_health_cli.py
git commit -m "feat: report statutory stamp tax calculations"
```

---

### Task 6: Public Documentation and Release Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/calculation-packs.md`
- Modify: `docs/roadmap.md`
- Modify: `skills/open-tax-check/SKILL.md`
- Modify: `skills/open-tax-check/references/methodology.md`
- Modify: `scripts/validate_release.py`
- Test: `tests/test_public_repo_contract.py`
- Test: `tests/test_release_validation.py`

**Interfaces:**
- Documents: built-in formal stamp-tax calculation and deed-tax local-policy boundary
- Verifies: every schema `1.3.0` source URL is official and all bundled periods are covered

- [ ] **Step 1: Write failing documentation and release tests**

Assert public docs contain:

```text
印花税法定税率表
2023年8月28日
契税具体适用税率必须匹配省级政策
不自动判断合同税目
```

Assert strict release validation recognizes schema `1.3.0`, checks official source suffixes, rejects unknown selector duplicates, and includes both stamp-tax calculation manifests in wheel and sdist.

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_public_repo_contract.py tests/test_release_validation.py -q`

Expected: missing public claims and release checks.

- [ ] **Step 3: Update documentation and validator**

State exactly:

- ordinary stamp-tax document rates are law-backed and built in;
- securities transactions use 0.1% through 2023-08-27 and 0.05% from 2023-08-28 under announcement 2023 No.39;
- incentives are not applied without explicit eligibility evidence;
- deed tax remains parameterized by province;
- the host Agent asks the customer to select the tax item and never invents classification.

- [ ] **Step 4: Run full verification**

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m build
.\.venv\Scripts\python scripts/validate_release.py --strict
```

Expected: all commands exit 0; OCR smoke remains explicitly skipped in the ordinary source suite because its separate real-runtime evidence is already recorded.

- [ ] **Step 5: Verify installed wheel**

Create an isolated temporary Python 3.12 environment, install the newly built wheel, load built-in packs, and assert:

```python
stamp = [p for p in load_builtin_tax_packs() if p.tax_id == "stamp_tax"]
assert sum(p.maturity.value == "calculation" for p in stamp) == 2
```

Run one fictional confirmed sale-contract calculation and assert amount `30.00` for base `100000.00`.

- [ ] **Step 6: Commit**

```powershell
git add README.md docs skills scripts tests
git commit -m "docs: publish statutory stamp tax workflow"
```

- [ ] **Step 7: Final branch audit**

```powershell
git status --short
git log -1 --oneline
git rev-list --count '@{upstream}..HEAD'
```

Expected: clean worktree. Report evidence and remaining statutory packs truthfully; do not push or merge.
