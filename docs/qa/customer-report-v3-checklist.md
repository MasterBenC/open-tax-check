# 客户报告 v3 验证记录

验证日期：2026-08-05  
验证分支：`feat/html-pdf-customer-report`  
报告结构版本：`tax-health-report/v3`

## 验证结论

正式客户报告固定为 `税务健康检查报告.pdf`。本轮在 Windows 11、Python 3.12.13、Playwright 1.62.0、Chromium 149.0.7827.55 环境生成四类真实 PDF，并用 pypdf 6.14.2、pypdfium2 5.12.1 和逐页联系表完成结构与视觉检查。

本记录不把未执行的跨平台运行视为通过。Windows 已完成真实 Chromium 验证；macOS、Linux 以及 Python 3.11、3.13 的结构测试和 Chromium smoke 由 CI 矩阵配置覆盖，但本地未实际执行，状态为待远端验证。

## PDF 样本

所有样本使用完全虚构企业数据，检查期间为 2023-01-01 至 2026-12-31，快照修订号为 7，报告编号为 `THR-20230101-20261231-R0007`。

| 场景 | 页数 | SHA-256 |
| --- | ---: | --- |
| 普通报告 | 7 | `3fb516b005ed9c0deb8381d61c8fad60b72ee1ec20309b5ed5bfe69ecb41a9ea` |
| 长文本压力 | 29 | `800b3e6dcfbe4e240ec9316b2294f6781bba458dbc0d5b505469349db0501486` |
| 无重点风险 | 6 | `27b339b4d4e243806f38dd6ad4d2fc76149e0184941198835f817880ad104199` |
| 大量资料缺口 | 9 | `540d2509ed81746aef332bd13f1b2b44c230b2470f5a6e7481842b36c24e3a3c` |

生成环境变量：

```powershell
$env:PYTHONPATH='src'
$env:OPENTAXCHECK_CHROMIUM_EXECUTABLE='C:\Users\benhu\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe'
```

实际生成命令（本地忽略目录中的 QA harness）：

```powershell
python work\customer-report-v3-qa\generate_qa.py ordinary
python work\customer-report-v3-qa\generate_qa.py long-text
python work\customer-report-v3-qa\generate_qa.py no-risk
python work\customer-report-v3-qa\generate_qa.py many-gaps
python work\customer-report-v3-qa\contacts.py
```

目录页码的自动化契约由真实 Chromium 测试执行：

```powershell
python -m pytest tests/test_pdf_report.py::test_real_chromium_pdf_is_valid_and_contains_customer_identity -q
```

该测试读取目录页的 8 个显示页码，并逐项比对 8 个 PDF 内部链接的命名目标页。四类 QA 样本另以相同 pypdf 检查逻辑逐份复核，结果均为 `toc=8/8`、`links=8/8`、`blank_pages=0`。

## 结构检查

- 四份 PDF 均未加密，可由严格模式 `pypdf.PdfReader` 打开并提取中文标题、企业名称、报告编号和正文。
- 每份 PDF 均包含封面、目录及八个主要章节的书签层级。
- 每份目录包含 8 个内部链接；显示页码逐项等于链接目标页，长文本报告的后段章节页码能够正确到达第 25、27、29 页。
- 目录不再出现列表标记与正文编号重复的 `1. 1.` 样式。
- 所有页面均包含有效正文，无意外空白页；PDF 页数与 144 DPI PNG 页数一致。
- 渲染过程阻断非 `about:`、`data:`、`blob:` 请求，报告字体由内嵌 `NotoSansSC-VF.ttf` 提供。

## 逐页视觉检查

四类 PDF 的每一页均转为 PNG，并按每张六页生成联系表检查：

- 中文字体一致，无缺字、乱码或系统回退字体痕迹。
- 无文字重叠、页边裁切、跨页表格破损或孤立标题。
- 普通、无重点风险和大量资料缺口报告的表格边界、表头重复和换行清晰。
- 29 页长文本报告连续分页稳定，风险段落在跨页后保持阅读顺序。
- 封面、页眉、页脚、当前页码和总页数显示正常。
- 高、中、提示和资料状态同时使用文字、边框与灰度层次，黑白打印仍可辨识。
- 普通报告末页存在较多留白，但包含附录和报告修订信息，不属于空白页。

## 证据边界

- Playwright 1.62 对应的默认 Chromium 下载曾因 CDN 连接重置而未完成；本轮真实渲染使用已安装的 Playwright Chromium 1228 路径。该限制不影响本轮 Windows 样本结论，但默认浏览器安装仍需在干净环境和 CI 中继续验证。
- macOS、Linux 和 Python 3.11、3.13 未在本机运行，不记录为通过。
- 本记录验证固定 HTML/Chromium/PDF 交付路径，不证明任意浏览器版本的像素级完全一致。
