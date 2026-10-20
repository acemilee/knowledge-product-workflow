"""原生排版边界扫描：短引入必须与其后续操作保持阅读连续。"""

import pytest
from pypdf import PdfReader

from knowledge_product_workflow.d2 import render_svg_with_browser, resolve_diagram_browser
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.pdf_fonts import probe_preferred_fonts
from knowledge_product_workflow.presentation import PresentationOverrides
from knowledge_product_workflow.publishing import create_quarto_source, render_pdf
from knowledge_product_workflow.quarto import resolve_quarto
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_short_introductions_following_code_and_lists_stay_together(tmp_path):
    browser = resolve_diagram_browser()
    if not browser.available or not resolve_quarto().available or not all(v["available"] for v in probe_preferred_fonts(tmp_path).values()):
        pytest.skip("原生环境不可用，未执行不等于成页通过")
    workspace = Workspace.create(tmp_path, "semantic-groups")
    create_product_brief(workspace, "短操作阅读连续性", DetailConstraints(delivery_format="pdf"),
                         presentation=PresentationOverrides(cover_summary_zh="原生检查操作引入、命令与核对清单的连续呈现。"))
    figure = workspace.root / "assets/figures/continuity.pdf"
    figure.parent.mkdir(parents=True, exist_ok=True)
    svg = figure.with_suffix(".svg")
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="360" height="200"><rect x="10" y="10" width="340" height="180" rx="6" fill="#F8EBE2" stroke="#B65E2B"/></svg>', encoding="utf-8")
    render_svg_with_browser(browser.path, svg, figure, figure.with_suffix(".png"))
    pieces = []
    pairs = []
    for kind in ("CODE", "LIST", "MATH", "FIGURE", "CLOSING"):
        for count in (9, 10, 11, 12):
            number = len(pairs) + 1
            intro, content = f"INTRO_{kind}_{count}", f"CONTENT_{kind}_{count}"
            filler = ("这是原生分页探针的固定讲解段，说明给定输入与预期输出的关系。" * 3 + "\n\n") * count
            if kind == "CODE":
                block = f"```sh\npython {content}.py\n```\n"
            elif kind == "LIST":
                block = f"- {content}：输入正确。\n- 输出一致。\n- 结果可复核。\n"
            elif kind == "MATH":
                content = f"CONTENTMATH{count}"
                block = "$$\n\\operatorname{" + content + "}=\\frac{1+2}{3}=1\n$$\n"
            elif kind == "FIGURE":
                block = f"![{content}](assets/figures/continuity.pdf){{fig-pos=\"H\"}}\n"
            else:
                block = ("1. 核对所需信息。\n2. 对照全部资料。\n3. 保留未知与证据边界。\n\n"
                         "根据上述判断，可回答：\n\n```text\n" + content + "：当前资料不足。\n请补充保留或归档规定，再核对具体期限。\n```\n")
            pieces.append(f"# {number}. 边界{number}\n\n" + filler + f"完成讲解后，执行{intro}所示操作：\n\n" + block)
            pairs.append((intro, content))
    body = "\n\n".join(pieces)
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text(body, encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
    pages = ["".join((p.extract_text() or "").split()) for p in PdfReader(pdf).pages]
    split = []
    for intro, content in pairs:
        intro_pages = {i for i, text in enumerate(pages) if intro in text}
        content_pages = {i for i, text in enumerate(pages) if content in text}
        assert intro_pages and content_pages
        if not intro_pages & content_pages:
            split.append({"intro": intro, "intro_pages": sorted(intro_pages), "content_pages": sorted(content_pages)})
    assert not split, f"短操作引入与后续对象跨页：{split}；实际PDF={pdf}"
