import re
import struct
import zlib
from pathlib import Path

import pytest
from pypdf import PdfReader

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.pdf_preview import (
    png_has_visible_ink,
    render_pdf_first_page,
    resolve_pdftoppm,
)
from knowledge_product_workflow.presentation import (
    PRESENTATION_PROFILE_ID,
    PublicationUpdateInput,
    apply_publication_update,
    load_or_create_publication_profile,
    preview_publication_update,
)
from knowledge_product_workflow.publishing import (
    count_pdf_bookmarks,
    count_pdf_external_links,
    count_pdf_pages,
    create_quarto_source,
    missing_pdf_source_contracts,
    render_pdf,
)
from knowledge_product_workflow.quarto import resolve_quarto
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def _workflow_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + kind + data + struct.pack(
            "!I", zlib.crc32(kind + data) & 0xFFFFFFFF
        )

    width, height = 640, 360
    boxes = ((48, 116, 190, 244), (250, 116, 392, 244), (452, 116, 594, 244))
    background = (255, 252, 250)
    fill = (246, 234, 230)
    ink = (32, 33, 36)
    brand = (199, 93, 69)
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            color = background
            for left, top, right, bottom in boxes:
                if left <= x <= right and top <= y <= bottom:
                    color = ink if x in (left, right) or y in (top, bottom) else fill
            if 190 < x < 250 and 176 <= y <= 184:
                color = brand
            if 392 < x < 452 and 176 <= y <= 184:
                color = brand
            if x in (242, 444) and 168 <= y <= 192:
                color = brand
            rows.extend(color)

    header = struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = zlib.compress(bytes(rows))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def test_quarto_source_binds_the_complete_brand_visual_contract(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(
        workspace,
        "AI Agent 工程实践",
        DetailConstraints(target_words=1, delivery_format="pdf"),
    )
    markdown_path = workspace.root / "delivery" / "book.md"
    markdown_path.write_text("# AI Agent 工程实践\n\n正文。\n", encoding="utf-8")

    qmd_path = create_quarto_source(workspace, markdown_path)

    qmd = qmd_path.read_text(encoding="utf-8")
    delivery = workspace.root / "delivery"
    assert PRESENTATION_PROFILE_ID in qmd
    assert "knowledge-book-cover.tex" in qmd
    assert "knowledge-book-style.tex" in qmd
    assert "knowledge-book-toc.tex" in qmd
    assert "knowledge-book-components.tex" in qmd
    assert "knowledge-book-components.lua" in qmd
    assert "latex-auto-install: false" in qmd
    assert (delivery / "knowledge-book-cover.tex").is_file()
    assert (delivery / "knowledge-book-style.tex").is_file()
    assert (delivery / "knowledge-book-toc.tex").is_file()
    assert (delivery / "knowledge-book-components.tex").is_file()
    assert (delivery / "knowledge-book-components.lua").is_file()
    assert "\\KnowledgeProductSubtitle" in (delivery / "knowledge-book-cover.tex").read_text(
        encoding="utf-8"
    )
    assert "KpSourceCard" in (delivery / "knowledge-book-components.tex").read_text(
        encoding="utf-8"
    )
    components = (delivery / "knowledge-book-components.tex").read_text(encoding="utf-8")
    assert "% knowledge-product-pdf-v6-inline-code" in components
    assert "\\newtcbox{\\KpInlineCode}" in components
    assert "\\renewcommand{\\texttt}" not in components
    assert missing_pdf_source_contracts(qmd_path) == []
    assert f"profile_id: {PRESENTATION_PROFILE_ID}" in (delivery / "publication-metadata.yaml").read_text(
        encoding="utf-8"
    )


def test_real_quarto_render_supports_brand_components_offline(tmp_path: Path) -> None:
    if not resolve_quarto().available:
        pytest.skip("本机未安装 Quarto，跳过真实 PDF 渲染验收")
    from knowledge_product_workflow.pdf_fonts import probe_preferred_fonts
    if not all(item["available"] for item in probe_preferred_fonts(tmp_path).values()):
        pytest.skip("缺少PingFang SC/Semibold、Inter或JetBrains Mono；不将兼容字体冒充默认验收")
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(
        workspace,
        "AI Agent 工程实践",
        DetailConstraints(delivery_format="pdf"),
    )
    (workspace.root / "assets" / "workflow.png").write_bytes(_workflow_png())
    markdown_path = workspace.root / "delivery" / "book.md"
    markdown_path.write_text(
        """# AI Agent 工程实践

## 学习路径

这一节说明如何建立可复现的 Agent 工程流程。

调用 `verify_artifact(path)` 检查 `runs/` 目录中的已冻结工件。

保留原文：`{"source_id":"S1","paragraph_id":"P1","text":"camera checkout requires approval"}`。

::: {.kp-source}
来源：[OpenAI 官方文档](https://example.com/openai)
:::

::: {.kp-key-takeaway}
关键结论：先把可验证的工件写入文件，再执行下一个阶段。
:::

![受控交付工作流示意图](assets/workflow.png){width=80%}

| 阶段 | 目标 |
| --- | --- |
| Brief | 固化读者与交付约束 |
| 验证 | 确认工件可用 |

```python
def verify_artifact(path: str) -> bool:
    return bool(path)
```
""",
        encoding="utf-8",
    )

    qmd_path = create_quarto_source(workspace, markdown_path)
    pdf_path = render_pdf(qmd_path, workspace.root / "delivery", workspace)

    assert pdf_path.is_file()
    embedded = set()
    for page in PdfReader(pdf_path).pages:
        for font in page.get("/Resources", {}).get("/Font", {}).values():
            value = font.get_object()
            embedded.add(str(value.get("/BaseFont", "")).replace(" ", ""))
    for family in ("PingFangSC", "PingFangSC-Semibold", "Inter", "JetBrainsMono"):
        assert any(family in name for name in embedded), (family, embedded)
    rendered_tex = (workspace.root / "delivery" / "book.tex").read_text(encoding="utf-8")
    assert re.search(
        r"\\documentclass\[[^\]]*\b12pt\b[^\]]*\]\{extreport\}", rendered_tex, re.DOTALL
    )
    assert count_pdf_pages(pdf_path) >= 3
    assert count_pdf_bookmarks(pdf_path) >= 1
    assert count_pdf_external_links(pdf_path) >= 1
    rendered_pages = [page.extract_text() or "" for page in PdfReader(pdf_path).pages]
    assert "Table of Contents" in rendered_pages[1]
    assert "知识产品工作流" not in rendered_pages[1]
    assert "def verify_artifact" in "\n".join(rendered_pages)
    assert "verify_artifact(path)" in "\n".join(rendered_pages)
    assert "runs/" in "\n".join(rendered_pages)
    # A long code span may break inside a word, but data-bearing spaces survive.
    rejoined = "".join(rendered_pages).replace("\n", "")
    assert "camera checkout requires approval" in rejoined
    assert "cameracheckoutrequiresapproval" not in rejoined
    assert "{.kp-" not in "\n".join(rendered_pages)
    assert "\\KeywordTok" not in "\n".join(rendered_pages)
    if resolve_pdftoppm() is None:
        pytest.skip("本机未安装 pdftoppm，跳过 PDF 截图像素验收")
    preview = render_pdf_first_page(pdf_path, workspace.root / "runs" / "pdf-preview")
    assert preview.is_file()
    assert png_has_visible_ink(preview)


def test_visual_contract_requires_frozen_publication_metadata(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "AI Agent 工程实践", DetailConstraints(delivery_format="pdf"))
    markdown_path = workspace.root / "delivery" / "book.md"
    markdown_path.write_text("# AI Agent 工程实践\n", encoding="utf-8")
    qmd_path = create_quarto_source(workspace, markdown_path)

    (workspace.root / "delivery" / "publication-metadata.yaml").unlink()

    assert "PDF_PUBLICATION_METADATA_MISSING" in missing_pdf_source_contracts(qmd_path)


def test_fallback_font_roles_render_real_chinese_and_monospace_text(tmp_path: Path) -> None:
    if not resolve_quarto().available:
        pytest.skip("需要真实 Quarto/XeLaTeX")
    workspace = Workspace.create(tmp_path, "portable-fonts")
    create_product_brief(workspace, "中文离线字体核验", DetailConstraints(delivery_format="pdf"))
    load_or_create_publication_profile(workspace, language="zh-CN")
    preview_publication_update(workspace, PublicationUpdateInput(font_policy="fallback", version_bump="none"), language="zh-CN")
    apply_publication_update(workspace, "测试用户")
    markdown = workspace.root / "delivery" / "book.md"
    markdown.write_text("# 中文离线字体核验\n\n## 1.1 字形与代码\n\n中文正文需要清晰可辨的无衬线字形，公式 $x^2$ 与代码分别保留各自角色。\n\n```python\nvalue = 3 ** 2\nprint(value)\n```\n", encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown)
    pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
    reader = PdfReader(pdf)
    assert "中文正文" in "".join(page.extract_text() or "" for page in reader.pages)
    fonts = {str(font.get_object().get("/BaseFont", "")) for page in reader.pages for font in page["/Resources"].get("/Font", {}).values()}
    assert any("SimHei" in font for font in fonts)
    assert any("LMMono" in font or "LMTypewriter" in font for font in fonts)


def test_real_wide_table_header_renders_without_cell_level_rowcolor(tmp_path):
    if not resolve_quarto().available:
        pytest.skip('本机未安装Quarto')
    workspace=Workspace.create(tmp_path,'wide-table')
    create_product_brief(workspace,'资料回查',DetailConstraints(delivery_format='pdf'))
    md=workspace.root/'delivery/book.md'
    md.write_text('# 资料回查\n\n| 资料位置 | 回查内容 | 仍待确认 |\n|---|---|---|\n| 原始资料的第二条记录 | `local-102`，`units=0` | 数量应取何值、依据是什么 |\n| 对应复核记录 | 零违反正整数条件 | 补正材料是否支持改值 |\n',encoding='utf-8')
    qmd=create_quarto_source(workspace,md)
    pdf=render_pdf(qmd,workspace.root/'delivery',workspace)
    assert '资料位置' in ''.join(page.extract_text() for page in PdfReader(pdf).pages)
    tex=pdf.with_suffix('.tex').read_text(encoding='utf-8')
    assert 'minipage' in tex and r'\rowcolor{kpBrandSoft}' not in tex


@pytest.mark.parametrize('has_previous',[False,True])
def test_failed_render_does_not_leave_partial_pdf(tmp_path,monkeypatch,has_previous):
    import subprocess
    from contextlib import contextmanager
    from types import SimpleNamespace
    qmd=tmp_path/'book.qmd';qmd.write_text('# 测试',encoding='utf-8')
    output=tmp_path/'book.pdf'
    if has_previous:output.write_bytes(b'previous valid PDF')
    @contextmanager
    def paths(*args):
        yield qmd,tmp_path,tmp_path,{}
    monkeypatch.setattr('knowledge_product_workflow.publishing._quarto_paths',paths)
    monkeypatch.setattr('knowledge_product_workflow.publishing.resolve_quarto',lambda:SimpleNamespace(available=True,path=Path('quarto.exe')))
    def fail(*args,**kwargs):
        output.write_bytes(b'partial failed output')
        raise subprocess.CalledProcessError(1,'quarto',stderr='Misplaced noalign')
    monkeypatch.setattr('knowledge_product_workflow.publishing.subprocess.run',fail)
    with pytest.raises(RuntimeError,match='渲染失败'):
        render_pdf(qmd,tmp_path)
    assert output.read_bytes()==b'previous valid PDF' if has_previous else not output.exists()
