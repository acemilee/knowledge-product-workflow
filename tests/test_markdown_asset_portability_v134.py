"""交付 Markdown 图链以文档目录解析，且完整书稿包可迁移。"""

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from knowledge_product_workflow.diagrams import DiagramManifest
from knowledge_product_workflow.presentation import load_or_create_publication_profile
from knowledge_product_workflow.presentation_validation import validate_presentation_markdown
from knowledge_product_workflow.publishing import assemble_book
from knowledge_product_workflow.quarto import resolve_quarto
from knowledge_product_workflow.workflows.product_brief import (
    BriefSemanticFields,
    create_product_brief,
)
from knowledge_product_workflow.workflows.validation import validate_reader_content
from knowledge_product_workflow.workspace import Workspace


def _paired_book(tmp_path: Path) -> tuple[Workspace, Path, Path]:
    workspace = Workspace.create(tmp_path, "portable-assets")
    create_product_brief(workspace, "图链可迁移核验",
                         semantics=BriefSemanticFields(reader_request="制作图链可迁移核验夹具。"))
    load_or_create_publication_profile(workspace, language="zh-CN")
    chapter = workspace.root / "chapters/chapter-01.md"
    chapter.write_text(
        "---\nbook_id: portable-assets\nartifact_type: chapter\nstatus: in_production\n"
        "version: 1\ndepends_on: []\napproved_by: null\n---\n"
        "# 1. 图义核验\n\n![核对图](assets/figures/paired.pdf)\n", encoding="utf-8",
    )
    assets = workspace.root / "assets/figures"
    assets.mkdir(parents=True, exist_ok=True)
    pdf, png = assets / "paired.pdf", assets / "paired.png"
    pdf.write_bytes(b"%PDF-paired-test")
    png.write_bytes(b"paired-png-test")
    manifest = DiagramManifest(
        diagram_id="paired", chapter_number=1, chapter_body_sha256="a" * 64,
        contract_sha256="b" * 64, spec_sha256="c" * 64, svg_sha256="d" * 64,
        pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        png_sha256=hashlib.sha256(png.read_bytes()).hexdigest(),
        caption="核对图", node_count=2, max_label_characters=3,
    )
    (workspace.root / "runs/diagrams").mkdir(parents=True, exist_ok=True)
    (workspace.root / "runs/diagrams/paired.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    return workspace, chapter, png


def test_assembled_markdown_resolves_its_bound_png_from_document_directory(tmp_path):
    workspace, chapter, png = _paired_book(tmp_path)
    original = chapter.read_bytes()

    markdown = assemble_book(workspace)

    target = re.search(r"!\[[^\]]*\]\(([^)]+)\)", markdown.read_text(encoding="utf-8")).group(1)
    assert (markdown.parent / target).resolve() == png.resolve()
    assert target == "../assets/figures/paired.png"
    assert chapter.read_bytes() == original
    qmd = markdown.with_suffix(".qmd").read_text(encoding="utf-8")
    assert "(assets/figures/paired.pdf)" in qmd
    inputs = json.loads((markdown.parent / "book-input-manifest.json").read_text(encoding="utf-8"))["inputs"]
    assert {"assets/figures/paired.png", "assets/figures/paired.pdf", "runs/diagrams/paired.json"} <= {item["path"] for item in inputs}


def test_final_markdown_asset_is_accepted_by_the_real_delivery_validation_path(tmp_path):
    workspace, _, _ = _paired_book(tmp_path)
    assemble_book(workspace)

    findings = validate_reader_content(workspace, include_delivery_book=True)

    assert not [item for item in findings if item.location.startswith("delivery/book.md")
                and item.code.startswith("PRESENTATION_ASSET")]


@pytest.mark.parametrize("target", ["../../outside.png", "../assets/figures/missing.png", "assets/../outside.png"])
def test_unknown_or_escaping_assets_are_rejected_before_replacing_the_delivery(tmp_path, target):
    workspace, chapter, _ = _paired_book(tmp_path)
    chapter.write_text(chapter.read_text(encoding="utf-8").replace("assets/figures/paired.pdf", target), encoding="utf-8")
    original = chapter.read_bytes()

    with pytest.raises(ValueError, match="图链"):
        assemble_book(workspace)

    assert chapter.read_bytes() == original
    assert not (workspace.root / "delivery/book.md").exists()


def test_the_complete_book_package_preserves_image_hashes_after_relocation(tmp_path):
    workspace, chapter, png = _paired_book(tmp_path)
    markdown = assemble_book(workspace)
    target = re.search(r"!\[[^\]]*\]\(([^)]+)\)", markdown.read_text(encoding="utf-8")).group(1)
    relocated = tmp_path / "relocated/portable-assets"

    shutil.copytree(workspace.root, relocated)

    relocated_image = (relocated / "delivery" / target).resolve()
    assert relocated_image.is_file()
    assert hashlib.sha256(relocated_image.read_bytes()).hexdigest() == hashlib.sha256(png.read_bytes()).hexdigest()
    assert (relocated / chapter.relative_to(workspace.root)).read_bytes() == chapter.read_bytes()
    inputs = json.loads((relocated / "delivery/book-input-manifest.json").read_text(encoding="utf-8"))["inputs"]
    assert all(hashlib.sha256((relocated / item["path"]).read_bytes()).hexdigest() == item["sha256"] for item in inputs)
    moved_workspace = Workspace(relocated)
    assert assemble_book(moved_workspace).read_bytes() == markdown.read_bytes()


def test_legacy_root_relative_assets_are_read_without_migrating_old_input(tmp_path):
    workspace, _, _ = _paired_book(tmp_path)
    legacy = "![核对图](assets/figures/paired.png)\n"
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text(legacy, encoding="utf-8")

    assert validate_presentation_markdown(legacy, "delivery/book.md", workspace) == []
    assert markdown.read_text(encoding="utf-8") == legacy


def test_symlink_asset_outside_the_book_workspace_is_rejected(tmp_path):
    workspace, chapter, _ = _paired_book(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside-controlled-book")
    link = workspace.root / "assets/figures/escaped.png"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"当前Windows无symlink权限；不得视为原生外跳验证通过：{error}")
    chapter.write_text(chapter.read_text(encoding="utf-8").replace("assets/figures/paired.pdf", "assets/figures/escaped.png"), encoding="utf-8")

    with pytest.raises(ValueError, match="越出"):
        assemble_book(workspace)

    assert not (workspace.root / "delivery/book.md").exists()
    findings = validate_presentation_markdown("![图](../assets/figures/escaped.png)", "delivery/book.md", workspace)
    assert any(item.code == "PRESENTATION_ASSET_OUTSIDE_WORKSPACE" for item in findings)


def test_markdown_image_teaching_code_is_preserved_beside_a_real_bound_image(tmp_path):
    workspace, chapter, _ = _paired_book(tmp_path)
    samples = (
        "```markdown\n![示例](assets/figures/paired.pdf)\n![未知图示例](../../unknown.png)\n```",
        "~~~markdown\n![示例](assets/figures/paired.pdf)\n~~~",
        "    ![示例](assets/figures/paired.pdf)",
        "内联语法为 `![示例](assets/figures/paired.pdf)`。",
        "> ```markdown\n> ![示例](assets/figures/paired.pdf)\n> ```",
        "- ```markdown\n  ![示例](assets/figures/paired.pdf)\n  ```",
    )
    chapter.write_text(chapter.read_text(encoding="utf-8") + "\n\n".join(samples) + "\n", encoding="utf-8")
    original = chapter.read_bytes()

    markdown = assemble_book(workspace)

    reader, qmd = markdown.read_text(encoding="utf-8"), markdown.with_suffix(".qmd").read_text(encoding="utf-8")
    assert "![核对图](../assets/figures/paired.png)" in reader
    assert all(sample in reader and sample in qmd for sample in samples)
    assert chapter.read_bytes() == original
    quarto = resolve_quarto()
    if not quarto.available:
        pytest.skip("需要现有Pandoc的真实Image/Code AST；跳过不视为AST保真通过")
    ast = json.loads(subprocess.run([str(quarto.path), "pandoc", "-f", "markdown", "-t", "json"],
                                   input=reader.encode("utf-8"), capture_output=True,
                                   check=True, timeout=30).stdout)
    nodes = {kind: [] for kind in ("Image", "Code", "CodeBlock")}

    def walk(node):
        if isinstance(node, dict):
            if node.get("t") in nodes:
                nodes[node["t"]].append(node["c"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(ast)
    assert len(nodes["Image"]) == 1
    assert nodes["Image"][0][-1][0] == "../assets/figures/paired.png"
    assert len(nodes["CodeBlock"]) == 5
    assert all("assets/figures/paired.pdf" in node[-1] for node in nodes["CodeBlock"])
    assert nodes["Code"][0][-1] == "![示例](assets/figures/paired.pdf)"


def test_an_existing_unknown_figure_is_not_promoted_into_the_bound_reader_package(tmp_path):
    workspace, chapter, _ = _paired_book(tmp_path)
    unknown = workspace.root / "assets/figures/unknown.png"
    unknown.write_bytes(b"unbound-local-png")
    chapter.write_text(chapter.read_text(encoding="utf-8").replace("assets/figures/paired.pdf", "assets/figures/unknown.png"), encoding="utf-8")

    with pytest.raises(ValueError, match="DIAGRAM_ASSET_UNBOUND"):
        assemble_book(workspace)

    assert not (workspace.root / "delivery/book.md").exists()
