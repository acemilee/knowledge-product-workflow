"""Version-bound PDF diagram visual review records and deterministic fallbacks."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, Field
from pypdf import PdfReader

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
    result: str = Field(pattern=r"^passed$")


def _review_path(workspace: Workspace) -> Path:
    return workspace.root / "runs" / "pdf-visual-review.json"


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _has_diagram_assets(workspace: Workspace) -> bool:
    return any((workspace.root / "runs" / "diagrams").glob("*.json"))


def record_manual_pdf_visual_review(workspace: Workspace, reviewed_by: str) -> Path:
    """Record a human check of caption, legibility, clipping, and placement."""
    pdf_path = workspace.root / "delivery" / "book.pdf"
    identity = reviewed_by.strip()
    if not pdf_path.is_file():
        raise FileNotFoundError("人工视觉复核前必须先生成当前 PDF")
    if not identity:
        raise ValueError("人工视觉复核需要有效审核身份")
    review = PdfVisualReview(
        reviewed_at=datetime.now(UTC),
        reviewed_by=identity,
        pdf_sha256=_sha256(pdf_path),
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
    if not _has_diagram_assets(workspace):
        return []
    pdf_path = workspace.root / "delivery" / "book.pdf"
    if not pdf_path.is_file():
        return []
    findings: list[QualityFinding] = []
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
    review = _read_review(workspace)
    if review is None or review.pdf_sha256 != _sha256(pdf_path) or review.result != "passed":
        findings.append(
            QualityFinding(
                code="PDF_DIAGRAM_MANUAL_VISUAL_REVIEW_REQUIRED",
                severity=Severity.BLOCKER,
                location="delivery/book.pdf",
                message="当前 PDF 包含图表。自动检查无法可靠判断最终图中文字、裁切和版式；请完成当前版本的人工视觉复核。",
            )
        )
    return findings
