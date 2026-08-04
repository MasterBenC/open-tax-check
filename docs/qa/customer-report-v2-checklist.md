# 客户报告 v2 验证记录

验证日期：2026-08-04
验证分支：`feat/all-tax-customer-integration`
报告结构版本：`tax-health-report/v2`

## 结论

客户报告 v2 已通过自动化测试、DOCX 结构检查、完整虚构案例交付检查、WPS 逐页视觉验收、构建、严格发布校验和隔离 wheel 安装验证。最终虚构案例共 7 页，其中客户主报告为前 5 页，专业附录为后 2 页。

证据状态：

- `automated_tests=passed`
- `docx_structural_qa=passed`
- `fictional_delivery_qa=passed_with_expected_partial_pdf`
- `build_and_strict_validation=passed`
- `installed_wheel_validation=passed`
- `visual_qa=passed_wps_12.1.0.28043`
- `owner_page_length=passed_5_pages`

## 自动化测试

聚焦报告测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_view.py tests/test_tax_health_reporting.py tests/test_docx_report.py tests/test_delivery.py tests/test_fictional_example.py -q
```

结果：报告相关测试通过；新增回归覆盖完整案例行动计划、受限状态标签、客户正文内容和明确分页。

完整测试集：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

结果：`921 passed, 21 skipped, 404 subtests passed in 133.50s`。跳过项属于既有可选环境能力。

## 完整虚构案例

实际成功执行的命令：

```powershell
.\.venv\Scripts\python.exe scripts\run_fictional_example.py --output work\qa-final-integrated-v5 --complete
```

案例结果：

- 企业：虚构科技有限公司
- 案例状态：`completed_with_limitations`
- 账套借贷平衡：通过
- 序时账与科目余额表勾稽：通过
- 业务招待费规则试算：`15000.00`
- 交付状态：`partial`
- 交付修订号：`33`
- 报告状态：`本次检查受限完成`
- 内置 PDF 状态：`pdf_renderer_unavailable`

`partial` 是预期结果：当前交付管线尚未把 WPS COM 认定为内置跨环境渲染器，系统没有把缺少内置 PDF 错报为完整交付。WPS 仅用于本轮 Windows 视觉验收。

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
- 包含 4 个固定宽度表格，表头可跨页重复
- 中文字体回退和命名样式存在
- 未声明外部关系
- 未加入总体健康分或完整度百分比

## 构建与严格校验

```powershell
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe scripts\validate_release.py --strict --require-distributions
```

结果：wheel 和 sdist 构建成功，严格发布校验通过。

- wheel 和 sdist 均低于严格校验规定的 `1 MiB` 单文件上限

隔离安装验证：

```powershell
.\.venv\Scripts\python.exe scripts\validate_installed_wheel.py --dist dist
```

结果：`status=verified`，Python `3.12`，模块从临时虚拟环境的 `site-packages` 加载，完整虚构案例状态为 `completed_with_limitations`。

## WPS 逐页视觉验收

使用 WPS Office `12.1.0.28043` 将最终 DOCX 导出为 PDF，再用 Poppler 以 120 DPI 转为 7 张 PNG；逐页人工检查全部页面。

- 第 1–5 页为客户主报告，符合 5–8 页要求
- 第 6–7 页为专业附录，跨页表头重复正常
- 中文文字和字体显示正常
- 未见文字重叠、裁切、表格破损、孤行标题、空白页或缺字
- 页眉、页脚和页码显示正常
- 封面与摘要均显示“本次检查受限完成”
- 摘要区分“经复核重点事项 0 项”和“待核对试算 6 项”
- 试算状态明确说明“不等于发现问题”，试算金额免责声明紧邻摘要
- 客户正文限量展示重点事项、资料缺口和行动，超出内容完整保留在专业附录
- 专业附录第 7 页包含全部行动清单

另生成最大预算压力案例，包含 4 个长重点事项、7 个长资料缺口、7 个长行动和 7 条长限制。WPS 渲染共 20 页；客户正文为第 1–8 页，专业附录从第 9 页开始。前 8 页逐页检查无重叠、裁切、断表或空白页，超长字段均显示有界摘要和“完整内容见专业附录”提示。

本次证据只证明当前 WPS 版本的渲染结果；不同 Office/WPS 版本的逐像素一致性未作承诺。
