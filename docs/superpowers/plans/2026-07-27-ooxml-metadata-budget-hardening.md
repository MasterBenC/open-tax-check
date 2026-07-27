# OOXML Metadata Budget Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在任何 `openpyxl.load_workbook()` 调用前，对全部非工作表 OOXML XML/关系成员实施单成员大小、累计大小和累计节点预算。

**Architecture:** 在现有 ZIP 结构预检之后枚举所有 `.xml`/`.rels` 成员，仅排除由现有工作表扫描器处理的 `xl/worksheets/*.xml`。先通过 `ZipInfo.file_size` 完成不解压的单成员和累计大小检查，再使用 `defusedxml.ElementTree.iterparse` 跨成员流式累计节点，最后才进入现有工作表特征扫描和两次 `openpyxl` 加载。

**Tech Stack:** Python 3.11-3.13、标准库 `zipfile/pathlib/xml.etree`、`defusedxml`、`openpyxl`、`unittest`、GitHub Actions。

## Global Constraints

- Python 许可范围保持 `>=3.11,<3.14`，不得新增第三方依赖。
- `MAX_METADATA_MEMBER_BYTES` 保持 `64 * 1024 * 1024`。
- 新增 `MAX_METADATA_TOTAL_BYTES = 128 * 1024 * 1024`。
- 新增 `MAX_METADATA_TOTAL_NODES = 1_000_000`。
- 候选成员为规范化名称以 `.xml` 或 `.rels` 结尾的所有 ZIP 成员。
- 仅排除 `xl/worksheets/` 下的实际 `.xml` 工作表；`xl/worksheets/_rels/*.rels` 必须纳入元数据预算。
- 未引用但位于包内的候选元数据同样计入预算并接受 XML 解析。
- ZIP、工作表、单元格、合并区域和关系目标的现有预算及错误码不得改变。
- XML 损坏和安全 XML 解析拒绝统一返回 `corrupt_workbook`。
- 预算错误不得包含 XML 文本、共享字符串、样式、单元格值或客户路径。
- 路径输入和 `source_bytes` 输入必须经过同一预检并产生相同诊断。
- 任何预算失败必须发生在 `_open_binary_stream()` 和 `openpyxl.load_workbook()` 之前。
- 不实现隐藏行过滤，不修改 Phase 2B 会计标准化、借贷平衡、报表解析或申报表解析。
- 所有代码任务必须按红、绿、重构执行；不能用真实超大文件测试，必须临时调低预算常量。

---

### Task 1: Bound metadata member and aggregate sizes

**Files:**
- Modify: `src/open_tax_check/workbooks.py:16-31`
- Modify: `src/open_tax_check/workbooks.py:494-511`
- Modify: `src/open_tax_check/workbooks.py:791-836`
- Test: `tests/test_workbooks.py:1-70`
- Test: `tests/test_workbooks.py:283-299`

**Interfaces:**
- Consumes: `ZipFile.infolist()` and each `ZipInfo.filename/file_size/is_dir()`.
- Produces: `_ooxml_metadata_members(package: ZipFile) -> tuple[ZipInfo, ...]` and `_bounded_ooxml_metadata_members(package: ZipFile) -> tuple[ZipInfo, ...]` for Task 2.
- Preserves: `_iter_xml_elements(package, member, element_tree, *, bounded_index_member)` behavior for existing string member names.

- [ ] **Step 1: Add a deterministic ZIP-member replacement helper**

Add `ZipInfo` to the existing import and place this helper beside `rewrite_zip_member()` in `tests/test_workbooks.py`:

```python
def upsert_zip_member(path: Path, member_name: str, content: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    replaced = False
    with ZipFile(path, "r") as source:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as target:
            for item in source.infolist():
                if item.filename == member_name:
                    target.writestr(item, content)
                    replaced = True
                else:
                    target.writestr(item, source.read(item))
            if not replaced:
                target.writestr(member_name, content)
    temporary.replace(path)
```

- [ ] **Step 2: Write failing security-contract and member-selection tests**

Extend `test_ooxml_limits_match_security_contract()`:

```python
self.assertEqual(
    workbooks.MAX_METADATA_TOTAL_BYTES,
    128 * 1024 * 1024,
)
self.assertEqual(workbooks.MAX_METADATA_TOTAL_NODES, 1_000_000)
```

Add a focused selection test:

```python
def test_metadata_members_include_relationships_but_exclude_worksheet_xml(self):
    payload = io.BytesIO()
    with ZipFile(payload, "w") as package:
        package.writestr("[Content_Types].xml", b"<Types/>")
        package.writestr("xl/styles.xml", b"<styleSheet/>")
        package.writestr("xl/worksheets/sheet1.xml", b"<worksheet/>")
        package.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            b"<Relationships/>",
        )
        package.writestr("xl/media/image1.png", b"image")

    payload.seek(0)
    with ZipFile(payload) as package:
        names = tuple(
            item.filename
            for item in workbooks._ooxml_metadata_members(package)
        )

    self.assertEqual(
        names,
        (
            "[Content_Types].xml",
            "xl/styles.xml",
            "xl/worksheets/_rels/sheet1.xml.rels",
        ),
    )
```

- [ ] **Step 3: Run the contract and selection tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest `
  tests.test_workbooks.WorkbookReaderTests.test_ooxml_limits_match_security_contract `
  tests.test_workbooks.WorkbookReaderTests.test_metadata_members_include_relationships_but_exclude_worksheet_xml -v
```

Expected: FAIL because `MAX_METADATA_TOTAL_BYTES`, `MAX_METADATA_TOTAL_NODES`, and `_ooxml_metadata_members` do not exist.

- [ ] **Step 4: Write failing per-member and aggregate-size tests**

Add a shared-string integration test that proves failure occurs before an `openpyxl` stream opens:

```python
def test_oversized_shared_strings_are_rejected_before_openpyxl(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        path = make_xlsx(Path(temp_dir) / "shared-strings.xlsx")
        content = (
            '<sst xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main">'
            + "".join(
                f"<si><t>unique-{index:05d}</t></si>"
                for index in range(2_000)
            )
            + "</sst>"
        ).encode("utf-8")
        upsert_zip_member(path, "xl/sharedStrings.xml", content)

        with mock.patch.object(workbooks, "MAX_METADATA_MEMBER_BYTES", 16_384):
            with mock.patch.object(workbooks, "MAX_COMPRESSION_RATIO", 10_000):
                with mock.patch(
                    "open_tax_check.workbooks._open_binary_stream",
                    side_effect=AssertionError("openpyxl stream must not open"),
                ) as stream_opener:
                    with self.assertRaisesRegex(
                        WorkbookSafetyError,
                        "^ooxml_metadata_member_too_large$",
                    ):
                        open_workbook(path)

    stream_opener.assert_not_called()
```

Add the equivalent styles test with a valid-enough untrusted XML payload that is blocked before semantic parsing:

```python
def test_oversized_styles_are_rejected_before_openpyxl(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        path = make_xlsx(Path(temp_dir) / "styles.xlsx")
        content = (
            '<styleSheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><numFmts>'
            + "".join(
                f'<numFmt numFmtId="{index + 164}" formatCode="value-{index:05d}"/>'
                for index in range(1_000)
            )
            + "</numFmts></styleSheet>"
        ).encode("utf-8")
        upsert_zip_member(path, "xl/styles.xml", content)

        with mock.patch.object(workbooks, "MAX_METADATA_MEMBER_BYTES", 16_384):
            with mock.patch.object(workbooks, "MAX_COMPRESSION_RATIO", 10_000):
                with mock.patch(
                    "open_tax_check.workbooks._open_binary_stream",
                    side_effect=AssertionError("openpyxl stream must not open"),
                ) as stream_opener:
                    with self.assertRaisesRegex(
                        WorkbookSafetyError,
                        "^ooxml_metadata_member_too_large$",
                    ):
                        open_workbook(path)

    stream_opener.assert_not_called()
```

Add aggregate and exact-boundary unit tests:

```python
def test_metadata_members_reject_aggregate_size_split_across_members(self):
    payload = io.BytesIO()
    with ZipFile(payload, "w") as package:
        package.writestr("docProps/a.xml", b"<a>1234</a>")
        package.writestr("docProps/b.xml", b"<b>5678</b>")

    payload.seek(0)
    with ZipFile(payload) as package:
        sizes = tuple(item.file_size for item in package.infolist())
        with mock.patch.object(
            workbooks,
            "MAX_METADATA_MEMBER_BYTES",
            max(sizes),
        ):
            with mock.patch.object(
                workbooks,
                "MAX_METADATA_TOTAL_BYTES",
                sum(sizes) - 1,
            ):
                with self.assertRaisesRegex(
                    WorkbookSafetyError,
                    "^ooxml_metadata_total_too_large$",
                ):
                    workbooks._bounded_ooxml_metadata_members(package)


def test_metadata_member_limit_precedes_aggregate_limit(self):
    payload = io.BytesIO()
    with ZipFile(payload, "w") as package:
        package.writestr("docProps/a.xml", b"<a>1234</a>")

    payload.seek(0)
    with ZipFile(payload) as package:
        size = package.infolist()[0].file_size
        with mock.patch.object(
            workbooks,
            "MAX_METADATA_MEMBER_BYTES",
            size - 1,
        ):
            with mock.patch.object(
                workbooks,
                "MAX_METADATA_TOTAL_BYTES",
                size - 1,
            ):
                with self.assertRaisesRegex(
                    WorkbookSafetyError,
                    "^ooxml_metadata_member_too_large$",
                ):
                    workbooks._bounded_ooxml_metadata_members(package)


def test_metadata_size_boundaries_are_inclusive(self):
    payload = io.BytesIO()
    with ZipFile(payload, "w") as package:
        package.writestr("docProps/a.xml", b"<a/>")
        package.writestr("docProps/b.xml", b"<b/>")

    payload.seek(0)
    with ZipFile(payload) as package:
        sizes = tuple(item.file_size for item in package.infolist())
        with mock.patch.object(
            workbooks,
            "MAX_METADATA_MEMBER_BYTES",
            max(sizes),
        ):
            with mock.patch.object(
                workbooks,
                "MAX_METADATA_TOTAL_BYTES",
                sum(sizes),
            ):
                members = workbooks._bounded_ooxml_metadata_members(package)

    self.assertEqual(len(members), 2)
```

- [ ] **Step 5: Run the five size tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest `
  tests.test_workbooks.WorkbookReaderTests.test_oversized_shared_strings_are_rejected_before_openpyxl `
  tests.test_workbooks.WorkbookReaderTests.test_oversized_styles_are_rejected_before_openpyxl `
  tests.test_workbooks.WorkbookReaderTests.test_metadata_members_reject_aggregate_size_split_across_members `
  tests.test_workbooks.WorkbookReaderTests.test_metadata_member_limit_precedes_aggregate_limit `
  tests.test_workbooks.WorkbookReaderTests.test_metadata_size_boundaries_are_inclusive -v
```

Expected: FAIL because the new metadata size helpers and aggregate error do not exist; the existing preflight does not apply the per-member metadata cap to `sharedStrings.xml` or `styles.xml`.

- [ ] **Step 6: Implement metadata selection and size budgets**

In `src/open_tax_check/workbooks.py`, import `ZipInfo`, add the two constants, and add these helpers before `_iter_xml_elements()`:

```python
MAX_METADATA_TOTAL_BYTES = 128 * 1024 * 1024
MAX_METADATA_TOTAL_NODES = 1_000_000


def _ooxml_metadata_members(package: ZipFile) -> tuple[ZipInfo, ...]:
    members = []
    for item in package.infolist():
        if item.is_dir():
            continue
        normalised = item.filename.replace("\\", "/")
        casefolded = normalised.casefold()
        suffix = PurePosixPath(casefolded).suffix
        worksheet_xml = (
            casefolded.startswith("xl/worksheets/")
            and suffix == ".xml"
            and not casefolded.startswith("xl/worksheets/_rels/")
        )
        if suffix in {".xml", ".rels"} and not worksheet_xml:
            members.append(item)
    return tuple(members)


def _bounded_ooxml_metadata_members(
    package: ZipFile,
) -> tuple[ZipInfo, ...]:
    members = _ooxml_metadata_members(package)
    if any(item.file_size > MAX_METADATA_MEMBER_BYTES for item in members):
        raise WorkbookSafetyError("ooxml_metadata_member_too_large")
    if sum(item.file_size for item in members) > MAX_METADATA_TOTAL_BYTES:
        raise WorkbookSafetyError("ooxml_metadata_total_too_large")
    return members
```

Call `_bounded_ooxml_metadata_members(package)` immediately after `_validate_ooxml_package(package)` inside `preflight_ooxml()`. Do not import `defusedxml` in this task; size failures must still precede dependency imports.

- [ ] **Step 7: Run Task 1 tests and the full workbook module**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_workbooks -v
```

Expected: PASS with no new skips beyond platform-dependent existing tests.

- [ ] **Step 8: Commit Task 1**

```powershell
git add src/open_tax_check/workbooks.py tests/test_workbooks.py
git commit -m "fix: bound OOXML metadata sizes"
```

---

### Task 2: Stream and bound metadata XML nodes

**Files:**
- Modify: `src/open_tax_check/workbooks.py:494-511`
- Modify: `src/open_tax_check/workbooks.py:791-836`
- Test: `tests/test_workbooks.py:120-299`
- Test: `tests/test_workbooks.py:700-760`

**Interfaces:**
- Consumes: `_bounded_ooxml_metadata_members(package) -> tuple[ZipInfo, ...]` from Task 1.
- Produces: `_validate_ooxml_metadata(package: ZipFile, element_tree: Any) -> None`.
- Changes: `_iter_xml_elements()` accepts `str | ZipInfo`; all existing string callers retain their behavior.
- Integrates: `preflight_ooxml(path, source_bytes=None)` validates metadata nodes for both path and in-memory snapshot sources.

- [ ] **Step 1: Write failing cumulative-node and boundary tests**

```python
def test_metadata_node_budget_accumulates_across_members(self):
    payload = io.BytesIO()
    with ZipFile(payload, "w") as package:
        package.writestr("docProps/a.xml", b"<root><a/></root>")
        package.writestr("docProps/b.xml", b"<root><b/></root>")

    payload.seek(0)
    with ZipFile(payload) as package:
        with mock.patch.object(workbooks, "MAX_METADATA_TOTAL_NODES", 3):
            with self.assertRaisesRegex(
                WorkbookSafetyError,
                "^ooxml_metadata_node_limit_exceeded$",
            ):
                workbooks._validate_ooxml_metadata(
                    package,
                    workbooks._defused_element_tree(),
                )


def test_metadata_node_boundary_is_inclusive(self):
    payload = io.BytesIO()
    with ZipFile(payload, "w") as package:
        package.writestr("docProps/a.xml", b"<root><a/><b/></root>")

    payload.seek(0)
    with ZipFile(payload) as package:
        with mock.patch.object(workbooks, "MAX_METADATA_TOTAL_NODES", 3):
            workbooks._validate_ooxml_metadata(
                package,
                workbooks._defused_element_tree(),
            )
```

- [ ] **Step 2: Write failing corrupt-XML and source-parity tests**

```python
def test_corrupt_shared_strings_fail_before_openpyxl_without_detail_leak(self):
    sensitive_detail = "private-shared-string"
    with tempfile.TemporaryDirectory() as temp_dir:
        path = make_xlsx(Path(temp_dir) / "corrupt-shared-strings.xlsx")
        upsert_zip_member(
            path,
            "xl/sharedStrings.xml",
            f"<sst><si>{sensitive_detail}".encode("utf-8"),
        )

        with mock.patch(
            "open_tax_check.workbooks._open_binary_stream",
            side_effect=AssertionError("openpyxl stream must not open"),
        ) as stream_opener:
            with self.assertRaisesRegex(
                WorkbookError,
                "^corrupt_workbook$",
            ) as raised:
                open_workbook(path)

    stream_opener.assert_not_called()
    self.assertNotIn(sensitive_detail, str(raised.exception))
    self.assertNotIn(str(path), str(raised.exception))


def test_metadata_node_limit_matches_for_path_and_source_snapshot(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        path = make_xlsx(Path(temp_dir) / "metadata-nodes.xlsx")
        source_bytes = path.read_bytes()

        with mock.patch(
            "open_tax_check.workbooks._open_binary_stream",
            side_effect=AssertionError("openpyxl stream must not open"),
        ) as stream_opener:
            for snapshot in (None, source_bytes):
                with self.subTest(source_bytes=snapshot is not None):
                    with mock.patch.object(
                        workbooks,
                        "MAX_METADATA_TOTAL_NODES",
                        1,
                    ):
                        with self.assertRaisesRegex(
                            WorkbookSafetyError,
                            "^ooxml_metadata_node_limit_exceeded$",
                        ):
                            open_workbook(path, source_bytes=snapshot)

        stream_opener.assert_not_called()
```

- [ ] **Step 3: Run the four Task 2 tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest `
  tests.test_workbooks.WorkbookReaderTests.test_metadata_node_budget_accumulates_across_members `
  tests.test_workbooks.WorkbookReaderTests.test_metadata_node_boundary_is_inclusive `
  tests.test_workbooks.WorkbookReaderTests.test_corrupt_shared_strings_fail_before_openpyxl_without_detail_leak `
  tests.test_workbooks.WorkbookReaderTests.test_metadata_node_limit_matches_for_path_and_source_snapshot -v
```

Expected: FAIL because `_validate_ooxml_metadata` does not exist and malformed unreferenced `sharedStrings.xml` is not currently parsed before `openpyxl`.

- [ ] **Step 4: Allow exact `ZipInfo` members in the streaming iterator**

Change `_iter_xml_elements()` without breaking existing callers:

```python
def _iter_xml_elements(
    package: ZipFile,
    member: str | ZipInfo,
    element_tree: Any,
    *,
    bounded_index_member: bool,
) -> Iterator[tuple[str, Any]]:
    metadata = (
        package.getinfo(member)
        if isinstance(member, str)
        else member
    )
    if (
        bounded_index_member
        and metadata.file_size > MAX_METADATA_MEMBER_BYTES
    ):
        raise WorkbookSafetyError("ooxml_metadata_member_too_large")
    with package.open(metadata) as source:
        yield from element_tree.iterparse(
            source,
            events=("start", "end"),
        )
```

Passing `ZipInfo` is required so duplicate ZIP member names cannot make the scanner repeatedly parse a different entry than the one whose size was budgeted.

- [ ] **Step 5: Implement cumulative streaming node validation**

Place this helper after `_bounded_ooxml_metadata_members()`:

```python
def _validate_ooxml_metadata(
    package: ZipFile,
    element_tree: Any,
) -> None:
    nodes = 0
    for metadata in _bounded_ooxml_metadata_members(package):
        for event, element in _iter_xml_elements(
            package,
            metadata,
            element_tree,
            bounded_index_member=False,
        ):
            if event == "start":
                nodes += 1
                if nodes > MAX_METADATA_TOTAL_NODES:
                    raise WorkbookSafetyError(
                        "ooxml_metadata_node_limit_exceeded"
                    )
            else:
                element.clear()
```

- [ ] **Step 6: Integrate the validator and preserve stable exception mapping**

Inside the `ZipFile` context in `preflight_ooxml()`, replace the Task 1 size-only call with:

```python
_validate_ooxml_package(package)
_validate_ooxml_metadata(package, _defused_element_tree())
```

Use the same safe mapping pattern as `_ooxml_sheet_features()`:

```python
except WorkbookError:
    raise
except (BadZipFile, KeyError, ParseError, UnicodeDecodeError):
    raise WorkbookError("corrupt_workbook") from None
except OSError:
    raise WorkbookError("workbook_io_error") from None
except Exception:
    raise WorkbookError("corrupt_workbook") from None
```

Do not move `_defused_element_tree()` before `_validate_ooxml_package()`: member-count, total-size, unsafe-name, and compression-ratio failures must still occur before dependency imports.

- [ ] **Step 7: Run Task 2 tests and the complete workbook test module**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_workbooks -v
```

Expected: PASS. Confirm the previously existing ZIP, worksheet, cell, formula/cache, close-error, `.xls`, and optional-dependency tests remain green.

- [ ] **Step 8: Run focused cross-module safety regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest `
  tests.test_intake `
  tests.test_inspect_pipeline `
  tests.test_excel_pipeline `
  tests.test_public_repo_contract -v
```

Expected: PASS with only explicit platform privilege skips.

- [ ] **Step 9: Commit Task 2**

```powershell
git add src/open_tax_check/workbooks.py tests/test_workbooks.py
git commit -m "fix: bound OOXML metadata nodes"
```

---

### Task 3: Validate the release and refresh PR #2

**Files:**
- Verify only: repository-wide tracked files
- External update: GitHub PR `MasterBenC/open-tax-check#2`

**Interfaces:**
- Consumes: the Task 1 and Task 2 commits plus design commit `c52debc`.
- Produces: a verified branch, updated draft PR title/body, and successful GitHub CI matrix.
- Does not produce: a merge to `main` or a GitHub release/tag.

- [ ] **Step 1: Run the complete source-tree suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Expected: 0 failures. Record passed and skipped counts exactly; do not classify permission-dependent skips as passes.

- [ ] **Step 2: Run static and dependency checks**

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests scripts
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Expected: all commands exit 0 and `pip check` reports no broken requirements.

- [ ] **Step 3: Build current distributions and run the strict release gate**

```powershell
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe scripts\validate_release.py --require-distributions
Get-FileHash -Algorithm SHA256 `
  dist\open_tax_check-0.1.0-py3-none-any.whl, `
  dist\open_tax_check-0.1.0.tar.gz
```

Expected: exactly one wheel and one sdist are accepted by strict validation. Record both SHA-256 values from the current checkout.

- [ ] **Step 4: Request independent whole-change review**

Use `superpowers:requesting-code-review` over the range `c52debc^..HEAD`. The reviewer must check:

- every `.xml`/`.rels` candidate is budgeted except actual worksheet XML;
- worksheet relationships remain included;
- single-member checks precede aggregate checks and XML opening;
- node totals accumulate across members;
- duplicate ZIP names are scanned through their exact `ZipInfo`;
- path and `source_bytes` flows match;
- no exception leaks content or paths;
- no existing workbook budget was weakened.

If the review reports Critical or Important findings, fix them with TDD and obtain a scoped re-review before continuing.

- [ ] **Step 5: Push the verified branch**

```powershell
git status -sb
git push origin feat/excel-intake-phase-2a
```

Expected: the remote branch advances to the verified local HEAD with no force push.

- [ ] **Step 6: Refresh PR #2 metadata without merging**

Set the title:

```powershell
gh pr edit 2 --repo MasterBenC/open-tax-check `
  --title "[codex] Complete safe Excel intake and accounting normalization"
```

Update the body so it contains these exact sections and facts:

- `## What changed`: Phase 2A intake, source-bound selection, Phase 2B mapping/normalization/reconciliation, procedure coverage, and unified OOXML metadata budgets.
- `## Safety boundaries`: hidden-row `exclude` fails closed with `hidden_row_exclusion_unavailable`; statements and returns remain `present_unparsed`; reconciliation success is not a no-risk conclusion.
- `## Validation`: exact local suite pass/skip counts, build result, strict release result, current wheel/sdist hashes, and the 3 operating systems by 3 Python versions CI requirement.

Do not mark the PR ready until the new commit's CI is complete.

- [ ] **Step 7: Wait for the full matrix and mark the PR ready**

```powershell
gh pr checks 2 --repo MasterBenC/open-tax-check --watch
gh pr ready 2 --repo MasterBenC/open-tax-check
gh pr view 2 --repo MasterBenC/open-tax-check `
  --json isDraft,mergeable,mergeStateStatus,statusCheckRollup,url
```

Expected: Ubuntu, Windows, and macOS on Python 3.11, 3.12, and 3.13 all report success; PR #2 is no longer a draft and remains mergeable. Stop before merging to `main`; that integration remains a separate user decision.
