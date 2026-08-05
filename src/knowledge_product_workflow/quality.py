"""Version-bound quality reports and publishing eligibility."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from knowledge_product_workflow.workspace import Workspace

QUALITY_REPORT_SCHEMA_VERSION = 2
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
    candidates = [
        root / "planning" / "curriculum-blueprint.md",
        root / "research" / "source-ledger.jsonl",
        root / "research" / "assumption-ledger.jsonl",
        root / "chapters" / "chapter-01-sample.approved",
    ]
    candidates.extend(sorted((root / "chapters").glob("chapter-[0-9][0-9].md")))
    candidates.extend(sorted((root / "chapters").glob("chapter-[0-9][0-9]-contract.md")))
    candidates.extend(sorted((root / "runs" / "chapter-compliance").glob("chapter-[0-9][0-9].json")))
    candidates.extend(sorted((root / "runs" / "humanize").glob("chapter-[0-9][0-9].json")))
    candidates.extend(sorted((root / "runs" / "review-batches").glob("**/*.json")))
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
                root / "delivery" / "publication-metadata.yaml",
                root / "delivery" / "knowledge-book-cover.tex",
                root / "delivery" / "knowledge-book-style.tex",
                root / "delivery" / "knowledge-book-toc.tex",
                root / "delivery" / "knowledge-book-components.tex",
                root / "delivery" / "knowledge-book-components.lua",
            )
        )
        candidates.extend(sorted((root / "assets").glob("**/*")))
    files = sorted({path.resolve() for path in candidates if path.is_file()}, key=lambda path: path.as_posix())
    inputs = [
        QualityInput(
            path=path.relative_to(root).as_posix(),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in files
    ]
    brief_path = root / "planning" / "product-brief.md"
    if brief_path.is_file():
        try:
            from knowledge_product_workflow.frontmatter import parse_markdown_artifact
            from knowledge_product_workflow.workflows.product_brief import (
                brief_contract_fingerprints,
            )

            _, brief_body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
            fingerprints = brief_contract_fingerprints(brief_body)
        except (OSError, ValueError):
            # A malformed Brief must still invalidate prior reports by its raw bytes.
            inputs.append(
                QualityInput(
                    path="planning/product-brief.md",
                    sha256=hashlib.sha256(brief_path.read_bytes()).hexdigest(),
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
        inputs=quality_report_inputs(workspace, validation_stage),
        findings=findings,
    )
    archive = quality_report_archive_path(workspace, report.report_id or "missing-report-id")
    archive.parent.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump_json(indent=2) + "\n"
    _atomic_write(archive, payload)
    current = workspace.root / "delivery" / "qa-report.json"
    _atomic_write(current, payload)
    return current, report


def _atomic_write(path: Path, contents: str) -> None:
    """Replace a report only after its complete next revision is available on disk."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(contents, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _is_upstream_quality_input(path: str) -> bool:
    """Return whether a normalized report path belongs to the full-book source gate."""
    return (
        path in {
            "planning/product-brief.md",
            "planning/curriculum-blueprint.md",
            "research/source-ledger.jsonl",
            "research/assumption-ledger.jsonl",
            "chapters/chapter-01-sample.approved",
        }
        or path.startswith(
            (
                "chapters/chapter-",
                "runs/chapter-compliance/chapter-",
                "runs/humanize/chapter-",
                "runs/review-batches/",
            )
        )
    )
