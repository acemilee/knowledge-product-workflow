"""横向文字定位只筛疑点，不代替实际看图或判定图表挤页。"""

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from knowledge_product_workflow.quality import Severity
from knowledge_product_workflow.visual_audit import _measure_page, _page_findings


def _page(commands):
    writer = PdfWriter()
    page = writer.add_blank_page(595.28, 841.89)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Courier"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    stream = DecodedStreamObject()
    stream.set_data(commands)
    page[NameObject("/Contents")] = writer._add_object(stream)
    return page


def test_long_run_near_page_edge_has_bounded_locatable_warning_only():
    page = _page(b"BT /F1 12 Tf 1 0 0 1 540 700 Tm (assets/very-long-program-path.py) Tj ET")
    metric = _measure_page(page, 3)
    assert metric.horizontal_text_candidates
    candidate = metric.horizontal_text_candidates[0]
    assert candidate.text == "assets/very-long-program-path.py"
    assert candidate.x_pt == 540 and candidate.y_pt == 700
    assert candidate.estimated_right_pt > metric.width_pt
    findings = [f for f in _page_findings(metric) if f.code == "PDF_HORIZONTAL_TEXT_CANDIDATE"]
    assert len(findings) == 1 and findings[0].severity == Severity.WARNING
    assert "估算" in findings[0].message and "清晰页" in findings[0].message
    assert "540" in findings[0].message and "assets/" in findings[0].message


def test_short_identifiers_and_legal_sparse_chapter_end_have_no_horizontal_warning():
    page = _page(b"BT /F1 12 Tf 1 0 0 1 50 700 Tm (top_k=2) Tj ET")
    metric = _measure_page(page, 3)
    assert not metric.horizontal_text_candidates
    assert not [f for f in _page_findings(metric) if f.code == "PDF_HORIZONTAL_TEXT_CANDIDATE"]


def test_rotated_or_origin_aggregate_callbacks_do_not_claim_precise_text_bounds():
    for commands in (
        b"BT /F1 12 Tf 0 1 -1 0 590 700 Tm (long-rotated-text) Tj ET",
        b"BT /F1 12 Tf 1 0 0 1 0 0 Tm (origin-aggregate-text) Tj ET",
    ):
        assert not _measure_page(_page(commands), 3).horizontal_text_candidates


def test_many_suspect_runs_do_not_produce_an_unbounded_report():
    commands = b" ".join(
        f"BT /F1 12 Tf 1 0 0 1 590 {700-i*15} Tm (overflow-{i}) Tj ET".encode()
        for i in range(30)
    )
    metric = _measure_page(_page(commands), 3)
    assert len(metric.horizontal_text_candidates) == 20
    assert metric.horizontal_text_candidate_count == 30
    findings = [f for f in _page_findings(metric) if f.code == "PDF_HORIZONTAL_TEXT_CANDIDATE"]
    assert len(findings) == 1 and "30" in findings[0].message


def test_actual_lua_filter_emits_nonprinting_fallback_for_unbroken_identifier():
    import subprocess
    from pathlib import Path

    import pytest

    from knowledge_product_workflow.quarto import resolve_quarto
    resolved = resolve_quarto()
    if not resolved.available:
        pytest.skip("没有既有Quarto；不把跳过算作过滤器实测")
    import knowledge_product_workflow
    lua = Path(knowledge_product_workflow.__file__).parent / "templates/pdf/v6/knowledge-book-components.lua"
    payload = "a" * 75
    result = subprocess.run([str(resolved.path), "pandoc", "-f", "markdown", "-t", "latex", "--lua-filter", str(lua)],
        input=f"`{payload}`\n", text=True, encoding="utf-8", capture_output=True, timeout=10, check=True)
    assert "\\KpCodeSmart{" + payload + "}" in result.stdout
    assert result.stdout.count("\\penalty100{}") == 74
    assert result.stdout.count("{a}") == 75


def test_actual_fvextra_options_leave_no_printed_break_glyphs(tmp_path):
    import re
    import shutil
    import subprocess
    from pathlib import Path

    import pytest

    import knowledge_product_workflow
    tool = shutil.which("xelatex")
    if not tool:
        pytest.skip("无既有XeLaTeX；不算宏展开探针通过")
    style = (Path(knowledge_product_workflow.__file__).parent / "templates/pdf/v6/knowledge-book-style.tex").read_text(encoding="utf-8")
    options = re.search(r"\\DefineVerbatimEnvironment\{Highlighting\}\{Verbatim\}\s*\n  (.*)", style)[1]
    # Exercise actual installed fvextra keys with the source options; no PDF
    # page/render/sample is produced. The complete final-PDF gate is separate.
    document = (r"\documentclass{article}\usepackage{fvextra}\begin{document}" + "\n"
        + r"\fvset" + options + "\n" + r"\makeatletter" + "\n"
        + "\n".join("\\ifx\\" + name + r"\empty\typeout{KP-" + name + r"-EMPTY}\else\errmessage{printed break glyph remains}\fi"
                    for name in ["FancyVerbBreakSymbolLeft", "FancyVerbBreakSymbolRight",
                                 "FancyVerbBreakAnywhereSymbolPre", "FancyVerbBreakAnywhereSymbolPost"])
        + "\n" + r"\end{document}")
    source = tmp_path / "probe.tex"
    source.write_text(document, encoding="utf-8")
    done = subprocess.run([tool, "-no-pdf", "-interaction=nonstopmode", "-halt-on-error", source.name],
        cwd=tmp_path, capture_output=True, timeout=30, check=False)
    assert done.returncode == 0, done.stdout.decode("utf-8", "replace")[-3000:]
    assert done.stdout.count(b"-EMPTY") == 4
    assert not list(tmp_path.glob("*.pdf"))
