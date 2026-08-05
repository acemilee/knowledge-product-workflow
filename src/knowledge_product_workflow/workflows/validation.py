"""Deterministic source, detail, and delivery validation workflows."""

from pathlib import Path

from pypdf.errors import PdfReadError

from knowledge_product_workflow.assumption_ledger import (
    required_assumption_claim_ids,
    validate_assumptions,
)
from knowledge_product_workflow.chapter_compliance import validate_current_chapter_compliance
from knowledge_product_workflow.claim_quality import validate_undeclared_high_risk_claims
from knowledge_product_workflow.claims import current_claim_contract_bindings
from knowledge_product_workflow.content_quality import scan_markdown_content
from knowledge_product_workflow.detail_constraints import ReviewPolicy, count_words
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.pdf_visual import validate_pdf_visual_contract
from knowledge_product_workflow.presentation_validation import validate_presentation_markdown
from knowledge_product_workflow.publishing import (
    count_pdf_bookmarks,
    count_pdf_external_links,
    count_pdf_pages,
    delivery_component_leak_findings,
    missing_pdf_source_contracts,
    pdf_font_size_contract_findings,
    release_readiness_findings,
)
from knowledge_product_workflow.quality import (
    QualityFinding,
    QualityReport,
    Severity,
    write_quality_report,
)
from knowledge_product_workflow.quality_exceptions import apply_controlled_exceptions
from knowledge_product_workflow.source_ledger import validate_claims
from knowledge_product_workflow.workflows.curriculum import parse_chapter_budgets
from knowledge_product_workflow.workflows.product_brief import parse_detail_constraints
from knowledge_product_workflow.workspace import Workspace

_RECOMPUTED_CODES = {
    "BOOK_WORD_RANGE_VIOLATION",
    "CHAPTER_BUDGET_RANGE_WARNING",
    "CHAPTER_BUDGET_CONTRACT_INVALID",
    "PDF_REQUIRED",
    "PDF_RENDER_FAILED",
    "PDF_PAGE_COUNT_NOT_MET",
    "PDF_PAGE_COUNT_EXCEEDED",
    "PDF_DIAGRAM_CAPTION_DUPLICATED",
    "PDF_DIAGRAM_MANUAL_VISUAL_REVIEW_REQUIRED",
    "PDF_BOOKMARKS_MISSING",
    "LAYOUT_CONTRACT_MISMATCH",
    "PDF_COVER_CONTRACT_MISSING",
    "PDF_TOC_CONTRACT_MISSING",
    "PDF_CHINESE_STYLE_CONTRACT_MISSING",
    "PDF_PUBLICATION_METADATA_MISSING",
    "PDF_FONT_SIZE_CONTRACT_FAILED",
    "PDF_INTERNAL_COMPONENT_LEAK",
    "PDF_SOURCE_LINKS_MISSING",
    "DUPLICATE_CONTENT_DETECTED",
    "DUPLICATE_HEADING_DETECTED",
    "UNRESOLVED_TEMPLATE_DETECTED",
    "INTERNAL_TRACE_LEAKED",
    "INTERNAL_SOURCE_LEAKED",
    "FORBIDDEN_READER_HEADING",
    "MALFORMED_MARKDOWN_STRUCTURE",
    "HORIZONTAL_RULE_DETECTED",
    "UNDECLARED_HIGH_RISK_CLAIM",
    "PERCENTAGE_TOTAL_INVALID",
    "RESUME_EVIDENCE_BOUNDARY_MISSING",
    "PRESENTATION_REMOTE_ASSET",
    "PRESENTATION_ASSET_OUTSIDE_WORKSPACE",
    "PRESENTATION_ASSET_TYPE_INVALID",
    "PRESENTATION_ASSET_MISSING",
    "PRESENTATION_COMPONENT_INVALID",
    "PRESENTATION_SOURCE_CARD_INVALID",
    "PRESENTATION_RAW_TEX",
    "PRESENTATION_RAW_HTML",
    "REVIEW_BATCH_MISSING",
    "REVIEW_DISPATCH_UNAVAILABLE",
    "REVIEW_DISPATCH_FAILED",
    "REVIEW_TASK_RECEIPT_MISSING",
    "REVIEW_TASK_PENDING",
    "REVIEW_TASKS_BLOCKED",
    "REVIEW_BATCH_SUPERSEDED",
    "REVIEW_SNAPSHOT_INVALID",
    "REVIEW_RESULT_BINDING_MISMATCH",
    "REVIEW_DISPATCH_NOT_PRODUCTION",
    "REVIEW_RESULT_FAILED",
    "REVIEW_REQUIREMENT_MISSING",
    "REVIEW_INDEPENDENCE_UNVERIFIED",
    "ASSUMPTION_LEDGER_INVALID",
    "ASSUMPTION_UNRESOLVED",
    "ASSUMPTION_EVIDENCE_MISSING",
    "RELEASE_BRIEF_INVALID",
    "RELEASE_BRIEF_NOT_APPROVED",
    "RELEASE_BLUEPRINT_STALE",
    "RELEASE_BLUEPRINT_NOT_APPROVED",
    "RELEASE_SAMPLE_NOT_APPROVED",
    "RELEASE_CONTRACT_STALE",
    "RELEASE_CHAPTER_MISSING",
    "RELEASE_BOOK_INPUTS_MISSING",
    "RELEASE_BOOK_STALE",
    "RELEASE_BOOK_INPUT_MANIFEST_MISSING",
    "RELEASE_BOOK_INPUT_MANIFEST_INVALID",
    "RELEASE_INPUT_HASH_MISMATCH",
}


def validate_detail_constraints(workspace: Workspace, include_pdf: bool = False) -> list[QualityFinding]:
    """Return blockers for approved word, PDF, page, and layout requirements."""
    brief_path = workspace.root / "planning" / "product-brief.md"
    if not brief_path.exists():
        return []
    _, brief_body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    constraints = parse_detail_constraints(brief_body)
    if constraints is None:
        return []

    findings: list[QualityFinding] = []
    budgets = {}
    blueprint_path = workspace.root / "planning" / "curriculum-blueprint.md"
    if blueprint_path.exists():
        _, blueprint_body = parse_markdown_artifact(blueprint_path.read_text(encoding="utf-8"))
        try:
            budgets = parse_chapter_budgets(blueprint_body, constraints)
        except ValueError as error:
            findings.append(
                QualityFinding(
                    code="CHAPTER_BUDGET_CONTRACT_INVALID",
                    severity=Severity.BLOCKER,
                    location="planning/curriculum-blueprint.md",
                    message=f"章节预算与已批准 Brief 不一致：{error}",
                )
            )

    chapter_word_total = 0
    for chapter_path in sorted(workspace.root.joinpath("chapters").glob("chapter-[0-9][0-9].md")):
        _, chapter_body = parse_markdown_artifact(chapter_path.read_text(encoding="utf-8"))
        measured_words = count_words(chapter_body)
        chapter_word_total += measured_words
        chapter_number = int(chapter_path.stem.split("-")[1])
        budget = budgets.get(chapter_number)
        if budget and budget.word_min is not None and budget.word_max is not None and (
            measured_words < budget.word_min or measured_words > budget.word_max
        ):
            findings.append(
                QualityFinding(
                    code="CHAPTER_BUDGET_RANGE_WARNING",
                    severity=Severity.WARNING,
                    location=str(chapter_path.relative_to(workspace.root)).replace("\\", "/"),
                    message=(
                        f"本章当前字数为 {measured_words}，已偏离批准区间 "
                        f"{budget.word_min}-{budget.word_max} 字；请结合教学单元完整性调整。"
                    ),
                )
            )
    if constraints.word_min is not None and constraints.word_max is not None and (
        chapter_word_total < constraints.word_min or chapter_word_total > constraints.word_max
    ):
        findings.append(
            QualityFinding(
                code="BOOK_WORD_RANGE_VIOLATION",
                severity=Severity.BLOCKER,
                location="delivery/book.md",
                message=(
                    f"全书当前字数为 {chapter_word_total}，未落在已批准区间 "
                    f"{constraints.word_min}-{constraints.word_max} 字内。"
                ),
            )
        )

    if not include_pdf or not constraints.requires_pdf:
        return findings
    pdf_path = workspace.root / "delivery" / "book.pdf"
    if not pdf_path.is_file():
        return findings + [
            QualityFinding(
                code="PDF_REQUIRED",
                severity=Severity.BLOCKER,
                location="delivery/book.pdf",
                message="已批准的交付格式要求生成 PDF",
            )
        ]
    qmd_path = workspace.root / "delivery" / "book.qmd"
    contract_messages = {
        "LAYOUT_CONTRACT_MISMATCH": "PDF 缺少已批准的 A4 标准版式源契约",
        "PDF_COVER_CONTRACT_MISSING": "PDF 缺少工作流生成的固定封面页源契约",
            "PDF_TOC_CONTRACT_MISSING": "PDF 缺少独立目录页源契约",
            "PDF_CHINESE_STYLE_CONTRACT_MISSING": "PDF 缺少固定中文排版、标题、段落和代码样式源契约",
            "PDF_INLINE_CODE_STYLE_CONTRACT_MISSING": "PDF 缺少固定行内代码灰色圆角样式源契约",
            "PDF_PUBLICATION_METADATA_MISSING": "PDF 缺少已冻结的封面出版元数据源契约",
            "PDF_FONT_SIZE_CONTRACT_FAILED": "PDF 实际正文字号与已批准出版配置不一致",
    }
    for code in missing_pdf_source_contracts(qmd_path):
        findings.append(
            QualityFinding(
                code=code,
                severity=Severity.BLOCKER,
                location="delivery/book.qmd",
                    message=contract_messages.get(code, f"PDF 源契约检查未通过：{code}"),
            )
        )
    try:
        page_count = count_pdf_pages(pdf_path)
    except (OSError, PdfReadError, ValueError) as error:
        findings.append(
            QualityFinding(
                code="PDF_RENDER_FAILED",
                severity=Severity.BLOCKER,
                location="delivery/book.pdf",
                message=f"无法读取已渲染的 PDF：{error}",
            )
        )
        return findings
    findings.extend(pdf_font_size_contract_findings(workspace))
    findings.extend(delivery_component_leak_findings(workspace, include_pdf=True))
    findings.extend(validate_pdf_visual_contract(workspace))
    page_budget = constraints.page_budget
    if page_budget and page_budget.minimum is not None and page_count < page_budget.minimum:
        scope_label = "正文" if page_budget.scope.value == "body_only" else "最终 PDF"
        upper = f"-{page_budget.maximum}" if page_budget.maximum is not None else ""
        findings.append(
            QualityFinding(
                code="PDF_PAGE_COUNT_NOT_MET",
                severity=Severity.BLOCKER,
                location="delivery/book.pdf",
                message=(
                    f"{scope_label}当前为 {page_count} 页，未达到已批准页数约束 "
                    f"{page_budget.minimum}{upper} 页的下限。"
                ),
            )
        )
    if page_budget and page_budget.maximum is not None and page_count > page_budget.maximum:
        findings.append(
            QualityFinding(
                code="PDF_PAGE_COUNT_EXCEEDED",
                severity=Severity.BLOCKER,
                location="delivery/book.pdf",
                message=(
                    f"PDF 当前为 {page_count} 页，超出已批准页数约束的上限 "
                    f"{page_budget.maximum} 页。"
                ),
            )
        )
    if count_pdf_bookmarks(pdf_path) == 0:
        findings.append(
            QualityFinding(
                code="PDF_BOOKMARKS_MISSING",
                severity=Severity.BLOCKER,
                location="delivery/book.pdf",
                message="PDF 缺少可导航的书签；请通过受控 Quarto 渲染链路重新生成。",
            )
        )
    if (
        qmd_path.is_file()
        and ".kp-source" in qmd_path.read_text(encoding="utf-8")
        and count_pdf_external_links(pdf_path) == 0
    ):
        findings.append(
            QualityFinding(
                code="PDF_SOURCE_LINKS_MISSING",
                severity=Severity.BLOCKER,
                location="delivery/book.pdf",
                message="书稿包含来源卡片，但 PDF 中未发现可点击的外部来源链接。",
            )
        )
    return findings


def validate_reader_content(
    workspace: Workspace, include_delivery_book: bool = False
) -> list[QualityFinding]:
    """Scan reader-visible chapter and final-book text without altering either artifact."""
    findings: list[QualityFinding] = []
    for chapter_path in sorted(workspace.root.joinpath("chapters").glob("chapter-[0-9][0-9].md")):
        _, chapter_body = parse_markdown_artifact(chapter_path.read_text(encoding="utf-8"))
        findings.extend(
            scan_markdown_content(
                chapter_body,
                str(chapter_path.relative_to(workspace.root)).replace("\\", "/"),
            )
        )
        findings.extend(
            validate_presentation_markdown(
                chapter_body,
                str(chapter_path.relative_to(workspace.root)).replace("\\", "/"),
                workspace,
            )
        )
        findings.extend(validate_current_chapter_compliance(workspace, chapter_path))
        findings.extend(validate_undeclared_high_risk_claims(workspace, chapter_path, chapter_body))
    book_path = workspace.root / "delivery" / "book.md"
    if include_delivery_book and book_path.is_file():
        book_markdown = book_path.read_text(encoding="utf-8")
        findings.extend(scan_markdown_content(book_markdown, "delivery/book.md"))
        findings.extend(validate_presentation_markdown(book_markdown, "delivery/book.md", workspace))
    return findings


def _collect_validation_findings(
    workspace: Workspace, claim_ids: set[str] | None = None
) -> list[QualityFinding]:
    """Compute the deterministic full-book findings without changing report state."""
    assumption_ledger = workspace.root / "research" / "assumption-ledger.jsonl"
    try:
        claim_bindings = current_claim_contract_bindings(workspace)
    except (OSError, ValueError):
        claim_bindings = {}
    required_claims = set(claim_bindings) if claim_bindings else (claim_ids or set())
    findings = validate_claims(
        required_claims | required_assumption_claim_ids(assumption_ledger),
        workspace.root / "research" / "source-ledger.jsonl",
        expected_contract_sha256=claim_bindings or None,
    )
    findings.extend(validate_assumptions(assumption_ledger))
    findings.extend(validate_detail_constraints(workspace))
    findings.extend(validate_reader_content(workspace))
    brief_path = workspace.root / "planning" / "product-brief.md"
    if brief_path.is_file():
        _, brief_body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
        constraints = parse_detail_constraints(brief_body)
        if constraints is not None and constraints.review_policy is ReviewPolicy.INDEPENDENT_REVIEW_REQUIRED:
            from knowledge_product_workflow.review_batches import validate_review_batches

            findings.extend(validate_review_batches(workspace))
    return apply_controlled_exceptions(workspace, findings)


def run_validation(workspace: Workspace, claim_ids: set[str] | None = None) -> tuple[Path, QualityReport]:
    """Validate current source inputs and publish a new immutable full-book report revision."""
    output, report = write_quality_report(workspace, "quality", _collect_validation_findings(workspace, claim_ids))
    workspace.append_event(
        "validation_completed",
        {
            "report_id": report.report_id,
            "finding_count": len(report.findings),
            "publishable": report.publishable,
        },
    )
    return output, report


def run_delivery_validation(workspace: Workspace) -> tuple[Path, QualityReport]:
    """Validate the exact source and delivery graph in one new immutable report revision."""
    findings = _collect_validation_findings(workspace)
    findings.extend(
        finding
        for finding in validate_detail_constraints(workspace, include_pdf=True)
        if finding.code.startswith("PDF_") or finding.code == "LAYOUT_CONTRACT_MISMATCH"
    )
    findings.extend(
        finding
        for finding in validate_reader_content(workspace, include_delivery_book=True)
        if finding.location.startswith("delivery/book.md")
    )
    findings.extend(release_readiness_findings(workspace))
    output, report = write_quality_report(workspace, "delivery", findings)
    workspace.append_event(
        "delivery_validation_completed",
        {
            "report_id": report.report_id,
            "finding_count": len(report.findings),
            "publishable": report.publishable,
        },
    )
    return output, report
