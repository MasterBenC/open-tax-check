# 文档抽取合同

当前版本提供跨宿主交换的 `document-extraction/v1` JSON 合同，以及 `off` provider 和宿主结果导入/校验能力。实际 PDF、图片 OCR provider 尚未实现。

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

## 结果结构

结果包含文档元数据、provider 与授权信息、页码与旋转、文本块（四点坐标和 `[0,1]` 置信度）、表格单元格、机器可读问题及案件来源引用。合同版本独立于现有 `SCHEMA_VERSION` 和税务规则包版本。
