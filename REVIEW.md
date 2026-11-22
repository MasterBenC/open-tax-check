# open-tax-check 代码审查（skill 层）

> 审查日期：2026-11-22 ｜ HEAD `4e77dab`（`docs(skill): 重构 SKILL.md 为薄控制器并下沉细节到 references`）
> **范围**：只审 skill 层（`skills/open-tax-check/`：SKILL.md、references/ 全部 10 份、agents/、scripts/opentaxcheck.py）
> 与 `src/open_tax_check/cli.py` 的接口一致性。**不审 src 内部业务算法。**
> 已读：上述 skill 层全部、`src/open_tax_check/cli.py`、`case_orchestration.py`、`customer_guide.py`、
> `workpaper.py`、`tax_registry.py`、`tax_calculation.py`、`delivery.py`、`tax_packs/*.json`（抽样 + 计数核对）、`pyproject.toml`
> 未读：`src` 其余业务模块、`tests/` 全部、`docs/superpowers/plans/*`、README.md 全文
> 结论只覆盖已读部分。

## 总评

skill 层与 CLI 的**接口是通的**：`scripts/opentaxcheck.py` 只做依赖探测与错误提示，不转发参数，
不存在"文档写的参数 CLI 里没有"这类 P0。CI 也硬（三平台 × 三个 Python 版本 + wheel 构建 + 安装态校验 + OCR smoke），
这个仓库的工程质量明显高于同类。

问题集中在两处：
1. **skill 层对 CLI 能力的覆盖不全**——`case-status` 会返回一个 `next_action`，而 skill 层没有任何命令能完成它（见 P1-1），宿主命中即卡死。
2. **references 里的事实性数字与文件名有 5 处和代码不一致**——税务合规类 skill 里，这类错误的代价比普通文档高。

## P0

无。（逐条核对了 SKILL.md 路由表、10 份 references 的命令与参数，以及 `opentaxcheck.py` 的调用方式，未发现参数名与 CLI 不吻合。）

---

## P1

### 1. `case-status` 会返回 `complete_document_extraction`，而 skill 层完全没有这个入口

`src/open_tax_check/case_orchestration.py:363-374`：

```python
def _next_action(status: CaseStatus) -> str:
    return {
        CaseStatus.WAITING_FOR_MATERIALS: "add_source_files",
        CaseStatus.WAITING_FOR_EXTRACTION: "complete_document_extraction",
        ...
```

而 `skills/open-tax-check/SKILL.md` 的路由表（:36-49）只覆盖 `inspect` / `select-sheet` / `confirm-mapping` /
`run` / `case-*`（`status: failed` + `error_code`）/ `tax-health-check`，没有 `waiting_for_extraction` 这一行。

**我本机 grep 实测**：下面这些 CLI 子命令在 `skills/open-tax-check/` 目录下命中 **0 次**：

```
prepare-ocr  extract-document  import-extraction  validate-extraction
import-structured-document  validate-structured-document
set-scope  confirm-cross-check  confirm-account-classification
complete_document_extraction
```

它们在 `cli.py` 里都真实存在（`prepare-ocr:278`、`extract-document:280`、`validate-extraction:260`、
`import-extraction:262`、结构化导入 `:266/:271`、`set-scope:217`、`confirm-cross-check:310`）。

同时 `SKILL.md:143` 还写着"本地离线 OCR 为可选后备"——承诺了能力但没有命令。

**后果**：宿主拿到 `next_action=complete_document_extraction` 时无从下手，智能体只能自由发挥，
而这个仓库的设计原则恰恰是"宿主不得自行判断"。
**建议**：路由表补全 `case-*` 的全部 status/next_action；新增 `references/ocr-and-structured-import.md`，
写明这 9 个命令的参数与退出码。

### 2. `model-recommendations.md` 让宿主原样展示一个不存在的版本标识

- `references/model-recommendations.md:21`：`任何模式都必须原样展示 case-guide/v1、客户问题和 tax-customer-handoff/v1`
- `src/open_tax_check/customer_guide.py:19`：`CASE_GUIDE_SCHEMA_VERSION = "case-guide/v2"`（全库无 `case-guide/v1`）

同源问题还有 `SKILL.md:68` 与 `host-integrations.md:24` 的"第一次/第二次"表述——两次返回的 schema 版本都是 v2，
区别只在第二次的 `document_groups` 按场景动态生成，现写法会让读者以为第一次是 v1。
**建议**：`model-recommendations.md:21` 改 `case-guide/v2`；`SKILL.md:68` 改述为"第二次调用返回带动态 `document_groups` 的 v2 引导"。

### 3. `opentaxcheck.py` 的依赖修复命令在发布态目录里不可执行

`skills/open-tax-check/scripts/opentaxcheck.py:6-9`：

```python
INSTALL_HINT = (
    '  python -m pip install -e ".[excel,report]"\n'
    "  python -m playwright install chromium\n"
)
```

`skills/open-tax-check/` 下没有 `pyproject.toml`（在仓库根）。skill 层是发布物，
用户照这条提示在 skill 目录执行会因找不到 `pyproject.toml` 失败。
另外 `MODULE_HINTS`（:11-15）只覆盖 `open_tax_check` / `openpyxl` / `playwright`，
缺 `pypdfium2`、`Pillow`、`paddleocr`、`xlrd`、`defusedxml`（`pyproject.toml:33-39` 的 `ocr` / `excel` extras），
这些缺失会落到通用提示，且 `INSTALL_HINT` 不含 `[ocr]`。
**建议**：发布态提示改用 `python -m pip install "open-tax-check[excel,report,ocr]"`；`MODULE_HINTS` 按 extra 补全。

---

## P2

### 4. `tax-packs.md` 说 21 个税种，代码是 20 个

- `references/tax-packs.md:15`：`21 个税种、45 个包`
- `src/open_tax_check/tax_registry.py:30-51` 的 `TAX_DEFINITIONS` 我实际数过：**20 条**（18 个税 + 2 个附加费：教育费附加、地方教育附加）
- `src/open_tax_check/cli.py:295` 的 help 也写 `all 20 tax items`

包数 45 是对的（`tax_packs/` 下 23 个 `calculation` + 21 个 `screening` + 1 个 `prototype`）。
**建议**：改"20 个税种、45 个包"。

### 5. `tax-packs.md` 消费税的公式族写错

- `references/tax-packs.md:20`：`| 消费税 consumption_tax | consumption-tax-cn-2023-2026 | base_minus_deductions_times_rate |`
- 实际 `consumption-tax-cn-2023-2026.calculation.json` 的 `calculation.formula_id` = `generic.base_times_applicable_parameter.v1`

**建议**：改为 `base_times_applicable_parameter`。这是全表里唯一一处公式族错误，其余税种（增值税、企业所得税、个税、关税）我都核对过，对得上。

### 6. `tax-packs.md:41` 对评估器的描述与实现不符（不是 bug，是描述误导）

`references/tax-packs.md:41`：`` `calculation` 包的评估器是 `generic.calculation.<公式族>.v1` ``
实际 23 个 `calculation` 包的 `evaluator_id` **统一都是** `generic.calculation.base_minus_deductions_times_rate.v1`，
公式族体现在包内 `calculation.formula_id`，由 `tax_calculation.py:31-51` 按 `formula_id` 分派。
按现在的写法，读者会去找一个不存在的 `generic.calculation.quantity_times_unit_amount.v1`。
**建议**：改述为"所有 `calculation` 包挂同一个评估器 `generic.calculation.base_minus_deductions_times_rate.v1`，
实际算法由包内 `calculation.formula_id` 决定"。

### 7. `host-integrations.md` 的工作底稿文件名写错

- `references/host-integrations.md:52`：`内部 HTML、workpaper.xlsx、机器快照和证据包必须来自同一个 snapshot_revision`
- `src/open_tax_check/workpaper.py:24`：`WORKPAPER_NAME = "税务健康检查工作底稿.xlsx"`

（`delivery.py` 里证据包 artifact 的友好名可能才是 `workpaper.xlsx`——两种名字并存时，文档要写清是哪一个。）
**建议**：写明磁盘名与证据包内 artifact 名的区别。

### 8. `SKILL.md` 薄控制器残留三处长段落，与 references 近乎逐字重复

`SKILL.md:68`（case-guide 字段清单长段）与 `host-integrations.md:24` 高度重复；`:143`（OCR/health-check 说明）、
`:149`（专业复核门槛）同样属于已经下沉过一遍的细节。刚做完"薄控制器"重构，这三段会两边不同步。
**建议**：各留一句结论 + 链接，细节只留 references。

### 9. `kb-mount.md` 的挂载口径弱于同款 julie 版

`references/kb-mount.md:18-22` 只列三种可能的挂载位置（"任选其一"），**没有"实际读取 `_index.md` 验证可读性"这一步**；
且 `:68/:71/:72` 写死了带日期的文件名（`企业所得税法实施条例(2024-12-06).md` 等）。
对照 `commercial-lawyer-julie/references/kb-mount.md` 的三步可执行探测（逐个尝试 `_index.md`，读到即挂载成功，
全失败则显式说明"知识库未挂载或不可读"）以及"不写死带日期文件名"的规则，这份明显更弱。
**建议**：对齐 julie 的三步探测 + 去掉写死的日期文件名 + 未挂载时要求输出开头显式说明。

### 10. 未发现"示意图被写成真实结论"的红线问题

`references/example-dialogue.md` 已明确标注虚构/脱敏；`docs/qa/*-checklist.md` 属内部检查表，不对外。
这一点是本次审查的**通过项**，记下来是为了说明已查过，不是发现问题。

---

## 修复顺序

1. 补 `references/ocr-and-structured-import.md` + 路由表补全 extraction 类状态（P1-1，唯一会让宿主卡死的项）
2. `model-recommendations.md` 与 `SKILL.md` 的 `case-guide/v1` → `v2`（P1-2）
3. `opentaxcheck.py` 的依赖提示改发布态写法 + `MODULE_HINTS` 补全（P1-3）
4. `tax-packs.md` 三处事实错误（P2-4、P2-5、P2-6）
5. `host-integrations.md` 文件名（P2-7）
6. `SKILL.md` 三段下沉（P2-8）、`kb-mount.md` 对齐 julie（P2-9）
