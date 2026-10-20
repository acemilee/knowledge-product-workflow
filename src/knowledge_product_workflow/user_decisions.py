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
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import (
    load_yaml as _v_load_yaml,
)
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    read_bytes as _v_read_bytes,
)
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workspace import Workspace


class UserDecisionTarget(StrEnum):
    """Reader-reviewable artifacts that require a human decision."""

    BRIEF = "brief"
    BLUEPRINT = "blueprint"
    SAMPLE = "sample"
    BRIEF_AMENDMENT = "brief_amendment"
    PDF = "pdf"


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
    publication_profile_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
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
    sample_basis_sha256: str | None = None
    carried_forward_from: str | None = None
    requested_scope: list[str] = Field(default_factory=list)
    confirmed_scope: list[str] = Field(default_factory=list)
    decision_context_sha256: str | None = None
    question: str | None = None


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
    UserDecisionTarget.PDF: Path("delivery/book.pdf"),
}

_PROMPTS = {
    UserDecisionTarget.BRIEF: "请确认产品定位，或直接说明修改要求。",
    UserDecisionTarget.BLUEPRINT: "请确认课程结构，或直接说明修改要求。",
    UserDecisionTarget.SAMPLE: "请确认是否按该样章方向继续生成全书，或直接说明修改要求。",
    UserDecisionTarget.BRIEF_AMENDMENT: "请确认 Brief 修改，或直接说明需要继续调整的内容。",
    UserDecisionTarget.PDF: "请确认当前 PDF 的已展示范围。",
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
    return _v_is_file(workspace.root / "runs" / "orchestration" / "state.json")


def _artifact_snapshot(workspace: Workspace, target: UserDecisionTarget) -> tuple[str, str, int]:
    path = target_path(workspace, target)
    if not _v_is_file(path):
        raise FileNotFoundError(f"缺少待确认产物：{path.relative_to(workspace.root)}")
    artifact_sha256 = sha256(_v_read_bytes(path)).hexdigest()
    if target is UserDecisionTarget.PDF:
        version = 1  # PDF identity is its byte digest, not a fabricated document revision.
    elif target is UserDecisionTarget.BRIEF_AMENDMENT:
        try:
            payload = json.loads(_v_read_text(path, encoding="utf-8"))
            prior_version = payload["prior_version"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError("Brief 修订预览格式无效") from error
        if not isinstance(prior_version, int) or prior_version < 1:
            raise ValueError("Brief 修订预览缺少有效版本")
        version = prior_version + 1
    else:
        metadata, _ = parse_markdown_artifact(_v_read_text(path, encoding="utf-8"))
        version = metadata.version
    return str(path.relative_to(workspace.root)).replace("\\", "/"), artifact_sha256, version


def _read_record(workspace: Workspace, target: UserDecisionTarget) -> UserDecisionRecord | None:
    path = _record_path(workspace, target)
    if not _v_is_file(path):
        return None
    try:
        return UserDecisionRecord.model_validate_json(_v_read_text(path, encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"用户决定记录格式无效：{path.relative_to(workspace.root)}") from error


def _write_record(workspace: Workspace, record: UserDecisionRecord) -> Path:
    output = _record_path(workspace, record.target)
    _v_mkdir(output.parent,parents=True,exist_ok=True)
    _v_write_text(output, record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return output


def _archive_record(workspace: Workspace, record: UserDecisionRecord) -> Path:
    history = _history_root(workspace)
    _v_mkdir(history,parents=True,exist_ok=True)
    output = history / f"{record.target.value}-{record.decision_id}.json"
    _v_write_text(output, record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return output


def _design_binding(workspace: Workspace, target: UserDecisionTarget) -> str | None:
    if target not in {UserDecisionTarget.BRIEF, UserDecisionTarget.PDF}:
        return None
    path = workspace.root / "planning" / "publication-profile.yaml"
    if not _v_is_file(path):
        return None
    raw = _v_load_yaml(_v_read_text(path, encoding="utf-8"))
    return sha256(_v_read_bytes(path)).hexdigest() if isinstance(raw, dict) and raw.get("profile_id") == "knowledge-product-pdf-v6" else None


def _matches_snapshot(
    workspace: Workspace, record: UserDecisionRecord, *, path: str, artifact_sha256: str, version: int
) -> bool:
    if record.target is UserDecisionTarget.PDF and record.decision_context_sha256 != _pdf_context(workspace)[0]:
        return False
    if record.target is UserDecisionTarget.SAMPLE and record.sample_basis_sha256 is not None:
        from knowledge_product_workflow.change_impact import semantic_digest
        return (record.artifact_path == path
                and record.sample_basis_sha256 == semantic_digest(sample_approval_basis(workspace)))
    return (
        record.artifact_path == path
        and record.artifact_sha256 == artifact_sha256
        and record.artifact_version == version
        and record.publication_profile_sha256 == _design_binding(workspace, record.target)
    )


def pdf_requested_scopes(scopes: list[str] | None) -> list[str]:
    requested = sorted(set(scopes or ['visual']))
    if 'visual' not in requested or not set(requested) <= {'visual','content','manual_full_coverage','style_anchor'}:
        raise ValueError('PDF确认范围无效；visual必需，完整覆盖须单独请求')
    return requested


def precheck_pdf_reply(workspace: Workspace, scopes: list[str] | None, response: str) -> None:
    record = _read_record(workspace, UserDecisionTarget.PDF)
    if record is None:
        raise UserDecisionRequiredError('尚未展示当前产物，不能记录用户决定')
    if not response.strip() or not set(scopes or ['visual']) <= set(record.requested_scope):
        raise ValueError('确认范围超出已展示问题或回复为空；不扩大用户答复')


def request_user_decision(
    workspace: Workspace,
    target: UserDecisionTarget,
    *,
    work_order_id: str | None = None,
    scopes: list[str] | None = None,
) -> UserDecisionRecord:
    """Record that the current artifact was presented for a user decision."""
    requested = pdf_requested_scopes(scopes) if target is UserDecisionTarget.PDF else []
    if target is UserDecisionTarget.PDF:
        from knowledge_product_workflow.delivery_preflight import require_delivery_preflight
        require_delivery_preflight(workspace)
    path, artifact_sha256, version = _artifact_snapshot(workspace, target)
    existing = _read_record(workspace, target)
    if existing is not None and _matches_snapshot(
        workspace, existing, path=path, artifact_sha256=artifact_sha256, version=version
    ) and existing.requested_scope == requested and existing.state not in {UserDecisionState.INVALIDATED, UserDecisionState.CONSUMED}:
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
        publication_profile_sha256=_design_binding(workspace, target),
        requested_at=now,
        displayed_at=now,
        work_order_id=work_order_id,
        requested_scope=requested,
    )
    if target is UserDecisionTarget.PDF:
        context, count = _pdf_context(workspace)
        record.decision_context_sha256 = context
        labels = {'visual':'视觉效果','content':'内容','manual_full_coverage':'本人完整检查全部页面','style_anchor':'母版风格'}
        record.question = (f'请核对当前PDF（共{count}页）。确认范围：' + '、'.join(labels[s] for s in requested) +
            ('；请只在你本人已直接完整检查全部页面后确认完整覆盖。' if 'manual_full_coverage' in requested else '；一般批准不包含本人完整全页覆盖。'))
    from knowledge_product_workflow.change_impact import binding_policy_revision, semantic_digest
    if target is UserDecisionTarget.SAMPLE and binding_policy_revision(workspace) == 2:
        record.sample_basis_sha256 = semantic_digest(sample_approval_basis(workspace))
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
    confirmed_scope: list[str] | None = None,
) -> UserDecisionRecord:
    """Persist the host-interpreted user response without retaining its raw text."""
    record = _read_record(workspace, target)
    if record is None:
        raise UserDecisionRequiredError("尚未展示当前产物，不能记录用户决定")
    scopes = sorted(set(confirmed_scope or ['visual'])) if target is UserDecisionTarget.PDF else []
    if target is UserDecisionTarget.PDF and not set(scopes) <= set(record.requested_scope):
        raise ValueError('确认范围超出已展示问题；重新展示明确范围，不扩大用户答复')
    path, artifact_sha256, version = _artifact_snapshot(workspace, target)
    if not _matches_snapshot(workspace, record, path=path, artifact_sha256=artifact_sha256, version=version):
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
            "confirmed_scope": scopes,
        }
    )
    _write_record(workspace, resolved)
    workspace.append_event(
        "user_decision_recorded",
        {"target": target.value, "decision": decision.value, "decision_id": record.decision_id},
    )
    return resolved


def _pdf_context(workspace: Workspace) -> tuple[str, int]:
    from knowledge_product_workflow.pdf_preview import current_pdf_preview
    from knowledge_product_workflow.visual_audit import audit_path, current_visual_audit
    pdf = workspace.root / 'delivery/book.pdf'
    preview = current_pdf_preview(pdf, workspace.root / 'runs/pdf-preview')
    audit = current_visual_audit(workspace)
    if preview is None or audit is None:
        raise ValueError('PDF决定需要当前完整预览与视觉报告')
    paths = [pdf, preview[0], audit_path(workspace), workspace.root/'planning/publication-profile.yaml']
    values = [(str(p.relative_to(workspace.root)), sha256(_v_read_bytes(p)).hexdigest()) for p in paths]
    return sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest(), audit.page_count


def require_pdf_scope(workspace: Workspace, scopes: set[str]) -> UserDecisionRecord:
    record = require_approved_user_decision(workspace, UserDecisionTarget.PDF)
    if not scopes <= set(record.confirmed_scope):
        raise UserDecisionRequiredError('当前PDF用户决定未明确包含所请求范围：' + '、'.join(sorted(scopes)))
    return record


def precheck_pdf_scope(workspace: Workspace, scopes: set[str], response: str | None = None) -> None:
    """Cheap rejection only; never replaces current artifact/permission checks."""
    record = _read_record(workspace, UserDecisionTarget.PDF)
    if record is None or record.state is not UserDecisionState.APPROVED:
        raise UserDecisionRequiredError('缺少当前PDF已批准决定；先request-user-decision pdf展示范围，再record-user-decision pdf记录实际答复')
    if not scopes <= set(record.confirmed_scope):
        raise UserDecisionRequiredError('PDF实际确认scope缺少：' + '、'.join(sorted(scopes-set(record.confirmed_scope)))
            + '；不能扩展一般回复，读取当前请求范围与用户原话')
    if response is not None and sha256(response.strip().encode()).hexdigest() != record.response_sha256:
        raise ValueError('PDF确认原话与已登记用户决定不一致')


def require_approved_user_decision(workspace: Workspace, target: UserDecisionTarget) -> UserDecisionRecord:
    """Return the current approval decision or stop a protected transition."""
    record = _read_record(workspace, target)
    if record is None:
        raise UserDecisionRequiredError("尚未收到当前版本的用户确认")
    path, artifact_sha256, version = _artifact_snapshot(workspace, target)
    if not _matches_snapshot(workspace, record, path=path, artifact_sha256=artifact_sha256, version=version):
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


def prepare_decision_invalidation(workspace: Workspace, target: UserDecisionTarget, *,
                                  reason: str, include_consumed: bool = False) -> tuple[dict, list]:
    """Compute a decision change for the caller's file transaction; never signs approval."""
    record = _read_record(workspace, target)
    if record is None:
        if _v_exists(_record_path(workspace, target)):
            raise UserDecisionStaleError("人工决定记录损坏，不能按缺失处理")
        return {}, []
    if record.state is UserDecisionState.INVALIDATED or (
            record.state is UserDecisionState.CONSUMED and not include_consumed):
        return {}, []
    invalidated = record.model_copy(update={"state": UserDecisionState.INVALIDATED,
        "invalidated_at": datetime.now(UTC), "invalidated_reason": reason})
    relative = _record_path(workspace, target).relative_to(workspace.root).as_posix()
    return {relative: (invalidated.model_dump_json(indent=2) + "\n").encode("utf-8")}, [
        ("user_decision_invalidated", {"target": target.value,
         "decision_id": record.decision_id, "reason": reason})]


def sample_approval_basis(workspace: Workspace, *, body_override: str | None = None) -> dict:
    from knowledge_product_workflow.change_impact import chapter_bindings
    from knowledge_product_workflow.diagrams import diagram_semantics_digest
    path = workspace.root / "chapters/chapter-01.md"
    metadata, body = parse_markdown_artifact(_v_read_text(path, encoding="utf-8"))
    reader = (body_override if body_override is not None else body).strip() + "\n"
    return {"book_id": metadata.book_id, "reader_body_sha256": sha256(reader.encode("utf-8")).hexdigest(),
        "requirements": chapter_bindings(workspace, 1).semantic_values(),
        "diagram_semantics": diagram_semantics_digest(workspace, reader)}


def sample_approval_is_current(workspace: Workspace, *, body_override: str | None = None) -> bool:
    from knowledge_product_workflow.change_impact import binding_policy_revision, semantic_digest
    marker = workspace.root / "chapters/chapter-01-sample.approved"
    if not _v_is_file(marker):
        return False
    if binding_policy_revision(workspace) == 1:
        return True
    try:
        saved = json.loads(_v_read_text(marker, encoding="utf-8"))
        record = _read_record(workspace, UserDecisionTarget.SAMPLE)
        if managed_user_decisions(workspace) and (record is None or record.state not in {
                UserDecisionState.APPROVED, UserDecisionState.CONSUMED}):
            return False
        return (saved["basis_sha256"] == semantic_digest(sample_approval_basis(workspace, body_override=body_override))
                and (record is None or saved.get("decision_id") == record.decision_id))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def carry_sample_approval(workspace: Workspace, *, impact_id: str) -> Path:
    # U08: no restoration of a revoked/unknown decision, even with a matching body.
    if not sample_approval_is_current(workspace):
        raise UserDecisionStaleError("旧样章批准不可自动恢复；请走原人工决定")
    return workspace.root / "chapters/chapter-01-sample.approved"


def prepare_decision_consumption(workspace: Workspace, target: UserDecisionTarget) -> tuple[dict, list]:
    record = require_approved_user_decision(workspace, target)
    consumed = record.model_copy(update={"state": UserDecisionState.CONSUMED, "consumed_at": datetime.now(UTC)})
    relative = _record_path(workspace, target).relative_to(workspace.root).as_posix()
    return {relative: (consumed.model_dump_json(indent=2) + "\n").encode("utf-8")}, [
        ("user_decision_consumed", {"target": target.value, "decision_id": record.decision_id})]


def import_legacy_approval_records(workspace: Workspace) -> list[UserDecisionRecord]:
    """Import existing approval facts without asking legacy users to repeat them."""
    imported: list[UserDecisionRecord] = []
    for target in (UserDecisionTarget.BRIEF, UserDecisionTarget.BLUEPRINT):
        path = target_path(workspace, target)
        if not _v_is_file(path) or _read_record(workspace, target) is not None:
            continue
        try:
            metadata, _ = parse_markdown_artifact(_v_read_text(path, encoding="utf-8"))
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
    if _v_is_file(sample_marker) and _read_record(workspace, UserDecisionTarget.SAMPLE) is None:
        try:
            artifact_path, artifact_sha256, version = _artifact_snapshot(workspace, UserDecisionTarget.SAMPLE)
        except (FileNotFoundError, ValueError):
            return imported
        approved_by = "legacy_import"
        for line in _v_read_text(sample_marker, encoding="utf-8").splitlines():
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
    elif not _matches_snapshot(workspace, record, path=path, artifact_sha256=artifact_sha256, version=version):
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
