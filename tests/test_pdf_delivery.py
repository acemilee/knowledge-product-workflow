import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.publishing import (
    create_quarto_source,
    missing_pdf_source_contracts,
    pdf_render_log_path,
    render_pdf,
)
from knowledge_product_workflow.quarto import QuartoResolution
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_qmd_uses_the_approved_fixed_a4_layout(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "Guide", DetailConstraints(target_words=1, language="zh-CN"))
    markdown_path = workspace.root / "delivery" / "book.md"
    markdown_path.write_text("# Guide\n", encoding="utf-8")

    qmd_path = create_quarto_source(workspace, markdown_path)

    qmd = qmd_path.read_text(encoding="utf-8")
    assert "papersize: a4" in qmd
    assert "fontsize: 12pt" in qmd
    assert "geometry: left=13mm,right=13mm,top=18mm,bottom=18mm" in qmd
    assert "lang: zh-CN" in qmd
    assert "pdf-engine: xelatex" in qmd
    assert "toc-title: 目录" in qmd
    assert "title-block: false" in qmd
    assert "knowledge-book-cover.tex" in qmd
    assert "knowledge-book-style.tex" in qmd
    assert "kp-font-role-policy: preferred" in qmd
    assert (workspace.root / "delivery" / "knowledge-book-cover.tex").is_file()
    style = (workspace.root / "delivery" / "knowledge-book-style.tex").read_text(encoding="utf-8")
    assert "% knowledge-product-pdf-v6-style" in style
    assert "\\usepackage{xeCJK}" in style
    assert "\\providecommand{\\KnowledgeProductLineSpread}{1.32}" in style
    assert "\\linespread{\\KnowledgeProductLineSpread}" in style
    assert "fontsize=\\small" in style
    assert "\\renewcommand{\\headrulewidth}{0pt}" in style
    assert "\\AtBeginDocument" in style
    assert "\\@ifundefined{Shaded}" in style
    assert "\\let\\maketitle\\relax" in style
    cover = (workspace.root / "delivery" / "knowledge-book-cover.tex").read_text(encoding="utf-8")
    assert "\\providecommand{\\KnowledgeProductTitle}{Guide}" in cover
    assert "\\providecommand{\\KnowledgeProductSubtitle}{专业知识教程}" in cover
    assert "\\providecommand{\\KnowledgeProductCoverLabel}{知识产品工作流}" in cover
    assert "\\providecommand{\\KnowledgeProductFooterLabel}{}" in cover
    assert missing_pdf_source_contracts(qmd_path) == []


def test_missing_quarto_records_a_failure_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    qmd_path = workspace.root / "delivery" / "book.qmd"
    qmd_path.write_text("---\n---\n# Guide\n", encoding="utf-8")
    monkeypatch.setattr(
        "knowledge_product_workflow.publishing.resolve_quarto",
        lambda: QuartoResolution(None, None, None, ("PATH：不存在",)),
    )

    with pytest.raises(RuntimeError, match="未检测到 Quarto"):
        render_pdf(qmd_path, workspace.root / "delivery", workspace)

    assert "quarto_unavailable" in (workspace.root / "runs" / "events.jsonl").read_text(
        encoding="utf-8"
    )


def test_render_pdf_disables_quarto_texlive_auto_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    qmd_path = workspace.root / "delivery" / "book.qmd"
    qmd_path.write_text("---\n---\n# Guide\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        captured["command"] = args[0]
        writer = PdfWriter()
        writer.add_blank_page(595, 842)
        with (workspace.root / "delivery" / "book.pdf").open("wb") as handle:
            writer.write(handle)
        return type("Completed", (), {"stdout": "rendered", "stderr": ""})()

    monkeypatch.setattr(
        "knowledge_product_workflow.publishing.resolve_quarto",
        lambda: QuartoResolution(Path(r"C:\Program Files\Quarto\bin\quarto.exe"), "默认安装路径", "1.7", ()),
    )
    monkeypatch.setattr("knowledge_product_workflow.publishing.subprocess.run", fake_run)

    render_pdf(qmd_path, workspace.root / "delivery", workspace)

    assert captured["env"]["QUARTO_TEXLIVE_AUTO_INSTALL"] == "false"
    assert captured["command"][0] == r"C:\Program Files\Quarto\bin\quarto.exe"
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    render_log = pdf_render_log_path(workspace).read_text(encoding="utf-8")
    assert "Quarto 输出：\nrendered" in render_log
    assert f"有效工作目录：{workspace.root}" in render_log
    assert f"预期输出：{workspace.root / 'delivery' / 'book.pdf'}" in render_log


def test_render_pdf_uses_absolute_paths_and_a_fixed_workspace_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = Workspace.create(Path("output"), "guide")
    qmd_path = workspace.root / "delivery" / "book.qmd"
    qmd_path.write_text("---\n---\n# Guide\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured["command"] = args[0]
        captured.update(kwargs)
        writer = PdfWriter()
        writer.add_blank_page(595, 842)
        with (workspace.root / "delivery" / "book.pdf").open("wb") as handle:
            writer.write(handle)
        return type("Completed", (), {"stdout": "rendered", "stderr": ""})()

    monkeypatch.setattr(
        "knowledge_product_workflow.publishing.resolve_quarto",
        lambda: QuartoResolution(Path(r"C:\\Program Files\\Quarto\\bin\\quarto.exe"), "默认安装路径", "1.7", ()),
    )
    monkeypatch.setattr("knowledge_product_workflow.publishing.subprocess.run", fake_run)

    output = render_pdf(
        Path("output") / "guide" / "delivery" / "book.qmd",
        Path("output") / "guide" / "delivery",
        workspace,
    )

    command = captured["command"]
    assert command[2] == str(qmd_path.resolve())
    assert command[6] == str((workspace.root / "delivery").resolve())
    assert captured["cwd"] == str(workspace.root.resolve())
    assert output == (workspace.root / "delivery" / "book.pdf").resolve()


def test_nested_quarto_output_is_rejected_as_a_path_contract_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    qmd_path = workspace.root / "delivery" / "book.qmd"
    qmd_path.write_text("---\n---\n# Guide\n", encoding="utf-8")

    def fake_run(*args, **kwargs):
        nested_pdf = workspace.root / "delivery" / "output" / "guide" / "delivery" / "book.pdf"
        nested_pdf.parent.mkdir(parents=True)
        nested_pdf.write_bytes(b"%PDF-nested")
        return type("Completed", (), {"stdout": "rendered", "stderr": ""})()

    monkeypatch.setattr(
        "knowledge_product_workflow.publishing.resolve_quarto",
        lambda: QuartoResolution(Path(r"C:\\Program Files\\Quarto\\bin\\quarto.exe"), "默认安装路径", "1.7", ()),
    )
    monkeypatch.setattr("knowledge_product_workflow.publishing.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="输出路径契约失败"):
        render_pdf(qmd_path, workspace.root / "delivery", workspace)

    events = [
        json.loads(line)
        for line in (workspace.root / "runs" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    failure = events[-1]["payload"]
    assert failure["reason"] == "output_path_contract_failed"
    assert failure["nested_outputs"] == [
        str(workspace.root / "delivery" / "output" / "guide" / "delivery" / "book.pdf")
    ]


def test_tex_overflow_in_render_log_blocks_pdf_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    qmd_path = workspace.root / "delivery" / "book.qmd"
    qmd_path.write_text("---\n---\n# Guide\n", encoding="utf-8")
    monkeypatch.setattr(
        "knowledge_product_workflow.publishing.resolve_quarto",
        lambda: QuartoResolution(Path(r"C:\Program Files\Quarto\bin\quarto.exe"), "默认安装路径", "1.7", ()),
    )
    monkeypatch.setattr(
        "knowledge_product_workflow.publishing.subprocess.run",
        lambda *args, **kwargs: type("Completed", (), {"stdout": "Overfull \\hbox (12.0pt too wide)", "stderr": ""})(),
    )

    with pytest.raises(RuntimeError, match="版式溢出"):
        render_pdf(qmd_path, workspace.root / "delivery", workspace)

    assert "Overfull \\hbox" in pdf_render_log_path(workspace).read_text(encoding="utf-8")


def test_render_pdf_cli_refuses_to_skip_workflow_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    (workspace.root / "delivery" / "book.qmd").write_text("---\n---\n# Guide\n", encoding="utf-8")
    monkeypatch.setattr(
        "knowledge_product_workflow.publishing.resolve_quarto",
        lambda: QuartoResolution(None, None, None, ("PATH：不存在",)),
    )

    result = CliRunner().invoke(app, ["render-pdf", "--workspace", str(workspace.root)])

    assert result.exit_code == 1
    assert "当前工作流阶段不允许" in result.output
    assert "Traceback" not in result.output


def test_body_page_budget_uses_actual_chapter_destination(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    from knowledge_product_workflow.publishing import pdf_body_page_numbers

    workspace = Workspace.create(tmp_path, "body-range")
    (workspace.root / "delivery" / "book.md").write_text("# 1. 正文起点\n", encoding="utf-8")
    writer = PdfWriter()
    for _ in range(6):
        writer.add_blank_page(595.28, 841.89)
    writer.add_outline_item("目录", 1)
    writer.add_outline_item("1. 正文起点", 4)
    pdf = workspace.root / "delivery" / "book.pdf"
    writer.write(pdf)
    assert pdf_body_page_numbers(workspace, pdf) == [5, 6]
    writer = PdfWriter()
    writer.add_blank_page(595.28, 841.89)
    writer.add_outline_item("其他正文", 0)
    writer.write(pdf)
    with pytest.raises(ValueError, match="不能猜测正文页数"):
        pdf_body_page_numbers(workspace, pdf)
