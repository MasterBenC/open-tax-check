# OpenTaxCheck Phase 2A-2B Excel Intake and Normalization Design

**Date:** 2026-07-22
**Status:** Approved by user on 2026-07-22
**Scope:** Safe Excel intake, evidence coverage, journal and trial-balance normalization, and deterministic reconciliation

## 1. Purpose

Phase 2A-2B expands OpenTaxCheck from a single CSV journal into a resumable, multi-file Excel intake workflow suitable for tax-health-check engagements. The phase supports `.xlsx`, `.xlsm`, `.xls`, and the existing CSV format while preserving the project's core boundary: Python owns file handling, mapping controls, arithmetic, lineage, state, and reconciliation; AI may propose mappings but cannot approve them or change source data.

This phase establishes the accounting-data foundation required by later financial-statement, tax-return, document-AI, tax-rule, and professional-report phases.

## 2. Confirmed Product Decisions

1. Excel is implemented before PDF and image processing.
2. `.xlsx`, `.xlsm`, and legacy `.xls` are supported. Macros are never executed.
3. The first normalized accounting roles are `journal` and `trial_balance`.
4. Financial statements, tax returns, and supporting schedules are inventoried but not normalized in this phase.
5. Evidence requests use three tiers:
   - required baseline package;
   - recommended package;
   - risk-triggered supplemental package.
6. Missing evidence blocks only the procedures that depend on it. Independent procedures may continue.
7. PDF and image processing is outside this phase. Its later provider choices are `baidu`, `host`, and `off`, all behind an explicit authorization gate and a shared document-extraction schema.
8. The default document-AI route will be a provider-neutral adapter with Baidu as the first integrated provider; host agents may instead return the same schema.

## 3. Goals

- Accept multiple ordinary Excel and CSV files in one case.
- Preserve every original file unchanged and record a SHA-256 digest.
- Inventory workbooks and sheets without executing code or following external links.
- Deterministically classify common journal and trial-balance sheets.
- Stop for sheet selection or mapping review when classification is ambiguous.
- Normalize journal and trial-balance rows into versioned canonical schemas.
- Preserve field-level lineage to the source workbook, sheet, row, column, and cell.
- Reconcile journal movements to trial-balance movements with decimal arithmetic.
- Produce a procedure-level evidence coverage matrix.
- Reuse confirmed decisions when source hashes are unchanged and invalidate only affected downstream artifacts when a source changes.
- Preserve all existing CSV behavior.

## 4. Non-goals

This phase does not:

- parse or reconcile financial statements;
- parse or reconcile tax returns;
- call Baidu, another cloud OCR service, or a large language model directly;
- extract data from PDF or image files;
- execute formulas, macros, VBA, add-ins, external links, or embedded objects;
- infer missing debit or credit directions;
- generate a client-facing Excel, DOCX, or PDF report;
- add new tax-law conclusions or expanded tax-rule packs;
- issue an overall numeric tax-health score.

## 5. Evidence-Tier Model

### 5.1 Required baseline package

The case evidence model defines the following as required for a baseline tax health check:

- journal;
- trial balance with opening, period movement, and closing balances;
- balance sheet and income statement;
- VAT and corporate-income-tax returns for the review period;
- entity tax profile, including taxpayer type, industry, review period, principal rates, and incentives;
- chart of accounts or accounting-policy explanation.

Phase 2A-2B normalizes the journal and trial balance. The other required roles are recorded as `present_unparsed` or `missing`. A case cannot claim `baseline_health_check` readiness until the later parsers and reconciliations are implemented.

### 5.2 Recommended package

The coverage matrix recognizes, without parsing in this phase:

- cash-flow statement;
- output-invoice and input-credit schedules;
- payroll, individual-income-tax, and social-insurance schedules;
- fixed-asset and depreciation register;
- receivable, payable, and other-balance aging schedules;
- related-party and related-transaction schedules;
- annual corporate-income-tax adjustment schedules;
- detailed schedules for R&D, employee welfare, business entertainment, and advertising expenses;
- incentive qualification and filing documents.

### 5.3 Risk-triggered supplemental package

Later rule findings may request contracts, invoices, bank records, personnel records, ownership records, R&D evidence, cross-border payment evidence, property records, or other targeted support. The coverage matrix reserves `trigger_rule_id`, `requested_at`, and `request_status` fields so supplemental requests remain attributable to the finding that created them.

## 6. Architecture

The package gains focused modules with explicit boundaries:

- `intake.py`: enumerate safe top-level inputs, hash ordinary files, enforce limits, and build the evidence coverage matrix.
- `workbooks.py`: expose a format-neutral read-only workbook interface and implement `.xlsx`/`.xlsm` and `.xls` adapters.
- `sheet_detection.py`: locate header candidates and assign deterministic role scores.
- `schemas.py`: define canonical journal, trial-balance, lineage, inventory, and reconciliation records.
- `mapping.py`: extend header mapping to be role-specific and validate persisted confirmations.
- `reconciliation.py`: perform trial-balance identities and journal-to-trial-balance movement reconciliation.
- `pipeline.py`: orchestrate resumable steps and publish the current run atomically.
- `cli.py`: expose inspection and review commands without embedding workbook logic.

The current tax-rule modules remain downstream consumers of normalized, validated journal data. Trial-balance rules are added only in a separate rule-pack phase.

## 7. Case Workflow

The user-facing workflow is:

```text
opentaxcheck init --case <case> --entity <name> --period-start YYYY-MM-DD --period-end YYYY-MM-DD
place files in <case>/input/
opentaxcheck inspect --case <case>
opentaxcheck run --case <case> --ai-mode off
```

If the case period is absent, inspection stops at `needs_intake_review` with `next_action=set_case_period`. Existing v0.1 CSV cases remain readable; they can use `opentaxcheck set-scope` before entering the multi-file workflow.

Ambiguous sheets are confirmed with:

```text
opentaxcheck select-sheet --case <case> --role <role> --source <source_id> --sheet <sheet_id> --header-row <one-based-row>
```

Multiple selected sheets are allowed for a role. The command stores an explicit decision rather than modifying the source workbook.

Ambiguous mappings are confirmed with:

```text
opentaxcheck confirm-mapping --case <case> --role <role> --mapping <mapping.json>
```

The command validates the proposal, rejects duplicate targets and missing required fields, records the reviewer identity supplied by the host, and binds the confirmation to the source digest, sheet identifier, header rows, and mapping schema version. AI-generated mapping proposals use the same input file but are not active until this command succeeds.

## 8. Intake Safety Contract

### 8.1 Accepted entries

- Ordinary top-level `.csv`, `.xlsx`, `.xlsm`, and `.xls` files are eligible.
- Office lock files whose basename begins with `~$` are recorded as `ignored_office_lock` and are not opened.
- Directories, symbolic links, junctions, and non-ordinary entries are rejected without following them.
- Unsupported ordinary files are inventoried as evidence but are not opened by an Excel adapter.

### 8.2 Default limits

- At most 50 top-level input entries.
- At most 100 MiB per file.
- At most 200 worksheets per workbook.
- At most 5,000,000 visited cells per selected worksheet.
- Header discovery scans at most the first 100 non-empty rows and first 256 columns.
- OOXML packages may contain at most 10,000 ZIP members, at most 1 GiB total declared uncompressed size, and no member with a declared compression ratio above 100:1.

An exceeded limit produces `needs_intake_review`; the first release does not provide an unsafe override. Users split or re-export the workbook.

### 8.3 Workbook behavior

- `.xlsx` and `.xlsm` are read with external-link preservation disabled.
- `defusedxml` is installed with the Excel extra because openpyxl does not itself guarantee protection against XML expansion attacks.
- `.xlsm` VBA packages, add-ins, embedded objects, charts, and images are never loaded as executable content.
- `.xls` is read through `xlrd`, which returns stored formula results and ignores macros and embedded objects.
- Password-protected or encrypted workbooks are reported as `encrypted_workbook` and are not processed.
- Hidden worksheets are inventoried but never auto-selected.
- Hidden data rows in a selected sheet require an explicit include/exclude decision before normalization.
- Merged cells may be expanded only when constructing a header label. Merged data cells are not filled down automatically.

### 8.4 Formula cells

For `.xlsx` and `.xlsm`, the adapter records formula text separately from the workbook's cached value. It never recalculates the formula. A required numeric field may use a non-empty cached value and records `value_origin=cached_formula`. A formula without a cached value creates a blocking `missing_cached_formula_value` issue. For `.xls`, the extracted stored result is marked `value_origin=xls_formula_result` when the format exposes that distinction.

## 9. Workbook and Sheet Inventory

Every input file receives a stable `source_id` derived from the first 16 hexadecimal characters of its SHA-256 digest. Each workbook record includes:

- local original filename;
- `source_id`, full SHA-256 digest, byte size, extension, and adapter version;
- workbook status and safe diagnostic code;
- sheet index, stable `sheet_id`, name, visibility, used range, and visited-cell estimate;
- candidate header rows and deterministic role scores;
- whether formulas, merged ranges, or hidden data are present.

Public logs and exceptions use `source_id` and diagnostic codes. Original filenames remain in case-local artifacts only.

## 10. Deterministic Sheet Detection

The detector normalizes whitespace, punctuation, full-width forms, and common header aliases. It evaluates one-, two-, and three-row header combinations within the discovery window.

Each role receives a score from 0 to 100:

- `85-100`: high-confidence candidate;
- `60-84`: review candidate;
- `0-59`: not a candidate.

Journal high confidence requires unique mappings for `voucher_no`, `date`, `account_name`, `debit`, and `credit`. Trial-balance high confidence requires `account_name`, period debit and credit, plus a complete opening and closing representation using either split debit/credit columns or amount-and-direction pairs.

Exactly one visible high-confidence sheet for a role may be auto-selected. Multiple candidates, multiple period sheets, hidden candidates, or overlapping selected ranges produce `needs_sheet_selection`. The detector never concatenates sheets merely because their headers look alike.

Financial-statement, tax-return, and supporting-schedule scores create inventory classifications only. They do not produce normalized accounting data in this phase.

## 11. Mapping Contract

Mapping is role-specific and versioned. Each source header must be:

- mapped to exactly one canonical field; or
- explicitly excluded with a reason.

Mapping schema `0.3.0` keeps the public confirmation JSON label-keyed, but binds each nonempty selected header to its one-based physical worksheet column. The source-header fingerprint hashes the ordered `{column, header}` pairs, so inserting or moving a blank spacer column invalidates an earlier confirmation rather than redirecting it to another cell. Legacy label-only `0.2.0` confirmations are inactive and must be reconfirmed.

No two source headers may map to the same canonical field unless the schema explicitly defines a composite mapping, such as a three-row header or an amount-and-direction pair. Missing required fields, duplicate targets, uncertain directions, and incompatible data types stop normalization.

An AI or host agent may propose mappings from header labels, field type profiles, sheet dimensions, and redacted structural samples. It does not receive the full workbook by default. The proposal is stored with `proposal_source=ai` or `proposal_source=host`; only a validated confirmation activates it.

## 12. Canonical Schemas

### 12.1 Journal row

The existing journal fields remain:

- required: `voucher_no`, `date`, `account_name`, `debit`, `credit`;
- optional: `account_code`, `summary`, `counterparty`.

The row adds `period`, `tax_year`, `source_id`, `source_sheet`, `source_row`, `mapping_version`, and field-level lineage.

### 12.2 Trial-balance row

Required normalized fields are:

- `account_name`;
- `opening_debit`, `opening_credit`;
- `period_debit`, `period_credit`;
- `closing_debit`, `closing_credit`.

Optional fields are:

- `account_code`;
- `account_level`;
- `parent_account_code`;
- `auxiliary_type`;
- `auxiliary_name`;
- `currency`.

All monetary fields are canonical decimal strings. Blank monetary cells normalize to zero only after a valid row has been identified. A source using balance plus direction is converted to split debit and credit only when the direction is explicit and valid. Source values and conversion metadata remain in field-level lineage.

### 12.3 Field-level lineage

Every canonical field records:

- `source_id`;
- `sheet_id` and local sheet name;
- one-based row and column;
- A1 cell coordinate;
- original cell type;
- `value_origin` (`literal`, `cached_formula`, `xls_formula_result`, or `derived_direction_split`);
- mapping version.

For an amount-plus-direction split, the derived debit and credit lineage keeps the amount cell at the compatible top level with `value_origin=derived_direction_split` and adds `contributing_cells.amount` plus `contributing_cells.direction`. Each contributor repeats the location-only fields above with its own original type and origin; raw cell values are not included. Reconciliation preserves this nested lineage unchanged.

Raw client values do not appear in general application logs.

## 13. Reconciliation

Reconciliation runs only on confirmed mappings and data-quality-valid rows.

1. Trial-balance movement totals must satisfy total period debits equals total period credits.
2. Each row must satisfy signed opening balance plus period debit minus period credit equals signed closing balance. Debit is positive and credit is negative solely for this identity calculation.
3. Journal rows are aggregated by review period and account.
4. Journal and trial-balance accounts match by exact normalized account code first. A normalized account name is used only when it is unique on both sides. Ambiguous or unmatched accounts are reported and never force-matched.
5. Journal debit and credit movements are compared with trial-balance period debit and credit movements at exact cent precision.
6. Every difference includes the source account identifiers and contributing source-row lineage.

A failed reconciliation blocks only procedures that depend on the affected role, period, or account population. The coverage matrix records `performed`, `blocked`, `not_performed_missing_evidence`, `present_unparsed`, or `not_applicable` for every procedure.

## 14. State and Resumability

The workflow adds these states:

- `needs_intake_review`;
- `needs_sheet_selection`;
- `needs_mapping_review`;
- `blocked_data_quality`;
- `partial_scope_ready`;
- `needs_professional_review`.

Run artifacts are written under an immutable run directory and published through the existing atomic current-view mechanism. The current artifacts are:

- `work/input_manifest.json`;
- `work/workbook_inventory.json`;
- `work/sheet_selection.json`;
- `work/mapping_review.json`;
- `work/normalized_journal.jsonl`;
- `work/normalized_trial_balance.jsonl`;
- `work/reconciliation.json`;
- `work/coverage_matrix.json`.

Confirmed selections and mappings are stored in a separate review layer. They are reusable only when the source digest, sheet identity, header rows, case period, and applicable schema version are unchanged. A changed dependency invalidates that decision and all downstream artifacts that consume it; unrelated sources and review decisions remain reusable.

## 15. Error Handling and Privacy

- Expected intake failures return stable diagnostic codes without tracebacks or raw cell values.
- Unexpected failures set `next_action=inspect_processing_error`, preserve the previous current view, and keep the failed run for local diagnosis without publishing raw values to logs.
- Workbook adapters close file handles on success and failure.
- Partial normalized files are never published as current artifacts.
- Original inputs are read-only from the application's perspective.
- Test fixtures and public examples contain only fictional entities, values, vouchers, accounts, and conclusions.
- Real client workbooks may be used only for local manual acceptance testing and must remain ignored and untracked.

## 16. Dependencies and Compatibility

The core package keeps zero runtime dependencies. Excel support is installed with:

```text
pip install -e ".[excel]"
```

The `excel` optional dependency group contains:

- `openpyxl>=3.1.5,<4` for `.xlsx` and `.xlsm`;
- `xlrd>=2.0.2,<3` for `.xls`;
- `defusedxml>=0.7.1,<1` for defensive XML parsing support.

Python 3.11, 3.12, and 3.13 remain supported on Windows, macOS, and Linux. CI upgrades to `actions/checkout@v6` and `actions/setup-python@v6` so the workflow uses the supported Node.js 24 action runtime.

## 17. Test Strategy

Tests use programmatically generated fictional workbooks and small reviewed binary fixtures. No fixture contains client data.

Coverage includes:

- `.xlsx`, `.xlsm`, `.xls`, and existing CSV behavior;
- one sheet, multiple sheets, hidden sheets, and hidden rows;
- headers starting below row one, two- and three-row headers, and merged header cells;
- clear, ambiguous, conflicting, and missing role mappings;
- split debit/credit and amount-plus-direction trial balances;
- formulas with and without cached values;
- literal dates, Excel serial dates, text dates, blank amounts, grouped amounts, negatives, and rejected numeric forms;
- damaged, encrypted, oversized, and ZIP-bomb-like files;
- duplicate and overlapping candidate sheets;
- matching and mismatching journal-to-trial-balance populations;
- atomic publication failure and selective invalidation after a source digest changes;
- deterministic repeat runs and full field-level lineage;
- optional-extra absence with a clear `install_excel_extra` action;
- the complete existing unit, release-validation, fictional-example, package-build, and Skill-contract suites.

The public end-to-end example gains a fictional Excel journal and trial balance whose reconciled totals and tax-screening findings match the CSV example.

## 18. Acceptance Criteria

Phase 2A-2B is complete only when:

1. Common accounting-software exports in `.xlsx`, `.xlsm`, and `.xls` enter the deterministic workflow without code execution.
2. Ambiguous sheet roles and mappings stop at explicit review states.
3. Every normalized monetary field is traceable to an exact source cell and mapping version.
4. Journal and trial-balance movements reconcile by period and account with exact decimal arithmetic.
5. Missing financial statements, returns, or supplemental schedules are visible in the coverage matrix and never described as no-risk results.
6. Changed source files invalidate only dependent decisions and artifacts.
7. The same unchanged case produces identical business artifacts apart from run timestamps and run identifiers.
8. Existing CSV cases and public contracts remain compatible.
9. The full test matrix passes on all nine operating-system and Python combinations.
10. Release validation confirms no real client data, credentials, protected templates, or unsafe binary fixtures are tracked.

## 19. Subsequent Designs

After this phase is merged, separate designs cover:

1. financial-statement and tax-return schemas plus four-way reconciliation;
2. provider-neutral PDF/image document AI with Baidu and host-agent adapters;
3. versioned tax-rule packs consuming journal, trial-balance, return, and evidence data;
4. professional workpapers, DOCX/PDF reporting, and evidence packages.

These designs must preserve the intake, authorization, lineage, data-quality, and professional-review controls established here.

## 20. Technical References

- openpyxl package and XML security notice: <https://pypi.org/project/openpyxl/>
- xlrd `.xls` behavior and limitations: <https://pypi.org/project/xlrd/>
- setup-python Node.js 24 migration: <https://github.com/actions/setup-python>
