# 文档抽取/OCR统一合同实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不接入实际 OCR 服务的前提下，为 PDF、扫描 PDF 和图片建立可审计、可跨宿主交换的统一文档抽取合同。

**Architecture:** 使用 `src/open_tax_check/document_extraction.py` 中的不可变 Python 模型作为内部真相，通过严格的 `to_dict`/`from_dict` 暴露 `document-extraction/v1` JSON。provider 层只负责 `off` 与宿主 Agent 结果导入；授权、路径和来源绑定在边界处强制校验，CLI 只做合同校验/导入。

**Tech Stack:** Python 3.11–3.13、标准库 `dataclasses`/`datetime`/`json`/`pathlib`、现有 `pytest` 测试体系；不增加 OCR、网络或重量级依赖。

## Global Constraints

- 本阶段不接入 PaddleOCR、百度 OCR、其他云服务、网络 SDK 或重量级 OCR 依赖。
- 新合同版本固定为 `document-extraction/v1`，不修改现有 `SCHEMA_VERSION` 或税务规则包版本。
- provider 返回的 `source_id`、文件名和路径不可信，必须绑定到本地案件来源。
- `not_performed`、`partial`、`failed` 不能被下游解释为无风险或证据不存在。
- 路径必须在案件输入目录内，并拒绝符号链接、重解析点和目录逃逸。
- 所有页码、坐标、置信度、数量、文本长度和总 payload 必须有确定上限。
- 工作树中已有的 `tax_reporting.py`、`tax_screening.py`、`tests/test_tax_health.py`、`tests/test_tax_workflow.py` 未提交改动不得覆盖或重置。

---

### Task 1: 不可变模型与严格 JSON 合同

**Files:**
- Create: `src/open_tax_check/document_extraction.py`
- Modify: `src/open_tax_check/__init__.py`
- Create: `tests/test_document_extraction.py`

**Interfaces:**
- Produces `DOCUMENT_EXTRACTION_SCHEMA_VERSION = "document-extraction/v1"`。
- Produces frozen/slotted dataclasses `DocumentPage`, `TextBlock`, `TableCell`, `TableRegion`, `ExtractionIssue`, `AuthorizationRecord`, `DocumentExtraction`。
- Produces `document_extraction_to_dict(value: DocumentExtraction) -> dict` and `document_extraction_from_dict(payload: Mapping[str, object]) -> DocumentExtraction`。
- `DocumentExtraction.status` 取 `not_requested | not_performed | completed | partial | failed`；`provider_kind` 取 `off | host | cloud`。

- [ ] **Step 1: Write the failing test**

在 `tests/test_document_extraction.py` 写测试，覆盖：合法文档结果可构造；`document_extraction_to_dict` 后再 `from_dict` 保持完全相等；未知顶层字段、缺失 `schema_version`、非法 status、非法坐标长度、置信度不在 `[0, 1]`、重复页码和超长文本被拒绝；`not_performed` 可带 issues 但不能伪装为 `completed`。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_document_extraction.py -q`

Expected: FAIL with `ModuleNotFoundError` 或模型/解析函数未定义。

- [ ] **Step 3: Write minimal implementation**

在 `document_extraction.py` 中使用 `@dataclass(frozen=True, slots=True)`。统一坐标为四点 `tuple[tuple[float, float], ...]`，页码从 1 开始；限制至少包括单块文本 1 MiB、块数 100_000、页数 10_000、总 JSON 20 MiB。解析器必须拒绝未知字段并把列表归一化为 tuple；时间使用带时区 ISO-8601 字符串；调用现有 `schemas.to_dict` 只做递归转换，不改变合同校验。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_document_extraction.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/test_document_extraction.py src/open_tax_check/document_extraction.py src/open_tax_check/__init__.py
git commit -m "feat: add document extraction contract models"
```

### Task 2: 授权记录与 `off`/`host` provider 边界

**Files:**
- Create: `src/open_tax_check/document_providers.py`
- Modify: `src/open_tax_check/document_extraction.py`
- Create: `tests/test_document_providers.py`

**Interfaces:**
- Produces `DocumentProvider` protocol with `extract(source: LocalDocumentSource, options: ExtractionOptions) -> DocumentExtraction`。
- Produces `LocalDocumentSource(source_id: str, path: Path, media_type: str, case_input_dir: Path)` and `ExtractionOptions`。
- Produces `OffProvider.extract(...)` returning `status="not_performed"` without reading file bytes。
- Produces `HostProvider.import_result(source, payload)`，对宿主 JSON 严格解析并以本地 `source.source_id`、文件名和媒体类型覆盖不可信字段。
- Produces `AuthorizationError`/`ProviderContractError`，错误结果统一使用 `status="failed"` 的 `DocumentExtraction`，不抛出泄露原文的异常消息。

- [ ] **Step 1: Write the failing test**

测试 `OffProvider` 不读取文件即可返回 `not_performed`；测试 `HostProvider` 能导入合法 payload 并覆盖伪造的 `source_id`；测试路径逃逸、符号链接、重解析点、媒体类型不匹配、未授权 cloud、超限 payload、重复页码均返回 `failed`，且 issues 只包含机器可读代码。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_document_providers.py -q`

Expected: FAIL with provider 模块或协议未定义。

- [ ] **Step 3: Write minimal implementation**

实现受控路径检查：`Path.resolve(strict=True)` 必须位于 `case_input_dir.resolve()` 下；逐级拒绝 `is_symlink()`，Windows 上检测 reparse point。`HostProvider` 先限制原始 JSON 字节数，再调用 Task 1 解析器，并重写 `source_id`、`document_id`、`filename`、`media_type`；任何授权或边界错误构造 `failed` 结果。为 cloud 只提供 `AuthorizationRecord` 校验函数，不实现网络调用。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_document_providers.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/test_document_providers.py src/open_tax_check/document_providers.py src/open_tax_check/document_extraction.py
git commit -m "feat: add document provider authorization boundaries"
```

### Task 3: CLI 合同校验与宿主结果导入

**Files:**
- Modify: `src/open_tax_check/cli.py`
- Modify: `src/open_tax_check/__init__.py`
- Create: `tests/test_document_extraction_cli.py`

**Interfaces:**
- Adds CLI subcommands `validate-extraction --input result.json` and `import-extraction --case CASE_DIR --source SOURCE_ID --input result.json`。
- `validate-extraction` 输出标准 JSON：`{"valid": true, "schema_version": "document-extraction/v1"}`；错误返回非零退出码和机器可读 `error_code`。
- `import-extraction` 只写入案件受控的 `work/document-extractions/<source_id>.json`，写入前使用 `HostProvider` 完成路径和来源绑定。

- [ ] **Step 1: Write the failing test**

使用 `subprocess.run([sys.executable, "-m", "open_tax_check.cli", ...])` 覆盖合法校验、未知字段失败、缺失 source 失败、导入文件落在受控目录、伪造 source_id 被覆盖，以及 `failed` 结果仍被保留且不能变成成功。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_document_extraction_cli.py -q`

Expected: FAIL with未知子命令或 CLI 入口不存在。

- [ ] **Step 3: Write minimal implementation**

沿用现有 argparse 结构添加两个子命令；CLI 只读取 JSON，不读取 OCR 服务。导入采用临时文件 + `os.replace`，目标目录固定在案件 `work` 下；输出通过现有 JSON 工具保证稳定排序和 newline。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_document_extraction_cli.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/test_document_extraction_cli.py src/open_tax_check/cli.py src/open_tax_check/__init__.py
git commit -m "feat: add document extraction validation CLI"
```

### Task 4: 公开合同文档、回归测试与发布边界

**Files:**
- Create: `docs/document-extraction-contract.md`
- Modify: `README.md`
- Modify: `docs/roadmap.md`
- Modify: `tests/test_public_repo_contract.py`
- Modify: `tests/test_package_contract.py`

**Interfaces:**
- Public docs describe JSON fields, status semantics, authorization disclosure and host import examples。
- Public docs explicitly say actual PDF/image OCR providers are not implemented yet。

- [ ] **Step 1: Write the failing test**

在公开合同测试中加入断言：文档包含 `document-extraction/v1`、`not_performed`/`partial`/`failed`、逐文件/逐页授权、`host`/`off` provider，且包含“实际 OCR 尚未实现”；包合同测试确认不新增运行时依赖。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_public_repo_contract.py tests/test_package_contract.py -q`

Expected: FAIL，因为公开文档和断言尚不存在。

- [ ] **Step 3: Write minimal implementation**

新增中文合同文档和 JSON 示例；更新 README/roadmap 的当前能力表，只把“统一合同/导入校验”标为已实现，继续将实际 OCR 标为尚未实现；保持税务健康检查报告名称和免责边界不变。

- [ ] **Step 4: Run focused and full tests**

Run: `python -m pytest tests/test_document_extraction.py tests/test_document_providers.py tests/test_document_extraction_cli.py tests/test_public_repo_contract.py tests/test_package_contract.py -q`

Then run: `python -m pytest -q`

Expected: focused tests and full suite pass；若环境缺少可选 Excel 依赖，只允许现有明确 skip，不得把未执行检查报告为 pass。

- [ ] **Step 5: Commit**

```bash
git add docs/document-extraction-contract.md README.md docs/roadmap.md tests/test_public_repo_contract.py tests/test_package_contract.py
git commit -m "docs: publish document extraction contract boundaries"
```

## Self-Review Checklist

- Spec coverage: 模型/序列化对应 Task 1；provider、授权和安全边界对应 Task 2；CLI 和跨宿主导入对应 Task 3；公开声明和回归验收对应 Task 4。
- Placeholder scan: 无 `TBD`、`TODO` 或未定义的后续动作；每一步都有文件、接口、测试命令和预期结果。
- Type consistency: Task 1 定义的 `DocumentExtraction`、`AuthorizationRecord` 被 Task 2 直接复用；Task 2 的 `HostProvider` 被 Task 3 复用；版本号统一为 `document-extraction/v1`。
- Scope check: 没有加入实际 OCR、云服务、规则包或报告生成，符合已批准设计的单一子项目边界。
