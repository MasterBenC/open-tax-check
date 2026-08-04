from zipfile import ZipFile

from test_tax_health_reporting import snapshot_with_every_status

from open_tax_check.docx_report import document_xml
from open_tax_check.report_view import ActionItem, ClientIssue, MaterialGap, build_report_view
from open_tax_check.tax_reporting import write_tax_health_report_docx


def _parts(path):
    with ZipFile(path) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist() if name.endswith(".xml")}


def test_docx_contains_customer_report_structure(tmp_path):
    path = write_tax_health_report_docx(
        snapshot_with_every_status(), tmp_path / "report.docx"
    )
    parts = _parts(path)

    assert "word/header1.xml" in parts
    assert "word/footer1.xml" in parts
    assert "word/settings.xml" in parts
    assert parts["word/document.xml"].count("<w:tbl>") >= 3
    assert "PAGE" in parts["word/footer1.xml"]
    assert "专业附录" in parts["word/document.xml"]


def test_docx_has_named_styles_page_break_and_repeating_headers(tmp_path):
    path = write_tax_health_report_docx(
        snapshot_with_every_status(), tmp_path / "report.docx"
    )
    parts = _parts(path)
    styles = parts["word/styles.xml"]
    document = parts["word/document.xml"]

    for style_id in (
        "Title", "Heading1", "Heading2", "Heading3", "BodyText",
        "TableHeader", "IssueState", "Disclaimer",
    ):
        assert f'w:styleId="{style_id}"' in styles
    assert '<w:br w:type="page"/>' in document
    assert "<w:tblHeader/>" in document
    assert '<w:tblW w:w="9360" w:type="dxa"/>' in document
    assert '<w:tblInd w:w="120" w:type="dxa"/>' in document
    assert "health score" not in document.lower()


def test_docx_owner_section_is_substantive_and_has_five_explicit_pages(tmp_path):
    path = write_tax_health_report_docx(
        snapshot_with_every_status(), tmp_path / "report.docx"
    )
    document = _parts(path)["word/document.xml"]
    owner, appendix = document.rsplit("专业附录", 1)

    assert owner.count('<w:br w:type="page"/>') == 5
    assert "本次检查覆盖概览" in owner
    assert "这些结果怎么理解" in owner
    assert "什么情况下需要重新检查" in owner
    assert "试算金额不代表应补税额" in owner
    assert "经复核重点事项" in owner
    assert "不等于发现问题" in owner
    assert appendix


def test_docx_uses_material_fallback_for_approved_issue():
    view = build_report_view(snapshot_with_every_status())
    issue = ClientIssue(
        title_zh="核对事项",
        client_state="重点核实",
        observed_zh="已发现确定性差异。",
        why_it_matters_zh="可能影响申报口径。",
        cannot_conclude_zh="不能据此认定少缴税款。",
        required_materials_zh=(),
        next_action_zh="补充资料后复核。",
        source_kind="reviewed_finding",
    )

    document = document_xml(view.__class__(
        view.report_status_zh, view.executive_summary, (issue,), view.material_gaps,
        view.actions, view.tax_items, view.limitations_zh,
    ))

    assert "需要补什么资料：按核对事项补充可验证资料" in document


def test_docx_bounds_owner_details_and_keeps_overflow_in_appendix():
    view = build_report_view(snapshot_with_every_status())
    issues = tuple(
        ClientIssue(
            title_zh=f"重点事项 {index}", client_state="重点核实",
            observed_zh=f"观察 {index}", why_it_matters_zh="影响申报核对。",
            cannot_conclude_zh="不能据此认定违规。", required_materials_zh=("申报表",),
            next_action_zh="逐项复核。", source_kind="reviewed_finding",
        )
        for index in range(1, 11)
    )
    gaps = tuple(
        MaterialGap(f"资料 {index}", "限制判断。", (), "补齐资料。")
        for index in range(1, 11)
    )
    actions = tuple(ActionItem(index, f"行动 {index}") for index in range(1, 11))
    crowded = view.__class__(
        view.report_status_zh, view.executive_summary, issues, gaps,
        actions, view.tax_items, view.limitations_zh,
    )

    document = document_xml(crowded)
    owner, appendix = document.rsplit("专业附录", 1)

    assert "重点事项 2" in owner
    assert "重点事项 3" not in owner
    assert "其余 8 项重点事项详见专业附录" in owner
    assert "资料 4" in owner
    assert "资料 5" not in owner
    assert "其余 6 项资料缺口详见专业附录" in owner
    assert "行动 4" in owner
    assert "行动 5" not in owner
    assert "重点事项 10" in appendix
    assert "资料 10" in appendix
    assert "行动 10" in appendix


def test_docx_bounds_long_owner_narratives_and_preserves_full_appendix_text():
    view = build_report_view(snapshot_with_every_status())
    long_text = "需要核对的详细业务背景" * 120
    issue = ClientIssue(
        title_zh="超长重点事项", client_state="重点核实",
        observed_zh=long_text, why_it_matters_zh=long_text,
        cannot_conclude_zh=long_text, required_materials_zh=(long_text,),
        next_action_zh=long_text, source_kind="reviewed_finding",
    )
    gap = MaterialGap(long_text, long_text, (), long_text)
    crowded = view.__class__(
        view.report_status_zh, view.executive_summary, (issue,), (gap,),
        (ActionItem(1, long_text),), view.tax_items, (long_text,),
    )

    document = document_xml(crowded)
    owner, appendix = document.rsplit("专业附录", 1)

    assert long_text not in owner
    assert "完整内容见专业附录" in owner
    assert long_text in appendix
