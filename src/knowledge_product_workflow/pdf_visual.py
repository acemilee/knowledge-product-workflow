"""Version-bound PDF diagram visual review records and deterministic fallbacks."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pypdf import PdfReader

from knowledge_product_workflow.pdf_preview import (
    current_pdf_preview,
    preview_manifest_path,
)
from knowledge_product_workflow.presentation import (
    PRESENTATION_PROFILE_ID,
    V5_PRESENTATION_PROFILE_ID,
    PublicationMetadata,
)
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.visual_audit import audit_path, current_visual_audit, full_visual_observation_coverage
from knowledge_product_workflow.workspace import Workspace

_DUPLICATE_CAPTION = re.compile(
    r"(?:图|figure)\s*(?P<number>\d+)\s*[:：.、-]?\s*(?:图|figure)\s*(?P=number)",
    re.IGNORECASE,
)


class PdfVisualReview(BaseModel):
    """A human visual verdict tied to the exact PDF bytes being delivered."""

    schema_version: int = 1
    reviewed_at: datetime
    reviewed_by: str = Field(min_length=1)
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    visual_audit_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result: str = Field(pattern=r"^passed$")
    resolved_style_items: list[str] = Field(default_factory=list)
    style_anchor_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confirmed_scope: list[str] = Field(default_factory=lambda: ["visual"])
    manual_coverage_confirmed: bool = False
    user_response: str | None = None


def _review_path(workspace: Workspace) -> Path:
    return workspace.root / "runs" / "pdf-visual-review.json"


def _preview_directory(workspace: Workspace) -> Path:
    return workspace.root / "runs" / "pdf-preview"


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _has_diagram_assets(workspace: Workspace) -> bool:
    return any((workspace.root / "runs" / "diagrams").glob("*.json"))


def _uses_v5_profile(workspace: Workspace) -> bool:
    path = workspace.root / "delivery" / "publication-metadata.yaml"
    try:
        metadata = PublicationMetadata.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return False
    return metadata.profile_id in {PRESENTATION_PROFILE_ID, V5_PRESENTATION_PROFILE_ID}


def uses_v6_profile(workspace: Workspace) -> bool:
    path = workspace.root / "delivery" / "publication-metadata.yaml"
    try:
        return PublicationMetadata.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).profile_id == PRESENTATION_PROFILE_ID
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return False


def preview_repair_needed(workspace: Workspace) -> bool:
    """Identify the missing derivative separately from a missing or invalid PDF."""
    pdf_path = workspace.root / "delivery" / "book.pdf"
    return (
        pdf_path.is_file()
        and _uses_v5_profile(workspace)
        and current_pdf_preview(pdf_path, _preview_directory(workspace)) is None
    )


def _current_style_anchor(workspace: Workspace) -> str | None:
    from knowledge_product_workflow.illustrations import current_profile, style_resources
    if current_profile(workspace).illustration_style_revision == 2:
        from knowledge_product_workflow.diagrams import _FIGURE_REFERENCE, DiagramManifest
        source = workspace.root / "delivery/book.qmd"
        if not source.is_file():
            return None
        for reference in _FIGURE_REFERENCE.finditer(source.read_text(encoding="utf-8")):
            target = reference.group("target")
            if not target.startswith("assets/figures/"):
                continue
            diagram_id = Path(target).stem
            manifest_path = workspace.root / "runs/diagrams" / f"{diagram_id}.json"
            if not manifest_path.is_file():
                raise ValueError("当前成书图引用缺少清单，不能猜测母版是否适用")
            manifest = DiagramManifest.model_validate_json(manifest_path.read_bytes())
            if manifest.renderer in {"host-image", "host-image+svg"}:
                return style_resources(2)["anchor_sha256"]
    return None


def record_manual_pdf_visual_review(workspace: Workspace, reviewed_by: str, *, confirm_style_anchor: bool = False,
                                   confirm_manual_coverage: bool = False, confirm_content: bool = False,
                                   user_response: str | None = None) -> Path:
    """Record a human check of caption, legibility, clipping, and placement."""
    pdf_path = workspace.root / "delivery" / "book.pdf"
    identity = reviewed_by.strip()
    if (confirm_manual_coverage or confirm_content) and not (user_response and user_response.strip()):
        raise ValueError("人工覆盖/内容确认须保存本次实际用户原话；测试审批不能替代最终批准")
    if not pdf_path.is_file():
        raise FileNotFoundError("人工视觉复核前必须先生成当前 PDF")
    if not identity:
        raise ValueError("人工视觉复核需要有效审核身份")
    current = current_pdf_preview(pdf_path, _preview_directory(workspace))
    if current is None:
        raise ValueError("当前 PDF 的页面预览不完整或已过期；请先运行 kp repair-pdf-preview。")
    preview_path, _preview = current
    audit = current_visual_audit(workspace) if uses_v6_profile(workspace) else None
    if uses_v6_profile(workspace):
        if audit is None:
            raise ValueError("v6 人工复核前须先运行 kp audit-pdf-visual，查看当前逐页量测及清晰预览。")
        if any(finding.severity is Severity.BLOCKER for finding in audit.findings) or any(item.assessment == "failed" for item in audit.observations):
            raise ValueError("视觉报告仍有 blocker 或失败观察；修复并重新渲染后才能记录人工通过。")
        if not full_visual_observation_coverage(audit) and not confirm_manual_coverage:
            raise ValueError("最终全书视觉验收须覆盖全部当前清晰页；抽样不能冒全书通过，完整人工覆盖须保存实际用户原话。")
        from knowledge_product_workflow.illustrations import current_profile
        if current_profile(workspace).illustration_style_revision:
            observed = {item.page_number for item in audit.observations if item.assessment == "passed"}
            if observed != set(audit.sampled_pages) and not confirm_manual_coverage:
                raise ValueError("新插图风格人工门前须覆盖全部当前抽样页，解决主观待决并记录passed观察；不得静默自动放行")
    pdf_sha256 = _sha256(pdf_path)
    preview_sha256 = _sha256(preview_path)
    audit_sha256 = _sha256(audit_path(workspace)) if audit is not None else None
    anchor_sha256 = _current_style_anchor(workspace) if uses_v6_profile(workspace) else None
    if anchor_sha256:
        from knowledge_product_workflow.illustrations import style_resources
        if style_resources(2).get("approval_state") != "approved" and not confirm_style_anchor:
            raise ValueError("当前中性母版仍是候选；先展示母版与PDF并取得用户明确认可，再绑定母版确认。")
    previous = _read_review(workspace)
    if previous is not None and previous.reviewed_by == identity and previous.pdf_sha256 == pdf_sha256 and previous.preview_manifest_sha256 == preview_sha256 and previous.visual_audit_sha256 == audit_sha256 and previous.style_anchor_sha256 == anchor_sha256 and (not confirm_content or "content" in previous.confirmed_scope) and (not confirm_manual_coverage or previous.manual_coverage_confirmed):
        return _review_path(workspace)
    review = PdfVisualReview(
        reviewed_at=datetime.now(UTC),
        reviewed_by=identity,
        pdf_sha256=pdf_sha256,
        preview_manifest_sha256=preview_sha256,
        visual_audit_sha256=audit_sha256,
        result="passed",
        style_anchor_sha256=anchor_sha256,
        confirmed_scope=["visual", "content"] if confirm_content else ["visual"],
        manual_coverage_confirmed=confirm_manual_coverage,
        user_response=user_response.strip() if user_response else None,
        resolved_style_items=[finding.location for finding in audit.findings
                              if finding.code == "ILLUSTRATION_STYLE_MANUAL_REQUIRED"] if audit else [],
    )
    output = _review_path(workspace)
    output.write_text(review.model_dump_json(indent=2) + "\n", encoding="utf-8")
    workspace.append_event("pdf_visual_review_recorded", {"result": review.result})
    return output


def _read_review(workspace: Workspace) -> PdfVisualReview | None:
    try:
        return PdfVisualReview.model_validate_json(_review_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def manual_visual_review_needed(workspace: Workspace) -> bool:
    if not uses_v6_profile(workspace):
        return False
    pdf = workspace.root / "delivery" / "book.pdf"
    preview = preview_manifest_path(_preview_directory(workspace))
    audit = current_visual_audit(workspace)
    review = _read_review(workspace)
    return (
        audit is not None and (
            review is None or not pdf.is_file() or not preview.is_file()
            or review.pdf_sha256 != _sha256(pdf)
            or review.preview_manifest_sha256 != _sha256(preview)
            or review.visual_audit_sha256 != _sha256(audit_path(workspace))
            or review.style_anchor_sha256 != _current_style_anchor(workspace)
        )
    )


def validate_pdf_visual_contract(workspace: Workspace) -> list[QualityFinding]:
    """Fail closed for diagram visual quality when no machine proof is sufficient."""
    pdf_path = workspace.root / "delivery" / "book.pdf"
    if not pdf_path.is_file():
        return []
    findings: list[QualityFinding] = []
    current = current_pdf_preview(pdf_path, _preview_directory(workspace))
    preview = current[1] if current is not None else None
    preview_path = preview_manifest_path(_preview_directory(workspace))
    uses_v5 = _uses_v5_profile(workspace)
    uses_v6 = uses_v6_profile(workspace)
    audit = current_visual_audit(workspace) if uses_v6 else None
    if uses_v6:
        if audit is None:
            findings.append(QualityFinding(
                code="PDF_VISUAL_AUDIT_REQUIRED", severity=Severity.BLOCKER,
                location="runs/visual-audit-report.json",
                message="当前 v6 PDF 缺少有效逐页视觉报告；请运行 kp audit-pdf-visual。",
            ))
        else:
            findings.extend(audit.findings)
            for observation in audit.observations:
                if observation.assessment == "failed":
                    findings.append(QualityFinding(
                        code="PDF_VISUAL_OBSERVATION_FAILED", severity=Severity.BLOCKER,
                        location=f"delivery/book.pdf:第 {observation.page_number} 页",
                        message=observation.note,
                    ))
    if uses_v5 and (preview is None or preview.pdf_sha256 != _sha256(pdf_path)):
        findings.append(
            QualityFinding(
                code="PDF_PREVIEW_MANIFEST_REQUIRED",
                severity=Severity.BLOCKER,
                location="runs/pdf-preview/preview-manifest.json",
                message="当前 PDF 缺少完整的页面预览清单或图片；请运行 kp repair-pdf-preview。",
            )
        )
    if uses_v5 and preview is not None and preview.renderer_available:
        for page in preview.pages:
            if page.ink_detected is False:
                findings.append(
                    QualityFinding(
                        code="PDF_PREVIEW_BLANK_PAGE",
                        severity=Severity.BLOCKER,
                        location=f"delivery/book.pdf:第 {page.page_number} 页",
                        message="PDF 预览检测到无可见内容的页面；请修订分页、图表或模板后重新渲染。",
                    )
                )
            elif page.sparse:
                findings.append(
                    QualityFinding(
                        code="PDF_PREVIEW_SPARSE_PAGE",
                        severity=Severity.WARNING,
                        location=f"delivery/book.pdf:第 {page.page_number} 页",
                        message="页面文本密度偏低，需要人工确认留白是否承担阅读或填写任务。",
                    )
                )
    try:
        extracted = "\n".join(page.extract_text() or "" for page in PdfReader(pdf_path).pages)
    except (OSError, ValueError):
        extracted = ""
    if _DUPLICATE_CAPTION.search(extracted):
        findings.append(
            QualityFinding(
                code="PDF_DIAGRAM_CAPTION_DUPLICATED",
                severity=Severity.BLOCKER,
                location="delivery/book.pdf",
                message="PDF 图注出现重复编号；请使用不含“图 N”的语义图注重新汇编和渲染。",
            )
        )
    requires_manual_review = uses_v6 or _has_diagram_assets(workspace) or (uses_v5 and preview is not None and preview.needs_manual_visual_review)
    review = _read_review(workspace)
    preview_matches = preview is not None and preview_path.is_file() and review is not None and review.preview_manifest_sha256 == _sha256(preview_path)
    if uses_v6 and audit is not None and not full_visual_observation_coverage(audit) and not (
        review is not None and review.manual_coverage_confirmed and review.user_response and review.user_response.strip()
    ):
        findings.append(QualityFinding(code="PDF_FULL_VISUAL_COVERAGE_REQUIRED", severity=Severity.BLOCKER,
            location="delivery/book.pdf", message="抽样／缺清晰页／未通过观察不能作为全书视觉验收；补齐实际全页观察或取得本人明确完整覆盖确认。"))
    if requires_manual_review and (
        review is None or review.pdf_sha256 != _sha256(pdf_path) or review.result != "passed" or not preview_matches
        or (uses_v6 and (audit is None or review.visual_audit_sha256 != _sha256(audit_path(workspace)) or review.style_anchor_sha256 != _current_style_anchor(workspace)))
    ):
        findings.append(
            QualityFinding(
                code="PDF_DIAGRAM_MANUAL_VISUAL_REVIEW_REQUIRED",
                severity=Severity.BLOCKER,
                location="delivery/book.pdf",
                message="自动量测不能代替对图中文字、裁切与排版的实际判断；请完成当前版本的人工视觉复核。",
            )
        )
    return findings
