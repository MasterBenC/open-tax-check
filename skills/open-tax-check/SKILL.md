---
name: open-tax-check
description: Use when performing a Chinese enterprise tax health check from journals or ledgers, including field normalization, deterministic tax-risk screening, semantic anomaly triage, professional review, and draft report preparation.
---

# Tax Health Check

Use this skill as a thin workflow controller around the bundled Python engine. Let code perform mapping, validation, calculation, lineage, and state management. Use AI only to explain results, enrich unreviewed candidates, and draft wording.

## Non-negotiable boundaries

- Never call code output “100% accurate.” State the source scope, mapping status, data-quality status, rule version, and assumptions.
- Never silently accept an unresolved field mapping. Stop at `needs_mapping_review`.
- Never turn a keyword or semantic match into a tax conclusion. Its status remains `candidate` until a tax professional reviews it.
- Never put an unreviewed finding into a client-facing conclusion.
- Never issue an overall numeric health score in v0.1.
- Treat the output as advisory screening, not an audit, assurance report, legal opinion, or tax authority determination.
- Preserve source lineage. Every finding must remain traceable to source file, sheet, row, voucher, rule, and rule version where available.
- Stop at `blocked_data_quality` / `resolve_data_quality_findings` when a required row value or date is missing or invalid, both amount fields are blank, or a voucher is unbalanced within its accounting period.

## Supported scope in v0.1

- One legal entity per case.
- UTF-8/UTF-8-BOM CSV journal input.
- Deterministic field mapping, voucher-balance validation, initial hard rules, deterministic semantic candidate rules, JSON findings, and Markdown internal draft.
- Supported AI modes are exactly `off`, `assist`, and `deep`; use `off` when AI is unavailable.
- Excel, OCR, tax returns, external company data, polished workbooks, and PDF rendering are later adapters. Do not claim they are supported by v0.1.

## Workflow

### 1. Initialize a resumable case

Run:

```text
python <skill-dir>/scripts/opentaxcheck.py init --case <case-dir> --entity "企业全称"
```

Put exactly one journal CSV in `<case-dir>/input/`; it must be a regular, non-symlink CSV, and the adapter recognizes a case-insensitive `.csv` suffix. Keep original source files unchanged. Regular non-CSV files, directories and symbolic links, and other top-level entries are unsupported; stop at `blocked_data_quality` with `next_action` set to `remove_unsupported_input_files`. If the folder contains zero or multiple eligible CSV files, use `provide_exactly_one_csv_journal`. Rows with extra or missing cells are blocking structure findings and must never be inferred or repaired by AI. Posting dates and numeric values must use the deterministic forms documented in [references/canonical-schema.md](references/canonical-schema.md).

The Phase 1 numeric gate also blocks when any value exceeds its precision or scale limit, or when the dataset's aggregate absolute amount exceeds the documented limit. Do not split, round, truncate, or summarize values to bypass this gate.

### 2. Run the deterministic engine

```text
python <skill-dir>/scripts/opentaxcheck.py run --case <case-dir> --ai-mode off
```

Use `assist` or `deep` only if the host can provide an AI layer. These modes do not relax any review gate.

### 3. Follow `state.json`

Read `status` and `next_action`; do not improvise around a blocked state.

`state.json` points to the current run. Each attempt also has an immutable `runs/run-XXXX/manifest.json`; use that history when comparing a blocked rerun with an earlier successful result. The top-level `work/` and `output/` directories are only the current-run view.

| Status | Required action |
| --- | --- |
| `ready_for_input` | Add the source journal. |
| `needs_mapping_review` | Confirm or extend the field mapping before calculation. |
| `blocked_data_quality` | Resolve or document blocking data-quality exceptions. |
| `blocked_processing_error` | Follow `inspect_processing_error` and inspect the failed run's safe error category and completed artifacts. |
| `needs_professional_review` | Review every finding and supporting evidence. |

Exit code `0` means screening completed and awaits professional review. `3` means mapping review is required. `4` means a blocking data-quality issue exists. Exit code `5` means a processing error was captured in a failed run and requires inspection; do not bypass that run or reuse an earlier output as the current result.

### 4. Review findings

For each finding, confirm source lineage, calculation basis, applicable period, taxpayer type, legal authority, missing evidence, and whether the issue is `suspected`, `confirmed`, `closed`, or `insufficient_data`.

Do not overwrite the original candidate. Record reviewer, review time, conclusion, rationale, evidence, and any adjusted exposure as a review layer.

### 5. Prepare the deliverable

Only reviewed findings with an approved report status may enter a client-facing report. Always disclose:

- entity and period;
- documents received and missing;
- procedures performed and procedures not performed;
- data-quality limitations;
- rule versions and material assumptions;
- that the work is a tax health check, not assurance.

## Reference routing

- Read [references/methodology.md](references/methodology.md) before changing workflow gates or report language.
- Read [references/canonical-schema.md](references/canonical-schema.md) before adding input formats or field aliases.
- Read [references/rule-catalog.md](references/rule-catalog.md) before changing tax logic, thresholds, legal citations, or rule versions.

## Common mistakes

- Calculating from a visually plausible but unconfirmed column mapping.
- Treating a 12-month shareholder receivable as automatic deemed dividends.
- Describing the full business-entertainment expense as the tax adjustment.
- Using a current tax rule for the wrong tax year.
- Letting attractive report formatting hide incomplete data coverage.
- Asking the AI to infer missing figures instead of recording insufficient data.
