"""Progressive, customer-safe renderers for tax-health-check snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .schemas import to_dict
from .tax_models import Question, RuleSource, TaxCheckSnapshot, TaxResult, TaxStatus
from .tax_registry import TAX_DEFINITIONS


class PdfExportUnavailable(RuntimeError):
    """Raised when the host lacks a supported DOCX-to-PDF renderer."""


_STATUS_LABELS = {
    TaxStatus.CALCULATED: "已完成计算",
    TaxStatus.NOT_APPLICABLE: "经资料确认不适用",
    TaxStatus.NEEDS_EVIDENCE: "待补充资料",
    TaxStatus.NEEDS_LOCAL_POLICY: "待确认当地政策",
    TaxStatus.BLOCKED: "暂时无法判断",
}

_FIXED_DISCLAIMER = (
    "仅供客户内部自查",
    "不属于涉税鉴证、专业税务意见或纳税申报结论",
    "取决于客户提供的资料、确认事实和规则版本",
    "资料或政策不足的项目未形成确定结论；客户自行决定是否申报、调整或另行咨询专业机构",
)

_FORMULA_LABELS_ZH = {
    "generic.base_minus_deductions_times_rate.v1": "计税基础减扣除额后乘税率",
    "generic.base_times_rate.v1": "计税基础乘税率",
    "generic.base_times_rate_minus_credits.v1": "计税基础乘税率后减允许抵扣税额",
    "generic.base_times_rate_minus_quick_deduction.v1": "计税基础乘税率后减速算扣除数",
    "generic.quantity_times_unit_amount.v1": "计税数量乘适用单位税额",
    "generic.base_times_applicable_parameter.v1": "已确认计税依据乘适用计税参数",
    "generic.base_times_rate_minus_confirmed_deduction.v1": "增值额乘确认税率后减确认速算扣除额",
}


def tax_snapshot_payload(snapshot: TaxCheckSnapshot) -> dict[str, object]:
    """Return an allowlisted, JSON-friendly snapshot without customer facts."""
    result_payloads = []
    for result in snapshot.results:
        payload = to_dict(result)
        payload["rule_pack_versions"] = list(result.rule_pack_versions)
        payload["status_label_zh"] = _STATUS_LABELS[result.status]
        result_payloads.append(payload)
    question_payloads = []
    for question in snapshot.questions:
        payload = to_dict(question)
        payload.update({
            "question_zh": question.question_zh,
            "help_zh": question.help_zh,
            "evidence_help_zh": question.evidence_help_zh,
            "choices": to_dict(question.choices),
        })
        question_payloads.append(payload)
    return {
        "schema_version": snapshot.schema_version,
        "revision": snapshot.revision,
        "request": {
            "period": to_dict(snapshot.request.period),
            "jurisdiction": snapshot.request.jurisdiction,
            "input_hashes": list(snapshot.request.input_hashes),
        },
        "results": result_payloads,
        "questions": question_payloads,
        "audit_events": [
            {
                "event_type": event.event_type,
                "tax_ids": list(event.tax_ids),
                "fact_keys": list(event.fact_keys),
                "confirmation_digest": event.confirmation_digest,
            }
            for event in snapshot.audit_events
        ],
    }


def _sources(results: Iterable[TaxResult]) -> tuple[RuleSource, ...]:
    sources: list[RuleSource] = []
    for result in results:
        for source in result.rule_sources:
            if source not in sources:
                sources.append(source)
    return tuple(sources)


def _format_rate_zh(rate: str) -> str:
    try:
        percentage = Decimal(rate) * Decimal("100")
    except InvalidOperation:
        return rate
    rendered = format(percentage, "f").rstrip("0").rstrip(".")
    return f"{rendered or '0'}%"


def _result_lines(result: TaxResult) -> list[str]:
    lines = [
        f"- 检查结果: {_STATUS_LABELS[result.status]}",
    ]
    if result.calculation is not None:
        calculation = result.calculation
        formula_label = _FORMULA_LABELS_ZH.get(calculation.formula_id, "参数化计算公式")
        deductions_label = {
            "generic.base_times_rate_minus_credits.v1": "允许抵扣进项税额",
            "generic.base_times_rate_minus_quick_deduction.v1": "速算扣除数",
            "generic.base_times_rate_minus_confirmed_deduction.v1": "确认速算扣除额",
        }.get(calculation.formula_id, "扣除额")
        if calculation.rate_selector_label_zh is not None:
            lines.append(f"- 适用税目: {calculation.rate_selector_label_zh}")
        lines.extend(
            (
                f"- 计算公式: {formula_label}（{calculation.formula_id}）",
                f"- \u8ba1\u7a0e\u57fa\u7840: {calculation.tax_base}",
                f"- {deductions_label}: {calculation.deductions}",
                f"- \u51c0\u8ba1\u7a0e\u57fa\u7840: {calculation.net_base}",
                (
                    f"- 适用单位税额: {calculation.rate}"
                    if calculation.formula_id
                    == "generic.quantity_times_unit_amount.v1"
                    else f"- 适用计税参数: {calculation.rate}"
                    if calculation.formula_id
                    == "generic.base_times_applicable_parameter.v1"
                    else f"- \u9002\u7528\u7a0e\u7387: {_format_rate_zh(calculation.rate)}"
                ),
                f"- \u8ba1\u7b97\u91d1\u989d: {calculation.amount}",
                f"- \u8ba1\u7b97\u8bc1\u636e: {', '.join(calculation.evidence_refs)}",
            )
        )
    for gap in result.gaps:
        if gap.reason_code == "missing_local_policy":
            lines.append("- 需要补充适用的当地政策参数")
        else:
            lines.append("- 需要补充与该事项相关的资料")
    for source in result.rule_sources:
        lines.append(f"- 适用规则来源: [{source.title}]({source.url})（发布于 {source.published_on.isoformat()}）")
    return lines


def _question_lines(question: Question) -> list[str]:
    tax_names = {
        definition.tax_id: definition.name_zh for definition in TAX_DEFINITIONS
    }
    lines = [
        f"### {question.question_zh or '请补充相关资料'}",
        f"- 涉及税种: {', '.join(tax_names.get(tax_id, '相关税种') for tax_id in question.tax_ids)}",
    ]
    if question.help_zh:
        lines.append(f"- 说明: {question.help_zh}")
    if question.evidence_help_zh:
        lines.append(f"- 资料提示: {question.evidence_help_zh}")
    return lines


def render_tax_health_report(snapshot: TaxCheckSnapshot) -> str:
    """Render one progressive report from active snapshot questions only."""
    results_by_tax_id = {result.tax_id: result for result in snapshot.results}
    counts = {status: sum(result.status is status for result in snapshot.results) for status in TaxStatus}
    request = snapshot.request
    lines = [
        "# 税务健康检查报告",
        "",
        "仅供客户内部自查。",
        "",
        "## 检查范围",
        f"- 检查期间: {request.period.start.isoformat()} 至 {request.period.end.isoformat()}",
        f"- 适用地区: {'中国大陆' if request.jurisdiction == 'CN' else request.jurisdiction}",
        f"- 修订版本: {snapshot.revision}",
        f"- 输入哈希数量: {len(request.input_hashes)}",
        "",
        "## 状态汇总",
    ]
    lines.extend(f"- {_STATUS_LABELS[status]}: {counts[status]}" for status in TaxStatus)
    lines.extend(("", "## 税种检查结果"))
    for definition in TAX_DEFINITIONS:
        lines.extend(("", f"### {definition.name_zh}"))
        result = results_by_tax_id.get(definition.tax_id)
        if result is None:
            lines.append("- 检查结果: 暂时无法判断")
        else:
            lines.extend(_result_lines(result))

    lines.extend(("", "## 待确认客户问题"))
    if snapshot.questions:
        for question in snapshot.questions:
            lines.extend(("", *_question_lines(question)))
    else:
        lines.append("- 当前没有待确认问题。")

    lines.extend(("", "## 规则来源与版本"))
    rendered_sources = _sources(snapshot.results)
    if rendered_sources:
        for source in rendered_sources:
            lines.append(f"- [{source.title}]({source.url})（发布日: {source.published_on.isoformat()}）")
    else:
        lines.append("- 当前快照没有可用规则来源。")

    lines.extend(("", "## 局限性与声明"))
    lines.extend(f"- {line}" for line in _FIXED_DISCLAIMER)
    return "\n".join(lines) + "\n"


def _write_text_atomic(path: Path, content: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return path


def write_tax_health_report(snapshot: TaxCheckSnapshot, path: Path) -> Path:
    """Atomically write the Chinese customer self-check Markdown report."""
    return _write_text_atomic(Path(path), render_tax_health_report(snapshot))


def write_tax_health_snapshot(snapshot: TaxCheckSnapshot, path: Path) -> Path:
    """Atomically write the allowlisted machine snapshot payload as JSON."""
    content = json.dumps(
        tax_snapshot_payload(snapshot), ensure_ascii=False, sort_keys=True
    ) + "\n"
    return _write_text_atomic(Path(path), content)


def _docx_xml_text(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _docx_paragraphs(markdown: str) -> str:
    paragraphs: list[str] = []
    for line in markdown.splitlines():
        if not line.strip():
            continue
        style = "Normal"
        text = line
        if line.startswith("### "):
            style, text = "Heading3", line[4:]
        elif line.startswith("## "):
            style, text = "Heading2", line[3:]
        elif line.startswith("# "):
            style, text = "Title", line[2:]
        elif line.startswith("- "):
            style, text = "ListParagraph", line[2:]
        text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
        num_pr = '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if style == "ListParagraph" else ""
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/>{num_pr}</w:pPr>'
            f'<w:r><w:t xml:space="preserve">{_docx_xml_text(text)}</w:t></w:r></w:p>'
        )
    return "".join(paragraphs)


def _docx_parts(markdown: str) -> dict[str, str]:
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    document_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:pPr><w:spacing w:before="0" w:after="240"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:b/><w:sz w:val="40"/><w:color w:val="0B2545"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/><w:pPr><w:spacing w:before="320" w:after="120"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:b/><w:sz w:val="32"/><w:color w:val="2E74B5"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="Heading 2"/><w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="280" w:after="100"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:b/><w:sz w:val="26"/><w:color w:val="2E74B5"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="Heading 3"/><w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="240" w:after="80"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:b/><w:sz w:val="24"/><w:color w:val="1F4D78"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:pPr><w:ind w:left="720" w:hanging="360"/><w:spacing w:after="80"/></w:pPr></w:style>
</w:styles>'''
    numbering = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>'''
    document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{_docx_paragraphs(markdown)}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708"/></w:sectPr></w:body></w:document>'''
    return {"[Content_Types].xml": content_types, "_rels/.rels": root_rels, "word/_rels/document.xml.rels": document_rels, "word/styles.xml": styles, "word/numbering.xml": numbering, "word/document.xml": document}


def write_tax_health_report_docx(snapshot: TaxCheckSnapshot, path: Path) -> Path:
    """Write a deterministic, styled DOCX rendition of the customer report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary_path = Path(handle.name)
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in _docx_parts(render_tax_health_report(snapshot)).items():
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                archive.writestr(info, content.encode("utf-8"))
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return path


def write_tax_health_report_pdf(snapshot: TaxCheckSnapshot, path: Path) -> Path:
    """Convert the deterministic DOCX report to PDF using an explicit host renderer."""
    renderer = shutil.which("soffice") or shutil.which("libreoffice")
    if renderer is None:
        raise PdfExportUnavailable("pdf_renderer_unavailable")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="open-tax-check-pdf-") as directory:
        temporary_dir = Path(directory)
        docx_path = temporary_dir / "tax-health-report.docx"
        write_tax_health_report_docx(snapshot, docx_path)
        try:
            completed = subprocess.run(
                [renderer, "--headless", "--convert-to", "pdf", "--outdir", str(temporary_dir), str(docx_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except subprocess.TimeoutExpired as error:
            raise PdfExportUnavailable("pdf_render_timeout") from error
        rendered_path = temporary_dir / "tax-health-report.pdf"
        if completed.returncode != 0 or not rendered_path.is_file():
            raise PdfExportUnavailable("pdf_render_failed")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=path.parent, suffix=".tmp", delete=False) as handle:
                temporary_path = Path(handle.name)
                handle.write(rendered_path.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
    return path
