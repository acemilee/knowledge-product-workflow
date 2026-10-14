"""Measure the actual rendered font roles, not a nominal fontsize setting."""

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest
from pypdf import PdfReader

from knowledge_product_workflow.callouts import render_reader_markdown
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.pdf_fonts import probe_preferred_fonts
from knowledge_product_workflow.presentation import PresentationOverrides
from knowledge_product_workflow.publishing import create_quarto_source, render_pdf
from knowledge_product_workflow.quarto import resolve_quarto
from knowledge_product_workflow.visual_audit import audit_pdf_visual
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def font_role_sizes(pdf):
    counts = {role: Counter() for role in ("cjk", "latin", "math", "code")}
    for page in PdfReader(pdf).pages[2:]:
        fonts = page["/Resources"]["/Font"]
        for operands, operator in page.get_contents().operations:
            if operator != b"Tf":
                continue
            name = str(fonts[operands[0]].get_object().get("/BaseFont", ""))
            size = float(operands[1])
            role = "cjk" if "PingFang" in name else "math" if "LatinModernMath" in name else "code" if "JetBrainsMono" in name else "latin" if "Inter-Regular" in name else None
            if role and 9 <= size <= 18:
                counts[role][round(size, 4)] += 1
    return {role: sorted(counter.items()) for role, counter in counts.items()}


def test_rendered_mixed_roles_spacing_and_protected_payload(tmp_path):
    if not resolve_quarto().available or not all(x["available"] for x in probe_preferred_fonts(tmp_path).values()):
        pytest.skip("需要当前原生渲染及四默认字体；跳过不是混排通过")
    tool = shutil.which("pdftotext")
    if not tool:
        pytest.skip("需要已有Poppler字形边界量测；跳过不是通过")
    workspace = Workspace.create(tmp_path, "mixed-typography")
    create_product_brief(workspace, "中英数字与公式混排回归", DetailConstraints(delivery_format="pdf"),
        presentation=PresentationOverrides(cover_layout_revision=2, cover_summary_zh="核对实际字体字级、间距与数据保真。"))
    body = (Path(__file__).parent / "fixtures/reading-regressions/mixed-typography.md").read_text(encoding="utf-8")
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text(render_reader_markdown(body), encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
    roles = font_role_sizes(pdf)
    result = subprocess.run([tool, "-bbox", str(pdf), "-"], capture_output=True, check=True, timeout=30)
    xml = ET.fromstring(result.stdout)
    words = [{"text": item.text, **{k: float(v) for k, v in item.attrib.items()}}
             for item in xml.iter() if item.tag.endswith("word")]
    (workspace.root / "runs/native-mixed-measurements.json").write_text(
        json.dumps({"font_roles": roles, "words": words}, ensure_ascii=False, indent=2), encoding="utf-8")
    expected = 12 * 72 / 72.27
    for role in ("cjk", "latin", "math"):
        # Body text dominates CJK/Latin; math includes valid smaller scripts.
        observed = max(roles[role], key=lambda item: item[1])[0] if role != "math" else max(x[0] for x in roles[role])
        assert abs(observed - expected) < 0.15, (role, roles, expected)
    pairs = []
    for index, word in enumerate(words[1:-1], start=1):
        left, right = words[index-1], words[index+1]
        if word["text"] == "Agent" and left["text"] == "甲" and right["text"].startswith("乙"):
            pairs.append((word["xMin"]-left["xMax"], right["xMin"]-word["xMax"]))
    assert len(pairs) == 3, pairs  # unspaced, spaced and table cell
    assert all(abs(gap - expected * .25) < .15 for pair in pairs for gap in pair), pairs
    text = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
    assert "camera checkout requires approval" in text
    assert "notes-to-actions/SKILL.md" in text
    # Poppler honours the native PDF ActualText emitted by XeTeX. pypdf's
    # glyph-only decoding may expose a font's private-use alias instead.
    copied = subprocess.run([tool, str(pdf), "-"], capture_output=True, check=True, timeout=30).stdout.decode("utf-8")
    assert "v1.3.3" in copied and "-0.5%" in copied and "2026-10" in copied
    assert '"中文Agent"' in copied  # No added prose spacing inside string data.
    assert all(payload in qmd.read_text(encoding="utf-8") for payload in (
        "$x_i$", "$2.4+3.0+1.8=7.2$", "-0.5%", "2026-10", "https://example.com/Agent/v1.3.3?q=0.5"))
    audit = audit_pdf_visual(workspace)
    assert audit.typography_measurement_revision == 2
    assert "PDF_BODY_ROLE_SIZE_DRIFT" not in {item.code for item in audit.findings}


def test_inline_filter_preserves_math_code_and_link_targets_and_gap_idempotence(tmp_path):
    quarto = resolve_quarto()
    if not quarto.available:
        pytest.skip("需要现有Pandoc AST；跳过不是语义保真通过")
    fixture = Path(__file__).parent / "fixtures/reading-regressions/mixed-typography.md"
    filter_path = Path(__file__).parents[1] / "src/knowledge_product_workflow/templates/pdf/v6/knowledge-book-components.lua"
    original = subprocess.run([str(quarto.path), "pandoc", str(fixture), "-t", "json"],
        capture_output=True, check=True, timeout=30).stdout
    def filtered(data):
        return subprocess.run([str(quarto.path), "pandoc", "-f", "json", "-t", "json", "--lua-filter", str(filter_path)],
            input=data, capture_output=True, check=True, timeout=30).stdout
    first = filtered(original)
    second = filtered(first)
    def protected(data):
        result = {key: [] for key in ("Math", "CodeBlock", "Link")}
        def walk(node):
            if isinstance(node, dict):
                if node.get("t") in result:
                    kind = node["t"]
                    result[kind].append(node["c"] if kind == "Math" else node["c"][-1])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(json.loads(data))
        return result
    assert protected(first) == protected(original) == protected(second)
    assert first.count(b"KpMixedGap") == second.count(b"KpMixedGap")
    assert first.count(b"KpMixedGap") > 0


def test_zero_glue_or_missing_explicit_math_scale_rejects_source_contract(tmp_path):
    from knowledge_product_workflow.publishing import missing_pdf_source_contracts
    workspace = Workspace.create(tmp_path, "bad-mixed-contract")
    create_product_brief(workspace, "混排合同", DetailConstraints(delivery_format="pdf"))
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text("# 混排合同\n\n正文。\n", encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown)
    style = qmd.parent / "knowledge-book-style.tex"
    original = style.read_text(encoding="utf-8")
    for good, bad in [(r"\hskip .25em", r"\hskip 0pt"),
                      (r"\setmathfont[Scale=1]", r"\setmathfont")]:
        style.write_text(original.replace(good, bad), encoding="utf-8")
        assert "PDF_DEFAULT_FONT_CONTRACT_STALE" in missing_pdf_source_contracts(qmd)


def test_preserved_table_introduction_has_no_isolated_decimal_tail(tmp_path):
    if not resolve_quarto().available or not all(x["available"] for x in probe_preferred_fonts(tmp_path).values()):
        pytest.skip("需要实际出版链；跳过不代表引入/表格分页通过")
    workspace = Workspace.create(tmp_path, "table-introduction")
    create_product_brief(workspace, "用加权平均比较方案", DetailConstraints(delivery_format="pdf"),
        presentation=PresentationOverrides(cover_layout_revision=2, cover_summary_zh="核对表格引入的实际分页。"))
    body = (Path(__file__).parent / "fixtures/reading-regressions/table-intro-tail.md").read_text(encoding="utf-8")
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text(render_reader_markdown(body), encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
    pages = ["".join(page.extract_text().split()) for page in PdfReader(pdf).pages]
    assert any("成本友好度仍为0.2" in page and "比较条件" in page for page in pages)
    assert any("A贡献" in page and "B贡献" in page for page in pages)


@pytest.mark.parametrize("paragraph_spacing", ["compact", "relaxed"])
@pytest.mark.parametrize("with_long_table", [False, True])
def test_closing_table_and_checks_stay_coherent_without_locking_long_tables(tmp_path, with_long_table, paragraph_spacing):
    if not resolve_quarto().available or not all(x["available"] for x in probe_preferred_fonts(tmp_path).values()):
        pytest.skip("需要原生排版；跳过不是收尾组合通过")
    workspace = Workspace.create(tmp_path, "closing-table")
    create_product_brief(workspace, "规则交接与收尾组合", DetailConstraints(delivery_format="pdf"),
        presentation=PresentationOverrides(cover_layout_revision=2, cover_summary_zh="核对收尾验收表和解释连续。"))
    from knowledge_product_workflow.presentation import (
        PublicationUpdateInput,
        apply_publication_update,
        load_or_create_publication_profile,
        preview_publication_update,
    )
    if load_or_create_publication_profile(workspace, language="zh-CN").paragraph_spacing != paragraph_spacing:
        preview_publication_update(workspace, PublicationUpdateInput(paragraph_spacing=paragraph_spacing, version_bump="none"), language="zh-CN")
        apply_publication_update(workspace, approved_by="测试夹具")
    body = (Path(__file__).parent / "fixtures/reading-regressions/closing-table-tail.md").read_text(encoding="utf-8")
    if with_long_table:
        body += "\n# 2. 跨页查询表\n\n完整记录如下。\n\n| 条目 | 核对内容 |\n| --- | --- |\n"
        body += "".join(f"| 记录{i:02} | 这是需要保留的完整条件与输出核对记录，不缩小字体，也不删除条目。 |\n" for i in range(50))
        body += "\n查询时沿条目逐项核对，所有记录都保留。\n"
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text(render_reader_markdown(body), encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
    pages = ["".join(page.extract_text().split()) for page in PdfReader(pdf).pages]
    assert any("缺少" in page and "damage_B" in page and "交接前检查规则单" in page and "手工推演通过不能写成" in page for page in pages)
    if with_long_table:
        assert sum("核对内容" in page for page in pages) >= 2  # Real longtable continues and repeats its header.
        assert all(f"记录{i:02}" in "".join(pages) for i in range(50))
    tex = (qmd.parent / "book.tex").read_text(encoding="utf-8")
    assert r"\KpReserveBegin{36pt}" in tex  # Actual branch, not an accidental favourable page break.
    roles = font_role_sizes(pdf)
    assert abs(max(roles["cjk"], key=lambda x: x[1])[0] - 12*72/72.27) < .15


def test_closing_constraints_and_recall_do_not_leave_an_isolated_tail(tmp_path):
    if not resolve_quarto().available or not all(x["available"] for x in probe_preferred_fonts(tmp_path).values()):
        pytest.skip("需要原生出版链；跳过不是收尾列表通过")
    workspace = Workspace.create(tmp_path, "closing-list")
    create_product_brief(workspace, "目录与字段规则", DetailConstraints(delivery_format="pdf"),
        presentation=PresentationOverrides(cover_layout_revision=2, cover_summary_zh="核对章节收束的连续阅读。"))
    body = (Path(__file__).parent / "fixtures/reading-regressions/closing-list-tail.md").read_text(encoding="utf-8")
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text(render_reader_markdown(body), encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
    pages = ["".join(page.extract_text().split()) for page in PdfReader(pdf).pages]
    assert any("name长度为1至64" in page and "description长度为1至1024" in page and "现在回看这份文件" in page and "读懂这三个位置" in page for page in pages)
    assert "name:task-notes" in "".join(pages)
