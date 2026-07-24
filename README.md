# OpenTaxCheck｜开源税务健康检查

OpenTaxCheck 将确定性的 Python 税务规则引擎、AI 税务项目助理和强制专业复核结合起来，用于中国企业税务风险筛查。它提高资料盘点、计算、追溯和交付效率，不替代税务专业人员的判断。

## 当前版本范围

当前仓库处于未发布的 **v0.1 / Phase 2A**：

- `.xlsx`、`.xlsm`、`.xls` 可进入安全盘点层，程序会记录文件哈希、工作表结构、有限表头信号、候选角色、公式/合并/隐藏信息和资料覆盖矩阵；
- 唯一、可见、高置信且不含隐藏数据的工作表才可自动选择；其他情况进入 `needs_sheet_selection`，由复核人运行 `select-sheet`；
- 隐藏工作表不得自动选择；选择含隐藏数据的工作表时，隐藏行必须明确选择 `include` 或 `exclude`；
- 现有 CSV `run` 流程保持兼容，继续支持字段映射、业务行标准化、凭证借贷平衡检查、首批确定性规则、语义候选和内部 Markdown 草稿。

Phase 2A 尚未标准化 Excel 业务行，也不会对 Excel 执行勾稽、税务规则或生成正式客户报告；这些属于 Phase 2B。PDF、图片 OCR 和联网企业数据尚未实现。专业 Excel 工作底稿、DOCX、PDF 报告和完整证据包也尚未实现。公开路线不代表已经交付的能力；详见 [docs/roadmap.md](docs/roadmap.md)。

## 工作方式

1. 初始化案件并明确实体和检查期间。
2. `inspect` 安全盘点 Excel/CSV 输入，生成输入清单、工作簿清单、选表候选和资料覆盖矩阵。
3. 仅在自动选择条件全部满足时接受工作表；否则按 `state.json` 的 `next_action` 人工选表，并记录来源哈希、sheet、表头行、隐藏行决定和 schema 版本。
4. `partial_scope_ready` 只表示 Phase 2A 盘点已完成，不表示 Excel 已标准化、已勾稽、已跑规则或“无风险”。
5. 对单一 CSV 序时账，仍可使用 `run` 进入既有确定性筛查与专业复核流程。

## 快速开始

需要 Python 3.11、3.12 或 3.13。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[excel]"
opentaxcheck init --case .\cases\demo --entity "虚构科技有限公司" --period-start 2025-01-01 --period-end 2025-12-31
```

把不含真实客户信息的测试 `.xlsx`、`.xlsm`、`.xls` 或 CSV 放入 `.\cases\demo\input\`，然后运行：

```powershell
opentaxcheck inspect --case .\cases\demo
```

若状态为 `needs_sheet_selection`，先从 `work/workbook_inventory.json` 和 `work/sheet_selection.json` 取得 `source_id`、`sheet_id` 和候选表头行，再由复核人执行：

```powershell
opentaxcheck select-sheet --case .\cases\demo --role journal --source <source_id> --sheet <sheet_id> --header-row <行号> --hidden-rows exclude --reviewer host:<复核人>
opentaxcheck inspect --case .\cases\demo
```

如决定纳入隐藏行，将 `--hidden-rows exclude` 改为 `--hidden-rows include`；不得省略该决定。隐藏工作表只能在人工复核后显式选择，不能自动选择。

macOS 或 Linux 使用 `source .venv/bin/activate` 激活虚拟环境；其余命令相同。

### 兼容的 CSV 筛查

当 `input/` 顶层只有一份非符号链接的普通 CSV 序时账时，既有流程仍可运行：

```powershell
opentaxcheck run --case .\cases\demo --ai-mode off
```

Excel 文件不能交给当前 `run` 冒充 CSV 处理；完成 `inspect` 不代表可以运行 Phase 2B。

## 运行虚构示例

仓库保留一份完全虚构且借贷平衡的 CSV 文本作为可审查的示例源。runner 会在独立案件目录中运行时生成 `journal.xlsx`，仓库不提交 `.xlsx` 二进制。

```powershell
.\.venv\Scripts\python.exe scripts\run_fictional_example.py --output work\fictional-demo
```

已激活虚拟环境时，也可运行：

```text
python scripts/run_fictional_example.py --output work/fictional-demo
```

`--output` 必须指向不存在或空目录；runner 会拒绝非空目录，避免覆盖已有数据或混杂旧产物。成功后状态为 `partial_scope_ready`，主要产物是 `work/workbook_inventory.json`、`work/sheet_selection.json` 和 `work/coverage_matrix.json`。示例只演示 Phase 2A 安全盘点，不生成 `findings.json` 或正式报告。公司、交易、凭证、金额、人物和结论均为完全虚构。

## 输出

### Phase 2A 盘点输出

- `state.json`：当前状态和下一步动作；
- `work/input_manifest.json`：输入清单、哈希与安全诊断；
- `work/workbook_inventory.json`：工作簿和工作表盘点；
- `work/sheet_selection.json`：自动选择或人工候选；
- `work/coverage_matrix.json`：资料覆盖状态和未执行程序；
- `reviews/sheet_selection.json`：存在人工选表时的来源绑定决定。

资料缺失、未解析或程序未执行时，覆盖矩阵会明确记录 `missing`、`present_unparsed` 或相应 `procedure_status`，不会描述为“无风险”。

### 兼容的 CSV `run` 输出

- `work/mapping_review.json`：字段映射结果；
- `work/normalized_journal.jsonl`：标准化序时账；
- `work/data_quality.json`：数据质量问题；
- `output/findings.json`：硬规则结果和待复核语义候选；
- `output/report.md`：仅供内部复核的 Markdown 草稿。

CSV 日期确定性支持 `YYYY-MM-DD`、`YYYY/M/D` 和 `YYYY.M.D`。有效日期会被标准化，凭证按“会计月份 + 凭证号”检查平衡，年度税务规则按纳税年度分别计算；日期或关键行字段缺失/无效时，流水线会以数据质量问题阻止计算。

CSV `run` 要求 `input/` 顶层只有一份非符号链接的普通 CSV 序时账；`.csv` 后缀不区分大小写。不支持文件、目录、符号链接或其他顶层入口会阻止计算，且程序不会跟随目录或链接。CSV 行中出现多余或缺失单元格也会阻止计算，诊断不会回显单元格内容。

金额只接受 ASCII 数字、可选 ASCII 正负号、普通整数或小数，以及合法三位千分位。字段首尾空白会被忽略，空白金额按零处理。每个金额最多包含 18 位整数和 2 位小数；整套数据中借贷金额的绝对金额总额最多包含 20 位整数。程序使用 50 位十进制精度精确累加，仅在最终展示时按 `ROUND_HALF_UP` 舍入到 2 位小数。畸形千分位、`NaN`、`Infinity`、指数记法、全角数字、内部空格、货币符号、括号负数及越界值均为阻断项。

## AI 模式

命令行接受 `off`、`assist` 和 `deep`。当前 Python 核心不会自行调用外部大模型；`assist` 和 `deep` 只为支持 AI 的宿主 Agent 预留，不会绕过选表、映射、数据质量或专业复核门槛。保密或无模型环境使用 `off`。

AI 可以协助解释资料、整理证据和草拟文字，但不得静默修改原始数据、选表决定、计算公式、适用期间、正式规则或专业结论。

## 隐私与云端授权

当前核心默认只在本地处理，不收集遥测，也不向 OpenTaxCheck 项目方上传资料。当前核心不调用 OCR、企业查询或大模型云服务。未来任何云端适配器都必须在调用前说明服务和拟发送的数据范围，并取得明确授权；密钥只能放在环境变量或宿主安全凭证中。

真实项目应按机构制度执行最小化、脱敏、访问控制、备份和安全删除。日志、截图、Issue、Discussion、测试、提交记录和示例均不得包含真实客户数据或凭证。

## 规则覆盖与限制

兼容的 CSV 流程只包含用于验证架构的首批通用筛查逻辑，例如业务招待费扣除限额计算和少量确定性摘要关键词候选。业务招待费规则 `2026.2` 的收入基数仅汇总账内可识别的主营业务和其他业务收入科目；完整性仍未确认，必须与年度企业所得税申报表及视同销售收入核对。`calculated` 不代表最终申报税额。

“未触发”仅表示在已提供资料、已确认映射和当前规则版本下未命中，不表示没有税务风险。每条结果仍需结合适用年度的官方法规、企业事实、反向证据和专业判断复核。

## 贡献税务规则

欢迎提交可验证、可追溯的税务规则。每个规则提案必须说明税种、适用期间、纳税人类型、触发条件、排除条件、官方法规依据、计算公式、证据要求、常见误报、虚构测试输入、预期输出和专业复核边界。详见 [CONTRIBUTING.md](CONTRIBUTING.md) 和税务规则提案 Issue 表单。

## 社区与税争协作

GitHub Issues 用于程序缺陷和结构化规则提案，GitHub Discussions 用于公开问答、方法讨论和路线交流。涉及具体客户的复杂税收争议，应另行建立经过确认的私密协作渠道，不在公开仓库讨论事实。

微信：PosterNote；添加时请备注 GitHub。请勿通过 GitHub Issue、Discussion 或微信发送未经脱敏的客户资料。

## 免责声明

本项目输出是税务风险筛查和咨询辅助材料，不是审计、鉴证、法律意见、纳税申报或税务机关认定。使用前请完整阅读 [DISCLAIMER.md](DISCLAIMER.md)，并由具备相应经验的税务专业人员复核。

## 许可证

代码和项目文档按 [Apache License 2.0](LICENSE) 提供。该许可证不自动授予项目名称、标识、发起人姓名或形象的商标、背书或专业服务许可。
