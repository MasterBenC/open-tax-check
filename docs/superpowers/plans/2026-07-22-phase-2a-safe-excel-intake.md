# OpenTaxCheck 2A：安全 Excel 接收与资料覆盖实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不执行宏、公式或外部链接的前提下，安全接收 `.csv`、`.xlsx`、`.xlsm`、`.xls`，形成工作簿盘点、资料覆盖矩阵以及需要人工确认的选表结果。

**Architecture:** `intake.py` 只负责顶层文件安全检查、哈希和资料角色覆盖；`workbooks.py` 通过统一只读接口隔离不同 Excel 格式；`sheet_detection.py` 只做确定性的表头发现与角色评分；`pipeline.py` 负责编排并沿用现有原子发布机制。2A 不标准化业务行，也不运行税务规则，只把案件推进到可供 2B 使用的已确认选表状态。

**Tech Stack:** Python 3.11-3.13、标准库、`openpyxl>=3.1.5,<4`、`xlrd>=2.0.2,<3`、`defusedxml>=0.7.1,<1`、`unittest`、GitHub Actions。

## 全局约束

- 核心包继续保持零运行时依赖；Excel 能力只能通过 `pip install -e ".[excel]"` 安装。
- `excel` 可选依赖必须精确为 `openpyxl>=3.1.5,<4`、`xlrd>=2.0.2,<3`、`defusedxml>=0.7.1,<1`。
- 支持 Python 3.11、3.12、3.13，以及 Windows、macOS、Linux 九种组合。
- 顶层输入最多 50 个；单文件最多 100 MiB；单工作簿最多 200 个工作表。
- 单个选中工作表最多访问 5,000,000 个单元格；表头发现最多扫描前 100 个非空行和前 256 列。
- OOXML 最多 10,000 个 ZIP 成员、总声明解压大小最多 1 GiB、单成员声明压缩比不得超过 100:1。
- 不跟随目录、符号链接或 junction；不执行宏、VBA、公式、加载项、嵌入对象或外部链接。
- 隐藏工作表不得自动选择；选中表含隐藏数据行时必须进入人工确认。
- 日志和普通异常不得包含原始单元格值；公开测试和示例只能使用虚构数据。
- Excel 文件作为真实输入只能保留在案件目录；仓库中的二进制测试夹具必须是虚构、小型、固定哈希并经发布校验允许。
- 设计文档与实施计划默认使用中文；稳定的 CLI、JSON 字段、Python 标识符继续使用英文。

---

## 文件结构

- `src/open_tax_check/schemas.py`：2A/2B 共用的不可变记录、状态值和序列化边界。
- `src/open_tax_check/intake.py`：安全枚举、文件限制、哈希、资料角色与覆盖矩阵。
- `src/open_tax_check/workbooks.py`：只读工作簿协议、OOXML 预检、openpyxl/xlrd 适配器。
- `src/open_tax_check/sheet_detection.py`：表头组合、字段信号与角色评分。
- `src/open_tax_check/reviews.py`：独立于运行产物的人工选表决定及绑定指纹。
- `src/open_tax_check/pipeline.py`：新增 `inspect_case()`，保留现有 `run_case()` 的 CSV 兼容行为。
- `src/open_tax_check/cli.py`：扩展 `init`，新增 `set-scope`、`inspect`、`select-sheet`。
- `tests/workbook_factory.py`：仅在测试运行时生成虚构 `.xlsx`/`.xlsm`。
- `tests/fixtures/fictional-journal.xls`：唯一受控的虚构 BIFF8 集成夹具。

---

### Task 1：增加案件期间、Excel 可选依赖和新版 CI

**Files:**
- Modify: `pyproject.toml`
- Modify: `.github/workflows/ci.yml`
- Modify: `src/open_tax_check/case.py`
- Modify: `src/open_tax_check/cli.py`
- Modify: `tests/test_case.py`
- Modify: `tests/test_package_contract.py`

**Interfaces:**
- Consumes: `case_dir: Path`、`entity_name: str`、ISO 日期字符串。
- Produces: `init_case(case_dir, entity_name, period_start=None, period_end=None) -> dict`、`set_case_scope(case_dir, period_start, period_end) -> dict`、`[project.optional-dependencies].excel`。

- [ ] **Step 1: 写案件期间与依赖契约的失败测试**

```python
def test_init_records_review_period(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        case_dir = Path(temp_dir) / "case"
        state = init_case(case_dir, "虚构企业", "2025-01-01", "2025-12-31")
        self.assertEqual(state["review_period"], {
            "start": "2025-01-01", "end": "2025-12-31"
        })

def test_scope_rejects_reversed_period_without_changing_state(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        case_dir = Path(temp_dir) / "case"
        init_case(case_dir, "虚构企业")
        before = (case_dir / "state.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "invalid_review_period"):
            set_case_scope(case_dir, "2025-12-31", "2025-01-01")
        self.assertEqual((case_dir / "state.json").read_bytes(), before)

def test_excel_extra_is_bounded(self):
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    self.assertEqual(data["project"]["optional-dependencies"]["excel"], [
        "openpyxl>=3.1.5,<4", "xlrd>=2.0.2,<3", "defusedxml>=0.7.1,<1"
    ])
```

- [ ] **Step 2: 运行测试并确认失败原因正确**

Run: `python -m unittest tests.test_case tests.test_package_contract -v`

Expected: FAIL，分别显示 `init_case` 不接受期间参数、`set_case_scope` 不存在、`excel` extra 不存在。

- [ ] **Step 3: 实现严格期间校验和可选依赖**

```python
def _review_period(start: str | None, end: str | None) -> dict | None:
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise ValueError("incomplete_review_period")
    try:
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as error:
        raise ValueError("invalid_review_period") from error
    if start_date > end_date:
        raise ValueError("invalid_review_period")
    return {"start": start_date.isoformat(), "end": end_date.isoformat()}

def set_case_scope(case_dir: Path, period_start: str, period_end: str) -> dict:
    state_path = case_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["review_period"] = _review_period(period_start, period_end)
    state["next_action"] = "add_source_files"
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(state_path, state)
    return state
```

在 `pyproject.toml` 增加：

```toml
[project.optional-dependencies]
excel = [
  "openpyxl>=3.1.5,<4",
  "xlrd>=2.0.2,<3",
  "defusedxml>=0.7.1,<1",
]
```

CI 将 action 更新为 `actions/checkout@v6` 和 `actions/setup-python@v6`，安装命令改为 `python -m pip install -e ".[excel]"`。

- [ ] **Step 4: 验证案件命令与九宫格 CI 配置**

Run: `python -m unittest tests.test_case tests.test_package_contract -v`

Expected: PASS；`init --period-start 2025-01-01 --period-end 2025-12-31` 与 `set-scope` 均输出包含 `review_period` 的 UTF-8 JSON。

- [ ] **Step 5: 提交案件范围与依赖契约**

```powershell
git add pyproject.toml .github/workflows/ci.yml src/open_tax_check/case.py src/open_tax_check/cli.py tests/test_case.py tests/test_package_contract.py
git commit -m "feat: add review period and Excel extra"
```

---

### Task 2：建立版本化清单与工作簿记录模型

**Files:**
- Create: `src/open_tax_check/schemas.py`
- Create: `tests/test_schemas.py`

**Interfaces:**
- Consumes: 普通 Python 值。
- Produces: `SourceRecord`、`SheetRecord`、`RoleScore`、`CoverageItem` 以及递归 `to_dict(value) -> dict | list | str | int | bool | None`。

- [ ] **Step 1: 写不可变记录和稳定序列化的失败测试**

```python
def test_source_record_uses_digest_prefix_as_source_id(self):
    record = SourceRecord.from_digest(
        filename="虚构序时账.xlsx", digest="a" * 64, size_bytes=128,
        extension=".xlsx", status="accepted"
    )
    self.assertEqual(record.source_id, "a" * 16)
    self.assertEqual(to_dict(record)["schema_version"], "0.2.0")

def test_role_score_rejects_out_of_range_score(self):
    with self.assertRaisesRegex(ValueError, "invalid_role_score"):
        RoleScore(role="journal", score=101, signals=())
```

- [ ] **Step 2: 运行测试并确认模块缺失**

Run: `python -m unittest tests.test_schemas -v`

Expected: FAIL with `ModuleNotFoundError: open_tax_check.schemas`。

- [ ] **Step 3: 实现最小不可变模型**

```python
SCHEMA_VERSION = "0.2.0"

@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    filename: str
    sha256: str
    size_bytes: int
    extension: str
    status: str
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_digest(cls, *, filename: str, digest: str, size_bytes: int,
                    extension: str, status: str) -> "SourceRecord":
        return cls(digest[:16], filename, digest, size_bytes, extension, status)

@dataclass(frozen=True, slots=True)
class RoleScore:
    role: str
    score: int
    signals: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 100:
            raise ValueError("invalid_role_score")

def to_dict(value):
    if is_dataclass(value):
        return {field.name: to_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    return value
```

同一文件中按设计稿字段完整定义 `SheetRecord` 和 `CoverageItem`；两者仅保存元数据，不保存任意业务单元格原值。

- [ ] **Step 4: 运行模型测试**

Run: `python -m unittest tests.test_schemas -v`

Expected: PASS。

- [ ] **Step 5: 提交公共模型**

```powershell
git add src/open_tax_check/schemas.py tests/test_schemas.py
git commit -m "feat: add versioned intake schemas"
```

---

### Task 3：安全枚举输入并生成资料覆盖矩阵

**Files:**
- Create: `src/open_tax_check/intake.py`
- Create: `tests/test_intake.py`

**Interfaces:**
- Consumes: `case_dir: Path`、`review_period: dict | None`。
- Produces: `inventory_inputs(case_dir: Path) -> IntakeResult`，其中含 `sources`、`issues`、`coverage`、`status`、`next_action`。

- [ ] **Step 1: 写限制、链接和资料分层的失败测试**

```python
def test_inventory_hashes_supported_regular_file_without_following_symlink(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        case = Path(temp_dir); (case / "input").mkdir()
        (case / "input" / "journal.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        result = inventory_inputs(case)
        self.assertEqual(result.sources[0].status, "accepted")
        self.assertEqual(len(result.sources[0].sha256), 64)

def test_inventory_rejects_more_than_fifty_entries(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        case = Path(temp_dir); (case / "input").mkdir()
        for index in range(51):
            (case / "input" / f"{index:02d}.csv").write_text("x\n", encoding="utf-8")
        result = inventory_inputs(case)
        self.assertEqual(result.status, "needs_intake_review")
        self.assertIn("too_many_input_entries", [issue.code for issue in result.issues])

def test_baseline_coverage_never_treats_missing_as_no_risk(self):
    result = build_coverage_matrix(set())
    journal = next(item for item in result if item.evidence_role == "journal")
    self.assertEqual(journal.tier, "required")
    self.assertEqual(journal.status, "missing")
    self.assertEqual(journal.procedure_status, "not_performed_missing_evidence")
```

- [ ] **Step 2: 运行测试并确认模块缺失**

Run: `python -m unittest tests.test_intake -v`

Expected: FAIL with `ModuleNotFoundError: open_tax_check.intake`。

- [ ] **Step 3: 实现封闭式安全枚举**

```python
SUPPORTED = {".csv", ".xlsx", ".xlsm", ".xls"}
MAX_ENTRIES = 50
MAX_FILE_BYTES = 100 * 1024 * 1024

def _entry_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_file():
        return "regular_file"
    if path.is_dir():
        return "directory"
    return "other"

def inventory_inputs(case_dir: Path) -> IntakeResult:
    entries = sorted((case_dir / "input").iterdir(), key=lambda item: item.name.casefold())
    if len(entries) > MAX_ENTRIES:
        return IntakeResult.blocked("too_many_input_entries")
    sources, issues = [], []
    for path in entries:
        kind = _entry_kind(path)
        if path.name.startswith("~$"):
            sources.append(SourceRecord.ignored(path.name, "ignored_office_lock"))
        elif kind != "regular_file":
            issues.append(IntakeIssue(path.name, f"rejected_{kind}"))
        elif path.stat().st_size > MAX_FILE_BYTES:
            issues.append(IntakeIssue(path.name, "file_too_large"))
        else:
            digest = sha256_file(path)
            status = "accepted" if path.suffix.lower() in SUPPORTED else "present_unparsed"
            sources.append(SourceRecord.from_path(path, digest=digest, status=status))
    coverage = build_coverage_matrix(detect_filename_roles(sources))
    return IntakeResult.from_parts(sources, issues, coverage)
```

`build_coverage_matrix()` 必须逐项列出设计稿中的必需、建议和风险触发资料；2A 仅允许 `present_unparsed`、`missing`、`not_performed_missing_evidence`、`not_applicable`，不得生成“无风险”状态。

- [ ] **Step 4: 验证限制边界和不泄露原值**

Run: `python -m unittest tests.test_intake -v`

Expected: PASS；诊断只含本地文件名、`source_id`、限制代码，不读取目录/链接目标。

- [ ] **Step 5: 提交安全接收层**

```powershell
git add src/open_tax_check/intake.py tests/test_intake.py
git commit -m "feat: add safe multi-file intake"
```

---

### Task 4：实现只读 Excel 适配器和 OOXML 预检

**Files:**
- Create: `src/open_tax_check/workbooks.py`
- Create: `tests/workbook_factory.py`
- Create: `tests/test_workbooks.py`
- Create: `tests/fixtures/fictional-journal.xls`
- Modify: `scripts/validate_release.py`
- Modify: `tests/test_release_validation.py`

**Interfaces:**
- Consumes: 通过 Task 3 的普通文件检查后的 `Path`。
- Produces: `open_workbook(path: Path) -> WorkbookReader`；reader 提供 `sheets() -> tuple[SheetMeta, ...]`、`iter_rows(sheet_id, min_row, max_row, max_col)`、`close()`。

- [ ] **Step 1: 写格式、安全预检和无 extra 的失败测试**

```python
def test_xlsx_reader_disables_links_and_exposes_formula_metadata(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        path = make_xlsx(Path(temp_dir) / "book.xlsx", formula="=1+1")
        with open_workbook(path) as book:
            cells = list(book.iter_rows("sheet-0001", 1, 2, 4))
        self.assertEqual(cells[1][0].formula, "=1+1")
        self.assertIsNone(cells[1][0].cached_value)

def test_ooxml_preflight_rejects_excessive_declared_ratio(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        path = make_zip_ratio_fixture(Path(temp_dir) / "bomb.xlsx", ratio=101)
        with self.assertRaisesRegex(WorkbookSafetyError, "ooxml_compression_ratio_exceeded"):
            open_workbook(path)

def test_reader_rejects_more_than_two_hundred_sheets(self):
    path = make_xlsx_with_sheet_count(self.temp_path / "many.xlsx", 201)
    with self.assertRaisesRegex(WorkbookSafetyError, "worksheet_limit_exceeded"):
        open_workbook(path)

def test_selected_sheet_stops_before_visiting_more_than_five_million_cells(self):
    reader = fake_reader(rows=20_000, columns=256)
    with self.assertRaisesRegex(WorkbookSafetyError, "visited_cell_limit_exceeded"):
        tuple(reader.iter_rows("sheet-0001", 1, 20_000, 256))

def test_missing_excel_extra_has_stable_action(self):
    with patch("open_tax_check.workbooks.import_module", side_effect=ModuleNotFoundError):
        with self.assertRaisesRegex(ExcelExtraRequired, "install_excel_extra"):
            open_workbook(Path("book.xlsx"))
```

- [ ] **Step 2: 运行测试并确认模块缺失**

Run: `python -m unittest tests.test_workbooks -v`

Expected: FAIL with `ModuleNotFoundError: open_tax_check.workbooks`。

- [ ] **Step 3: 实现 OOXML 预检与格式适配器**

```python
MAX_ZIP_MEMBERS = 10_000
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_WORKSHEETS = 200
MAX_VISITED_CELLS = 5_000_000

def preflight_ooxml(path: Path) -> None:
    with ZipFile(path) as package:
        members = package.infolist()
        if len(members) > MAX_ZIP_MEMBERS:
            raise WorkbookSafetyError("ooxml_member_limit_exceeded")
        if sum(item.file_size for item in members) > MAX_UNCOMPRESSED_BYTES:
            raise WorkbookSafetyError("ooxml_uncompressed_limit_exceeded")
        for item in members:
            ratio = item.file_size / max(item.compress_size, 1)
            if ratio > MAX_COMPRESSION_RATIO:
                raise WorkbookSafetyError("ooxml_compression_ratio_exceeded")

def open_workbook(path: Path) -> WorkbookReader:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        preflight_ooxml(path)
        openpyxl = _required_module("openpyxl")
        return OpenPyxlReader(openpyxl.load_workbook(
            path, read_only=True, data_only=False, keep_links=False, keep_vba=False
        ))
    if suffix == ".xls":
        xlrd = _required_module("xlrd")
        return XlrdReader(xlrd.open_workbook(path, on_demand=True))
    raise UnsupportedWorkbook("unsupported_workbook_format")
```

`OpenPyxlReader` 同时打开一个 `data_only=True` 的只读副本，以相同坐标提供缓存值；两份句柄在 `close()` 和异常路径中都必须关闭。`.xlsm` 永远使用 `keep_vba=False`。`.xls` 只读取 xlrd 暴露的存储结果。

受控 `.xls` 夹具必须只含“虚构企业”、五列序时账和两行平衡分录。将其 SHA-256 与最大 64 KiB 大小写入 `scripts/validate_release.py` 的 `ALLOWED_BINARY_FIXTURES`；除此以外继续拒绝所有公开 Excel 二进制文件。

- [ ] **Step 4: 运行适配器和发布安全测试**

Run: `python -m unittest tests.test_workbooks tests.test_release_validation -v`

Expected: `.xlsx`、`.xlsm`、`.xls` 读取测试 PASS；损坏、加密、超限、伪 ZIP 炸弹均返回稳定代码；非白名单 Excel 仍被发布校验拒绝。

- [ ] **Step 5: 提交工作簿适配器**

```powershell
git add src/open_tax_check/workbooks.py tests/workbook_factory.py tests/test_workbooks.py tests/fixtures/fictional-journal.xls scripts/validate_release.py tests/test_release_validation.py
git commit -m "feat: add safe read-only workbook adapters"
```

---

### Task 5：确定性发现表头并进行资料角色评分

**Files:**
- Create: `src/open_tax_check/sheet_detection.py`
- Create: `tests/test_sheet_detection.py`

**Interfaces:**
- Consumes: `WorkbookReader`、`SheetMeta`。
- Produces: `detect_sheet(reader, sheet) -> SheetDetection`，包括 1-3 行表头候选、标准化标签、`journal`/`trial_balance` 评分和其他资料角色的盘点信号。

- [ ] **Step 1: 写多行表头、隐藏表和歧义评分测试**

```python
def test_two_row_trial_balance_header_scores_high_confidence(self):
    rows = [
        ["科目编码", "科目名称", "期初余额", None, "本期发生额", None, "期末余额", None],
        [None, None, "借方", "贷方", "借方", "贷方", "借方", "贷方"],
    ]
    result = detect_rows(rows, visibility="visible")
    self.assertGreaterEqual(result.score_for("trial_balance"), 85)
    self.assertEqual(result.header_rows, (1, 2))

def test_hidden_high_confidence_sheet_is_never_auto_selectable(self):
    result = detect_rows(JOURNAL_HEADERS, visibility="hidden")
    self.assertGreaterEqual(result.score_for("journal"), 85)
    self.assertFalse(result.auto_selectable)

def test_merged_two_row_headers_expand_only_for_detection(self):
    rows = [
        ["科目名称", "本期发生额", None],
        [None, "借方", "贷方"],
    ]
    result = detect_rows(rows, merged_ranges=("B1:C1",), visibility="visible")
    self.assertIn("本期发生额|借方", result.combined_headers)
    self.assertIn("本期发生额|贷方", result.combined_headers)

def test_tax_return_signal_is_inventory_only(self):
    result = detect_rows([["增值税纳税申报表", "销项税额", "进项税额"]],
                         visibility="visible")
    self.assertGreaterEqual(result.score_for("vat_return"), 60)
    self.assertFalse(result.auto_selectable_for_normalization("vat_return"))
```

- [ ] **Step 2: 运行测试并确认模块缺失**

Run: `python -m unittest tests.test_sheet_detection -v`

Expected: FAIL with `ModuleNotFoundError: open_tax_check.sheet_detection`。

- [ ] **Step 3: 实现标准化与 0-100 评分**

```python
def normalize_header(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[\s_\-—（）()\[\]【】]+", "", text)

def confidence_band(score: int) -> str:
    if score >= 85:
        return "high"
    if score >= 60:
        return "review"
    return "none"

def choose_automatic(candidates: Sequence[SheetDetection], role: str) -> SheetDetection | None:
    eligible = [item for item in candidates
                if item.visibility == "visible" and item.score_for(role) >= 85]
    return eligible[0] if len(eligible) == 1 else None
```

`journal` 的高置信必须唯一命中 `voucher_no`、`date`、`account_name`、`debit`、`credit`；`trial_balance` 必须命中 `account_name`、本期借贷和完整的期初/期末表示。任何重复目标会扣分并加入 `conflicting_header_signal`，不能达到自动选择条件。

- [ ] **Step 4: 验证 100×256 扫描边界和确定性**

Run: `python -m unittest tests.test_sheet_detection -v`

Expected: PASS；相同输入重复检测产生逐字节相同的业务结果。

- [ ] **Step 5: 提交表头检测器**

```powershell
git add src/open_tax_check/sheet_detection.py tests/test_sheet_detection.py
git commit -m "feat: add deterministic sheet detection"
```

---

### Task 6：编排 inspect 流程并原子发布盘点产物

**Files:**
- Modify: `src/open_tax_check/pipeline.py`
- Modify: `src/open_tax_check/cli.py`
- Create: `tests/test_inspect_pipeline.py`

**Interfaces:**
- Consumes: 已初始化案件目录。
- Produces: `inspect_case(case_dir: Path) -> tuple[int, dict]`，以及 `work/input_manifest.json`、`work/workbook_inventory.json`、`work/sheet_selection.json`、`work/coverage_matrix.json`。

- [ ] **Step 1: 写无期间、唯一候选和多候选的失败测试**

```python
def test_inspect_without_period_stops_before_opening_workbook(self):
    case = self.init_case(period=False)
    make_xlsx(case / "input" / "journal.xlsx", rows=JOURNAL_ROWS)
    code, result = inspect_case(case)
    self.assertEqual(code, 4)
    self.assertEqual(result["status"], "needs_intake_review")
    self.assertEqual(result["next_action"], "set_case_period")

def test_inspect_auto_selects_one_visible_high_confidence_journal(self):
    case = self.init_case(period=True)
    make_xlsx(case / "input" / "journal.xlsx", rows=JOURNAL_ROWS)
    code, result = inspect_case(case)
    selection = json.loads((case / "work" / "sheet_selection.json").read_text("utf-8"))
    self.assertEqual(code, 0)
    self.assertEqual(selection["journal"][0]["decision_source"], "automatic")
    self.assertEqual(result["status"], "partial_scope_ready")
```

- [ ] **Step 2: 运行测试并确认 inspect 尚不存在**

Run: `python -m unittest tests.test_inspect_pipeline -v`

Expected: FAIL because `inspect_case` and CLI `inspect` do not exist。

- [ ] **Step 3: 实现 inspect 编排和状态优先级**

```python
STATUS_PRIORITY = (
    "needs_intake_review", "needs_sheet_selection", "partial_scope_ready"
)

def inspect_case(case_dir: Path) -> tuple[int, dict]:
    state = load_case_state(case_dir)
    if not state.get("review_period"):
        return finalize_inspection(case_dir, status="needs_intake_review",
                                   next_action="set_case_period", artifacts=[])
    intake = inventory_inputs(case_dir)
    inventory = inspect_workbooks(intake.accepted_sources)
    selection = build_sheet_selection(inventory)
    status, action = inspection_state(intake, inventory, selection)
    artifacts = write_inspection_artifacts(
        case_dir=case_dir,
        intake=intake,
        inventory=inventory,
        selection=selection,
    )
    return finalize_inspection(
        case_dir, status=status, next_action=action, artifacts=artifacts
    )
```

`finalize_inspection()` 必须复用现有 `_start_run()`、`_finalize_run()` 和原子 current-view 发布；失败运行不得覆盖上次成功 current view。预期输入错误用稳定诊断码，意外错误使用 `inspect_processing_error`，不回显单元格值。

- [ ] **Step 4: 运行 inspect、原子回滚和旧 CSV 回归测试**

Run: `python -m unittest tests.test_inspect_pipeline tests.test_pipeline -v`

Expected: PASS；原 CSV `run` 行为保持不变。

- [ ] **Step 5: 提交 inspect 流程**

```powershell
git add src/open_tax_check/pipeline.py src/open_tax_check/cli.py tests/test_inspect_pipeline.py
git commit -m "feat: add resumable workbook inspection"
```

---

### Task 7：保存人工选表决定并按指纹失效

**Files:**
- Create: `src/open_tax_check/reviews.py`
- Modify: `src/open_tax_check/cli.py`
- Create: `tests/test_reviews.py`

**Interfaces:**
- Consumes: `case_dir`、`role`、`source_id`、`sheet_id`、一基表头行、`reviewer`。
- Produces: `confirm_sheet_selection(case_dir: Path, role: str, source_id: str, sheet_id: str, header_rows: tuple[int, ...], hidden_rows: str, reviewer: str) -> dict`，保存到 `reviews/sheet_selection.json`；`load_valid_sheet_selections(case_dir: Path, inventory: dict) -> tuple[dict, list[dict]]`。

- [ ] **Step 1: 写绑定指纹、重复确认和源文件变化测试**

```python
def test_confirmed_selection_is_bound_to_source_and_headers(self):
    decision = confirm_sheet_selection(
        case, role="journal", source_id="a" * 16, sheet_id="sheet-0001",
        header_rows=(3,), hidden_rows="exclude", reviewer="host:user"
    )
    self.assertEqual(decision["binding"]["source_sha256"], "a" * 64)
    self.assertEqual(decision["header_rows"], [3])

def test_hidden_data_requires_explicit_include_or_exclude(self):
    with self.assertRaisesRegex(ReviewError, "hidden_row_decision_required"):
        confirm_sheet_selection(
            case, role="journal", source_id="a" * 16, sheet_id="sheet-0001",
            header_rows=(3,), hidden_rows="unspecified", reviewer="host:user"
        )

def test_changed_digest_invalidates_only_affected_selection(self):
    valid, invalid = load_valid_sheet_selections(case, changed_inventory)
    self.assertNotIn("journal", valid)
    self.assertIn("source_digest_changed", invalid[0]["reasons"])
    self.assertIn("trial_balance", valid)
```

- [ ] **Step 2: 运行测试并确认 reviews 模块缺失**

Run: `python -m unittest tests.test_reviews -v`

Expected: FAIL with `ModuleNotFoundError: open_tax_check.reviews`。

- [ ] **Step 3: 实现独立复核层和 CLI**

```python
def selection_binding(source: dict, sheet: dict, header_rows: tuple[int, ...]) -> dict:
    payload = {
        "source_sha256": source["sha256"], "sheet_id": sheet["sheet_id"],
        "sheet_name": sheet["name"], "header_rows": list(header_rows),
        "selection_schema_version": "0.2.0"
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {**payload, "fingerprint": hashlib.sha256(encoded).hexdigest()}

def confirm_sheet_selection(
    case_dir: Path,
    role: str,
    source_id: str,
    sheet_id: str,
    header_rows: tuple[int, ...],
    hidden_rows: str,
    reviewer: str,
) -> dict:
    inventory = load_current_inventory(case_dir)
    source, sheet = require_inventory_match(inventory, source_id, sheet_id)
    if hidden_rows not in {"include", "exclude"}:
        raise ReviewError("hidden_row_decision_required")
    decision = {"role": role, "reviewer": reviewer, "hidden_rows": hidden_rows,
                "header_rows": list(header_rows),
                "binding": selection_binding(source, sheet, header_rows)}
    return upsert_review_decision(case_dir / "reviews" / "sheet_selection.json", decision)
```

CLI：`opentaxcheck select-sheet --case PATH --role journal --source SOURCE_ID --sheet SHEET_ID --header-row 3 --hidden-rows exclude --reviewer host:user`。允许同一角色选择多张表，但拒绝同一 sheet 的重叠选择、越界表头行和未知角色。即使工作表当前没有隐藏数据行，也保存 `hidden_rows=exclude`，使后续源文件新增隐藏行时决定能够按 sheet 指纹失效。

- [ ] **Step 4: 运行复核层与 inspect 重跑测试**

Run: `python -m unittest tests.test_reviews tests.test_inspect_pipeline -v`

Expected: PASS；源哈希不变时复用选择，变化时只让受影响角色回到 `needs_sheet_selection`。

- [ ] **Step 5: 提交人工选表控制**

```powershell
git add src/open_tax_check/reviews.py src/open_tax_check/cli.py tests/test_reviews.py
git commit -m "feat: add source-bound sheet review"
```

---

### Task 8：更新 Skill、公开文档、虚构示例和完整验收

**Files:**
- Modify: `README.md`
- Modify: `docs/roadmap.md`
- Modify: `CHANGELOG.md`
- Modify: `skills/open-tax-check/SKILL.md`
- Modify: `skills/open-tax-check/references/canonical-schema.md`
- Modify: `skills/open-tax-check/references/methodology.md`
- Modify: `tests/test_public_repo_contract.py`
- Modify: `tests/test_skill_contract.py`
- Modify: `examples/fictional-company/README.md`
- Modify: `scripts/run_fictional_example.py`

**Interfaces:**
- Consumes: Task 1-7 的稳定 CLI 与 JSON 契约。
- Produces: 与实际 2A 能力一致的中文文档、虚构 Excel 演示和发布验收。

- [ ] **Step 1: 写公开契约的失败测试**

```python
def test_readme_documents_excel_inspection_without_claiming_normalization(self):
    text = README.read_text(encoding="utf-8")
    self.assertIn('pip install -e ".[excel]"', text)
    self.assertIn("opentaxcheck inspect", text)
    self.assertIn("尚未标准化 Excel 业务行", text)

def test_skill_requires_explicit_sheet_review(self):
    text = SKILL_MD.read_text(encoding="utf-8")
    self.assertIn("needs_sheet_selection", text)
    self.assertIn("select-sheet", text)
    self.assertIn("不得自动选择隐藏工作表", text)
```

- [ ] **Step 2: 运行公开与 Skill 契约并确认失败**

Run: `python -m unittest tests.test_public_repo_contract tests.test_skill_contract -v`

Expected: FAIL because documentation still says Excel unsupported。

- [ ] **Step 3: 更新中文文档和运行时生成的虚构 Excel 演示**

README 快速开始必须包含：

```powershell
python -m pip install -e ".[excel]"
opentaxcheck init --case .\cases\demo --entity "虚构科技有限公司" --period-start 2025-01-01 --period-end 2025-12-31
opentaxcheck inspect --case .\cases\demo
```

文档明确区分“已支持安全盘点/选表”和“2B 才支持标准化/勾稽/规则”。`scripts/run_fictional_example.py` 用 `tests/workbook_factory.py` 同构逻辑在输出案件内生成虚构 `.xlsx`，不得在仓库提交真实或演示 Excel 二进制。

- [ ] **Step 4: 执行 2A 完整验收**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[excel]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\validate_release.py
.\.venv\Scripts\python.exe scripts\run_fictional_example.py --output work\phase-2a-demo
.\.venv\Scripts\python.exe -m build
git diff --check
```

Expected: 全部 PASS；演示状态为 `partial_scope_ready` 或 `needs_sheet_selection`（取决于示例是否设置为单一明确候选）；构建成功；仓库不存在真实客户资料或未批准二进制。

- [ ] **Step 5: 提交 2A 文档与验收**

```powershell
git add README.md docs/roadmap.md CHANGELOG.md skills/open-tax-check examples/fictional-company scripts/run_fictional_example.py tests
git commit -m "docs: publish safe Excel intake workflow"
```

---

## 2A 完成门槛

- `.xlsx`、`.xlsm`、`.xls`、CSV 均可安全进入盘点层；未安装 extra 时给出 `install_excel_extra`。
- 所有限制、隐藏表、隐藏行、公式缓存、加密和损坏文件测试均通过。
- 唯一可见高置信表才可自动选择；其他情况必须进入 `needs_sheet_selection`。
- 资料覆盖矩阵不会把缺失、未解析或未执行描述为“无风险”。
- 复核决定与源哈希、sheet、表头行和 schema 版本绑定，并能选择性失效。
- 现有 CSV 流程与 106 项基础测试不得回归。
- 九宫格 CI、发布校验、虚构示例和包构建全部通过。
