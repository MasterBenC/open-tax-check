# OpenTaxCheck 本地 PDF/图片 OCR 设计

**日期：** 2026-07-29  
**状态：** 已获用户确认，待编制实施计划  
**范围：** Phase 3 本地数字 PDF 提取、扫描页/图片 OCR 和显式模型预热

## 1. 目标与非目标

本阶段在既有 provider-neutral `DocumentExtraction` 合同上实现真正可运行的本地文档抽取。数字 PDF 优先读取原生文本；扫描 PDF 和 PNG/JPEG 使用 PaddleOCR。全部处理默认离线，不向 OpenTaxCheck 项目方或第三方发送客户资料。

本阶段不实现百度或其他云 OCR，不实现复杂表格语义、印章识别、合同条款推理、税额判断或自动税务结论。PP-Structure/复杂版式和云端增强分别进入后续独立阶段。

## 2. 技术栈与许可证

不采用 PyMuPDF。其 2026-07-29 PyPI 元数据显示为 AGPL-3.0 或商业许可，不适合作为 Apache-2.0 项目的默认可选依赖。

新增 `open-tax-check[ocr]` 可选依赖：

- `pypdf>=6.14.2,<7`：数字 PDF 原生文本提取；
- `pypdfium2>=5.12.1,<6`：PDF 页面渲染；
- `Pillow>=12.3,<13`：受限图片读取和像素校验；
- `paddleocr>=3.7,<4`：中文 OCR pipeline；
- `paddlepaddle>=3.3.1,<4`：默认 CPU inference engine。

核心安装继续保持零运行时依赖。上述版本区间必须通过 Python 3.11、3.12、3.13 和目标平台安装验证后才能进入发布构建；如果上游 wheel 缺失，文档和 CI 必须明确缩小该 OCR extra 的平台范围，不能把未执行描述为通过。

## 3. 架构与路由

新增 `LocalOcrProvider`，稳定 provider ID 为 `local-paddleocr`。数据流为：

`LocalDocumentSource -> 安全/预算检查 -> 按媒体类型路由 -> 按页提取或 OCR -> DocumentExtraction`

### 3.1 数字 PDF

使用 `pypdf` 按页检查内容流预算并提取原生文本。页面存在达到最低有效字符数的原生文本时，生成 `native-pdf` 文本块，不再 OCR。原生文本提取不伪造模型置信度。

### 3.2 扫描与混合 PDF

原生文本不足的页面由 `pypdfium2` 以固定、受限 DPI 渲染，再交给 PaddleOCR。混合 PDF 逐页决策；成功页面和失败页面合并为一个结果。至少一页成功且至少一页失败时状态为 `partial`，全部失败才是 `failed`。

### 3.3 图片

只接受单帧 PNG、JPEG。图片先由 Pillow 检查格式、尺寸、总像素和解压炸弹风险，再交给 PaddleOCR。动画、多帧、损坏或不在允许列表内的图片 fail closed。

## 4. 合同演进

本地 OCR 输出使用 `document-extraction/v1.1`。解析器继续接受现有 `document-extraction/v1`，但新序列化默认输出 v1.1。

`TextBlock` 和 `TableCell` 的 `confidence` 调整为 `float | None`，并新增 `confidence_source`：

- `model`：PaddleOCR 返回的 `[0,1]` 分数；
- `native_unscored`：PDF 原生文本，没有模型分数，`confidence` 必须为 `None`。

v1 导入结果缺少 `confidence_source` 时按既有非空置信度归一化为 `model`。provider 元数据必须区分文档级 provider 与块级 extraction method。

## 5. 模型与联网边界

案件运行不得静默下载模型。新增：

- `opentaxcheck prepare-ocr --model-dir PATH`：显式初始化模型，输出模型 ID、版本、文件清单和 SHA-256；
- `opentaxcheck extract-document --case CASE --source SOURCE_ID --provider local-paddleocr --model-dir PATH`：只使用已准备模型。

模型目录缺失、不完整或哈希不符时返回 `not_performed / local_ocr_model_unavailable`。日志不保存客户原文，只记录模型、版本、哈希、页级状态和机器可读问题。默认 CPU、受控线程数，不自动启用 GPU、远程模型代码或云端 inference engine。

## 6. 资源与安全预算

第一版固定预算：

- 单文件最大 50 MiB；
- PDF 最大 500 页；
- 单页解压内容流最大 32 MiB；
- 单张输入图片最大 50,000,000 像素；
- PDF 渲染固定 200 DPI，单页最大 50,000,000 像素；
- 单文档总渲染像素最大 500,000,000；
- 沿用合同的 100,000 文本块、单块 1 MiB 和总 JSON 20 MiB 上限。

继续复用案件目录、符号链接、Windows 重解析点和目录逃逸防护。拒绝加密 PDF、损坏 PDF、动画/多帧图片及未知格式。任何超限均生成稳定 issue code，不回显原文。

## 7. 状态与问题

页级 issue 至少包括：`pdf_content_stream_too_large`、`pdf_page_render_failed`、`ocr_page_failed`、`page_has_no_text`、`image_pixel_limit_exceeded`、`encrypted_pdf`、`local_ocr_dependency_missing` 和 `local_ocr_model_unavailable`。

未安装 `[ocr]` 或模型未准备时为 `not_performed`。来源不安全、文档损坏、加密或整体超限为 `failed`。页面部分失败为 `partial`。这些状态不能转成无风险或证据不存在。

## 8. 测试与发布验收

测试分层：

1. 核心测试不安装 OCR 依赖，验证依赖缺失降级、授权、路径、预算和状态传播；
2. 使用伪后端验证 PDF 按页路由、原生文本优先、混合结果合并和 v1/v1.1 往返；
3. OCR smoke 使用完全虚构的中文 PNG 和数字 PDF，验证真实非空文本、页码和坐标；
4. wheel/sdist 校验不携带模型、客户资料或未声明二进制；
5. Windows、Ubuntu、macOS 普通测试矩阵保持绿色；OCR smoke 只在上游 wheel 已验证的平台/Python 组合运行并明确标注范围。

验收要求：无 OCR extra 时核心行为不变；已预热模型时 PNG/JPEG、数字 PDF、扫描 PDF 和混合 PDF 都可生成可审计合同；所有预算和失败状态有自动测试；公开文档准确描述安装体积、首次预热、平台范围和未实现的云/复杂版式能力。

## 9. 官方依据

- PaddleOCR 安装文档：https://www.paddleocr.ai/latest/en/version3.x/installation.html
- PaddleOCR inference engine：https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/local_inference/inference_engine.html
- pypdf 文本提取和内存警告：https://pypdf.readthedocs.io/en/stable/user/extract-text.html
- pypdfium2 文档：https://pypdfium2.readthedocs.io/en/stable/
