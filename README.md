# OpenTaxCheck｜开源税务健康检查

OpenTaxCheck 将确定性的 Python 税务规则引擎、AI 税务项目助理和强制专业复核结合起来，用于中国企业税务风险筛查。它提高资料盘点、计算、追溯和交付效率，不替代税务专业人员的判断。

## 当前版本范围

当前仓库处于未发布的 **v0.1 / Phase 2B**：

- `.xlsx`、`.xlsm`、`.xls` 可进入安全盘点层，程序会记录文件哈希、工作表结构、有限表头信号、候选角色、公式/合并/隐藏信息和资料覆盖矩阵；
- 唯一、可见、高置信且不含隐藏数据的工作表才可自动选择；其他情况进入 `needs_sheet_selection`，由复核人运行 `select-sheet`；
- 隐藏工作表不得自动选择；选择含隐藏数据的工作表时，隐藏行必须明确选择 `include` 或 `exclude`；
- CSV 和已确认映射的 Excel 都支持业务行标准化、凭证借贷平衡检查、试算平衡表勾稽、首批确定性规则、语义候选和内部 Markdown 草稿。

Excel 映射提案在复核人明确执行 `confirm-mapping` 前始终不生效；`needs_professional_review` 也不是正式客户报告。财务报表和纳税申报表即使已提供但仍为 `present_unparsed`，也不能宣称已完成完整基线健康检查。PDF、图片已提供可选的本地离线 OCR；联网企业数据和复杂版式理解尚未实现。专业 Excel 工作底稿和完整证据包仍未实现，DOCX/PDF 报告已提供基础导出。公开路线不代表已经交付的能力；详见 [docs/roadmap.md](docs/roadmap.md)。

## 工作方式

1. 初始化案件并明确实体和检查期间。
2. `inspect` 安全盘点 Excel/CSV 输入，生成输入清单、工作簿清单、选表候选和资料覆盖矩阵。
3. 仅在自动选择条件全部满足时接受工作表；否则按 `state.json` 的 `next_action` 人工选表，并记录来源哈希、sheet、表头行、隐藏行决定和 schema 版本。
4. 映射提案必须由复核人确认并绑定来源；随后才可运行标准化、勾稽和规则。
5. 勾稽通过不表示不存在税务风险；所有 finding 仍须专业复核，且未解析资料会保留在覆盖矩阵中。

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
opentaxcheck select-sheet --case .\cases\demo --role journal --source aaaaaaaaaaaaaaaa --sheet sheet-0001 --header-row 1 --hidden-rows exclude --reviewer host:user
opentaxcheck confirm-mapping --case .\cases\demo --mapping .\mapping.json
opentaxcheck run --case .\cases\demo --ai-mode off
```

`aaaaaaaaaaaaaaaa` 仅是命令格式示例，绝不是可复用的真实 `source_id`。必须从当前案件的 `work/workbook_inventory.json` 或 `work/sheet_selection.json` 读取实际来源标识，并在来源变更后重新 `inspect`。

若状态为 `needs_sheet_selection`，先从 `work/workbook_inventory.json` 和 `work/sheet_selection.json` 取得 `source_id`、`sheet_id` 和候选表头行，再由复核人执行：

```powershell
opentaxcheck select-sheet --case .\cases\demo --role journal --source <source_id> --sheet <sheet_id> --header-row <行号> --hidden-rows exclude --reviewer host:<复核人>
opentaxcheck inspect --case .\cases\demo
```

当前读取器尚不支持实际过滤隐藏行。含隐藏数据时，`exclude` 不会过滤后继续处理，而是阻断并记录 `hidden_row_exclusion_unavailable`；复核人只能在复核后改为 `include`，或停止处理。不得省略该决定。隐藏工作表只能在人工复核后显式选择，不能自动选择。

macOS 或 Linux 使用 `source .venv/bin/activate` 激活虚拟环境；其余命令相同。

### 兼容的 CSV 筛查

当 `input/` 顶层只有一份非符号链接的普通 CSV 序时账时，既有流程仍可运行：

```powershell
opentaxcheck run --case .\cases\demo --ai-mode off
```

Excel 文件不能交给 CSV 适配器冒充 CSV 处理；只有来源绑定映射经复核确认后，`run` 才会标准化 Excel、生成勾稽和规则结果。

## 运行虚构示例

仓库保留一份完全虚构且借贷平衡的 CSV 文本作为可审查的示例源。runner 会在独立案件目录中生成 `journal.xlsx`、`trial-balance.xlsx`，以及仅供 Phase 2A 检测且保持 `present_unparsed` 的财务报表和纳税申报表文本占位证据；随后从 `inspect` 产物读取真实 `source_id`，取得映射提案后写入并确认来源绑定的虚构映射。仓库不提交 `.xlsx` 二进制。

```powershell
.\.venv\Scripts\python.exe scripts\run_fictional_example.py --output work\phase-2b-demo --format excel
```

已激活虚拟环境时，也可运行：

```text
python scripts/run_fictional_example.py --output work/fictional-demo
```

`--output` 必须指向不存在或空目录；runner 会拒绝非空目录，避免覆盖已有数据或混杂旧产物。Excel 和 CSV 示例的业务招待费发现及金额一致；Excel 的序时账与试算平衡表勾稽通过，状态为 `needs_professional_review`。主要产物包括 `work/normalized_journal.jsonl`、`work/normalized_trial_balance.jsonl`、`work/reconciliation.json`、`work/coverage_matrix.json`、`output/findings.json` 和内部 `output/report.md`。财务报表和纳税申报表相关程序仍是 `present_unparsed`，因此示例不构成完整基线健康检查。公司、交易、凭证、金额、人物和结论均为完全虚构。

## 输出

### Excel / Phase 2B 输出

- `state.json`：当前状态和下一步动作；
- `work/input_manifest.json`：输入清单、哈希与安全诊断；
- `work/workbook_inventory.json`：工作簿和工作表盘点；
- `work/sheet_selection.json`：自动选择或人工候选；
- `work/coverage_matrix.json`：资料覆盖状态和未执行程序；
- `reviews/sheet_selection.json`：存在人工选表时的来源绑定决定。
- `reviews/mapping_review.json`：复核人确认的完整来源绑定字段映射；提案本身不生效。
- `work/normalized_journal.jsonl`：标准化序时账；
- `work/normalized_trial_balance.jsonl`：标准化试算平衡表；
- `work/reconciliation.json`：试算平衡表自身和序时账至试算平衡表的勾稽结果；

资料缺失、未解析或程序未执行时，覆盖矩阵会明确记录 `missing`、`present_unparsed` 或相应 `procedure_status`，不会描述为“无风险”。勾稽通过不表示不存在税务风险。

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

### 实验性的客户内部自查 API：《税务健康检查报告》

基础框架另行提供实验性的客户内部自查 API：

```python
from open_tax_check.tax_health import (
    continue_tax_health_check,
    run_tax_health_check,
)
from open_tax_check.tax_reporting import render_tax_health_report
from open_tax_check.tax_reporting import write_tax_health_report, write_tax_health_snapshot
```

宿主 Agent 负责将客户答复或补充资料转换为结构化确认；Python 核心不依赖任何模型，并以确定性接口校验确认和执行增量重跑。报告会在首次检查后立即生成；`needs_evidence`、`needs_local_policy` 和 `blocked` 等未解决项目会继续保留在后续渐进式报告中。

该报告仅供客户内部自查，不属于涉税鉴证、专业税务意见或纳税申报结论。不以税务专业人员复核、签字或盖章作为生成条件；是否申报、调整或另行咨询专业机构由客户自行决定。这个自查 API 与上文仍要求专业复核的 Phase 2B 客户交付工作流相互独立。

全国 20 个税种的 2023–2026 年适用性筛查规则包已经随版本内置，按税种、地区和期间选择。印花税另有正式法定税率表计算包，覆盖 17 个法定税目，并按 2023-08-28 证券交易税率调整分段；契税正式计算包接受客户确认的省级具体税率并强制校验 3%–5% 法定幅度。其他税种的税额计算、地方政策参数和专业复核仍不完整。客户不需要单独安装规则包，升级 OpenTaxCheck 包即可获得新版官方规则包。规则包之外的第三方内容不会自动信任。

宿主 Agent 接入见 [七生态接入契约](skills/open-tax-check/references/host-integrations.md)。Python 核心同时提供 `build_agent_question_tasks`、`task_payload` 和 `confirmation_from_task`，用于把缺口问题交给客户宿主 Agent 对话，再把结构化确认交回确定性核心。该契约覆盖 Codex、Hermes、WorkBuddy、扣子、Qoder、Claude Code 和 OpenClaw 的共同业务字段，但不声明平台认证，也未实现 MCP/HTTP 部署、认证或租户隔离。

也可以直接使用 CLI 生成首版报告：`opentaxcheck tax-health-check --request request.json --output-dir output/`。请求 JSON 只接受期间、地区、结构化事实和输入哈希；输出为中文 Markdown 报告及机器可读快照。需要 Word 交付物时追加 `--include-docx`；在安装 LibreOffice 的宿主环境中可再追加 `--include-pdf`。DOCX/PDF 与 Markdown 共享同一快照，不会新增税务结论。

通用税额计算内核只接受调用方明确提供、客户确认且带证据引用的计税基础、税目、税率和扣除额。印花税法定税率只按客户确认的精确税目选择；AI 不得自动分类，当前规则包未提供的税率和地方政策不会由程序或 AI 猜测。

参数化规则包的安装、调用、公式和交付边界见 [`docs/calculation-packs.md`](docs/calculation-packs.md)。
需要一次启用全部 20 个参数化计算模板时，可在 CLI 追加 `--include-parameterized-packs`；默认流程只会在印花税适用性、税目、计税依据及证据均确认后执行已内置的正式计算。

## 隐私与云端授权

当前核心默认只在本地处理，不收集遥测，也不向 OpenTaxCheck 项目方上传资料。统一的宿主文档抽取 JSON、授权和导入边界见 [文档抽取合同](docs/document-extraction-contract.md)。

### 本地 OCR（可选）

离线 PDF/PNG/JPEG OCR 通过 `local-paddleocr` provider 提供。安装可选依赖：`pip install -e ".[ocr]"`；模型必须由操作者显式预热：`opentaxcheck prepare-ocr --model-dir PATH`；案件提取使用 `opentaxcheck extract-document --provider local-paddleocr --model-dir PATH`。案件运行不会联网下载模型，缺少依赖或模型会返回 `not_performed`。烟测只有在 `OPEN_TAX_CHECK_RUN_OCR_SMOKE=1` 时才会执行；默认结果是明确跳过。专用 CI 烟测会在隔离环境中下载 PaddleOCR 官方移动模型并识别完全虚构的图片；一旦显式启用，依赖、下载或识别失败都会按失败处理，不再伪装成跳过。

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
