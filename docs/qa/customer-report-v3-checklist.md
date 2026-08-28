# 客户报告 v3 验证记录

验证日期：2026-08-05

本地样本验证提交：`main@f881f07`

远端矩阵验证提交：`main@8c723ec`

报告结构版本：`tax-health-report/v3`

## 验证结论

正式客户报告固定为 `税务健康检查报告.pdf`。本轮在 Windows 11、Python 3.12.13、Playwright 1.62.0、Chromium 149.0.7827.55 环境生成四类真实 PDF，并用 pypdf 6.14.2、pypdfium2 5.12.1 和逐页联系表完成结构与视觉检查。

Windows 已完成 Python 3.12.13 和 3.13 的本地真实 Chromium 验证。GitHub Actions 随后在 Windows、macOS、Linux 与 Python 3.11–3.13 的九组矩阵中完成 Chromium 安装、完整测试、虚构案例、构建、隔离 wheel 安装和发行校验；真实 OCR smoke 也已通过。

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

## 跨平台 CI 验证

GitHub Actions 运行 [`30974537001`](https://github.com/MasterBenC/open-tax-check/actions/runs/30974537001) 验证提交 `8c723ec71cdb2a94c60406a7d914ae44f029ff20`，总状态为 `completed/success`。以下 10 个 job 均为 `success`：

- `ocr-smoke`；
- Ubuntu + Python 3.11、3.12、3.13；
- Windows + Python 3.11、3.12、3.13；
- macOS + Python 3.11、3.12、3.13。

每个系统/Python job 均成功执行依赖安装、`python -m playwright install chromium`、完整 `pytest`、虚构案例、构建、隔离 wheel 安装验证和要求发行包的严格校验。该证据证明 CI 所选运行器和 Chromium 版本上的运行兼容性，不等于任意未来系统或浏览器版本均像素级一致。

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

## 2026-08-05 高频账税交叉核对增量验收

当前功能分支 `feat/high-frequency-tax-cross-checks` 已在 Windows / Python 3.12 使用本机 Playwright Chromium 1228 完成以下增量验证：

- 管道、结构化勾稽、交付与报告组合回归：`150 passed, 3 skipped, 20 subtests passed`。三个环境相关跳过项单独记录，不作为通过。
- 客户报告、工作底稿、交付、HTML 与真实 PDF：`61 passed, 2 skipped`；HTML/PDF 表格分段回归：`12 passed`。
- 生成 `matched`、`tolerance`、`difference`、`many-missing` 四类真实 PDF；逐页转为 PNG 后检查共 27 页。未发现字体缺失、文字重叠、页边裁切、意外空白页或灰度不可辨识问题。
- 初次视觉检查发现长表续页未稳定重复表头；已改为固定分段表格，每段自带表头并避免段内分页。复渲染后的差异报告 8 页、资料缺口报告 7 页均通过逐页检查。
- 当前增量尚未在新的远端提交上运行 Windows、macOS、Linux × Python 3.11–3.13 矩阵；下方历史 CI 记录不能替代本分支的远端证据，推送前保持为待验证。

## 历史报告 v3 证据边界

- Playwright 1.62 对应的默认 Chromium 下载曾在本机因 CDN 连接重置而未完成；本地真实渲染因此使用已安装的 Playwright Chromium 1228 路径。GitHub Actions 的九组矩阵均已成功执行默认 Chromium 安装。
- macOS、Linux 和 Python 3.11 未在本机运行；其通过结论仅来自上述 GitHub Actions 远端证据。
- 本记录验证固定 HTML/Chromium/PDF 交付路径，不证明任意浏览器版本的像素级完全一致。

## 2026-08-26 视觉验收与 2026-08-28 最终复验

- 验证环境：Windows only，Python 3.12，`PYTHONPATH=src`，Playwright Chromium 路径为 `C:\Users\benhu\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe`；未声称 macOS/Linux 结果。
- focused 命令：设置上述 `PYTHONPATH` 和 `OPENTAXCHECK_CHROMIUM_EXECUTABLE` 后运行 `python -m pytest tests/test_scenario_map.py tests/test_customer_guide.py tests/test_customer_guide_cli.py tests/test_case_orchestration.py tests/test_delivery.py tests/test_report_view.py tests/test_tax_health_reporting.py tests/test_html_report.py tests/test_pdf_report.py tests/test_skill_contract.py tests/test_public_repo_contract.py -q`；最终结果为 **318 passed, 4 skipped, 110 subtests passed**。
- full 命令：在相同环境下运行 `python -m pytest -q -x`；最终结果为 **1201 passed, 23 skipped, 468 subtests passed**，终端摘要未报告 warning。跳过项单独记录，不计为通过。
- 构建与发布命令：依次运行 `python -m build`、`python scripts/validate_release.py --strict`、`python scripts/validate_installed_wheel.py --dist dist`；wheel/sdist 构建成功，strict release passed，isolated wheel 返回 `status=verified`、`report_schema_version=tax-health-report/v3`。
- 四类 PDF 均未加密、0 个空白页，且均存在场景风险地图：
  - ordinary：`.superpowers/sdd/2026-08-22-scenario-risk-map/visual-qa/ordinary/税务健康检查报告.pdf`，8 页，SHA-256 `c18af498e1c48031e5009fbbd9296cba1423575fd19513062d6869fb8eb529d8`
  - long-text：`.superpowers/sdd/2026-08-22-scenario-risk-map/visual-qa/long-text/税务健康检查报告.pdf`，9 页，SHA-256 `80f753c5617cb0b7d3cc8fad30e8631c1274a0674e41b1e9ac3e40e2a625daec`
  - no-risk：`.superpowers/sdd/2026-08-22-scenario-risk-map/visual-qa/no-risk/税务健康检查报告.pdf`，7 页，SHA-256 `4482f3451f76e3dff4bf75a21c355bb6f8e34336846545104672b9a99d004b16`
  - many-gaps：`.superpowers/sdd/2026-08-22-scenario-risk-map/visual-qa/many-gaps/税务健康检查报告.pdf`，11 页，SHA-256 `a141c5ddd06e79ef79165707feb7a713fe3d301fbb822934218b7e53e59a8b0e`
- 视觉检查：字体统一，无裁切或重叠；长文正确续页；表格跨页可读；灰度层次可辨。many-gaps 第 10 页留白较多但含检查边界，不是空白页。
