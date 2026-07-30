# Common Template Parsing and Cross-Document Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read bounded common Excel/CSV accounting and tax templates, generate the existing strict financial-statement/v1 and tax-return/v1 contracts, and reconcile those contracts without producing a normal result when data quality is not proven.

**Architecture:** Add a small template adapter that consumes the existing `WorkbookReader`/CSV table shape and emits the already-versioned structured-document dictionaries. Add a pure reconciliation module over the immutable contract objects, with explicit identities and source anchors. Extend the case runner to consume imported structured documents from `work/structured-documents/`, publish a deterministic reconciliation artifact, and return `blocked_data_quality` for any failed or unverifiable cross-document control.

**Tech Stack:** Python 3.11-3.13, standard library `csv`, `decimal`, `json`, `dataclasses`, existing `WorkbookReader`, existing structured document contracts, `pytest`.

## Global Constraints

- Preserve the existing safe workbook boundary: do not execute formulas, macros, external links, or read through symlinks/reparse points.
- Use `Decimal` and canonical two-decimal strings for money; never infer a missing debit/credit direction or a missing tax fact.
- Every generated line must carry a source anchor with the trusted source id and physical row/column location.
- Host AI output remains `proposed` and can never be upgraded to `confirmed` by these adapters.
- Entity, currency, unit multiplier, and review period must match before cross-document comparison.
- Any debit/credit imbalance, statement identity failure, tax-return identity failure, period mismatch, or missing required control input produces `blocked_data_quality`; no normal tax conclusion is written for that run.
- Diagnostics contain stable codes and locations only; never include raw customer cell values.
- Existing journal/trial-balance behavior and public CLI contracts must remain unchanged.

---

### Task 1: Parse common templates into strict structured-document contracts

**Files:**
- Create: `src/open_tax_check/template_documents.py`
- Create: `tests/test_template_documents.py`
- Modify: `src/open_tax_check/__init__.py`

**Interfaces:**
- `TemplateIssue(code: str, row: int | None, fields: tuple[str, ...])`
- `TemplateParseResult(payload: dict | None, issues: tuple[TemplateIssue, ...])`
- `financial_statement_template(rows, *, source_id, entity_name, statement_id, statement_type, period_start, period_end, as_of_date=None, sheet_name="Sheet1", header_row=1) -> TemplateParseResult`
- `tax_return_template(rows, *, source_id, entity_name, return_id, tax_id, return_type, period_start, period_end, filing_status="unknown", filed_at=None, sheet_name="Sheet1", header_row=1) -> TemplateParseResult`
- `rows` is a sequence of `(row_number, sequence_of_cell_values)` where the first row is the header row. Supported financial headers are `line_code`, `line_name`, `standard_name`, `current_amount`, `prior_amount`; supported tax headers are `form_code`, `line_number`, `line_name`, `fact_key`, `value`, `value_type`.

- [ ] **Step 1: Write failing tests for valid CSV-shaped rows, formula/money rejection, and source anchors.**
- [ ] **Step 2: Run `python -m pytest tests/test_template_documents.py -q`; confirm the new functions are missing.**
- [ ] **Step 3: Implement bounded header normalization, row iteration, amount normalization, stable issues, and contract payload construction.**
- [ ] **Step 4: Validate each payload through `financial_statement_from_dict` or `tax_return_from_dict`; return `payload=None` when any issue exists.**
- [ ] **Step 5: Run `python -m pytest tests/test_template_documents.py tests/test_structured_documents.py -q` and commit `feat: parse common financial tax templates`.**

### Task 2: Deterministic financial-statement and tax-return reconciliation

**Files:**
- Create: `src/open_tax_check/structured_reconciliation.py`
- Create: `tests/test_structured_reconciliation.py`

**Interfaces:**
- `DocumentReconciliationIssue(code: str, severity: str, document_ids: tuple[str, ...], source_ids: tuple[str, ...], line_codes: tuple[str, ...])`
- `DocumentReconciliationResult(status: str, passed: bool, checks: tuple[dict, ...], issues: tuple[DocumentReconciliationIssue, ...])`
- `reconcile_structured_documents(financial_statements: Sequence[FinancialStatement], tax_returns: Sequence[TaxReturn]) -> DocumentReconciliationResult`

Required checks:
- all documents have the same entity, currency, unit multiplier, and exactly the same period;
- every balance sheet declares `assets` and `liabilities_and_equity` totals and they are equal;
- every income statement that declares `revenue`, `cost`, `expenses`, `taxes`, and `net_profit` satisfies `revenue - cost - expenses - taxes == net_profit`;
- when both are present, balance-sheet `cash` equals cash-flow `ending_cash`;
- duplicate tax form/line pairs, missing decimal values for a declared decimal line, and any existing document issue are blocking;
- known tax facts are compared only through an explicit map (`vat.taxable_sales -> revenue`, `cit.revenue -> revenue`, `cit.net_profit -> net_profit`); absent facts are reported as not performed, never guessed.

- [ ] **Step 1: Write failing tests for balanced documents, each identity failure, period mismatch, and tax fact mismatch.**
- [ ] **Step 2: Run `python -m pytest tests/test_structured_reconciliation.py -q`; confirm the module/API is missing.**
- [ ] **Step 3: Implement pure `Decimal` checks and location-only issue serialization.**
- [ ] **Step 4: Verify the result is `status="blocked_data_quality"` whenever `passed` is false, and never raises on ordinary bad customer data.**
- [ ] **Step 5: Run focused regression tests and commit `feat: reconcile structured financial tax documents`.**

### Task 3: Consume imported structured documents in case runs

**Files:**
- Modify: `src/open_tax_check/pipeline.py`
- Modify: `src/open_tax_check/coverage.py`
- Create: `tests/test_structured_pipeline.py`
- Modify: `docs/document-extraction-contract.md`

**Interfaces:**
- Add internal `load_case_structured_documents(case_dir) -> tuple[tuple[FinancialStatement, ...], tuple[TaxReturn, ...], tuple[str, ...]]`.
- Add a structured-documents case path to `run_case` when `work/structured-documents/` contains imported contracts and no spreadsheet path has priority.

Behavior:
- read only regular JSON files under the case work directory, reject unsafe links and malformed contracts with stable data-quality codes;
- write `work/structured-reconciliation.json` with checks/issues and `work/coverage_matrix.json` entries for financial-statement and tax-return reconciliation;
- return `(4, {"status": "blocked_data_quality", "next_action": "resolve_data_quality_findings"})` for failed or unverifiable controls, without writing `output/findings.json` or a normal tax report;
- return `partial_scope_ready` with `review_coverage` when contracts are valid and balanced, because this path does not invent tax rules or conclusions;
- leave existing CSV/Excel run selection and output bytes unchanged.

- [ ] **Step 1: Add failing integration tests for balanced imported contracts, a balance-sheet imbalance, a period mismatch, and malformed/unsafe JSON.**
- [ ] **Step 2: Run `python -m pytest tests/test_structured_pipeline.py -q`; confirm the structured path is not yet selected.**
- [ ] **Step 3: Implement safe loading, reconciliation artifact publication, coverage states, and run dispatch.**
- [ ] **Step 4: Run focused pipeline regressions plus `tests/test_excel_pipeline.py tests/test_structured_document_cli.py`.**
- [ ] **Step 5: Update the contract documentation and commit `feat: run structured document reconciliation in cases`.**

