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
from knowledge_product_workflow.presentation import PRESENTATION_PROFILE_ID
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
    assert "knowledge-product-pdf-v4" in qmd
    assert "knowledge-book-cover.tex" in qmd
    assert "knowledge-book-style.tex" in qmd
    assert "knowledge-book-toc.tex" in qmd
    assert "knowledge-book-components.tex" in qmd
    assert "knowledge-book-components.lua" in qmd
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
    assert "% knowledge-product-pdf-v4-inline-code" in components
    assert "\\newtcbox{\\KpInlineCode}" in components
    assert "\\renewcommand{\\texttt}" in components
    assert missing_pdf_source_contracts(qmd_path) == []
    assert f"profile_id: {PRESENTATION_PROFILE_ID}" in (delivery / "publication-metadata.yaml").read_text(
        encoding="utf-8"
    )


def test_real_quarto_render_supports_brand_components_offline(tmp_path: Path) -> None:
    if not resolve_quarto().available:
        pytest.skip("本机未安装 Quarto，跳过真实 PDF 渲染验收")
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
    rendered_tex = (workspace.root / "delivery" / "book.tex").read_text(encoding="utf-8")
    assert re.search(
        r"\\documentclass\[[^\]]*\b12pt\b[^\]]*\]\{extreport\}", rendered_tex, re.DOTALL
    )
    assert count_pdf_pages(pdf_path) >= 3
    assert count_pdf_bookmarks(pdf_path) >= 1
    assert count_pdf_external_links(pdf_path) >= 1
    rendered_pages = [page.extract_text() or "" for page in PdfReader(pdf_path).pages]
    assert "Table of Contents" in rendered_pages[1]
    assert "知识产品工作流" in rendered_pages[1]
    assert "def verify_artifact" in "\n".join(rendered_pages)
    assert r"\texttt{verify\_artifact(path)}" in rendered_tex
    assert r"\texttt{runs/}" in rendered_tex
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
