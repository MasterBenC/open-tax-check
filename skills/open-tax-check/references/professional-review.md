# 专业复核与逐层核对

本文件承接“逐层核对”路径的具体复核流程。**只适用于逐层核对路径（`init` → `inspect` → `select-sheet` → `confirm-mapping` → `run` → `case-delivery`）**；客户自助自查路径（`case-self-check` → `tax-health-check`）不走这里的批准门槛，那份《税务健康检查报告》仅供客户内部自查，不以税务专业人员复核、签字或盖章作为生成条件。

## 1. 五层核对

| 层 | 谁负责 | 核对内容 | 不通过时 |
| --- | --- | --- | --- |
| 事实层 | 客户确认 + 复核人核证据 | 实体、期间、纳税人类型、经营事实、申报表数字 | 记为 `missing` / `present_unparsed`，不得估计 |
| 映射层 | 复核人 | `source_id`、工作表、表头行、字段映射、金额口径 | `needs_mapping_review` / `confirm_field_mapping` |
| 计算层 | 引擎 | 公式、税基、期间、规则版本、血缘 | 修输入重跑，不改引擎、不手改结果 |
| 规则层 | 复核人 | 规则是否适用、地方参数、法规版本 | 写“待确认当地政策”，不外推 |
| 结论层 | 税务专业人员 | finding 状态、严重程度、风险金额、建议 | 保留为 `candidate` / `insufficient_data` |

引擎负责算术、确定性匹配、标识、血缘、规则版本和状态；AI 只解释模式、排序候选、建议证据、草拟文字。模型选择不得改变税务计算、案件状态、证据要求或客户结论。

## 2. Finding 生命周期

`candidate` → `suspected` / `confirmed` / `closed` / `insufficient_data`。每次转换必须有 review 记录，并保留引擎原始结果，使后来复核人能区分来源事实、系统计算、AI 评论和专业判断。

- 语义命中、关键词命中、外部清单条目：只能停在 `candidate`。
- `calculated` 或“未触发”：不是风险结论，也不是无风险结论。
- 严重程度、风险金额、建议三个字段只能由复核人写，AI 不得代填。

## 3. Review 记录字段

每条 review 记录至少包含：`reviewer`、`reviewed_at`、`finding_id`、`from_status`、`to_status`、`rationale`（理由）、`evidence_refs`（证据引用或 `customer-confirmation:<question_id>`）、`adjusted_amount`（调整金额，可空）、`rule_version`。缺 `rationale` 或 `evidence_refs` 的复核视为未完成。

## 4. 进入客户报告的门槛

只有同时满足以下三条的 finding 才有资格进入确定性结论：

1. `review_status` 为 `reviewed`；
2. `report_status` 恰为 `approved`（缺失、草稿、拒绝或其他取值都不是批准）；
3. finding 状态为 `suspected`、`confirmed` 或 `insufficient_data`（`candidate` 与 `closed` 即使标为 approved 也要排除）。

不满足的 finding 只能留在内部工作底稿，不得把未复核结果放入客户结论。

## 5. 交付前清单

- 实体、期间、规则版本、schema 版本；
- 已收资料、缺失资料、收到但未解析资料；
- 已执行程序、未执行程序及原因；
- 数据限制：隐藏数据、未解析文件、映射假设、期间外事项；
- 重大假设与待确认当地政策；
- 交付物清单与哈希（`case-delivery` 生成 `tax-delivery/v1`，含 `workpaper.xlsx` 与证据清单）。

一句话边界：这是税务健康检查，不是审计、鉴证、法律意见、纳税申报或税务机关认定。
