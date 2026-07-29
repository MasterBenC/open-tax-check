# Agent 宿主接入契约

本契约对 Codex、Hermes、WorkBuddy、扣子、Qoder、Claude Code、OpenClaw 使用同一套业务字段。当前只提供宿主无关的操作顺序，不声明任何平台认证或原生插件兼容性；每个宿主都需按当前官方文档核验。

## 路线

- Python / CLI / JSON 核心：适用于本地命令能力，调用 `run_tax_health_check`、`continue_tax_health_check`、`tax_snapshot_payload` 和 `render_tax_health_report`。
- Agent Skills：把本 Skill 安装到宿主可调用的本地环境；升级 OpenTaxCheck 包即可获得新版规则包，不要求客户单独安装规则包。
- MCP / HTTP：本阶段只规定业务字段，不实现部署、认证、租户隔离或远程网关。

## 固定顺序

1. 验证 Python 3.11–3.13；需要工作簿时安装带 Excel extra 的 OpenTaxCheck。
2. 为一个法律实体、一个地区和 2023–2026 检查期间构造 `TaxCheckRequest`。
3. 不提供模型服务，直接调用 `run_tax_health_check`。
4. 只向客户展示 `question_zh`、`help_zh`、`evidence_help_zh`。
5. 用带确认人和时间戳的 `Confirmation` 提交客户答案。
6. 调用 `continue_tax_health_check`，只重跑受影响税种。
7. 输出中文报告，并保留机器 payload 用于审计。
8. 升级 OpenTaxCheck 包以接收新版官方规则包。

## 能力矩阵

| 宿主 | 首选路线 | 说明 |
| --- | --- | --- |
| Codex | Agent Skills | 需按当前官方文档核验 |
| Hermes | Agent Skills | 需按当前官方文档核验 |
| WorkBuddy | Agent Skills / MCP / HTTP | 能力取决于宿主配置；需按当前官方文档核验 |
| 扣子 | MCP / HTTP | 本阶段不提供网关实现；需按当前官方文档核验 |
| Qoder | Agent Skills | 需按当前官方文档核验 |
| Claude Code | Agent Skills | 需按当前官方文档核验 |
| OpenClaw | Agent Skills | 需按当前官方文档核验 |

客户面对的结果和问题必须是中文。远程传输前必须披露服务商、发送字段、保存期限和风险并取得明确授权。永远不要把未解决状态改写成“无风险”，不要替客户编造确认，也不要把机器状态码当成客户文案。

规则包由 OpenTaxCheck 版本统一携带，按税种、地区和期间选择；地方政策缺口必须继续展示，第三方规则包不会自动信任。

本阶段明确未实现：MCP/HTTP 部署、认证、租户隔离、平台认证和七个宿主的原生插件。
## Structured question handoff

The host Agent may call `build_agent_question_tasks(snapshot, max_questions=...)` and display only the Chinese presentation fields from each task. It must not invent confirmations. Convert a validated answer with `confirmation_from_task(task, value, confirmed_by=..., confirmed_at=..., evidence_refs=...)`, then pass the resulting confirmations to `continue_tax_health_check`. The Python core remains model-independent; no prompt text, token count, hidden reasoning, or provider metadata enters the business contract.

Before attaching a file reference to a confirmation or calculation, call `verify_evidence_file(root, relative_path, expected_sha256)`. A hash mismatch, unsafe path, reparse point, unreadable file, or size limit is a failed evidence check and must remain visible to the customer.
