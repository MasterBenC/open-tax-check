# 文档提取与结构化导入（OCR / extraction / structured import）

本文件是 `SKILL.md` 路由表中 `waiting_for_extraction` 状态的执行细节，覆盖 9 个真实存在但先前未在 skill 文档中出现的 CLI 子命令。命令入口与 `SKILL.md` 一致：`python <skill-dir>/scripts/opentaxcheck.py <子命令> ...`；已安装 `open-tax-check` 时也可直接用 `opentaxcheck <子命令>`。每条命令的参数、产物与退出码均从 `src/open_tax_check/cli.py` 的 argparse 定义与处理分支逐条读出，宿主不得自造参数。

## 退出码总览（本文件内）

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功（但 `import-extraction` 的 `status` 字段仍可能是 `failed`/`partial`，见下） |
| `2` | 提取结果本身无效，或导入失败（`validate-extraction` / `import-extraction`） |
| `3` | 本地 OCR 提取失败，或结构化文档/账户分类写入失败 |
| `4` | 输入、来源、kind、绑定不匹配或结构无效 |
| `5` | 本地 OCR 依赖或模型缺失（`local_ocr_dependency_missing` / `local_ocr_model_unavailable`） |
| `1` | 未捕获异常（仅 `set-scope` 的期间错误会走到这里） |

---

## 1. `set-scope` — 设定案件检查期间

```text
python <skill-dir>/scripts/opentaxcheck.py set-scope --case <case-dir> --period-start YYYY-MM-DD --period-end YYYY-MM-DD
```

- 用途：重设 `<case-dir>/state.json` 的 `review_period`，并把 `next_action` 置为 `add_source_files`。
- 必填参数：`--case`、`--period-start`、`--period-end`（三者都必填；起止日期必须是 `YYYY-MM-DD`，且起点不得晚于终点）。
- 前置：`<case-dir>/state.json` 必须已存在（即先 `init` 或 `case-start`）。
- 产物：改写 `state.json`；stdout 打印完整 `state.json` JSON。
- 退出码：成功 `0`；`incomplete_review_period`（只给一个端点）或 `invalid_review_period`（格式错 / 起点晚于终点）会以未捕获 `ValueError` 抛出，进程退出 `1`（打印 traceback）。`unsafe_case_path` 同样退出 `1`。

## 2. `prepare-ocr` — 准备并见证本地 OCR 模型

```text
python <skill-dir>/scripts/opentaxcheck.py prepare-ocr --model-dir <模型目录>
```

- 用途：对 `<模型目录>` 内的模型文件生成防篡改清单，供后续 `extract-document` 校验。**只验证目录里有模型文件，不安装、不校验 Python OCR 依赖**（依赖校验发生在 `extract-document` 阶段）。
- 必填参数：`--model-dir`。
- 行为细节：会先 `mkdir(parents=True, exist_ok=True)` 创建该目录；目录里除清单本身外没有任何文件时直接报错，不会生成空清单。
- 产物：`<模型目录>/open-tax-check-ocr-models.json`，schema `local-paddleocr/v1`，字段含 `provider`（`local-paddleocr`）、`provider_version`（`3.7`）、`model_dir`、`files`（每个文件的 `name`/`sha256`/`size_bytes`）与 `prepared_at`。
- 退出码：成功 `0`（打印清单 JSON，`sort_keys` 排序）；目录无模型文件或扫描失败时 `5`（`error_code=local_ocr_model_unavailable`）。

## 3. `extract-document` — 本地离线 OCR 提取单份资料

```text
python <skill-dir>/scripts/opentaxcheck.py extract-document --case <case-dir> --source <source_id> --provider local-paddleocr --model-dir <模型目录>
```

- 用途：对 `<case-dir>/input/` 下的单份 PDF/PNG/JPEG 做本地离线 OCR，产出结构化提取结果。
- 必填参数：`--case`、`--source`（必须已出现在 `work/workbook_inventory.json` 的 `sources` 中）、`--provider`（当前只有 `local-paddleocr`）、`--model-dir`（须先 `prepare-ocr` 且清单通过校验）。
- 前置：`work/workbook_inventory.json` 存在且含该 `source_id`；对应文件在 `<case-dir>/input/` 下、扩展名为 `.pdf`/`.png`/`.jpg`/`.jpeg`；`--model-dir` 清单有效；OCR 运行库（`pypdf`、`pypdfium2`、`PIL`、`paddleocr`、`paddle`）已安装。
- 产物：`work/document-extractions/<source_id>.json`，schema `document-extraction/v1.1`，含 `status`（`completed`/`partial`/`failed`/`not_performed`）、`pages`、`blocks`、`issues` 等。
- 退出码：
  - `0`：`status` 为 `completed` 或 `partial`；
  - `3`：`status` 为 `failed`，或捕获到 `local_ocr_failed`；
  - `4`：`unsafe_source_path`、`unsupported_document_media_type`、`invalid_document_source`；
  - `5`：`local_ocr_dependency_missing` 或 `local_ocr_model_unavailable`（含 `not_performed` 且 issue 码命中这两个）。

## 4. `validate-extraction` — 校验一份提取结果 JSON

```text
python <skill-dir>/scripts/opentaxcheck.py validate-extraction --input <extraction.json>
```

- 用途：在导入前独立校验一份提取结果是否符合 `document-extraction` schema。
- 必填参数：`--input`。
- 接受 schema：`document-extraction/v1.1` 与旧版 `document-extraction/v1`。
- 退出码：`0` 合法（打印 `valid:true` 与 `schema_version`）；`2` 非法（`error_code=invalid_document_extraction`，含文件不存在、JSON 解析失败、schema 版本不支持、字段非法等所有情况）。

## 5. `import-extraction` — 把宿主/外部提取结果导入案件

```text
python <skill-dir>/scripts/opentaxcheck.py import-extraction --case <case-dir> --source <source_id> --input <extraction.json>
```

- 用途：把宿主自带的 OCR/视觉能力产出的提取结果（或经 `validate-extraction` 通过的 JSON）导入案件。
- 必填参数：`--case`、`--source`（必须已在 `work/workbook_inventory.json` 中）、`--input`。
- 产物：`work/document-extractions/<source_id>.json`。
- 退出码：任何失败统一 `2`（`error_code=invalid_document_extraction_import`）。**注意**：只要写入成功就返回 `0`，但 stdout 的 `status` 字段可能是 `completed`/`partial`/`failed`/`not_performed`——宿主必须读 `status` 字段，不能只看退出码就断定提取成功。

## 6. `validate-structured-document` — 校验结构化财务/申报文档

```text
python <skill-dir>/scripts/opentaxcheck.py validate-structured-document --kind <financial-statement|tax-return> --input <doc.json>
```

- 用途：在导入前独立校验一份结构化文档 JSON。
- 必填参数：`--kind`（仅 `financial-statement` 或 `tax-return`）、`--input`（≤ 20 MB）。
- 接受 schema：`financial-statement/v1` 或 `tax-return/v1`。
- 退出码：`0` 合法（打印 `valid:true`、`kind`、`schema_version`）；`4` 非法（`invalid_kind` 或 `invalid_structured_document`）。

## 7. `import-structured-document` — 导入结构化财务/申报文档

```text
python <skill-dir>/scripts/opentaxcheck.py import-structured-document --case <case-dir> --source <source_id> --kind <financial-statement|tax-return> --input <doc.json>
```

- 用途：把结构化财务报表/纳税申报表绑定到来源，供勾稽与交叉核对使用。
- 必填参数：`--case`、`--source`（必须已在 `work/input_manifest.json` 中）、`--kind`、`--input`。
- 前置：`work/input_manifest.json` 与 `state.json` 均存在；文档内 `entity_name` 与 `state.json` 的 `entity_name` 一致，文档期间必须落在 `review_period` 之内。
- 产物：`work/structured-documents/<16位hex>.<fs|tr>.json`（`financial-statement` 用 `.fs.json`，`tax-return` 用 `.tr.json`），并更新索引 `work/structured-document-index.json`（schema `structured-document-index/v1`）。
- 退出码：
  - `0`：`status=completed`（打印 `output_path`）；
  - `4`：`invalid_kind`、`invalid_source`、`entity_mismatch`、`period_mismatch`、`invalid_structured_document`；
  - `3`：`write_failed`。

## 8. `confirm-cross-check` — 确认一条带证据的交叉核对解释

```text
python <skill-dir>/scripts/opentaxcheck.py confirm-cross-check --case <case-dir> --input <confirmation.json>
```

- 用途：对 `work/tax-cross-checks.json` / `case-tasks` 中出现的单条交叉核对差异，确认其解释。
- 必填参数：`--case`、`--input`（一个 JSON 对象，恰好 9 个字段）。
- 输入字段（缺一或多余即 `invalid_cross_check_confirmation`）：`question_id`、`check_id`、`source_manifest_sha256`、`category`、`explanation_zh`、`confirmed_by`、`confirmed_at`、`evidence_refs`、`bridge`。其中 `category` 只能是 8 种解释类目之一：`unbilled_revenue`、`deemed_sale`、`non_taxable_or_exempt_revenue`、`tax_inclusive_conversion`、`timing_difference`、`amended_return`、`book_adjustment`、`other`。
- 产物：stdout 打印 `tax_cross_check_payload`（含复核后交叉核对结果）。
- 退出码：`0` 成功；`4` 失败，`error_code` 可为 `invalid_cross_check_confirmation`、`cross_check_confirmation_binding_mismatch`、`cross_check_confirmation_conflict`、`cross_check_confirmation_locked`、`cross_check_confirmation_write_failed`、`unverified_cross_check_evidence`、`cross_check_rerun_inputs_invalid`、`unsafe_case_path`；不在上述集合内的错误统一降级为 `cross_check_confirmation_failed`（仍是 `4`）。

## 9. `confirm-account-classification` — 确认来源绑定账户分类

```text
python <skill-dir>/scripts/opentaxcheck.py confirm-account-classification --case <case-dir> --input <classification.json>
```

- 用途：确认序时账科目到四类受控类别的分类，供 `BOOK-FS-REV-001` 等交叉核对把科目金额聚合到对应类别。
- 必填参数：`--case`、`--input`。
- 输入 schema：`account-classification/v1`，恰好 6 个字段：`schema_version`、`source_manifest_sha256`、`mapping_fingerprint`、`reviewer`、`confirmed_at`、`classifications`。每个 `classification` 恰好 3 字段：`account_code`、`account_name`、`category`；`category` 只能是 `main_business_revenue`、`other_business_revenue`、`vat_output_tax`、`business_entertainment` 之一。
- 关键约束：`source_manifest_sha256` 必须是 `work/input_manifest.json` 的 SHA-256，`mapping_fingerprint` 必须等于当前生效账簿映射的指纹（`reviews.effective_journal_mapping_fingerprint`），二者任一不匹配返回 `account_classification_binding_mismatch`。`reviewer` 必须以 `human:` 开头且通过非 AI 身份校验；`confirmed_at` 必须是带时区的 ISO-8601 时间戳。
- 产物：`reviews/account_classification.json`（原子写入）。
- 退出码：`0` 成功（`status=account_classification_confirmed`）；`3` 失败，`status=needs_account_classification_review`，`error_type` 可为 `invalid_account_classification_file`、`invalid_account_classification`、`account_classification_binding_mismatch`、`unsafe_review_path`、`confirm_account_classification_failed`。

---

## 状态推进

`case-status` 返回的 `next_action` 中，`complete_document_extraction` 对应「有资料已上传但尚未解析」。宿主按资料形态二选一：

- 宿主有自身 OCR/视觉能力：宿主自己提取，产出一个符合 `document-extraction/v1.1`（或 `v1`）的 JSON，先 `validate-extraction` 校验，再 `import-extraction` 导入。
- 宿主没有视觉能力：先 `prepare-ocr` 准备模型目录，再 `extract-document` 本地提取。

结构化财务报表/申报表（`financial-statement`/`tax-return`）走 `validate-structured-document` → `import-structured-document`，不进 `import-extraction`。两类导入都用 `source_id` 绑定来源，`import-extraction` 依据 `work/workbook_inventory.json`，`import-structured-document` 依据 `work/input_manifest.json`。

本地离线 OCR 只是宿主没有视觉能力时的可选后备；联网企业数据与复杂版式理解尚未实现。
