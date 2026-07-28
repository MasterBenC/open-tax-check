# 完全虚构公司示例

本示例演示 OpenTaxCheck Phase 2B 的安全盘点、来源绑定映射确认、标准化、勾稽和规则流程。公司、交易、凭证、金额、人物和结论均为完全虚构，不包含任何真实客户信息，也不得被解释为针对任何真实主体的税务意见。

示例数据和本说明随本仓库按 Apache License 2.0 许可；详见仓库根目录的 `LICENSE`。税务筛查结果必须经过专业复核，不能作为审计、鉴证、法律意见、纳税申报或税务机关认定。

仓库中的 `input/journal.csv` 是可直接审查的虚构文本源，包含三张借贷平衡的凭证：销售收入 1,000,000 元、业务招待费 20,000 元，以及摘要含“中秋礼品及高档茶叶”的办公费 5,000 元。

`scripts/run_fictional_example.py` 会在 `--output` 指定的独立案件中运行时生成 `input/journal.xlsx`、`input/trial-balance.xlsx`，以及仅供 Phase 2A 检测的财务报表和纳税申报表文本占位证据；这些占位证据保持 `present_unparsed`。runner 从 `inspect` 产物读取真实 `source_id`，取得映射提案后写入并确认来源绑定的虚构 mapping JSON。仓库不提交 `.xlsx` 二进制。预期产物包括：

- `state.json`，状态为 `needs_professional_review`；
- `work/input_manifest.json`、`work/workbook_inventory.json`、`work/sheet_selection.json` 和 `work/coverage_matrix.json`；
- `work/normalized_journal.jsonl`、`work/normalized_trial_balance.jsonl` 和 `work/reconciliation.json`；
- `output/findings.json` 和仅供内部复核的 `output/report.md`。

Excel 与 CSV 示例均产生同一笔业务招待费规则金额；Excel 序时账和试算平衡表勾稽通过。勾稽通过不表示不存在税务风险，所有结果保持 `needs_professional_review`，不会生成正式客户报告。财务报表和纳税申报表程序仍为 `present_unparsed`，因此不能宣称已完成完整基线健康检查。

`--output` 必须指向不存在或空目录；runner 会拒绝非空目录，避免覆盖已有数据或混杂旧产物。runner 不会修改示例源数据。
