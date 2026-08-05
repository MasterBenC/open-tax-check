# High-Frequency Tax Cross-Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five deterministic, source-traceable ledger/financial-statement/tax-return cross-checks, with bounded AI follow-up and customer-safe PDF presentation.

**Architecture:** Keep accounting and structured-document quality gates in their existing modules, add a source-bound `account-classification/v1` review contract, and execute monetary comparisons in a new `tax_cross_checks.py` domain module. Persist one immutable `tax-cross-checks/v1` result per run; agent, report, workpaper, and delivery layers consume that result without recalculating amounts.

**Tech Stack:** Python 3.11-3.13, frozen dataclasses, `Decimal`, JSON/JSONL, pytest, Playwright Chromium, pypdf.

## Global Constraints

- The five checks are exactly `BOOK-FS-REV-001`, `FS-VAT-SALES-001`, `BOOK-VAT-OUTPUT-001`, `FS-CIT-REV-PROFIT-001`, and `BOOK-CIT-BE-001`.
- Whole-run status is only `completed` or `blocked_data_quality`; per-check status is only `matched`, `within_tolerance`, `difference_found`, or `not_performed`.
- A difference is within tolerance only when absolute difference is `<= 10.00` and relative difference is `<= 0.0001`; AI, CLI input, and customer confirmations cannot override either threshold.
- All monetary arithmetic uses `Decimal`; machine JSON serializes amounts and ratios as canonical strings, never binary floats.
- Unbalanced journal/trial balance, entity/period/currency/unit mismatch, invalid structured documents, and failed statement identities block all cross-checks.
- Valid sources with differing amounts create risk items and do not block report generation.
- Ledger aggregation uses only explicitly confirmed, source-bound controlled account classifications; AI may propose questions but may not confirm classifications.
- One host interaction exposes at most three deduplicated questions.
- Customer explanations require category, non-empty Chinese explanation, confirmer, timestamp, and verifiable evidence; text alone never closes a risk, and `closed` requires a complete amount bridge.
- Existing `business_entertainment_adjustment(actual_expense, revenue)` remains the sole business-entertainment formula implementation.
- Existing debit-equals-credit invariants remain unchanged.
- Customer-facing output remains Chinese PDF only; no Word fallback is introduced.
- Phase 1 excludes payroll, R&D expense, related-party transactions, asset losses, deemed-sale auto-determination, and local-policy inference.

## File Map

- Create `src/open_tax_check/account_classification.py`: strict immutable classification contract, source binding, validation, serialization, and ledger aggregation.
- Modify `src/open_tax_check/reviews.py`: safe persistence and loading of confirmed `account-classification/v1` decisions.
- Modify `src/open_tax_check/cli.py`: explicit human confirmation command for account classifications.
- Create `src/open_tax_check/tax_cross_checks.py`: public contracts, tolerance calculation, five checks, questions, explanations, amount bridges, and deterministic rerun.
- Modify `src/open_tax_check/structured_reconciliation.py`: retain context and internal-identity quality gates while removing monetary tax-fact equality from blocking logic.
- Modify `src/open_tax_check/pipeline.py`: load frozen inputs, apply quality gate, execute and atomically persist cross-check results.
- Modify `src/open_tax_check/agent_orchestration.py`: expose at most three cross-check questions and accept structured explanation confirmations without changing machine thresholds.
- Modify `src/open_tax_check/report_view.py`: translate persisted cross-check differences into Chinese customer risk items and appendix checks.
- Modify `src/open_tax_check/workpaper.py` and `src/open_tax_check/delivery.py`: include the immutable cross-check result and its hashes in audit artifacts.
- Modify report templates/styles only where needed to render cross-check risk fields without exposing internal enum names.
- Add focused tests plus fictional end-to-end cases; update package exports, user documentation, QA evidence, and changelog.

---

### Task 1: Source-Bound Controlled Account Classifications

**Files:**
- Create: `src/open_tax_check/account_classification.py`
- Modify: `src/open_tax_check/reviews.py`
- Modify: `src/open_tax_check/cli.py`
- Modify: `src/open_tax_check/__init__.py`
- Test: `tests/test_account_classification.py`
- Test: `tests/test_reviews.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: normalized ledger rows with `account_code`, `account_name`, `debit`, `credit`, and `field_lineage`; current source manifest and confirmed field-mapping fingerprints.
- Produces: `AccountClassification`, `AccountClassificationReview`, `parse_account_classification_review(payload)`, `confirm_account_classifications(case_dir, payload)`, `load_active_account_classifications(case_dir, source_manifest_sha256)`, and `aggregate_classified_amount(rows, review, category)`.

- [ ] **Step 1: Write strict contract and aggregation tests**

```python
def test_review_is_bound_to_manifest_mapping_and_unique_accounts():
    review = parse_account_classification_review(valid_review_payload())
    assert review.schema_version == "account-classification/v1"
    assert review.source_manifest_sha256 == "a" * 64
    assert review.mapping_fingerprint == "b" * 64
    assert review.classifications[0].category == "main_business_revenue"

@pytest.mark.parametrize("mutation", ["duplicate_account", "unknown_category", "bad_hash", "ai_reviewer"])
def test_invalid_or_non_human_classification_is_rejected(mutation):
    with pytest.raises(ValueError, match="invalid_account_classification"):
        parse_account_classification_review(mutated_review_payload(mutation))

def test_aggregate_uses_only_confirmed_exact_account_identity():
    amount, refs = aggregate_classified_amount(rows(), review(), "vat_output_tax")
    assert amount == Decimal("130.00")
    assert refs == (AccountSourceRef("222101", "应交税费-应交增值税-销项税额", "vat_output_tax"),)
```

- [ ] **Step 2: Run the focused tests and verify red**

Run: `python -m pytest tests/test_account_classification.py -q`

Expected: FAIL because `open_tax_check.account_classification` does not exist.

- [ ] **Step 3: Implement immutable classification contracts**

```python
ALLOWED_ACCOUNT_CATEGORIES = frozenset({
    "main_business_revenue", "other_business_revenue",
    "vat_output_tax", "business_entertainment",
})

@dataclass(frozen=True, slots=True)
class AccountClassification:
    account_code: str
    account_name: str
    category: str

@dataclass(frozen=True, slots=True)
class AccountClassificationReview:
    schema_version: str
    source_manifest_sha256: str
    mapping_fingerprint: str
    reviewer: str
    confirmed_at: str
    classifications: tuple[AccountClassification, ...]

@dataclass(frozen=True, slots=True)
class AccountSourceRef:
    account_code: str
    account_name: str
    category: str
```

Validation must require the exact JSON key set, SHA-256 lowercase hex strings, an ISO-8601 timezone-aware confirmation timestamp, unique `(account_code, account_name)` pairs, allowed categories, and a non-empty human reviewer that is not `ai`, `agent`, `host_ai`, or `model`. Aggregation matches both account code and normalized account name, uses existing exact numeric helpers, validates each matched row's lineage, and returns `None` rather than guessing when a required category is absent.

- [ ] **Step 4: Add safe persistence and explicit CLI confirmation**

```python
def confirm_account_classifications(case_dir: Path, payload: dict) -> dict:
    """Validate bindings against current frozen inputs, then atomically replace the active review."""

def load_active_account_classifications(
    case_dir: Path,
    source_manifest_sha256: str,
) -> AccountClassificationReview | None:
    """Return only a safe, valid review whose manifest and mapping bindings are current."""
```

Persist to `reviews/account_classification.json` through the same lstat/reparse-point checks and atomic write pattern used for mapping reviews. Add `confirm-account-classification --case PATH --input JSON` to the CLI; stale bindings fail with stable `account_classification_binding_mismatch` and never overwrite the previous valid review.

- [ ] **Step 5: Run focused tests and verify green**

Run: `python -m pytest tests/test_account_classification.py tests/test_reviews.py tests/test_cli.py -q`

Expected: PASS, including symlink/reparse rejection, stale-source rejection, atomic-write failure preservation, and absence of any AI auto-confirm path.

- [ ] **Step 6: Commit**

```bash
git add src/open_tax_check/account_classification.py src/open_tax_check/reviews.py src/open_tax_check/cli.py src/open_tax_check/__init__.py tests/test_account_classification.py tests/test_reviews.py tests/test_cli.py
git commit -m "feat: add source-bound account classifications"
```

### Task 2: Cross-Check Contracts and Exact Difference Engine

**Files:**
- Create: `src/open_tax_check/tax_cross_checks.py`
- Modify: `src/open_tax_check/__init__.py`
- Test: `tests/test_tax_cross_checks.py`

**Interfaces:**
- Consumes: `Decimal` amounts and source references from Task 1.
- Produces: `CrossCheckStatus`, `CrossCheckComponent`, `CrossCheckResult`, `CrossCheckQuestion`, `TaxCrossCheckRun`, `compare_amounts(left: Decimal, right: Decimal)`, and `tax_cross_check_payload(run: TaxCrossCheckRun)`.

- [ ] **Step 1: Write failing contract and boundary tests**

```python
@pytest.mark.parametrize(("left", "right", "status", "difference", "ratio"), [
    ("0.00", "0.00", "matched", "0.00", "0"),
    ("100.00", "100.00", "matched", "0.00", "0"),
    ("100000.00", "100009.00", "within_tolerance", "9.00", "0.00008999190072893439590436860683"),
    ("100.00", "109.00", "difference_found", "9.00", "0.08256880733944954128440366972"),
    ("0.00", "1.00", "difference_found", "1.00", "1"),
    ("-100.00", "-110.01", "difference_found", "10.01", "0.09099172802472502545223161531"),
])
def test_compare_amounts_uses_both_fixed_thresholds(left, right, status, difference, ratio):
    result = compare_amounts(Decimal(left), Decimal(right))
    assert result == (status, Decimal(difference), Decimal(ratio))
```

- [ ] **Step 2: Run the focused test and verify red**

Run: `python -m pytest tests/test_tax_cross_checks.py -q`

Expected: FAIL because the comparison engine and contracts do not exist.

- [ ] **Step 3: Implement frozen public contracts and exact comparison**

```python
ABSOLUTE_TOLERANCE = Decimal("10.00")
RELATIVE_TOLERANCE = Decimal("0.0001")
CHECK_ORDER = (
    "BOOK-FS-REV-001", "FS-VAT-SALES-001", "BOOK-VAT-OUTPUT-001",
    "FS-CIT-REV-PROFIT-001", "BOOK-CIT-BE-001",
)

def compare_amounts(left: Decimal, right: Decimal) -> tuple[str, Decimal, Decimal]:
    difference = abs(left - right)
    if difference == 0:
        return "matched", difference, Decimal("0")
    denominator = max(abs(left), abs(right))
    ratio = Decimal("1") if denominator == 0 else difference / denominator
    status = (
        "within_tolerance"
        if difference <= ABSOLUTE_TOLERANCE and ratio <= RELATIVE_TOLERANCE
        else "difference_found"
    )
    return status, difference, ratio
```

`CrossCheckResult` contains the design fields plus `components: tuple[CrossCheckComponent, ...]` for the two CIT subcomparisons and entertainment bridge values. `TaxCrossCheckRun` fixes `schema_version="tax-cross-checks/v1"`, validates five-result order for completed runs, and requires empty results/questions for blocked runs. Serialization uses existing `_json_business_value` conventions or an equivalent explicit serializer that emits canonical decimal strings.

- [ ] **Step 4: Add constructor invariants and deterministic JSON tests**

Test unknown check/status/category, threshold mutation, duplicate check IDs, unordered results, blocked runs with results, missing period/source refs, float amounts, non-finite decimals, and two serializations producing identical UTF-8 bytes.

- [ ] **Step 5: Run focused tests and verify green**

Run: `python -m pytest tests/test_tax_cross_checks.py -q`

Expected: PASS for exact matches, both-threshold boundaries, zero/negative/large values, immutable types, and stable JSON.

- [ ] **Step 6: Commit**

```bash
git add src/open_tax_check/tax_cross_checks.py src/open_tax_check/__init__.py tests/test_tax_cross_checks.py
git commit -m "feat: add exact tax cross-check contracts"
```

### Task 3: Implement the Five Deterministic Checks

**Files:**
- Modify: `src/open_tax_check/tax_cross_checks.py`
- Modify: `src/open_tax_check/structured_documents.py`
- Test: `tests/test_tax_cross_checks.py`
- Test: `tests/test_structured_documents.py`

**Interfaces:**
- Consumes: `run_tax_cross_checks(rows, classifications, financial_statements, tax_returns, context, source_manifest_sha256)` and existing `business_entertainment_adjustment(actual_expense, revenue)`.
- Produces: one ordered `TaxCrossCheckRun` with five results and at most three questions.

- [ ] **Step 1: Extend controlled tax facts with failing parser tests**

Add strict fact keys needed by the checks: `vat.output_tax`, `cit.total_profit`, `cit.business_entertainment.book_amount`, and `cit.business_entertainment.tax_amount`. Existing `vat.taxable_sales` and `cit.revenue` remain compatible. Accept `total_profit` as an explicit income-statement total while preserving the existing internal identity checks; do not derive profit total from net profit. Tests must prove duplicate `(form_code, line_number)`, duplicate required fact, overlapping VAT return periods, missing months/quarters, mixed filed/amended versions without an unambiguous latest return, and unit conflicts are rejected or yield `not_performed`, never silently summed.

- [ ] **Step 2: Run parser/check tests and verify red**

Run: `python -m pytest tests/test_structured_documents.py tests/test_tax_cross_checks.py -q`

Expected: FAIL on the new fact extraction and five-check scenarios.

- [ ] **Step 3: Implement exact source extractors**

Implement these exact interfaces: `statement_total(statements: Sequence[FinancialStatement], statement_type: str, total_key: str) -> SourcedAmount | None`, `tax_fact_total(returns: Sequence[TaxReturn], tax_id: str, fact_key: str, period: DateRange) -> SourcedAmount | None`, and `classified_ledger_total(rows: Sequence[Mapping[str, object]], review: AccountClassificationReview, category: str) -> SourcedAmount | None`.

Each extractor scales values by the validated `unit_multiplier`, preserves `source_id`, form code/line number or account identity, and refuses incomplete/overlapping periods. It reads only confirmed structured lines and classifications.

- [ ] **Step 4: Implement five checks in fixed order**

Implement `run_tax_cross_checks(rows: Sequence[Mapping[str, object]], classifications: AccountClassificationReview | None, financial_statements: Sequence[FinancialStatement], tax_returns: Sequence[TaxReturn], context: CrossCheckContext, source_manifest_sha256: str, explanations: Sequence[CrossCheckExplanation] = ()) -> TaxCrossCheckRun`.

`BOOK-FS-REV-001` sums the two revenue categories and compares income-statement `revenue`. `FS-VAT-SALES-001` compares statement `revenue` with complete-period `vat.taxable_sales`. `BOOK-VAT-OUTPUT-001` compares classified output tax with `vat.output_tax`. `FS-CIT-REV-PROFIT-001` stores independent revenue and total-profit components. `BOOK-CIT-BE-001` compares classified expense with declared book/tax amounts and calls `business_entertainment_adjustment`; it stores calculated deductible and add-back amounts without duplicating the formula.

- [ ] **Step 5: Implement missing-source results and bounded questions**

Only `difference_found` and `not_performed` generate questions. Sort missing critical inputs first, then descending absolute difference, descending relative difference, and `CHECK_ORDER`; deduplicate by `(check_id, period_start, period_end, source_refs)` and slice to three. Question text is Chinese and states what document/fact is needed without asserting evasion or underpayment.

- [ ] **Step 6: Run focused tests and verify green**

Run: `python -m pytest tests/test_structured_documents.py tests/test_tax_cross_checks.py -q`

Expected: PASS for all five matched/tolerance/difference/missing cases, complete VAT period validation, exact CIT components, business-entertainment formula reuse, source traceability, question ordering, deduplication, and maximum three.

- [ ] **Step 7: Commit**

```bash
git add src/open_tax_check/tax_cross_checks.py src/open_tax_check/structured_documents.py tests/test_tax_cross_checks.py tests/test_structured_documents.py
git commit -m "feat: implement five tax cross-checks"
```

### Task 4: Separate Data Quality Blocking and Persist Pipeline Results

**Files:**
- Modify: `src/open_tax_check/structured_reconciliation.py`
- Modify: `src/open_tax_check/pipeline.py`
- Modify: `src/open_tax_check/delivery.py`
- Test: `tests/test_structured_reconciliation.py`
- Test: `tests/test_pipeline.py`
- Test: `tests/test_delivery.py`

**Interfaces:**
- Consumes: Task 3 `run_tax_cross_checks(rows, classifications, financial_statements, tax_returns, context, source_manifest_sha256)`, current normalization/reconciliation results, structured documents, manifest digest, and active classification review.
- Produces: `work/tax-cross-checks.json` and delivery audit copy bound to the active case revision and manifest hash.

- [ ] **Step 1: Write regression tests for quality/risk separation**

```python
def test_valid_documents_with_tax_amount_difference_do_not_block_quality():
    result = reconcile_structured_documents(statements(), differing_returns())
    assert result.status == "reconciled"
    assert not any(issue.code == "tax_fact_mismatch" for issue in result.issues)

def test_context_mismatch_blocks_cross_checks_without_fake_results(tmp_path):
    response = run_case(case_with_currency_mismatch(tmp_path))
    payload = read_json(tmp_path / "work/tax-cross-checks.json")
    assert response[1]["status"] == "blocked_data_quality"
    assert payload["status"] == "blocked_data_quality"
    assert payload["results"] == [] and payload["questions"] == []
```

- [ ] **Step 2: Run focused tests and verify red**

Run: `python -m pytest tests/test_structured_reconciliation.py tests/test_pipeline.py tests/test_delivery.py -q`

Expected: FAIL because `tax_fact_mismatch` still blocks and no cross-check artifact is persisted.

- [ ] **Step 3: Restrict structured reconciliation to quality checks**

Remove `_FACT_BINDINGS` monetary equality comparisons from blocking. Keep tax-return structural validation: duplicate form lines, missing decimal values, document issues, context matching, and financial-statement identities. Replace legacy `tax_fact` equality checks with a non-blocking `deferred_to_tax_cross_checks` coverage record so old audit consumers can distinguish migration from accidental omission.

- [ ] **Step 4: Integrate the quality gate and atomic persistence**

In `_complete_normalized_run`, compute the source-manifest digest from frozen manifest bytes, load only a current classification review, and write exactly one result:

```python
cross_check_run = (
    blocked_tax_cross_check_run(source_manifest_sha256, quality_issue_refs)
    if quality_payload or not trial_result.passed or not journal_result.passed
    else run_tax_cross_checks(journal, classifications, statements, tax_returns, context, source_manifest_sha256)
)
_write_json(context.work_dir / "tax-cross-checks.json", tax_cross_check_payload(cross_check_run))
```

The run must read frozen in-memory rows/documents, recheck the manifest digest before commit, write via temporary file plus atomic replace, and leave the previous persisted artifact and active delivery pointer unchanged on any child-check or write failure.

- [ ] **Step 5: Bind delivery audit artifacts**

Copy `tax-cross-checks.json` into the case-audit section, add its SHA-256 and `source_manifest_sha256` to `delivery-manifest.json`, and validate both before publishing the revision pointer. `difference_found` remains deliverable; `blocked_data_quality` remains non-deliverable under existing v3 rules.

- [ ] **Step 6: Run focused tests and verify green**

Run: `python -m pytest tests/test_structured_reconciliation.py tests/test_pipeline.py tests/test_delivery.py -q`

Expected: PASS for context/identity blocking, non-blocking valid monetary differences, stale classification fallback to `not_performed`, atomic failure behavior, artifact hashes, and delivery-pointer preservation.

- [ ] **Step 7: Commit**

```bash
git add src/open_tax_check/structured_reconciliation.py src/open_tax_check/pipeline.py src/open_tax_check/delivery.py tests/test_structured_reconciliation.py tests/test_pipeline.py tests/test_delivery.py
git commit -m "feat: persist nonblocking tax cross-checks"
```

### Task 5: Structured Explanations, AI Questions, and Incremental Rerun

**Files:**
- Modify: `src/open_tax_check/tax_cross_checks.py`
- Modify: `src/open_tax_check/agent_orchestration.py`
- Modify: `src/open_tax_check/case_orchestration.py`
- Modify: `src/open_tax_check/cli.py`
- Test: `tests/test_tax_cross_checks.py`
- Test: `tests/test_agent_orchestration.py`
- Test: `tests/test_case_orchestration.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: persisted `TaxCrossCheckRun`, host answers, current verified evidence manifest.
- Produces: `CrossCheckExplanation`, `AmountBridgeItem`, `confirm_cross_check_explanation(case_dir: Path, payload: dict)`, and a new revision that reruns only affected checks while retaining append-only audit events.

- [ ] **Step 1: Write failing explanation and rerun tests**

```python
def test_text_only_explanation_cannot_close_difference():
    updated = apply_explanation(run(), valid_explanation(), bridge=())
    assert result_for(updated, "FS-VAT-SALES-001").resolution_status == "explained_pending_verification"

def test_complete_verified_bridge_can_close_difference():
    updated = apply_explanation(run(), valid_explanation(), bridge=(bridge_item("100.00"),))
    assert result_for(updated, "FS-VAT-SALES-001").resolution_status == "closed"

def test_confirmation_reruns_only_affected_check_and_keeps_old_audit_event():
    revised = confirm_cross_check_explanation(case, payload())
    assert rerun_spy.calls == ["FS-VAT-SALES-001"]
    assert revised.audit_events[:len(original.audit_events)] == original.audit_events
```

- [ ] **Step 2: Run focused tests and verify red**

Run: `python -m pytest tests/test_tax_cross_checks.py tests/test_agent_orchestration.py tests/test_case_orchestration.py tests/test_cli.py -q`

Expected: FAIL because cross-check explanations and incremental reruns are not implemented.

- [ ] **Step 3: Implement strict explanation contracts**

Allowed categories are exactly `unbilled_revenue`, `deemed_sale`, `non_taxable_or_exempt_revenue`, `tax_inclusive_conversion`, `timing_difference`, `amended_return`, `book_adjustment`, and `other`. Require `check_id`, category, non-empty Chinese explanation, `confirmed_by`, timezone-aware `confirmed_at`, and at least one evidence reference resolvable through the current evidence manifest. `other` additionally requires evidence and explanation; no category is auto-selected by AI.

`AmountBridgeItem` requires signed `amount`, Chinese label, and verified evidence refs. Closure requires exact sum of bridge items equal to the original signed difference; otherwise resolution is at most `explained_pending_verification`. Original amounts and status remain immutable and visible in audit history.

- [ ] **Step 4: Extend the host-agent envelope without granting authority**

Add cross-check question task conversion with a hard `max_questions=3`. The payload allowlist exposes question ID, check ID, Chinese question/help, allowed explanation categories, required evidence types, and source-safe labels; it excludes thresholds, arbitrary status fields, filesystem paths, and mutation commands. Host answers pass through contract validation and evidence verification before persistence.

- [ ] **Step 5: Add confirmation command and affected-check rerun**

Add `confirm-cross-check --case PATH --input JSON`. Lock the case revision, verify question/check/source-manifest bindings, append the confirmation event, rerun the referenced check only, regenerate the global ordered question queue, atomically persist a new snapshot revision, and invalidate any previous delivery pointer. Duplicate idempotent submissions return the existing revision; conflicting reuse fails with `cross_check_confirmation_conflict`.

- [ ] **Step 6: Run focused tests and verify green**

Run: `python -m pytest tests/test_tax_cross_checks.py tests/test_agent_orchestration.py tests/test_case_orchestration.py tests/test_cli.py -q`

Expected: PASS for the three-question cap, allowed categories, evidence verification, no AI-selected confirmations, text-only non-closure, exact bridge closure, idempotency, conflict rejection, append-only events, and affected-only rerun.

- [ ] **Step 7: Commit**

```bash
git add src/open_tax_check/tax_cross_checks.py src/open_tax_check/agent_orchestration.py src/open_tax_check/case_orchestration.py src/open_tax_check/cli.py tests/test_tax_cross_checks.py tests/test_agent_orchestration.py tests/test_case_orchestration.py tests/test_cli.py
git commit -m "feat: add bounded cross-check follow-up"
```

### Task 6: Customer PDF, Workpaper, and Delivery Presentation

**Files:**
- Modify: `src/open_tax_check/report_view.py`
- Modify: `src/open_tax_check/workpaper.py`
- Modify: `src/open_tax_check/delivery.py`
- Modify: `src/open_tax_check/report_template.html`
- Modify: `src/open_tax_check/report.css`
- Test: `tests/test_report_view.py`
- Test: `tests/test_workpaper.py`
- Test: `tests/test_delivery.py`
- Test: `tests/test_report_html.py`
- Test: `tests/test_report_pdf.py`

**Interfaces:**
- Consumes: persisted cross-check JSON and existing v3 report snapshot.
- Produces: approved customer risks for `difference_found`, professional appendix entries for all performed checks, Chinese material gaps for `not_performed`, and audit rows with exact sources and thresholds.

- [ ] **Step 1: Write failing view/report tests**

```python
def test_difference_is_customer_risk_without_tax_evasion_claim():
    view = build_report_view_v3(snapshot(), entity_name="示例公司", tax_cross_checks=difference_run())
    risk = next(item for item in view.risks if item.finding_id == "BOOK-VAT-OUTPUT-001")
    assert "账套" in risk.observed_zh and "申报" in risk.observed_zh
    assert "差异" in risk.observed_zh
    assert "漏税" not in risk.observed_zh and "应补税" not in risk.can_conclude_zh

def test_not_performed_is_material_gap_not_match():
    view = build_report_view_v3(snapshot(), entity_name="示例公司", tax_cross_checks=missing_run())
    assert any("资料不足" in item.impact_zh for item in view.materials)
    assert not any("金额一致" in text for text in view.appendix_executed_checks)
```

- [ ] **Step 2: Run focused tests and verify red**

Run: `python -m pytest tests/test_report_view.py tests/test_workpaper.py tests/test_delivery.py tests/test_report_html.py tests/test_report_pdf.py -q`

Expected: FAIL because report/workpaper layers do not consume cross-check results.

- [ ] **Step 3: Add customer-safe report translation**

Extend the existing signature with keyword-only `tax_cross_checks: TaxCrossCheckRun | None = None`. For each `difference_found`, render Chinese title, both amounts, absolute difference, evidence boundary, required material, and next action; do not expose Python field names, enum values, speculative reasons, or internal thresholds in the executive section. Keep `within_tolerance` out of priority risks but list it in the professional appendix. Render `not_performed` as “资料不足，未执行该项核对”, never as agreement or no-risk.

- [ ] **Step 4: Add audit workpaper rows**

Workpaper rows include check ID, component, original values, difference, ratio, fixed tolerances, status, source refs, explanation/audit refs, manifest digest, and revision. Workpaper generation parses persisted JSON and never recalculates monetary values.

- [ ] **Step 5: Update fixed HTML/PDF template**

Add compact cross-check risk blocks and appendix tables using existing Noto Sans SC embedded font and A4 pagination rules. Long source labels wrap; amount columns do not clip; table headers repeat across pages; grayscale still distinguishes status by text and border, not color alone. Formal delivery remains `税务健康检查报告.pdf` only.

- [ ] **Step 6: Run focused and visual tests**

Run: `python -m pytest tests/test_report_view.py tests/test_workpaper.py tests/test_delivery.py tests/test_report_html.py tests/test_report_pdf.py -q`

Expected: PASS for escaping, no internal enum leakage, difference/tolerance/missing semantics, source references, PDF Chinese extraction, report ID, bookmarks, and absence of DOCX.

Render matched, tolerance-only, multiple-difference, and many-missing-data PDFs to PNG. Inspect every page for font consistency, clipping, overlap, table splitting, directory page numbers, blank pages, and grayscale legibility; record image paths and observations in the task review evidence.

- [ ] **Step 7: Commit**

```bash
git add src/open_tax_check/report_view.py src/open_tax_check/workpaper.py src/open_tax_check/delivery.py src/open_tax_check/report_template.html src/open_tax_check/report.css tests/test_report_view.py tests/test_workpaper.py tests/test_delivery.py tests/test_report_html.py tests/test_report_pdf.py
git commit -m "feat: present tax cross-checks in pdf reports"
```

### Task 7: End-to-End Cases, Documentation, Release Validation, and Review

**Files:**
- Modify: `tests/test_fictional_example.py`
- Modify: `tests/test_release_validation.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/agent-installation.md`
- Modify: `docs/qa/html-pdf-customer-report.md`
- Modify: `CHANGELOG.md`
- Test: all tests and distribution validation

**Interfaces:**
- Consumes: complete Tasks 1-6 behavior.
- Produces: reproducible acceptance fixtures, cross-platform evidence, updated public instructions, and a merge-ready branch.

- [ ] **Step 1: Add five complete fictional acceptance cases**

Create source-bound CSV and Excel fixtures for: all matched; all differences inside both tolerances; VAT sales/output tax outside tolerance; CIT revenue/profit/entertainment outside tolerance; and multiple missing sources followed by staged confirmations. Each case asserts identical CSV/Excel machine outcomes, question queue, audit events, report view, delivery manifest hashes, and PDF text.

- [ ] **Step 2: Run acceptance tests and verify any missing integration fails**

Run: `python -m pytest tests/test_fictional_example.py -q`

Expected before final fixes: at least one new end-to-end assertion FAILS; if all pass immediately, verify the fixtures exercise actual pipeline and delivery entry points rather than constructing result objects directly.

- [ ] **Step 3: Complete package exports, docs, and release validation**

Export public contracts in `open_tax_check.__init__`; ensure wheel/sdist include no customer data or generated reports. Document the two explicit confirmation commands, deterministic thresholds, AI authority boundary, maximum-three question behavior, source requirements, and Chinese PDF-only output. Update changelog and QA evidence without describing skipped checks as passed.

- [ ] **Step 4: Run full local verification**

Run:

```powershell
python -m pytest -q
python -m build
python scripts/validate_release.py --strict dist/*.whl dist/*.tar.gz
python scripts/verify_isolated_wheel.py dist/*.whl
```

Expected: all tests PASS; wheel, sdist, strict release validation, and isolated wheel installation PASS. Record every skipped test separately with its reason; a skip is not a pass.

- [ ] **Step 5: Verify the cross-platform CI matrix**

Run Windows, macOS, and Linux on Python 3.11, 3.12, and 3.13. Each OS must include at least one real Chromium PDF smoke. Expected: all matrix jobs PASS; unavailable platform evidence remains pending rather than being inferred from local results.

- [ ] **Step 6: Perform task-level and full-branch independent review**

Use `superpowers:requesting-code-review` after each task. After Task 7, request a fresh reviewer to compare the entire branch against the approved design and inspect security, accounting invariants, tax formulas, source binding, status semantics, AI authority, PDF-only delivery, and test evidence. Fix every Critical or Important finding, rerun affected tests, and repeat review until none remain.

- [ ] **Step 7: Run final branch audit and commit evidence**

Run:

```powershell
git status --short
git diff --check origin/main...HEAD
git log --oneline origin/main..HEAD
rg -n "TB[D]|TO[D]O|include-docx|tax_fact_mismatch" src tests docs README.md CHANGELOG.md
```

Expected: clean worktree after the evidence commit; no whitespace errors; unfinished-work markers are absent; no public DOCX option; any retained `tax_fact_mismatch` string appears only in explicit compatibility/migration tests.

```bash
git add tests .github/workflows/ci.yml README.md docs CHANGELOG.md src/open_tax_check/__init__.py
git commit -m "docs: validate high-frequency tax cross-checks"
```

- [ ] **Step 8: Hand off with the development-branch finishing workflow**

Use `superpowers:verification-before-completion`, then `superpowers:finishing-a-development-branch`. Report commits, tests, builds, visual evidence, CI URLs, review outcome, and remaining skips or platform gaps. Do not push, merge, tag, or publish without a new explicit user authorization.
