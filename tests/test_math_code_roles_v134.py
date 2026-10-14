"""数学手算不能冒用 Code；程序数据和源码教学保持原样。"""

import pytest

from knowledge_product_workflow.content_quality import scan_markdown_content
from knowledge_product_workflow.quality import Severity


@pytest.mark.parametrize("expression", [
    "‖q‖ = √(1² + 0²) = 1",
    "q·d = 1 × d₁ + 0 × d₂",
    "1² + 0² = 1",
])
def test_high_confidence_hand_calculation_as_code_is_blocked_with_exact_location(expression):
    line = f"手算为 `{expression}`。"
    markdown = f"# 1. 手工核对\n\n{line}\n"

    findings = scan_markdown_content(markdown, "chapters/chapter-01.md")

    finding = next(item for item in findings if item.code == "MATH_EXPRESSION_AS_CODE")
    assert finding.severity is Severity.BLOCKER
    assert finding.location == f"chapters/chapter-01.md:第 3 行，第 {line.index(expression) + 1} 列"
    assert expression in finding.message


@pytest.mark.parametrize("expression", [
    "q=(1,0)", "top_k=2", "math.sqrt(1**2 + 0**2)", "sum(x*x for x in q)",
    "q @ d", "q[0] * d[0] + q[1] * d[1]", "1 + 2", "x = 1 + 2",
    '"‖q‖ = √(1² + 0²) = 1"', '{"calculation": "q·d = 1 × d₁"}',
    "https://example.com/√?q=1²+0²", "../assets/1²+0².png", "assets/figures/√.png",
    r"E:\assets\√.png", r"\sqrt{1^2+0^2}", r"$q\cdot d = 1$",
    'python -c "print(1**2 + 0**2)"', 'echo "‖q‖ = √(1² + 0²)"',
    'mytool --label="√(1²+0²)"',
])
def test_program_data_commands_paths_literals_and_latex_source_keep_code_role(expression):
    assert not [item for item in scan_markdown_content(f"示例：`{expression}`", "chapters/chapter-01.md")
                if item.code == "MATH_EXPRESSION_AS_CODE"]


def test_ambiguous_ascii_equation_gets_only_an_editor_warning():
    findings = scan_markdown_content("排版意图待确认：`a + b = c`。", "chapters/chapter-01.md")

    finding = next(item for item in findings if item.code == "MATH_EXPRESSION_AS_CODE")
    assert finding.severity is Severity.WARNING


@pytest.mark.parametrize("fence", ["```text", "~~~text", "````text"])
def test_fenced_code_and_indented_source_examples_are_not_scanned(fence):
    closing = fence.removesuffix("text")
    markdown = f"{fence}\n示例：`‖q‖ = √(1² + 0²)`\n{closing}\n\n    示例：`q·d = 1 × d₁`\n"
    assert not [item for item in scan_markdown_content(markdown, "chapters/chapter-01.md")
                if item.code == "MATH_EXPRESSION_AS_CODE"]


@pytest.mark.parametrize("markdown", [
    "> ```text\n> `‖q‖ = √(1² + 0²)`\n> ```\n",
    "- ```text\n  `q·d = 1 × d₁ + 0 × d₂`\n  ```\n",
])
def test_nested_markdown_code_blocks_keep_their_source_role(markdown):
    assert not [item for item in scan_markdown_content(markdown, "chapter")
                if item.code == "MATH_EXPRESSION_AS_CODE"]


def test_valid_markdown_math_and_variable_length_code_delimiters_are_distinguished():
    assert not [item for item in scan_markdown_content(r"公式 $\Vert q\Vert=\sqrt{1^2+0^2}=1$。", "chapter")
                if item.code == "MATH_EXPRESSION_AS_CODE"]
    findings = scan_markdown_content("数学误标 ``‖q‖ = √(1² + 0²)``。", "chapter")
    assert len([item for item in findings if item.code == "MATH_EXPRESSION_AS_CODE"]) == 1


def test_submit_chapter_rejects_math_as_code_before_replacing_the_frozen_draft(tmp_path):
    from knowledge_product_workflow.chapter_compliance import ChapterSubmissionError, submit_chapter
    from tests.test_chapter_compliance import _body, _mapping, _setup, _workspace_input

    workspace, _ = _setup(tmp_path)
    chapter = workspace.root / "chapters/chapter-01.md"
    original = chapter.read_bytes()
    source = workspace.root / "runs/math-as-code.md"
    source.write_text(_body() + "\n手算为 `‖q‖ = √(1² + 0²) = 1`。\n", encoding="utf-8")
    source_original = source.read_bytes()

    with pytest.raises(ChapterSubmissionError, match="MATH_EXPRESSION_AS_CODE"):
        submit_chapter(workspace, 1, source, _workspace_input(workspace, "mapping.json", _mapping()))

    assert chapter.read_bytes() == original
    assert source.read_bytes() == source_original


def test_quality_and_delivery_reports_recompute_the_math_role_blocker_without_editing_payload(tmp_path):
    from knowledge_product_workflow.workflows.validation import (
        run_delivery_validation,
        run_validation,
    )
    from knowledge_product_workflow.workspace import Workspace

    workspace = Workspace.create(tmp_path, "math-role-gate")
    body = "# 1. 手算\n\n`q·d = 1 × d₁ + 0 × d₂`。\n"
    chapter = workspace.root / "chapters/chapter-01.md"
    chapter.write_text("---\nbook_id: math-role-gate\nartifact_type: chapter\nstatus: in_production\n"
                       "version: 1\ndepends_on: []\napproved_by: null\n---\n" + body, encoding="utf-8")
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text(body, encoding="utf-8")
    originals = {path: path.read_bytes() for path in (chapter, markdown)}

    _, quality = run_validation(workspace, set())
    _, delivery = run_delivery_validation(workspace)

    assert any(item.code == "MATH_EXPRESSION_AS_CODE" for item in quality.findings)
    assert any(item.code == "MATH_EXPRESSION_AS_CODE" and item.location.startswith("delivery/book.md") for item in delivery.findings)
    assert not quality.publishable and not delivery.publishable
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_native_math_font_role_and_program_copy_payload_are_preserved(tmp_path):
    import json
    import shutil
    import subprocess
    import unicodedata

    from pypdf import PdfReader

    from knowledge_product_workflow.detail_constraints import DetailConstraints
    from knowledge_product_workflow.pdf_fonts import probe_preferred_fonts
    from knowledge_product_workflow.presentation import PresentationOverrides
    from knowledge_product_workflow.publishing import create_quarto_source, render_pdf
    from knowledge_product_workflow.quarto import resolve_quarto
    from knowledge_product_workflow.workflows.product_brief import (
        BriefSemanticFields,
        create_product_brief,
    )
    from knowledge_product_workflow.workspace import Workspace

    if not resolve_quarto().available or not all(item["available"] for item in probe_preferred_fonts(tmp_path).values()):
        pytest.skip("需要已有Quarto和固定字体；跳过不作原生Math角色验证通过")
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        pytest.skip("需要已有Poppler；跳过不作代码复制验证通过")
    workspace = Workspace.create(tmp_path, "math-code-native")
    create_product_brief(workspace, "手算与程序角色核验", DetailConstraints(delivery_format="pdf"),
                         presentation=PresentationOverrides(cover_summary_zh="核对手工计算的数学角色与程序数据保真。"),
                         semantics=BriefSemanticFields(reader_request="核验手算数学字体与程序数据复制保真。"))
    math_payload = r"$\Vert q\Vert = \sqrt{1^2 + 0^2} = 1$"
    code_payloads = ("q=(1,0)", "top_k=2", "math.sqrt(1**2+0**2)")
    body = ("# 1. 数学与程序\n\n手算范数：" + math_payload + "。\n\n"
            + "程序数据依次为 " + "、".join(f"`{value}`" for value in code_payloads) + "。\n")
    assert not [item for item in scan_markdown_content(body, "chapter") if item.code == "MATH_EXPRESSION_AS_CODE"]
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text(body, encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
    runs = []

    def capture(text, _cm, _tm, font, size):
        if font is not None and text.strip():
            runs.append({"text": text, "font": str(font.get("/BaseFont", "")), "size": size})

    for page in PdfReader(pdf).pages[2:]:
        page.extract_text(visitor_text=capture)
    math_runs = [item for item in runs if "LatinModernMath" in item["font"]]
    code_runs = [item for item in runs if "JetBrainsMono" in item["font"]]
    # unicode-math correctly exposes mathematical italic q as U+1D45E.
    assert any("q" in unicodedata.normalize("NFKC", item["text"]) for item in math_runs), runs
    assert any("top" in item["text"] for item in code_runs), runs
    copied = subprocess.run([pdftotext, str(pdf), "-"], capture_output=True, check=True, timeout=30).stdout.decode("utf-8")
    assert all(value in copied for value in code_payloads), copied
    assert math_payload in qmd.read_text(encoding="utf-8")
    assert all(f"`{value}`" in qmd.read_text(encoding="utf-8") for value in code_payloads)
    (workspace.root / "runs/native-math-code-measurements.json").write_text(
        json.dumps({"pdf": str(pdf), "math_runs": math_runs, "code_runs": code_runs,
                    "copied_payloads": code_payloads, "copied_payloads_verified": True}, ensure_ascii=False, indent=2), encoding="utf-8")
