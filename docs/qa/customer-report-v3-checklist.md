# 客户报告 v3 验证记录

验证日期：2026-08-05

验证提交：`main@f881f07`

报告结构版本：`tax-health-report/v3`

## 验证结论

正式客户报告固定为 `税务健康检查报告.pdf`。本轮在 Windows 11、Python 3.12.13、Playwright 1.62.0、Chromium 149.0.7827.55 环境生成四类真实 PDF，并用 pypdf 6.14.2、pypdfium2 5.12.1 和逐页联系表完成结构与视觉检查。

本记录不把未执行的跨平台运行视为通过。Windows 已完成 Python 3.12.13 和 3.13 的真实 Chromium 验证；macOS、Linux 以及 Python 3.11 的结构测试和 Chromium smoke 由 CI 矩阵配置覆盖，但本地未实际执行，状态为待远端验证。

## Windows / Python 3.13 验证

在全新 Python 3.13 隔离环境中按 CI 口径安装 `.[excel,report]`，并对合并后的 `main@f881f07` 执行完整回归：

```text
936 passed, 21 skipped, 403 subtests passed in 457.20s
```

源码测试隔离环境继续完成 `--complete` 虚构案例，结果为 `completed_with_limitations`，试算平衡表与序时账勾稽通过，交付修订 33 的状态为 `verified`。生成的正式 PDF 为 976,102 字节，SHA-256 为 `1a7bbc571aa6fc0c41f4c51567478fa06ed9584dfb53a776503fa1d49b22d93a`。

对应命令如下；`$python313` 指向新建 Python 3.13 虚拟环境中的 `python.exe`，`$caseOutput` 指向不存在的新临时目录：

```powershell
& $python313 -m pip install --upgrade pip build pytest
& $python313 -m pip install -e '.[excel,report]'
$env:OPENTAXCHECK_CHROMIUM_EXECUTABLE='C:\Users\benhu\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe'
& $python313 -m pytest -q
& $python313 scripts\run_fictional_example.py --output $caseOutput --complete
& $python313 -m build
& $python313 scripts\validate_installed_wheel.py --dist dist
& $python313 scripts\validate_release.py --strict
```

Python 3.13 构建、隔离 wheel 安装和严格发行校验均通过：

- wheel：`open_tax_check-1.1.0-py3-none-any.whl`，SHA-256 `c77642b24faf836a162abf8f4baa58eeb2bc16a6fdd7882331cd7606d9eeabac`；
- sdist：`open_tax_check-1.1.0.tar.gz`，SHA-256 `5c52adf4804b5b5272821901e31b4aaa6bb6853976fde78f97b3858e7d6584e4`；
- `validate_installed_wheel.py` 另建临时环境；安装模块来自该环境的 `site-packages`，报告结构版本为 `tax-health-report/v3`，状态为 `verified`；
- `validate_release.py --strict` 返回 `release validation passed`。

## PDF 样本

所有样本使用完全虚构企业数据，检查期间为 2023-01-01 至 2026-12-31，快照修订号为 7，报告编号为 `THR-20230101-20261231-R0007`。

| 场景 | 页数 | SHA-256 |
| --- | ---: | --- |
| 普通报告 | 7 | `3fb516b005ed9c0deb8381d61c8fad60b72ee1ec20309b5ed5bfe69ecb41a9ea` |
| 长文本压力 | 29 | `800b3e6dcfbe4e240ec9316b2294f6781bba458dbc0d5b505469349db0501486` |
| 无重点风险 | 6 | `27b339b4d4e243806f38dd6ad4d2fc76149e0184941198835f817880ad104199` |
| 大量资料缺口 | 9 | `a70c51b59ecf59f3dca4db6a52d78a9ab75e3a7a7550e8007599538d22fee2cf` |

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
- macOS、Linux 和 Python 3.11 未在本机运行，不记录为通过。
- 本记录验证固定 HTML/Chromium/PDF 交付路径，不证明任意浏览器版本的像素级完全一致。
