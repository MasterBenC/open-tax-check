# OpenTaxCheck 2B：会计数据标准化与勾稽实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 2A 已确认工作表的基础上，完成人工确认字段映射、序时账与科目余额表标准化、字段级血缘、账表勾稽及按程序阻断，并让现有税务筛查继续消费可靠的标准化序时账。

**Architecture:** `mapping.py` 只产生或验证版本化字段映射；`normalization.py` 通过工作簿只读接口将单元格转换为 canonical rows 并记录字段级血缘；`reconciliation.py` 使用 `Decimal` 独立执行余额恒等式和序时账—科目余额表发生额核对；`pipeline.py` 依据覆盖矩阵只阻断依赖受影响数据的程序。AI/宿主只能写入 mapping proposal，只有 `confirm-mapping` 的确定性校验成功后才激活。

**Tech Stack:** Python 3.11-3.13、标准库 `decimal/json/hashlib`、2A 的工作簿适配器、`openpyxl`/`xlrd` 可选依赖、`unittest`、GitHub Actions。

## 全局约束

- 完整继承 2A 的全部文件安全、隐私、版本、依赖和跨平台约束。
- 映射必须角色化、版本化；每个源表头必须映射到唯一 canonical field，或显式排除并说明理由。
- AI 或宿主提出的映射默认不生效，只有确定性 `confirm-mapping` 成功才可进入标准化。
- 不推断缺失借贷方向；金额+方向只有方向明确且合法时才能拆分。
- 金额使用 canonical decimal string；所有计算使用 `Decimal`，分币精确比较。
- Excel 空金额只在已经识别为有效业务行后转为零；空白行不得生成 canonical row。
- 公式不重算；无缓存值的必需数值公式必须阻断。
- 每个 canonical 字段都必须记录 source、sheet、行列、A1 坐标、原始类型、value origin、mapping version。
- 科目先按标准化科目编码精确匹配；只有双方科目名称都唯一时才允许名称匹配；不得强制模糊匹配。
- 勾稽失败只阻断依赖受影响角色、期间或科目总体的程序，不 blanket 阻断整个案件。
- 业务产物在相同输入、期间和复核决定下必须确定性一致，只有 run id 和时间戳可以变化。
- 设计文档与实施计划默认中文；CLI、JSON 和 Python 稳定接口保持英文。

---

## 文件结构

- `src/open_tax_check/mapping.py`：角色字段定义、alias、proposal 与 confirmation 校验。
- `src/open_tax_check/normalization.py`：序时账/科目余额表行识别、转换和字段血缘。
- `src/open_tax_check/reconciliation.py`：余额表恒等式、总额平衡、账表发生额匹配。
- `src/open_tax_check/coverage.py`：程序级依赖和阻断状态归并。
- `src/open_tax_check/reviews.py`：映射确认的持久化、指纹和选择性失效。
- `src/open_tax_check/pipeline.py`：多源标准化、勾稽、规则消费和原子发布。
- `src/open_tax_check/cli.py`：新增 `confirm-mapping` 并让 `run` 进入 2B 流程。

---

### Task 1：扩展角色化字段定义与映射提议

**Files:**
- Modify: `src/open_tax_check/mapping.py`
- Modify: `tests/test_mapping.py`

**Interfaces:**
- Consumes: `role: Literal["journal", "trial_balance"]`、组合表头标签。
- Produces: `propose_mapping(role, headers) -> MappingProposal`、`required_fields(role) -> frozenset[str]`；保留 `map_headers(headers) -> dict` 作为 CSV 序时账兼容包装。

- [ ] **Step 1: 写角色映射和显式排除的失败测试**

```python
def test_trial_balance_split_columns_map_without_conflict(self):
    proposal = propose_mapping("trial_balance", [
        "科目编码", "科目名称", "期初余额|借方", "期初余额|贷方",
        "本期发生额|借方", "本期发生额|贷方", "期末余额|借方", "期末余额|贷方"
    ])
    self.assertEqual(proposal.mapping["本期发生额|借方"], "period_debit")
    self.assertEqual(proposal.missing_required, ())

def test_unknown_header_requires_explicit_exclusion(self):
    proposal = propose_mapping("journal", [
        "凭证号", "日期", "科目名称", "借方", "贷方", "内部标签"
    ])
    self.assertEqual(proposal.unresolved, ("内部标签",))
    self.assertFalse(proposal.ready_for_confirmation)
```

- [ ] **Step 2: 运行测试并确认新接口缺失**

Run: `python -m unittest tests.test_mapping -v`

Expected: FAIL because `propose_mapping` and trial-balance fields do not exist。

- [ ] **Step 3: 实现角色化字段和兼容包装**

```python
ROLE_FIELDS = {
    "journal": {
        "required": frozenset({"voucher_no", "date", "account_name", "debit", "credit"}),
        "optional": frozenset({"account_code", "summary", "counterparty"}),
    },
    "trial_balance": {
        "required": frozenset({
            "account_name", "opening_debit", "opening_credit", "period_debit",
            "period_credit", "closing_debit", "closing_credit"
        }),
        "optional": frozenset({
            "account_code", "account_level", "parent_account_code", "auxiliary_type",
            "auxiliary_name", "currency"
        }),
    },
}

def propose_mapping(role: str, headers: Sequence[str]) -> MappingProposal:
    fields = ROLE_FIELDS.get(role)
    if fields is None:
        raise ValueError("unknown_mapping_role")
    mapping, unresolved, targets = {}, [], defaultdict(list)
    for header in headers:
        target = ROLE_ALIASES[role].get(normalize_header(header))
        if target is None:
            unresolved.append(header)
        else:
            mapping[header] = target
            targets[target].append(header)
    conflicts = {key: tuple(value) for key, value in targets.items() if len(value) > 1}
    missing = tuple(sorted(fields["required"] - set(mapping.values())))
    return MappingProposal(role, mapping, tuple(unresolved), missing, conflicts,
                           mapping_version="0.2.0")

def map_headers(headers: list[str]) -> dict:
    return propose_mapping("journal", headers).to_legacy_dict()
```

金额+方向形式定义为显式 composite mapping，不伪装成两个相同目标；必须带 `amount_field` 与 `direction_field`。

- [ ] **Step 4: 运行新旧映射测试**

Run: `python -m unittest tests.test_mapping tests.test_pipeline -v`

Expected: PASS；现有 CSV alias、冲突和 unresolved 行为不变。

- [ ] **Step 5: 提交角色化映射提议**

```powershell
git add src/open_tax_check/mapping.py tests/test_mapping.py
git commit -m "feat: add role-specific mapping proposals"
```

---

### Task 2：确定性确认映射并与源指纹绑定

**Files:**
- Modify: `src/open_tax_check/reviews.py`
- Modify: `src/open_tax_check/cli.py`
- Create: `tests/test_mapping_reviews.py`

**Interfaces:**
- Consumes: 映射 JSON（`role`、`source_id`、`sheet_id`、`header_rows`、`mapping`、`exclusions`、`reviewer`）。
- Produces: `confirm_mapping(case_dir, payload) -> dict`，持久化到 `reviews/mapping_review.json`；CLI `confirm-mapping --mapping PATH`。

- [ ] **Step 1: 写重复目标、遗漏排除和 AI 未确认测试**

```python
def test_ai_proposal_is_inactive_until_confirmed(self):
    save_mapping_proposal(case, payload, proposal_source="ai")
    self.assertEqual(load_active_mappings(case), {})

def test_confirmation_rejects_duplicate_targets(self):
    payload = valid_journal_payload()
    payload["mapping"]["借方金额2"] = "debit"
    with self.assertRaisesRegex(MappingReviewError, "duplicate_mapping_target"):
        confirm_mapping(case, payload)

def test_confirmation_requires_every_header_mapped_or_excluded(self):
    payload = valid_journal_payload(extra_header="内部标签")
    with self.assertRaisesRegex(MappingReviewError, "unresolved_source_header"):
        confirm_mapping(case, payload)

def test_exclusion_requires_a_nonempty_reason(self):
    payload = valid_journal_payload(extra_header="内部标签")
    payload["exclusions"] = {"内部标签": ""}
    with self.assertRaisesRegex(MappingReviewError, "missing_exclusion_reason"):
        confirm_mapping(case, payload)
```

- [ ] **Step 2: 运行测试并确认确认接口缺失**

Run: `python -m unittest tests.test_mapping_reviews -v`

Expected: FAIL because mapping review persistence does not exist。

- [ ] **Step 3: 实现映射校验、签名指纹和 CLI**

```python
def validate_confirmation(headers: Sequence[str], payload: dict) -> None:
    mapping = payload.get("mapping", {})
    exclusions = payload.get("exclusions", {})
    if set(mapping) & set(exclusions):
        raise MappingReviewError("header_mapped_and_excluded")
    if set(headers) != set(mapping) | set(exclusions):
        raise MappingReviewError("unresolved_source_header")
    if any(not str(reason).strip() for reason in exclusions.values()):
        raise MappingReviewError("missing_exclusion_reason")
    targets = list(mapping.values())
    if len(targets) != len(set(targets)):
        raise MappingReviewError("duplicate_mapping_target")
    missing = required_fields(payload["role"]) - set(targets)
    if missing:
        raise MappingReviewError("missing_required_mapping")

def mapping_binding(selection: dict, payload: dict) -> dict:
    basis = {
        "source_sha256": selection["binding"]["source_sha256"],
        "sheet_id": selection["binding"]["sheet_id"],
        "header_rows": selection["header_rows"],
        "case_period": load_case_period(payload["case_dir"]),
        "mapping_schema_version": "0.2.0",
    }
    return {**basis, "fingerprint": stable_digest(basis)}
```

CLI 必须要求宿主传入非空 `reviewer`，并且只打印状态和安全诊断，不打印 mapping 中的源表头原值；程序不得自行伪造自然人身份。

- [ ] **Step 4: 运行映射复核与选择性失效测试**

Run: `python -m unittest tests.test_mapping_reviews tests.test_reviews -v`

Expected: PASS；源哈希、sheet、表头、期间或 schema 任一变化会使对应 mapping 失效。

- [ ] **Step 5: 提交映射确认控制**

```powershell
git add src/open_tax_check/reviews.py src/open_tax_check/cli.py tests/test_mapping_reviews.py
git commit -m "feat: add explicit mapping confirmation"
```

---

### Task 3：标准化序时账并保留字段级血缘

**Files:**
- Create: `src/open_tax_check/normalization.py`
- Create: `tests/test_journal_normalization.py`

**Interfaces:**
- Consumes: reader、已确认 `SheetSelection`、已激活 `MappingConfirmation`、案件期间。
- Produces: `normalize_journal(reader: WorkbookReader, selection: dict, mapping: dict, review_period: dict) -> NormalizationResult`，rows 包含 canonical 字段、`period`、`tax_year` 和 `field_lineage`。

- [ ] **Step 1: 写日期、金额、公式缓存和血缘测试**

```python
def test_journal_normalization_records_lineage_for_each_field(self):
    result = normalize_journal(reader, selection, mapping, REVIEW_PERIOD)
    row = result.rows[0]
    self.assertEqual(row["debit"], "100.00")
    self.assertEqual(row["period"], "2025-01")
    self.assertEqual(row["tax_year"], "2025")
    self.assertEqual(row["field_lineage"]["debit"], {
        "source_id": SOURCE_ID, "sheet_id": "sheet-0001", "sheet_name": "序时账",
        "row": 2, "column": 4, "coordinate": "D2", "original_type": "number",
        "value_origin": "literal", "mapping_version": "0.2.0"
    })

def test_required_formula_without_cached_value_blocks(self):
    result = normalize_journal(formula_reader(cached=None), selection, mapping, REVIEW_PERIOD)
    self.assertIn("missing_cached_formula_value", [issue.code for issue in result.issues])
```

- [ ] **Step 2: 运行测试并确认 normalization 模块缺失**

Run: `python -m unittest tests.test_journal_normalization -v`

Expected: FAIL with `ModuleNotFoundError: open_tax_check.normalization`。

- [ ] **Step 3: 实现有效行识别、转换与血缘**

```python
def field_lineage(cell: CellValue, *, source_id: str, sheet: SheetMeta,
                  mapping_version: str, value_origin: str | None = None) -> dict:
    return {
        "source_id": source_id, "sheet_id": sheet.sheet_id, "sheet_name": sheet.name,
        "row": cell.row, "column": cell.column, "coordinate": cell.coordinate,
        "original_type": cell.data_type,
        "value_origin": value_origin or cell.value_origin,
        "mapping_version": mapping_version,
    }

def normalize_money(cell: CellValue, *, required: bool) -> tuple[str, str]:
    if cell.formula is not None and cell.cached_value is None:
        raise RowIssue("missing_cached_formula_value")
    value = cell.cached_value if cell.formula is not None else cell.value
    if value in (None, ""):
        return "0.00", "literal"
    return format_money(parse_decimal(value)), cell.value_origin

def normalize_journal(reader, selection, mapping, review_period) -> NormalizationResult:
    rows, issues = [], []
    for source_row in iter_data_rows(reader, selection):
        if is_blank_business_row(source_row, mapping):
            continue
        try:
            rows.append(build_journal_row(source_row, mapping, review_period))
        except RowIssue as issue:
            issues.append(issue.with_location(selection.source_id, selection.sheet_id,
                                              source_row.row_number))
    return NormalizationResult(tuple(rows), tuple(issues))
```

Excel serial日期、Python `date/datetime`、设计稿支持的文本日期必须归一到 ISO；超出案件期间生成 `date_outside_review_period`，不猜测期间。隐藏行在未确认 include/exclude 前不进入迭代。

- [ ] **Step 4: 运行序时账标准化和旧 CSV 质量测试**

Run: `python -m unittest tests.test_journal_normalization tests.test_quality -v`

Expected: PASS；所有失败项只含字段名和位置，不含原始敏感值。

- [ ] **Step 5: 提交序时账标准化**

```powershell
git add src/open_tax_check/normalization.py tests/test_journal_normalization.py
git commit -m "feat: normalize journal with field lineage"
```

---

### Task 4：标准化科目余额表和方向金额组合

**Files:**
- Modify: `src/open_tax_check/normalization.py`
- Create: `tests/test_trial_balance_normalization.py`

**Interfaces:**
- Consumes: 已确认的 split debit/credit 或 amount+direction 映射。
- Produces: `normalize_trial_balance(reader: WorkbookReader, selection: dict, mapping: dict) -> NormalizationResult`，每行含七个必需字段和可选科目辅助字段。

- [ ] **Step 1: 写分列、方向组合和无效方向测试**

```python
def test_amount_direction_is_split_and_traced_as_derived(self):
    result = normalize_trial_balance(direction_reader("贷"), selection, mapping)
    row = result.rows[0]
    self.assertEqual(row["opening_debit"], "0.00")
    self.assertEqual(row["opening_credit"], "250.00")
    self.assertEqual(
        row["field_lineage"]["opening_credit"]["value_origin"],
        "derived_direction_split"
    )

def test_unknown_direction_blocks_without_guessing(self):
    result = normalize_trial_balance(direction_reader("平"), selection, mapping)
    self.assertEqual(result.rows, ())
    self.assertEqual(result.issues[0].code, "invalid_balance_direction")
```

- [ ] **Step 2: 运行测试并确认接口缺失**

Run: `python -m unittest tests.test_trial_balance_normalization -v`

Expected: FAIL because `normalize_trial_balance` does not exist。

- [ ] **Step 3: 实现余额表行转换**

```python
DIRECTION_ALIASES = {"借": "debit", "借方": "debit", "d": "debit",
                     "贷": "credit", "贷方": "credit", "c": "credit"}

def split_direction(amount: Decimal, direction: object) -> tuple[Decimal, Decimal]:
    normalized = normalize_header(direction)
    side = DIRECTION_ALIASES.get(normalized)
    if side is None:
        raise RowIssue("invalid_balance_direction")
    absolute = abs(amount)
    return (absolute, Decimal("0")) if side == "debit" else (Decimal("0"), absolute)

def normalize_trial_balance(reader, selection, mapping) -> NormalizationResult:
    rows, issues = [], []
    for source_row in iter_data_rows(reader, selection):
        if not identifies_account_row(source_row, mapping):
            continue
        try:
            rows.append(build_trial_balance_row(source_row, mapping))
        except RowIssue as issue:
            issues.append(issue.with_location(selection.source_id, selection.sheet_id,
                                              source_row.row_number))
    return NormalizationResult(tuple(rows), tuple(issues))
```

科目合计行只有在明确标记且不会与明细重复时才保留；默认排除“合计/总计”行并记录 `excluded_total_row`。负数方向组合先验证来源语义，不允许通过双重负号改变方向。

- [ ] **Step 4: 运行余额表全部边界测试**

Run: `python -m unittest tests.test_trial_balance_normalization -v`

Expected: PASS；split 与 direction 两种输入得到相同 canonical 金额和不同但正确的血缘来源。

- [ ] **Step 5: 提交科目余额表标准化**

```powershell
git add src/open_tax_check/normalization.py tests/test_trial_balance_normalization.py
git commit -m "feat: normalize trial balance safely"
```

---

### Task 5：实现余额恒等式和账表发生额勾稽

**Files:**
- Create: `src/open_tax_check/reconciliation.py`
- Create: `tests/test_reconciliation.py`

**Interfaces:**
- Consumes: canonical journal rows、canonical trial-balance rows。
- Produces: `reconcile_trial_balance(rows) -> ReconciliationResult`、`reconcile_journal_to_trial_balance(journal, trial_balance) -> ReconciliationResult`。

- [ ] **Step 1: 写恒等式、唯一名称匹配和歧义阻断测试**

```python
def test_trial_balance_identity_uses_signed_balances(self):
    result = reconcile_trial_balance([tb_row(
        opening_debit="100.00", opening_credit="0.00",
        period_debit="20.00", period_credit="50.00",
        closing_debit="70.00", closing_credit="0.00")])
    self.assertTrue(result.passed)

def test_trial_balance_period_debits_must_equal_period_credits(self):
    result = reconcile_trial_balance([
        tb_row(code="1001", period_debit="100.00", period_credit="0.00"),
        tb_row(code="6001", period_debit="0.00", period_credit="99.99"),
    ])
    self.assertFalse(result.passed)
    self.assertEqual(result.issues[0].code, "trial_balance_movement_unbalanced")

def test_account_code_precedes_unique_name(self):
    result = reconcile_journal_to_trial_balance(
        [journal_row(code="1002", name="银行存款", debit="100.00")],
        [tb_row(code="1002", name="银行存款", period_debit="100.00")]
    )
    self.assertEqual(result.items[0].match_method, "account_code")

def test_duplicate_names_are_never_force_matched(self):
    result = reconcile_journal_to_trial_balance(
        [journal_row(code=None, name="其他应收款")],
        [tb_row(code="122101", name="其他应收款"), tb_row(code="122102", name="其他应收款")]
    )
    self.assertEqual(result.items[0].status, "ambiguous_account")
```

- [ ] **Step 2: 运行测试并确认模块缺失**

Run: `python -m unittest tests.test_reconciliation -v`

Expected: FAIL with `ModuleNotFoundError: open_tax_check.reconciliation`。

- [ ] **Step 3: 实现分币精确勾稽**

```python
def signed(debit: str, credit: str) -> Decimal:
    return exact_subtract(parse_decimal(debit), parse_decimal(credit))

def row_identity(row: dict) -> Decimal:
    opening = signed(row["opening_debit"], row["opening_credit"])
    movement = signed(row["period_debit"], row["period_credit"])
    closing = signed(row["closing_debit"], row["closing_credit"])
    return exact_subtract(exact_add(opening, movement), closing)

def match_accounts(journal_rows, trial_balance_rows):
    by_code = unique_index(trial_balance_rows, "account_code")
    journal_names = unique_values(journal_rows, "account_name")
    trial_names = unique_values(trial_balance_rows, "account_name")
    for account in aggregate_journal(journal_rows):
        if account.code and account.code in by_code:
            yield matched(account, by_code[account.code], "account_code")
        elif account.name in journal_names and account.name in trial_names:
            yield matched(account, trial_names[account.name], "unique_account_name")
        else:
            yield unmatched_or_ambiguous(account)
```

每条差异必须包括 canonical 科目标识、journal/TB 借贷发生额、精确差额和 contributing source-row lineage；不得包含与差异无关的摘要或交易对手原值。

- [ ] **Step 4: 运行勾稽和低 Decimal 环境测试**

Run: `python -m unittest tests.test_reconciliation tests.test_numeric -v`

Expected: PASS；即使环境 Decimal precision 被调低，计算仍精确且不改变全局 context。

- [ ] **Step 5: 提交确定性勾稽引擎**

```powershell
git add src/open_tax_check/reconciliation.py tests/test_reconciliation.py
git commit -m "feat: reconcile journal and trial balance"
```

---

### Task 6：实现程序级覆盖和局部阻断

**Files:**
- Create: `src/open_tax_check/coverage.py`
- Create: `tests/test_coverage.py`

**Interfaces:**
- Consumes: evidence coverage、normalization issues、reconciliation results。
- Produces: `evaluate_procedure_coverage(evidence: dict[str, str], role_issues: dict[str, list[str]], dependencies: dict[str, frozenset[str]] = PROCEDURE_DEPENDENCIES) -> tuple[ProcedureCoverage, ...]`，状态限定为 `performed`、`blocked`、`not_performed_missing_evidence`、`present_unparsed`、`not_applicable`。

- [ ] **Step 1: 写局部阻断和未解析资料测试**

```python
def test_trial_balance_failure_does_not_block_journal_only_screen(self):
    coverage = evaluate_procedure_coverage(
        evidence=baseline_evidence(journal=True, trial_balance=True),
        role_issues={"trial_balance": ["identity_mismatch"]},
        dependencies={
            "journal_semantic_screen": {"journal"},
            "journal_tb_reconciliation": {"journal", "trial_balance"},
        },
    )
    self.assertEqual(status_of(coverage, "journal_semantic_screen"), "performed")
    self.assertEqual(status_of(coverage, "journal_tb_reconciliation"), "blocked")

def test_statement_present_but_unparsed_is_not_performed(self):
    coverage = evaluate_procedure_coverage(evidence={"balance_sheet": "present_unparsed"})
    self.assertEqual(status_of(coverage, "financial_statement_reconciliation"),
                     "present_unparsed")
```

- [ ] **Step 2: 运行测试并确认模块缺失**

Run: `python -m unittest tests.test_coverage -v`

Expected: FAIL with `ModuleNotFoundError: open_tax_check.coverage`。

- [ ] **Step 3: 实现依赖表和状态归并**

```python
PROCEDURE_DEPENDENCIES = {
    "journal_quality": frozenset({"journal"}),
    "trial_balance_quality": frozenset({"trial_balance"}),
    "journal_tb_reconciliation": frozenset({"journal", "trial_balance"}),
    "journal_hard_rules": frozenset({"journal"}),
    "journal_semantic_screen": frozenset({"journal"}),
    "financial_statement_reconciliation": frozenset({"balance_sheet", "income_statement"}),
    "vat_return_reconciliation": frozenset({"vat_return"}),
    "cit_return_reconciliation": frozenset({"cit_return"}),
}

def procedure_status(required_roles, evidence, role_issues) -> tuple[str, tuple[str, ...]]:
    missing = tuple(sorted(role for role in required_roles if evidence.get(role) == "missing"))
    if missing:
        return "not_performed_missing_evidence", missing
    unparsed = tuple(sorted(role for role in required_roles
                            if evidence.get(role) == "present_unparsed"))
    if unparsed:
        return "present_unparsed", unparsed
    blocked = tuple(sorted(role for role in required_roles if role_issues.get(role)))
    return ("blocked", blocked) if blocked else ("performed", ())
```

- [ ] **Step 4: 运行覆盖矩阵测试**

Run: `python -m unittest tests.test_coverage -v`

Expected: PASS；任何状态都不会使用 `no_risk`、`clear` 或等价表述。

- [ ] **Step 5: 提交程序级覆盖引擎**

```powershell
git add src/open_tax_check/coverage.py tests/test_coverage.py
git commit -m "feat: add procedure-level coverage gates"
```

---

### Task 7：整合多源 run 流程、选择性失效和现有规则

**Files:**
- Modify: `src/open_tax_check/pipeline.py`
- Modify: `src/open_tax_check/cli.py`
- Modify: `tests/test_pipeline.py`
- Create: `tests/test_excel_pipeline.py`

**Interfaces:**
- Consumes: 2A inventory/selection、2B active mapping、review period、`ai_mode`。
- Produces: `normalized_journal.jsonl`、`normalized_trial_balance.jsonl`、`reconciliation.json`、`coverage_matrix.json`、现有 `findings.json`/`report.md`。

- [ ] **Step 1: 写完整成功、待映射、局部阻断和确定性重跑测试**

```python
def test_excel_run_reconciles_and_reaches_professional_review(self):
    case = prepared_excel_case(confirm=True, reconciles=True)
    code, result = run_case(case, "off")
    reconciliation = load_json(case / "work" / "reconciliation.json")
    self.assertEqual(code, 0)
    self.assertTrue(reconciliation["journal_to_trial_balance"]["passed"])
    self.assertEqual(result["status"], "needs_professional_review")

def test_unconfirmed_mapping_stops_before_normalized_artifacts(self):
    case = prepared_excel_case(confirm=False)
    code, result = run_case(case, "off")
    self.assertEqual(code, 3)
    self.assertEqual(result["status"], "needs_mapping_review")
    self.assertFalse((case / "work" / "normalized_journal.jsonl").exists())

def test_unchanged_rerun_has_identical_business_artifacts(self):
    case = prepared_excel_case(confirm=True, reconciles=True)
    run_case(case, "off"); first = business_artifact_bytes(case)
    run_case(case, "off"); second = business_artifact_bytes(case)
    self.assertEqual(first, second)
```

- [ ] **Step 2: 运行测试并确认旧 pipeline 尚未消费 Excel**

Run: `python -m unittest tests.test_excel_pipeline -v`

Expected: FAIL because `run_case` still requires exactly one CSV journal。

- [ ] **Step 3: 按清晰状态机接入 2A/2B**

```python
def run_case(case_dir: Path, ai_mode: str) -> tuple[int, dict]:
    context = start_case_run(case_dir, ai_mode)
    try:
        intake = inventory_inputs(case_dir)
        selections = resolve_sheet_selections(case_dir, intake)
        if selections.needs_review:
            return context.finish(4, "needs_sheet_selection", "select_source_sheets")
        mappings = resolve_active_mappings(case_dir, selections)
        if mappings.needs_review:
            write_mapping_proposals(context.work_dir, mappings.proposals)
            return context.finish(3, "needs_mapping_review", "confirm_field_mapping")
        normalized = normalize_selected_sources(selections, mappings)
        write_normalized_artifacts(context.work_dir, normalized)
        reconciled = reconcile_normalized(normalized)
        write_json(context.work_dir / "reconciliation.json", reconciled.to_dict())
        coverage = evaluate_case_coverage(intake, normalized, reconciled)
        write_json(context.work_dir / "coverage_matrix.json", coverage.to_dict())
        findings = run_eligible_journal_procedures(normalized.journal, coverage)
        write_review_outputs(context.output_dir, findings)
        status = "needs_professional_review" if findings is not None else "partial_scope_ready"
        return context.finish(0, status, "review_findings")
    except ExpectedCaseIssue as issue:
        return context.finish(issue.exit_code, issue.status, issue.next_action,
                              diagnostic_code=issue.code)
```

实际实现必须抽取现有原子 run helper，避免 `run_case()` 继续膨胀；现有 CSV 通过一个虚拟 `sheet_id="csv"` 和一基行列血缘进入同一 normalization 接口。受影响源哈希变化只删除依赖该源的 active selection/mapping cache；未受影响决定保留。

- [ ] **Step 4: 运行全 pipeline、原子回滚和 CSV 回归测试**

Run: `python -m unittest tests.test_excel_pipeline tests.test_pipeline -v`

Expected: PASS；错误运行不发布半成品；上次 current view 不被破坏；现有 CSV 结果金额和 rule id 不变。

- [ ] **Step 5: 提交多源标准化流水线**

```powershell
git add src/open_tax_check/pipeline.py src/open_tax_check/cli.py tests/test_pipeline.py tests/test_excel_pipeline.py
git commit -m "feat: integrate Excel normalization pipeline"
```

---

### Task 8：更新虚构端到端案例、Skill 与 2B 发布验收

**Files:**
- Modify: `README.md`
- Modify: `docs/roadmap.md`
- Modify: `CHANGELOG.md`
- Modify: `skills/open-tax-check/SKILL.md`
- Modify: `skills/open-tax-check/references/canonical-schema.md`
- Modify: `skills/open-tax-check/references/methodology.md`
- Modify: `examples/fictional-company/README.md`
- Modify: `scripts/run_fictional_example.py`
- Modify: `tests/test_fictional_example.py`
- Modify: `tests/test_public_repo_contract.py`
- Modify: `tests/test_skill_contract.py`

**Interfaces:**
- Consumes: 2B 稳定 CLI、artifacts 和 review states。
- Produces: 公开可重复的虚构 Excel+CSV 案例、中文操作说明和完整发布验证。

- [ ] **Step 1: 写公开能力与结论边界的失败测试**

```python
def test_readme_documents_mapping_confirmation_and_reconciliation(self):
    text = README.read_text(encoding="utf-8")
    self.assertIn("confirm-mapping", text)
    self.assertIn("normalized_trial_balance.jsonl", text)
    self.assertIn("reconciliation.json", text)
    self.assertIn("勾稽通过不表示不存在税务风险", text)

def test_fictional_excel_and_csv_findings_match(self):
    csv_result = run_fictional_case(format="csv")
    excel_result = run_fictional_case(format="excel")
    self.assertEqual(csv_result.rule_amounts, excel_result.rule_amounts)
```

- [ ] **Step 2: 运行公开、Skill 和案例测试并确认失败**

Run: `python -m unittest tests.test_fictional_example tests.test_public_repo_contract tests.test_skill_contract -v`

Expected: FAIL because documentation/example does not yet expose 2B workflow。

- [ ] **Step 3: 更新中文文档和虚构案例**

README 的标准流程必须包含：

```powershell
opentaxcheck inspect --case .\cases\demo
opentaxcheck select-sheet --case .\cases\demo --role journal --source aaaaaaaaaaaaaaaa --sheet sheet-0001 --header-row 1 --hidden-rows exclude --reviewer host:user
opentaxcheck confirm-mapping --case .\cases\demo --mapping .\mapping.json
opentaxcheck run --case .\cases\demo --ai-mode off
```

文档中的 `aaaaaaaaaaaaaaaa` 仅为格式示例，不得复制为真实 source id；虚构 runner 直接从 inspect JSON 取得 source id，并以预置的虚构 mapping JSON 完成确认。文档必须说明财务报表和申报表仍为 `present_unparsed`，因此尚不能声称完成完整基线 health check。

- [ ] **Step 4: 执行 2B 完整验收**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[excel]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\validate_release.py
.\.venv\Scripts\python.exe scripts\run_fictional_example.py --output work\phase-2b-demo --format excel
.\.venv\Scripts\python.exe -m build
git diff --check
git status --short --branch
```

Expected: 全部 PASS；虚构 Excel 案例的 journal/TB 分币一致，现有业务招待费发现与 CSV 示例一致；状态为 `needs_professional_review`，覆盖矩阵明确报表和申报表尚未执行。

- [ ] **Step 5: 提交 2B 文档和最终验收**

```powershell
git add README.md docs/roadmap.md CHANGELOG.md skills/open-tax-check examples/fictional-company scripts/run_fictional_example.py tests
git commit -m "docs: publish accounting normalization workflow"
```

---

## 2B 完成门槛

- 映射 proposal 与 confirmation 严格分离，AI/host proposal 不会静默生效。
- journal 与 trial balance 的每个 canonical 字段都有准确到单元格的血缘和 value origin。
- split 借贷与 amount+direction 均能正确转换；未知方向、缺缓存公式和越界数据闭锁失败。
- TB 总借贷、逐行余额恒等式、journal—TB 科目发生额勾稽均使用精确十进制。
- 科目匹配只允许精确代码或双方唯一名称；歧义和未匹配保持显式。
- 缺资料、未解析和局部数据问题只影响依赖程序，覆盖矩阵不产生“无风险”暗示。
- 相同案件重跑的业务产物确定性一致；源变化只失效依赖决定和产物。
- CSV 兼容、九宫格 CI、完整测试、发布校验、虚构案例和构建全部通过。
