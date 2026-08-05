"""Deterministic, customer-safe Excel workpaper generation."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import tempfile
from typing import Mapping
import unicodedata
import xml.etree.ElementTree as ElementTree
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .tax_models import TaxCheckSnapshot, TaxStatus
from .tax_registry import TAX_DEFINITIONS
from .tax_cross_checks import (
    CrossCheckStatus,
    compare_amounts,
    parse_tax_cross_check_payload,
)


WORKPAPER_SCHEMA_VERSION = "tax-workpaper/v1"
WORKPAPER_NAME = "税务健康检查工作底稿.xlsx"

_SHEETS = (
    "项目概览", "资料清单", "抽取与映射", "数据质量", "账务勾稽", "财报勾稽", "申报勾稽",
    "账税交叉核对", "税费筛查", "正式计算", "客户问题", "证据索引", "规则版本", "报告选取", "运行记录",
)
_HEADER_FILL = "1F4E78"
_HEADER_FONT = "FFFFFF"
_ALT_FILL = "EAF2F8"
_TAX_NAMES_ZH = {item.tax_id: item.name_zh for item in TAX_DEFINITIONS}
_TAX_STATUS_ZH = {
    TaxStatus.CALCULATED: "已完成计算",
    TaxStatus.NOT_APPLICABLE: "经资料确认不适用",
    TaxStatus.NEEDS_EVIDENCE: "待补充资料",
    TaxStatus.NEEDS_LOCAL_POLICY: "待确认当地政策",
    TaxStatus.BLOCKED: "暂时无法判断",
}
_SOURCE_STATUS_ZH = {
    "accepted": "已接收",
    "present_unparsed": "已接收，待解析",
    "rejected": "已拒绝",
}
_RECONCILIATION_CHECK_ZH = {
    "document_set": "文档集合",
    "balance_sheet_identity": "资产负债表恒等式",
    "income_statement_identity": "利润表恒等式",
    "cash_flow_identity": "现金流量表恒等式",
    "tax_fact": "申报事实勾稽",
}
_RECONCILIATION_STATUS_ZH = {
    "passed": "通过",
    "blocked": "阻塞",
    "not_performed_missing_fact": "未执行（缺少事实）",
}
_FORMULA_LABELS_ZH = {
    "generic.base_times_rate.v1": "计税基础乘税率",
    "generic.base_minus_deductions_times_rate.v1": "计税基础减扣除额后乘税率",
    "generic.base_times_rate_minus_credits.v1": "计税基础乘税率后减允许抵扣税额",
    "generic.base_times_rate_minus_quick_deduction.v1": "计税基础乘税率后减速算扣除数",
    "generic.quantity_times_unit_amount.v1": "计税数量乘适用单位税额",
    "generic.base_times_applicable_parameter.v1": "已确认计税依据乘适用计税参数",
    "generic.base_times_rate_minus_confirmed_deduction.v1": "增值额乘确认税率后减确认速算扣除额",
}
_ANSWER_TYPE_ZH = {
    "text": "文本",
    "choice": "选择",
    "integer": "整数",
    "boolean": "是/否",
    "date": "日期",
    "decimal": "金额或小数",
}
_AUDIT_EVENT_ZH = {
    "check_started": "检查开始",
    "fact_confirmed": "事实已确认",
    "confirmation_conflict": "确认内容发生冲突",
    "confirmation_reaffirmed": "原确认已再次确认",
}
_MIN_COLUMN_WIDTHS = {
    "税种编号": 30,
    "来源编号": 28,
    "证据引用": 40,
    "SHA-256": 48,
    "文件名": 28,
    "稳定诊断": 28,
    "文档或事实": 30,
    "规则包": 40,
    "公式编号": 32,
    "问题": 40,
    "涉及事实键": 36,
    "证据类型": 32,
    "来源标题": 36,
    "文号": 28,
    "官方链接": 48,
    "选取说明": 24,
    "确认摘要": 48,
}
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
    return tuple(sorted(ref for ref in refs if not ref.startswith("customer-confirmation:")))


def _tax_label(tax_id: str) -> str:
    name = _TAX_NAMES_ZH.get(tax_id)
    return f"{name}（{tax_id}）" if name else tax_id


def _source_status(value: object) -> str:
    text = _as_text(value)
    return _SOURCE_STATUS_ZH.get(text, text)


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
    from openpyxl.utils import get_column_letter

    def text_safe(value: object) -> object:
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    def display_width(value: object) -> int:
        return sum(
            2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
            for character in _as_text(value)
        )

    safe_headers = tuple(text_safe(value) for value in headers)
    safe_rows = [tuple(text_safe(value) for value in row) for row in rows]
    sheet.append(safe_headers)
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=_HEADER_FILL)
        cell.font = Font(color=_HEADER_FONT, bold=True)
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )
    sheet.row_dimensions[1].height = 24
    for row_number, row in enumerate(safe_rows, start=2):
        sheet.append(row)
        for cell in sheet[row_number]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        if row_number % 2 == 0:
            for cell in sheet[row_number]:
                cell.fill = PatternFill("solid", fgColor=_ALT_FILL)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = []
    for index, header in enumerate(headers):
        populated = [header, *(row[index] for row in safe_rows if index < len(row))]
        widths.append(
            min(
                48,
                max(
                    12,
                    _MIN_COLUMN_WIDTHS.get(header, 0),
                    max(display_width(value) for value in populated) + 2,
                ),
            )
        )
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _audit_mapping(case_audit: Mapping[str, object] | None, key: str) -> Mapping[str, object] | None:
    if case_audit is None:
        return None
    if not isinstance(case_audit, Mapping):
        raise WorkpaperError("invalid_workpaper_case_audit")
    value = case_audit.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise WorkpaperError("invalid_workpaper_case_audit")
    return value


def _accounting_reconciliation_rows(case_audit: Mapping[str, object] | None) -> list[tuple[object, ...]]:
    payload = _audit_mapping(case_audit, "reconciliation")
    if payload is None:
        return [("账务勾稽", "未提供", "当前快照未直接包含账务勾稽明细")]
    rows = []
    for key, label in (
        ("trial_balance", "试算平衡表"),
        ("journal_to_trial_balance", "序时账至试算平衡表"),
    ):
        result = payload.get(key)
        if not isinstance(result, Mapping):
            continue
        issues = result.get("issues", [])
        items = result.get("items", [])
        if not isinstance(issues, list) or not isinstance(items, list):
            raise WorkpaperError("invalid_workpaper_case_audit")
        status = "通过" if result.get("passed") is True else "未通过"
        detail = f"问题 {len(issues)} 项"
        if key == "journal_to_trial_balance":
            detail = f"匹配 {len(items)} 项；{detail}"
        rows.append((label, status, detail))
    return rows or [("账务勾稽", "未提供", "当前快照未直接包含账务勾稽明细")]


def _structured_reconciliation_rows(
    case_audit: Mapping[str, object] | None,
    *,
    tax_checks: bool,
) -> list[tuple[object, ...]]:
    payload = _audit_mapping(case_audit, "structured_reconciliation")
    if payload is None:
        label = "申报勾稽" if tax_checks else "财报勾稽"
        return [(label, "", "未提供")]
    checks = payload.get("checks", [])
    if not isinstance(checks, list):
        raise WorkpaperError("invalid_workpaper_case_audit")
    rows = []
    for check in checks:
        if not isinstance(check, Mapping):
            raise WorkpaperError("invalid_workpaper_case_audit")
        is_tax_check = check.get("check") == "tax_fact"
        if is_tax_check != tax_checks:
            continue
        subject = check.get("fact_key") or check.get("document_id") or "案件整体"
        if subject == "document_set":
            subject = "案件整体"
        check_code = _as_text(check.get("check"))
        status_code = _as_text(check.get("status"))
        rows.append((
            _RECONCILIATION_CHECK_ZH.get(check_code, check_code),
            subject,
            _RECONCILIATION_STATUS_ZH.get(status_code, status_code),
        ))
    if rows:
        return rows
    label = "申报勾稽" if tax_checks else "财报勾稽"
    return [(label, "", "未提供")]


def _tax_cross_check_rows(
    case_audit: Mapping[str, object] | None,
    delivery_revision: int | None,
) -> list[tuple[object, ...]]:
    payload = _audit_mapping(case_audit, "tax_cross_checks")
    if payload is None:
        return []
    try:
        run = parse_tax_cross_check_payload(payload)
    except ValueError:
        raise WorkpaperError("invalid_workpaper_case_audit") from None
    status_zh = {
        CrossCheckStatus.MATCHED: "金额一致",
        CrossCheckStatus.WITHIN_TOLERANCE: "容差内",
        CrossCheckStatus.DIFFERENCE_FOUND: "发现金额差异",
        CrossCheckStatus.NOT_PERFORMED: "资料不足，未执行",
    }
    rows: list[tuple[object, ...]] = []
    for result in run.results:
        components = result.components or (None,)
        for component in components:
            ledger = component.ledger_amount if component is not None else result.ledger_amount
            comparison = component.comparison_amount if component is not None else result.comparison_amount
            difference = component.difference_amount if component is not None else result.difference_amount
            ratio = component.difference_ratio if component is not None else result.difference_ratio
            refs = component.source_refs if component is not None else result.source_refs
            row_status = (
                compare_amounts(
                    component.ledger_amount, component.comparison_amount
                )[0]
                if component is not None
                else result.status
            )
            rows.append((
                result.check_id,
                component.component_id if component is not None else "整体",
                "" if ledger is None else format(ledger, "f"),
                "" if comparison is None else format(comparison, "f"),
                "" if difference is None else format(difference, "f"),
                "" if ratio is None else format(ratio, "f"),
                format(result.absolute_tolerance, "f"),
                format(result.relative_tolerance, "f"),
                status_zh[row_status],
                "；".join(f"{ref.source_id}:{ref.locator}" for ref in refs),
                "；".join(result.audit_refs),
                run.source_manifest_sha256,
                "" if delivery_revision is None else delivery_revision,
            ))
    return rows


def _build_workbook(
    snapshot: TaxCheckSnapshot,
    *,
    source_manifest=None,
    delivery_revision=None,
    case_audit: Mapping[str, object] | None = None,
):
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
    overview_rows.extend(
        (f"明确限制：{item.code}", item.message_zh)
        for item in snapshot.limitations
    )
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
                _source_status(item.get("status")),
            )
            for source_id, item in sorted(manifest.items())
        ],
    )

    _write_table(
        workbook["抽取与映射"],
        ("来源编号", "文件名", "解析状态", "映射状态"),
        [
            (
                source_id,
                _as_text(item.get("filename")),
                _source_status(item.get("status")),
                "待确认" if item.get("status") == "present_unparsed" else "已绑定",
            )
            for source_id, item in sorted(manifest.items())
        ],
    )

    quality_rows: list[tuple[object, ...]] = []
    for result in snapshot.results:
        quality_rows.append((
            _tax_label(result.tax_id),
            _TAX_STATUS_ZH[result.status],
            ";".join(result.diagnostics),
        ))
    _write_table(workbook["数据质量"], ("税种编号", "状态", "稳定诊断"), quality_rows)

    _write_table(
        workbook["账务勾稽"],
        ("检查项目", "状态", "说明"),
        _accounting_reconciliation_rows(case_audit),
    )
    _write_table(
        workbook["财报勾稽"],
        ("检查项目", "文档或事实", "状态"),
        _structured_reconciliation_rows(case_audit, tax_checks=False),
    )
    _write_table(
        workbook["申报勾稽"],
        ("检查项目", "文档或事实", "状态"),
        _structured_reconciliation_rows(case_audit, tax_checks=True),
    )

    _write_table(
        workbook["账税交叉核对"],
        (
            "检查编号", "组成项目", "账套或报表金额", "申报或对比金额", "差异金额",
            "差异比例", "绝对容差", "相对容差", "状态", "来源引用", "解释及审计引用",
            "来源清单SHA-256", "交付修订号",
        ),
        _tax_cross_check_rows(case_audit, delivery_revision),
    )

    _write_table(
        workbook["税费筛查"],
        ("税种编号", "状态", "状态说明", "规则包", "规则版本"),
        [
            (
                _tax_label(result.tax_id),
                _TAX_STATUS_ZH[result.status],
                _TAX_STATUS_ZH[result.status],
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
            _tax_label(result.tax_id),
            f"{_FORMULA_LABELS_ZH.get(calculation.formula_id, '参数化计算公式')}（{calculation.formula_id}）",
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
                "、".join(_tax_label(tax_id) for tax_id in question.tax_ids),
                question.question_zh,
                _ANSWER_TYPE_ZH.get(question.answer_type, question.answer_type),
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
                _tax_label(result.tax_id),
                source.title,
                source.document_number,
                source.published_on.isoformat(),
                source.url,
                source.verified_on.isoformat() if source.verified_on else "",
            ))
    _write_table(workbook["规则版本"], ("税种编号", "来源标题", "文号", "发布日期", "官方链接", "核验日期"), rule_rows)

    _write_table(
        workbook["报告选取"],
        ("税种编号", "检查状态", "是否进入报告", "选取说明"),
        [
            (
                _tax_label(result.tax_id),
                _TAX_STATUS_ZH[result.status],
                "否" if result.status is TaxStatus.BLOCKED else "是",
                "数据质量阻断" if result.status is TaxStatus.BLOCKED else "按当前快照选取",
            )
            for result in snapshot.results
        ],
    )

    _write_table(
        workbook["运行记录"],
        ("事件类型", "发生时间", "涉及税种", "涉及事实键", "确认摘要"),
        [
            (
                f"{_AUDIT_EVENT_ZH.get(event.event_type, '审计事件')}（{event.event_type}）",
                event.occurred_at,
                "、".join(_tax_label(tax_id) for tax_id in event.tax_ids),
                ",".join(event.fact_keys),
                event.confirmation_digest or "",
            )
            for event in snapshot.audit_events
        ],
    )

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
    case_audit: Mapping[str, object] | None = None,
) -> Path:
    """Write a deterministic, redacted Excel workpaper atomically."""
    snapshot = _require_snapshot(snapshot)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = _build_workbook(
        snapshot,
        source_manifest=source_manifest,
        delivery_revision=delivery_revision,
        case_audit=case_audit,
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
