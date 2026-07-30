# Structured Financial and Tax Documents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为资产负债表、利润表、现金流量表和纳税申报表提供严格、来源绑定、可由宿主 AI 安全导入的 v1 数据合同。

**Architecture:** 新增一个纯领域合同模块，使用不可变 dataclass、严格字段集合、受限十进制金额和稳定错误码。来源安全由独立路径模块复用到现有文档抽取和新结构化文档 provider；CLI 只导入经过案件来源重新绑定的合同，并原子写入案件工作目录。

**Tech Stack:** Python 3.11+、标准库 `dataclasses`/`datetime`/`decimal`/`json`/`pathlib`、现有安全路径与原子写入模式、pytest/unittest。

## Global Constraints

- Schema 名称必须精确为 `financial-statement/v1` 和 `tax-return/v1`。
- 顶层、嵌套和列表元素都拒绝未知或缺失字段。
- JSON 上限 20 MiB；单份文档最多 10,000 行、1,000 个问题和 10,000 个来源锚点。
- 金额只能是普通 ASCII 十进制字符串，最多 18 位整数和 2 位小数；不接受浮点数、指数记法、全角数字、`NaN` 或 `Infinity`。
- 币种首版只接受 `CNY`；`unit_multiplier` 只接受字符串 `1`、`1000` 或 `10000`。
- 宿主 payload 中的 `source_id`、文件名、媒体类型、实体和期间不受信任；导入时必须用案件清单重新绑定和核对。案件清单中的 `sha256` 必须与读取文件的实际 SHA-256 一致，合同通过受信任 `source_id` 与该清单记录绑定。
- 绝对路径、目录逃逸、符号链接和 Windows reparse point 必须在读取前拒绝。
- `proposed` 或 `rejected` 行不得产生客户确认事实；AI 不得升级确认状态。
- 稳定错误只返回错误码，不回显路径、客户值或模型响应正文。
- 所有写入使用同目录临时文件和 `os.replace`；失败不覆盖已存在的有效结果。
- 使用 Windows PowerShell 和仓库虚拟环境；不得要求 Bash、Docker、网络或系统级 LibreOffice。
- 本计划不得 push、merge、tag、release 或 deploy。
- 本 worktree 不复制虚拟环境。每个 PowerShell 验证块先执行 `$python=(Resolve-Path '..\\..\\.venv\\Scripts\\python.exe').Path` 和 `$env:PYTHONPATH=(Resolve-Path 'src').Path`，随后用 `& $python` 运行模块。

---

### Task 1: Shared Primitives and Financial Statement Contract

**Files:**
- Create: `src/open_tax_check/structured_documents.py`
- Create: `tests/test_structured_documents.py`

**Interfaces:**
- Produces: `SourceAnchor`, `StructuredIssue`, `FinancialStatementLine`, `FinancialStatement`。
- Produces: `financial_statement_from_dict(payload: Mapping[str, object]) -> FinancialStatement`。
- Produces: `financial_statement_to_dict(value: FinancialStatement) -> dict[str, object]`。

- [ ] **Step 1: Write failing strict-contract tests**

Create `tests/test_structured_documents.py` with literal fixtures:

```python
from copy import deepcopy

import pytest

from open_tax_check.structured_documents import (
    FinancialStatement,
    financial_statement_from_dict,
    financial_statement_to_dict,
)


def financial_payload():
    return {
        "schema_version": "financial-statement/v1",
        "statement_id": "balance-2026-12",
        "source_id": "host-untrusted",
        "entity_name": "完全虚构科技有限公司",
        "statement_type": "balance_sheet",
        "period_start": "2026-01-01",
        "period_end": "2026-12-31",
        "as_of_date": "2026-12-31",
        "currency": "CNY",
        "unit_multiplier": "1",
        "lines": [{
            "line_code": "BS-ASSETS-TOTAL",
            "original_name": "资产总计",
            "standard_name": "资产总计",
            "current_amount": "1000.00",
            "prior_amount": "800.00",
            "source_anchor": {
                "source_id": "host-untrusted",
                "page_number": None,
                "sheet_name": "资产负债表",
                "cell": "D32",
                "coordinates": None,
            },
            "confidence_source": "template",
            "confirmation_state": "confirmed",
        }],
        "totals": {"assets": "1000.00", "liabilities_and_equity": "1000.00"},
        "issues": [],
    }


def test_financial_statement_v1_round_trips_exactly():
    value = financial_statement_from_dict(financial_payload())
    assert isinstance(value, FinancialStatement)
    assert financial_statement_to_dict(value) == financial_payload()


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(extra=True),
    lambda value: value.pop("currency"),
    lambda value: value["lines"][0].update(extra=True),
    lambda value: value.update(schema_version="financial-statement/v0"),
    lambda value: value.update(currency="USD"),
    lambda value: value.update(unit_multiplier="100"),
    lambda value: value["lines"][0].update(current_amount="1e3"),
    lambda value: value["lines"][0].update(current_amount=1000.0),
    lambda value: value["lines"][0].update(confirmation_state="approved"),
])
def test_financial_statement_rejects_non_contract_payloads(mutate):
    payload = deepcopy(financial_payload())
    mutate(payload)
    with pytest.raises(ValueError):
        financial_statement_from_dict(payload)
```

- [ ] **Step 2: Run the new tests and observe RED**

Run:

```powershell
$python=(Resolve-Path '..\..\.venv\Scripts\python.exe').Path
$env:PYTHONPATH=(Resolve-Path 'src').Path
& $python -m pytest tests/test_structured_documents.py -q
```

Expected: collection fails because `open_tax_check.structured_documents` does not exist.

- [ ] **Step 3: Implement immutable primitives and exact parsing**

In `structured_documents.py` define these exact public types:

```python
@dataclass(frozen=True, slots=True)
class SourceAnchor:
    source_id: str
    page_number: int | None
    sheet_name: str | None
    cell: str | None
    coordinates: tuple[tuple[float, float], ...] | None


@dataclass(frozen=True, slots=True)
class StructuredIssue:
    code: str
    severity: str
    message_zh: str
    line_code: str | None
    source_anchor: SourceAnchor | None


@dataclass(frozen=True, slots=True)
class FinancialStatementLine:
    line_code: str
    original_name: str
    standard_name: str
    current_amount: str | None
    prior_amount: str | None
    source_anchor: SourceAnchor
    confidence_source: str
    confirmation_state: str


@dataclass(frozen=True, slots=True)
class FinancialStatement:
    schema_version: str
    statement_id: str
    source_id: str
    entity_name: str
    statement_type: str
    period_start: str
    period_end: str
    as_of_date: str | None
    currency: str
    unit_multiplier: str
    lines: tuple[FinancialStatementLine, ...]
    totals: tuple[tuple[str, str], ...]
    issues: tuple[StructuredIssue, ...]
```

Use exact key sets at every mapping boundary. Accept statement types only from `balance_sheet`, `income_statement`, and `cash_flow_statement`; confidence sources only from `template`, `host_ai`, `customer`, and `native`; confirmation states only from `proposed`, `confirmed`, and `rejected`. Validate ISO dates with `date.fromisoformat`, non-empty identifiers/text, A1 cells with `^[A-Z]{1,3}[1-9][0-9]{0,6}$`, four-point coordinates in `[0, 1]`, and the global budgets. A source anchor must use exactly one locator family: either `sheet_name` plus `cell`, or `page_number` plus `coordinates`; mixed and empty anchors are invalid. Store totals internally as a sorted tuple of unique key/value pairs and serialize it back to a JSON object.

Parse amounts with the existing `parse_decimal()` lexical contract and require `format(value, "f")` to have at most two fractional digits. Preserve the original canonical string after validation so round-trip output is exact.

- [ ] **Step 4: Run contract tests and existing numeric tests**

Run:

```powershell
$python=(Resolve-Path '..\..\.venv\Scripts\python.exe').Path
$env:PYTHONPATH=(Resolve-Path 'src').Path
& $python -m pytest tests/test_structured_documents.py tests/test_numeric.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add src/open_tax_check/structured_documents.py tests/test_structured_documents.py
git commit -m "feat: add strict financial statement contract"
```

---

### Task 2: Tax Return Contract

**Files:**
- Modify: `src/open_tax_check/structured_documents.py`
- Modify: `tests/test_structured_documents.py`

**Interfaces:**
- Consumes: `SourceAnchor` and `StructuredIssue` from Task 1。
- Produces: `TaxReturnLine`, `TaxReturn`。
- Produces: `tax_return_from_dict(payload: Mapping[str, object]) -> TaxReturn`。
- Produces: `tax_return_to_dict(value: TaxReturn) -> dict[str, object]`。

- [ ] **Step 1: Add failing tax-return round-trip and strictness tests**

Append a literal `tax_return_payload()` with:

```python
{
    "schema_version": "tax-return/v1",
    "return_id": "vat-2026-01",
    "source_id": "host-untrusted",
    "entity_name": "完全虚构科技有限公司",
    "tax_id": "vat",
    "return_type": "monthly_return",
    "period_start": "2026-01-01",
    "period_end": "2026-01-31",
    "filing_status": "filed",
    "filed_at": "2026-02-10T09:00:00+08:00",
    "currency": "CNY",
    "unit_multiplier": "1",
    "lines": [{
        "form_code": "VAT-MAIN",
        "line_number": "1",
        "original_name": "按适用税率计税销售额",
        "fact_key": "vat.taxable_sales",
        "value": "1000.00",
        "value_type": "decimal",
        "source_anchor": {
            "source_id": "host-untrusted",
            "page_number": 1,
            "sheet_name": None,
            "cell": None,
            "coordinates": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.2], [0.1, 0.2]],
        },
        "confidence_source": "host_ai",
        "confirmation_state": "proposed",
    }],
    "totals": {"tax_payable": "130.00"},
    "issues": [],
}
```

Test exact round-trip plus rejection of unknown keys, unsupported `tax_id`, reversed periods, naive `filed_at`, invalid `filing_status`, non-string decimal values, invalid fact keys, duplicate `(form_code, line_number)`, duplicate non-null `fact_key`, and more than 10,000 lines.

- [ ] **Step 2: Run the added tests and observe RED**

Run the two named new tests. Expected: import or attribute failure for the missing tax-return API.

- [ ] **Step 3: Implement the tax-return types and parser**

Define:

```python
@dataclass(frozen=True, slots=True)
class TaxReturnLine:
    form_code: str
    line_number: str
    original_name: str
    fact_key: str | None
    value: str | bool | None
    value_type: str
    source_anchor: SourceAnchor
    confidence_source: str
    confirmation_state: str


@dataclass(frozen=True, slots=True)
class TaxReturn:
    schema_version: str
    return_id: str
    source_id: str
    entity_name: str
    tax_id: str
    return_type: str
    period_start: str
    period_end: str
    filing_status: str
    filed_at: str | None
    currency: str
    unit_multiplier: str
    lines: tuple[TaxReturnLine, ...]
    totals: tuple[tuple[str, str], ...]
    issues: tuple[StructuredIssue, ...]
```

Derive the accepted tax IDs as `frozenset(item.tax_id for item in TAX_DEFINITIONS)` from the existing public catalog rather than duplicating values. `value_type` accepts `decimal`, `text`, `choice`, `boolean`, or `empty`; enforce the Python type and lexical rule for each. `filing_status` accepts `draft`, `filed`, `amended`, or `unknown`; `filed_at` is required for `filed` and `amended` and must include a timezone.

Reject repeated `(form_code, line_number)` pairs and repeated non-null `fact_key` values with `ValueError`. These are the v1 definition of duplicate critical return lines; no duplicate line may become a confirmed fact.

- [ ] **Step 4: Run all structured-document tests**

Run:

```powershell
$python=(Resolve-Path '..\..\.venv\Scripts\python.exe').Path
$env:PYTHONPATH=(Resolve-Path 'src').Path
& $python -m pytest tests/test_structured_documents.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add src/open_tax_check/structured_documents.py tests/test_structured_documents.py
git commit -m "feat: add strict tax return contract"
```

---

### Task 3: Shared Safe Source Resolution

**Files:**
- Create: `src/open_tax_check/source_paths.py`
- Create: `tests/test_source_paths.py`
- Modify: `src/open_tax_check/document_providers.py`
- Modify: `tests/test_document_providers.py`

**Interfaces:**
- Produces: `resolve_safe_source(path: Path, root: Path) -> Path`。
- Raises: `ValueError("unsafe_source_path")` for escape, non-file, symlink or reparse point。
- Existing `LocalDocumentSource` providers consume the shared helper without behavior changes。

- [ ] **Step 1: Write failing path-security tests**

Cover an ordinary file, a path outside the root, a directory, a missing file, a symlink where supported, a linked root ancestor, and a monkeypatched Windows reparse attribute. The successful case must return the resolved ordinary file; every unsafe case must raise exactly `unsafe_source_path` without including the path in the message.

- [ ] **Step 2: Run source-path tests and observe RED**

Run `tests/test_source_paths.py`; expected failure is the missing module/API.

- [ ] **Step 3: Implement and reuse the helper**

Resolve root and path without following an unchecked link. Walk every existing ancestor from the candidate to the root with `os.lstat`; reject `stat.S_ISLNK(mode)` and any `st_file_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)`. Require `candidate.is_file()` and `candidate.relative_to(root)` after validation.

Change `document_providers._safe_path()` to call `resolve_safe_source()` and return `False` only on `OSError` or `ValueError`. Do not weaken current provider error mapping.

- [ ] **Step 4: Verify shared and existing provider behavior**

Run:

```powershell
$python=(Resolve-Path '..\..\.venv\Scripts\python.exe').Path
$env:PYTHONPATH=(Resolve-Path 'src').Path
& $python -m pytest tests/test_source_paths.py tests/test_document_providers.py tests/test_document_extraction_cli.py -q
```

Expected: all selected tests pass; platform-unavailable symlink creation may be explicitly skipped.

- [ ] **Step 5: Commit Task 3**

```powershell
git add src/open_tax_check/source_paths.py src/open_tax_check/document_providers.py tests/test_source_paths.py tests/test_document_providers.py
git commit -m "refactor: share safe case source resolution"
```

---

### Task 4: Source-Bound Structured Document Provider

**Files:**
- Create: `src/open_tax_check/structured_document_providers.py`
- Create: `tests/test_structured_document_providers.py`

**Interfaces:**
- Consumes: Task 1/2 parsers and Task 3 `resolve_safe_source`。
- Produces: `StructuredDocumentSource(source_id, path, media_type, input_root, sha256, entity_name, period_start, period_end)`。
- Produces: `import_financial_statement(source, payload) -> FinancialStatement`。
- Produces: `import_tax_return(source, payload) -> TaxReturn`。
- Raises: `StructuredDocumentImportError` with `.code` from `invalid_structured_document`, `source_mismatch`, `entity_mismatch`, `period_mismatch`, or `unsafe_source_path`。

- [ ] **Step 1: Write failing provider rebinding tests**

Use a real temporary input file and payloads from `tests/test_structured_documents.py`. Compute its SHA-256 for the trusted source record. Assert that successful import replaces top-level and every nested anchor `source_id` with the trusted source ID. Assert stable rejection for an invalid or mismatched SHA-256, unsafe path, wrong entity, a statement/return outside the case period, malformed payload and a payload over 20 MiB. Error strings must equal only the code.

- [ ] **Step 2: Run provider tests and observe RED**

Expected: missing module/API.

- [ ] **Step 3: Implement minimal provider**

Validate the local source first, stream its SHA-256 without exceeding existing source-size limits, and compare it with the trusted lowercase 64-hex `sha256` before parsing the payload. Parse the payload second, compare normalized entity names exactly after trimming outer whitespace, and require the document period to be fully contained in the case period. Rebuild immutable records with `dataclasses.replace`, replacing every anchor source ID. Do not mutate the caller payload. Map an invalid or mismatched digest to `source_mismatch` without exposing either digest or a path.

- [ ] **Step 4: Run provider and contract tests**

Run:

```powershell
$python=(Resolve-Path '..\..\.venv\Scripts\python.exe').Path
$env:PYTHONPATH=(Resolve-Path 'src').Path
& $python -m pytest tests/test_structured_documents.py tests/test_structured_document_providers.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 4**

```powershell
git add src/open_tax_check/structured_document_providers.py tests/test_structured_document_providers.py
git commit -m "feat: bind structured documents to case sources"
```

---

### Task 5: CLI Validation and Case Import

**Files:**
- Modify: `src/open_tax_check/cli.py`
- Modify: `src/open_tax_check/__init__.py`
- Create: `tests/test_structured_document_cli.py`

**Interfaces:**
- Adds: `validate-structured-document --kind financial-statement|tax-return --input PATH`。
- Adds: `import-structured-document --case CASE --source SOURCE_ID --kind financial-statement|tax-return --input PATH`。
- Writes: `CASE/work/structured-documents/<SOURCE_ID>.<KIND>.json` atomically。
- Success output: `{"valid": true, "kind": ..., "schema_version": ...}` for validation and `{"status": "completed", "kind": ..., "source_id": ..., "output_path": ...}` for import。

- [ ] **Step 1: Write failing CLI tests**

Use real files and `cli.main()` with monkeypatched `sys.argv`. Cover both kinds, valid/invalid validation, source rebinding, missing source, linked input, entity/period mismatch, invalid kind and preservation of an existing valid output when replacement fails. Assert exact exit codes: `0` success, `4` invalid contract/source, `3` operational write failure. Assert failures return only `status` and `error_code`.

- [ ] **Step 2: Run CLI tests and observe RED**

Expected: argparse rejects the new commands.

- [ ] **Step 3: Add commands using existing case manifests**

For case import, read `state.json` for entity and period and `work/input_manifest.json` for the trusted source ID, filename, media type and `sha256`. Construct `StructuredDocumentSource` from the case `input` directory; never accept a path or digest from the host payload. Recompute and verify the source digest through the Task 4 provider before accepting the contract. Serialize through the Task 1/2 public serializers and write through a same-directory temporary file, `flush`, `os.fsync`, and `os.replace`.

Export all public dataclasses, parsers, serializers and provider APIs from `open_tax_check.__init__`.

- [ ] **Step 4: Run CLI, package and regression tests**

Run:

```powershell
$python=(Resolve-Path '..\..\.venv\Scripts\python.exe').Path
$env:PYTHONPATH=(Resolve-Path 'src').Path
& $python -m pytest tests/test_structured_document_cli.py tests/test_package_contract.py tests/test_document_extraction_cli.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 5**

```powershell
git add src/open_tax_check/cli.py src/open_tax_check/__init__.py tests/test_structured_document_cli.py
git commit -m "feat: validate and import structured tax documents"
```

---

### Task 6: Public Contract, Fixtures, and Release Gate

**Files:**
- Create: `tests/fixtures/structured_documents/financial-statement.json`
- Create: `tests/fixtures/structured_documents/tax-return.json`
- Modify: `docs/document-extraction-contract.md`
- Modify: `README.md`
- Modify: `tests/test_public_repo_contract.py`
- Modify: `tests/test_release_validation.py`

**Interfaces:**
- Consumes: all prior public APIs and CLI commands。
- Produces: fictional, source-free reusable fixtures and wheel/sdist membership requirements。

- [ ] **Step 1: Add failing public and distribution tests**

Require README and the document contract to name both schema versions, both CLI commands, `proposed` versus `confirmed`, source-ID plus SHA-256 rebinding, and the rule that uncertain host AI output cannot enter calculations. Require wheel members `open_tax_check/structured_documents.py`, `open_tax_check/structured_document_providers.py`, and `open_tax_check/source_paths.py`; require both fixture suffixes in the sdist only.

- [ ] **Step 2: Run public/release tests and observe RED**

Run:

```powershell
$python=(Resolve-Path '..\..\.venv\Scripts\python.exe').Path
$env:PYTHONPATH=(Resolve-Path 'src').Path
& $python -m pytest tests/test_public_repo_contract.py tests/test_release_validation.py -q
```

Expected: failures identify missing docs, fixtures and distribution members.

- [ ] **Step 3: Add fictional fixtures and accurate documentation**

The financial fixture is a balanced fictional 2026-12 balance sheet with assets and liabilities-plus-equity both `1000.00`. The tax fixture is a fictional 2026-01 VAT return with taxable sales `1000.00` and payable VAT `130.00`. Use only `example.invalid` identifiers and the entity name `完全虚构科技有限公司`; include no contacts, credentials, tax IDs or real organizations.

Document that these contracts normalize extracted content but do not prove filing correctness, source truth or tax treatment. State that template parsing and cross-document reconciliation are the next independent implementation boundary; do not claim them complete in this plan.

- [ ] **Step 4: Run focused and complete tests**

Run the structured-document, provider, CLI, public and release tests, then:

```powershell
$python=(Resolve-Path '..\..\.venv\Scripts\python.exe').Path
$env:PYTHONPATH=(Resolve-Path 'src').Path
& $python -m pytest -q
```

Expected: no failures; conditional platform skips remain explicitly reported.

- [ ] **Step 5: Build and strictly validate distributions**

Run:

```powershell
$python=(Resolve-Path '..\..\.venv\Scripts\python.exe').Path
$env:PYTHONPATH=(Resolve-Path 'src').Path
& $python -m build
& $python scripts/validate_release.py --strict
```

Expected: one wheel and one sdist build successfully and strict validation prints `release validation passed`.

- [ ] **Step 6: Commit Task 6**

```powershell
git add README.md docs/document-extraction-contract.md tests/fixtures/structured_documents tests/test_public_repo_contract.py tests/test_release_validation.py
git commit -m "docs: publish structured document contracts"
```

- [ ] **Step 7: Independent whole-plan review**

Review every commit from the plan base through HEAD against the design and Global Constraints. Specifically recheck nested strictness, numeric budgets, path/reparse handling, source rebinding, atomic replacement, error privacy, wheel/sdist contents and the fact that proposed AI values never become confirmed facts.
