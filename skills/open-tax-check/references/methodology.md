# Methodology and control gates

## Purpose

This skill performs a Chinese enterprise tax-risk screening designed for professional review. It separates deterministic processing from judgment so that a low-capability host agent can run the same core workflow.

## Processing layers

1. Intake: preserve originals and record entity, period, file inventory, and hashes when available.
2. Normalization: map source headers to the canonical schema and retain source coordinates.
3. Data quality: validate required row values and calendar dates, then reconcile debits and credits by voucher number within each accounting period before tax calculation.
4. Hard rules: run versioned formulas on mapped, validated data.
5. Semantic screening: create candidates from summaries and account mismatches; do not conclude.
6. Professional review: obtain evidence, apply the law for the relevant period, and record reviewer rationale.
7. Reporting: render only approved reviewed findings and disclose scope limitations.

## Separation of duties

Python owns arithmetic, deterministic matching, identifiers, lineage, rule versions, state, and repeatable rendering. AI may describe patterns, prioritize candidates, suggest evidence requests, or draft prose. A tax professional owns the conclusion, severity, exposure, recommendation, and release decision.

Missing or invalid dates block hard rules. The engine derives the tax year and accounting period only from explicitly supported date forms; neither Python nor AI may infer a missing or ambiguous date. Blocking row diagnostics must not copy narratives or source amounts into the finding.

## Finding lifecycle

`candidate` → `suspected` / `confirmed` / `closed` / `insufficient_data`

The transition requires a review record. Preserve the original engine result so later reviewers can distinguish source facts, system calculation, AI commentary, and professional judgment.

## Reporting boundary

The report is consulting advice based on documents supplied and procedures described. It is not a statutory audit, assurance engagement, legal opinion, tax filing, or binding interpretation by a tax authority. Never hide missing inputs behind a health score.
