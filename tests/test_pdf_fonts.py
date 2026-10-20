from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_product_workflow.pdf_fonts import PREFERRED_FONTS, probe_preferred_fonts
from knowledge_product_workflow.publishing import (
    create_quarto_source,
    missing_pdf_source_contracts,
    render_pdf,
)
from knowledge_product_workflow.workspace import Workspace


def test_font_probe_uses_actual_resolver_and_cleans_workspace_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr("knowledge_product_workflow.pdf_fonts.shutil.which", lambda _: "xelatex")

    def fake_run(argv, **kwargs):
        assert kwargs["timeout"] == 8 and "-no-shell-escape" in argv
        folder = kwargs["cwd"]
        assert folder.is_relative_to(tmp_path)
        (folder / "probe.log").write_text("KP-FONT-0=NO\nKP-FONT-1=NO\nKP-FONT-2=YES\nKP-FONT-3=YES\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("knowledge_product_workflow.pdf_fonts.subprocess.run", fake_run)
    result = probe_preferred_fonts(tmp_path)
    assert not result["pingfang_sc"]["available"]
    assert result["inter"]["available"] and result["jetbrains_mono"]["available"]
    assert not list(tmp_path.iterdir())


def test_default_font_resources_keep_old_layout_and_explicit_semibold():
    root = files("knowledge_product_workflow").joinpath("templates", "pdf", "v6")
    new = root.joinpath("knowledge-book-style.tex").read_text(encoding="utf-8")
    assert r"\setmainfont[Scale=1]{Inter}" in new
    assert r"\setmonofont[Scale=0.99254]{JetBrains Mono}" in new
    assert r"\setCJKmainfont[Scale=1,BoldFont={PingFang SC Semibold}]{PingFang SC}" in new
    assert "IfFontExistsTF" not in new
    assert "Microsoft YaHei" in root.joinpath("layout1", "knowledge-book-style.tex").read_text(encoding="utf-8")


def test_missing_default_font_blocks_before_renderer_preserving_old_pdf(tmp_path, monkeypatch):
    workspace = Workspace.create(tmp_path, "font-book")
    from knowledge_product_workflow.workflows.product_brief import create_product_brief
    create_product_brief(workspace, "字体核验")
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text("# 字体核验\n\n正文。\n", encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown)
    pdf = workspace.root / "delivery/book.pdf"
    pdf.write_bytes(b"previous approved PDF")
    monkeypatch.setattr("knowledge_product_workflow.pdf_fonts.probe_preferred_fonts", lambda _:
                        {key: {"font": value, "available": key != "pingfang_sc_semibold"}
                         for key, value in PREFERRED_FONTS.items()})
    with pytest.raises(RuntimeError, match="PingFang SC Semibold.*不会静默替换"):
        render_pdf(qmd, pdf.parent, workspace)
    assert pdf.read_bytes() == b"previous approved PDF"


def test_old_candidate_fonts_cannot_masquerade_as_current_default(tmp_path):
    workspace = Workspace.create(tmp_path, "stale-font-book")
    from knowledge_product_workflow.workflows.product_brief import create_product_brief
    create_product_brief(workspace, "字体核验")
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text("# 字体核验\n\n正文。\n", encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown)
    style = qmd.parent / "knowledge-book-style.tex"
    style.write_text(style.read_text(encoding="utf-8").replace(r"\setmonofont[Scale=0.99254]{JetBrains Mono}",
                                                               r"\setmonofont{Consolas}"), encoding="utf-8")
    assert "PDF_DEFAULT_FONT_CONTRACT_STALE" in missing_pdf_source_contracts(qmd)


def test_render_failure_before_command_keeps_concrete_reason(tmp_path, monkeypatch):
    from contextlib import contextmanager
    workspace = Workspace.create(tmp_path, "path-denied-book")
    from knowledge_product_workflow.workflows.product_brief import create_product_brief
    create_product_brief(workspace, "路径诊断")
    md=workspace.root/'delivery/book.md';md.write_text('# 路径诊断\n\n正文。\n',encoding='utf-8')
    qmd=create_quarto_source(workspace,md)
    monkeypatch.setattr('knowledge_product_workflow.pdf_fonts.probe_preferred_fonts',lambda _: {})
    monkeypatch.setattr('knowledge_product_workflow.publishing.resolve_quarto',lambda:SimpleNamespace(available=True,path=Path(__file__),failed_candidates=[]))
    @contextmanager
    def denied(*args):
        raise RuntimeError('Quarto临时路径映射拒绝')
        yield
    monkeypatch.setattr('knowledge_product_workflow.publishing._quarto_paths',denied)
    with pytest.raises(RuntimeError,match='Quarto PDF 渲染失败'):
        render_pdf(qmd,md.parent,workspace)
    assert 'Quarto临时路径映射拒绝' in (workspace.root/'runs/pdf-render.log').read_text(encoding='utf-8')
