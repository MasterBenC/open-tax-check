# Customer Onboarding and Model Guidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every AI agent the same approved Chinese opening, five-field business profile intake, progressive material list, and model guidance.

**Architecture:** Add a versioned customer-profile and case-guide module, expose two CLI commands, and rewrite the Skill to display machine-generated guide and handoff contracts. Keep model advice in a focused reference file; model selection never alters tax calculations.

**Tech Stack:** Python dataclasses, JSON, pathlib, existing case state, pytest/unittest, Markdown Skill references.

## Global Constraints

- Use “AI 智能体” in customer-facing Chinese.
- Ask for entity name, province/city, primary business, review period, and VAT taxpayer type.
- Materials may be supplied progressively.
- Formal period is 2023-01-01 through 2026-12-31; never silently truncate another period.
- Recommended models: DeepSeek V4, Hunyuan 3, GLM-5.2, GPT-5.6, Claude 4.8+, Qwen 3.7 Max, Doubao Seed 2.0 Pro.
- `qwen3.7-plus` is a balanced-cost alternative; preview models are not default recommendations.
- No multi-store package generation is included in this plan.

---

### Task 1: Add Customer Profile and Opening Guide

**Files:**
- Create: `src/open_tax_check/customer_guide.py`
- Create: `tests/test_customer_guide.py`
- Modify: `src/open_tax_check/__init__.py`

**Interfaces:**
- Produces: `start_case_from_profile(case_dir: Path, profile: Mapping[str, object]) -> dict[str, object]`
- Produces: `build_case_guide(case_dir: Path) -> dict[str, object]`
- Schemas: `customer-profile/v1`, `case-guide/v1`.

- [ ] **Step 1: Write failing profile and opening tests**

```python
def test_missing_case_returns_approved_opening(tmp_path):
    guide = build_case_guide(tmp_path / "case")
    assert guide["stage"] == "welcome"
    assert "税务健康检查 AI 智能体" in guide["message_zh"]
    assert "资料不需要一次准备齐" in guide["message_zh"]
    assert [item["field"] for item in guide["profile_questions"]] == [
        "entity_name", "province_city", "primary_business", "period", "vat_taxpayer_type"
    ]
```

- [ ] **Step 2: Write failing validation tests**

Validate the three VAT values `general`, `small_scale`, and `unknown`; reject blank entity, region, business, reversed dates, and dates outside the formal period with stable codes.

- [ ] **Step 3: Run RED tests**

Run: `python -m pytest tests/test_customer_guide.py -q`

Expected: FAIL because the module is absent.

- [ ] **Step 4: Implement the immutable contracts**

```python
@dataclass(frozen=True, slots=True)
class CustomerProfile:
    entity_name: str
    province_city: str
    primary_business: str
    period_start: date
    period_end: date
    vat_taxpayer_type: Literal["general", "small_scale", "unknown"]
```

Validate before calling `init_case`, then atomically write `work/customer-profile.json`. A missing case returns `welcome`; an initialized case without a valid profile returns `awaiting_business_profile`.

- [ ] **Step 5: Run GREEN tests and commit**

Run: `python -m pytest tests/test_customer_guide.py tests/test_case.py -q`

Expected: PASS.

```bash
git add src/open_tax_check/customer_guide.py src/open_tax_check/__init__.py tests/test_customer_guide.py
git commit -m "feat: add customer opening guide"
```

### Task 2: Add Progressive Materials, State Guidance, and CLI

**Files:**
- Modify: `src/open_tax_check/customer_guide.py`
- Modify: `src/open_tax_check/cli.py`
- Create: `tests/test_customer_guide_cli.py`
- Modify: `tests/test_customer_guide.py`

**Interfaces:**
- CLI: `opentaxcheck case-guide --case CASE`
- CLI: `opentaxcheck case-start --case CASE --profile PROFILE.json`

- [ ] **Step 1: Write failing three-group material tests**

```python
def test_profile_creates_progressive_material_groups(case_with_profile):
    guide = build_case_guide(case_with_profile)
    assert [item["group_id"] for item in guide["document_groups"]] == [
        "minimum_to_start", "recommended_if_available", "conditional_business_materials"
    ]
    assert "序时账或明细账" in guide["document_groups"][0]["items_zh"]
    assert "契税" not in guide["document_groups"][0]["items_zh"]
```

- [ ] **Step 2: Write failing CLI round-trip tests**

Run `case-start`, read its JSON, then run `case-guide`; assert both return `awaiting_documents` and the same profile digest.

- [ ] **Step 3: Run RED tests**

Run: `python -m pytest tests/test_customer_guide.py tests/test_customer_guide_cli.py -q`

Expected: FAIL because groups and CLI commands are absent.

- [ ] **Step 4: Implement exact stage mapping**

```python
CASE_TO_GUIDE_STAGE = {
    "waiting_for_materials": "awaiting_documents",
    "waiting_for_extraction": "inventory_in_progress",
    "ready_for_self_check": "check_in_progress",
    "waiting_for_customer_confirmation": "awaiting_customer_input",
    "completed_with_limitations": "draft_report_ready",
    "completed": "completed",
    "blocked_data_quality": "blocked",
    "failed": "blocked",
}
```

Conditional materials depend on profile and confirmed facts but never establish tax applicability. Add privacy and disclaimer fields to every guide response.

- [ ] **Step 5: Implement CLI handlers, run GREEN, and commit**

Run: `python -m pytest tests/test_customer_guide.py tests/test_customer_guide_cli.py tests/test_case_orchestration.py -q`

Expected: PASS.

```bash
git add src/open_tax_check/customer_guide.py src/open_tax_check/cli.py tests/test_customer_guide.py tests/test_customer_guide_cli.py
git commit -m "feat: expose progressive customer intake"
```

### Task 3: Add Model Guidance and Rewrite the Skill Entry Flow

**Files:**
- Create: `skills/open-tax-check/references/model-recommendations.md`
- Modify: `skills/open-tax-check/SKILL.md`
- Modify: `skills/open-tax-check/references/host-integrations.md`
- Modify: `skills/open-tax-check/references/methodology.md`
- Modify: `tests/test_skill_contract.py`

**Interfaces:**
- Requires hosts to display `case-guide/v1` and `tax-customer-handoff/v1` rather than inventing their own opening or completion message.

- [ ] **Step 1: Write failing Skill contract tests**

```python
def test_skill_contains_opening_model_and_handoff_contracts():
    skill = SKILL.read_text(encoding="utf-8")
    models = (SKILL / "references/model-recommendations.md").read_text(encoding="utf-8")
    assert "税务健康检查 AI 智能体" in skill
    assert "资料不需要一次准备齐" in skill
    assert "case-guide" in skill and "case-start" in skill
    for name in ("DeepSeek V4", "腾讯混元 3", "GLM-5.2", "GPT-5.6", "Claude 4.8", "Qwen 3.7 Max", "Doubao Seed 2.0 Pro"):
        assert name in models
    assert "qwen3.8-max-preview" not in models
```

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest tests/test_skill_contract.py -q`

Expected: FAIL because the opening and model reference are absent.

- [ ] **Step 3: Rewrite the Skill entry flow**

Place customer opening and profile intake before developer commands. Require the AI agent to display guide fields, use `case-confirm` for customer answers, display the machine handoff, and never author a stronger conclusion. Document capability fallback: recommended models may use `deep`; unknown capable models use `assist`; missing structured JSON or tools uses `off`.

- [ ] **Step 4: Run GREEN tests and commit**

Run: `python -m pytest tests/test_skill_contract.py tests/test_host_replays.py -q`

Expected: PASS.

```bash
git add skills/open-tax-check/SKILL.md skills/open-tax-check/references/model-recommendations.md skills/open-tax-check/references/host-integrations.md skills/open-tax-check/references/methodology.md tests/test_skill_contract.py
git commit -m "docs: add customer-first skill guidance"
```

### Task 4: Verify the Complete Customer Journey

**Files:**
- Modify: `scripts/run_fictional_example.py`
- Modify: `tests/test_fictional_example.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Verifies opening, profile, materials, processing, customer questions, confirmations, report, handoff, and delivery in one case.

- [ ] **Step 1: Add failing journey assertions**

Require `welcome -> awaiting_documents -> check_in_progress -> awaiting_customer_input -> draft_report_ready/completed`, no repeated opening after upload, and identical revisions across report, handoff, snapshot, and delivery.

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest tests/test_fictional_example.py -q`

Expected: FAIL until the example uses the new guide entry points.

- [ ] **Step 3: Update the example and user documentation**

Show the natural-language opening, the five profile fields, progressive materials, recommended models, and the rule that model choice cannot alter calculations.

- [ ] **Step 4: Run full regression and release gates**

Run: `python -m pytest -q`

Run: `python -m build`

Run: `python scripts/validate_release.py --strict --require-distributions`

Expected: all tests, build, and strict validation pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_fictional_example.py tests/test_fictional_example.py README.md CHANGELOG.md
git commit -m "feat: complete guided customer journey"
```
