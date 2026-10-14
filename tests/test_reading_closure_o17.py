"""受控实际PDF探针：原代码不变，短引入与引用对象保持邻接。"""
from pypdf import PdfReader

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.presentation import PresentationOverrides
from knowledge_product_workflow.publishing import create_quarto_source, render_pdf
from knowledge_product_workflow.visual_audit import VisualPageMetric, _closure_findings
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_interior_thin_chapter_tail_is_located_without_fill_quota():
    pages = [VisualPageMetric(page_number=i, page_type="body", width_pt=595.28, height_pt=841.89,
        text_characters=1200, body_height_fraction=.65) for i in range(1, 6)]
    pages[2].body_height_fraction = .04
    pages[2].text_characters = 95
    findings = _closure_findings(pages)
    assert any(item.code == "PDF_POSSIBLE_ORPHAN_CLOSURE" and "第 3 页" in item.location for item in findings)
    assert all(item.severity.value == "warning" for item in findings)


def test_actual_quote_introduction_and_object_stay_on_same_page(tmp_path):
    workspace = Workspace.create(tmp_path, "quote-closure")
    create_product_brief(workspace, "练习对象邻接", DetailConstraints(delivery_format="pdf"),
        presentation=PresentationOverrides(cover_summary_zh="用固定内容检查条件与待改对象的邻接。"))
    sections = []
    markers = []
    for count in (9, 10, 11, 12):
        filler = ("给定条件与需要执行的操作应能在同一阅读位置核对。" * 4 + "\n\n") * count
        intro, quote = f"INTRO{count}", f"QUOTE{count}"
        sections.append(f"# {count}. 固定条件\n\n" + filler +
            f"输入条件仍为既定材料，现有草稿为 {intro}：\n\n> {quote}：这是需要读者修改的完整对象。\n\n完成后逐项核对既定边界。\n")
        markers.append((intro, quote))
    body = "\n".join(sections)
    md = workspace.root / "delivery/book.md"
    md.write_text(body, encoding="utf-8")
    qmd = create_quarto_source(workspace, md, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
    pages = ["".join((page.extract_text() or "").split()) for page in PdfReader(pdf).pages]
    for intro, quote in markers:
        assert any(intro in text and quote in text for text in pages), f"条件与对象跨页：{intro}；{pdf}"
    assert md.read_text(encoding="utf-8") == body
    assert all(quote in qmd.read_text(encoding="utf-8") for _, quote in markers)


def test_actual_long_code_never_leaves_a_single_line_continuation(tmp_path):
    workspace = Workspace.create(tmp_path, "code-edges")
    create_product_brief(workspace, "代码尾上下文", DetailConstraints(delivery_format="pdf"),
        presentation=PresentationOverrides(cover_summary_zh="原始代码保持不变，核对长代码分页后的末尾上下文。"))
    sections, markers = [], []
    for count in (39, 40, 41, 42, 43, 44):
        lines = [f'print("EDGE{count}LINE{i:03d}")' for i in range(count)]
        sections.append(f"# {count}. 固定代码\n\n运行下面完整代码并核对结果：\n\n```python\n" +
                        "\n".join(lines) + "\n```\n")
        markers.append((lines[-2], lines[-1]))
    body = "\n".join(sections)
    md = workspace.root / "delivery/book.md"
    md.write_text(body, encoding="utf-8")
    qmd = create_quarto_source(workspace, md, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
    pages = ["".join((page.extract_text() or "").split()) for page in PdfReader(pdf).pages]
    for previous, final in markers:
        assert any(previous in text and final in text for text in pages), f"代码单行尾：{final}；{pdf}"
    assert md.read_text(encoding="utf-8") == body
