# 完全虚构公司示例

本示例用于演示 OpenTaxCheck Phase 2A 的本地安全盘点流程。公司、交易、凭证、金额、人物和结论均为完全虚构，不包含任何真实客户信息，也不得被解释为针对任何真实主体的税务意见。

示例数据和本说明随本仓库按 Apache License 2.0 许可；详情见仓库根目录的 `LICENSE`。税务筛查结果必须经过专业复核，不能作为审计、鉴证、法律意见、纳税申报或税务机关认定。

仓库中的 `input/journal.csv` 是可直接审查的虚构文本源，包含三张借贷平衡的凭证：销售收入 1,000,000 元、业务招待费 20,000 元，以及摘要含“中秋礼品及高档茶叶”的办公费 5,000 元。

`scripts/run_fictional_example.py` 会在 `--output` 指定的独立案件中运行时生成 `input/journal.xlsx`，再执行 `inspect`。仓库不提交 `.xlsx` 二进制。预期产物包括：

- `state.json`，状态为 `partial_scope_ready`；
- `work/input_manifest.json`；
- `work/workbook_inventory.json`；
- `work/sheet_selection.json`；
- `work/coverage_matrix.json`。

示例只演示安全盘点、候选识别、自动选择条件和资料覆盖矩阵。它不会标准化 Excel 业务行、执行勾稽或税务规则，也不会生成正式客户报告。

`--output` 必须指向不存在或空目录；runner 会拒绝非空目录，避免覆盖已有数据或混杂旧产物。runner 不会修改示例源数据。
