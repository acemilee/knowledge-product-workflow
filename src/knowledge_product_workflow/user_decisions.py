"""Version-bound human decision records for host-driven approval gates."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.workspace import Workspace


class UserDecisionTarget(StrEnum):
    """Reader-reviewable artifacts that require a human decision."""

    BRIEF = "brief"
    BLUEPRINT = "blueprint"
    SAMPLE = "sample"
    BRIEF_AMENDMENT = "brief_amendment"


class UserDecisionState(StrEnum):
    """Lifecycle of a single version-bound human decision."""

    PENDING = "pending"
    APPROVED = "approved"
    REVISE = "revise"
    CLARIFY = "clarify"
    CONSUMED = "consumed"
    INVALIDATED = "invalidated"


class UserResponseDecision(StrEnum):
    """The three host-interpreted outcomes of a user reply."""

    APPROVE = "approve"
    REVISE = "revise"
    CLARIFY = "clarify"


class UserDecisionRecord(BaseModel):
    """A minimal audit record without persisting full user conversation text."""

    schema_version: int = 1
    decision_id: str
    target: UserDecisionTarget
    artifact_path: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_version: int = Field(ge=1)
    requested_at: datetime
    displayed_at: datetime
    state: UserDecisionState = UserDecisionState.PENDING
    decided_at: datetime | None = None
    approved_by: str | None = None
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    feedback_summary: str | None = Field(default=None, max_length=500)
    work_order_id: str | None = None
    consumed_at: datetime | None = None
    invalidated_at: datetime | None = None
    invalidated_reason: str | None = None
    provenance: str = "current"


class UserDecisionRequirement(BaseModel):
    """Read-only status data for a host; default terminal output hides its internals."""

    target: UserDecisionTarget
    state: str
    artifact_path: str
    artifact_sha256: str
    artifact_version: int
    user_prompt: str
    decision_id: str | None = None


class UserDecisionRequiredError(ValueError):
    """Raised when a managed workspace tries to advance without human input."""


class UserDecisionStaleError(UserDecisionRequiredError):
    """Raised when a decision no longer describes the current artifact version."""


_TARGET_PATHS = {
    UserDecisionTarget.BRIEF: Path("planning/product-brief.md"),
    UserDecisionTarget.BLUEPRINT: Path("planning/curriculum-blueprint.md"),
    UserDecisionTarget.SAMPLE: Path("chapters/chapter-01.md"),
    UserDecisionTarget.BRIEF_AMENDMENT: Path("runs/brief-amendment-preview.json"),
}

_PROMPTS = {
    UserDecisionTarget.BRIEF: "请确认产品定位，或直接说明修改要求。",
    UserDecisionTarget.BLUEPRINT: "请确认课程结构，或直接说明修改要求。",
    UserDecisionTarget.SAMPLE: "请确认是否按该样章方向继续生成全书，或直接说明修改要求。",
    UserDecisionTarget.BRIEF_AMENDMENT: "请确认 Brief 修改，或直接说明需要继续调整的内容。",
}


def _interaction_root(workspace: Workspace) -> Path:
    return workspace.root / "runs" / "interaction"


def _record_path(workspace: Workspace, target: UserDecisionTarget) -> Path:
    return _interaction_root(workspace) / f"{target.value}.json"


def _history_root(workspace: Workspace) -> Path:
    return _interaction_root(workspace) / "history"


def target_path(workspace: Workspace, target: UserDecisionTarget) -> Path:
    return workspace.root / _TARGET_PATHS[target]


def target_for_artifact(workspace: Workspace, artifact: Path) -> UserDecisionTarget | None:
    """Map a canonical review artifact to its decision target."""
    resolved = artifact.resolve()
    for target, relative in _TARGET_PATHS.items():
        if resolved == (workspace.root / relative).resolve():
            return target
    return None


def managed_user_decisions(workspace: Workspace) -> bool:
    """Enable strict decision gates only after v1.1 orchestration is adopted."""
    return (workspace.root / "runs" / "orchestration" / "state.json").is_file()


def _artifact_snapshot(workspace: Workspace, target: UserDecisionTarget) -> tuple[str, str, int]:
    path = target_path(workspace, target)
    if not path.is_file():
        raise FileNotFoundError(f"缺少待确认产物：{path.relative_to(workspace.root)}")
    artifact_sha256 = sha256(path.read_bytes()).hexdigest()
    if target is UserDecisionTarget.BRIEF_AMENDMENT:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            prior_version = payload["prior_version"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError("Brief 修订预览格式无效") from error
        if not isinstance(prior_version, int) or prior_version < 1:
            raise ValueError("Brief 修订预览缺少有效版本")
        version = prior_version + 1
    else:
        metadata, _ = parse_markdown_artifact(path.read_text(encoding="utf-8"))
        version = metadata.version
    return str(path.relative_to(workspace.root)).replace("\\", "/"), artifact_sha256, version


def _read_record(workspace: Workspace, target: UserDecisionTarget) -> UserDecisionRecord | None:
    path = _record_path(workspace, target)
    if not path.is_file():
        return None
    try:
        return UserDecisionRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"用户决定记录格式无效：{path.relative_to(workspace.root)}") from error


def _write_record(workspace: Workspace, record: UserDecisionRecord) -> Path:
    output = _record_path(workspace, record.target)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return output


def _archive_record(workspace: Workspace, record: UserDecisionRecord) -> Path:
    history = _history_root(workspace)
    history.mkdir(parents=True, exist_ok=True)
    output = history / f"{record.target.value}-{record.decision_id}.json"
    output.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return output


def _matches_snapshot(
    record: UserDecisionRecord, *, path: str, artifact_sha256: str, version: int
) -> bool:
    return (
        record.artifact_path == path
        and record.artifact_sha256 == artifact_sha256
        and record.artifact_version == version
    )


def request_user_decision(
    workspace: Workspace,
    target: UserDecisionTarget,
    *,
    work_order_id: str | None = None,
) -> UserDecisionRecord:
    """Record that the current artifact was presented for a user decision."""
    path, artifact_sha256, version = _artifact_snapshot(workspace, target)
    existing = _read_record(workspace, target)
    if existing is not None and _matches_snapshot(
        existing, path=path, artifact_sha256=artifact_sha256, version=version
    ) and existing.state not in {UserDecisionState.INVALIDATED, UserDecisionState.CONSUMED}:
        return existing
    if existing is not None:
        if existing.state is not UserDecisionState.INVALIDATED:
            existing = existing.model_copy(
                update={
                    "state": UserDecisionState.INVALIDATED,
                    "invalidated_at": datetime.now(UTC),
                    "invalidated_reason": "待确认产物版本已变化",
                }
            )
        _archive_record(workspace, existing)
    now = datetime.now(UTC)
    record = UserDecisionRecord(
        decision_id=f"decision-{uuid4().hex}",
        target=target,
        artifact_path=path,
        artifact_sha256=artifact_sha256,
        artifact_version=version,
        requested_at=now,
        displayed_at=now,
        work_order_id=work_order_id,
    )
    _write_record(workspace, record)
    workspace.append_event(
        "user_decision_requested",
        {"target": target.value, "decision_id": record.decision_id, "artifact": path},
    )
    return record


def record_user_decision(
    workspace: Workspace,
    target: UserDecisionTarget,
    decision: UserResponseDecision,
    *,
    approved_by: str,
    response: str,
    feedback_summary: str | None = None,
) -> UserDecisionRecord:
    """Persist the host-interpreted user response without retaining its raw text."""
    record = _read_record(workspace, target)
    if record is None:
        raise UserDecisionRequiredError("尚未展示当前产物，不能记录用户决定")
    path, artifact_sha256, version = _artifact_snapshot(workspace, target)
    if not _matches_snapshot(record, path=path, artifact_sha256=artifact_sha256, version=version):
        raise UserDecisionStaleError("内容已更新，请确认最新版本")
    if record.state is UserDecisionState.CONSUMED:
        raise UserDecisionRequiredError("当前用户决定已被消费；请等待新的待确认产物")
    if record.state is UserDecisionState.INVALIDATED:
        raise UserDecisionStaleError("内容已更新，请确认最新版本")
    response_value = response.strip()
    if not response_value:
        raise ValueError("用户决定需要非空的原始回复")
    now = datetime.now(UTC)
    state = {
        UserResponseDecision.APPROVE: UserDecisionState.APPROVED,
        UserResponseDecision.REVISE: UserDecisionState.REVISE,
        UserResponseDecision.CLARIFY: UserDecisionState.CLARIFY,
    }[decision]
    resolved = record.model_copy(
        update={
            "state": state,
            "decided_at": now,
            "approved_by": approved_by.strip(),
            "response_sha256": sha256(response_value.encode("utf-8")).hexdigest(),
            "feedback_summary": feedback_summary.strip() if feedback_summary else None,
        }
    )
    _write_record(workspace, resolved)
    workspace.append_event(
        "user_decision_recorded",
        {"target": target.value, "decision": decision.value, "decision_id": record.decision_id},
    )
    return resolved


def require_approved_user_decision(workspace: Workspace, target: UserDecisionTarget) -> UserDecisionRecord:
    """Return the current approval decision or stop a protected transition."""
    record = _read_record(workspace, target)
    if record is None:
        raise UserDecisionRequiredError("尚未收到当前版本的用户确认")
    path, artifact_sha256, version = _artifact_snapshot(workspace, target)
    if not _matches_snapshot(record, path=path, artifact_sha256=artifact_sha256, version=version):
        raise UserDecisionStaleError("内容已更新，请确认最新版本")
    if record.state is UserDecisionState.INVALIDATED:
        raise UserDecisionStaleError("内容已更新，请确认最新版本")
    if record.state is not UserDecisionState.APPROVED:
        raise UserDecisionRequiredError("当前产物尚未获得用户继续确认")
    return record


def consume_approved_user_decision(
    workspace: Workspace,
    target: UserDecisionTarget,
    *,
    record: UserDecisionRecord | None = None,
) -> UserDecisionRecord:
    """Consume one approval after its bound state transition succeeds."""
    approved = record or require_approved_user_decision(workspace, target)
    current = _read_record(workspace, target)
    if current is None or current.decision_id != approved.decision_id:
        raise UserDecisionRequiredError("当前用户决定已被替换；不能消费旧决定")
    if current.state is not UserDecisionState.APPROVED:
        raise UserDecisionRequiredError("当前用户决定不是可消费的继续确认")
    consumed = current.model_copy(
        update={"state": UserDecisionState.CONSUMED, "consumed_at": datetime.now(UTC)}
    )
    _write_record(workspace, consumed)
    workspace.append_event(
        "user_decision_consumed",
        {"target": target.value, "decision_id": approved.decision_id},
    )
    return consumed


def invalidate_user_decision(
    workspace: Workspace, target: UserDecisionTarget, *, reason: str, include_consumed: bool = False
) -> UserDecisionRecord | None:
    """Invalidate a prior decision after an upstream artifact changes."""
    record = _read_record(workspace, target)
    if record is None or record.state is UserDecisionState.INVALIDATED:
        return record
    if record.state is UserDecisionState.CONSUMED and not include_consumed:
        return record
    invalidated = record.model_copy(
        update={
            "state": UserDecisionState.INVALIDATED,
            "invalidated_at": datetime.now(UTC),
            "invalidated_reason": reason,
        }
    )
    _write_record(workspace, invalidated)
    workspace.append_event(
        "user_decision_invalidated",
        {"target": target.value, "decision_id": record.decision_id, "reason": reason},
    )
    return invalidated


def import_legacy_approval_records(workspace: Workspace) -> list[UserDecisionRecord]:
    """Import existing approval facts without asking legacy users to repeat them."""
    imported: list[UserDecisionRecord] = []
    for target in (UserDecisionTarget.BRIEF, UserDecisionTarget.BLUEPRINT):
        path = target_path(workspace, target)
        if not path.is_file() or _read_record(workspace, target) is not None:
            continue
        try:
            metadata, _ = parse_markdown_artifact(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if metadata.status is not ArtifactStatus.APPROVED:
            continue
        artifact_path, artifact_sha256, version = _artifact_snapshot(workspace, target)
        now = datetime.now(UTC)
        record = UserDecisionRecord(
            decision_id=f"legacy-{uuid4().hex}",
            target=target,
            artifact_path=artifact_path,
            artifact_sha256=artifact_sha256,
            artifact_version=version,
            requested_at=now,
            displayed_at=now,
            state=UserDecisionState.CONSUMED,
            decided_at=now,
            approved_by=metadata.approved_by or "legacy_import",
            consumed_at=now,
            provenance="legacy_import",
        )
        _write_record(workspace, record)
        imported.append(record)

    sample_marker = workspace.root / "chapters" / "chapter-01-sample.approved"
    if sample_marker.is_file() and _read_record(workspace, UserDecisionTarget.SAMPLE) is None:
        try:
            artifact_path, artifact_sha256, version = _artifact_snapshot(workspace, UserDecisionTarget.SAMPLE)
        except (FileNotFoundError, ValueError):
            return imported
        approved_by = "legacy_import"
        for line in sample_marker.read_text(encoding="utf-8").splitlines():
            if line.startswith("approved_by:") and line.partition(":")[2].strip():
                approved_by = line.partition(":")[2].strip()
                break
        now = datetime.now(UTC)
        record = UserDecisionRecord(
            decision_id=f"legacy-{uuid4().hex}",
            target=UserDecisionTarget.SAMPLE,
            artifact_path=artifact_path,
            artifact_sha256=artifact_sha256,
            artifact_version=version,
            requested_at=now,
            displayed_at=now,
            state=UserDecisionState.CONSUMED,
            decided_at=now,
            approved_by=approved_by,
            consumed_at=now,
            provenance="legacy_import",
        )
        _write_record(workspace, record)
        imported.append(record)
    if imported:
        workspace.append_event(
            "legacy_user_decisions_imported",
            {"targets": [record.target.value for record in imported]},
        )
    return imported


def user_decision_requirement(
    workspace: Workspace, target: UserDecisionTarget
) -> UserDecisionRequirement:
    """Return deterministic readiness for ``kp status --json`` without mutation."""
    path, artifact_sha256, version = _artifact_snapshot(workspace, target)
    record = _read_record(workspace, target)
    if record is None:
        state = "request_required"
        decision_id = None
    elif (
        record.state is UserDecisionState.CONSUMED
        and record.artifact_path == path
        and record.artifact_version == version
    ):
        # Approval changes the artifact frontmatter SHA but not its reviewed version.
        state = UserDecisionState.CONSUMED.value
        decision_id = record.decision_id
    elif not _matches_snapshot(record, path=path, artifact_sha256=artifact_sha256, version=version):
        state = "stale"
        decision_id = record.decision_id
    else:
        state = record.state.value
        decision_id = record.decision_id
    return UserDecisionRequirement(
        target=target,
        state=state,
        artifact_path=path,
        artifact_sha256=artifact_sha256,
        artifact_version=version,
        user_prompt=_PROMPTS[target],
        decision_id=decision_id,
    )
