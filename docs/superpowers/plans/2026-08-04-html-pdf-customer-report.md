# HTML/PDF Customer Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the DOCX-centered customer report with a deterministic fixed-layout HTML master that always produces the only official customer deliverable, `税务健康检查报告.pdf`.

**Architecture:** Extend the immutable report view model to `tax-health-report/v3`, render it through an offline HTML/CSS template, and export A4 PDF through Playwright-managed Chromium. Delivery remains transactional: PDF/font/structure validation must succeed before a revision pointer is published; machine snapshot, workpaper, evidence bundle, and internal HTML remain audit artifacts.

**Tech Stack:** Python 3.11-3.13, dataclasses, HTML5/CSS print layout, Playwright/Chromium, pypdf, pytest.

## Global Constraints

- The official customer artifact is exactly `税务健康检查报告.pdf`; do not generate or deliver Word/DOCX.
- HTML is a fixed rendering master and controlled intermediate artifact, not an editable customer report.
- Use `tax-health-report/v3`; preserve existing v2 revision directories as immutable history.
- A PDF renderer or bundled report font failure is a hard delivery failure.
- Do not load fonts, CSS, JavaScript, or images from the network while rendering.
- Do not produce a health score, risk probability, or unsupported percentage.
- Separate material-processing status from tax-risk severity.
- An uploaded file must never be described as “未提供”.
- “本次检查范围内未触发异常” must not be shortened to “无风险”.
- Trial amounts must retain `试算金额不代表应补税额、欠税金额或最终申报金额。`.
- Use TDD, a focused verification command for every task, and a fresh review before each task commit.

---

## File Map

- `src/open_tax_check/report_view.py`: immutable v3 customer content contract and deterministic sorting.
- `src/open_tax_check/html_report.py`: HTML escaping, semantic document rendering, local asset URLs, and print CSS composition.
- `src/open_tax_check/pdf_report.py`: Playwright Chromium discovery, offline render, PDF export, and structural validation.
- `src/open_tax_check/report_assets/__init__.py`: installed-package resource anchor.
- `src/open_tax_check/report_assets/report.css`: fixed A4 visual system.
- `src/open_tax_check/report_assets/fonts/`: bundled OFL-licensed Noto Sans CJK SC Regular/Bold files and license.
- `src/open_tax_check/tax_reporting.py`: retain machine/Markdown helpers; expose v3 HTML/PDF entry points and remove DOCX entry points.
- `src/open_tax_check/delivery.py`: atomic v3 PDF-first publication and artifact manifest.
- `src/open_tax_check/evidence_bundle.py`: v3 schema binding and HTML audit-artifact handling.
- `src/open_tax_check/cli.py`: PDF-default customer command; remove `--include-docx` and optional-PDF behavior.
- `src/open_tax_check/__init__.py`: public API migration.
- `pyproject.toml`: report dependencies and package data.
- `tests/test_report_view.py`: v3 content, severity, source status, and ordering.
- `tests/test_html_report.py`: semantic HTML and print-layout contract.
- `tests/test_pdf_report.py`: renderer failures and real PDF structure.
- `tests/test_delivery.py`: transactional v3 artifact publication.
- `tests/test_tax_health_cli.py`: PDF-only CLI behavior.
- `tests/test_evidence_bundle.py`: v3 evidence binding.
- `tests/test_fictional_example.py`: end-to-end customer artifact expectations.
- `scripts/run_fictional_example.py`: default PDF generation.
- `scripts/validate_release.py`: packaged asset and report-extra validation.
- `docs/qa/customer-report-v3-checklist.md`: reproducible PDF evidence record.

### Task 1: Define the v3 customer report contract

**Files:**
- Modify: `src/open_tax_check/report_view.py`
- Modify: `tests/test_report_view.py`

**Interfaces:**
- Consumes: `TaxCheckSnapshot`, reviewed finding mappings, structured-document index entries.
- Produces: `ReportViewV3`, `ReportMeta`, `RiskItem`, `MaterialStatusItem`, `TaxCoverageItem`, and `build_report_view_v3(...) -> ReportViewV3`.

- [ ] **Step 1: Write failing tests for report metadata, four risk bands, tax coverage, and six material states**

```python
def test_v3_separates_risk_severity_from_received_material_state():
    view = build_report_view_v3(
        snapshot_with_every_status(),
        entity_name="虚构科技有限公司",
        reviewed_findings=({"title_zh": "业务招待费扣除核对", "severity": "high", "review_state": "approved"},),
        material_statuses=({"source_id": "cit-return", "display_name_zh": "企业所得税申报表", "status": "received_pending"},),
    )
    assert view.risk_overview.high_count == 1
    assert view.materials[0].status_zh == "已收到待处理"
    assert "未提供" not in view.materials[0].status_zh
```

- [ ] **Step 2: Run the focused tests and verify the missing v3 symbols fail**

Run: `python -m pytest tests/test_report_view.py -q`

Expected: FAIL because `build_report_view_v3` and v3 dataclasses do not exist.

- [ ] **Step 3: Add frozen v3 dataclasses and allowlisted state mappings**

```python
MATERIAL_STATUS_ZH = {
    "not_provided": "截至报告生成时未收到",
    "received_pending": "已收到待处理",
    "extraction_partial": "提取不完整或失败",
    "contract_invalid": "合同校验失败",
    "imported_pending_confirmation": "已导入待确认",
    "imported_reconciled": "已导入并核对",
}

RISK_SEVERITY_ORDER = {"high": 0, "medium": 1, "notice": 2}

def build_report_view_v3(
    snapshot: TaxCheckSnapshot,
    *,
    entity_name: str,
    reviewed_findings: Sequence[Mapping[str, object]] = (),
    material_statuses: Sequence[Mapping[str, object]] = (),
) -> ReportViewV3:
    """Build the immutable, customer-safe tax-health-report/v3 view."""
```

Reject unknown material states rather than mapping them to `not_provided`. Sort risks by severity, affected period, tax ID, and stable finding ID; sort actions from the same immutable sequence. Add explicit `can_conclude_zh` and `cannot_conclude_zh` fields, tax coverage entries for every registered tax, appendix executed checks, and report metadata derived from the snapshot revision and period.

- [ ] **Step 4: Add regression tests for wording and deterministic ordering**

```python
assert "无风险" not in serialize_customer_text(view)
assert view.executive_summary.amount_disclaimer_zh == AMOUNT_DISCLAIMER_ZH
assert [item.severity for item in view.risks] == ["high", "medium", "notice"]
assert build_report_view_v3(snapshot, entity_name="甲公司") == build_report_view_v3(snapshot, entity_name="甲公司")
```

- [ ] **Step 5: Run report-view tests**

Run: `python -m pytest tests/test_report_view.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/open_tax_check/report_view.py tests/test_report_view.py
git commit -m "feat: define customer report v3 contract"
```

### Task 2: Build the offline semantic HTML master

**Files:**
- Create: `src/open_tax_check/html_report.py`
- Create: `src/open_tax_check/report_assets/__init__.py`
- Create: `src/open_tax_check/report_assets/report.css`
- Create: `src/open_tax_check/report_assets/fonts/NotoSansCJKsc-Regular.otf`
- Create: `src/open_tax_check/report_assets/fonts/NotoSansCJKsc-Bold.otf`
- Create: `src/open_tax_check/report_assets/fonts/OFL.txt`
- Create: `tests/test_html_report.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `ReportViewV3`.
- Produces: `render_report_html(view: ReportViewV3) -> str` and `write_report_html(view: ReportViewV3, path: Path) -> Path`.

- [ ] **Step 1: Write failing semantic HTML tests**

```python
def test_html_contains_fixed_report_sections_and_no_external_assets(sample_v3_view):
    html = render_report_html(sample_v3_view)
    assert '<section id="executive-summary"' in html
    assert '<section id="risk-overview"' in html
    assert '<section id="tax-coverage"' in html
    assert '<section id="appendix"' in html
    assert "http://" not in html and "https://" not in html
    assert "税务健康检查报告" in html
```

- [ ] **Step 2: Run the test and verify the renderer is missing**

Run: `python -m pytest tests/test_html_report.py -q`

Expected: FAIL with import error for `open_tax_check.html_report`.

- [ ] **Step 3: Add licensed fonts and package-data rules**

Use the official Google Noto CJK release assets `NotoSansCJKsc-Regular.otf` and `NotoSansCJKsc-Bold.otf`, preserve the upstream SIL Open Font License as `OFL.txt`, and verify each font starts with a valid OpenType signature. Add these package rules:

```toml
[project.optional-dependencies]
report = [
  "playwright>=1.54,<2",
  "pypdf>=6.14.2,<7",
]

[tool.setuptools.package-data]
"open_tax_check.report_assets" = ["report.css", "fonts/*.otf", "fonts/OFL.txt"]
```

Add `__init__.py` under `report_assets` so `importlib.resources.files("open_tax_check.report_assets")` resolves in an installed wheel.

- [ ] **Step 4: Implement escaped semantic rendering**

```python
def render_report_html(view: ReportViewV3) -> str:
    css = resources.files("open_tax_check.report_assets").joinpath("report.css").read_text("utf-8")
    body = "".join((_cover(view), _toc(view), _summary(view), _risk_overview(view),
                    _priority_actions(view), _risk_details(view), _tax_coverage(view),
                    _material_scope(view), _appendix(view)))
    return f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>{css}</style></head><body>{body}</body></html>'
```

All customer-controlled values pass through `html.escape(..., quote=True)`. Use semantic `section`, `h1`-`h3`, `table`, `thead`, and stable IDs. Convert the local font files to `data:font/otf;base64,...` at render time so the HTML is offline and portable.

- [ ] **Step 5: Implement the A4 print CSS**

```css
@page { size: A4; margin: 18mm 16mm 17mm; }
html { font-family: "OpenTaxCheck Sans", sans-serif; color: #202428; letter-spacing: 0; }
thead { display: table-header-group; }
tr, .risk-heading, .summary-block { break-inside: avoid; }
h1, h2, h3 { break-after: avoid; }
.page-break { break-before: page; }
```

Define restrained high/medium/notice/material colors, grayscale-safe borders, fixed table layouts, cover treatment, page header/footer, and CSS counters. Do not use watermarks, gradients, health-score rings, or decorative cards.

- [ ] **Step 6: Test escaping, section order, local fonts, and stress content**

Run: `python -m pytest tests/test_html_report.py -q`

Expected: PASS, including a test where `<script>` in an entity name is escaped and no external URL appears in `src`, `href`, or CSS `url(...)`.

- [ ] **Step 7: Commit**

```powershell
git add pyproject.toml src/open_tax_check/html_report.py src/open_tax_check/report_assets tests/test_html_report.py
git commit -m "feat: render fixed offline HTML tax report"
```

### Task 3: Export and validate the official PDF

**Files:**
- Create: `src/open_tax_check/pdf_report.py`
- Create: `tests/test_pdf_report.py`
- Modify: `src/open_tax_check/tax_reporting.py`
- Modify: `src/open_tax_check/__init__.py`

**Interfaces:**
- Consumes: `ReportViewV3`, a destination `Path`, and Playwright-managed Chromium.
- Produces: `write_report_pdf(view: ReportViewV3, path: Path) -> Path`, `validate_report_pdf(path: Path, expected_report_id: str) -> None`, and stable `PdfExportUnavailable` codes.

- [ ] **Step 1: Write failing renderer and failure-atomicity tests**

```python
def test_pdf_renderer_failure_leaves_no_customer_file(sample_v3_view, tmp_path, monkeypatch):
    output = tmp_path / "税务健康检查报告.pdf"
    monkeypatch.setattr("open_tax_check.pdf_report.sync_playwright", None)
    with pytest.raises(PdfExportUnavailable, match="^pdf_renderer_unavailable$"):
        write_report_pdf(sample_v3_view, output)
    assert not output.exists()
```

- [ ] **Step 2: Run and verify the missing module fails**

Run: `python -m pytest tests/test_pdf_report.py -q`

Expected: FAIL with import error.

- [ ] **Step 3: Implement temporary offline Chromium rendering**

```python
with sync_playwright() as runtime:
    browser = runtime.chromium.launch(headless=True)
    page = browser.new_page(locale="zh-CN", viewport={"width": 1240, "height": 1754})
    page.set_content(html, wait_until="load")
    page.wait_for_function("document.fonts.status === 'loaded'")
    page.emulate_media(media="print")
    page.pdf(path=str(temp_pdf), format="A4", print_background=True,
             prefer_css_page_size=True, display_header_footer=False,
             tagged=True, outline=True)
```

Block network requests before `set_content`; any `http`, `https`, or unexpected request raises `pdf_external_resource_blocked`. Render to a sibling temporary file, validate, then atomically replace the destination.

- [ ] **Step 4: Validate PDF structure and customer text**

Use `pypdf.PdfReader` to require a non-encrypted PDF, at least one page, extractable Chinese title, expected report ID, page count within the configured safety limit, and outline entries for the principal sections. Return only allowlisted errors: `pdf_renderer_unavailable`, `pdf_render_timeout`, `pdf_render_failed`, `pdf_font_unavailable`, and `pdf_validation_failed`.

- [ ] **Step 5: Run real PDF tests with the installed report extra**

Run: `python -m playwright install chromium`

Run: `python -m pytest tests/test_pdf_report.py -q`

Expected: PASS and the representative PDF is reopenable by `pypdf`.

- [ ] **Step 6: Remove DOCX public exports and connect the v3 HTML/PDF wrappers**

Remove `write_tax_health_report_docx` from `tax_reporting.py` and `__init__.py`. Keep Markdown helpers only as internal compatibility functions. Make `write_tax_health_report_pdf(...)` build v3 view data and delegate exclusively to `write_report_pdf`; it must never invoke LibreOffice, WPS, or DOCX conversion.

- [ ] **Step 7: Run reporting tests**

Run: `python -m pytest tests/test_tax_health_reporting.py tests/test_pdf_report.py -q`

Expected: PASS with no DOCX export tests remaining.

- [ ] **Step 8: Commit**

```powershell
git add src/open_tax_check/pdf_report.py src/open_tax_check/tax_reporting.py src/open_tax_check/__init__.py tests/test_pdf_report.py tests/test_tax_health_reporting.py
git commit -m "feat: generate validated PDF customer reports"
```

### Task 4: Make v3 PDF mandatory and transactional in case delivery

**Files:**
- Modify: `src/open_tax_check/delivery.py`
- Modify: `src/open_tax_check/evidence_bundle.py`
- Modify: `tests/test_delivery.py`
- Modify: `tests/test_evidence_bundle.py`

**Interfaces:**
- Consumes: existing immutable case snapshot and v3 report context.
- Produces: `build_case_delivery(case_dir: Path, *, include_evidence_files: bool = False) -> dict[str, object]` with a verified PDF or an exception.

- [ ] **Step 1: Change delivery tests to demand the v3 PDF artifact**

```python
result = build_case_delivery(case)
manifest = json.loads((case / result["manifest_path"]).read_text("utf-8"))
assert manifest["report_schema_version"] == "tax-health-report/v3"
assert manifest["status"] == "verified"
assert {item["kind"] for item in manifest["artifacts"]} >= {
    "pdf_report", "html_report", "workpaper", "machine_snapshot", "evidence_bundle"
}
assert "docx_report" not in {item["kind"] for item in manifest["artifacts"]}
```

- [ ] **Step 2: Run and verify current v2/DOCX assertions fail**

Run: `python -m pytest tests/test_delivery.py tests/test_evidence_bundle.py -q`

Expected: FAIL on v2 schema and missing mandatory PDF.

- [ ] **Step 3: Implement transactional v3 publication**

Set `REPORT_SCHEMA_VERSION = "tax-health-report/v3"`. Remove `include_pdf` from options and public signature. Within the existing temporary revision directory, write `report.html`, `report.pdf`, workpaper, snapshot, handoff, and evidence bundle. Validate all hashes before `os.replace(...)`; only then update `work/delivery.json`.

```python
pdf_path = write_tax_health_report_pdf(snapshot, temporary_directory / "report.pdf", **context)
artifacts = [
    BundleArtifact("html_report", html_path, "report.html"),
    BundleArtifact("pdf_report", pdf_path, "税务健康检查报告.pdf"),
    BundleArtifact("workpaper", workpaper_path, WORKPAPER_NAME),
    BundleArtifact("machine_snapshot", snapshot_path, "tax-health-snapshot.json"),
]
```

The customer handoff lists `pdf_report`; `html_report` remains flagged `customer_deliverable: false`. A renderer or validation failure must leave no revision directory and must not update the pointer.

- [ ] **Step 4: Bind evidence manifests to v3 and preserve v2 history**

Evidence manifests created now use `tax-health-report/v3`. Verification of active v3 delivery rejects v2 manifests, while read-only historical tooling may still inspect an explicitly selected v2 revision without rewriting it.

- [ ] **Step 5: Run delivery and evidence tests**

Run: `python -m pytest tests/test_delivery.py tests/test_evidence_bundle.py -q`

Expected: PASS, including tamper, failure cleanup, idempotence, and v2-history tests.

- [ ] **Step 6: Commit**

```powershell
git add src/open_tax_check/delivery.py src/open_tax_check/evidence_bundle.py tests/test_delivery.py tests/test_evidence_bundle.py
git commit -m "feat: publish PDF-only report v3 deliveries"
```

### Task 5: Simplify the CLI to PDF-only customer output

**Files:**
- Modify: `src/open_tax_check/cli.py`
- Modify: `tests/test_tax_health_cli.py`
- Modify: `scripts/run_fictional_example.py`
- Modify: `tests/test_fictional_example.py`

**Interfaces:**
- Consumes: `tax-health-check` request or case delivery command.
- Produces: `税务健康检查报告.pdf` by default and JSON containing `pdf_path`, `report_schema_version`, and verification status.

- [ ] **Step 1: Write failing CLI tests for mandatory PDF and removed flags**

```python
assert cli.main() == 0
payload = json.loads(capsys.readouterr().out)
assert Path(payload["pdf_path"]).name == "税务健康检查报告.pdf"
assert payload["report_schema_version"] == "tax-health-report/v3"
assert "docx_path" not in payload
assert not (output_dir / "税务健康检查报告.docx").exists()
```

Also assert `--include-docx` and `--include-pdf` are rejected as unknown legacy flags with a stable usage exit.

- [ ] **Step 2: Run and verify the legacy CLI behavior fails**

Run: `python -m pytest tests/test_tax_health_cli.py tests/test_fictional_example.py -q`

Expected: FAIL because PDF is optional and DOCX remains exposed.

- [ ] **Step 3: Remove legacy report switches and make PDF the default**

Delete `--include-docx` and `--include-pdf`. The command always calls the v3 PDF renderer; Markdown and HTML are internal artifacts only. Map allowlisted PDF errors to operational exit code `3` without exposing paths or subprocess output.

- [ ] **Step 4: Update the fictional example**

Make `scripts/run_fictional_example.py` generate the official PDF and assert the report is present before printing completion. Keep the fictional company label and all tax/evidence caveats.

- [ ] **Step 5: Run CLI and example tests**

Run: `python -m pytest tests/test_tax_health_cli.py tests/test_fictional_example.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/open_tax_check/cli.py scripts/run_fictional_example.py tests/test_tax_health_cli.py tests/test_fictional_example.py
git commit -m "feat: make PDF the only customer report output"
```

### Task 6: Harden packaging and installed-wheel validation

**Files:**
- Modify: `scripts/validate_release.py`
- Modify: `scripts/validate_installed_wheel.py`
- Modify: `tests/test_release_validation.py`
- Modify: `README.md`
- Modify: `skills/open-tax-check/SKILL.md`
- Modify: `skills/open-tax-check/references/host-integrations.md`

**Interfaces:**
- Consumes: built wheel/sdist and an installed Playwright Chromium runtime.
- Produces: strict release validation proving fonts, license, CSS, HTML renderer, and representative PDF work from the installed wheel.

- [ ] **Step 1: Add failing release tests for required report assets**

```python
assert wheel_contains("open_tax_check/report_assets/report.css")
assert wheel_contains("open_tax_check/report_assets/fonts/OFL.txt")
assert wheel_contains("open_tax_check/report_assets/fonts/NotoSansCJKsc-Regular.otf")
assert wheel_contains("open_tax_check/report_assets/fonts/NotoSansCJKsc-Bold.otf")
```

Require the font files to have valid OpenType signatures and prohibit `.docx` customer artifacts in packaged examples and documentation.

- [ ] **Step 2: Run release tests and verify missing asset rules fail**

Run: `python -m pytest tests/test_release_validation.py -q`

Expected: FAIL until package validation is updated.

- [ ] **Step 3: Adjust size policy narrowly for licensed font assets**

Keep the existing 1 MiB per-file ceiling for all ordinary files. Add a path-specific allowance only for the two named font files, capped at their reviewed committed sizes plus 1 KiB. Require `OFL.txt`; do not add a general large-file exception.

- [ ] **Step 4: Extend isolated-wheel validation**

Install `.[report]`, install Playwright Chromium, import assets via `importlib.resources`, render a fictional v3 PDF, validate its title/report ID, and confirm no DOCX is created.

- [ ] **Step 5: Update customer and host instructions**

Document `pip install open-tax-check[report]` followed by `python -m playwright install chromium`. State in Chinese that the AI 智能体 may use its own OCR/vision, while OpenTaxCheck produces a fixed PDF and does not provide Word. Remove commands that recommend `--include-docx` or optional PDF.

- [ ] **Step 6: Run packaging verification**

Run: `python -m pytest tests/test_release_validation.py -q`

Run: `python -m build`

Run: `python scripts/validate_release.py --strict --require-distributions`

Run: `python scripts/validate_installed_wheel.py --dist dist`

Expected: all commands PASS with the report assets loaded from the installed wheel.

- [ ] **Step 7: Commit**

```powershell
git add README.md skills/open-tax-check/SKILL.md skills/open-tax-check/references/host-integrations.md scripts/validate_release.py scripts/validate_installed_wheel.py tests/test_release_validation.py
git commit -m "build: validate installed PDF report runtime"
```

### Task 7: Run full regression and actual PDF visual QA

**Files:**
- Create: `docs/qa/customer-report-v3-checklist.md`
- Modify: any report file only when a failing check has a reproduced cause and regression test.

**Interfaces:**
- Consumes: representative normal, long-text, no-risk, and many-material-gap cases.
- Produces: reproducible automated, structural, cross-platform, and visual evidence for report v3.

- [ ] **Step 1: Run the full automated suite**

Run: `python -m pytest -q`

Expected: all tests pass; skipped optional checks are recorded as skipped, never as passed.

- [ ] **Step 2: Build four PDFs**

Generate fixtures for ordinary, long-text pressure, no approved risk, and many material gaps. Record command, snapshot revision, report ID, SHA-256, page count, and renderer version in the checklist.

- [ ] **Step 3: Render every PDF page to PNG and inspect it**

Use `pypdfium2` at 144 DPI. Check every page for consistent Chinese fonts, missing glyphs, overlaps, clipped tables, orphan headings, empty pages, incorrect TOC page numbers, broken grayscale severity cues, and unexpected system text.

- [ ] **Step 4: Verify live links and PDF outlines**

Use `pypdf` to inspect the outline tree and annotations. Every top-level report section must have an outline entry; internal TOC targets and official rule-source links must resolve to valid destinations or HTTPS URLs.

- [ ] **Step 5: Run cross-platform CI**

Require Windows, macOS, and Linux jobs on Python 3.11-3.13 to pass structural report tests. Run real Chromium PDF smoke at least once per OS; label any unavailable browser install as pending rather than passed.

- [ ] **Step 6: Request independent code review**

Review specifically for conclusion escalation, HTML injection, external-resource leakage, delivery atomicity, font licensing, dependency installation, and v2 history preservation. Resolve each actionable finding with a focused regression test.

- [ ] **Step 7: Re-run final release evidence**

Run: `python -m pytest -q`

Run: `python -m build`

Run: `python scripts/validate_release.py --strict --require-distributions`

Run: `python scripts/validate_installed_wheel.py --dist dist`

Expected: PASS, plus four visually reviewed PDF sets and recorded cross-platform CI evidence.

- [ ] **Step 8: Commit QA evidence**

```powershell
git add docs/qa/customer-report-v3-checklist.md
git commit -m "docs: record customer report v3 validation"
```

### Task 8: Final branch audit and handoff

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: all Task 1-7 commits and verification evidence.
- Produces: a review-ready branch with an explicit breaking-change note; no push or merge without user authorization.

- [ ] **Step 1: Audit the final diff**

Run: `git diff --check origin/main...HEAD`

Run: `git status --short`

Run: `rg -n "include-docx|docx_report|税务健康检查报告\.docx|tax-health-report/v2" src tests scripts README.md skills docs --glob '!docs/superpowers/specs/2026-08-02-customer-report-redesign-design.md' --glob '!docs/qa/customer-report-v2-checklist.md'`

Expected: only intentional v2 compatibility/history references remain; no active DOCX customer path remains.

- [ ] **Step 2: Record the breaking output change**

Document that v3 removes Word/DOCX, makes PDF mandatory, adds the `report` extra and Chromium install requirement, and leaves historical v2 deliveries immutable.

- [ ] **Step 3: Run the final fresh verification set**

Repeat the Task 7 automated, build, installed-wheel, and PDF visual checks from a clean working tree. Record exact results without converting skipped checks into passes.

- [ ] **Step 4: Commit release notes**

```powershell
git add CHANGELOG.md
git commit -m "docs: describe PDF-only report v3 migration"
```

- [ ] **Step 5: Hand off with finishing-a-development-branch**

Offer the user the normal finish options. Do not push, create a PR, merge, tag, or publish a release until the user explicitly chooses that action.
