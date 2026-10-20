"""Atomic, snapshot-bound review batches and controlled reviewer result intake."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import yaml
from pydantic import BaseModel, Field, ValidationError

from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.review import ReviewIssue, ReviewScore, ReviewStatus, UnitEvidence
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workspace import Workspace


class ReviewBatchState(StrEnum):
    PENDING_DISPATCH = "review_batch_pending"
    DISPATCH_UNAVAILABLE = "review_dispatch_unavailable"
    DISPATCHING = "review_dispatching"
    TASKS_PENDING = "review_tasks_pending"
    TASKS_BLOCKED = "review_tasks_blocked"
    DISPATCH_FAILED = "review_dispatch_failed"
    READY = "review_ready"
    SUPERSEDED = "review_batch_superseded"


class ReviewMode(StrEnum):
    """How a frozen review task is executed and evidenced."""

    HOST_SKILL = "host_skill"
    RECEIPT_BOUND = "receipt_bound"


class ReviewTaskState(StrEnum):
    PENDING = "pending"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class BatchReviewTarget(BaseModel):
    """The exact bytes and author context frozen for one chapter review."""

    chapter_number: int = Field(gt=0)
    chapter_path: str = Field(pattern=r"^chapters/chapter-\d{2}\.md$")
    contract_path: str = Field(pattern=r"^chapters/chapter-\d{2}-contract\.md$")
    chapter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_version: int = Field(ge=1)
    author_identity: str = Field(min_length=1)
    author_session_id: str = Field(min_length=1)


class CandidateReviewTarget(BatchReviewTarget):
    """Private candidate target; never silently reinterpret it as a formal chapter."""
    candidate_task_id: str
    candidate_version: int = Field(ge=1)
    candidate_input_manifest_sha256: str
    candidate_context_path: str


class HostCapabilities(BaseModel):
    """Verified host-side task creation capability, not a model self-declaration."""

    platform: str = Field(min_length=1)
    version: str | None = None
    supports_real_subtasks: bool = False
    production_verified: bool = False
    max_parallelism: int = Field(default=0, ge=0)
    reason: str = Field(min_length=1)


class TaskReceipt(BaseModel):
    """Host-issued proof that a single reviewer task was actually created."""

    task_id: str = Field(min_length=1)
    parent_task_id: str = Field(min_length=1)
    dispatched_at: datetime
    task_input_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewTask(BaseModel):
    """One chapter's immutable input and controlled output authorization."""

    chapter_number: int = Field(gt=0)
    task_token: str = Field(min_length=32)
    target: BatchReviewTarget
    snapshot_directory: str = Field(min_length=1)
    work_order_path: str = Field(min_length=1)
    task_input_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: ReviewTaskState = ReviewTaskState.PENDING
    receipt: TaskReceipt | None = None
    result_path: str | None = None


class ReviewBatch(BaseModel):
    """The append-only control record for an atomic set of chapter reviews."""

    review_batch_schema_version: int = 1
    batch_id: str = Field(pattern=r"^review-batch-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
    created_at: datetime
    state: ReviewBatchState
    review_mode: ReviewMode = ReviewMode.RECEIPT_BOUND
    dispatcher_kind: str = Field(min_length=1)
    capabilities: HostCapabilities
    input_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tasks: list[ReviewTask] = Field(min_length=1)
    failure_reason: str | None = None


class ReviewResultInput(BaseModel):
    """Reviewer-provided content only; controller-owned binding fields are absent."""

    status: ReviewStatus
    model: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    prompt_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    reviewer_identity: str = Field(min_length=1)
    reviewer_session_id: str = Field(min_length=1)
    scores: ReviewScore
    unit_evidence: list[UnitEvidence]
    issues: list[ReviewIssue] = Field(default_factory=list)
    revision_requests: list[str] = Field(default_factory=list)


class BatchChapterReview(BaseModel):
    """Controller-issued v3 review record, bound to an immutable batch task."""

    review_schema_version: int = 3
    batch_id: str
    chapter_number: int = Field(gt=0)
    review_mode: ReviewMode = ReviewMode.RECEIPT_BOUND
    task_id: str | None = None
    parent_task_id: str | None = None
    task_input_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target: BatchReviewTarget
    status: ReviewStatus
    model: str
    model_version: str
    prompt_hash: str
    reviewer_identity: str
    reviewer_session_id: str
    reviewed_at: datetime
    scores: ReviewScore
    unit_evidence: list[UnitEvidence]
    issues: list[ReviewIssue] = Field(default_factory=list)
    revision_requests: list[str] = Field(default_factory=list)


class ReviewDispatchUnavailableError(RuntimeError):
    """Raised when the active host cannot create attributable child reviewer tasks."""


class HostTaskDispatcher(Protocol):
    """Host adapter boundary. Production adapters must provide real receipts."""

    kind: str

    def probe(self) -> HostCapabilities: ...

    def dispatch(self, batch: ReviewBatch, task: ReviewTask) -> TaskReceipt: ...

    def cancel(self, task_id: str) -> None: ...


class UnavailableHostTaskDispatcher:
    """Default adapter used until a host exposes a supported parent-child task API."""

    kind = "unavailable"

    def __init__(self, platform: str = "当前宿主") -> None:
        self.platform = platform

    def probe(self) -> HostCapabilities:
        return HostCapabilities(
            platform=self.platform,
            supports_real_subtasks=False,
            reason="当前宿主未提供可由 KP 调用、可绑定父任务并返回回执的子任务接口。",
        )

    def dispatch(self, batch: ReviewBatch, task: ReviewTask) -> TaskReceipt:
        raise ReviewDispatchUnavailableError(self.probe().reason)

    def cancel(self, task_id: str) -> None:
        return None


class FakeHostTaskDispatcher:
    """Deterministic test double. Production validation always rejects its receipts."""

    kind = "fake"

    def __init__(self, parent_task_id: str = "fake-parent") -> None:
        self.parent_task_id = parent_task_id

    def probe(self) -> HostCapabilities:
        return HostCapabilities(
            platform="fake-test-host",
            version="test-only",
            supports_real_subtasks=True,
            max_parallelism=64,
            reason="仅用于自动化测试，不可用于生产发布。",
        )

    def dispatch(self, batch: ReviewBatch, task: ReviewTask) -> TaskReceipt:
        return TaskReceipt(
            task_id=f"fake-task-{batch.batch_id[-8:]}-{task.chapter_number:02d}",
            parent_task_id=self.parent_task_id,
            dispatched_at=datetime.now(UTC),
            task_input_manifest_sha256=task.task_input_manifest_sha256,
        )

    def cancel(self, task_id: str) -> None:
        return None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _relative(workspace: Workspace, path: Path) -> str:
    return str(path.relative_to(workspace.root)).replace("\\", "/")


def _batch_root(workspace: Workspace) -> Path:
    return workspace.root / "runs" / "review-batches"


def _batch_path(workspace: Workspace, batch_id: str) -> Path:
    return _batch_root(workspace) / batch_id


def _batch_json_path(workspace: Workspace, batch_id: str) -> Path:
    return _batch_path(workspace, batch_id) / "batch.json"


def _write_batch(workspace: Workspace, batch: ReviewBatch) -> None:
    _v_write_text(_batch_json_path(workspace, batch.batch_id),
        batch.model_dump_json(indent=2), encoding="utf-8"
    )


def _read_batch(path: Path) -> ReviewBatch | None:
    try:
        return ReviewBatch.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


def _all_batches(workspace: Workspace) -> list[ReviewBatch]:
    root = _batch_root(workspace)
    if not root.is_dir():
        return []
    batches = [batch for path in root.glob("review-batch-*/batch.json") if (batch := _read_batch(path))]
    return sorted(batches, key=lambda item: item.created_at)


def _chapter_paths(workspace: Workspace, chapter_number: int) -> tuple[Path, Path]:
    return (
        workspace.root / "chapters" / f"chapter-{chapter_number:02d}.md",
        workspace.root / "chapters" / f"chapter-{chapter_number:02d}-contract.md",
    )


def _current_target(
    workspace: Workspace, chapter_number: int, author_identity: str, author_session_id: str
) -> BatchReviewTarget:
    chapter_path, contract_path = _chapter_paths(workspace, chapter_number)
    if not _v_is_file(chapter_path):
        raise FileNotFoundError(f"缺少章节正文：{chapter_path}")
    if not _v_is_file(contract_path):
        raise FileNotFoundError(f"缺少章节合约：{contract_path}")
    metadata, body = parse_markdown_artifact(chapter_path.read_text(encoding="utf-8"))
    contract = contract_path.read_text(encoding="utf-8")
    return BatchReviewTarget(
        chapter_number=chapter_number,
        chapter_path=_relative(workspace, chapter_path),
        contract_path=_relative(workspace, contract_path),
        chapter_sha256=_sha256_text(body),
        contract_sha256=_sha256_text(contract),
        chapter_version=metadata.version,
        author_identity=author_identity,
        author_session_id=author_session_id,
    )


def _same_revision(left: BatchReviewTarget, right: BatchReviewTarget) -> bool:
    return (
        left.chapter_sha256 == right.chapter_sha256
        and left.contract_sha256 == right.contract_sha256
        and left.chapter_version == right.chapter_version
    )


def _required_units(contract_path: Path) -> list[str]:
    if not _v_is_file(contract_path):
        return []
    text = contract_path.read_text(encoding="utf-8")
    markers = ("## 章节预算", "## Detail budgets")
    start = next((text.find(marker) for marker in markers if text.find(marker) >= 0), -1)
    if start < 0:
        return []
    fenced = text.find("```yaml", start)
    end = text.find("```", fenced + 7)
    if fenced < 0 or end < 0:
        return []
    raw = yaml.safe_load(text[fenced + 7 : end])
    values = raw.get("required_units", []) if isinstance(raw, dict) else []
    return [value for value in values if isinstance(value, str)]


def _review_output_path(workspace: Workspace, batch_id: str, chapter_number: int) -> Path:
    return _batch_path(workspace, batch_id) / "reviews" / f"chapter-{chapter_number:02d}-review.json"


def _load_result(workspace: Workspace, batch: ReviewBatch, task: ReviewTask) -> BatchChapterReview | None:
    path = _review_output_path(workspace, batch.batch_id, task.chapter_number)
    try:
        return BatchChapterReview.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


def _has_current_passing_review(workspace: Workspace, chapter_number: int) -> bool:
    for batch in reversed(_all_batches(workspace)):
        for task in batch.tasks:
            if task.chapter_number != chapter_number:
                continue
            try:
                current = _current_target(
                    workspace, chapter_number, task.target.author_identity, task.target.author_session_id
                )
            except (FileNotFoundError, OSError, ValueError):
                return False
            review = _load_result(workspace, batch, task)
            if (
                review is not None
                and review.status is ReviewStatus.PASSED
                and task.state is ReviewTaskState.COMPLETED
                and _same_revision(task.target, current)
                and _converted_candidate_is_current(workspace,batch,task)
            ):
                return True
    return False


def _reusable_active_batch(workspace: Workspace, chapter_numbers: list[int]) -> ReviewBatch | None:
    """Return an exact-current in-flight batch so repeated submit calls are idempotent."""
    wanted = set(chapter_numbers)
    for batch in reversed(_all_batches(workspace)):
        if batch.state not in {
            ReviewBatchState.PENDING_DISPATCH,
            ReviewBatchState.DISPATCH_UNAVAILABLE,
            ReviewBatchState.DISPATCHING,
            ReviewBatchState.TASKS_PENDING,
        }:
            continue
        if {task.chapter_number for task in batch.tasks} != wanted:
            continue
        matches = True
        for task in batch.tasks:
            try:
                current = _current_target(
                    workspace,
                    task.chapter_number,
                    task.target.author_identity,
                    task.target.author_session_id,
                )
            except (FileNotFoundError, OSError, ValueError):
                matches = False
                break
            if not _same_revision(task.target, current):
                matches = False
                break
        if matches:
            return batch
    return None


def _batch_id() -> str:
    return f"review-batch-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def _snapshot_batch(
    workspace: Workspace,
    chapter_numbers: list[int],
    author_identity: str,
    author_session_id: str,
    dispatcher: HostTaskDispatcher,
) -> ReviewBatch:
    batch_id = _batch_id()
    root = _batch_root(workspace)
    _v_mkdir(root,parents=True,exist_ok=True)
    temporary = root / f".{batch_id}.tmp-{secrets.token_hex(4)}"
    final = root / batch_id
    _v_mkdir(temporary,parents=False)
    try:
        targets = [
            _current_target(workspace, number, author_identity, author_session_id)
            for number in chapter_numbers
        ]
        manifest_payload = {
            "batch_id": batch_id,
            "targets": [target.model_dump(mode="json") for target in targets],
            "evidence_files": [
                name
                for name in ("source-ledger.jsonl", "assumption-ledger.jsonl")
                if _v_is_file(workspace.root / "research" / name)
            ],
        }
        manifest_hash = _sha256_json(manifest_payload)
        tasks: list[ReviewTask] = []
        for target in targets:
            chapter_dir = temporary / "chapters" / f"chapter-{target.chapter_number:02d}"
            _v_mkdir(chapter_dir,parents=True)
            chapter_path, contract_path = _chapter_paths(workspace, target.chapter_number)
            shutil.copyfile(chapter_path, chapter_dir / "chapter.md")
            shutil.copyfile(contract_path, chapter_dir / "contract.md")
            task_manifest_hash = _sha256_json(
                {"batch_manifest_sha256": manifest_hash, "target": target.model_dump(mode="json")}
            )
            work_order_path = temporary / "work-orders" / f"chapter-{target.chapter_number:02d}.json"
            _v_mkdir(work_order_path.parent,exist_ok=True)
            task = ReviewTask(
                chapter_number=target.chapter_number,
                task_token=secrets.token_urlsafe(32),
                target=target,
                snapshot_directory=f"runs/review-batches/{batch_id}/chapters/chapter-{target.chapter_number:02d}",
                work_order_path=f"runs/review-batches/{batch_id}/work-orders/chapter-{target.chapter_number:02d}.json",
                task_input_manifest_sha256=task_manifest_hash,
            )
            tasks.append(task)
            _v_write_text(work_order_path,
                json.dumps(
                    {
                        "batch_id": batch_id,
                        "chapter_number": target.chapter_number,
                        "snapshot_directory": task.snapshot_directory,
                        "task_token": task.task_token,
                        "task_input_manifest_sha256": task.task_input_manifest_sha256,
                        "submission_command": (
                            f'kp submit-review-result --workspace "{workspace.root}" '
                            f"--batch {batch_id} --task-token <已提供的任务令牌> --input review-result.json"
                        ),
                    },
                    ensure_ascii=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        evidence = temporary / "evidence"
        _v_mkdir(evidence)
        for filename in manifest_payload["evidence_files"]:
            shutil.copyfile(workspace.root / "research" / filename, evidence / filename)
        capabilities = dispatcher.probe()
        batch = ReviewBatch(
            batch_id=batch_id,
            created_at=datetime.now(UTC),
            state=ReviewBatchState.PENDING_DISPATCH,
            review_mode=(
                ReviewMode.RECEIPT_BOUND
                if capabilities.supports_real_subtasks
                else ReviewMode.HOST_SKILL
            ),
            dispatcher_kind=dispatcher.kind,
            capabilities=capabilities,
            input_manifest_sha256=manifest_hash,
            tasks=tasks,
        )
        _v_write_text(temporary / "manifest.json",
            json.dumps(manifest_payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
        )
        _v_write_text(temporary / "batch.json", batch.model_dump_json(indent=2), encoding="utf-8")
        _v_mkdir(temporary / "reviews")
        for attempt in range(3):
            try:
                temporary.rename(final)
                break
            except PermissionError as error:
                if attempt == 2:
                    raise PermissionError(f"审稿批次暂存目录提交在三次短重试后仍被拒绝：{temporary} -> {final}") from error
                workspace.append_event("review_batch_commit_retry", {"attempt": attempt + 1, "reason": str(error)})
                time.sleep(0.1 * (attempt + 1))
        return batch
    except Exception:
        if _v_exists(temporary):
            shutil.rmtree(temporary)
        raise


def _activate_host_skill_batch(workspace: Workspace, batch: ReviewBatch) -> ReviewBatch:
    """Adopt a frozen batch for the Skill-only reviewer protocol."""
    batch.review_mode = ReviewMode.HOST_SKILL
    batch.dispatcher_kind = "host-skill"
    batch.state = ReviewBatchState.TASKS_PENDING
    batch.failure_reason = None
    for task in batch.tasks:
        if task.state is ReviewTaskState.BLOCKED:
            task.state = ReviewTaskState.PENDING
    _write_batch(workspace, batch)
    workspace.append_event(
        "host_skill_review_tasks_ready",
        {"batch_id": batch.batch_id, "chapter_count": len(batch.tasks)},
    )
    return batch


def submit_review_batch(
    workspace: Workspace,
    *,
    dispatcher: HostTaskDispatcher | None = None,
    author_identity: str = "当前作者",
    author_session_id: str = "当前宿主会话",
) -> ReviewBatch | None:
    """Freeze every currently unreviewed chapter, then dispatch all reviewer tasks once."""
    from knowledge_product_workflow.runtime import authorize_workflow_transition

    authorize_workflow_transition(workspace, "submit-review-batch")
    dispatcher = dispatcher or UnavailableHostTaskDispatcher()
    with workspace.review_batch_lock():
        chapters = [
            int(path.stem.split("-")[1])
            for path in sorted((workspace.root / "chapters").glob("chapter-[0-9][0-9].md"))
        ]
        if not chapters:
            raise FileNotFoundError("没有可送审的章节正文")
        needed = [number for number in chapters if not _has_current_passing_review(workspace, number)]
        if not needed:
            return None
        existing = _reusable_active_batch(workspace, needed)
        if existing is not None:
            if existing.state is ReviewBatchState.DISPATCH_UNAVAILABLE:
                return _activate_host_skill_batch(workspace, existing)
            return existing
        batch = _snapshot_batch(
            workspace, needed, author_identity, author_session_id, dispatcher
        )
        if batch.review_mode is ReviewMode.HOST_SKILL:
            return _activate_host_skill_batch(workspace, batch)
        batch.state = ReviewBatchState.DISPATCHING
        _write_batch(workspace, batch)
        receipts: list[str] = []
        try:
            for task in batch.tasks:
                receipt = dispatcher.dispatch(batch, task)
                if receipt.task_input_manifest_sha256 != task.task_input_manifest_sha256:
                    raise RuntimeError("宿主任务回执的输入清单哈希不匹配")
                task.receipt = receipt
                receipts.append(receipt.task_id)
            batch.state = ReviewBatchState.TASKS_PENDING
        except (OSError, RuntimeError, ValueError) as error:
            cleanup_failures: list[str] = []
            for task_id in receipts:
                try:
                    dispatcher.cancel(task_id)
                except (OSError, RuntimeError, ValueError) as cleanup_error:
                    cleanup_failures.append(f"{task_id}: {cleanup_error}")
            batch.state = ReviewBatchState.DISPATCH_FAILED
            batch.failure_reason = str(error)
            for task in batch.tasks:
                if task.receipt is None:
                    task.state = ReviewTaskState.FAILED
        _write_batch(workspace, batch)
        workspace.append_event(
            "review_batch_submitted",
            {
                "batch_id": batch.batch_id,
                "chapter_count": len(batch.tasks),
                "state": batch.state.value,
                "dispatcher": batch.dispatcher_kind,
                "cleanup_failures": cleanup_failures if batch.state is ReviewBatchState.DISPATCH_FAILED else [],
            },
        )
        return batch


def _find_task(batch: ReviewBatch, task_token: str) -> ReviewTask:
    for task in batch.tasks:
        if hmac.compare_digest(task.task_token, task_token):
            return task
    raise ValueError("审稿任务令牌无效或不属于该批次")


def submit_review_result(
    workspace: Workspace, batch_id: str, task_token: str, result: ReviewResultInput
) -> Path:
    """Accept only task-scoped reviewer content and fill all binding fields internally."""
    batch = _read_batch(_batch_json_path(workspace, batch_id))
    if batch is None:
        raise FileNotFoundError("找不到审稿批次")
    task = _find_task(batch, task_token)
    if task.state is not ReviewTaskState.PENDING:
        raise ValueError("该审稿任务已完成、被阻断或已失效")
    if batch.review_mode is ReviewMode.RECEIPT_BOUND and task.receipt is None:
        raise ValueError("该审稿任务未被宿主成功创建或缺少任务回执")
    if (
        result.reviewer_identity == task.target.author_identity
        or result.reviewer_session_id == task.target.author_session_id
    ):
        raise ValueError("作者身份或作者会话不能提交本章独立审稿结果")
    receipt = task.receipt
    review = BatchChapterReview(
        batch_id=batch.batch_id,
        chapter_number=task.chapter_number,
        review_mode=batch.review_mode,
        task_id=receipt.task_id if receipt is not None else None,
        parent_task_id=receipt.parent_task_id if receipt is not None else None,
        task_input_manifest_sha256=(
            receipt.task_input_manifest_sha256 if receipt is not None else task.task_input_manifest_sha256
        ),
        target=task.target,
        status=result.status,
        model=result.model,
        model_version=result.model_version,
        prompt_hash=result.prompt_hash.lower(),
        reviewer_identity=result.reviewer_identity,
        reviewer_session_id=result.reviewer_session_id,
        reviewed_at=datetime.now(UTC),
        scores=result.scores,
        unit_evidence=result.unit_evidence,
        issues=result.issues,
        revision_requests=result.revision_requests,
    )
    output = _review_output_path(workspace, batch_id, task.chapter_number)
    if _v_exists(output):
        raise ValueError("该审稿任务已提交结果，不能覆盖")
    _v_write_text(output, review.model_dump_json(indent=2), encoding="utf-8")
    task.state = ReviewTaskState.COMPLETED
    task.result_path = _relative(workspace, output)
    if all(item.state is ReviewTaskState.COMPLETED for item in batch.tasks):
        batch.state = ReviewBatchState.READY
    _write_batch(workspace, batch)
    workspace.append_event(
        "review_result_submitted",
        {
            "batch_id": batch_id,
            "chapter_number": task.chapter_number,
            "review_mode": batch.review_mode.value,
            "task_id": receipt.task_id if receipt is not None else None,
        },
    )
    from knowledge_product_workflow.production_dependencies import finalize_serial_exports
    finalize_serial_exports(workspace, chapter_number=task.chapter_number)
    return output


def _finding(code: str, location: str, message: str) -> QualityFinding:
    return QualityFinding(code=code, severity=Severity.BLOCKER, location=location, message=message)


def _current_batch_for_chapter(workspace: Workspace, chapter_number: int) -> tuple[ReviewBatch, ReviewTask] | None:
    candidates: list[tuple[ReviewBatch, ReviewTask]] = []
    for batch in _all_batches(workspace):
        for task in batch.tasks:
            if task.chapter_number == chapter_number:
                candidates.append((batch, task))
    return candidates[-1] if candidates else None


def validate_review_batches(workspace: Workspace, *, chapter_numbers: set[int] | None = None) -> list[QualityFinding]:
    """Validate batch/task receipts, immutable revision binding and rubric gates."""
    findings: list[QualityFinding] = []
    for chapter_path in sorted((workspace.root / "chapters").glob("chapter-[0-9][0-9].md")):
        chapter_number = int(chapter_path.stem.split("-")[1])
        if chapter_numbers is not None and chapter_number not in chapter_numbers:
            continue
        location = f"chapters/chapter-{chapter_number:02d}.md"
        current_pair = _current_batch_for_chapter(workspace, chapter_number)
        if current_pair is None:
            findings.append(_finding("REVIEW_BATCH_MISSING", location, "本章尚未进入原子化独立审稿批次。"))
            continue
        batch, task = current_pair
        if not _converted_candidate_is_current(workspace,batch,task):
            findings.append(_finding("REVIEW_CONVERSION_STALE",location,"候选转换／提交或原始证明不完整、已过期。"))
            continue
        try:
            current = _current_target(
                workspace, chapter_number, task.target.author_identity, task.target.author_session_id
            )
        except (FileNotFoundError, OSError, ValueError):
            findings.append(_finding("REVIEW_SNAPSHOT_INVALID", location, "无法读取当前正文或合约。"))
            continue
        if not _same_revision(task.target, current):
            findings.append(
                _finding("REVIEW_BATCH_SUPERSEDED", location, "正文或合约已变化；仅本章需要重新提交送审。")
            )
            continue
        if batch.state is ReviewBatchState.DISPATCH_UNAVAILABLE:
            findings.append(
                _finding("REVIEW_DISPATCH_UNAVAILABLE", location, batch.failure_reason or "旧审稿批次尚未迁移到宿主 Skill 审稿模式。")
            )
            continue
        if batch.state is ReviewBatchState.DISPATCH_FAILED:
            findings.append(_finding("REVIEW_DISPATCH_FAILED", location, batch.failure_reason or "审稿任务派发失败。"))
            continue
        if task.state is ReviewTaskState.BLOCKED:
            findings.append(_finding("REVIEW_TASK_RECEIPT_MISSING", location, "缺少宿主签发的审稿任务回执。"))
            continue
        if batch.review_mode is ReviewMode.RECEIPT_BOUND and task.receipt is None:
            findings.append(_finding("REVIEW_TASK_RECEIPT_MISSING", location, "缺少宿主签发的审稿任务回执。"))
            continue
        if batch.review_mode is ReviewMode.HOST_SKILL:
            findings.append(
                _finding(
                    "REVIEW_INDEPENDENCE_UNVERIFIED",
                    location,
                    "宿主审稿诊断没有真实子任务回执，不能作为独立审稿证明。",
                )
            )
        if task.state is ReviewTaskState.FAILED:
            findings.append(_finding("REVIEW_TASKS_BLOCKED", location, "审稿任务失败，需要重新派发。"))
            continue
        review = _load_result(workspace, batch, task)
        if review is None:
            findings.append(_finding("REVIEW_TASK_PENDING", location, "等待独立审稿任务提交受控结果。"))
            continue
        receipt = task.receipt
        binding_matches = (
            review.batch_id == batch.batch_id
            and review.chapter_number == task.chapter_number
            and review.review_mode is batch.review_mode
            and review.task_input_manifest_sha256 == task.task_input_manifest_sha256
            and review.target == task.target
        )
        if batch.review_mode is ReviewMode.RECEIPT_BOUND:
            binding_matches = binding_matches and receipt is not None and (
                review.task_id == receipt.task_id
                and review.parent_task_id == receipt.parent_task_id
            )
        else:
            binding_matches = binding_matches and review.task_id is None and review.parent_task_id is None
        if not binding_matches:
            findings.append(_finding("REVIEW_RESULT_BINDING_MISMATCH", location, "审稿结果与冻结批次任务不匹配。"))
            continue
        if batch.review_mode is ReviewMode.RECEIPT_BOUND and (
            batch.dispatcher_kind in {"fake", "unavailable"}
            or not batch.capabilities.supports_real_subtasks
            or not batch.capabilities.production_verified
        ):
            findings.append(
                _finding("REVIEW_DISPATCH_NOT_PRODUCTION", location, "测试或未知调度器不能作为生产独立审稿证明。")
            )
        if review.status is not ReviewStatus.PASSED:
            findings.append(_finding("REVIEW_RESULT_FAILED", location, "审稿未通过，需完成修订后重新送审。"))
        if min(review.scores.model_dump().values()) < 3 or review.scores.average < 3.5:
            findings.append(
                _finding("REVIEW_RESULT_FAILED", location, "审稿评分未达标：最低分至少 3，平均分至少 3.5。")
            )
        if any(issue.severity is Severity.BLOCKER for issue in review.issues):
            findings.append(_finding("REVIEW_RESULT_FAILED", location, "审稿结果包含未解决的阻断问题。"))
        contract_path = workspace.root / task.target.contract_path
        missing_units = sorted(
            set(_required_units(contract_path)) - {evidence.unit for evidence in review.unit_evidence}
        )
        if missing_units:
            findings.append(
                _finding("REVIEW_REQUIREMENT_MISSING", location, f"审稿缺少必需教学单元证据：{', '.join(missing_units)}。")
            )
    return findings


def review_batch_proofs(workspace: Workspace) -> list[dict[str, object]]:
    """Return internal-only proof records for delivery manifests."""
    proofs: list[dict[str, object]] = []
    for batch in _all_batches(workspace):
        for task in batch.tasks:
            review = _load_result(workspace, batch, task)
            if review is None:
                continue
            path = _review_output_path(workspace, batch.batch_id, task.chapter_number)
            proofs.append(
                {
                    "path": _relative(workspace, path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "batch_id": batch.batch_id,
                    "chapter_number": task.chapter_number,
                    "review_mode": batch.review_mode.value,
                    "task_id": task.receipt.task_id if task.receipt is not None else None,
                    "parent_task_id": task.receipt.parent_task_id if task.receipt is not None else None,
                    "reviewed_chapter_sha256": review.target.chapter_sha256,
                    "reviewed_contract_sha256": review.target.contract_sha256,
                }
            )
    return proofs


def _converted_candidate_is_current(workspace, batch, task) -> bool:
    if batch.dispatcher_kind!="codex-native-candidate":
        return True
    from knowledge_product_workflow.production_context import (
        CandidateEvaluationContext,
        CandidateProofSet,
        verify_candidate_proofs,
    )
    from knowledge_product_workflow.production_dependencies import (
        binding_digest,
        file_digest,
        workspace_file,
    )
    try:
        path=_batch_path(workspace,batch.batch_id)/"conversion.json"
        conversion=json.loads(path.read_text(encoding="utf-8"))
        commit=json.loads(workspace_file(workspace,"runs/production/commits/"+conversion["commit_id"]+".json").read_text(encoding="utf-8"))
        if commit["state"]!="completed" or commit.get("diagnostic_only") or conversion["diagnostic_only"]:
            return False
        for relative,item in commit["outputs"].items():
            if file_digest(workspace_file(workspace,relative))!=item["sha256"]:
                return False
        if commit["outputs"][_relative(workspace,path)]["sha256"]!=file_digest(path):
            return False
        context_path=workspace_file(workspace,conversion["context_path"])
        if file_digest(context_path)!=conversion["context_sha256"]:
            return False
        context=CandidateEvaluationContext.model_validate_json(context_path.read_text(encoding="utf-8"))
        if Path(context.canonical_root).resolve()!=workspace.root.resolve():
            return False
        context.verify()
        proof_path=Path(context.private_result_root)/"proofs.json"
        if file_digest(proof_path)!=conversion["proof_sha256"]:
            return False
        proof=CandidateProofSet.model_validate_json(proof_path.read_text(encoding="utf-8"))
        verify_candidate_proofs(context,proof)
        if proof.diagnostic_only or proof.required_missing:
            return False
        original=json.loads(Path(proof.reports["candidate_review"]).read_text(encoding="utf-8"))
        if original["diagnostic_only"] or not original["acceptance_ready"] or original["result"]!=conversion["original_result"]:
            return False
        for key,expected in context.live_bindings.items():
            if key in commit["outputs"]:
                continue  # Exact committed output was checked above, rather than its superseded input.
            if binding_digest(workspace,key)!=expected:
                return False
        return bool(conversion["formal_target"]==task.target.model_dump(mode="json")
            and conversion["native_task_id"]==task.receipt.task_id
            and conversion["native_parent_id"]==task.receipt.parent_task_id
            and conversion["native_manifest"]==task.task_input_manifest_sha256)
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return False


def materialize_candidate_review_batch(private, context, writer, reviewer, *, commit_id: str,
                                       capabilities, diagnostic_only: bool = False) -> list[str]:
    """Map an equal reader body into the original batch schema, retaining all original evidence."""
    from knowledge_product_workflow.chapter_compliance import _reader_body_from_source
    from knowledge_product_workflow.production_context import (
        CandidateProofSet,
        verify_candidate_proofs,
    )
    from knowledge_product_workflow.production_dependencies import file_digest
    context.verify()
    proof_path=Path(context.private_result_root)/"proofs.json"
    proof=CandidateProofSet.model_validate_json(proof_path.read_text(encoding="utf-8"))
    verify_candidate_proofs(context,proof)
    if not private.root.resolve().is_relative_to(Path(context.private_result_root).resolve()):
        raise ValueError("物化输入不属于当前候选私有目录")
    mutable={f"chapters/chapter-{context.chapter_number:02d}.md","runs/events.jsonl"}
    for relative,expected in proof.private_input_bindings.items():
        if relative in mutable:
            continue
        path=(private.root/relative).resolve()
        if (not path.is_relative_to(private.root.resolve()) or not _v_is_file(path)
                or file_digest(path)!=expected):
            raise ValueError("物化输入与已审候选不相等："+relative)
    raw=json.loads(Path(proof.reports["candidate_review"]).read_text(encoding="utf-8"))
    if not diagnostic_only and (not capabilities.admits_live_parallelism() or proof.diagnostic_only
            or raw["diagnostic_only"] or not raw["acceptance_ready"] or proof.required_missing):
        raise ValueError("候选缺真实资格或完整审稿证明，不能转换正式批次")
    if not reviewer.host_task_id or not reviewer.host_parent_id or not reviewer.host_dispatched_at:
        raise ValueError("候选转换缺原生回执身份／启动观察时间")
    result=ReviewResultInput.model_validate(raw["result"])
    if (writer.role!="writer" or reviewer.role!="reviewer" or raw["reviewer_task_id"]!=reviewer.task_id
            or writer.task_id!=context.task_id or reviewer.candidate_version!=context.candidate_version
            or reviewer.chapter_number!=context.chapter_number
            or result.reviewer_session_id!=reviewer.host_task_id
            or writer.host_task_id==reviewer.host_task_id):
        raise ValueError("转换目标／原生作者审稿身份不一致")
    target=_current_target(private,context.chapter_number,writer.task_id,writer.host_task_id or writer.task_id)
    normalized=_reader_body_from_source(Path(context.snapshot_root)/context.source_relative).strip()+"\n"
    if _sha256_text(normalized)!=target.chapter_sha256:
        raise ValueError("正式读者正文与已审核候选不相等，不能改签旧审稿")
    now=datetime.now(UTC)
    batch_id="review-batch-"+now.strftime("%Y%m%dT%H%M%SZ")+"-"+hashlib.sha256(commit_id.encode()).hexdigest()[:8]
    directory=_batch_path(private,batch_id)
    _v_mkdir(directory/"reviews",parents=True,exist_ok=False)
    receipt=TaskReceipt(task_id=reviewer.host_task_id,parent_task_id=reviewer.host_parent_id,
        dispatched_at=reviewer.host_dispatched_at,task_input_manifest_sha256=reviewer.input_manifest_sha256)
    task=ReviewTask(chapter_number=context.chapter_number,task_token=secrets.token_hex(24),target=target,
        snapshot_directory=str(Path(context.snapshot_root).relative_to(Path(context.canonical_root)).as_posix()),
        work_order_path=str((Path(context.snapshot_root).parent/"context.json").relative_to(Path(context.canonical_root)).as_posix()),
        task_input_manifest_sha256=reviewer.input_manifest_sha256,state=ReviewTaskState.COMPLETED,receipt=receipt,
        result_path=_relative(private,_review_output_path(private,batch_id,context.chapter_number)))
    batch=ReviewBatch(batch_id=batch_id,created_at=now,state=ReviewBatchState.READY,
        review_mode=ReviewMode.HOST_SKILL if diagnostic_only else ReviewMode.RECEIPT_BOUND,
        dispatcher_kind="diagnostic-candidate" if diagnostic_only else "codex-native-candidate",
        capabilities=HostCapabilities(platform="codex-app-server",supports_real_subtasks=not diagnostic_only,
            production_verified=not diagnostic_only,max_parallelism=capabilities.max_parallelism,
            reason="同一候选受控转换；原始审稿及原生回执保留"),
        input_manifest_sha256=context.input_manifest_sha256,tasks=[task])
    record=BatchChapterReview(batch_id=batch_id,chapter_number=context.chapter_number,review_mode=batch.review_mode,
        task_id=None if diagnostic_only else receipt.task_id,parent_task_id=None if diagnostic_only else receipt.parent_task_id,
        task_input_manifest_sha256=reviewer.input_manifest_sha256,target=target,reviewed_at=now,**result.model_dump())
    context_path=Path(context.snapshot_root).parent/"context.json"
    conversion={"commit_id":commit_id,"diagnostic_only":diagnostic_only,
        "context_path":context_path.relative_to(Path(context.canonical_root)).as_posix(),"context_sha256":file_digest(context_path),
        "proof_sha256":file_digest(proof_path),"original_result":raw["result"],"original_target":raw["target"],
        "formal_target":target.model_dump(mode="json"),"native_task_id":receipt.task_id,
        "native_parent_id":receipt.parent_task_id,"native_manifest":receipt.task_input_manifest_sha256}
    files={directory/"batch.json":batch.model_dump_json(indent=2),
        _review_output_path(private,batch_id,context.chapter_number):record.model_dump_json(indent=2),
        directory/"conversion.json":json.dumps(conversion,ensure_ascii=False,indent=2)}
    for path,text in files.items(): _v_write_text(path, text,encoding="utf-8")
    return [_relative(private,path) for path in files]


def record_candidate_review(context, *, author_identity: str, author_session_id: str,
    reviewer_task, result: ReviewResultInput, capabilities, diagnostic_only: bool = True,
    host_provenance: dict | None = None) -> dict:
    """Full-candidate semantic intake in private storage, using the existing review rubric."""
    from knowledge_product_workflow.production_context import (
        CandidateProofSet,
        verify_candidate_proofs,
    )
    from knowledge_product_workflow.production_dependencies import file_digest, payload_digest
    context.verify()
    if not diagnostic_only and not capabilities.admits_live_parallelism():
        raise ValueError("未通过宿主资格，不能签真实独立审稿")
    if (reviewer_task.role != "reviewer" or reviewer_task.chapter_number != context.chapter_number
            or reviewer_task.candidate_version != context.candidate_version
            or reviewer_task.state not in {"reviewing", "acceptance_ready"}):
        raise ValueError("审稿目标／候选版本不相等")
    if result.reviewer_identity == author_identity or result.reviewer_session_id == author_session_id:
        raise ValueError("作者身份或作者会话不能自签独立审稿")
    if not diagnostic_only and (not reviewer_task.host_task_id
            or result.reviewer_session_id != reviewer_task.host_task_id
            or not reviewer_task.host_parent_id or reviewer_task.host_task_id == author_session_id):
        raise ValueError("审稿缺少真实独立父子任务回执")
    if not diagnostic_only and (
            reviewer_task.candidate_context_path != str(Path(context.snapshot_root).parent / "context.json")
            or reviewer_task.candidate_source_sha256 != file_digest(
                Path(context.snapshot_root) / context.source_relative)):
        raise ValueError("审稿任务未绑定同一完整候选源稿")
    proofs_path = Path(context.private_result_root) / "proofs.json"
    proof = CandidateProofSet.model_validate_json(proofs_path.read_text(encoding="utf-8"))
    verify_candidate_proofs(context, proof)
    contract_relative = f"chapters/chapter-{context.chapter_number:02d}-contract.md"
    contract = Path(context.snapshot_root) / contract_relative
    missing_units = set(_required_units(contract)) - {e.unit for e in result.unit_evidence}
    issues = list(result.revision_requests)
    if result.status is not ReviewStatus.PASSED:
        issues.append("实际审稿未通过")
    if min(result.scores.model_dump().values()) < 3 or result.scores.average < 3.5:
        issues.append("审稿评分未达原Rubric阈值")
    if any(i.severity is Severity.BLOCKER for i in result.issues):
        issues.append("审稿存在未解决blocker")
    if missing_units:
        issues.append("审稿缺必需教学单元：" + ", ".join(sorted(missing_units)))
    target = CandidateReviewTarget(chapter_number=context.chapter_number,
        chapter_path=f"chapters/chapter-{context.chapter_number:02d}.md",
        contract_path=contract_relative, chapter_sha256=proof.source_sha256,
        contract_sha256=file_digest(contract), chapter_version=context.candidate_version,
        author_identity=author_identity, author_session_id=author_session_id,
        candidate_task_id=context.task_id, candidate_version=context.candidate_version,
        candidate_input_manifest_sha256=context.input_manifest_sha256,
        candidate_context_path=str(Path(context.snapshot_root).parent / "context.json"))
    record = {"schema_version": 1, "target": target.model_dump(), "diagnostic_only": diagnostic_only,
        "whole_chapter_source": context.source_relative, "reviewer_task_id": reviewer_task.task_id,
        "result": result.model_dump(mode="json"), "issues": issues,
        "acceptance_ready": not proof.required_missing and not issues,
        "result_digest": payload_digest(result.model_dump(mode="json"))}
    if host_provenance is not None:
        record["host_provenance"] = host_provenance
    path = Path(context.private_result_root) / "candidate-review.json"
    if _v_exists(path):
        previous = json.loads(path.read_text(encoding="utf-8"))
        if previous != record:
            raise ValueError("同一候选审稿已提交不同结果；需新版本，不覆盖旧证据")
        return previous
    _v_write_text(path, json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    proof.reports["candidate_review"] = str(path)
    proof.report_bindings[str(path)] = file_digest(path)
    proof.diagnostic_only = diagnostic_only
    proof.required_missing.extend(issues)
    _v_write_text(proofs_path, proof.model_dump_json(indent=2), encoding="utf-8")
    return record
