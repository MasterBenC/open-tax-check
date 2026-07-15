# OpenTaxCheck｜开源税务健康检查

OpenTaxCheck combines a deterministic Python tax-rule engine, an AI tax project assistant, and mandatory professional review for Chinese enterprise tax health checks.

OpenTaxCheck 将确定性的 Python 税务规则引擎、AI 税务项目助理和强制专业复核结合起来。项目旨在提高风险筛查的效率、可追溯性和交付质量，不替代税务专业人员的判断。

## Current release scope

当前仓库处于 **v0.1 基础版本**，仅支持单一法律实体的 UTF-8/UTF-8-BOM **CSV 序时账**：字段映射、凭证借贷平衡检查、首批确定性硬规则、确定性语义候选筛查，以及 JSON/JSONL 和 Markdown 内部草稿输出。

Excel、PDF、图片、OCR、申报表解析、企业外部信息、云端大模型调用、专业 Excel 工作底稿、DOCX 和 PDF 报告均属于后续版本，当前版本尚不支持。公开路线不代表已经交付的能力。

## How it works

1. Python 读取 CSV，并将常见字段名映射到统一字段。
2. 数据质量检查发现未确认映射或借贷不平时停止处理，并在 `state.json` 给出 `next_action`。
3. 硬规则由 Python 计算；语义关键词只形成待复核候选，不直接形成税务结论。
4. 所有结果保留来源文件、行号、凭证号、规则编号和规则版本等可用血缘。
5. 税务专业人员核实期间、纳税人类型、法规依据、事实和证据后，才可决定是否进入客户报告。

## Quick start

需要 Python 3.11、3.12 或 3.13。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
opentaxcheck init --case .\cases\demo --entity "示例科技有限公司"
```

将一份不含真实客户信息的测试 CSV 放入 `.\cases\demo\input\`，然后运行：

```powershell
opentaxcheck run --case .\cases\demo --ai-mode off
```

macOS 或 Linux 激活虚拟环境时使用 `source .venv/bin/activate`；其余命令相同。

## Run the fictional example

完整虚构案例将在首个公开完整版本前加入。目前 v0.1 仓库基础阶段尚未提供可直接运行的示例目录，请勿使用真实客户账套代替示例数据。

## Outputs

当前 v0.1 在案件目录中生成：

- `state.json`：可恢复状态和下一步动作；
- `work/mapping_review.json`：字段映射结果；
- `work/normalized_journal.jsonl`：标准化序时账；
- `work/data_quality.json`：数据质量问题；
- `output/findings.json`：硬规则结果和待复核语义候选；
- `output/report.md`：仅供内部复核的 Markdown 草稿。

专业 `workpaper.xlsx`、`report.docx`、`report.pdf` 和完整 `evidence-pack/` 是后续目标，不是 v0.1 现有输出。

## AI modes

命令行接受 `off`、`assist` 和 `deep` 三种模式。v0.1 的 Python 核心在三种模式下都不会自行调用外部大模型；`assist` 和 `deep` 只为支持 AI 的宿主 Agent 预留协作模式，且不会绕过映射、质量或专业复核门槛。保密或无模型环境请使用 `off`。

AI 可以协助理解资料、整理证据和草拟文字，但不得静默修改原始数据、计算公式、适用期间、正式规则或专业结论。

## Privacy and cloud authorization

v0.1 核心默认只在本地处理，不收集遥测，也不向 OpenTaxCheck 项目方上传资料。当前核心不调用 OCR、企业查询或大模型云服务。未来任何云端适配器都必须在调用前说明服务、拟发送的数据范围并取得明确授权；密钥只能放在环境变量或宿主安全凭证中。

真实项目应按机构制度进行最小化、脱敏、访问控制、备份和安全删除。日志、截图、Issue、Discussion、测试、提交记录和示例均不得包含真实客户数据或凭证。

## Rule coverage and limitations

v0.1 只包含用于验证架构的首批通用筛查逻辑，例如业务招待费扣除限额计算和少量确定性摘要关键词候选。它不构成中国企业税务风险的完整规则库，不覆盖所有税种、行业、地区口径、期间或事实情形。

“未触发”仅表示在已提供资料、已确认映射和当前规则版本下未命中，不表示没有税务风险。每条结果仍需结合适用年度的官方法规、企业事实、反向证据和专业判断复核。

## Contributing tax rules

欢迎提交可验证、可追溯的税务规则。每个规则提案必须说明税种、适用期间、纳税人类型、触发条件、排除条件、官方法规依据、计算公式、证据要求、常见误报、虚构测试输入、预期输出和专业复核边界。详见 [CONTRIBUTING.md](CONTRIBUTING.md) 和税务规则提案 Issue 表单。

## Community and tax-dispute collaboration

GitHub Issues 用于程序缺陷和结构化规则提案，GitHub Discussions 用于公开问答、方法讨论和路线交流。涉及具体客户的复杂税收争议，应另行建立经过确认的私密协作渠道，不在公开仓库讨论事实。

微信：PosterNote；添加时请备注 GitHub。请勿通过 GitHub Issue、Discussion 或微信发送未经脱敏的客户资料。

## Disclaimer

本项目输出是税务风险筛查和咨询辅助材料，不是审计、鉴证、法律意见、纳税申报或税务机关认定。使用前请完整阅读 [DISCLAIMER.md](DISCLAIMER.md)，并由具备相应经验的税务专业人员复核。

## License

代码和项目文档按 [Apache License 2.0](LICENSE) 提供。该许可证不自动授予项目名称、标识、发起人姓名或形象的商标、背书或专业服务许可。
