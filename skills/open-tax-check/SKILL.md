---
name: open-tax-check
description: 用于检查中国企业的税务资料（CSV 序时账或 Excel 工作簿），尤其在来源范围、工作表选择、数据质量、规则覆盖或报告交付仍不完整时使用；只做客户内部自查，不出具鉴证、专业意见或申报结论。Use when checking Chinese enterprise tax materials from CSV journals or Excel workbooks, especially when source scope, worksheet selection, data quality, rule coverage or report delivery may still be incomplete.
---

# OpenTaxCheck 税务健康检查

薄控制器：按 `state.json` 的 `next_action` 推进；代码负责盘点、映射、计算、血缘和状态，AI 只解释、整理证据、草拟文字和向客户提问。命令入口统一为 `<skill-dir>/scripts/opentaxcheck.py`，各节代码块给出带 `python` 前缀的完整调用；已安装 `open-tax-check` 时也可直接用 `opentaxcheck <子命令>`。安装与版本见 https://github.com/MasterBenC/open-tax-check 根目录 [README.md](../../README.md)（一句话安装指令以 README 为准）；OpenTaxCheck 尚未在其他智能体商店正式发布。

## 绝对不做（红线）

1. 不得把 `partial_scope_ready`、`calculated` 或“未触发”解释为无风险、完整或最终结论。
2. 不得自动选择隐藏工作表；隐藏行决定不得省略，含隐藏数据的可见工作表也必须人工确认。
3. 不得把关键词或语义命中变成税务结论；其状态保持 `candidate`，直到税务专业人员复核。
4. 不得把未复核结果放入客户结论，不得给出总体数字健康分。
5. 不得猜测数字、日期、映射、选表、事实、税率、地方政策、税目或减免；不得替客户确认经营事实。
6. 不得把未解析、未执行的程序或已上传未提取成功的资料写成“无风险”“未提供”或“已完成”。
7. 不得复用旧运行输出、把旧成功输出当作当前结果，或自行改写案件状态。
8. 永远不要称代码输出“100% 准确”；结论必须保留来源血缘、期间和限制。

解释税种口径、税基、税率或减免前，必须先读 [references/tax-packs.md](references/tax-packs.md)。

## 自助自查与专业复核（professional review）

| 路径 | 入口 | 交付物 | 复核门槛 |
| --- | --- | --- | --- |
| 自助自查 | `case-guide` → `case-start` → `case-self-check` → `case-tasks` / `case-confirm` → `tax-health-check` | 客户内部《税务健康检查报告》PDF | 不以税务专业人员复核、签字或盖章作为生成条件 |
| 逐层核对 | `init` → `inspect` → `select-sheet` → `confirm-mapping` → `run` → `case-delivery` | 经复核的 finding 与交付修订 | `review_status=reviewed` 且 `report_status` 恰为 `approved` |

两条路径独立，都不构成涉税鉴证、专业税务意见或纳税申报结论；混用同一案件时以 `next_action` 为准，不重复建案。

## 命令 × 状态 × 退出码 × 下一步

| 命令 | 状态 / `next_action` | 退出码 | 下一步 |
| --- | --- | --- | --- |
| `inspect` | `partial_scope_ready`（`await_phase_2b_excel_normalization`） | `0` | 从 `work/mapping_review.json` 取提案，复核人执行 `confirm-mapping` |
| `inspect` | `needs_intake_review`（`resolve_intake_issues`、`provide_supported_spreadsheet_input`） | `4` | 补期间或修输入，保持原始文件不变 |
| `inspect` | `needs_sheet_selection`（`select_<role>_sheet`） | `4` | 复核人执行 `select-sheet` 后再次 `inspect` |
| `inspect` | `needs_intake_review`（`inspect_processing_error`） | `4` | 检查本次失败，退出码为 `4`，不复用旧输出 |
| `select-sheet` | `sheet_selection_confirmed` / `needs_sheet_selection`（`hidden_row_exclusion_unavailable`） | `0` / `4` | `0` 再次 `inspect`；`4` 复核后改为 `include`，或停止处理 |
| `confirm-mapping` | `mapping_confirmed` / `needs_mapping_review` | `0` / `3` | `0` 运行 `run`；`3` 修正映射后重跑 |
| `run` | `needs_professional_review`（`review_findings`）或 `partial_scope_ready`（`review_coverage`） | `0` | 逐项复核，见 [references/professional-review.md](references/professional-review.md) |
| `run` | `needs_mapping_review`（`confirm_field_mapping`） | `3` | 先 `confirm-mapping` |
| `run` | `blocked_data_quality`（`resolve_data_quality_findings`、`provide_exactly_one_csv_journal`）或 Excel `needs_intake_review` | `4` | 解决日期、关键字段、金额、凭证平衡或输入入口 |
| `run` | `blocked_processing_error`（`inspect_processing_error`） | `5` | 只看本次运行；`work/processing_error.json` 已安全记录失败 |
| `case-*` | `status: failed` + `error_code` | `0` / `4` | 按 `error_code` 修输入或证据，如 `unverified_confirmation_evidence`、`delivery_blocked_data_quality` |
| `tax-health-check` | `completed` | `0` / `3` / `4` | `4` 请求、证据清单或规则包问题；`3` PDF 或证据包写入失败，不得回退 DOCX |

读法：`0` 可复核；`3` 映射待确认；`4` 输入、选表、证据或数据质量阻断；`5` 表示已安全记录处理错误；`2` 命令或依赖问题；`1` 未捕获异常。`inspect` 的 `next_action` 只完成 Excel 输入门槛；`blocked_processing_error` 与退出码 `5` 只属于 `run`，Excel `inspect` 的处理异常一律是 `needs_intake_review` + 退出码 `4`。

## 失败处理

- Python 必须是 3.11、3.12 或 3.13，版本不符先换隔离环境。
- PDF 需要 Chromium：缺内核先运行 `python -m playwright install chromium`，不得回退 DOCX 或自称已交付。
- 退出码 `1` 或 `state.json` 损坏时停下：保留现场、说明失败步骤，不手改状态，不复用旧 run 输出。
- 客户拒提供资料时记为缺失并留在覆盖矩阵，不得用估计值、行业均值或 AI 推断补齐。
- AI 模式只允许 `off`、`assist`、`deep`；无可用 AI 时使用 `off`。

## 1. 客户引导与建案

先运行第一次 `case-guide`，展示 `message_zh` 中的“税务健康检查 AI 智能体”欢迎语，按 `profile_questions` 收集企业名称、所在省/市、主营业务、检查期间和增值税纳税人类型；资料不需要一次准备齐。再把这五项信息写入 `customer-profile/v1` JSON 并运行 `case-start`，随后再跑一次 `case-guide`：

```text
python <skill-dir>/scripts/opentaxcheck.py case-guide --case <case-dir>
python <skill-dir>/scripts/opentaxcheck.py case-start --case <case-dir> --profile <profile.json>
```

第一次 `case-guide` 只有 `message_zh`、欢迎语和 `profile_questions`，`document_groups` 可为空；第二次才是 `case-guide/v2` 动态引导，提供 `scenario_questions`、`active_scenarios` 和按基础资料+已激活场景动态生成的 `document_groups`。`scenario_id`、`fact_key`、`question_id`、`group_id`、`active_scenarios` 仅供宿主控制，不向客户直接展示；客户只看中文字段：`message_zh`、`scenario_questions.question_zh`、`document_groups.title_zh`、`items_zh`、`applicability_note_zh`、`privacy_zh`、`disclaimer_zh`。不另写开场、不自行判断税种适用性。

## 2. 经营场景引导

五类经营场景：收入与流转、成本费用、资产与不动产、合同及特殊交易、人员、股东及关联事项。先留五项基础档案，再只问不动产、应税合同、雇员/股东往来及关联/重组事实；场景命中不等于税种适用，AI 智能体不得替客户确认经营事实。状态码与七宿主展示见 [references/host-integrations.md](references/host-integrations.md)，模型见 [references/model-recommendations.md](references/model-recommendations.md)。待补资料、已收到待解析和未执行均不计入已执行或已通过。五个交叉核对 ID：`BOOK-FS-REV-001`、`FS-VAT-SALES-001`、`BOOK-VAT-OUTPUT-001`、`FS-CIT-REV-PROFIT-001`、`BOOK-CIT-BE-001`；外部清单中的未核验条目只能作为询问型候选。

## 3. Excel 工作簿盘点与选表

`.xlsx`、`.xlsm`、`.xls` 支持安全盘点、候选识别、人工选表和资料覆盖矩阵；PDF、图片已提供可选的本地离线 OCR；联网企业数据和复杂版式理解尚未实现。工作簿放入 `<case-dir>/input/` 后运行：

```text
python <skill-dir>/scripts/opentaxcheck.py inspect --case <case-dir>
```

读取 `state.json` 的 `status` 与 `next_action`，并看 `work/workbook_inventory.json`、`work/coverage_matrix.json`。只有唯一、可见、高置信且 `has_hidden_data` 为 `false` 的候选才可自动选择，其余进入 `needs_sheet_selection`；隐藏工作表不得自动选择。细节见 [references/excel-intake.md](references/excel-intake.md)。

1. 从盘点产物读取真实 `source_id`、`sheet_id` 和候选表头行。
2. 由复核人决定角色、工作表和全部表头行；隐藏行决定必须是 `include` 或 `exclude`。
3. 执行 `select-sheet`：

```text
python <skill-dir>/scripts/opentaxcheck.py select-sheet --case <case-dir> --role journal --source <source_id> --sheet <sheet_id> --header-row <行号> --hidden-rows <include|exclude> --reviewer host:<复核人>
```

多行表头重复传 `--header-row`；隐藏行必须明确写为 `--hidden-rows include` 或 `--hidden-rows exclude`。读取器尚不能过滤隐藏行，含隐藏数据时 `exclude` 不会实际过滤，会以 `hidden_row_exclusion_unavailable` 阻断；复核人只能复核后改为 `include`，或停止处理。

4. 再次运行 `inspect`，重读 `state.json`。
5. 达到 `partial_scope_ready` 后取得映射提案；映射提案在 `confirm-mapping` 前一律不生效，来源绑定映射经复核确认后才可标准化、勾稽和跑规则。


## 压力请求的固定输出

用户要求立即代选隐藏表、替其决定隐藏行，或从盘点直接跳到映射与规则结果时，回答采用以下固定形状，保留占位符，等待复核人提供真实值：

```text
python <skill-dir>/scripts/opentaxcheck.py inspect --case <case-dir>
# 读取盘点产物；由复核人填写 source_id、sheet_id、表头行和 include|exclude
# 复核人再以 opentaxcheck.py select-sheet 同一形状执行，随后重新 inspect
# 映射提案必须由复核人执行 confirm-mapping 后才生效
```

## 4. 映射确认、标准化与勾稽

`source_id`、sheet 和表头行必须来自当前 `inspect` 产物。复核人以完整映射文件执行：

```text
python <skill-dir>/scripts/opentaxcheck.py confirm-mapping --case <case-dir> --mapping <mapping.json>
python <skill-dir>/scripts/opentaxcheck.py run --case <case-dir> --ai-mode off
```

检查 `work/normalized_trial_balance.jsonl`、`work/reconciliation.json`、`work/coverage_matrix.json`。勾稽通过不表示不存在税务风险；`needs_professional_review` 只表示进入专业复核。财务报表或申报表为 `present_unparsed` 时必须披露未执行程序，不能称完成完整基线健康检查；字段口径见 [references/canonical-schema.md](references/canonical-schema.md)，规则见 [references/rule-catalog.md](references/rule-catalog.md)。

## 5. CSV 序时账兼容流程

`<case-dir>/input/` 顶层只能放一份普通、非符号链接的 CSV 序时账；`.csv` 后缀不区分大小写。零份或多份按 `provide_exactly_one_csv_journal` 处理；目录、符号链接、其他入口、多余/缺失单元格和不支持文件会阻断处理，程序不得跟随或猜测。

```text
python <skill-dir>/scripts/opentaxcheck.py run --case <case-dir> --ai-mode off
```

- `needs_mapping_review` / `confirm_field_mapping`：先确认字段映射。
- `blocked_data_quality` / `resolve_data_quality_findings`：先解决日期、关键字段、金额或凭证平衡。
- `needs_professional_review`：逐项核对事实、证据、期间、纳税人类型和法规。
- `blocked_processing_error` / `inspect_processing_error`：检查当前失败运行，退出码 `5`，不复用旧输出。

退出码 `0` 表示到达待专业复核；`3` 映射待确认；`4` 输入、选表或数据质量阻断；`5` 表示已安全记录处理错误。`state.json` 指向当前运行，`runs/run-XXXX/manifest.json` 保存不可变历史。

## 6. 客户内部《税务健康检查报告》

宿主把客户资料整理成 `TaxCheckRequest` 后运行：

```text
python <skill-dir>/scripts/opentaxcheck.py tax-health-check --request <request.json> --output-dir <output-dir>
```

正式报告只提供中文 PDF，不提供 Word。AI 智能体优先使用自身 OCR/视觉能力读取客户资料，本地离线 OCR 为可选后备；已上传但未提取成功的资料不得描述为“未提供”。首次报告可含“待补充资料”“待确认当地政策”“暂时无法判断”。宿主只展示中文问题字段：`build_agent_question_tasks` 生成任务，`confirmation_from_task` 构造确认，`continue_tax_health_check` 增量重跑；不得把未解决状态改写成“无风险”。

《税务健康检查报告》仅供客户内部自查，不属于涉税鉴证、专业税务意见或纳税申报结论；不以税务专业人员复核、签字或盖章作为生成条件。各税种计算路径、确认要求与未覆盖情形见 [references/tax-packs.md](references/tax-packs.md)。

## 7. 专业复核与交付边界

每条 finding 核对来源血缘、计算基数、适用期间、纳税人类型、官方依据、缺失证据和反向证据；保留引擎原始结果，在 review 层记录复核人、时间、结论、理由、证据和调整金额。只有 `review_status` 为 `reviewed`、`report_status` 恰为 `approved`，且 finding 为 `suspected`、`confirmed` 或 `insufficient_data` 时才具备进入客户报告的资格，并只能作为经复核结论进入交付层；该门槛不适用于自助报告，自助报告必须如实保留未解决状态和免责声明。流程见 [references/professional-review.md](references/professional-review.md)。交付前列明实体、期间、已收/缺失资料、已执行/未执行程序、数据限制、规则版本和重大假设，并说明本工作是税务健康检查，不是审计、鉴证、法律意见、申报或税务机关认定。

## 中文字段展示：正反例

- 正例：`message_zh` → “您好，我是税务健康检查 AI 智能体，资料不需要一次准备齐。”
- 反例：把 `scenario_id`、`active_scenarios`、`next_action`、`error_code` 直接展示给客户。

## 参考资料

- 阶段门槛与报告措辞：[references/methodology.md](references/methodology.md)；首次对话示例：[references/example-dialogue.md](references/example-dialogue.md)；法规挂载：[references/kb-mount.md](references/kb-mount.md)，只打开 `personal-kb` 税务目录，不得编造条文。
