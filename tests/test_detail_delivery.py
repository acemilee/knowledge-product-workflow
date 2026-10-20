from pathlib import Path

import pytest
from pypdf import PdfWriter

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import render_markdown_artifact
from knowledge_product_workflow.models import ArtifactMetadata, ArtifactStatus
from knowledge_product_workflow.pdf_visual import (
    record_manual_pdf_visual_review,
    validate_pdf_visual_contract,
)
from knowledge_product_workflow.publishing import (
    count_pdf_bookmarks,
    create_delivery_manifest,
    create_quarto_source,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workflows.validation import (
    run_delivery_validation,
    validate_detail_constraints,
)
from knowledge_product_workflow.workspace import Workspace


def test_book_word_range_underflow_is_a_blocker(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "Guide", DetailConstraints(target_words=100))
    chapter = workspace.root / "chapters" / "chapter-01.md"
    chapter.write_text("---\nbook_id: guide\nartifact_type: chapter\nstatus: in_production\nversion: 1\ndepends_on: []\napproved_by: null\n---\n# Short\n", encoding="utf-8")

    findings = validate_detail_constraints(workspace)

    assert any(finding.code == "BOOK_WORD_RANGE_VIOLATION" for finding in findings)


def test_book_word_range_overflow_and_chapter_range_are_reported(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    constraints = DetailConstraints(word_min=10, word_target=20, word_max=30)
    create_product_brief(workspace, "Guide", constraints)
    blueprint = ArtifactMetadata(
        book_id="guide",
        artifact_type="curriculum_blueprint",
        status=ArtifactStatus.READY_FOR_REVIEW,
        version=1,
    )
    (workspace.root / "planning" / "curriculum-blueprint.md").write_text(
        render_markdown_artifact(
            blueprint,
            """## 章节预算

```yaml
chapter_budgets:
  - chapter_number: 1
    weight: 1
    word_min: 10
    word_target: 20
    word_max: 30
    rationale: 仅用于测试
    required_units: [concept_explanation]
```
""",
        ),
        encoding="utf-8",
    )
    (workspace.root / "chapters" / "chapter-01.md").write_text(
        "---\nbook_id: guide\nartifact_type: chapter\nstatus: in_production\nversion: 1\ndepends_on: []\napproved_by: null\n---\n" + "字" * 31,
        encoding="utf-8",
    )

    findings = validate_detail_constraints(workspace)

    assert any(finding.code == "BOOK_WORD_RANGE_VIOLATION" for finding in findings)
    assert any(
        finding.code == "CHAPTER_BUDGET_RANGE_WARNING" and finding.severity.value == "warning"
        for finding in findings
    )


def test_pdf_page_shortfall_blocks_manifest(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(
        workspace,
        "Guide",
        DetailConstraints(target_pages=2, delivery_format="pdf"),
    )
    markdown_path = workspace.root / "delivery" / "book.md"
    markdown_path.write_text("# Guide\n", encoding="utf-8")
    (workspace.root / "delivery" / "book.qmd").write_text("papersize: a4\n", encoding="utf-8")
    pdf_path = workspace.root / "delivery" / "book.pdf"
    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    with pdf_path.open("wb") as handle:
        writer.write(handle)
    report_path, report = run_delivery_validation(workspace)

    assert report_path.exists()
    assert any(finding.code == "PDF_PAGE_COUNT_NOT_MET" for finding in report.findings)
    with pytest.raises(ValueError, match="质量报告未通过"):
        create_delivery_manifest(workspace, markdown_path, pdf_path)


def test_pdf_page_interval_only_blocks_below_its_minimum(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(
        workspace,
        "Guide",
        DetailConstraints(page_min=10, page_target=15, page_max=20, delivery_format="pdf"),
    )
    pdf_path = workspace.root / "delivery" / "book.pdf"
    writer = PdfWriter()
    for _ in range(9):
        writer.add_blank_page(595, 842)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    shortfall = validate_detail_constraints(workspace, include_pdf=True)

    assert any(finding.code == "PDF_PAGE_COUNT_NOT_MET" for finding in shortfall)

    writer = PdfWriter()
    for _ in range(10):
        writer.add_blank_page(595, 842)
    with pdf_path.open("wb") as handle:
        writer.write(handle)
    minimum_met = validate_detail_constraints(workspace, include_pdf=True)

    assert not any(finding.code == "PDF_PAGE_COUNT_NOT_MET" for finding in minimum_met)


def test_pdf_requirement_and_corrupt_pdf_are_both_blockers(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "Guide", DetailConstraints(target_pages=1, delivery_format="pdf"))

    missing_pdf_findings = validate_detail_constraints(workspace, include_pdf=True)
    assert [finding.code for finding in missing_pdf_findings] == ["PDF_REQUIRED"]

    (workspace.root / "delivery" / "book.qmd").write_text(
        "papersize: a4\nfontsize: 14pt\n", encoding="utf-8"
    )
    (workspace.root / "delivery" / "book.pdf").write_bytes(b"not a PDF")
    corrupt_pdf_findings = validate_detail_constraints(workspace, include_pdf=True)

    assert any(finding.code == "PDF_RENDER_FAILED" for finding in corrupt_pdf_findings)


def test_missing_fixed_pdf_contract_is_a_layout_contract_blocker(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "Guide", DetailConstraints(target_pages=1, delivery_format="pdf"))
    (workspace.root / "delivery" / "book.qmd").write_text("papersize: a4\n", encoding="utf-8")
    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    with (workspace.root / "delivery" / "book.pdf").open("wb") as handle:
        writer.write(handle)

    findings = validate_detail_constraints(workspace, include_pdf=True)

    codes = {finding.code for finding in findings}
    assert "LAYOUT_CONTRACT_MISMATCH" in codes
    assert "PDF_COVER_CONTRACT_MISSING" in codes
    assert "PDF_TOC_CONTRACT_MISSING" in codes
    assert "PDF_CHINESE_STYLE_CONTRACT_MISSING" in codes
    assert "PDF_INLINE_CODE_STYLE_CONTRACT_MISSING" in codes


def test_replaced_pdf_style_asset_blocks_delivery(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "Guide", DetailConstraints(target_pages=1, delivery_format="pdf"))
    markdown_path = workspace.root / "delivery" / "book.md"
    markdown_path.write_text("# Guide\n", encoding="utf-8")
    create_quarto_source(workspace, markdown_path)
    (workspace.root / "delivery" / "knowledge-book-style.tex").write_text(
        "% replaced without contract marker\n", encoding="utf-8"
    )
    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    with (workspace.root / "delivery" / "book.pdf").open("wb") as handle:
        writer.write(handle)

    findings = validate_detail_constraints(workspace, include_pdf=True)

    assert any(finding.code == "PDF_CHINESE_STYLE_CONTRACT_MISSING" for finding in findings)


def test_pdf_diagram_layout_filter_is_part_of_the_fixed_rendering_contract(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "Guide", DetailConstraints(target_pages=1, delivery_format="pdf"))
    markdown_path = workspace.root / "delivery" / "book.md"
    markdown_path.write_text("# Guide\n\n![流程](assets/figures/flow.pdf)\n", encoding="utf-8")

    create_quarto_source(workspace, markdown_path)

    filter_source = (workspace.root / "delivery" / "knowledge-book-components.lua").read_text(
        encoding="utf-8"
    )
    assert "knowledge-product-pdf-v6-diagram-layout" in filter_source
    assert 'element.attributes["width"] = "100%"' in filter_source
    assert 'element.attributes["height"] = "145mm"' in filter_source


def test_pdf_bookmarks_are_required_for_pdf_delivery(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "Guide", DetailConstraints(target_pages=1, delivery_format="pdf"))
    markdown_path = workspace.root / "delivery" / "book.md"
    markdown_path.write_text("# Guide\n", encoding="utf-8")
    create_quarto_source(workspace, markdown_path)
    pdf_path = workspace.root / "delivery" / "book.pdf"
    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    missing_bookmark_codes = {finding.code for finding in validate_detail_constraints(workspace, include_pdf=True)}
    assert "PDF_BOOKMARKS_MISSING" in missing_bookmark_codes

    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    writer.add_outline_item("目录", 0)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    assert count_pdf_bookmarks(pdf_path) == 1
    assert "PDF_BOOKMARKS_MISSING" not in {
        finding.code for finding in validate_detail_constraints(workspace, include_pdf=True)
    }


def test_pdf_diagram_visual_review_is_bound_to_current_pdf_bytes(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    pdf_path = workspace.root / "delivery" / "book.pdf"
    (workspace.root / "runs" / "diagrams").mkdir(parents=True)
    (workspace.root / "runs" / "diagrams" / "agent-layers.json").write_text("{}\n", encoding="utf-8")

    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    assert {finding.code for finding in validate_pdf_visual_contract(workspace)} == {
        "PDF_DIAGRAM_MANUAL_VISUAL_REVIEW_REQUIRED"
    }
    from knowledge_product_workflow.pdf_preview import render_pdf_preview_set

    render_pdf_preview_set(pdf_path, workspace.root / "runs" / "pdf-preview")
    record_manual_pdf_visual_review(workspace, "用户")
    assert validate_pdf_visual_contract(workspace) == []

    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    writer.add_blank_page(595, 842)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    assert {finding.code for finding in validate_pdf_visual_contract(workspace)} == {
        "PDF_DIAGRAM_MANUAL_VISUAL_REVIEW_REQUIRED"
    }


def test_manifest_records_rendering_source_hashes(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    # This is a legacy rendering-manifest fixture, not a new approved product.
    from knowledge_product_workflow.presentation import (
        PresentationOverrides,
        load_or_create_publication_profile,
    )
    load_or_create_publication_profile(workspace, language="zh-CN",
                                       overrides=PresentationOverrides(cover_layout_revision=1))
    markdown_path = workspace.root / "delivery" / "book.md"
    markdown_path.write_text("# Guide\n", encoding="utf-8")
    create_quarto_source(workspace, markdown_path)
    pdf_path = workspace.root / "delivery" / "book.pdf"
    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    writer.add_outline_item("目录", 0)
    with pdf_path.open("wb") as handle:
        writer.write(handle)
    _, report = run_delivery_validation(workspace)
    assert report.publishable, report.findings

    manifest_path = create_delivery_manifest(workspace, markdown_path, pdf_path)

    manifest = manifest_path.read_text(encoding="utf-8")
    assert "rendering_inputs:" in manifest
    assert "book.qmd" in manifest
    assert "knowledge-book-cover.tex" in manifest
    assert "knowledge-book-style.tex" in manifest
