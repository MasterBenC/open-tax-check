# Task 4 Report: 报告视图与机器快照同源

## RED / GREEN

- RED 1：一致性测试因 `build_report_view_v3()` 不接受 `revision` 而失败。
- RED 2：交叉核对已有 3 项 clear、1 项 risk，但 coverage 全部为 `not_performed`。
- RED 3：绑定到检查项的 `received_pending` 资料未覆盖 clear 状态。
- RED 4：未执行交叉核对没有按当前 `TaxStatus.BLOCKED` 保守降级。
- RED 5：机器 payload 未拒绝未知资料状态。
- GREEN：`PYTHONPATH=src python -m pytest tests/test_report_view.py tests/test_tax_health_reporting.py tests/test_tax_health.py -q`
  结果为 `47 passed, 20 subtests passed`。

## 接口

- `ReportViewV3.scenario_coverage: ScenarioCoverageSnapshot`
- `build_report_view_v3(..., confirmed_facts=None, revision=None)`
- `tax_snapshot_payload(..., material_statuses=(), tax_cross_checks=None, confirmed_facts=None, revision=None)`
- 正式 JSON 写出包含顶层 `scenario_coverage`，其子协议为 `tax-scenario-coverage/v1`。
- 报告主协议保持 `tax-health-report/v3`。
- 视图和机器 payload 均调用同一个私有适配器；每次构造只生成一次 coverage，机器侧只序列化该对象一次。

## 状态适配表

| 当前证据 | coverage 闭集输入 |
|---|---|
| `MATCHED` | `matched` |
| `WITHIN_TOLERANCE` | `within_tolerance` |
| `DIFFERENCE_FOUND` + `unresolved` / `explained_pending_verification` | `difference_found` |
| `DIFFERENCE_FOUND` + `closed` | `confirmed`（仍为 risk，不伪装 clear） |
| `NOT_PERFORMED` + `TaxStatus.BLOCKED` | `blocked` |
| `NOT_PERFORMED` + `TaxStatus.NEEDS_EVIDENCE` | `needs_evidence` |
| `NOT_PERFORMED` + `TaxStatus.NEEDS_LOCAL_POLICY` | `needs_local_policy` |
| `NOT_PERFORMED` + 全部相关税种 `NOT_APPLICABLE` | `not_applicable` |
| 其他 `NOT_PERFORMED` | `not_performed` |
| 显式 `check_id` 的资料状态 | 原值交给 scenario_map 闭集转换 |
| `confirmed_facts` | 原样传递；未知键、非布尔值和矛盾由 scenario_map fail-closed |

冲突状态同时阻止报告视图和机器 payload 交付；未知资料状态也会阻止交付。

## Commit

`b3c32ce93b20e535c0c4a2af368c1a3b2f5222bd`

## Concerns

- 为兼容既有预执行接口，直接调用 `tax_snapshot_payload()` 且快照没有任何 results、也未传 revision/场景输入时，保留旧键集合；正式 `write_tax_health_snapshot()` 始终显式传当前 revision，因此交付 JSON 始终包含 `scenario_coverage`。
- 资料只有显式提供 `check_id` 时才绑定检查项；未绑定资料仍校验状态，但不会猜测检查项关联。

---

# Fix round 1/5

## RED / GREEN

- C1 RED：正式 delivery 的 HTML/PDF coverage 含 3 clear + 1 risk，机器 JSON 重新构造后为 4 not-performed；底层 coverage 构造计数为 2。
- I1 RED：空 results 的 `tax_snapshot_payload()` 缺少 `scenario_coverage`。
- I2 RED：正式形态资料只有 `source_id/status/tax_ids` 时，唯一 source-ref 匹配没有覆盖 clear 状态。
- I3 RED：NOT_PERFORMED 只看到部分相关税种为 NOT_APPLICABLE 时被错误归为 not-applicable。
- I4 RED：公共 loader 不存在，且 coverage fact key 被旧 guide key allowlist 丢弃；正式 delivery 未读取确认事实。
- GREEN：`PYTHONPATH=src python -m pytest tests/test_report_view.py tests/test_tax_health_reporting.py tests/test_tax_health.py tests/test_delivery.py tests/test_customer_guide.py -q`
  结果为 `133 passed, 3 skipped, 20 subtests passed`。

## 单次构造路径

正式 delivery 现在按以下顺序执行：

1. 读取当前 immutable `TaxCheckSnapshot`、source manifest、material statuses、`TaxCrossCheckRun`。
2. 通过 `load_confirmed_scenario_facts(case_dir)` 安全读取 `work/scenario-facts.json`，仅保留 customer 或非空 `human:*` 确认的严格布尔值，再精确过滤为 scenario catalog 已声明的 coverage fact keys。
3. 调用一次 `_build_scenario_coverage(...)`。
4. 同一 `ScenarioCoverageSnapshot` 注入 `build_report_view_v3(...)`、`write_tax_health_snapshot(...)` 和 snapshot digest。
5. HTML、PDF 和机器 JSON 序列化结果完全一致，revision 与 delivery revision 一致。

`build_report_view_v3`、`tax_snapshot_payload`、`write_tax_health_snapshot` 均支持可选 `scenario_coverage` 注入；注入后不重建，并校验类型与 revision。

## 状态修复

- 空 results 也无条件输出 `tax-scenario-coverage/v1`。
- material source_id 仅在与某一 cross-check 的 `source_ref.source_id` 或 `source_ref.locator` 唯一匹配时绑定；不匹配或多检查匹配均不改变具体检查计数。
- NOT_PERFORMED 仅在该检查全部关联 tax_ids 均存在于 snapshot 且全部 NOT_APPLICABLE 时归为 not-applicable。
- `scenario-facts.json` 无效时返回空事实；AI、无来源、空 `human:`、非布尔值均不进入 coverage。
- customer guide 的高层场景问题 facts 不会被映射或伪造成细粒度 coverage facts；delivery 只传 catalog 中原本存在、且被人类明确确认的 fact keys。

## Commit

`a69371ad7512958e6290a1bb943d2b6a74785979`

## Concerns

- 聚焦测试有 3 项按现有平台条件跳过；本轮未运行完整回归，遵循任务要求仅运行指定五个测试文件。
- 未唯一匹配到 cross-check source ref 的资料仍完整保留在报告资料区，但不会影响具体检查 coverage 计数。

---

# Fix round 2/5

## RED / GREEN

- C2 RED：公共调用者可传入裸 `ScenarioCoverageSnapshot`，使
  `DIFFERENCE_FOUND` evidence 被序列化为 clear；机器 writer 也只接受裸 coverage。
- I2a RED：同一 `source_id` 被多个 cross-check 引用时，资料状态因候选数大于一而
  被忽略，没有覆盖所有引用检查，也没有触发 risk/资料缺口冲突的 fail-closed。
- I2b RED：material `source_id` 与 source-ref `locator` 文本碰撞会被错误绑定。
- GREEN：
  `PYTHONPATH=src python -m pytest tests/test_report_view.py tests/test_tax_health_reporting.py tests/test_tax_health.py tests/test_delivery.py tests/test_customer_guide.py -q`
  结果为 `133 passed, 3 skipped, 20 subtests passed`。

## 接口变化与单次构造

- `build_report_view_v3` 删除公共 `scenario_coverage` 参数，始终从当前 snapshot、
  cross-check、material statuses、confirmed facts 和 revision 构造 coverage。
- `tax_snapshot_payload` 与 `write_tax_health_snapshot` 删除裸 coverage 注入；正式同源
  路径只接受完整 `ReportViewV3`。复用前校验 v3 schema、revision、period、
  report id、coverage revision 及非空 entity。
- 同时传 `report_view` 与另一套 cross-check/material/facts 会拒绝为
  `report_view_evidence_conflict`。
- 正式 delivery 先且只调用一次 `build_report_view_v3`；HTML/PDF 直接消费该 view，
  snapshot digest 与机器 JSON 通过已校验的同一 view 序列化
  `view.scenario_coverage`。
- 公共默认机器 payload 路径不传 view 时仍只依据调用者提供的当前 evidence 自行构造，
  无法用裸 coverage 绕过 evidence。

## 状态适配修正

- material 仅按 `material.source_id == source_ref.source_id` 精确绑定，完全不读取
  `source_ref.locator` 做关联。
- 精确匹配后绑定所有引用该 source_id 的 check ids，不再要求唯一候选。
- 同源资料状态与检查风险状态冲突时沿用 scenario_map 保守优先级并 fail-closed；
  unmatched material 不改变具体检查 coverage，仍由报告资料区保留。

## Commit

`c91b47c`（本报告补记 SHA 后 amend，最终 SHA 见提交历史）。

## Concerns

- 聚焦测试有 3 项按现有平台条件跳过；本轮按要求未运行完整回归。
