# 经营场景税务风险地图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立由 Skill 动态资料引导和正式中文 PDF 共用的经营场景目录与覆盖快照。

**Architecture:** 新增独立 `scenario_map` 领域模块，静态目录只保存场景、税种、检查编号和资料关联，运行时汇总函数把案件事实、检查结果和资料状态转换为版本化覆盖快照。`customer_guide`、报告视图和 HTML/PDF 只消费该快照，不各自维护统计逻辑。

**Tech Stack:** Python 3.11–3.13、dataclasses、现有 TaxCheckSnapshot/TaxCrossCheckRun、pytest、Playwright Chromium、pypdf、离线 HTML/CSS。

## Global Constraints

- 第一版固定五类场景：收入与流转、成本费用、资产与不动产、合同及特殊交易、人员、股东及关联事项。
- 客户可见状态固定为 `executed_clear`、`executed_risk`、`awaiting_evidence`、`received_unparsed`、`not_applicable`、`not_performed`。
- `skipped`、资料缺失、资料未解析和未执行不得计入已执行或已通过。
- 借贷不平衡仍是阻断项。
- AI 不得选择税目、税率、科目分类、地方政策或税务结论，也不得替客户确认经营事实。
- 正式客户交付只提供中文 PDF，不回退 Word。
- 初始目录必须关联 `BOOK-FS-REV-001`、`FS-VAT-SALES-001`、`BOOK-VAT-OUTPUT-001`、`FS-CIT-REV-PROFIT-001`、`BOOK-CIT-BE-001`。
- 一个检查可关联多个场景，但全局总数按稳定检查编号去重。
- 外部清单中的未核验条目只能是询问型候选，不得产生自动结论。

---

## File Structure

- Create `src/open_tax_check/scenario_map.py`: 目录数据类型、固定五场景目录、状态转换、覆盖快照和序列化。
- Create `tests/test_scenario_map.py`: 目录契约、去重、状态转换、资料状态和失败行为。
- Modify `src/open_tax_check/customer_guide.py`: 将场景事实和目录转换为动态资料组，不作税务适用判断。
- Modify `tests/test_customer_guide.py`: 场景触发、未触发、未确认事实和兼容行为。
- Modify `src/open_tax_check/report_view.py`: 在 v3 视图中加入不可变的场景覆盖数据。
- Modify `tests/test_report_view.py`: 报告视图统计和同修订一致性。
- Modify `src/open_tax_check/html_report.py`: 新增检查地图章节、目录项和安全转义。
- Modify `src/open_tax_check/report_assets/report.css`: A4 表格、状态标识和灰度可辨识样式。
- Modify `tests/test_html_report.py`: HTML 结构、数字、文本转义和离线资源。
- Modify `tests/test_pdf_report.py`: PDF 中文文本、目录书签、分页和稳定检查编号。
- Modify `src/open_tax_check/tax_reporting.py`: 机器快照包含同源场景覆盖 payload。
- Modify `src/open_tax_check/__init__.py`: 导出稳定公共接口。
- Modify `skills/open-tax-check/SKILL.md`: 更新开场、场景化资料引导和状态口径。
- Modify `README.md`, `CHANGELOG.md`, `docs/qa/customer-report-v3-checklist.md`: 用户说明、版本记录和验收证据。

### Task 1: 固定场景目录契约

**Files:**
- Create: `src/open_tax_check/scenario_map.py`
- Create: `tests/test_scenario_map.py`
- Modify: `src/open_tax_check/__init__.py`

**Interfaces:**
- Produces: `SCENARIO_CATALOG_SCHEMA_VERSION = "tax-scenario-catalog/v1"`.
- Produces: `ScenarioDefinition(scenario_id, name_zh, description_zh, tax_ids, check_ids, trigger_fact_keys, material_items_zh, capability)`.
- Produces: `scenario_catalog() -> tuple[ScenarioDefinition, ...]`.
- Produces: `scenario_catalog_payload() -> dict[str, object]`.

- [ ] **Step 1: Write the failing catalog tests**

```python
def test_catalog_has_five_stable_unique_scenarios():
    catalog = scenario_catalog()
    assert [item.scenario_id for item in catalog] == [
        "revenue_and_turnover", "costs_and_expenses", "assets_and_real_estate",
        "contracts_and_special_transactions", "people_shareholders_and_related_parties",
    ]
    assert len({item.scenario_id for item in catalog}) == 5

def test_catalog_contains_all_fixed_cross_checks():
    check_ids = {check_id for item in scenario_catalog() for check_id in item.check_ids}
    assert {
        "BOOK-FS-REV-001", "FS-VAT-SALES-001", "BOOK-VAT-OUTPUT-001",
        "FS-CIT-REV-PROFIT-001", "BOOK-CIT-BE-001",
    } <= check_ids
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_scenario_map.py -q`  
Expected: FAIL because `open_tax_check.scenario_map` does not exist.

- [ ] **Step 3: Implement the immutable catalog**

Create frozen dataclasses, validate unique IDs, non-empty Chinese labels, known capability values `deterministic|question_candidate`, and return a fixed tuple. Associate the five current cross-checks with `revenue_and_turnover` and `costs_and_expenses`; map all `TAX_DEFINITIONS` tax IDs across at least one scenario without copying rates or formulas.

- [ ] **Step 4: Export and verify GREEN**

Export `ScenarioDefinition`, `scenario_catalog`, and `scenario_catalog_payload` from `open_tax_check.__init__`.  
Run: `python -m pytest tests/test_scenario_map.py tests/test_public_repo_contract.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/open_tax_check/scenario_map.py src/open_tax_check/__init__.py tests/test_scenario_map.py
git commit -m "feat: add scenario risk map catalog"
```

### Task 2: 确定性场景覆盖快照

**Files:**
- Modify: `src/open_tax_check/scenario_map.py`
- Modify: `tests/test_scenario_map.py`

**Interfaces:**
- Produces: `ScenarioCheckState` literal with the six Global Constraints states.
- Produces: `ScenarioCoverageItem(scenario_id, name_zh, total_count, executed_clear_count, executed_risk_count, awaiting_evidence_count, received_unparsed_count, not_applicable_count, not_performed_count, check_ids)`.
- Produces: `ScenarioCoverageSnapshot(schema_version, catalog_version, revision, items, unique_check_count)`.
- Produces: `build_scenario_coverage(*, revision: int, check_states: Mapping[str, str], material_states: Mapping[str, str], confirmed_facts: Mapping[str, object]) -> ScenarioCoverageSnapshot`.
- Produces: `scenario_coverage_payload(snapshot) -> dict[str, object]`.

- [ ] **Step 1: Write failing state and deduplication tests**

```python
def test_skipped_and_unparsed_are_not_executed():
    coverage = build_scenario_coverage(
        revision=3,
        check_states={"BOOK-FS-REV-001": "skipped", "FS-VAT-SALES-001": "received_unparsed"},
        material_states={},
        confirmed_facts={},
    )
    revenue = next(item for item in coverage.items if item.scenario_id == "revenue_and_turnover")
    assert revenue.executed_clear_count == 0
    assert revenue.not_performed_count >= 1
    assert revenue.received_unparsed_count == 1

def test_global_check_total_deduplicates_multi_scenario_checks():
    coverage = build_scenario_coverage(
        revision=1, check_states={"FS-CIT-REV-PROFIT-001": "matched"},
        material_states={}, confirmed_facts={},
    )
    assert coverage.unique_check_count == len(set(
        check_id for item in coverage.items for check_id in item.check_ids
    ))
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_scenario_map.py -q`  
Expected: FAIL because coverage interfaces are missing.

- [ ] **Step 3: Implement explicit state conversion**

Use a closed mapping table: `matched|within_tolerance|not_triggered -> executed_clear`, `difference_found|suspected|confirmed -> executed_risk`, `needs_evidence|needs_local_policy|blocked -> awaiting_evidence`, `received_pending|extraction_partial|present_unparsed -> received_unparsed`, `not_applicable -> not_applicable`, and `skipped|not_performed -> not_performed`. Reject unknown states with `ValueError("unknown_scenario_check_state")`; never default unknown input to clear.

- [ ] **Step 4: Verify GREEN and invariants**

Add assertions that every item’s six counts sum to `total_count`, revision must be a positive non-boolean integer, and unknown states fail closed.  
Run: `python -m pytest tests/test_scenario_map.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/open_tax_check/scenario_map.py tests/test_scenario_map.py
git commit -m "feat: build deterministic scenario coverage"
```

### Task 3: 场景化 Skill 引导与动态资料

**Files:**
- Modify: `src/open_tax_check/customer_guide.py`
- Modify: `tests/test_customer_guide.py`
- Modify: `tests/test_customer_guide_cli.py`

**Interfaces:**
- Consumes: `scenario_catalog_payload()`.
- Produces: `case-guide/v2` with `scenario_questions`, `active_scenarios`, and scenario-linked `document_groups`.
- Produces: `SCENARIO_FACT_KEYS = ("has_real_estate_activity", "has_taxable_contracts", "has_employees_or_shareholder_flows", "has_related_party_or_restructuring_activity")`.

- [ ] **Step 1: Write failing dynamic-guide tests**

```python
def test_confirmed_false_scenario_does_not_emit_conditional_materials(case_with_profile):
    write_json(case_with_profile / "work/scenario-facts.json", {
        "schema_version": "tax-scenario-facts/v1",
        "facts": {"has_real_estate_activity": False},
    })
    guide = build_case_guide(case_with_profile)
    assert "assets_and_real_estate" not in guide["active_scenarios"]
    assert all(group.get("scenario_id") != "assets_and_real_estate"
               for group in guide["document_groups"])

def test_unknown_fact_is_asked_but_not_treated_as_not_applicable(case_with_profile):
    guide = build_case_guide(case_with_profile)
    question = next(item for item in guide["scenario_questions"]
                    if item["fact_key"] == "has_real_estate_activity")
    assert question["answer_type"] == "boolean"
    assert question["question_zh"]
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_customer_guide.py tests/test_customer_guide_cli.py -q`  
Expected: FAIL because the guide has no scenario fields.

- [ ] **Step 3: Implement v2 guide without inference**

Keep the current five profile questions. Read only source-safe `work/scenario-facts.json`; accept booleans explicitly confirmed by a human/customer envelope, preserve unknown facts as questions, always keep the revenue/cost baseline active, and add materials only for confirmed-true conditional scenarios. Do not infer facts from `primary_business`.

- [ ] **Step 4: Verify guide and CLI output**

Run: `python -m pytest tests/test_customer_guide.py tests/test_customer_guide_cli.py tests/test_skill_contract.py -q`  
Expected: PASS with exact Chinese fields and no duplicate materials.

- [ ] **Step 5: Commit**

```text
git add src/open_tax_check/customer_guide.py tests/test_customer_guide.py tests/test_customer_guide_cli.py
git commit -m "feat: guide customers by operating scenario"
```

### Task 4: 报告视图与机器快照同源

**Files:**
- Modify: `src/open_tax_check/report_view.py`
- Modify: `src/open_tax_check/tax_reporting.py`
- Modify: `tests/test_report_view.py`
- Modify: `tests/test_tax_health_reporting.py`

**Interfaces:**
- Consumes: `build_scenario_coverage(...)` and `scenario_coverage_payload(...)`.
- Produces: `ReportViewV3.scenario_coverage: ScenarioCoverageSnapshot`.
- Produces: machine snapshot key `scenario_coverage` with schema `tax-scenario-coverage/v1`.

- [ ] **Step 1: Write failing view/snapshot consistency test**

```python
def test_report_view_and_machine_snapshot_share_scenario_counts(snapshot_with_every_status):
    view = build_report_view_v3(snapshot_with_every_status, revision=4)
    payload = tax_snapshot_payload(snapshot_with_every_status, revision=4)
    assert payload["scenario_coverage"] == scenario_coverage_payload(view.scenario_coverage)
    assert view.scenario_coverage.revision == 4
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_report_view.py tests/test_tax_health_reporting.py -q`  
Expected: FAIL because `ReportViewV3` lacks `scenario_coverage`.

- [ ] **Step 3: Build coverage once from current evidence**

Add a private adapter that converts current `TaxStatus`, cross-check status/resolution, material statuses and confirmed facts into the closed inputs accepted by `build_scenario_coverage`. Reuse its output for both report view and machine payload. Unknown or contradictory states must raise and prevent delivery.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_report_view.py tests/test_tax_health_reporting.py tests/test_tax_health.py -q`  
Expected: PASS; existing report schema remains `tax-health-report/v3`.

- [ ] **Step 5: Commit**

```text
git add src/open_tax_check/report_view.py src/open_tax_check/tax_reporting.py tests/test_report_view.py tests/test_tax_health_reporting.py
git commit -m "feat: bind reports to scenario coverage"
```

### Task 5: HTML/PDF 税务检查地图

**Files:**
- Modify: `src/open_tax_check/html_report.py`
- Modify: `src/open_tax_check/report_assets/report.css`
- Modify: `tests/test_html_report.py`
- Modify: `tests/test_pdf_report.py`

**Interfaces:**
- Consumes: `ReportViewV3.scenario_coverage`.
- Produces: HTML section `#scenario-map`, table rows keyed by `data-scenario-id`, and a matching TOC entry.

- [ ] **Step 1: Write failing HTML tests**

```python
def test_html_contains_scenario_map_with_safe_counts(report_view):
    html = render_report_html(report_view)
    assert 'id="scenario-map"' in html
    assert 'data-scenario-id="revenue_and_turnover"' in html
    assert "待补资料不计入已执行或已通过" in html
    assert html.index('href="#scenario-map"') < html.index('href="#priority-actions"')
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_html_report.py -q`  
Expected: FAIL because the section is absent.

- [ ] **Step 3: Implement semantic HTML and print-safe CSS**

Render the map after risk overview and before priority actions. Include columns for scene, total, current-clear, needs-attention, pending evidence, received-unparsed, not-applicable and not-performed. Escape every label, use repeated table headers, avoid row splitting, and distinguish statuses using text plus border/pattern so grayscale remains readable.

- [ ] **Step 4: Add and run PDF structure tests**

Assert extracted PDF contains “本次税务检查地图”, all five Chinese scene names, the state disclaimer and the five stable cross-check IDs in the appendix. Assert the bookmark order places the map before actions and the PDF remains unencrypted.  
Run: `python -m pytest tests/test_html_report.py tests/test_pdf_report.py -q`  
Expected: PASS with configured Chromium.

- [ ] **Step 5: Commit**

```text
git add src/open_tax_check/html_report.py src/open_tax_check/report_assets/report.css tests/test_html_report.py tests/test_pdf_report.py
git commit -m "feat: render scenario map in customer pdf"
```

### Task 6: Skill、说明和发行契约

**Files:**
- Modify: `skills/open-tax-check/SKILL.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_skill_contract.py`
- Modify: `tests/test_public_repo_contract.py`

**Interfaces:**
- Documents: case-guide/v2 fields and the same six states.
- Preserves: PDF-only delivery and host AI authority boundaries.

- [ ] **Step 1: Write failing documentation-contract tests**

```python
def test_skill_describes_scenario_map_without_claiming_no_risk():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "经营场景" in skill
    assert "待补资料不计入已执行或已通过" in skill
    assert "AI 智能体不得替客户确认经营事实" in skill
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_skill_contract.py tests/test_public_repo_contract.py -q`  
Expected: FAIL on the new required copy.

- [ ] **Step 3: Update customer-facing instructions**

Document the five scenes, dynamic questions, the distinction between navigation and tax applicability, all six states, current five cross-check IDs, upgrade behavior, and the fact that unverified external checklist items remain question candidates. Keep the existing welcome sentence, model recommendations, OCR guidance and PDF-only installation instructions.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_skill_contract.py tests/test_public_repo_contract.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add skills/open-tax-check/SKILL.md README.md CHANGELOG.md tests/test_skill_contract.py tests/test_public_repo_contract.py
git commit -m "docs: explain scenario based tax checks"
```

### Task 7: 完整验收、视觉检查与分支收尾

**Files:**
- Modify: `docs/qa/customer-report-v3-checklist.md`
- Modify only if a failing test proves a defect: files owned by Tasks 1–6.

**Interfaces:**
- Produces: fresh automated, build, isolated-wheel and visual evidence.

- [ ] **Step 1: Run focused feature regression**

Run:

```text
python -m pytest tests/test_scenario_map.py tests/test_customer_guide.py tests/test_customer_guide_cli.py tests/test_report_view.py tests/test_tax_health_reporting.py tests/test_html_report.py tests/test_pdf_report.py tests/test_skill_contract.py tests/test_public_repo_contract.py -q
```

Expected: all collected tests pass; skipped tests are recorded separately and never reported as passed.

- [ ] **Step 2: Generate four PDF fixtures**

Use existing report test factories to generate normal, long-text, no-highlighted-risk, and many-material-gaps reports. Render every page to PNG and inspect font consistency, clipping, overlaps, table continuation, TOC page numbers, blank pages and grayscale distinction.

- [ ] **Step 3: Run full local verification**

Run:

```text
python -m pytest -q
python -m build
python scripts/validate_release.py --strict
python scripts/validate_installed_wheel.py --dist dist
```

Expected: all tests pass; wheel/sdist, strict release validation and isolated installed-wheel Chromium PDF smoke pass.

- [ ] **Step 4: Record QA evidence**

Append the exact date, commands, pass/skip/warning counts, generated PDF paths, page counts and visual findings to `docs/qa/customer-report-v3-checklist.md`. Do not infer macOS/Linux results from Windows; cite only fresh remote CI evidence if available.

- [ ] **Step 5: Run final audits**

Run:

```text
git diff --check
git status --short
rg -n "TBD|TODO|include-docx|tax_fact_mismatch" src tests skills docs README.md CHANGELOG.md
git log --oneline origin/main..HEAD
```

Review that no customer Word delivery returned, borrowing remains balanced, skipped is never passed, report and machine snapshot counts match, and all customer text is escaped.

- [ ] **Step 6: Commit QA evidence**

```text
git add docs/qa/customer-report-v3-checklist.md
git commit -m "docs: validate scenario tax risk map"
```

- [ ] **Step 7: Request independent review and finish branch**

Use `superpowers:requesting-code-review`, fix all Critical/Important findings with a failing test first, repeat affected verification, then use `superpowers:verification-before-completion` and `superpowers:finishing-a-development-branch`. Stop at a merge-ready branch; do not push, merge, tag or release without separate user authorization.
