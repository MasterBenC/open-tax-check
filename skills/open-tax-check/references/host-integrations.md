# Agent 宿主接入契约

本契约对 Codex、Hermes、WorkBuddy、扣子、Qoder、Claude Code、OpenClaw 使用同一套业务字段。当前只提供宿主无关的操作顺序，不声明任何平台认证或原生插件兼容性；每个宿主都需按当前官方文档核验。

## 路线

- Python / CLI / JSON 核心：适用于本地命令能力，优先调用可恢复的案件接口 `run_case_self_check`、`case_status_payload`、`build_case_customer_tasks` 和 `apply_case_confirmations`；无案件目录的嵌入式调用仍可使用 `run_tax_health_check` 和 `continue_tax_health_check`。
- Agent Skills：把本 Skill 安装到宿主可调用的本地环境；升级 OpenTaxCheck 包即可获得新版规则包，不要求客户单独安装规则包。
- MCP / HTTP：本阶段只规定业务字段，不实现部署、认证、租户隔离或远程网关。

## 固定顺序

1. 验证 Python 3.11–3.13；安装 `open-tax-check[excel,report]`，再运行 `python -m playwright install chromium`。
2. 首次对话调用 `case-guide`，取得 `message_zh`、欢迎语和 `profile_questions`；第一次 `case-guide` 的 `document_groups` 可为空，不能当作动态资料清单。宿主不得自行改写开场或阶段状态。
3. 收齐五项企业信息后调用 `case-start`。
4. `case-start` 后必须再次调用 `case-guide`，读取第二次 `case-guide/v2` 的动态引导。机器字段 `scenario_id`、`fact_key`、`question_id`、`group_id`、`active_scenarios` 仅供宿主控制，不向客户直接展示；客户仅看到实际存在的中文字段：`message_zh`、`scenario_questions.question_zh`（如实际提供 `help_zh` 才展示）、`document_groups.title_zh`、`items_zh`、`applicability_note_zh`、`privacy_zh`、`disclaimer_zh` 等。第二次返回的 `document_groups` 按基础资料+已激活场景动态生成，数量不固定。宿主不得自行改写场景问题、资料组或阶段状态。
5. 为一个法律实体、一个地区和 2023–2026 检查期间构造 `TaxCheckRequest`。
6. 不提供模型服务，调用 `case-self-check` 生成可恢复案件快照。
7. 读取 `case-status` 和 `case-tasks`，只向客户展示实际存在的中文字段 `question_zh`、`help_zh`、`evidence_help_zh`。
8. 用带确认人、带时区时间戳和证据引用的结构化确认调用 `case-confirm`；不得传入或覆盖案件状态。
9. Python 只重跑受影响税种，并重新生成同一修订版的任务、脱敏快照和中文报告。
10. 只向客户输出中文《税务健康检查报告》PDF；HTML、工作底稿、快照和证据包仅用于内部审计，不提供 Word。
11. 升级 OpenTaxCheck 包以接收新版官方规则包。

CLI 可恢复案件运行 `opentaxcheck case-self-check --case CASE --request request.json`，随后使用 `case-status`、`case-tasks` 和 `case-confirm` 完成补件闭环。无案件目录的单次报告仍可运行 `opentaxcheck tax-health-check --request request.json --output-dir output`；需要显式加入全部参数化计算模板时可追加 `--include-parameterized-packs`。所有计算事实必须经客户确认并绑定证据。

## 客户状态呈现

以下六个英文内部值仅作为接口状态码；宿主只展示中文字段/文案，不直接展示代码：

| 接口状态码 | 客户中文文案 |
| --- | --- |
| `executed_clear` | 已执行且未见异常 |
| `executed_risk` | 已执行需关注 |
| `awaiting_evidence` | 待补资料 |
| `received_unparsed` | 已收到待解析 |
| `not_applicable` | 不适用 |
| `not_performed` | 未执行 |

待补资料、已收到待解析和未执行均不计入已执行或已通过。宿主不得把未解决状态改写为“无风险”或已完成。

## 统一案件交付

补件完成后，宿主调用 `opentaxcheck case-delivery --case CASE`。命令发布一个 `tax-delivery/v1` 修订目录，例如 `work/deliveries/revision-0001-report-v3/`，并通过 `work/delivery.json` 指向活动清单。正式客户交付物只有 PDF；内部 HTML、`workpaper.xlsx`、机器快照和证据包必须来自同一个 `snapshot_revision`，每个文件都带 SHA-256。

交付时必须读取并原样展示机器生成的 `tax-customer-handoff/v1`。宿主不得自行改写标题、限制、待补资料、试算金额免责声明或完成状态，也不得用自己的总结替代该交付说明。

PDF 是强制交付条件；渲染器、字体或结构校验失败时不得发布修订或更新活动指针，也不得回退 Word。相同快照和相同 `delivery_options` 可重复调用；选项变化返回 `delivery_revision_conflict`。历史 v2 修订只读保留，不得重新写入客户交接。

宿主 AI 智能体应优先调用自身的云端 OCR/视觉能力处理 PDF、图片和扫描件，再提交结构化提取合同。本地 OCR 仅在宿主没有视觉能力时作为可选后备。资料已上传但尚未解析时，状态必须是“已收到待处理”“提取不完整”或其他真实处理状态，不能写成“未提供”。

`customer-confirmation:<question_id>` 是宿主代客户声明的绑定标识，只表示回答已关联到具体问题，不等于文件证据、签名或事实真实性证明。宿主必须把该边界和中文免责声明一并展示。

## 证据包交付字段

Codex、Hermes、WorkBuddy、扣子、Qoder、Claude Code 和 OpenClaw 必须原样传递同一套四字段 JSON，不得增加客户事实或把未验证文件改写成已验证：

```json
{
  "evidence_bundle_status": "verified",
  "evidence_bundle_path": "output/税务健康检查证据包.zip",
  "evidence_bundle_sha256": "<64 位小写十六进制 SHA-256>",
  "includes_evidence_files": false
}
```

未启用证据包时，前三个字段均为 `null`，`includes_evidence_files` 为 `false`。默认证据包不复制客户原始证据；只有客户显式选择包含原件时，该字段才为 `true`。SHA-256 只验证文件身份和完整性，不证明客户陈述、申报正确性或不存在税务风险。

未经客户明确授权，任何宿主都不得上传包含原始证据文件的证据包。上传前还必须披露服务商、发送字段、保存期限和风险。证据包仅用于交付审计，不是独立完整重跑输入，也不是涉税鉴证或税务保证意见。

## 本地合同回放

维护者可以在无网络和无客户资料的环境中验证七宿主共同字段：

```text
python scripts/replay_host_integrations.py
```

该命令从 `tests/fixtures/host-replays/common-contract.json` 把同一 Skill 复制到七种不同的目标布局（`.codex/skills/`、`.hermes/skills/`、`.workbuddy/skills/`、`.coze/skills/`、`.qoder/skills/`、`.claude/skills/`、`.openclaw/skills/`），校验各自的 `SKILL.md` 发现标记，再重放中文任务字段和四字段交付 envelope。状态使用 `layout_validated`、`marker_validated` 和 `runtime_validation=pending`；它是 host-neutral 离线合同及目标布局校验，不调用真实宿主 CLI/provider，不表示安装发现或真实宿主运行已经完成。

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

《税务健康检查报告》仅供客户内部自查，不属于涉税鉴证、专业税务意见或纳税申报结论；不以税务专业人员复核、签字或盖章作为生成条件。

规则包由 OpenTaxCheck 版本统一携带，按税种、地区和期间选择；地方政策缺口必须继续展示，第三方规则包不会自动信任。

本阶段明确未实现：MCP/HTTP 部署、认证、租户隔离、平台认证和七个宿主的原生插件。
## Structured question handoff

The host Agent may call `build_agent_question_tasks(snapshot, max_questions=...)` and display only the Chinese presentation fields from each task. It must not invent confirmations. Convert a validated answer with `confirmation_from_task(task, value, confirmed_by=..., confirmed_at=..., evidence_refs=...)`, then pass the resulting confirmations to `continue_tax_health_check`. The Python core remains model-independent; no prompt text, token count, hidden reasoning, or provider metadata enters the business contract.

Before attaching a file reference to a confirmation or calculation, call `verify_evidence_file(root, relative_path, expected_sha256)`. A hash mismatch, unsafe path, reparse point, unreadable file, or size limit is a failed evidence check and must remain visible to the customer.

For the resumable `case-confirm` flow, `evidence_refs` must use a source ID present in the case `work/input_manifest.json` with a currently matching file hash, or the exact `customer-confirmation:<question_id>` reference for the question being answered. Unknown, mismatched, unsafe, or changed references are rejected as `unverified_confirmation_evidence`.
