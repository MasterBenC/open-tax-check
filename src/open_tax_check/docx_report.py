"""Deterministic OOXML builder for the structured customer report."""

from __future__ import annotations

from xml.sax.saxutils import escape

from .report_view import ReportView


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _p(text: str, style: str = "BodyText", *, page_break: bool = False) -> str:
    br = '<w:r><w:br w:type="page"/></w:r>' if page_break else ""
    run = f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>' if text else ""
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{br}{run}</w:p>'


def _cell(text: str, width: int, *, header: bool = False) -> str:
    shade = '<w:shd w:fill="E8EEF5"/>' if header else ""
    style = "TableHeader" if header else "BodyText"
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}'
        '<w:vAlign w:val="center"/></w:tcPr>' + _p(text, style) + '</w:tc>'
    )


def _table(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...], widths: tuple[int, ...]) -> str:
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
    header = "".join(_cell(text, width, header=True) for text, width in zip(headers, widths))
    body = "".join(
        '<w:tr>' + "".join(_cell(text, width) for text, width in zip(row, widths)) + '</w:tr>'
        for row in rows
    )
    return (
        '<w:tbl><w:tblPr><w:tblW w:w="9360" w:type="dxa"/>'
        '<w:tblInd w:w="120" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
        '<w:tblBorders><w:top w:val="single" w:sz="4" w:color="B8C2CC"/>'
        '<w:left w:val="single" w:sz="4" w:color="B8C2CC"/>'
        '<w:bottom w:val="single" w:sz="4" w:color="B8C2CC"/>'
        '<w:right w:val="single" w:sz="4" w:color="B8C2CC"/>'
        '<w:insideH w:val="single" w:sz="4" w:color="D9E0E7"/>'
        '<w:insideV w:val="single" w:sz="4" w:color="D9E0E7"/></w:tblBorders>'
        '<w:tblCellMar><w:top w:w="80" w:type="dxa"/><w:start w:w="120" w:type="dxa"/>'
        '<w:bottom w:w="80" w:type="dxa"/><w:end w:w="120" w:type="dxa"/></w:tblCellMar>'
        f'</w:tblPr><w:tblGrid>{grid}</w:tblGrid>'
        f'<w:tr><w:trPr><w:tblHeader/></w:trPr>{header}</w:tr>{body}</w:tbl>'
    )


def document_xml(view: ReportView) -> str:
    summary = view.executive_summary
    parts = [
        _p("税务健康检查报告", "Title"),
        _p(summary.entity_name or "企业税务健康自查", "Subtitle"),
        _p(view.report_status_zh, "IssueState"),
        _p("本报告仅供客户内部自查", "Disclaimer"),
        _p("", page_break=True),
        _p("目录", "Heading1"),
        _p("1. 管理层摘要"), _p("2. 老板先看这些事"), _p("3. 待补资料及影响"),
        _p("4. 建议行动顺序"), _p("5. 检查范围和限制"), _p("6. 专业附录"),
        _p("", page_break=True),
        _p("管理层摘要", "Heading1"),
        _table(("项目", "当前情况"), (
            ("报告状态", summary.status_zh),
            ("重点核实事项", f"{summary.issue_count} 项"),
            ("待补资料事项", f"{summary.material_gap_count} 项"),
            ("待核对试算", f"{summary.trial_amount_count} 项"),
        ), (2400, 6960)),
        _p(summary.amount_disclaimer_zh, "Disclaimer"),
        _p("老板先看这些事", "Heading1"),
    ]
    if view.issues:
        for issue in view.issues:
            parts.extend((
                _p(issue.title_zh, "Heading2"), _p(issue.client_state, "IssueState"),
                _p(f"发现了什么：{issue.observed_zh}"),
                _p(f"为什么需要关注：{issue.why_it_matters_zh}"),
                _p(f"目前不能下什么结论：{issue.cannot_conclude_zh}"),
                _p("需要补什么资料：" + "、".join(issue.required_materials_zh)),
                _p(f"建议下一步：{issue.next_action_zh}"),
            ))
    else:
        parts.append(_p("当前没有经复核批准后进入客户报告的重点事项。"))
    parts.extend((_p("待补资料及影响", "Heading1"), _table(
        ("待补资料", "影响", "补充方式"),
        tuple((item.material_zh, item.impact_zh, item.next_action_zh) for item in view.material_gaps)
        or (("当前无待补资料", "-", "-"),),
        (2800, 3280, 3280),
    ), _p("建议行动顺序", "Heading1")))
    parts.extend(_p(f"{item.priority}. {item.action_zh}") for item in view.actions)
    parts.extend((_p("检查范围和限制", "Heading1"),))
    parts.extend(_p(item, "Disclaimer") for item in view.limitations_zh)
    parts.extend((_p("", page_break=True), _p("专业附录", "Heading1"), _table(
        ("税费项目", "客户状态", "试算/资料缺口"),
        tuple((
            item.name_zh, item.client_state,
            (f"试算金额 {item.trial_amount}" if item.trial_amount is not None else f"资料缺口 {item.gap_count} 项"),
        ) for item in view.tax_items) or (("当前无税费结果", "待补资料", "-"),),
        (2800, 2800, 3760),
    )))
    body = "".join(parts)
    sect = (
        '<w:sectPr><w:headerReference w:type="default" r:id="rId3"/>'
        '<w:footerReference w:type="default" r:id="rId4"/>'
        '<w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" '
        'w:bottom="1440" w:left="1440" w:header="708" w:footer="708"/></w:sectPr>'
    )
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{body}{sect}</w:body></w:document>'


def styles_xml() -> str:
    styles = (
        ("Normal", "Normal", 22, "1F2933", 0, 120, False),
        ("BodyText", "Body Text", 22, "1F2933", 0, 120, False),
        ("Title", "Title", 56, "0B2545", 0, 160, True),
        ("Subtitle", "Subtitle", 28, "52606D", 0, 240, False),
        ("Heading1", "Heading 1", 32, "2E74B5", 320, 160, True),
        ("Heading2", "Heading 2", 26, "2E74B5", 240, 120, True),
        ("Heading3", "Heading 3", 24, "1F4D78", 160, 80, True),
        ("TableHeader", "Table Header", 20, "0B2545", 0, 40, True),
        ("IssueState", "Issue State", 22, "7A5A00", 0, 100, True),
        ("Disclaimer", "Disclaimer", 19, "52606D", 80, 80, False),
    )
    rendered = []
    for style_id, name, size, color, before, after, bold in styles:
        rendered.append(
            f'<w:style w:type="paragraph" w:styleId="{style_id}"'
            + (' w:default="1"' if style_id == "Normal" else '')
            + f'><w:name w:val="{name}"/><w:pPr><w:keepNext/>'
            f'<w:spacing w:before="{before}" w:after="{after}" w:line="264" w:lineRule="auto"/></w:pPr>'
            '<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/>'
            + ('<w:b/>' if bold else '') + f'<w:color w:val="{color}"/><w:sz w:val="{size}"/></w:rPr></w:style>'
        )
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W}">' + "".join(rendered) + '</w:styles>'


def header_xml(view: ReportView) -> str:
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr xmlns:w="{W}">{_p("税务健康检查报告 | " + view.report_status_zh, "Disclaimer")}</w:hdr>'


def footer_xml(view: ReportView) -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr xmlns:w="{W}"><w:p><w:pPr><w:jc w:val="right"/></w:pPr>'
        '<w:r><w:t>第 </w:t></w:r><w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple>'
        '<w:r><w:t> 页</w:t></w:r></w:p></w:ftr>'
    )


def build_docx_parts(view: ReportView) -> dict[str, bytes]:
    content_types = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/><Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/><Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/></Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'''
    document_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/></Relationships>'''
    settings = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings xmlns:w="{W}"><w:updateFields w:val="true"/></w:settings>'
    parts = {
        "[Content_Types].xml": content_types, "_rels/.rels": root_rels,
        "word/_rels/document.xml.rels": document_rels,
        "word/document.xml": document_xml(view), "word/styles.xml": styles_xml(),
        "word/settings.xml": settings, "word/header1.xml": header_xml(view),
        "word/footer1.xml": footer_xml(view),
    }
    return {name: content.encode("utf-8") for name, content in parts.items()}
