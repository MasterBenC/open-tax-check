# 客户报告 v2 验证记录

验证日期：2026-08-03  
验证分支：`feat/customer-safety-report-v2`  
报告结构版本：`tax-health-report/v2`

## 结论

客户报告 v2 已通过自动化测试、DOCX 结构检查、完整虚构案例交付检查、构建和严格发布校验。当前环境没有可靠的 DOCX/PDF 渲染器，因此未执行逐页视觉验收，也不能确认老板主报告实际渲染后为 5–8 页。

证据状态：

- `automated_tests=passed`
- `docx_structural_qa=passed`
- `fictional_delivery_qa=passed_with_expected_partial_pdf`
- `build_and_strict_validation=passed`
- `visual_qa=not_performed_renderer_unavailable`
- `owner_page_length=not_verified_renderer_unavailable`

## 自动化测试

聚焦报告测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_view.py tests/test_tax_health_reporting.py tests/test_docx_report.py tests/test_delivery.py tests/test_fictional_example.py -q
```

结果：`55 passed, 2 skipped, 20 subtests passed`。跳过项属于既有可选环境能力。

完整测试集：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

结果：`883 passed, 20 skipped, 404 subtests passed in 117.72s`。

## 完整虚构案例

实际成功执行的命令：

```powershell
.\.venv\Scripts\python.exe scripts\run_fictional_example.py --output work\qa-report-v2 --complete
```

案例结果：

- 企业：虚构科技有限公司
- 案例状态：`completed_with_limitations`
- 账套借贷平衡：通过
- 序时账与科目余额表勾稽：通过
- 业务招待费规则试算：`15000.00`
- 交付状态：`partial`
- 交付修订号：`33`
- PDF 状态：`pdf_renderer_unavailable`

`partial` 是预期结果：当前环境没有可靠 PDF 渲染器，系统没有把缺少 PDF 错报为完整交付。

修订 `33` 的六类交付物均绑定同一快照修订号和 `tax-health-report/v2`：

- `markdown_report`
- `docx_report`
- `workpaper`
- `machine_snapshot`
- `customer_handoff`
- `evidence_bundle`

## DOCX 结构检查

自动化 OOXML 检查确认：

- DOCX ZIP 完整，`testzip()` 返回 `None`
- 包含 8 个要求的 OOXML 部件
- 包含封面、目录列表、页眉、页脚和 `PAGE` 页码域
- 专业附录前存在分页
- 包含 3 个固定宽度表格，表头可跨页重复
- 中文字体回退和命名样式存在
- 未声明外部关系
- 未加入总体健康分或完整度百分比

## 构建与严格校验

```powershell
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe scripts\validate_release.py --strict --require-distributions
```

结果：wheel 和 sdist 构建成功，严格发布校验通过。

- wheel：`244115` 字节
- sdist：`357938` 字节

## 尚待外部环境验证

由于本机没有 `soffice` 或 `libreoffice`，DOCX/PDF 逐页渲染失败于 `FileNotFoundError [WinError 2]`。以下项目保持待验证，不得描述为已通过：

- 中文字体替换后的实际效果
- 表格跨页、裁切、重叠、孤行标题和空白页
- 老板主报告是否稳定落在 5–8 个渲染页
- 不同 Office/WPS 版本的版式一致性

取得可靠渲染器后，应渲染全部页面并完成上述人工视觉检查，再更新本记录。
