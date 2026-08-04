"""Deterministic OOXML builder for the structured customer report."""

from __future__ import annotations

from xml.sax.saxutils import escape

from .report_view import ReportView


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
OWNER_ISSUE_LIMIT = 2
OWNER_GAP_LIMIT = 4
OWNER_ACTION_LIMIT = 4
OWNER_LIMITATION_LIMIT = 4


def _owner_text(text: str, limit: int) -> str:
    """Bound owner-facing prose while keeping the full value in the appendix."""

    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "……（完整内容见专业附录）"


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
    state_counts = {
        state: sum(item.client_state == state for item in view.tax_items)
        for state in ("重点核实", "待补资料", "账务勾稽提示", "本次范围内未见异常")
    }
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
            ("经复核重点事项", f"{summary.issue_count} 项"),
            ("待补资料事项", f"{summary.material_gap_count} 项"),
            ("待核对试算", f"{summary.trial_amount_count} 项"),
        ), (2400, 6960)),
        _p(summary.amount_disclaimer_zh, "Disclaimer"),
        _p("本次检查覆盖概览", "Heading2"),
        _table(("客户可读状态", "税费项目数", "应当如何处理"), tuple(
            (state, f"{count} 项", {
                "重点核实": "存在试算或核对结果，需结合申报资料复核；不等于发现问题。",
                "待补资料": "补齐资料后再形成判断。",
                "账务勾稽提示": "核对账簿、申报和试算口径。",
                "本次范围内未见异常": "仅表示已执行程序未见异常，不代表无风险。",
            }[state])
            for state, count in state_counts.items()
        ), (2600, 1600, 5160)),
        _p("这些结果怎么理解", "Heading2"),
        _p("报告只陈述已执行程序和已取得证据支持的结果。缺少资料、当地政策或客户确认时，相关税种保持待补状态，不会被写成不适用或无风险。"),
        _p("试算用于帮助定位需要核对的口径。正式申报仍应以申报表、完税凭证、合同和适用政策为准。", "Disclaimer"),
        _p("", page_break=True),
        _p("老板先看这些事", "Heading1"),
    ]
    if view.issues:
        for issue in view.issues[:OWNER_ISSUE_LIMIT]:
            parts.extend((
                _p(_owner_text(issue.title_zh, 48), "Heading2"),
                _p(issue.client_state, "IssueState"),
                _p(f"发现了什么：{_owner_text(issue.observed_zh, 100)}"),
                _p(f"为什么需要关注：{_owner_text(issue.why_it_matters_zh, 100)}"),
                _p(f"目前不能下什么结论：{_owner_text(issue.cannot_conclude_zh, 100)}"),
                _p("需要补什么资料：" + _owner_text((
                    "、".join(issue.required_materials_zh)
                    if issue.required_materials_zh else "按核对事项补充可验证资料"
                ), 100)),
                _p(f"建议下一步：{_owner_text(issue.next_action_zh, 100)}"),
            ))
        if len(view.issues) > OWNER_ISSUE_LIMIT:
            parts.append(_p(
                f"其余 {len(view.issues) - OWNER_ISSUE_LIMIT} 项重点事项详见专业附录。",
                "Disclaimer",
            ))
    else:
        parts.append(_p("当前没有经复核批准后进入客户报告的重点事项。"))
    parts.extend((_p("待补资料及影响", "Heading1"), _table(
        ("待补资料", "影响", "补充方式"),
        tuple((
            _owner_text(item.material_zh, 70),
            _owner_text(item.impact_zh, 80),
            _owner_text(item.next_action_zh, 80),
        ) for item in view.material_gaps[:OWNER_GAP_LIMIT])
        or (("当前无待补资料", "-", "-"),),
        (2800, 3280, 3280),
    ), _p("建议行动顺序", "Heading1")))
    if len(view.material_gaps) > OWNER_GAP_LIMIT:
        parts.append(_p(
            f"其余 {len(view.material_gaps) - OWNER_GAP_LIMIT} 项资料缺口详见专业附录。",
            "Disclaimer",
        ))
    parts.extend(
        _p(f"{item.priority}. {_owner_text(item.action_zh, 120)}")
        for item in view.actions[:OWNER_ACTION_LIMIT]
    )
    if len(view.actions) > OWNER_ACTION_LIMIT:
        parts.append(_p(
            f"其余 {len(view.actions) - OWNER_ACTION_LIMIT} 项行动详见专业附录。",
            "Disclaimer",
        ))
    parts.extend((_p("", page_break=True), _p("检查范围和限制", "Heading1"),))
    parts.extend(
        _p(_owner_text(item, 140), "Disclaimer")
        for item in view.limitations_zh[:OWNER_LIMITATION_LIMIT]
    )
    if len(view.limitations_zh) > OWNER_LIMITATION_LIMIT:
        parts.append(_p(
            f"其余 {len(view.limitations_zh) - OWNER_LIMITATION_LIMIT} 项限制详见专业附录。",
            "Disclaimer",
        ))
    if not view.limitations_zh:
        parts.append(_p("本报告仅覆盖已提供资料和已执行程序，不构成鉴证、税务意见、申报结论或税务机关认定。", "Disclaimer"))
    parts.extend((
        _p("什么情况下需要重新检查", "Heading2"),
        _p("发生重大资产交易、股权变化、跨地区经营、新增不动产或重要合同后。"),
        _p("取得补充申报表、完税凭证、合同、发票或其他能够改变现有判断的资料后。"),
        _p("适用政策、地方执行口径、计税基础或税率发生变化后。"),
        _p("进入新的申报年度，或企业希望按相同口径进行下一轮持续复查时。"),
        _p("重要边界", "Heading2"),
        _p("本报告用于企业内部税务健康自查。它不替代纳税申报、专项鉴证、法律或税务意见，也不代表税务机关对任何事项的认定。", "Disclaimer"),
    ))
    parts.extend((_p("", page_break=True), _p("专业附录", "Heading1"), _table(
        ("税费项目", "客户状态", "试算/资料缺口"),
        tuple((
            item.name_zh, item.client_state,
            (f"试算金额 {item.trial_amount}" if item.trial_amount is not None else f"资料缺口 {item.gap_count} 项"),
        ) for item in view.tax_items) or (("当前无税费结果", "待补资料", "-"),),
        (2800, 2800, 3760),
    )))
    if view.issues:
        parts.append(_p("全部经复核重点事项", "Heading2"))
        for issue in view.issues:
            parts.extend((
                _p(issue.title_zh, "Heading3"),
                _p(f"状态：{issue.client_state}"),
                _p(f"发现了什么：{issue.observed_zh}"),
                _p(f"为什么需要关注：{issue.why_it_matters_zh}"),
                _p(f"目前不能下什么结论：{issue.cannot_conclude_zh}"),
                _p("需要补什么资料：" + (
                    "、".join(issue.required_materials_zh)
                    if issue.required_materials_zh else "按核对事项补充可验证资料"
                )),
                _p(f"建议下一步：{issue.next_action_zh}"),
            ))
    if view.material_gaps:
        parts.extend((_p("全部资料缺口", "Heading2"), _table(
            ("待补资料", "影响", "补充方式"),
            tuple((item.material_zh, item.impact_zh, item.next_action_zh) for item in view.material_gaps),
            (2800, 3280, 3280),
        )))
    if view.actions:
        parts.append(_p("全部行动清单", "Heading2"))
        parts.extend(_p(f"{item.priority}. {item.action_zh}") for item in view.actions)
    if view.limitations_zh:
        parts.append(_p("全部检查限制", "Heading2"))
        parts.extend(_p(item, "Disclaimer") for item in view.limitations_zh)
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
