# 本地 PDF/图片 OCR 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 OpenTaxCheck 增加离线、可预算、可审计的数字 PDF 原生提取和扫描 PDF/PNG/JPEG PaddleOCR 能力。

**Architecture:** 扩展 `DocumentExtraction` 为向后兼容的 v1.1 合同；`ocr_runtime.py` 管理可选依赖与显式模型清单，`local_ocr.py` 负责安全检查、按页路由和结果合并。CLI 只使用已准备的本地模型，原子写入案件产物，绝不在案件运行中静默联网。

**Tech Stack:** Python 3.11–3.13、`pypdf>=6.14.2,<7`、`pypdfium2>=5.12.1,<6`、`Pillow>=12.3,<13`、`paddleocr>=3.7,<4`、`paddlepaddle>=3.3.1,<4`、pytest。

## Global Constraints

- 核心安装继续保持零运行时依赖；所有 OCR 依赖只进入 `open-tax-check[ocr]`。
- 不采用 PyMuPDF；不实现云 OCR、复杂表格语义、印章识别、税额判断或自动税务结论。
- 案件运行不得下载模型；缺依赖或缺模型返回 `not_performed`，不能伪装成功。
- 单文件 50 MiB、PDF 500 页、单页内容流 32 MiB、单图/单渲染页 50,000,000 像素、总渲染 500,000,000 像素。
- 沿用 100,000 块、单块文本 1 MiB、总 JSON 20 MiB 上限和案件路径/符号链接/Windows 重解析点防护。
- 默认 CPU、受控线程，不自动启用 GPU、远程模型代码或云 inference engine。
- `document-extraction/v1` 必须继续可导入；新输出为 `document-extraction/v1.1`。

---

### Task 1: v1.1 置信度来源与 v1 兼容解析

**Files:**
- Modify: `src/open_tax_check/document_extraction.py`
- Modify: `src/open_tax_check/__init__.py`
- Modify: `tests/test_document_extraction.py`

**Interfaces:**
- Produces `DOCUMENT_EXTRACTION_SCHEMA_VERSION = "document-extraction/v1.1"` and `LEGACY_DOCUMENT_EXTRACTION_SCHEMA_VERSION = "document-extraction/v1"`。
- `TextBlock.confidence: float | None` and `TextBlock.confidence_source: Literal["model", "native_unscored"]`。
- `TableCell` uses the same fields and invariant: `model` requires `[0,1]`; `native_unscored` requires `None`。
- `document_extraction_from_dict()` accepts v1 and v1.1; v1 non-null confidence normalizes to `confidence_source="model"`。

- [ ] **Step 1: Write failing compatibility tests**

Add tests that construct native text with `confidence=None, confidence_source="native_unscored"`, reject mismatched pairs, serialize as v1.1, and import an existing v1 payload without `confidence_source` as `model`.

- [ ] **Step 2: Verify RED**

Run: `.venv/Scripts/python -m pytest tests/test_document_extraction.py -q`

Expected: FAIL because `confidence_source` and legacy/current version handling are absent.

- [ ] **Step 3: Implement the minimal v1.1 parser/serializer**

Keep strict unknown-field rejection version-specific. Do not silently accept a v1.1 block missing `confidence_source`; only v1 receives the compatibility default.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/Scripts/python -m pytest tests/test_document_extraction.py tests/test_document_providers.py tests/test_document_extraction_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/open_tax_check/document_extraction.py src/open_tax_check/__init__.py tests/test_document_extraction.py
git commit -m "feat: evolve document extraction contract to v1.1"
```

### Task 2: 可选依赖、模型清单与显式预热边界

**Files:**
- Modify: `pyproject.toml`
- Create: `src/open_tax_check/ocr_runtime.py`
- Create: `tests/test_ocr_runtime.py`
- Modify: `tests/test_package_contract.py`

**Interfaces:**
- Produces `OcrDependencyError(code: str)`。
- Produces `OcrModelManifest(schema_version, provider, provider_version, model_dir, files)` with file SHA-256 values。
- Produces `load_ocr_dependencies() -> OcrDependencies` using lazy imports only。
- Produces `prepare_ocr_models(model_dir: Path, *, initializer: Callable | None = None) -> OcrModelManifest` and `load_ocr_model_manifest(model_dir: Path) -> OcrModelManifest`。
- Model manifest file is `<model_dir>/open-tax-check-ocr-models.json`; incomplete/hash-mismatched manifests raise `local_ocr_model_unavailable`。

- [ ] **Step 1: Write failing runtime tests**

Test lazy missing-dependency error without installing extras; use a fake initializer that creates two fake model files and verify deterministic manifest hashes; mutate a file and verify load rejects it. Assert the exact `[ocr]` dependency list in `pyproject.toml`.

- [ ] **Step 2: Verify RED**

Run: `.venv/Scripts/python -m pytest tests/test_ocr_runtime.py tests/test_package_contract.py -q`

Expected: FAIL because runtime and `ocr` extra do not exist.

- [ ] **Step 3: Implement runtime and manifest**

Lazy import `pypdf`, `pypdfium2`, `PIL`, `paddleocr`, and `paddle`; never import them from package `__init__`. The production initializer constructs PaddleOCR only inside `prepare_ocr_models`; normal extraction only loads verified local paths.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/Scripts/python -m pytest tests/test_ocr_runtime.py tests/test_package_contract.py -q`

Expected: PASS without OCR extras installed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/open_tax_check/ocr_runtime.py tests/test_ocr_runtime.py tests/test_package_contract.py
git commit -m "feat: add optional OCR runtime and model manifest"
```

### Task 3: 安全文档检查与数字 PDF 原生文本

**Files:**
- Create: `src/open_tax_check/local_ocr.py`
- Create: `tests/test_local_ocr.py`

**Interfaces:**
- Produces `OcrBudgets` with the exact global limits.
- Produces `PdfPageInput(page_number, width, height, native_fragments, needs_ocr)`。
- Produces `inspect_pdf(source, dependencies, budgets) -> tuple[PdfPageInput, ...]`。
- Produces `inspect_image(source, dependencies, budgets) -> ImageInput`。
- Native fragments become `TextBlock(..., confidence=None, confidence_source="native_unscored")`。

- [ ] **Step 1: Write failing safe-inspection tests**

Use fake pypdf/Pillow objects to cover encrypted PDF, 501 pages, 32 MiB content-stream boundary, native text priority, 50,000,000 pixel boundary, multi-frame image, corrupt input and stable issue codes without source text leakage.

- [ ] **Step 2: Verify RED**

Run: `.venv/Scripts/python -m pytest tests/test_local_ocr.py -q`

Expected: FAIL because `local_ocr` does not exist.

- [ ] **Step 3: Implement bounded inspection**

Read at most the approved structures, check content size before `extract_text()`, normalize PDF coordinates to four points, and return typed internal inputs. No Paddle inference occurs in this task.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/Scripts/python -m pytest tests/test_local_ocr.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/open_tax_check/local_ocr.py tests/test_local_ocr.py
git commit -m "feat: add bounded PDF and image inspection"
```

### Task 4: PaddleOCR adapter、按页路由与状态合并

**Files:**
- Modify: `src/open_tax_check/local_ocr.py`
- Modify: `src/open_tax_check/document_providers.py`
- Modify: `tests/test_local_ocr.py`
- Create: `tests/test_local_ocr_provider.py`

**Interfaces:**
- Produces `PaddleOcrBackend.predict(image) -> tuple[OcrLine, ...]` where `OcrLine(text, quadrilateral, confidence)`。
- Produces `LocalOcrProvider.extract(source: LocalDocumentSource, options: ExtractionOptions) -> DocumentExtraction`。
- Provider ID is `local-paddleocr`; PDF blocks may contain both `native_unscored` and `model` confidence sources.

- [ ] **Step 1: Write failing routing and merge tests**

Use fake renderer/backend to verify native pages skip OCR, scanned pages render at 200 DPI, total rendered pixels stop at 500,000,000, quadrilaterals and scores are preserved, one failed page yields `partial`, all failed yields `failed`, missing dependency/model yields `not_performed`.

- [ ] **Step 2: Verify RED**

Run: `.venv/Scripts/python -m pytest tests/test_local_ocr.py tests/test_local_ocr_provider.py -q`

Expected: FAIL because backend/provider are absent.

- [ ] **Step 3: Implement adapter and provider**

Use dependency injection for tests. Production backend uses only verified model paths, CPU and bounded threads. Convert provider exceptions to allowlisted issue codes and never include recognized text in exception messages.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/Scripts/python -m pytest tests/test_local_ocr.py tests/test_local_ocr_provider.py tests/test_document_providers.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/open_tax_check/local_ocr.py src/open_tax_check/document_providers.py tests/test_local_ocr.py tests/test_local_ocr_provider.py
git commit -m "feat: add local PaddleOCR document provider"
```

### Task 5: `prepare-ocr` 与 `extract-document` CLI

**Files:**
- Modify: `src/open_tax_check/cli.py`
- Modify: `tests/test_document_extraction_cli.py`

**Interfaces:**
- Adds `prepare-ocr --model-dir PATH` and `extract-document --case CASE --source SOURCE_ID --provider local-paddleocr --model-dir PATH`。
- `prepare-ocr` prints manifest JSON; `extract-document` atomically writes `work/document-extractions/<source_id>.json`。
- Stable exit codes: dependency/model unavailable `5`, unsafe/invalid source `4`, extraction failed `3`, success/partial `0`。

- [ ] **Step 1: Write failing CLI tests**

Patch runtime/provider factories at process-internal helper boundaries. Verify commands, exit codes, no silent prepare during extraction, source binding, partial result persistence and atomic output.

- [ ] **Step 2: Verify RED**

Run: `.venv/Scripts/python -m pytest tests/test_document_extraction_cli.py -q`

Expected: FAIL because commands and helper boundaries are absent.

- [ ] **Step 3: Implement CLI commands**

Reuse `_write_json_atomic`. Resolve source metadata from the current case inventory; reject source IDs not bound to an existing input file. Do not catch `KeyboardInterrupt` or `SystemExit` as provider failures.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/Scripts/python -m pytest tests/test_document_extraction_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/open_tax_check/cli.py tests/test_document_extraction_cli.py
git commit -m "feat: add local OCR preparation and extraction CLI"
```

### Task 6: Smoke fixtures、CI、公开文档与发布验收

**Files:**
- Create: `tests/fixtures/ocr/fictional-tax-note.png`
- Create: `tests/fixtures/ocr/fictional-digital.pdf`
- Create: `tests/test_ocr_smoke.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/document-extraction-contract.md`
- Modify: `docs/roadmap.md`
- Modify: `scripts/validate_release.py`
- Modify: `tests/test_public_repo_contract.py`
- Modify: `tests/test_release_validation.py`

**Interfaces:**
- OCR smoke is opt-in via `OPEN_TAX_CHECK_RUN_OCR_SMOKE=1` and skips with an explicit reason otherwise.
- Release validator rejects model files, model caches, unknown OCR binaries and non-fixture images/PDFs.

- [ ] **Step 1: Write failing public/release tests**

Assert exact optional-install and prewarm commands, supported formats, offline boundary, platform scope, OCR smoke gate, model exclusion and Phase 3 wording that distinguishes local OCR from unimplemented cloud/complex layout.

- [ ] **Step 2: Verify RED**

Run: `.venv/Scripts/python -m pytest tests/test_public_repo_contract.py tests/test_release_validation.py tests/test_ocr_smoke.py -q`

Expected: FAIL because docs, fixtures, smoke and release rules are absent.

- [ ] **Step 3: Implement fixtures, CI and docs**

Generate fixtures from deterministic test code or commit only the two allowlisted, fully fictional files. CI ordinary matrix runs without `[ocr]`; one Python 3.12 Ubuntu job installs `[ocr]`, prepares models with caching disabled for artifacts, and runs the gated smoke.

- [ ] **Step 4: Run focused, full and release validation**

Run:

```text
.venv/Scripts/python -m pytest tests/test_document_extraction.py tests/test_ocr_runtime.py tests/test_local_ocr.py tests/test_local_ocr_provider.py tests/test_document_extraction_cli.py tests/test_public_repo_contract.py tests/test_release_validation.py -q
.venv/Scripts/python -m pytest -q
.venv/Scripts/python scripts/validate_release.py --strict
```

Expected: focused and full tests pass; strict release validation passes; OCR smoke is either executed with evidence or explicitly skipped, never mislabeled as passed.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/ocr tests/test_ocr_smoke.py .github/workflows/ci.yml README.md docs/document-extraction-contract.md docs/roadmap.md scripts/validate_release.py tests/test_public_repo_contract.py tests/test_release_validation.py
git commit -m "docs: publish local OCR capability and validation"
```

## Self-Review Checklist

- Spec coverage: v1.1 contract Task 1; dependencies/models Task 2; budgets/native PDF Task 3; Paddle routing/status Task 4; CLI Task 5; smoke/CI/release/docs Task 6.
- Placeholder scan: no `TBD`, `TODO`, generic error-handling instruction or undefined interface.
- Type consistency: `OcrDependencies`, `OcrModelManifest`, `OcrBudgets`, `PaddleOcrBackend`, `LocalOcrProvider` are introduced before downstream use.
- Scope check: no cloud OCR, PP-Structure, semantic tax inference or report generation enters this plan.
