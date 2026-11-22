# Excel / 工作簿进入细节

本文件承接工作簿盘点的细节：隐藏数据、多行表头、合并单元格、候选表与自动选择边界。命令形状与退出码见 SKILL.md 主文件的命令表，这里只讲“看到什么、复核人怎么定”。

## 1. 盘点产物

`inspect` 之后，产物按运行目录写入：

| 产物 | 内容 |
| --- | --- |
| `work/workbook_inventory.json` | 每个工作簿的来源哈希、工作表清单、每表可见性、`has_hidden_data`、`candidate_header_rows`、识别出的角色候选与置信度 |
| `work/sheet_selection.json` | 当前生效的选表决定：`role`、`source_id`、`sheet`、`header_rows`、`hidden_rows`，以及是否需要人工选表 |
| `work/coverage_matrix.json` | 每个证据角色的 `status`（`missing` / `present_unparsed` / 已识别）、`procedure_status`、`source_ids` |

`state.json` 的 `status` 与 `next_action` 是唯一推进依据；盘点产物只用于读取事实，不要手改。

## 2. 自动选择的边界

引擎只在同时满足以下条件时自动选表：候选唯一、工作表可见、置信度高、`has_hidden_data` 为 `false`。任一条不满足就进入 `needs_sheet_selection`。

- **隐藏工作表不得自动选择**，即使候选唯一、置信度高。
- 含隐藏行、隐藏列、筛选隐藏或分组折叠的可见表也可能被自动选中，但任何隐藏数据都必须由复核人显式给 `include` 或 `exclude`。
- 多个工作表都像序时账、或一个工作簿里既有序时账又有试算平衡表时，逐角色分别决定，不要一次决定全部。

## 3. 隐藏行与隐藏列

读取器尚不能过滤隐藏行。因此：

1. 复核人必须先判断隐藏数据是否属于本次范围。
2. 选择 `--hidden-rows exclude` 且该表 `has_hidden_data` 为 `true` 时，引擎以 `hidden_row_exclusion_unavailable` 阻断，退出码 `4`；这不是程序错误，是未实现的能力边界。
3. 处理方式只有两种：复核后改为 `include`（并把隐藏数据纳入范围披露），或停止处理并说明原因。不得在文档或报告里写“已排除隐藏行”。
4. 隐藏列不改变行范围，但仍须在报告的数据限制里披露。

## 4. 多行表头与合并单元格

- 识别结果在 `candidate_header_rows`；真实表头常跨两到三行，合并单元格会让标题落在首行、单位落在次行。
- 应把**全部**表头行按行号顺序重复传 `--header-row`，例如 `--header-row 3 --header-row 4`；只传一行会丢字段名或单位。
- 表头行选择错误会直接导致字段映射缺失；重新选表后必须重新 `inspect` 并重新确认映射，不要沿用旧映射。
- 表体前有标题行、说明行、小计行、空行时，不要把它们当作表头或数据行。

## 5. 输入入口与文件类型

- 工作簿放在 `<case-dir>/input/`；支持的扩展名是 `.xlsx`、`.xlsm`、`.xls`。
- 目录、符号链接、临时文件（`~$` 开头）和不支持的扩展名会阻断处理，程序不会跟随或猜测。
- 加密、损坏或被其他程序独占的工作簿无法读取，属 `needs_intake_review`（`next_action=inspect_processing_error`），退出码 `4`；不得声称“文件中没有数据”。
- 同一案件不要混放多个版本的同一工作簿；先由客户指定版本，再重新 `inspect`。

## 6. 决定失效与重跑

- 选表决定与来源哈希、工作表名、表头行、`hidden_rows`、期间和 schema 版本绑定；来源文件变化后决定自动失效，须重新 `inspect` → 重新选表。
- 复核人身份写入 `--reviewer host:<复核人>`，同一决定不要由两个宿主并发写入。
- 每次重跑产生新的 `runs/run-XXXX/`；旧的 `partial_scope_ready` 输出不得当作当前结果。
