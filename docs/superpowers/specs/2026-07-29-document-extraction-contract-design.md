# OpenTaxCheck 文档抽取/OCR统一合同设计

**日期：** 2026-07-29  
**状态：** 已获用户确认，待编制实施计划  
**范围：** Phase 3 第一块：PDF、扫描 PDF 与图片的 provider-neutral 文档抽取合同

## 1. 目标与非目标

本阶段先建立数字 PDF、扫描 PDF 和常见图片进入税务健康检查流程时使用的统一结果合同。合同必须能被 Codex、Hermes、Qoder、Claude Code、OpenClaw、WorkBuddy 和扣子等宿主交换，同时保持案件内来源绑定、页级证据定位和明确授权边界。

本阶段不接入 PaddleOCR、百度 OCR、其他云服务、网络 SDK 或重量级 OCR 依赖，也不修改现有 Excel 解析、税种筛查和报告结论逻辑。第一版只实现模型、严格解析/序列化、`off`/`host` 合同实现和授权检查。

## 2. 架构与数据流

统一数据流为：

`案件输入文件 → 文档元数据 → 抽取 provider → DocumentExtraction → 证据引用/人工复核`

### 2.1 核心模型

使用不可变、可 JSON 序列化的 Python 模型，并在跨宿主边界暴露版本化 JSON。第一版包含：

- `DocumentExtraction`：文档级结果、provider 信息、授权状态、页、块、表格和问题列表。
- `DocumentPage`：页码、宽高、单位和旋转信息。
- `TextBlock`：文本、页码、四点坐标、置信度和语言。
- `TableRegion` 与 `TableCell`：表格区域、单元格文本、坐标、置信度和行列索引。
- `ExtractionIssue`：机器可读的代码、严重性、消息和页/区域定位。
- `AuthorizationRecord`：provider、版本、传输范围、授权主体/时间/范围、缓存留存和撤回状态。

`DocumentExtraction` 至少包含：`schema_version`、`document_id`、`source_id`、文件名、媒体类型、provider 与版本、执行时间、授权状态、执行状态、`pages`、`blocks`、`tables`、`issues` 和 `source_refs`。

### 2.2 执行状态

合同区分以下状态：

- `not_requested`：没有请求抽取。
- `not_performed`：provider 为 `off`、未授权或依赖缺失。
- `completed`：抽取完成并通过合同校验。
- `partial`：部分页面/区域成功，剩余内容由 `issues` 明确列出。
- `failed`：安全检查、来源绑定或 provider 执行失败。

`not_performed`、`partial` 和 `failed` 不能被下游解释为“没有发现问题”或“证据不存在”。它们继续沿用现有 `present_unparsed`、证据缺口和审计边界。

## 3. Provider 与授权边界

定义最小 provider 协议：输入案件内 `source_id`、受控文件路径、媒体类型和抽取选项；输出一个经过严格验证的 `DocumentExtraction`。每个 provider 声明稳定 ID、版本、能力、是否需要网络和是否会传输原文。

### 3.1 内置 provider 类型

1. `off`：不读取文档内容，返回 `not_performed` 及原因。
2. `host`：接收宿主 Agent 已生成的抽取 JSON，只负责严格校验、安全归一化和本地来源绑定；不信任宿主提供的 `source_id`、文件名或路径。
3. `cloud`：只定义授权检查和数据披露结构，第一版不提供百度或其他云实现。没有逐文件/逐页明确授权时返回 `not_performed`。

### 3.2 授权记录

授权必须记录 provider 名称/版本、传输范围（原文件、指定页、裁剪区域或仅文本）、授权主体、授权时间、授权范围、缓存/留存许可和撤回状态。撤回后禁止继续发送。

### 3.3 安全失败规则

- 路径必须位于案件输入目录内，拒绝符号链接、重解析点和目录逃逸。
- 页码、坐标、置信度、文本长度、块数量、表格数量和总 payload 均受上限约束。
- provider 返回的 `source_id` 必须与本地案件来源重新绑定。
- 越过授权边界、来源绑定失败或合同校验失败时只能返回 `failed`，不能伪装为空结果。

## 4. 集成与兼容策略

新增合同使用独立版本号，例如 `document-extraction/v1`，不修改现有 `SCHEMA_VERSION` 和税务规则包版本。没有抽取结果的旧案件保持原行为。

CLI 第一版只提供合同校验和结果导入能力，帮助宿主 Agent 交付统一 JSON，不宣称已经支持 OCR。公开文档必须明确“合同已实现、实际 OCR provider 尚未实现”。

## 5. 测试与验收

测试必须覆盖：

- 数字 PDF、扫描 PDF、图片的合法元数据合同样例。
- 页码、旋转、坐标、置信度、文本长度和表格结构边界。
- `off`、未授权 `cloud`、授权范围不匹配和来源绑定错误。
- 宿主伪造 `source_id`、越界路径、重复页码和超限 payload。
- `partial`/`failed` 不得变成无风险或证据不存在。
- JSON 往返稳定性与未知字段拒绝策略。
- Windows、macOS、Linux 上无外部服务、无重量依赖运行。

验收条件是：模型和 JSON 合同可独立导入/导出；所有失败状态可审计；案件来源绑定不可由宿主覆盖；现有全量测试保持通过；仓库文档不再暗示实际 OCR 已可用。

## 6. 后续实施边界

本设计批准后，下一步只编制实施计划。实施计划应先落地模型和严格解析，再落地 `off`/`host` 合同与授权检查，最后补充文档、CLI 和测试。实际本地 OCR、授权云 OCR 和版本化税务规则包分别进入后续独立设计/计划。
