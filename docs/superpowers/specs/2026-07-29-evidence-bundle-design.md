# OpenTaxCheck 可验证证据包设计

**日期：** 2026-07-29  
**范围：** 《税务健康检查报告》的本地证据归档、哈希绑定与宿主 Agent 交付

## 1. 目标

OpenTaxCheck 已能生成中文 Markdown、DOCX、可选 PDF 和机器快照，也能校验单个本地证据文件。本阶段把这些能力串成一份可移交、可复核的证据包，使报告结论、计算结果、规则来源、客户确认和原始资料引用具有同一条可验证链路。

证据包默认不复制客户原始文件。只有操作者显式提供 `--include-evidence-files` 时，已通过路径、大小和 SHA-256 校验的原件才进入 ZIP。这个默认值用于降低客户敏感资料在复制、传输和备份过程中的暴露面。

本阶段不改变以下边界：

- AI 不得猜测客户事实、税率、地方政策、税目分类或正式税务结论；
- 借贷不平衡或勾稽失败的账套不得进入正常结论；
- 证据包不是涉税鉴证、专业税务意见或纳税申报结论；
- 未验证证据不得被标记为 verified，也不得生成名为“完整证据包”的假成功产物；
- 不实现公网存储、租户隔离、远程下载、电子签名、加密密钥托管或证据原件自动上传。

## 2. 操作接口

`tax-health-check` 增加以下参数：

```text
--evidence-manifest PATH
--evidence-root PATH
--include-evidence-files
```

`--evidence-manifest` 与 `--evidence-root` 必须同时提供。任一缺失时返回稳定错误，不运行证据包构建。`--include-evidence-files` 必须与前两者一起使用。

启用证据包后，成功结果增加：

```json
{
  "evidence_bundle_path": "output/税务健康检查证据包.zip",
  "evidence_bundle_sha256": "<64 位小写十六进制>",
  "evidence_bundle_status": "verified"
}
```

未启用证据包时，这三个字段为 `null`，保持现有调用兼容。

## 3. 输入清单

证据清单使用严格 JSON 合同 `tax-evidence-manifest/v1`：

```json
{
  "schema_version": "tax-evidence-manifest/v1",
  "items": [
    {
      "source_id": "vat-return-2026-01",
      "relative_path": "申报表/2026-01增值税申报表.pdf",
      "sha256": "<64 位小写十六进制>",
      "media_type": "application/pdf"
    }
  ]
}
```

约束如下：

- 顶层及条目均拒绝未知字段和缺失字段；
- `source_id` 非空、唯一，并与 `TaxFact.evidence_refs` 使用相同标识；
- `relative_path` 在同一清单内唯一，避免同一原件被重复计算或复制；
- `relative_path` 必须位于 `evidence_root` 内，拒绝绝对路径、`..`、符号链接和 Windows reparse point；
- `sha256` 必须为 64 位小写十六进制；
- `media_type` 必须是非空字符串，只作清单描述，不替代文件内容校验；
- 最多 500 个条目；
- 单个文件最多 50 MiB；
- 无论是否复制原件，清单内所有证据文件合计最多 512 MiB。

## 4. 验证与绑定

证据验证复用 `verify_evidence_file`，但在构建层增加集合级限制和绑定检查。

验证顺序：

1. 严格解析证据清单；
2. 验证条目数量、唯一 `source_id` 和路径安全；
3. 按条目校验文件存在性、大小和 SHA-256；
4. 收集当前请求中所有 `confirmed` 事实的 `evidence_refs`；
5. 收集当前快照中所有计算记录的 `evidence_refs`；
6. 要求上述引用全部指向清单内已验证条目；
7. 生成报告、机器快照和规则来源清单；
8. 对所有交付物计算 SHA-256；
9. 原子写入证据包，再计算证据包自身 SHA-256。

`observed`、`inferred` 或尚未确认的事实不能因证据文件存在而自动升级为 `confirmed`。证据文件验证只证明文件身份和完整性，不证明其中陈述真实，也不替客户作税务判断。

## 5. 包内结构

默认 ZIP：

```text
税务健康检查证据包.zip
├── bundle-manifest.json
├── tax-health-snapshot.json
├── 税务健康检查报告.md
├── 税务健康检查报告.docx        # 仅在本次请求生成 DOCX 时包含
└── 税务健康检查报告.pdf         # 仅在本次请求生成 PDF 时包含
```

追加 `--include-evidence-files` 后：

```text
└── evidence/
    ├── vat-return-2026-01/2026-01增值税申报表.pdf
    └── ...
```

ZIP 内路径不直接采用不受信任的 `source_id` 或原始目录。构建器生成安全目录名，并在 `bundle-manifest.json` 中保存安全路径与原始 `source_id` 的映射。

`bundle-manifest.json` 使用 `tax-evidence-bundle/v1`，至少包含：

- 报告期间、地区、快照修订号和 schema；
- 是否包含证据原件；
- 每个证据条目的 `source_id`、原相对路径、包内路径或 `null`、SHA-256、大小、media type、验证状态；
- 每个 confirmed 事实键与证据引用的绑定，不保存未确认事实值；
- 每个计算结果的税种、公式 ID、金额和证据引用；
- 规则包 ID、规则版本、官方来源标题、URL、发布日期和核验日期；
- Markdown、DOCX、PDF、机器快照的包内路径、SHA-256 和大小；
- 审计事件的公开字段；
- 固定免责声明和生成器版本。

证据包不复制原始请求 JSON，也不保存模型隐藏推理、凭据、令牌、直接联系方式或支付标识。它用于验证本次交付的证据绑定和文件完整性，不作为脱离原案件目录独立重跑全部筛查的输入；需要重跑时仍由宿主使用原案件中的结构化请求。

## 6. 可复现与原子写入

ZIP 使用固定时间戳、稳定排序、UTF-8 文件名和确定性 JSON。相同的快照、证据清单及文件内容应产生相同的包内成员字节。

证据包自身包含 ZIP 容器元数据，因此其 SHA-256 在文件完成后由调用方结果返回，不写回 ZIP 内部，避免自引用哈希。

写入流程使用同目录临时文件、flush、`fsync` 和 `os.replace`。验证或写入失败时不保留目标 ZIP，也不覆盖已有有效证据包。

## 7. 宿主 Agent 契约

Codex、Hermes、WorkBuddy、扣子、Qoder、Claude Code 和 OpenClaw 共用以下交付字段：

```json
{
  "evidence_bundle_status": "verified",
  "evidence_bundle_path": "<宿主可访问的本地路径>",
  "evidence_bundle_sha256": "<sha256>",
  "includes_evidence_files": false
}
```

宿主 Agent 可以把路径和哈希展示给客户，但不得声称哈希验证等于税务事实真实、申报正确或不存在风险。宿主不得在未获客户明确授权时把包含原件的证据包上传到云端。

## 8. 错误处理

CLI 使用稳定、非敏感的错误码：

- `invalid_evidence_manifest`：JSON、schema、字段、数量或唯一性无效；
- `unsafe_evidence_path`：路径越界、链接或 reparse point；
- `evidence_verification_failed`：文件不可读、超限或哈希不符；
- `unverified_evidence_reference`：confirmed 事实或计算引用未绑定至已验证条目；
- `evidence_bundle_size_limit_exceeded`：清单内证据超过总大小；
- `evidence_bundle_write_failed`：原子构建失败。

错误输出不回显客户文件内容、事实值、绝对原件路径或哈希以外的敏感信息。失败时返回非零退出码，不生成降级的“verified”包。

## 9. 测试与验收

实施采用 TDD，至少覆盖：

- 严格 schema、未知字段、重复 `source_id` 和数量上限；
- 路径越界、符号链接、reparse point、不可读文件、单文件与合计大小限制；
- SHA-256 匹配与不匹配；
- confirmed 事实和计算证据的完整绑定；
- 默认 ZIP 不含原件；
- 显式选项只复制已验证原件；
- 安全、稳定的包内路径；
- 确定性 JSON、固定 ZIP 时间戳和稳定成员排序；
- Markdown、DOCX、PDF、快照和规则来源的哈希；
- 失败不覆盖已有有效证据包；
- CLI 返回字段和错误码；
- 七类宿主 Agent 的共同字段契约；
- 公开文档明确默认隐私边界；
- 全量测试、真实 OCR 烟测、DOCX/PDF 页面级验收、构建和严格发布校验。

## 10. 完成标准

本阶段只有在以下条件同时满足时完成：

1. 默认模式不会复制客户原件；
2. 显式模式只复制已验证且受限的原件；
3. 所有 confirmed 事实和计算证据引用均可追溯；
4. 包内交付物和外部证据均有 SHA-256；
5. 失败不会留下或覆盖目标证据包；
6. 七类宿主可消费统一结果字段；
7. 文档、测试、构建、严格发布验证和真实虚构案例全部通过。
