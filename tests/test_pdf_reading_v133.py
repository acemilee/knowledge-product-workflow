"""Real rendering of the preserved manuscript, not freshly shortened prose."""

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from pypdf import PdfReader

from knowledge_product_workflow.callouts import render_reader_markdown
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.pdf_fonts import probe_preferred_fonts
from knowledge_product_workflow.presentation import PresentationOverrides
from knowledge_product_workflow.publishing import create_quarto_source, render_pdf
from knowledge_product_workflow.quarto import resolve_quarto
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_preserved_reading_defects_render_without_header_duplication_or_split_file(tmp_path):
    if not resolve_quarto().available or not all(item["available"] for item in probe_preferred_fonts(tmp_path).values()):
        pytest.skip("需要实际Quarto/XeLaTeX与四默认字体；跳过不代表本版成页通过")
    fixtures = Path(__file__).parent / "fixtures/reading-regressions"
    body = (fixtures / "book.md").read_text(encoding="utf-8")
    body += "\n\n# 4. 长标识与公式回归\n\n" + (fixtures / "long-json.md").read_text(encoding="utf-8")
    body += "\n\n公式角色保留：$p = b/n$，且 $n > 0$。\n"
    body += "\n\n# 5. 色框几何回归\n\n" + (fixtures / "inline-geometry.md").read_text(encoding="utf-8").split("\n", 1)[1]
    workspace = Workspace.create(tmp_path, "reading-regression")
    title = "Agent Skills 入门：原理、结构与基础用法"
    create_product_brief(workspace, title, DetailConstraints(delivery_format="pdf"), presentation=PresentationOverrides(
        cover_layout_revision=2, cover_summary_zh="理解作用、看懂结构，并完成最小Skill的编写与调用。",
        cover_title_lines=["Agent Skills 入门：", "原理、结构与基础用法"], brand_color_intent="橙色基调"))
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text(render_reader_markdown(body), encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
    pages = [re.sub(r"\s", "", page.extract_text()) for page in PdfReader(pdf).pages]
    assert "原理、结构与基础用法" in pages[0]
    assert all(page.count("主要回答的问题") <= 1 for page in pages)
    assert sum(page.count("主要回答的问题") for page in pages) >= 1
    assert any(all(part in page for part in ("name:notes-to-actions", "只提取明确承诺或决定执行的行动", "不要创建虚构行")) for page in pages)
    assert "notes-to-actions/SKILL.md" in "".join(pages)
    assert "cameracheckoutrequiresapproval" in "".join(pages)
    assert "$p = b/n$" in qmd.read_text(encoding="utf-8")
    for identifier in ("task-notes", "skills", "/skills/", "AGENTS.md", "g_p_y", "input.jsonl", "rules.json"):
        assert identifier in "".join(pages)
    # Poppler measures painted glyphs; pypdf visitor callbacks after a nested
    # tcolorbox can carry the outer text origin rather than the glyph origin.
    text_tool = shutil.which("pdftotext")
    if not text_tool:
        pytest.skip("基线量测需要Poppler pdftotext；跳过不代表通过")
    result = subprocess.run([str(text_tool), "-bbox", str(pdf), "-"], capture_output=True, check=True, timeout=30)
    tree = ET.fromstring(result.stdout)
    cell_positions = {word.text: float(word.attrib["yMin"]) for word in tree.iter()
                      if word.tag.endswith("word") and word.text in {"TASK_CELL", "PLAIN_CELL"}}
    assert set(cell_positions) == {"TASK_CELL", "PLAIN_CELL"}
    assert abs(cell_positions["TASK_CELL"] - cell_positions["PLAIN_CELL"]) < 2
