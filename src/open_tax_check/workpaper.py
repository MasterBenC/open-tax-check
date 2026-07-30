"""Deterministic, customer-safe Excel workpaper generation."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import tempfile
from typing import Mapping
import xml.etree.ElementTree as ElementTree
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .tax_models import TaxCheckSnapshot, TaxStatus


WORKPAPER_SCHEMA_VERSION = "tax-workpaper/v1"
WORKPAPER_NAME = "税务健康检查工作底稿.xlsx"

_SHEETS = (
    "项目概览", "资料清单", "数据质量", "账务勾稽", "财报勾稽", "申报勾稽",
    "税务筛查", "正式计算", "客户问题", "证据索引", "规则版本", "运行记录",
)
_HEADER_FILL = "1F4E78"
_HEADER_FONT = "FFFFFF"
_ALT_FILL = "EAF2F8"
_FIXED_TIME = datetime(2026, 1, 1)
_CORE_PROPERTIES_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
_DCTERMS_NS = "http://purl.org/dc/terms/"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
for _prefix, _namespace in (
    ("cp", _CORE_PROPERTIES_NS),
    ("dcterms", _DCTERMS_NS),
    ("dc", _DC_NS),
    ("xsi", _XSI_NS),
):
    ElementTree.register_namespace(_prefix, _namespace)


class WorkpaperError(ValueError):
    """Stable workpaper generation error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require_snapshot(snapshot: object) -> TaxCheckSnapshot:
    if not isinstance(snapshot, TaxCheckSnapshot):
        raise WorkpaperError("invalid_workpaper_snapshot")
    return snapshot


def _as_text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _unique_evidence_refs(snapshot: TaxCheckSnapshot) -> tuple[str, ...]:
    refs: set[str] = set()
    for fact in snapshot.request.facts:
        refs.update(ref for ref in fact.evidence_refs if isinstance(ref, str) and ref)
    for result in snapshot.results:
        refs.update(ref for ref in result.evidence_refs if isinstance(ref, str) and ref)
        if result.calculation is not None:
            refs.update(
                ref for ref in result.calculation.evidence_refs
                if isinstance(ref, str) and ref
            )
    return tuple(sorted(refs))


def _manifest_index(source_manifest: Mapping[str, object] | None) -> dict[str, dict[str, object]]:
    if source_manifest is None:
        return {}
    raw_sources = source_manifest.get("sources")
    if not isinstance(raw_sources, list):
        raise WorkpaperError("invalid_workpaper_manifest")
    index: dict[str, dict[str, object]] = {}
    for item in raw_sources:
        if not isinstance(item, dict) or not isinstance(item.get("source_id"), str):
            raise WorkpaperError("invalid_workpaper_manifest")
        source_id = item["source_id"]
        if not source_id or source_id in index:
            raise WorkpaperError("invalid_workpaper_manifest")
        index[source_id] = item
    return index


def _write_table(sheet, headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    sheet.append(headers)
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=_HEADER_FILL)
        cell.font = Font(color=_HEADER_FONT, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row_number, row in enumerate(rows, start=2):
        sheet.append(row)
        if row_number % 2 == 0:
            for cell in sheet[row_number]:
                cell.fill = PatternFill("solid", fgColor=_ALT_FILL)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = [max(12, min(48, len(_as_text(value)) + 2)) for value in headers]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width


def _build_workbook(snapshot: TaxCheckSnapshot, *, source_manifest=None, delivery_revision=None):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
    except ImportError as error:
        raise WorkpaperError("workpaper_dependency_missing") from error

    manifest = _manifest_index(source_manifest)
    workbook = Workbook()
    workbook.properties.creator = "OpenTaxCheck"
    workbook.properties.lastModifiedBy = "OpenTaxCheck"
    workbook.properties.title = WORKPAPER_NAME
    workbook.properties.subject = WORKPAPER_SCHEMA_VERSION
    workbook.properties.created = _FIXED_TIME
    workbook.properties.modified = _FIXED_TIME
    workbook.calculation.fullCalcOnLoad = False
    workbook.calculation.forceFullCalc = False
    workbook.calculation.calcMode = "manual"

    first = workbook.active
    first.title = _SHEETS[0]
    for sheet_name in _SHEETS[1:]:
        workbook.create_sheet(sheet_name)

    overview_rows = [
        ("检查期间开始", snapshot.request.period.start.isoformat()),
        ("检查期间结束", snapshot.request.period.end.isoformat()),
        ("适用地区", snapshot.request.jurisdiction),
        ("快照修订号", snapshot.revision),
        ("输入哈希数量", len(snapshot.request.input_hashes)),
        ("工作底稿版本", WORKPAPER_SCHEMA_VERSION),
        ("当前结论范围", "仅限当前已覆盖程序"),
    ]
    if delivery_revision is not None:
        overview_rows.append(("交付修订号", delivery_revision))
    _write_table(first, ("字段", "值"), overview_rows)
    first["A1"].font = Font(color=_HEADER_FONT, bold=True)
    first["B1"].font = Font(color=_HEADER_FONT, bold=True)
    first.column_dimensions["A"].width = 24
    first.column_dimensions["B"].width = 48
    first["B2"].alignment = Alignment(horizontal="left")

    _write_table(
        workbook["资料清单"],
        ("来源编号", "SHA-256", "媒体类型", "状态"),
        [
            (
                source_id,
                _as_text(item.get("sha256"))[:64],
                _as_text(item.get("media_type") or item.get("extension")),
                _as_text(item.get("status")),
            )
            for source_id, item in sorted(manifest.items())
        ],
    )

    quality_rows: list[tuple[object, ...]] = []
    for result in snapshot.results:
        quality_rows.append((result.tax_id, result.status.value, ";".join(result.diagnostics)))
    _write_table(workbook["数据质量"], ("税种编号", "状态", "稳定诊断"), quality_rows)

    for sheet_name, title in (("账务勾稽", "当前快照未直接包含账务勾稽明细"),
                              ("财报勾稽", "当前快照未直接包含财报勾稽明细"),
                              ("申报勾稽", "当前快照未直接包含申报勾稽明细")):
        _write_table(workbook[sheet_name], ("控制范围", "状态", "说明"),
                     [(sheet_name, "未提供", title)])

    _write_table(
        workbook["税务筛查"],
        ("税种编号", "状态", "状态说明", "规则包", "规则版本"),
        [
            (
                result.tax_id,
                result.status.value,
                result.status.value,
                ",".join(result.rule_pack_ids),
                ",".join(result.rule_pack_versions),
            )
            for result in snapshot.results
        ],
    )

    calculation_rows: list[tuple[object, ...]] = []
    for result in snapshot.results:
        if result.calculation is None:
            continue
        calculation = result.calculation
        calculation_rows.append((
            result.tax_id,
            calculation.formula_id,
            calculation.tax_base,
            calculation.deductions,
            calculation.net_base,
            calculation.rate,
            calculation.amount,
            ",".join(calculation.evidence_refs),
        ))
    _write_table(
        workbook["正式计算"],
        ("税种编号", "公式编号", "计税基础", "扣除额", "净计税基础", "税率或参数", "计算金额", "证据引用"),
        calculation_rows,
    )

    _write_table(
        workbook["客户问题"],
        ("问题编号", "事实键", "涉及税种", "问题", "答案类型", "是否阻断", "证据类型"),
        [
            (
                question.question_id,
                question.fact_key,
                ",".join(question.tax_ids),
                question.question_zh,
                question.answer_type,
                "是" if question.blocking else "否",
                ",".join(question.evidence_types),
            )
            for question in snapshot.questions
        ],
    )

    evidence_rows = []
    for reference in _unique_evidence_refs(snapshot):
        item = manifest.get(reference, {})
        evidence_rows.append((
            reference,
            _as_text(item.get("sha256")),
            _as_text(item.get("media_type") or item.get("extension")),
            "已绑定" if reference in manifest else "待案件清单绑定",
        ))
    _write_table(workbook["证据索引"], ("证据引用", "SHA-256", "媒体类型", "绑定状态"), evidence_rows)

    rule_rows = []
    for result in snapshot.results:
        for source in result.rule_sources:
            rule_rows.append((
                result.tax_id,
                source.title,
                source.document_number,
                source.published_on.isoformat(),
                source.url,
                source.verified_on.isoformat() if source.verified_on else "",
            ))
    _write_table(workbook["规则版本"], ("税种编号", "来源标题", "文号", "发布日期", "官方链接", "核验日期"), rule_rows)

    _write_table(
        workbook["运行记录"],
        ("事件类型", "发生时间", "涉及税种", "涉及事实键", "确认摘要"),
        [
            (
                event.event_type,
                event.occurred_at,
                ",".join(event.tax_ids),
                ",".join(event.fact_keys),
                event.confirmation_digest or "",
            )
            for event in snapshot.audit_events
        ],
    )

    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    return workbook


def _canonical_core_properties(payload: bytes) -> bytes:
    root = ElementTree.fromstring(payload)
    modified = root.find(f"{{{_DCTERMS_NS}}}modified")
    if modified is None:
        raise WorkpaperError("workpaper_write_failed")
    modified.text = "2026-01-01T00:00:00Z"
    return ElementTree.tostring(root, encoding="utf-8")


def write_tax_health_workpaper(
    snapshot: TaxCheckSnapshot,
    path: Path,
    *,
    source_manifest: Mapping[str, object] | None = None,
    delivery_revision: int | None = None,
) -> Path:
    """Write a deterministic, redacted Excel workpaper atomically."""
    snapshot = _require_snapshot(snapshot)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = _build_workbook(
        snapshot,
        source_manifest=source_manifest,
        delivery_revision=delivery_revision,
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
        workbook.save(temporary)
        canonical = temporary.with_suffix(".canonical")
        with ZipFile(temporary, "r") as source, ZipFile(canonical, "w") as target:
            for name in sorted(source.namelist()):
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                payload = source.read(name)
                if name == "docProps/core.xml":
                    payload = _canonical_core_properties(payload)
                target.writestr(info, payload)
        os.replace(canonical, temporary)
        with temporary.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except WorkpaperError:
        raise
    except (OSError, ValueError):
        raise WorkpaperError("workpaper_write_failed") from None
    finally:
        workbook.close()
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
        canonical = temporary.with_suffix(".canonical") if temporary is not None else None
        if canonical is not None and canonical.exists():
            try:
                canonical.unlink()
            except OSError:
                pass
    return path


__all__ = [
    "WORKPAPER_NAME",
    "WORKPAPER_SCHEMA_VERSION",
    "WorkpaperError",
    "write_tax_health_workpaper",
]
