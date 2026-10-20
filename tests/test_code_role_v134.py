"""代码中的中文回退字体不能冒充正文基础字级。"""

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from knowledge_product_workflow.visual_audit import _measure_page, _page_findings


def _painted_page(tmp_path, *, tagged_code):
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    # ASCII glyphs intentionally use the CJK font role name: this is a PDF
    # operator/measurement unit test, not a visual Chinese-font fixture.
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/PingFangSC-Regular")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
        DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    opening, closing = ("/KPCode BMC", "EMC") if tagged_code else ("", "")
    stream.set_data(("BT /F1 12 Tf 1 0 0 1 40 700 Tm (" + "body" * 8 + ") Tj ET\n"
        + opening + "\nBT /F1 11 Tf 1 0 0 1 40 650 Tm (" + "code" * 24 + ") Tj ET\n" + closing).encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    path = tmp_path / ("tagged.pdf" if tagged_code else "untagged.pdf")
    writer.write(path)
    return PdfReader(path).pages[0]


def test_marked_chinese_fallback_is_code_not_body(tmp_path):
    metric = _measure_page(_painted_page(tmp_path, tagged_code=True), 3, 12)
    assert metric.body_role_base_pt["cjk"] == 12
    assert metric.font_role_medians_pt["monospace"] == 11
    assert "PDF_BODY_ROLE_SIZE_DRIFT" not in {item.code for item in _page_findings(metric)}


def test_untagged_small_body_still_blocks(tmp_path):
    metric = _measure_page(_painted_page(tmp_path, tagged_code=False), 3, 12)
    assert "PDF_BODY_ROLE_SIZE_DRIFT" in {item.code for item in _page_findings(metric)}


def test_native_chinese_code_carries_painted_role_without_changing_text(tmp_path):
    from knowledge_product_workflow.detail_constraints import DetailConstraints
    from knowledge_product_workflow.pdf_fonts import probe_preferred_fonts
    from knowledge_product_workflow.presentation import PresentationOverrides
    from knowledge_product_workflow.publishing import create_quarto_source, render_pdf
    from knowledge_product_workflow.quarto import resolve_quarto
    from knowledge_product_workflow.workflows.product_brief import create_product_brief
    from knowledge_product_workflow.workspace import Workspace

    if not resolve_quarto().available or not all(value["available"] for value in probe_preferred_fonts(tmp_path).values()):
        pytest.skip("需要已有原生排版与指定字体，不安装依赖")
    workspace = Workspace.create(tmp_path, "code-role")
    create_product_brief(workspace, "中文代码角色核验", DetailConstraints(delivery_format="pdf"),
        presentation=PresentationOverrides(cover_summary_zh="区分正文与代码回退中文的实际基础字级。"))
    body = "# 1. 角色核验\n\n这是普通中文正文。\n\n\\newpage\n\n```text\n现有资料未说明知识库资料的保留期限，因此目前无法确定。\n请补充知识库资料保留或归档规定，再核对具体期限。\n```\n"
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text(body, encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
    page = PdfReader(pdf).pages[-1]
    assert b"/KPCode BMC" in page.get_contents().get_data()
    metric = _measure_page(page, 4, 12 * 72 / 72.27)
    assert "cjk" not in metric.body_role_base_pt
    assert 10 < metric.font_role_medians_pt["monospace"] < 12
    assert "现有资料未说明知识库资料的保留期限" in page.extract_text()
