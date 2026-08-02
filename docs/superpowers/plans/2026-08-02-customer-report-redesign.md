# Customer Report Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the machine-list report with a 5–8 page owner-friendly main report followed by a complete professional appendix, all generated from one immutable snapshot.

**Architecture:** Introduce a report view model that converts machine statuses into four approved Chinese client states, render Markdown from that model, and build a structured DOCX with cover, tables, headers, footers, and appendix separation. Delivery continues to bind every artifact to one revision and hash set.

**Tech Stack:** Python, dataclasses, deterministic Markdown/OOXML, ZIP/XML inspection, existing PDF renderer, Poppler visual QA, pytest.

## Global Constraints

- Main report is 5–8 rendered pages; appendix length is unrestricted.
- No overall health score or precise material-completeness percentage.
- No internal status codes, formula IDs, or raw JSON keys in the main report.
- Unreviewed candidates are excluded from client conclusions.
- “本次范围内未见异常” never means no risk.
- PDF is optional and must report partial delivery when no reliable renderer exists.
- No push, merge, tag, Release, or store publication is authorized.

---

### Task 1: Build the Report View Model

**Files:**
- Create: `src/open_tax_check/report_view.py`
- Create: `tests/test_report_view.py`
- Modify: `src/open_tax_check/tax_reporting.py`

**Interfaces:**
- Produces: `build_report_view(snapshot: TaxCheckSnapshot, *, entity_name: str | None = None, reviewed_findings: Sequence[Mapping[str, object]] = ()) -> ReportView`
- Produces dataclasses: `ReportView`, `ExecutiveSummary`, `ClientIssue`, `MaterialGap`, `ActionItem`, `AppendixTaxItem`.

- [ ] **Step 1: Write failing state-mapping tests**

```python
def test_report_view_uses_four_approved_chinese_states(snapshot):
    view = build_report_view(snapshot)
    assert {item.client_state for item in view.tax_items} <= {
        "重点核实", "待补资料", "账务勾稽提示", "本次范围内未见异常"
    }
    assert all(item.client_state != "无风险" for item in view.tax_items)
```

- [ ] **Step 2: Write failing candidate and amount tests**

```python
def test_view_excludes_unreviewed_candidates_and_labels_trial_amount(snapshot, unreviewed_findings):
    view = build_report_view(snapshot, reviewed_findings=unreviewed_findings)
    assert all(issue.source_kind != "unreviewed_candidate" for issue in view.issues)
    assert view.executive_summary.amount_disclaimer_zh == "试算金额不代表应补税额、欠税金额或最终申报金额。"
```

- [ ] **Step 3: Run RED tests**

Run: `python -m pytest tests/test_report_view.py -q`

Expected: FAIL because `report_view` does not exist.

- [ ] **Step 4: Implement immutable view types and mapping**

```python
@dataclass(frozen=True, slots=True)
class ClientIssue:
    title_zh: str
    client_state: str
    observed_zh: str
    why_it_matters_zh: str
    cannot_conclude_zh: str
    required_materials_zh: tuple[str, ...]
    next_action_zh: str

@dataclass(frozen=True, slots=True)
class ReportView:
    report_status_zh: str
    executive_summary: ExecutiveSummary
    issues: tuple[ClientIssue, ...]
    material_gaps: tuple[MaterialGap, ...]
    actions: tuple[ActionItem, ...]
    tax_items: tuple[AppendixTaxItem, ...]
    limitations_zh: tuple[str, ...]
```

Only reviewed approved findings or deterministic discrepancies may create `ClientIssue`. Missing evidence creates `MaterialGap`; reconciliation differences create the neutral accounting prompt state.

- [ ] **Step 5: Run GREEN tests and commit**

Run: `python -m pytest tests/test_report_view.py tests/test_tax_health_reporting.py -q`

Expected: PASS.

```bash
git add src/open_tax_check/report_view.py src/open_tax_check/tax_reporting.py tests/test_report_view.py
git commit -m "feat: add customer report view model"
```

### Task 2: Render the Owner-Friendly Markdown and Appendix

**Files:**
- Modify: `src/open_tax_check/tax_reporting.py`
- Modify: `tests/test_tax_health_reporting.py`
- Modify: `tests/test_delivery.py`

**Interfaces:**
- Preserves: `render_tax_health_report(snapshot: TaxCheckSnapshot) -> str`
- Adds: `render_report_view(view: ReportView) -> str`.

- [ ] **Step 1: Write failing section-order tests**

```python
def test_markdown_places_owner_report_before_professional_appendix(snapshot):
    report = render_tax_health_report(snapshot)
    headings = [line for line in report.splitlines() if line.startswith("## ")]
    assert headings[:5] == [
        "## 管理层摘要", "## 老板先看这些事", "## 待补资料及影响", "## 建议行动顺序", "## 检查范围和限制"
    ]
    assert headings.index("## 专业附录") > headings.index("## 检查范围和限制")
```

- [ ] **Step 2: Write failing wording tests**

Assert the main report excludes `calculated`, `needs_evidence`, `generic.base_times_rate`, raw fact keys, and unreviewed candidate text. Assert every five-part issue contains “发现了什么”, “为什么需要关注”, “目前不能下什么结论”, “需要补什么资料”, and “建议下一步”.

- [ ] **Step 3: Run RED tests**

Run: `python -m pytest tests/test_tax_health_reporting.py tests/test_delivery.py -q`

Expected: FAIL because the existing report is tax-item-first.

- [ ] **Step 4: Implement deterministic section rendering**

```python
def render_tax_health_report(snapshot: TaxCheckSnapshot) -> str:
    return render_report_view(build_report_view(snapshot))
```

Render the owner section first, then a page-break marker and professional appendix. Put compact source citations in appendix tax items and move the full source catalog to the final appendix section.

- [ ] **Step 5: Run GREEN tests and commit**

Run: `python -m pytest tests/test_tax_health_reporting.py tests/test_delivery.py -q`

Expected: PASS.

```bash
git add src/open_tax_check/tax_reporting.py tests/test_tax_health_reporting.py tests/test_delivery.py
git commit -m "feat: render owner-friendly tax report"
```

### Task 3: Build a Structured Client DOCX

**Files:**
- Modify: `src/open_tax_check/tax_reporting.py`
- Create: `src/open_tax_check/docx_report.py`
- Create: `tests/test_docx_report.py`
- Modify: `tests/test_tax_health_reporting.py`

**Interfaces:**
- Preserves: `write_tax_health_report_docx(snapshot, path) -> Path`
- Produces OOXML with cover, contents list, section break, tables, header, footer, and page fields.

- [ ] **Step 1: Write failing OOXML structure tests**

```python
def test_docx_contains_customer_report_structure(snapshot, tmp_path):
    path = write_tax_health_report_docx(snapshot, tmp_path / "report.docx")
    parts = read_docx_parts(path)
    assert "word/header1.xml" in parts
    assert "word/footer1.xml" in parts
    assert "word/settings.xml" in parts
    assert xml_count(parts["word/document.xml"], "w:tbl") >= 3
    assert "PAGE" in parts["word/footer1.xml"]
    assert "专业附录" in extract_docx_text(path)
```

- [ ] **Step 2: Write failing style and pagination tests**

Require Title, Heading 1–3, body, table-header, issue-state, and disclaimer styles; require a page break before the appendix and repeating table headers.

- [ ] **Step 3: Run RED tests**

Run: `python -m pytest tests/test_docx_report.py tests/test_tax_health_reporting.py -q`

Expected: FAIL because the current DOCX has no tables, header, footer, or pagination fields.

- [ ] **Step 4: Implement focused OOXML builders**

```python
def build_docx_parts(view: ReportView) -> dict[str, bytes]:
    return {
        "word/document.xml": document_xml(view),
        "word/styles.xml": styles_xml(),
        "word/header1.xml": header_xml(view),
        "word/footer1.xml": footer_xml(view),
        "word/settings.xml": settings_xml(),
        "word/_rels/document.xml.rels": document_relationships_xml(),
    }
```

Use Chinese font fallbacks, fixed table widths, repeated headers, keep-with-next headings, and restrained status colors. Do not add a health-score graphic.

- [ ] **Step 5: Run GREEN tests and commit**

Run: `python -m pytest tests/test_docx_report.py tests/test_tax_health_reporting.py -q`

Expected: PASS.

```bash
git add src/open_tax_check/docx_report.py src/open_tax_check/tax_reporting.py tests/test_docx_report.py tests/test_tax_health_reporting.py
git commit -m "feat: build structured client docx report"
```

### Task 4: Bind Every Report Artifact to One Delivery Revision

**Files:**
- Modify: `src/open_tax_check/delivery.py`
- Modify: `src/open_tax_check/evidence_bundle.py`
- Modify: `tests/test_delivery.py`
- Modify: `tests/test_evidence_bundle.py`
- Modify: `scripts/run_fictional_example.py`
- Modify: `tests/test_fictional_example.py`

**Interfaces:**
- Extends `tax-delivery/v1` with report structure version and customer handoff artifact.

- [ ] **Step 1: Write failing revision-binding tests**

```python
def test_all_report_artifacts_share_snapshot_and_report_structure_revision(case_dir):
    delivery = build_case_delivery(case_dir)
    assert delivery["report_schema_version"] == "tax-health-report/v2"
    assert {item["snapshot_revision"] for item in delivery["artifacts"]} == {delivery["revision"]}
    assert all(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in delivery["artifacts"])
```

- [ ] **Step 2: Write the failing missing-artifact test**

Delete one declared report artifact and verify delivery reuse raises `delivery_artifact_invalid` rather than returning the stale pointer.

- [ ] **Step 3: Run RED tests**

Run: `python -m pytest tests/test_delivery.py tests/test_evidence_bundle.py tests/test_fictional_example.py -q`

Expected: FAIL because report schema and per-artifact revision fields are absent.

- [ ] **Step 4: Implement report-schema binding and regenerate the fictional case**

Add `report_schema_version="tax-health-report/v2"` to the delivery manifest and artifact records. Verify Markdown, DOCX, optional PDF, handoff, workpaper, snapshot, and evidence bundle before publishing the pointer.

- [ ] **Step 5: Run GREEN tests and commit**

Run: `python -m pytest tests/test_delivery.py tests/test_evidence_bundle.py tests/test_fictional_example.py -q`

Expected: PASS.

```bash
git add src/open_tax_check/delivery.py src/open_tax_check/evidence_bundle.py scripts/run_fictional_example.py tests/test_delivery.py tests/test_evidence_bundle.py tests/test_fictional_example.py
git commit -m "feat: bind report artifacts to one revision"
```

### Task 5: Perform Structural and Visual Report QA

**Files:**
- Create: `docs/qa/customer-report-v2-checklist.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces reproducible QA evidence for the complete fictional report.

- [ ] **Step 1: Run focused and full tests**

Run: `python -m pytest tests/test_report_view.py tests/test_tax_health_reporting.py tests/test_docx_report.py tests/test_delivery.py tests/test_fictional_example.py -q`

Run: `python -m pytest -q`

Expected: PASS with only established optional-environment skips.

- [ ] **Step 2: Generate the complete fictional delivery**

Run: `python scripts/run_fictional_example.py --root work/qa-report-v2 --complete`

Expected: Markdown, DOCX, workpaper, snapshot, handoff, evidence bundle, and delivery manifest are present; PDF is present only when a reliable renderer exists.

- [ ] **Step 3: Inspect DOCX structure**

Run OOXML tests and extract all text and relationships. Confirm cover, table count, headings, header/footer, page field, appendix break, and no undeclared external relationship.

- [ ] **Step 4: Perform visual QA**

If a reliable renderer exists, render every page and inspect Chinese text, overlap, clipping, table continuation, orphan headings, blank pages, and owner-section length of 5–8 pages. If no renderer exists, record `visual_qa=not_performed_renderer_unavailable`; do not mark it passed.

- [ ] **Step 5: Build and validate release artifacts**

Run: `python -m build`

Run: `python scripts/validate_release.py --strict --require-distributions`

Expected: build and strict validation pass.

- [ ] **Step 6: Document evidence and commit**

```bash
git add docs/qa/customer-report-v2-checklist.md CHANGELOG.md
git commit -m "docs: record customer report v2 validation"
```
