"""Version-bound quality reports and publishing eligibility."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from knowledge_product_workflow.verification_context import (
    atomic_replace as _v_atomic_replace,
)
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import glob_files as _v_glob_files
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import read_bytes as _v_read_bytes
from knowledge_product_workflow.verification_context import read_text as _v_read_text
from knowledge_product_workflow.verification_context import (
    unlink as _v_unlink,
)
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workspace import Workspace

QUALITY_REPORT_SCHEMA_VERSION = 2
QUALITY_POLICY_REVISION = "kp-quality-v141-r05-1"
_QUALITY_REPORT_STAGES = frozenset({"quality", "delivery"})


class Severity(StrEnum):
    """Severity levels emitted by deterministic and agent quality checks."""

    INFO = "info"
    WARNING = "warning"
    BLOCKER = "blocker"


class QualityFinding(BaseModel):
    """One actionable quality issue tied to a stable location."""

    code: str
    severity: Severity
    location: str
    message: str


class QualityInput(BaseModel):
    """One workspace-local input bound to a quality report revision."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class QualityReportValidity(StrEnum):
    """Whether a report can make a release decision for the current inputs."""

    CURRENT = "current"
    STALE = "stale"
    INVALID = "invalid"


class QualityReport(BaseModel):
    """Persisted result of one quality gate, bound to an exact workspace revision."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int | None = None
    report_id: str | None = None
    generated_at: datetime | None = None
    validation_stage: str | None = None
    policy_revision: str | None = None
    inputs: list[QualityInput] = Field(default_factory=list)
    findings: list[QualityFinding] = Field(default_factory=list)

    @property
    def publishable(self) -> bool:
        return evaluate_publishability(self.findings)

    @property
    def is_versioned(self) -> bool:
        """Return whether all mandatory V2 envelope fields are present and coherent."""
        return (
            self.schema_version == QUALITY_REPORT_SCHEMA_VERSION
            and bool(self.report_id)
            and self.generated_at is not None
            and self.validation_stage in _QUALITY_REPORT_STAGES
            and bool(self.inputs)
        )


def evaluate_publishability(findings: list[QualityFinding]) -> bool:
    """Allow publishing only when no blocker findings remain."""
    return not any(finding.severity is Severity.BLOCKER for finding in findings)


def quality_report_inputs(workspace: Workspace, validation_stage: str) -> list[QualityInput]:
    """Collect the exact local source graph relevant to one quality-gate stage."""
    if validation_stage not in _QUALITY_REPORT_STAGES:
        raise ValueError(f"不支持的质量校验阶段：{validation_stage}")
    root = workspace.root.resolve()
    clear_images: dict[Path, Path] = {}
    candidates = [
        root / "planning" / "curriculum-blueprint.md",
        root / "research" / "source-ledger.jsonl",
        root / "research" / "assumption-ledger.jsonl",
        root / "research" / "claim-classifications.jsonl",
        root / "chapters" / "chapter-01-sample.approved",
    ]
    chapter_paths = sorted(_v_glob_files(root / "chapters", "chapter-[0-9][0-9].md"))
    candidates.extend(chapter_paths)
    candidates.extend(sorted(_v_glob_files(root / "chapters", "chapter-[0-9][0-9]-contract.md")))
    candidates.extend(sorted(_v_glob_files(root / "runs" / "chapter-compliance", "chapter-[0-9][0-9].json")))
    candidates.extend(sorted(_v_glob_files(root / "runs" / "humanize", "chapter-[0-9][0-9].json")))
    candidates.extend(sorted(_v_glob_files(root / "runs" / "editorial", "chapter-[0-9][0-9]-developmental-report.json")))
    candidates.extend(root / "runs" / "editorial" / f"{chapter.stem}-structure-review.json"
        for chapter in chapter_paths if _v_is_file(chapter))
    candidates.extend(sorted(_v_glob_files(root / "chapters", "chapter-[0-9][0-9]-content-plan.yaml")))
    candidates.extend(sorted(_v_glob_files(root / "runs" / "illustrations" / "asset-checks", "*.json")))
    image_budget = root / "runs" / "illustrations" / "budget.json"
    if _v_is_file(image_budget) and validation_stage == "delivery":
        candidates.append(image_budget)
    candidates.extend(sorted(_v_glob_files(root / "runs" / "review-batches", "**/*.json")))
    from knowledge_product_workflow.quality_exceptions import controlled_exception_input_paths

    candidates.extend(controlled_exception_input_paths(workspace))
    if validation_stage == "delivery":
        candidates.extend(
            (
                root / "planning" / "publication-profile.yaml",
                root / "delivery" / "book.md",
                root / "delivery" / "book.qmd",
                root / "delivery" / "book.pdf",
                root / "delivery" / "book-input-manifest.json",
                root / "delivery" / "practice-resources.zip",
                root / "delivery" / "practice-resources-manifest.json",
                root / "delivery" / "publication-metadata.yaml",
                root / "delivery" / "knowledge-book-cover.tex",
                root / "delivery" / "knowledge-book-style.tex",
                root / "delivery" / "knowledge-book-toc.tex",
                root / "delivery" / "knowledge-book-components.tex",
                root / "delivery" / "knowledge-book-components.lua",
                root / "runs" / "pdf-preview" / "preview-manifest.json",
                root / "runs" / "visual-audit-report.json",
                root / "runs" / "pdf-visual-review.json",
                root / "runs" / "interaction" / "pdf.json",
            )
        )
        from knowledge_product_workflow.pdf_preview import current_pdf_preview

        preview_root = root / "runs" / "pdf-preview"
        current_preview = current_pdf_preview(root / "delivery" / "book.pdf", preview_root)
        if current_preview is not None:
            candidates.extend(
                preview_root / page.image_path
                for page in current_preview[1].pages
                if page.image_path is not None
            )
        candidates.extend(sorted(_v_glob_files(root / "assets", "**/*")))
        candidates.extend(sorted(_v_glob_files(root / "runs" / "visual-audit-pages", "*.png")))
        # Bind only the clear images declared by the current report, not every
        # historical raster directory. A PNG can change without changing mtime.
        audit = root / 'runs/visual-audit-report.json'
        try:
            pages = json.loads(_v_read_text(audit, encoding='utf8')).get('pages', [])
        except (OSError, ValueError, AttributeError):
            pages = []
        for page in pages if isinstance(pages, list) else []:
            stored = page.get('high_resolution_image') if isinstance(page, dict) else None
            if not isinstance(stored, str):
                continue
            path = Path(stored)
            if (not path.is_absolute() and '..' not in path.parts
                    and path.parts[:2] == ('runs','visual-audit-pages')
                    and (root/path).resolve().is_relative_to(root)):
                candidates.append(root/path)
                clear_images[(root/path).resolve()] = root/path
    files = sorted({path.resolve() for path in candidates if _v_is_file(path)}, key=lambda path: path.as_posix())
    inputs = [
        QualityInput(
            path=path.relative_to(root).as_posix(),
            sha256=hashlib.sha256(_v_read_bytes(clear_images[path]) if path in clear_images else _v_read_bytes(path)).hexdigest(),
        )
        for path in files
    ]
    brief_path = root / "planning" / "product-brief.md"
    if _v_is_file(brief_path):
        try:
            from knowledge_product_workflow.frontmatter import parse_markdown_artifact
            from knowledge_product_workflow.workflows.product_brief import (
                brief_contract_fingerprints,
            )

            _, brief_body = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
            fingerprints = brief_contract_fingerprints(brief_body)
        except (OSError, ValueError):
            # A malformed Brief must still invalidate prior reports by its raw bytes.
            inputs.append(
                QualityInput(
                    path="planning/product-brief.md",
                    sha256=hashlib.sha256(_v_read_bytes(brief_path)).hexdigest(),
                )
            )
        else:
            inputs.append(
                QualityInput(
                    path="planning/product-brief.md",
                    sha256=fingerprints["content_contract_sha256"],
                )
            )
            if validation_stage == "delivery":
                inputs.append(
                    QualityInput(
                        path="planning/product-brief.delivery-contract",
                        sha256=fingerprints["delivery_contract_sha256"],
                    )
                )
    return sorted(inputs, key=lambda item: item.path)


def quality_report_validity(
    workspace: Workspace, report: QualityReport, validation_stage: str
) -> QualityReportValidity:
    """Classify a report without mutating the workspace or discarding audit evidence."""
    if not report.is_versioned:
        return QualityReportValidity.INVALID
    if report.policy_revision != QUALITY_POLICY_REVISION:
        return QualityReportValidity.STALE
    if report.validation_stage != validation_stage:
        return QualityReportValidity.STALE
    if report.inputs != quality_report_inputs(workspace, validation_stage):
        return QualityReportValidity.STALE
    return QualityReportValidity.CURRENT


def quality_report_upstream_validity(workspace: Workspace, report: QualityReport) -> QualityReportValidity:
    """Check whether a quality or delivery report still binds the current upstream source graph."""
    if not report.is_versioned:
        return QualityReportValidity.INVALID
    bound_upstream = [item for item in report.inputs if _is_upstream_quality_input(item.path)]
    if bound_upstream != quality_report_inputs(workspace, "quality"):
        return QualityReportValidity.STALE
    return QualityReportValidity.CURRENT


def quality_report_archive_path(workspace: Workspace, report_id: str) -> Path:
    """Return the immutable archive path for one report ID."""
    return workspace.root / "runs" / "quality-reports" / f"{report_id}.json"


def write_quality_report(
    workspace: Workspace, validation_stage: str, findings: list[QualityFinding]
) -> tuple[Path, QualityReport]:
    """Archive a version-bound report, then atomically publish it as the current report."""
    report = QualityReport(
        schema_version=QUALITY_REPORT_SCHEMA_VERSION,
        report_id=f"quality-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:12]}",
        generated_at=datetime.now(UTC),
        validation_stage=validation_stage,
        policy_revision=QUALITY_POLICY_REVISION,
        inputs=quality_report_inputs(workspace, validation_stage),
        findings=findings,
    )
    archive = quality_report_archive_path(workspace, report.report_id or "missing-report-id")
    _v_mkdir(archive.parent,parents=True,exist_ok=True)
    payload = report.model_dump_json(indent=2) + "\n"
    _atomic_write(archive, payload)
    current = workspace.root / "delivery" / "qa-report.json"
    _atomic_write(current, payload)
    return current, report


def _atomic_write(path: Path, contents: str) -> None:
    """Replace a report only after its complete next revision is available on disk."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _v_write_text(temporary, contents, encoding="utf-8")
        _v_atomic_replace(temporary, path)
    finally:
        if _v_exists(temporary):
            _v_unlink(temporary)


def _is_upstream_quality_input(path: str) -> bool:
    """Return whether a normalized report path belongs to the full-book source gate."""
    return (
        path in {
            "planning/product-brief.md",
            "planning/curriculum-blueprint.md",
            "research/source-ledger.jsonl",
            "research/assumption-ledger.jsonl",
            "research/claim-classifications.jsonl",
            "chapters/chapter-01-sample.approved",
            "runs/token-budget.json#policy",
        }
        or path.startswith(
            (
                "chapters/chapter-",
                "runs/chapter-compliance/chapter-",
                "runs/humanize/chapter-",
                "runs/editorial/chapter-",
                "runs/review-batches/",
                "runs/illustrations/asset-checks/",
            )
        )
    )
