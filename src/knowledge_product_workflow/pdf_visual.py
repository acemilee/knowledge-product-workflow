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
    PdfPreviewManifest,
    preview_manifest_path,
    render_pdf_preview_set,
)
from knowledge_product_workflow.presentation import PRESENTATION_PROFILE_ID, PublicationMetadata
from knowledge_product_workflow.quality import QualityFinding, Severity
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
    result: str = Field(pattern=r"^passed$")


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
    return metadata.profile_id == PRESENTATION_PROFILE_ID


def _read_preview_manifest(workspace: Workspace) -> PdfPreviewManifest | None:
    path = preview_manifest_path(_preview_directory(workspace))
    try:
        return PdfPreviewManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def record_manual_pdf_visual_review(workspace: Workspace, reviewed_by: str) -> Path:
    """Record a human check of caption, legibility, clipping, and placement."""
    pdf_path = workspace.root / "delivery" / "book.pdf"
    identity = reviewed_by.strip()
    if not pdf_path.is_file():
        raise FileNotFoundError("人工视觉复核前必须先生成当前 PDF")
    if not identity:
        raise ValueError("人工视觉复核需要有效审核身份")
    preview_path, _preview = render_pdf_preview_set(pdf_path, _preview_directory(workspace))
    review = PdfVisualReview(
        reviewed_at=datetime.now(UTC),
        reviewed_by=identity,
        pdf_sha256=_sha256(pdf_path),
        preview_manifest_sha256=_sha256(preview_path),
        result="passed",
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


def validate_pdf_visual_contract(workspace: Workspace) -> list[QualityFinding]:
    """Fail closed for diagram visual quality when no machine proof is sufficient."""
    pdf_path = workspace.root / "delivery" / "book.pdf"
    if not pdf_path.is_file():
        return []
    findings: list[QualityFinding] = []
    preview = _read_preview_manifest(workspace)
    preview_path = preview_manifest_path(_preview_directory(workspace))
    uses_v5 = _uses_v5_profile(workspace)
    if uses_v5 and (preview is None or preview.pdf_sha256 != _sha256(pdf_path)):
        findings.append(
            QualityFinding(
                code="PDF_PREVIEW_MANIFEST_REQUIRED",
                severity=Severity.BLOCKER,
                location="runs/pdf-preview/preview-manifest.json",
                message="当前 v5 PDF 缺少绑定当前字节的页面预览清单；请重新受控渲染。",
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
    requires_manual_review = _has_diagram_assets(workspace) or (uses_v5 and preview is not None and preview.needs_manual_visual_review)
    review = _read_review(workspace)
    preview_matches = preview is not None and preview_path.is_file() and review is not None and review.preview_manifest_sha256 == _sha256(preview_path)
    if requires_manual_review and (
        review is None or review.pdf_sha256 != _sha256(pdf_path) or review.result != "passed" or not preview_matches
    ):
        findings.append(
            QualityFinding(
                code="PDF_DIAGRAM_MANUAL_VISUAL_REVIEW_REQUIRED",
                severity=Severity.BLOCKER,
                location="delivery/book.pdf",
                message="当前 PDF 包含图表。自动检查无法可靠判断最终图中文字、裁切和版式；请完成当前版本的人工视觉复核。",
            )
        )
    return findings
