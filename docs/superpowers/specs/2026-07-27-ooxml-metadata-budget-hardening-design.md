# OOXML 元数据预算加固设计

**日期：** 2026-07-27
**状态：** 已确认，待实施
**适用版本：** OpenTaxCheck 0.1.0 候选版本

## 背景

当前 `.xlsx`/`.xlsm` 入口在调用 `openpyxl` 前已经限制 ZIP 成员数、总解压大小、压缩比、工作表数量、工作表 XML 节点、单元格子树和合并区域。工作簿关系与工作表索引也有单成员大小和节点预算。

仍需加固的边界是非工作表 XML 元数据。`sharedStrings.xml`、`styles.xml`、主题、批注、外部关系和文档属性等成员可能在进入 `openpyxl.load_workbook()` 时被解析并保留在内存中。当前 ZIP 总解压上限不能充分限制这些结构在 Python 对象中的内存放大。

## 目标

- 在任何 `openpyxl.load_workbook()` 调用前，统一预检所有非工作表 OOXML XML 和关系成员。
- 同时限制单成员解压大小、非工作表 XML 累计解压大小和累计 XML 节点数。
- 使用流式、安全 XML 解析，不构建完整 XML 树。
- 保持现有正常账套产物、CLI、映射、标准化、勾稽和规则行为不变。
- 保持错误诊断稳定、无客户原始数据和文件内部内容泄露。

## 非目标

- 本次不实现隐藏行实际过滤。
- 本次不修改 Phase 2B 会计标准化或借贷平衡规则。
- 本次不解析财务报表或纳税申报表。
- 本次不引入子进程、操作系统内存限制或解析超时框架。
- 本次不改变 ZIP、工作表、单元格或合并区域的现有预算。

## 方案选择

采用统一非工作表元数据预检，而不是只处理 `sharedStrings.xml` 和 `styles.xml`。统一扫描可以覆盖当前和未来由 `openpyxl` 消费的其他 XML 元数据，避免按文件名逐个补洞。

暂不采用受限子进程。子进程隔离更强，但会引入跨 Windows、macOS 和 Linux 的进程生命周期、资源限制、错误传递和测试复杂度，不适合当前安全收口 PR。

## 扫描范围

候选成员满足以下任一后缀，比较时使用规范化后的成员名：

- `.xml`
- `.rels`

排除 `xl/worksheets/` 下的实际工作表 `.xml`。被引用的工作表 XML 已由现有工作表扫描器限制总节点、单元格子树、访问单元格和合并区域。`xl/worksheets/_rels/` 下的关系文件仍属于本次元数据扫描范围。

扫描范围包括但不限于：

- `[Content_Types].xml`
- `docProps/*.xml`
- `xl/workbook.xml`
- `xl/_rels/workbook.xml.rels`
- `xl/sharedStrings.xml`
- `xl/styles.xml`
- `xl/theme/*.xml`
- 批注、外部链接及其关系文件

未被工作簿引用但位于包内的候选元数据也计入预算。对不需要的超大 XML 保持失败关闭，避免攻击者通过未引用成员规避统一包预算。

## 资源预算

### 单成员预算

每个候选元数据成员继续使用现有 `MAX_METADATA_MEMBER_BYTES = 64 MiB`。在打开或解压成员前读取 `ZipInfo.file_size`，超过上限时返回：

`ooxml_metadata_member_too_large`

### 累计大小预算

新增：

`MAX_METADATA_TOTAL_BYTES = 128 MiB`

先对所有候选成员的 `ZipInfo.file_size` 求和。累计值超过上限时，不打开任何候选 XML，返回：

`ooxml_metadata_total_too_large`

### 累计节点预算

新增：

`MAX_METADATA_TOTAL_NODES = 1_000_000`

使用安全 XML `iterparse` 统计所有候选成员的 `start` 事件。节点数跨成员累计，超过上限时返回现有稳定错误码：

`ooxml_metadata_node_limit_exceeded`

解析 `end` 事件时立即清理元素，避免扫描器自身保留完整树。

### 现有预算

以下限制保持不变并先于元数据节点扫描生效：

- ZIP 成员数
- ZIP 总解压大小
- 单成员压缩比
- 工作表数量
- 工作表 XML 总节点
- 单元格子树节点
- 访问单元格数
- 合并区域数量和引用长度
- 工作簿关系数量和目标长度

## 执行顺序

`preflight_ooxml()` 按以下顺序执行：

1. 验证文件头与 ZIP 结构。
2. 执行现有 ZIP 成员数、总解压量、路径和压缩比检查。
3. 枚举并规范化候选非工作表 XML/关系成员。
4. 检查每个候选成员的单文件大小。
5. 检查候选成员累计解压大小。
6. 使用安全 XML 解析器流式累计节点数。
7. 关闭 ZIP 及所有成员流。
8. 继续现有工作表特征扫描。
9. 只有全部预检成功后，才允许两次 `openpyxl.load_workbook()` 分别加载公式视图和缓存值视图。

路径输入和 `source_bytes` 内存快照输入必须经过同一逻辑，产生相同诊断。

## 错误处理与隐私

- XML 语法损坏继续映射为 `corrupt_workbook`。
- 安全 XML 解析器拒绝的实体或危险结构同样映射为稳定的损坏工作簿诊断。
- 预算错误只包含稳定错误码，不包含 XML 文本、共享字符串、样式内容、单元格值或客户文件路径。
- 任一预检错误都必须发生在实际工作簿加载之前；测试必须验证 `openpyxl.load_workbook()` 未调用。
- 所有打开的 ZIP 和 XML 流在成功与失败路径上都必须关闭。

## 实现边界

预计生产代码只修改：

- `src/open_tax_check/workbooks.py`

预计测试只修改：

- `tests/test_workbooks.py`

可以增加小型测试辅助函数，但不新增生产模块，不改变公开 Python 数据模型。

## 测试策略

实施严格遵循红、绿、重构：

测试通过临时调低预算常量构造小型确定性夹具，不生成真实的超大文件。

1. 超限 `sharedStrings.xml` 在 `openpyxl` 前被拒绝。
2. 超限 `styles.xml` 在 `openpyxl` 前被拒绝。
3. 多个单成员均合规、累计大小超限时被拒绝。
4. 多个成员拆分节点、累计节点超限时被拒绝。
5. 单成员大小、累计大小和累计节点刚好等于上限时允许继续。
6. 路径输入与 `source_bytes` 输入返回相同错误码。
7. 损坏元数据 XML 返回 `corrupt_workbook`。
8. 工作表 XML 仍由现有工作表预算处理，不被新扫描重复计入。
9. 预算失败时 `openpyxl.load_workbook()` 未调用。
10. 现有工作簿、安全入口、Phase 2B 和公开契约测试全部回归通过。

## 验收门槛

- 新增测试先在未实现状态下按预期失败。
- 实现后新增测试和 `tests.test_workbooks` 全部通过。
- 完整测试套件无失败；平台权限条件测试必须如实报告跳过。
- `python -m build` 成功。
- `scripts/validate_release.py --require-distributions` 通过。
- `git diff --check` 通过。
- 独立代码复审无 Critical 或 Important 问题。
- PR #2 标题、正文和验证记录更新为 Phase 2A + Phase 2B + OOXML 安全收口。
- GitHub CI 在 Ubuntu、Windows、macOS 的 Python 3.11、3.12、3.13 矩阵全部通过后，才把 PR 从草稿转为可合并状态。
