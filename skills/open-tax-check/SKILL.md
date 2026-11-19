---
name: open-tax-check
description: Use when checking Chinese enterprise tax materials from CSV or Excel workbooks, especially when source scope, worksheet selection, data quality, rule coverage, or professional review may be incomplete.
---

# OpenTaxCheck 税务健康检查

把本 Skill 当作 Python 引擎的薄控制器。严格按 `state.json` 推进；代码负责盘点、映射、计算、血缘和状态，AI 只协助解释、整理证据和草拟文字。

## 一句话安装指令
把这句话发给支持 Skill 的 AI 智能体：“请从 https://github.com/MasterBenC/open-tax-check 获取最新版 OpenTaxCheck，把 `skills/open-tax-check` 安装到当前智能体的 Skill 目录，在该仓库根目录以可编辑模式安装 Python 包（`python -m pip install -e ".[excel,report]"`，本包尚未发布到 PyPI），再运行 `python -m playwright install chromium` 和仓库自带的离线宿主回放、完整虚构案例验证；不要替客户确认经营事实。”当前官方安装源为该 GitHub 仓库，Hermes 可直接获取，其他宿主按各自官方机制导入；OpenTaxCheck 尚未在其他智能体商店正式发布。
## 先和客户这样开始

首次对话先运行第一次 `case-guide`，展示其 `message_zh` 中的“税务健康检查 AI 智能体”欢迎语，并按 `profile_questions` 收集企业名称、所在省/市、主营业务、检查期间和增值税纳税人类型；资料不需要一次准备齐，可以按引导逐步补充。再把这五项信息写入 `customer-profile/v1` JSON，运行 `case-start`，随后运行第二次 `case-guide`：

```text
opentaxcheck case-guide --case <case-dir>
opentaxcheck case-start --case <case-dir> --profile <profile.json>
opentaxcheck case-guide --case <case-dir>
```
第一次 `case-guide` 只取得 `message_zh`、欢迎语和 `profile_questions`；其 `document_groups` 可为空，不能当作动态资料清单。收齐五项基础信息后运行 `case-start`，随后必须再次运行 `case-guide` 取得第二次 `case-guide/v2` 动态引导。机器字段 `scenario_id`、`fact_key`、`question_id`、`group_id`、`active_scenarios` 仅供宿主控制，不向客户直接展示；客户仅看到实际存在的中文字段：`message_zh`、`scenario_questions.question_zh`（如实际提供 `help_zh` 才展示）、`document_groups.title_zh`、`items_zh`、`applicability_note_zh`、`privacy_zh`、`disclaimer_zh` 等。不另写开场或自行判断税种适用性；模型选择规则见 [references/model-recommendations.md](references/model-recommendations.md)。

## 经营场景引导
第二次 `case-guide/v2` 提供 `scenario_questions`、`active_scenarios` 和按基础资料+已激活场景动态生成的 `document_groups`；数量不固定。`scenario_id`、`fact_key`、`question_id`、`group_id`、`active_scenarios` 仅供宿主控制，不向客户直接展示；先保留五项基础档案，再只询问不动产、应税合同、雇员/股东往来及关联/重组等经营事实，明确确认后才展开条件资料。
五类导航场景为：收入与流转、成本费用、资产与不动产、合同及特殊交易、人员、股东及关联事项；场景命中不等于税种适用，AI 智能体不得替客户确认经营事实。
六个英文内部值是接口状态码：`executed_clear`=已执行且未见异常、`executed_risk`=已执行需关注、`awaiting_evidence`=待补资料、`received_unparsed`=已收到待解析、`not_applicable`=不适用、`not_performed`=未执行；宿主只展示中文字段/文案，不直接展示代码。待补资料、已收到待解析和未执行均不计入已执行或已通过。
当前五个固定交叉核对 ID 为 `BOOK-FS-REV-001`、`FS-VAT-SALES-001`、`BOOK-VAT-OUTPUT-001`、`FS-CIT-REV-PROFIT-001`、`BOOK-CIT-BE-001`；外部清单中的未核验条目只能作为询问型候选，100+ 清单不扩张为自动规则或客户结论。

## 先守住范围

- Phase 2A 支持 `.xlsx`、`.xlsm`、`.xls` 的安全盘点、候选识别、人工选表和资料覆盖矩阵。
- 现有 CSV `run` 流程继续支持业务行标准化、数据质量检查、首批规则、语义候选、JSON/JSONL 和内部 Markdown 草稿。
- Phase 2B 支持已确认来源绑定映射的 Excel 业务行标准化、试算平衡表勾稽、确定性规则和内部 Markdown 草稿；映射提案在 `confirm-mapping` 前一律不生效。
- PDF、图片已提供可选的本地离线 OCR；联网企业数据和复杂版式理解尚未实现。
- 安全 ZIP 输入、Fengniao/cloud provider 合同与正式离线回放、`--complete` 完整虚构案例和七宿主合同回放已提供；真实联网服务仍需宿主环境单独验证。
- 不得把 `partial_scope_ready`、`calculated` 或“未触发”解释为无风险、完整或最终结论。
- 不得自动选择隐藏工作表。含隐藏数据的可见工作表也必须人工确认。
- 不得把关键词或语义命中变成税务结论；其状态保持 `candidate`，直到税务专业人员复核。
- 不得把未复核结果放入客户结论，不得给出总体数字健康分。

## 新旧命令的关系

`opentaxcheck`（装包后的命令行入口）与 `python <skill-dir>/scripts/opentaxcheck.py`（没装包时直接用仓库脚本）参数完全相同，二选一即可。两套流程按阶段分工，不要重复建案：引导与自助自查走 `case-guide` → `case-start` → `case-self-check` → `case-status` / `case-tasks` / `case-confirm` → `tax-health-check`；手工逐层核对走 `init` → `inspect` → `select-sheet` → `confirm-mapping` → `run` → `case-delivery`。同一案件目录混用两套时以 `state.json` 的 `next_action` 为准。

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

正式客户报告只提供 PDF，不提供 Word。安装 `open-tax-check[report]` 后必须运行 `python -m playwright install chromium`；缺少 Chromium 时交付失败，不得回退 DOCX。AI 智能体优先使用自身 OCR/视觉能力读取客户资料，本地 OCR 仅为可选后备。已上传资料即使尚未提取成功，也不得描述为“未提供”。默认流程已内置 2023–2026 年增值税常见路径计算：一般计税支持 13%、9%、6%，按确认的不含税销售额乘税率后减允许抵扣进项税额；符合条件的小规模 1% 路径不允许填入进项抵扣额。零税率、免税、3%/5% 简易计税、不动产等未覆盖路径必须保留待确认。印花税税率表要求先确认是否存在应税事项，再由客户选择法定税目并确认本条计税依据；AI 不得自动判断税目。跨 2023-08-28 证券交易税率变更期间时必须拆分计算。契税也有正式计算包，但省级具体税率必须由客户确认并绑定当地政策证据；系统只校验其处于 3%–5% 法定幅度，不得代选税率或减免。房产税仅覆盖房产余值1.2%和租金收入12%标准路径，省级原值减除幅度、征收期间和免减税事实必须先确认。个人所得税仅覆盖居民个人综合所得年度汇算，按客户确认的年度应纳税所得额自动选择3%至45%法定档位和速算扣除数；不得用于经营所得、非居民或分类所得。城市维护建设税、教育费附加和地方教育附加分别按确认的所在地类别或法定固定比例计算，但计征基础必须由客户依据两税申报、实缴、减免、留抵退税及进口排除口径确认。企业所得税常见路径包含居民企业一般税率、高新技术企业和小型微利企业；资格必须由客户确认，小微路径还会校验 300 万元应纳税所得额上限。车辆购置税和烟叶税分别按客户确认的法定计税价格或实际支付价款总额，使用 10% 和 20% 固定税率计算；免减税和特殊计税情形必须保留待确认。需要对全部 20 个税费项目启用参数化计算模板时追加 `--include-parameterized-packs`；这些模板不提供统一法定税率，税基、税率和扣除额必须由客户确认并绑定证据。

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
- 核对法规条文、部门规章或优惠口径前，读取 [references/kb-mount.md](references/kb-mount.md)；有挂载时只打开 `personal-kb` 税务相关目录，不得编造条文。

## 常见错误

- 看到高分候选就跳过 `needs_sheet_selection`。
- 未记录隐藏行 `include` / `exclude` 决定。
- 把 `partial_scope_ready` 当作 Excel 已跑规则。
- 用 AI 猜测缺失数字、日期、映射或选表决定。
- 让漂亮报告掩盖资料缺失或未执行程序。
