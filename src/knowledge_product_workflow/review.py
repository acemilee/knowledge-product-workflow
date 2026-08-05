"""Version-bound chapter review contracts and deterministic validation."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.workspace import Workspace


class ReviewStatus(StrEnum):
    """Disposition issued by a reviewer."""

    PASSED = "passed"
    REVISE = "revise"


class ReviewIndependenceMode(StrEnum):
    """The review isolation a report can truthfully claim."""

    HOST_SESSION_SEPARATED = "host_session_separated"
    API_HARNESS = "api_harness"


class ReviewScore(BaseModel):
    """Five rubric dimensions scored by a reviewer."""

    factual_grounding: int = Field(ge=1, le=5)
    teaching_progression: int = Field(ge=1, le=5)
    interview_actionability: int = Field(ge=1, le=5)
    case_specificity: int = Field(ge=1, le=5)
    expression_novelty: int = Field(ge=1, le=5)

    @property
    def average(self) -> float:
        return sum(self.model_dump().values()) / 5


class UnitEvidence(BaseModel):
    """Reader-visible proof that one required teaching unit was reviewed."""

    unit: str = Field(min_length=1)
    location: str = Field(min_length=1)
    note: str = Field(min_length=1)


class ReviewIssue(BaseModel):
    """An actionable issue reported by a reviewer."""

    severity: Severity
    location: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ReviewTarget(BaseModel):
    """Exact chapter revision a reviewer is authorized to assess."""

    chapter_number: int = Field(gt=0)
    chapter_path: str = Field(pattern=r"^chapters/chapter-\d{2}\.md$")
    contract_path: str = Field(pattern=r"^chapters/chapter-\d{2}-contract\.md$")
    chapter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_version: int = Field(ge=1)
    author_identity: str = Field(min_length=1)
    author_session_id: str = Field(min_length=1)


class ChapterRevision(BaseModel):
    """Append-only author checkpoint for a reviewable chapter revision."""

    revision_schema_version: Literal[1] = 1
    checkpointed_at: datetime
    target: ReviewTarget


class ReviewWorkOrder(BaseModel):
    """Scoped handoff for a review running in a separate host session."""

    work_order_id: str = Field(pattern=r"^review-chapter-\d{2}-[0-9a-f]{12}$")
    created_at: datetime
    target: ReviewTarget
    allowed_read_paths: list[str]
    allowed_write_paths: list[str]
    output_path: str = Field(pattern=r"^chapters/chapter-\d{2}-review\.json$")
    acceptance_command: str = Field(min_length=1)


class ChapterReview(BaseModel):
    """Version-bound assessment of a single chapter draft."""

    review_schema_version: Literal[2]
    chapter_number: int = Field(gt=0)
    status: ReviewStatus
    model: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    prompt_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    reviewed_chapter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_chapter_version: int = Field(ge=1)
    author_identity: str = Field(min_length=1)
    author_session_id: str = Field(min_length=1)
    reviewer_identity: str = Field(min_length=1)
    reviewer_session_id: str = Field(min_length=1)
    independence_mode: ReviewIndependenceMode
    review_work_order_id: str = Field(pattern=r"^review-chapter-\d{2}-[0-9a-f]{12}$")
    reviewed_at: datetime
    scores: ReviewScore
    unit_evidence: list[UnitEvidence]
    issues: list[ReviewIssue] = Field(default_factory=list)
    revision_requests: list[str] = Field(default_factory=list)


class ReviewCheckpointRequiredError(ValueError):
    """Raised when the current chapter bytes have no matching author checkpoint."""


_CONTRACT_BUDGET_BLOCK = re.compile(
    r"^## 章节预算\s*\n+```yaml\s*\n(?P<yaml>.*?)\n```",
    re.MULTILINE | re.DOTALL,
)


def _relative_path(workspace: Workspace, path: Path) -> str:
    return str(path.relative_to(workspace.root)).replace("\\", "/")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _chapter_paths(workspace: Workspace, chapter_number: int) -> tuple[Path, Path]:
    chapters = workspace.root / "chapters"
    return (
        chapters / f"chapter-{chapter_number:02d}.md",
        chapters / f"chapter-{chapter_number:02d}-contract.md",
    )


def _chapter_snapshot(workspace: Workspace, chapter_number: int) -> tuple[object, str, str]:
    chapter_path, contract_path = _chapter_paths(workspace, chapter_number)
    if not chapter_path.is_file():
        raise FileNotFoundError(f"缺少章节正文：{chapter_path}")
    if not contract_path.is_file():
        raise FileNotFoundError(f"缺少章节合约：{contract_path}")
    metadata, body = parse_markdown_artifact(chapter_path.read_text(encoding="utf-8"))
    return metadata, body, contract_path.read_text(encoding="utf-8")


def _target_from_snapshot(
    workspace: Workspace,
    chapter_number: int,
    author_identity: str,
    author_session_id: str,
) -> ReviewTarget:
    metadata, body, contract = _chapter_snapshot(workspace, chapter_number)
    chapter_path, contract_path = _chapter_paths(workspace, chapter_number)
    return ReviewTarget(
        chapter_number=chapter_number,
        chapter_path=_relative_path(workspace, chapter_path),
        contract_path=_relative_path(workspace, contract_path),
        chapter_sha256=_sha256_text(body),
        contract_sha256=_sha256_text(contract),
        chapter_version=metadata.version,
        author_identity=author_identity,
        author_session_id=author_session_id,
    )


def _revision_path(workspace: Workspace, chapter_number: int) -> Path:
    return workspace.root / "runs" / "chapter-revisions" / f"chapter-{chapter_number:02d}.jsonl"


def _latest_revision(workspace: Workspace, chapter_number: int) -> ChapterRevision | None:
    path = _revision_path(workspace, chapter_number)
    if not path.is_file():
        return None
    latest: ChapterRevision | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            latest = ChapterRevision.model_validate_json(line)
    return latest


def _target_matches_current(workspace: Workspace, target: ReviewTarget) -> bool:
    try:
        current = _target_from_snapshot(
            workspace,
            target.chapter_number,
            target.author_identity,
            target.author_session_id,
        )
    except (FileNotFoundError, OSError, ValueError):
        return False
    return (
        current.chapter_sha256 == target.chapter_sha256
        and current.contract_sha256 == target.contract_sha256
        and current.chapter_version == target.chapter_version
    )


def checkpoint_chapter(
    workspace: Workspace,
    chapter_number: int,
    author_identity: str,
    author_session_id: str,
) -> ReviewTarget:
    """Checkpoint the current chapter revision before assigning a reviewer."""
    latest = _latest_revision(workspace, chapter_number)
    if latest is not None and _target_matches_current(workspace, latest.target):
        return latest.target
    target = _target_from_snapshot(workspace, chapter_number, author_identity, author_session_id)
    revision = ChapterRevision(checkpointed_at=datetime.now(UTC), target=target)
    path = _revision_path(workspace, chapter_number)
    path.parent.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(revision.model_dump_json() + "\n")
    workspace.append_event(
        "chapter_checkpointed",
        {
            "chapter_number": chapter_number,
            "chapter_sha256": target.chapter_sha256,
            "contract_sha256": target.contract_sha256,
            "chapter_version": target.chapter_version,
            "author_identity": target.author_identity,
            "author_session_id": target.author_session_id,
        },
    )
    return target


def current_review_target(workspace: Workspace, chapter_number: int) -> ReviewTarget:
    """Return the latest checkpoint only when it still matches current source bytes."""
    latest = _latest_revision(workspace, chapter_number)
    if latest is None or not _target_matches_current(workspace, latest.target):
        raise ReviewCheckpointRequiredError(
            f"第 {chapter_number} 章缺少与当前正文和合约匹配的作者 checkpoint"
        )
    return latest.target


def _work_order_path(workspace: Workspace, work_order_id: str) -> Path:
    return workspace.root / "runs" / "work-orders" / f"{work_order_id}.json"


def create_review_work_order(workspace: Workspace, chapter_number: int) -> ReviewWorkOrder:
    """Create an idempotent, hash-bound work order for one reviewer session."""
    target = current_review_target(workspace, chapter_number)
    work_order_id = f"review-chapter-{chapter_number:02d}-{target.chapter_sha256[:12]}"
    path = _work_order_path(workspace, work_order_id)
    if path.is_file():
        return ReviewWorkOrder.model_validate_json(path.read_text(encoding="utf-8"))
    work_order = ReviewWorkOrder(
        work_order_id=work_order_id,
        created_at=datetime.now(UTC),
        target=target,
        allowed_read_paths=[
            target.chapter_path,
            target.contract_path,
            "planning/product-brief.md",
            "planning/curriculum-blueprint.md",
            "research/source-ledger.jsonl",
            "research/assumption-ledger.jsonl",
        ],
        allowed_write_paths=[f"chapters/chapter-{chapter_number:02d}-review.json"],
        output_path=f"chapters/chapter-{chapter_number:02d}-review.json",
        acceptance_command=f'kp review-check --workspace "{workspace.root}"',
    )
    path.parent.mkdir(exist_ok=True)
    path.write_text(work_order.model_dump_json(indent=2), encoding="utf-8")
    workspace.append_event(
        "review_requested",
        {"chapter_number": chapter_number, "work_order_id": work_order.work_order_id},
    )
    return work_order


def review_work_order_exists(workspace: Workspace, chapter_number: int) -> bool:
    """Return whether the current checkpoint has its deterministic review work order."""
    try:
        target = current_review_target(workspace, chapter_number)
    except (ReviewCheckpointRequiredError, FileNotFoundError, OSError, ValueError):
        return False
    work_order_id = f"review-chapter-{chapter_number:02d}-{target.chapter_sha256[:12]}"
    work_order = _load_review_work_order(workspace, work_order_id)
    return work_order is not None and work_order.target == target


def _load_review_work_order(workspace: Workspace, work_order_id: str) -> ReviewWorkOrder | None:
    path = _work_order_path(workspace, work_order_id)
    if not path.is_file():
        return None
    try:
        return ReviewWorkOrder.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


def _required_units(contract_path: Path) -> list[str]:
    if not contract_path.exists():
        return []
    text = contract_path.read_text(encoding="utf-8")
    body = parse_markdown_artifact(text)[1] if text.startswith("---\n") else text
    match = _CONTRACT_BUDGET_BLOCK.search(body)
    if match is None:
        return []
    raw = yaml.safe_load(match.group("yaml"))
    units = raw.get("required_units", []) if isinstance(raw, dict) else []
    return [unit for unit in units if isinstance(unit, str)]


def _finding(code: str, location: str, message: str) -> QualityFinding:
    return QualityFinding(code=code, severity=Severity.BLOCKER, location=location, message=message)


def _review_target_matches_report(target: ReviewTarget, review: ChapterReview) -> bool:
    return (
        review.chapter_number == target.chapter_number
        and review.reviewed_chapter_sha256 == target.chapter_sha256
        and review.reviewed_contract_sha256 == target.contract_sha256
        and review.reviewed_chapter_version == target.chapter_version
        and review.author_identity == target.author_identity
        and review.author_session_id == target.author_session_id
    )


def _current_hashes_match_report(workspace: Workspace, review: ChapterReview) -> bool:
    metadata, body, contract = _chapter_snapshot(workspace, review.chapter_number)
    return (
        review.reviewed_chapter_sha256 == _sha256_text(body)
        and review.reviewed_contract_sha256 == _sha256_text(contract)
        and review.reviewed_chapter_version == metadata.version
    )


def validate_chapter_reviews(workspace: Workspace) -> list[QualityFinding]:
    """Validate review version binding, host-session separation, and rubric results."""
    findings: list[QualityFinding] = []
    chapters_dir = workspace.root / "chapters"
    for chapter_path in sorted(chapters_dir.glob("chapter-[0-9][0-9].md")):
        chapter_number = int(chapter_path.stem.split("-")[1])
        review_path = chapters_dir / f"chapter-{chapter_number:02d}-review.json"
        location = _relative_path(workspace, review_path)
        if not review_path.is_file():
            findings.append(_finding("CHAPTER_REVIEW_MISSING", location, "缺少审稿报告，不能批准本章交付。"))
            continue
        try:
            raw = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            findings.append(_finding("CHAPTER_REVIEW_INVALID", location, f"审稿报告格式无效：{error}"))
            continue
        if not isinstance(raw, dict) or raw.get("review_schema_version") != 2:
            findings.append(
                _finding(
                    "CHAPTER_REVIEW_LEGACY_UNBOUND",
                    location,
                    "审稿报告未绑定当前正文/合约版本；请在当前版本上重新创建审稿工单并审稿。",
                )
            )
            continue
        try:
            review = ChapterReview.model_validate(raw)
        except ValidationError as error:
            findings.append(_finding("CHAPTER_REVIEW_INVALID", location, f"审稿报告格式无效：{error}"))
            continue
        if not _current_hashes_match_report(workspace, review):
            findings.append(
                _finding(
                    "CHAPTER_REVIEW_STALE",
                    location,
                    "审稿报告绑定的正文、合约或章节版本已变化；请为当前版本重新审稿。",
                )
            )
            continue
        try:
            target = current_review_target(workspace, chapter_number)
        except (ReviewCheckpointRequiredError, FileNotFoundError, OSError, ValueError):
            findings.append(
                _finding(
                    "CHAPTER_REVIEW_CHECKPOINT_REQUIRED",
                    location,
                    "当前章节缺少匹配的作者 checkpoint；请先登记当前修订再创建审稿工单。",
                )
            )
            continue
        if not _review_target_matches_report(target, review):
            findings.append(
                _finding(
                    "CHAPTER_REVIEW_TARGET_MISMATCH",
                    location,
                    "审稿报告的目标哈希、版本或作者身份与当前 checkpoint 不一致。",
                )
            )
            continue
        work_order = _load_review_work_order(workspace, review.review_work_order_id)
        if work_order is None or work_order.target != target:
            findings.append(
                _finding(
                    "CHAPTER_REVIEW_WORK_ORDER_MISSING",
                    location,
                    "审稿报告没有匹配当前版本的审稿工单。",
                )
            )
            continue
        if (
            review.author_identity == review.reviewer_identity
            or review.author_session_id == review.reviewer_session_id
        ):
            findings.append(
                _finding(
                    "CHAPTER_REVIEW_NOT_INDEPENDENT",
                    location,
                    "作者与审稿者身份或会话相同；请在独立宿主会话中重新审稿。",
                )
            )
        if review.status is not ReviewStatus.PASSED:
            findings.append(
                _finding("CHAPTER_REVIEW_FAILED", location, "审稿未通过，需按修订要求重写后重新审稿。")
            )
        scores = review.scores.model_dump().values()
        if min(scores) < 3 or review.scores.average < 3.5:
            findings.append(
                _finding(
                    "CHAPTER_REVIEW_FAILED",
                    location,
                    f"审稿评分未达门槛：最低分至少为 3，平均分至少为 3.5；当前平均分为 {review.scores.average:.1f}。",
                )
            )
        if any(issue.severity is Severity.BLOCKER for issue in review.issues):
            findings.append(_finding("CHAPTER_REVIEW_FAILED", location, "审稿报告包含未解决的阻断问题。"))
        required_units = _required_units(chapters_dir / f"chapter-{chapter_number:02d}-contract.md")
        evidenced_units = {evidence.unit for evidence in review.unit_evidence}
        missing_units = sorted(set(required_units) - evidenced_units)
        if missing_units:
            findings.append(
                _finding(
                    "CHAPTER_REQUIREMENT_MISSING",
                    location,
                    f"审稿缺少必需教学单元的证据：{', '.join(missing_units)}。",
                )
            )
    return findings
