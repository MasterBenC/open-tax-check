---
name: open-tax-check
description: Use when checking Chinese enterprise tax materials from CSV or Excel workbooks, especially when source scope, worksheet selection, data quality, rule coverage, or professional review may be incomplete.
---

# OpenTaxCheck 税务健康检查

把本 Skill 当作 Python 引擎的薄控制器。严格按 `state.json` 推进；代码负责盘点、映射、计算、血缘和状态，AI 只协助解释、整理证据和草拟文字。

## 先守住范围

- Phase 2A 支持 `.xlsx`、`.xlsm`、`.xls` 的安全盘点、候选识别、人工选表和资料覆盖矩阵。
- 现有 CSV `run` 流程继续支持业务行标准化、数据质量检查、首批规则、语义候选、JSON/JSONL 和内部 Markdown 草稿。
- Phase 2B 支持已确认来源绑定映射的 Excel 业务行标准化、试算平衡表勾稽、确定性规则和内部 Markdown 草稿；映射提案在 `confirm-mapping` 前一律不生效。
- PDF、图片已提供可选的本地离线 OCR；联网企业数据和复杂版式理解尚未实现。
- 不得把 `partial_scope_ready`、`calculated` 或“未触发”解释为无风险、完整或最终结论。
- 不得自动选择隐藏工作表。含隐藏数据的可见工作表也必须人工确认。
- 不得把关键词或语义命中变成税务结论；其状态保持 `candidate`，直到税务专业人员复核。
- 不得把未复核结果放入客户结论，不得给出总体数字健康分。

## 1. 初始化案件

```text
python <skill-dir>/scripts/opentaxcheck.py init --case <case-dir> --entity "企业全称" --period-start YYYY-MM-DD --period-end YYYY-MM-DD
```

保持原始文件不变。一个案件只对应一个法律实体。未明确期间时先停止并补充期间。

## 2. Excel / 工作簿走 Phase 2A

把测试工作簿放入 `<case-dir>/input/`，运行：

```text
python <skill-dir>/scripts/opentaxcheck.py inspect --case <case-dir>
```

读取 `state.json` 的 `status` 和 `next_action`，同时查看 `work/workbook_inventory.json`、`work/sheet_selection.json` 和 `work/coverage_matrix.json`。

| 状态 | 下一步 |
| --- | --- |
| `needs_intake_review` | 按 `next_action` 补期间、移除不安全入口或处理输入问题。 |
| `needs_sheet_selection` | 由复核人执行 `select-sheet`。 |
| `partial_scope_ready`（`next_action=await_phase_2b_excel_normalization`） | 从 `work/mapping_review.json` 取得提案，由复核人执行 `confirm-mapping`；提案本身不生效。 |
| `needs_intake_review`（`next_action=inspect_processing_error`） | 检查本次 `inspect` 失败；退出码为 `4`，不复用旧输出。 |

Excel `inspect_case` 的处理异常使用上述契约；`blocked_processing_error` 与退出码 `5` 只属于下文 CSV `run_case`。
`inspect` 的 `next_action` 只完成 Excel Phase 2A 输入门槛；Excel 只有在来源绑定映射经复核确认后才可运行，CSV `run` 仍是独立兼容流程。

只有唯一、可见、高置信且 `has_hidden_data` 为 `false` 的候选才可自动选择。其他情况都按以下正向顺序处理：

1. 从盘点产物读取真实的 `source_id`、`sheet_id` 和候选表头行。
2. 让复核人决定角色、工作表、全部表头行。隐藏行决定必须是 `include` 或 `exclude`。
3. 运行：

```text
python <skill-dir>/scripts/opentaxcheck.py select-sheet --case <case-dir> --role journal --source <source_id> --sheet <sheet_id> --header-row <行号> --hidden-rows <include|exclude> --reviewer host:<复核人>
```

多行表头要重复传 `--header-row`。隐藏行必须明确写为 `--hidden-rows include` 或 `--hidden-rows exclude`。当前读取器尚不能实际过滤隐藏行；含隐藏数据时，`exclude` 不会实际过滤，而会以 `hidden_row_exclusion_unavailable` 阻断。复核人只能在复核后改为 `include`，或停止处理。隐藏工作表只能人工确认，不得自动选择。

4. 再次运行 `inspect`，再次读取 `state.json`。
5. 达到 `partial_scope_ready` 后，读取 `work/mapping_review.json` 的提案；由复核人执行 `confirm-mapping` 后才可标准化、勾稽和跑规则。该门槛控制旧 Phase 2B finding 流程，不阻止下文独立生成客户内部自查报告。

资料覆盖矩阵必须保留 `missing`、`present_unparsed`、`not_performed_*` 等状态；缺失、未解析或未执行不等于“无风险”。人工决定必须与来源哈希、sheet、表头行、隐藏行决定、期间和 schema 版本绑定；来源变化后重新 `inspect`。

## 压力请求的固定输出

当用户要求立即代选隐藏表、替其决定隐藏行，或从 Excel 盘点直接继续 2B 时，回答采用以下固定形状。保留占位符，等待复核人提供真实值：

```text
python <skill-dir>/scripts/opentaxcheck.py inspect --case <case-dir>
# 读取盘点产物；由复核人填写 source_id、sheet_id、表头行和 include|exclude
python <skill-dir>/scripts/opentaxcheck.py select-sheet --case <case-dir> --role journal --source <source_id> --sheet <sheet_id> --header-row <行号> --hidden-rows <include|exclude> --reviewer host:<复核人>
python <skill-dir>/scripts/opentaxcheck.py inspect --case <case-dir>
# 从 mapping_review 读取真实来源绑定映射提案，由复核人执行 confirm-mapping 后才进入 2B
```

## 2B 映射确认、标准化与勾稽

`source_id`、sheet 和表头行必须从当前 `inspect` 产物读取，不能复用文档中的占位值。复核人以完整映射文件执行：

```text
python <skill-dir>/scripts/opentaxcheck.py confirm-mapping --case <case-dir> --mapping <mapping.json>
python <skill-dir>/scripts/opentaxcheck.py run --case <case-dir> --ai-mode off
```

运行后检查 `work/normalized_journal.jsonl`、`work/normalized_trial_balance.jsonl`、`work/reconciliation.json` 和 `work/coverage_matrix.json`。勾稽通过不表示不存在税务风险；`needs_professional_review` 只表示结果进入专业复核。财务报表或纳税申报表为 `present_unparsed` 时，必须披露未执行程序，不能宣称完成完整基线健康检查。

## 3. CSV 走兼容流程

`input/` 顶层只能放一份普通、非符号链接的 CSV 序时账；`.csv` 后缀不区分大小写。零份或多份时按 `provide_exactly_one_csv_journal` 处理。目录、符号链接、其他入口、多余/缺失单元格和不支持文件会阻断处理，程序不得跟随或猜测。

```text
python <skill-dir>/scripts/opentaxcheck.py run --case <case-dir> --ai-mode off
```

- AI 模式只允许 `off`、`assist`、`deep`；无可用 AI 时使用 `off`。
- `needs_mapping_review`：先确认字段映射。
- `blocked_data_quality` / `resolve_data_quality_findings`：先解决日期、关键字段、金额或凭证平衡问题。
- `needs_professional_review`：逐项核对事实、证据、期间、纳税人类型和法规。
- `blocked_processing_error` / `inspect_processing_error`：检查当前失败运行。

退出码 `0` 表示流程到达待专业复核；`3` 表示映射待确认；`4` 表示输入、选表或数据质量阻断；`5` 表示已安全记录处理错误。`state.json` 指向当前运行；`runs/run-XXXX/manifest.json` 保存不可变历史。不得把旧成功输出当作当前结果。

## 4. 客户内部《税务健康检查报告》

宿主 Agent 可把客户资料整理成 `TaxCheckRequest`，再运行：

```text
opentaxcheck tax-health-check --request request.json --output-dir output
```

需要 Word 时追加 `--include-docx`；宿主已安装 LibreOffice 时可追加 `--include-pdf`。默认流程已内置 2023–2026 年正式印花税税率表：先确认是否存在应税事项，再由客户选择法定税目并确认本条计税依据；AI 不得自动判断税目。跨 2023-08-28 证券交易税率变更期间时必须拆分计算。契税也有正式计算包，但省级具体税率必须由客户确认并绑定当地政策证据；系统只校验其处于 3%–5% 法定幅度，不得代选税率或减免。城市维护建设税、教育费附加和地方教育附加分别按确认的所在地类别或法定固定比例计算，但计征基础必须由客户依据两税申报、实缴、减免、留抵退税及进口排除口径确认。需要对全部 20 个税费项目启用参数化计算模板时追加 `--include-parameterized-packs`；这些模板不提供统一法定税率，税基、税率和扣除额必须由客户确认并绑定证据。

首次报告可以包含“待补充资料”“待确认当地政策”和“暂时无法判断”。宿主 Agent 只展示中文问题字段，通过 `build_agent_question_tasks` 生成任务，用 `confirmation_from_task` 构造确认，再调用 `continue_tax_health_check` 增量重跑。AI 不得猜测事实、税率、地方政策或把未解决状态改写成“无风险”。

《税务健康检查报告》仅供客户内部自查，不属于涉税鉴证、专业税务意见或纳税申报结论；不以税务专业人员复核、签字或盖章作为生成条件。

## 5. 旧 finding 专业复核与交付边界

每条 finding 都要核对来源血缘、计算基数、适用期间、纳税人类型、官方依据、缺失证据和反向证据。保留引擎原始结果，在 review 层记录复核人、时间、结论、理由、证据和调整金额。

只有 `review_status` 为 `reviewed`、`report_status` 恰为 `approved`，且 finding 状态为 `suspected`、`confirmed` 或 `insufficient_data` 时，旧 Phase 2B finding 才具备进入客户报告的资格，并只能作为经过复核的确定性结论进入交付层。该审批门槛不适用于上文独立的客户内部自查报告；自查报告必须如实保留未解决状态和免责声明。

所有说明都要列明实体、期间、已收/缺失资料、已执行/未执行程序、数据限制、规则版本和重大假设，并说明本工作是税务健康检查，不是审计、鉴证、法律意见、申报或税务机关认定。永远不要称代码输出“100% 准确”。

## 参考资料

- 调用宿主 Agent 时，读取 [references/host-integrations.md](references/host-integrations.md)；只展示 `question_zh`，不得编造确认或把未解决状态说成“无风险”。

- 调整阶段门槛、覆盖矩阵或报告措辞前，读取 [references/methodology.md](references/methodology.md)。
- 调整输入格式、字段别名、日期或金额口径前，读取 [references/canonical-schema.md](references/canonical-schema.md)。
- 调整税务逻辑、阈值、法规或版本前，读取 [references/rule-catalog.md](references/rule-catalog.md)。

## 常见错误

- 看到高分候选就跳过 `needs_sheet_selection`。
- 未记录隐藏行 `include` / `exclude` 决定。
- 把 `partial_scope_ready` 当作 Excel 已跑规则。
- 用 AI 猜测缺失数字、日期、映射或选表决定。
- 让漂亮报告掩盖资料缺失或未执行程序。
