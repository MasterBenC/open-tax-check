# 文档抽取合同

当前版本提供跨宿主交换的 `document-extraction/v1.1` JSON 合同（兼容导入 `v1`），以及 `off`、`host` 和可选的本地 `local-paddleocr` provider。PDF/PNG/JPEG 的本地离线提取已实现；联网 provider、复杂版式理解和联网企业数据尚未实现。

## 状态

- `not_requested`：没有请求抽取。
- `not_performed`：关闭 provider、未授权或依赖缺失。
- `completed`：抽取结果完整且通过合同校验。
- `partial`：部分页面或区域成功，剩余内容列在 `issues`。
- `failed`：安全、来源绑定或合同校验失败。

`not_performed`、`partial` 和 `failed` 不能解释为无风险或证据不存在。

## Provider

- `off`：不读取文档内容，明确返回 `not_performed`。
- `host`：导入宿主 Agent 已生成的结果，并重新绑定本地来源。
- `cloud`：仅定义授权和披露合同；当前没有网络调用实现。

## 授权

云端 provider 必须逐文件或逐页披露传输范围，并记录 provider、版本、授权主体、授权时间、缓存留存和撤回状态。未明确授权时不得发送原文件、页面、裁剪区域或文本。

## 宿主导入

宿主返回 JSON 后使用：

```text
opentaxcheck validate-extraction --input result.json
opentaxcheck import-extraction --case CASE_DIR --source SOURCE_ID --input result.json
```

导入会重新绑定案件内 `source_id`、文件名和媒体类型；宿主提供的这些字段不被信任。路径必须在案件 `input` 目录内，且拒绝符号链接、目录逃逸和超限 payload。

## 结构化财税资料合同

财务报表使用 `financial-statement/v1`，纳税申报表使用 `tax-return/v1`。两种合同都严格拒绝未知或缺失字段、限制 JSON/行/问题/来源锚点大小，并只接受 CNY 和受限十进制金额。行项目必须带来源定位、`confidence_source` 和 `confirmation_state`；宿主 AI 的结果只能是 `proposed` 或 `rejected`，不确定的宿主 AI 输出不得进入税务计算，也不能自行升级为 `confirmed`。

验证和案件导入命令为：

```text
opentaxcheck validate-structured-document --kind financial-statement|tax-return --input PATH
opentaxcheck import-structured-document --case CASE_DIR --source SOURCE_ID --kind financial-statement|tax-return --input PATH
```

案件导入只信任案件 `state.json` 和 `work/input_manifest.json` 中的实体、期间、文件名、媒体类型、来源编号和 SHA-256；读取前拒绝绝对路径逃逸、符号链接和 Windows reparse point，并重新计算来源文件 SHA-256。导入成功只表示合同结构和来源绑定通过，不证明申报正确、来源真实、税率适用或存在完整税务覆盖。模板解析和跨文档勾稽仍是后续边界。

## 结果结构

结果包含文档元数据、provider 与授权信息、页码与旋转、文本块（四点坐标和 `[0,1]` 置信度）、表格单元格、机器可读问题及案件来源引用。合同版本独立于现有 `SCHEMA_VERSION` 和税务规则包版本。
## Local OCR provider

`local-paddleocr` is an optional, offline provider for bounded PDF, PNG, and JPEG extraction. Install it with `pip install -e ".[ocr]"`, prepare a verified local model directory with `opentaxcheck prepare-ocr --model-dir PATH`, then run `opentaxcheck extract-document --provider local-paddleocr --model-dir PATH`. Extraction never downloads models; missing dependencies or an invalid manifest produce `not_performed`. The opt-in smoke gate is controlled by `OPEN_TAX_CHECK_RUN_OCR_SMOKE=1` and is explicitly skipped otherwise. The dedicated CI smoke may download PaddleOCR official mobile models before processing the fictional fixture; that download is isolated from case extraction and an opted-in runtime failure is reported as failed, never skipped.

Versioned calculation packs use schema `1.2.0` for customer-confirmed rate facts, schema `1.3.0` for audited statutory rate tables, or schema `1.4.0` for ordered statutory brackets and quick deductions. Every formula input must bind to a declared fact key and an official source. The CLI accepts external packs with repeatable `--pack PATH`; only confirmed, evidence-referenced facts can produce a calculated amount.

Audited parameterized examples are kept in `examples/calculation-packs/` for VAT, stamp tax, and deed tax. They deliberately require the applicable rate as a confirmed fact; the examples do not invent a rate or local preferential policy. Formal 2023–2026 stamp-tax schema `1.3.0` packs are bundled separately and select a statutory rate only from the customer's confirmed tax-item selector.
