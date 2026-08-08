"""Read-only workflow state inspection and deterministic Skill routing."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from knowledge_product_workflow.assumption_ledger import (
    AssumptionDisposition,
    latest_assumptions,
    required_assumption_claim_ids,
)
from knowledge_product_workflow.chapter_compliance import validate_current_chapter_compliance
from knowledge_product_workflow.claims import current_claim_contract_bindings
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.quality import (
    QualityReport,
    QualityReportValidity,
    quality_report_upstream_validity,
    quality_report_validity,
)
from knowledge_product_workflow.quarto import resolve_quarto
from knowledge_product_workflow.review_batches import HostCapabilities
from knowledge_product_workflow.source_ledger import SourceLedgerEntry, validate_claims
from knowledge_product_workflow.user_decisions import (
    UserDecisionRequirement,
    UserDecisionTarget,
    import_legacy_approval_records,
    user_decision_requirement,
)
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from knowledge_product_workflow.workflows.curriculum import (
    CurriculumPrerequisiteError,
    require_current_submitted_blueprint,
)
from knowledge_product_workflow.workflows.product_brief import (
    brief_contract_fingerprints,
    parse_detail_constraints,
)
from knowledge_product_workflow.workspace import Workspace

_CHAPTER_NUMBER = re.compile(r"^\s*(\d+)\.\s+", re.MULTILINE)
_CLAIM_SECTION = re.compile(
    r"^## (?:必需事实声明|Required claims)\s*\n(?P<body>.*?)(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)
_CLAIM_ID = re.compile(r"\bclaim-[a-z0-9][a-z0-9-]*\b", re.IGNORECASE)


class MacroPhase(StrEnum):
    """User-visible orchestration phases; detailed stages remain backwards compatible."""

    DEFINITION = "definition"
    ARCHITECTURE = "architecture"
    PRODUCTION = "production"
    ASSURANCE = "assurance"
    DELIVERY = "delivery"


class WorkflowStatus(BaseModel):
    """Machine-readable snapshot derived only from persisted workspace artifacts."""

    stage: str
    blocked_by: list[str] = Field(default_factory=list)
    recommended_skill: str
    next_command: str
    artifacts: dict[str, str] = Field(default_factory=dict)
    declared_claim_ids: list[str] = Field(default_factory=list)
    recent_events: list[str] = Field(default_factory=list)
    next_actions: list[WorkflowAction] = Field(default_factory=list)
    macro_phase: MacroPhase = Field(default_factory=lambda: MacroPhase.DEFINITION)
    user_summary: str = ""
    required_human_decision: str | None = None
    user_decision: UserDecisionRequirement | None = None
    current_work_order: str | None = None
    orchestration_mode: str = "legacy"
    run_intent_state: str | None = None
    auto_to_delivery: bool = False


class WorkflowAction(BaseModel):
    """One structured command action that a host can execute without guessing."""

    command: str
    argv: list[str]
    reason_code: str


class WorkOrder(BaseModel):
    """A bounded handoff contract for one host-driven Skill invocation."""

    work_order_id: str
    created_at: datetime
    stage: str
    skill: str
    inputs: list[str]
    allowed_read_paths: list[str]
    allowed_write_paths: list[str]
    outputs: list[str]
    acceptance_commands: list[str]


class WorkOrderLifecycle(StrEnum):
    """Controller-owned lifecycle for one scoped Skill handoff."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"
    # Kept solely to read v1.1.2 controller records. New work orders do not use it.
    EXPIRED = "expired"


class InputManifestEntry(BaseModel):
    """A concrete file state frozen before a host starts a bounded action."""

    path: str
    state: str
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class OutputManifestEntry(BaseModel):
    """One concrete output observed when a controller action completed."""

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CompletionProof(BaseModel):
    """Version-bound evidence that one orchestration work order reached a terminal state."""

    completed_at: datetime
    completed_by: str = "kp-control-plane"
    transition: str = "legacy_completion"
    event_id: str | None = None
    input_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    output_manifest: list[OutputManifestEntry] = Field(default_factory=list)
    successor_work_order_id: str | None = None
    proof: str | None = None
    reason: str | None = None


class ChapterLease(BaseModel):
    """Exclusive, expiring permission for a Skill to write one chapter body."""

    lease_id: str
    chapter_number: int = Field(gt=0)
    owner_work_order_id: str
    owner_skill: str
    acquired_at: datetime
    expires_at: datetime
    state: WorkOrderLifecycle = WorkOrderLifecycle.ACTIVE


class OrchestrationWorkOrder(BaseModel):
    """Version-bound contract for one macro-routed Skill invocation."""

    schema_version: int = 2
    work_order_id: str
    created_at: datetime
    expires_at: datetime
    lifecycle: WorkOrderLifecycle = WorkOrderLifecycle.PENDING
    macro_phase: MacroPhase
    stage: str
    skill: str
    target_chapter_number: int | None = Field(default=None, gt=0)
    inputs: list[InputManifestEntry]
    input_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_read_paths: list[str]
    allowed_write_paths: list[str]
    outputs: list[str]
    allowed_commands: list[str]
    preconditions: list[str]
    success_transition: str
    failure_transition: str
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    lease: ChapterLease | None = None
    completion_proof: CompletionProof | None = None

    @model_validator(mode="after")
    def require_terminal_evidence(self) -> OrchestrationWorkOrder:
        """New controller records cannot claim a terminal state without evidence."""
        terminal = {
            WorkOrderLifecycle.COMPLETED,
            WorkOrderLifecycle.FAILED,
            WorkOrderLifecycle.SUPERSEDED,
            WorkOrderLifecycle.CANCELLED,
        }
        if self.schema_version >= 2 and self.lifecycle in terminal and self.completion_proof is None:
            raise ValueError("终态编排工单必须包含 completion_proof")
        return self


class OrchestrationState(BaseModel):
    """Small marker that identifies a managed workspace and its origin."""

    schema_version: int = 2
    mode: str = "managed"
    initialized_at: datetime
    migrated_from_legacy: bool = False


class RunIntentState(StrEnum):
    """Lifecycle of a user-authorized controller run, never a reader artifact."""

    ACTIVE = "active"
    WAITING_FOR_DECISION = "waiting_for_decision"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"


class RunIntent(BaseModel):
    """Durable instruction to continue safe internal work until a legal stop."""

    schema_version: int = 1
    intent_id: str
    requested_at: datetime
    updated_at: datetime
    state: RunIntentState = RunIntentState.ACTIVE
    auto_to_delivery: bool = True
    last_work_order_id: str | None = None
    last_stage: str | None = None
    stop_reason: str | None = None
    consecutive_no_progress: int = Field(default=0, ge=0)


class OrchestrationReconciliation(BaseModel):
    """Read-only controller consistency result used before delivery publication."""

    checked_at: datetime
    blockers: list[str] = Field(default_factory=list)
    recoverable: list[str] = Field(default_factory=list)
    completed_work_orders: int = Field(default=0, ge=0)
    live_work_orders: int = Field(default=0, ge=0)


class ResearchImpact(StrEnum):
    """The only research outcomes that can change downstream routing."""

    NO_PRODUCT_CHANGE = "no_product_change"
    PACKAGING_CHANGE = "packaging_change"
    CONTENT_CHANGE = "content_change"
    UNVERIFIED = "unverified"


class ResearchImpactDecision(BaseModel):
    """Reader-safe research routing decision stored outside deliverable content."""

    schema_version: int = 1
    decision_id: str
    recorded_at: datetime
    impact: ResearchImpact
    summary: str = Field(min_length=1)
    route: str
    requires_human_approval: bool


class HostCapabilitySnapshot(BaseModel):
    """Pre-writing evidence that independent review can be truthfully requested."""

    schema_version: int = 2
    checked_at: datetime
    review_policy: str
    capabilities: HostCapabilities
    decision: str
    brief_content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    brief_version: int | None = Field(default=None, ge=1)


class RouteDecision(BaseModel):
    """The one Skill currently permitted by the deterministic control plane."""

    status: WorkflowStatus
    skill: str
    work_order_path: str | None = None


class WorkflowAuthorizationError(ValueError):
    """Raised when a mutating CLI command is invoked outside its allowed stage."""


class OrchestrationWorkOrderError(ValueError):
    """Raised when a host uses a work order outside its immutable authority."""


class WorkOrderInputChangedError(OrchestrationWorkOrderError):
    """Raised when a frozen input manifest no longer describes the workspace."""


class ChapterLeaseConflictError(OrchestrationWorkOrderError):
    """Raised when a second author attempts to take an active chapter lease."""


class HostCapabilityPreflightError(OrchestrationWorkOrderError):
    """Raised before writing when required independent review cannot be evidenced."""


def _relative(workspace: Workspace, path: Path) -> str:
    return str(path.relative_to(workspace.root)).replace("\\", "/")


def _artifact_state(path: Path) -> str:
    if not path.is_file():
        return "missing"
    try:
        metadata, _ = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "invalid"
    return metadata.status.value


def _artifact_body(path: Path) -> str:
    try:
        return parse_markdown_artifact(path.read_text(encoding="utf-8"))[1]
    except (OSError, ValueError):
        return ""


def _events(workspace: Workspace) -> list[dict[str, object]]:
    event_path = workspace.root / "runs" / "events.jsonl"
    if not event_path.is_file():
        return []
    events: list[dict[str, object]] = []
    for line in event_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _has_event(events: list[dict[str, object]], event_type: str) -> bool:
    return any(event.get("event_type") == event_type for event in events)


def _expected_chapters_from_submission(submission: object) -> list[int]:
    chapters = getattr(submission, "chapters", [])
    return [chapter.chapter_number for chapter in chapters]


def _declared_claims(workspace: Workspace, chapter_numbers: list[int]) -> list[str]:
    claims: set[str] = set()
    for number in chapter_numbers:
        contract = workspace.root / "chapters" / f"chapter-{number:02d}-contract.md"
        section = _CLAIM_SECTION.search(_artifact_body(contract))
        if section is not None:
            claims.update(match.casefold() for match in _CLAIM_ID.findall(section.group("body")))
    return sorted(claims)


def _covered_claims(workspace: Workspace) -> tuple[set[str], bool]:
    ledger = workspace.root / "research" / "source-ledger.jsonl"
    if not ledger.is_file():
        return set(), False
    claims: set[str] = set()
    try:
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                claims.update(item.casefold() for item in SourceLedgerEntry.model_validate_json(line).claim_ids)
    except (OSError, ValueError):
        return set(), True
    return claims, False


def _status(
    *,
    stage: str,
    blocked_by: list[str],
    recommended_skill: str,
    next_command: str,
    artifacts: dict[str, str],
    declared_claim_ids: list[str],
    events: list[dict[str, object]],
    next_actions: list[WorkflowAction] | None = None,
) -> WorkflowStatus:
    return WorkflowStatus(
        stage=stage,
        blocked_by=blocked_by,
        recommended_skill=recommended_skill,
        next_command=next_command,
        artifacts=artifacts,
        declared_claim_ids=declared_claim_ids,
        recent_events=[str(event.get("event_type")) for event in events[-5:]],
        next_actions=next_actions or [],
    )


_DEFINITION_STAGES = frozenset({"brief_missing", "brief_invalid", "brief_awaiting_approval"})
_ARCHITECTURE_STAGES = frozenset(
    {"blueprint_missing", "blueprint_invalid", "blueprint_awaiting_approval", "chapter_contracts_incomplete"}
)
_ASSURANCE_STAGES = frozenset(
    {
        "quality_validation_pending",
        "quality_report_invalid",
        "quality_report_stale",
        "quality_blocked",
        "reviews_missing",
        "reviews_pending",
        "reviews_stale",
        "reviews_failed",
        "review_batch_pending",
        "review_dispatch_unavailable",
        "review_dispatching",
        "review_tasks_pending",
        "review_tasks_blocked",
        "review_batch_superseded",
        "review_results_failed",
    }
)
_DELIVERY_STAGES = frozenset(
    {
        "assembly_pending",
        "pdf_environment_unavailable",
        "pdf_render_pending",
        "delivery_validation_pending",
        "delivery_quality_report_invalid",
        "delivery_quality_report_stale",
        "delivery_quality_blocked",
        "delivery_manifest_stale",
        "publish_pending",
        "completed",
    }
)


def _macro_phase_for_stage(stage: str) -> MacroPhase:
    if stage in _DEFINITION_STAGES or stage == "independent_review_capability_pending":
        return MacroPhase.DEFINITION
    if stage in _ARCHITECTURE_STAGES:
        return MacroPhase.ARCHITECTURE
    if stage in _ASSURANCE_STAGES:
        return MacroPhase.ASSURANCE
    if stage in _DELIVERY_STAGES:
        return MacroPhase.DELIVERY
    return MacroPhase.PRODUCTION


def _user_summary_for_stage(stage: str, blocked_by: list[str]) -> str:
    summaries = {
        "brief_missing": "先完成产品定义，随后由用户确认 Brief。",
        "brief_awaiting_approval": "产品定位已整理完成，等待用户确认或提出修订。",
        "blueprint_missing": "Brief 已确认，下一步是生成主题化课程蓝图。",
        "blueprint_awaiting_approval": "课程蓝图已生成，等待用户确认学习路径与章节比重。",
        "sample_draft_missing": "课程结构已冻结，下一步只生产第一章样章。",
        "sample_awaiting_approval": "样章已通过确定性检查，等待用户确认写作方向。",
        "quality_validation_pending": "正文已完成，正在进入全书质量闭环。",
        "assembly_pending": "质量门已通过，正在受控汇编读者交付物。",
        "pdf_render_pending": "Markdown 已汇编，正在生成 PDF 交付物。",
        "completed": "知识产品已完成并生成交付清单。",
        "independent_review_capability_pending": "本书要求独立审稿，必须先确认当前宿主能创建可验证的真实审稿子任务。",
        "research_impact_decision_pending": "研究已产生输入，需要先确认它是否影响课程范围或学习路径。",
    }
    return summaries.get(stage, blocked_by[0] if blocked_by else "工作流正在等待下一项受控动作。")


def _human_decision_for_stage(stage: str) -> str | None:
    return {
        "brief_awaiting_approval": "请确认产品定位，或直接说明修改要求。",
        "blueprint_awaiting_approval": "请确认课程结构，或直接说明修改要求。",
        "sample_awaiting_approval": "请确认是否按该样章方向继续生成全书，或直接说明修改要求。",
        "independent_review_capability_pending": "选择可验证独立审稿宿主，或将审稿策略改为 deterministic_only",
        "research_impact_decision_pending": "确认研究影响的恢复路径",
    }.get(stage)


def _user_decision_target_for_stage(stage: str) -> UserDecisionTarget | None:
    return {
        "brief_awaiting_approval": UserDecisionTarget.BRIEF,
        "blueprint_awaiting_approval": UserDecisionTarget.BLUEPRINT,
        "sample_awaiting_approval": UserDecisionTarget.SAMPLE,
    }.get(stage)


def _fallback_next_actions(workspace: Workspace, status: WorkflowStatus) -> list[WorkflowAction]:
    """Provide a command-shaped action when legacy stages only contain prose."""
    root = str(workspace.root)
    stage_commands: dict[str, tuple[str, list[str], str]] = {
        "brief_missing": ("brief", ["kp", "brief", "<主题>", "--workspace", root], "BRIEF_REQUIRED"),
        "brief_awaiting_approval": (
            "approve",
            ["kp", "approve", "planning/product-brief.md", "--workspace", root],
            "BRIEF_APPROVAL_REQUIRED",
        ),
        "blueprint_missing": ("outline", ["kp", "outline", "--workspace", root], "BLUEPRINT_REQUIRED"),
        "blueprint_awaiting_approval": (
            "approve",
            ["kp", "approve", "planning/curriculum-blueprint.md", "--workspace", root],
            "BLUEPRINT_APPROVAL_REQUIRED",
        ),
        "chapter_contracts_incomplete": (
            "compile-contracts",
            ["kp", "compile-contracts", "--all", "--workspace", root],
            "CHAPTER_CONTRACTS_REQUIRED",
        ),
        "sample_draft_missing": ("draft", ["kp", "draft", "1", "--workspace", root], "SAMPLE_DRAFT_REQUIRED"),
        "sample_compliance_pending": (
            "submit-chapter",
            ["kp", "submit-chapter", "1", "--workspace", root],
            "SAMPLE_COMPLIANCE_REQUIRED",
        ),
        "sample_awaiting_approval": (
            "approve-sample",
            ["kp", "approve-sample", "--workspace", root],
            "SAMPLE_APPROVAL_REQUIRED",
        ),
        "chapter_drafts_incomplete": (
            "draft",
            ["kp", "draft", "<章节号>", "--workspace", root],
            "CHAPTER_DRAFT_REQUIRED",
        ),
        "chapter_compliance_pending": (
            "submit-chapter",
            ["kp", "submit-chapter", "<章节号>", "--workspace", root],
            "CHAPTER_COMPLIANCE_REQUIRED",
        ),
        "research_required": (
            "research",
            ["kp", "research", "<来源ID>", "<URL>", "--workspace", root],
            "RESEARCH_EVIDENCE_REQUIRED",
        ),
        "assumptions_require_resolution": (
            "assumption",
            ["kp", "assumption", "<假设ID>", "--workspace", root],
            "ASSUMPTION_RESOLUTION_REQUIRED",
        ),
        "quality_validation_pending": ("validate", ["kp", "validate", "--workspace", root], "QUALITY_REQUIRED"),
        "quality_report_invalid": ("validate", ["kp", "validate", "--workspace", root], "QUALITY_REPORT_INVALID"),
        "quality_report_stale": ("validate", ["kp", "validate", "--workspace", root], "QUALITY_REPORT_STALE"),
        "quality_blocked": ("validate", ["kp", "validate", "--workspace", root], "QUALITY_BLOCKED"),
        "assembly_pending": ("assemble", ["kp", "assemble", "--workspace", root], "ASSEMBLY_REQUIRED"),
        "pdf_render_pending": (
            "render-pdf",
            ["kp", "render-pdf", "--workspace", root],
            "PDF_REQUIRED_AND_MISSING",
        ),
        "delivery_validation_pending": (
            "validate-delivery",
            ["kp", "validate-delivery", "--workspace", root],
            "DELIVERY_VALIDATION_REQUIRED",
        ),
        "delivery_quality_report_invalid": (
            "validate-delivery",
            ["kp", "validate-delivery", "--workspace", root],
            "DELIVERY_QUALITY_REPORT_INVALID",
        ),
        "delivery_quality_report_stale": (
            "validate-delivery",
            ["kp", "validate-delivery", "--workspace", root],
            "DELIVERY_QUALITY_REPORT_STALE",
        ),
        "delivery_quality_blocked": (
            "validate-delivery",
            ["kp", "validate-delivery", "--workspace", root],
            "DELIVERY_QUALITY_BLOCKED",
        ),
        "publish_pending": ("publish", ["kp", "publish", "--workspace", root], "PUBLISH_REQUIRED"),
    }
    entry = stage_commands.get(status.stage)
    if entry is None:
        return []
    command, argv, reason_code = entry
    return [WorkflowAction(command=command, argv=argv, reason_code=reason_code)]


def _orchestration_root(workspace: Workspace) -> Path:
    return workspace.root / "runs" / "orchestration"


def _orchestration_state_path(workspace: Workspace) -> Path:
    return _orchestration_root(workspace) / "state.json"


def _orchestration_work_order_root(workspace: Workspace) -> Path:
    return _orchestration_root(workspace) / "work-orders"


def _orchestration_lease_root(workspace: Workspace) -> Path:
    return _orchestration_root(workspace) / "leases"


def _run_intent_path(workspace: Workspace) -> Path:
    return _orchestration_root(workspace) / "run-intent.json"


def _atomic_json_write(path: Path, payload: BaseModel | dict[str, object]) -> None:
    """Commit a controller record atomically; callers never expose half JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload.model_dump_json(indent=2) if isinstance(payload, BaseModel) else json.dumps(payload, ensure_ascii=False, indent=2)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_orchestration_state(workspace: Workspace) -> OrchestrationState | None:
    try:
        return OrchestrationState.model_validate_json(_orchestration_state_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_run_intent(workspace: Workspace) -> RunIntent | None:
    """Read the latest persisted controller intent without changing it."""
    try:
        return RunIntent.model_validate_json(_run_intent_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def start_run_intent(workspace: Workspace, *, auto_to_delivery: bool = True) -> RunIntent:
    """Persist a user-approved request to keep routing safe internal work."""
    ensure_orchestration(workspace)
    now = datetime.now(UTC)
    current = read_run_intent(workspace)
    if current is not None and current.state is RunIntentState.ACTIVE and current.auto_to_delivery == auto_to_delivery:
        return current
    intent = RunIntent(
        intent_id=f"run-{uuid.uuid4().hex[:16]}",
        requested_at=now,
        updated_at=now,
        auto_to_delivery=auto_to_delivery,
    )
    _atomic_json_write(_run_intent_path(workspace), intent)
    workspace.append_event("run_intent_started", {"intent_id": intent.intent_id, "auto_to_delivery": auto_to_delivery})
    return intent


def pause_run_intent(workspace: Workspace, reason: str = "用户暂停") -> RunIntent | None:
    """Stop automatic routing while preserving enough state for a later resume."""
    current = read_run_intent(workspace)
    if current is None:
        return None
    paused = current.model_copy(
        update={
            "state": RunIntentState.PAUSED,
            "updated_at": datetime.now(UTC),
            "stop_reason": reason.strip() or "用户暂停",
        }
    )
    _atomic_json_write(_run_intent_path(workspace), paused)
    workspace.append_event("run_intent_paused", {"intent_id": paused.intent_id, "reason": paused.stop_reason})
    return paused


def ensure_orchestration(workspace: Workspace) -> OrchestrationState:
    """Opt a workspace into v1.1 without rewriting legacy book artifacts."""
    state = _read_orchestration_state(workspace)
    if state is not None:
        return state
    state = OrchestrationState(initialized_at=datetime.now(UTC), migrated_from_legacy=True)
    _atomic_json_write(_orchestration_state_path(workspace), state)
    imported = import_legacy_approval_records(workspace)
    workspace.append_event("orchestration_initialized", {"schema_version": state.schema_version})
    if imported:
        workspace.append_event(
            "orchestration_legacy_approvals_imported",
            {"count": len(imported)},
        )
    return state


def initialize_new_orchestration(workspace: Workspace) -> OrchestrationState:
    """Create the managed controller records required by a freshly initialized book.

    This is intentionally distinct from :func:`ensure_orchestration`: the latter is
    an explicit, non-destructive adoption path for historic workspaces, whereas a
    new ``kp init`` run must never be mistaken for legacy content.
    """
    state = _read_orchestration_state(workspace)
    if state is None:
        state = OrchestrationState(initialized_at=datetime.now(UTC), migrated_from_legacy=False)
        _atomic_json_write(_orchestration_state_path(workspace), state)
        workspace.append_event(
            "orchestration_initialized",
            {"schema_version": state.schema_version, "origin": "kp_init"},
        )
    # Make the first handoff durable immediately.  It is idempotent, so reopening
    # after an interrupted initialization never creates duplicate live work orders.
    create_orchestration_work_order(workspace)
    return state


def orchestration_migration_preview(workspace: Workspace) -> dict[str, object]:
    """Return a read-only explanation before a non-empty 1.0.x workspace is adopted."""
    status = _inspect_workflow(workspace)
    existing = sorted(path for path, state in status.artifacts.items() if state != "missing")
    return {
        "orchestration_mode": "managed" if _read_orchestration_state(workspace) is not None else "legacy",
        "requires_confirmation": bool(existing) and _read_orchestration_state(workspace) is None,
        "existing_artifacts": existing,
        "preserved": ["planning/", "research/", "chapters/", "delivery/", "runs/events.jsonl"],
        "created_on_adoption": [
            "runs/orchestration/state.json",
            "runs/orchestration/work-orders/",
            "runs/interaction/（仅导入已有审批事实时）",
        ],
        "message": "迁移只新增编排控制记录，不重写正文、审批、质量报告或交付物。",
    }


def _hash_bytes(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _input_manifest(workspace: Workspace, status: WorkflowStatus) -> list[InputManifestEntry]:
    """Freeze actual artifact bytes, including missing declared inputs, but never logs."""
    return [
        InputManifestEntry(path=path, state=state, sha256=_hash_bytes(workspace.root / path))
        for path, state in sorted(status.artifacts.items())
    ]


def _manifest_sha256(entries: list[InputManifestEntry]) -> str:
    payload = [entry.model_dump(mode="json") for entry in entries]
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _target_chapter_from_status(status: WorkflowStatus) -> int | None:
    if status.stage.startswith("sample_"):
        return 1
    missing = sorted(
        path
        for path, state in status.artifacts.items()
        if state == "missing" and re.fullmatch(r"chapters/chapter-\d{2}\.md", path)
    )
    if missing:
        return int(missing[0].split("-")[1].split(".")[0])
    return None


def _read_all_orchestration_work_orders(workspace: Workspace) -> list[tuple[Path, OrchestrationWorkOrder]]:
    root = _orchestration_work_order_root(workspace)
    if not root.is_dir():
        return []
    orders: list[tuple[Path, OrchestrationWorkOrder]] = []
    for path in root.glob("*.json"):
        try:
            orders.append((path, OrchestrationWorkOrder.model_validate_json(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return sorted(orders, key=lambda pair: pair[1].created_at)


def _is_live(order: OrchestrationWorkOrder, now: datetime | None = None) -> bool:
    return order.lifecycle in {WorkOrderLifecycle.PENDING, WorkOrderLifecycle.ACTIVE} and order.expires_at > (now or datetime.now(UTC))


def _current_orchestration_work_order_path(
    workspace: Workspace, status: WorkflowStatus
) -> Path | None:
    if _read_orchestration_state(workspace) is None:
        return None
    entries = _input_manifest(workspace, status)
    manifest_sha = _manifest_sha256(entries)
    target = _target_chapter_from_status(status)
    key_payload = {
        "macro_phase": _macro_phase_for_stage(status.stage).value,
        "stage": status.stage,
        "skill": status.recommended_skill,
        "target_chapter_number": target,
        "input_manifest_sha256": manifest_sha,
    }
    key = hashlib.sha256(
        json.dumps(key_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    for path, order in reversed(_read_all_orchestration_work_orders(workspace)):
        if order.idempotency_key == key and _is_live(order):
            return path
    return None


def _latest_research_impact(workspace: Workspace) -> ResearchImpactDecision | None:
    root = _orchestration_root(workspace) / "research-impacts"
    decisions: list[ResearchImpactDecision] = []
    if root.is_dir():
        for path in root.glob("*.json"):
            try:
                decisions.append(ResearchImpactDecision.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return max(decisions, key=lambda item: item.recorded_at) if decisions else None


def _capability_snapshot(workspace: Workspace) -> HostCapabilitySnapshot | None:
    path = _orchestration_root(workspace) / "capability-snapshot.json"
    try:
        return HostCapabilitySnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _brief_capability_binding(workspace: Workspace) -> tuple[str, int] | None:
    """Return the reader-content binding protected by a capability preflight."""
    brief = workspace.root / "planning" / "product-brief.md"
    if not brief.is_file():
        return None
    try:
        metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
        return brief_contract_fingerprints(body)["content_contract_sha256"], metadata.version
    except (OSError, ValueError):
        return None


def _independent_review_capability_is_current(workspace: Workspace) -> bool:
    """Accept only a production-verified snapshot bound to the active Brief."""
    brief = workspace.root / "planning" / "product-brief.md"
    constraints = parse_detail_constraints(_artifact_body(brief))
    if constraints is None or constraints.review_policy.value != "independent_review_required":
        return True
    snapshot = _capability_snapshot(workspace)
    binding = _brief_capability_binding(workspace)
    return bool(
        snapshot is not None
        and snapshot.decision == "proceed"
        and snapshot.review_policy == "independent_review_required"
        and snapshot.capabilities.supports_real_subtasks
        and snapshot.capabilities.production_verified
        and binding is not None
        and snapshot.brief_content_sha256 == binding[0]
        and snapshot.brief_version == binding[1]
    )


def authorize_workflow_transition(workspace: Workspace, action: str) -> None:
    """Enforce global policy before any state-changing workflow operation."""
    if not action.strip():
        raise ValueError("受控工作流动作不能为空")
    if not _independent_review_capability_is_current(workspace):
        raise HostCapabilityPreflightError(
            "当前环境无法提供可验证的独立审稿。本书可改用确定性质量检查，或在支持独立审稿的环境继续。"
        )


def _enrich_orchestration_status(workspace: Workspace, status: WorkflowStatus) -> WorkflowStatus:
    """Add v1.1 fields without mutating a read-only status call."""
    managed = _read_orchestration_state(workspace) is not None
    adjusted = status
    brief = workspace.root / "planning" / "product-brief.md"
    if managed and status.stage == "brief_awaiting_approval":
        constraints = parse_detail_constraints(_artifact_body(brief))
        independent_required = constraints is not None and constraints.review_policy.value == "independent_review_required"
        if independent_required and not _independent_review_capability_is_current(workspace):
            adjusted = status.model_copy(
                update={
                    "stage": "independent_review_capability_pending",
                    "blocked_by": ["独立审稿要求缺少可验证的真实子任务能力快照。"],
                    "recommended_skill": "kp-create",
                    "next_command": "先完成独立审稿能力预检，再请求 Brief 审批。",
                }
            )
    if managed and _macro_phase_for_stage(adjusted.stage) in {MacroPhase.ASSURANCE, MacroPhase.DELIVERY}:
        constraints = parse_detail_constraints(_artifact_body(brief))
        independent_required = constraints is not None and constraints.review_policy.value == "independent_review_required"
        if independent_required and not _independent_review_capability_is_current(workspace):
            adjusted = adjusted.model_copy(
                update={
                    "stage": "independent_review_capability_pending",
                    "blocked_by": ["独立审稿能力快照缺失、已失效或未通过生产验证。"],
                    "recommended_skill": "kp-create",
                    "next_command": "先恢复可验证的独立审稿宿主能力，再继续质量或交付阶段。",
                }
            )
    research_ledger = workspace.root / "research" / "source-ledger.jsonl"
    production_before_sample = {
        "sample_draft_missing",
        "sample_compliance_pending",
        "sample_awaiting_approval",
        "chapter_drafts_incomplete",
    }
    if (
        managed
        and adjusted.stage in production_before_sample
        and research_ledger.is_file()
        and research_ledger.read_text(encoding="utf-8").strip()
        and _latest_research_impact(workspace) is None
    ):
        adjusted = adjusted.model_copy(
            update={
                "stage": "research_impact_decision_pending",
                "blocked_by": ["来源核验已完成，但尚未记录研究结果对课程范围的影响。"],
                "recommended_skill": "kp-research-verifier",
                "next_command": "由 kp-research-verifier 记录 research impact 后再继续正文生产。",
            }
        )
    actions = adjusted.next_actions or _fallback_next_actions(workspace, adjusted)
    work_order = _current_orchestration_work_order_path(workspace, adjusted) if managed else None
    decision = None
    target = _user_decision_target_for_stage(adjusted.stage)
    if target is not None:
        try:
            decision = user_decision_requirement(workspace, target)
        except (FileNotFoundError, ValueError):
            decision = None
    summary = _user_summary_for_stage(adjusted.stage, adjusted.blocked_by)
    if decision is not None and decision.state == "stale":
        summary = "内容已更新，请确认最新版本。"
    return adjusted.model_copy(
        update={
            "macro_phase": _macro_phase_for_stage(adjusted.stage),
            "user_summary": summary,
            "required_human_decision": _human_decision_for_stage(adjusted.stage),
            "user_decision": decision,
            "current_work_order": _relative(workspace, work_order) if work_order is not None else None,
            "orchestration_mode": "managed" if managed else "legacy",
            "run_intent_state": read_run_intent(workspace).state.value if read_run_intent(workspace) else None,
            "auto_to_delivery": bool(
                (intent := read_run_intent(workspace)) is not None
                and intent.state is RunIntentState.ACTIVE
                and intent.auto_to_delivery
            ),
            "next_actions": actions,
        }
    )


def _inspect_workflow(workspace: Workspace) -> WorkflowStatus:
    """Return the next permitted stage without mutating the workspace."""
    root = workspace.root
    brief = root / "planning" / "product-brief.md"
    blueprint = root / "planning" / "curriculum-blueprint.md"
    artifacts = {
        "planning/product-brief.md": _artifact_state(brief),
        "planning/curriculum-blueprint.md": _artifact_state(blueprint),
        "research/source-ledger.jsonl": "present"
        if (root / "research" / "source-ledger.jsonl").is_file()
        else "missing",
        "research/assumption-ledger.jsonl": "present"
        if (root / "research" / "assumption-ledger.jsonl").is_file()
        else "missing",
        "delivery/book.md": "present" if (root / "delivery" / "book.md").is_file() else "missing",
        "delivery/book.qmd": "present" if (root / "delivery" / "book.qmd").is_file() else "missing",
        "delivery/book.pdf": "present" if (root / "delivery" / "book.pdf").is_file() else "missing",
        "delivery/delivery-manifest.yaml": "present"
        if (root / "delivery" / "delivery-manifest.yaml").is_file()
        else "missing",
    }
    events = _events(workspace)
    workspace_arg = str(root)

    if artifacts["planning/product-brief.md"] == "missing":
        return _status(
            stage="brief_missing",
            blocked_by=["缺少产品 Brief：planning/product-brief.md"],
            recommended_skill="kp-product-strategist",
            next_command=f'kp brief "<主题>" --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=[],
            events=events,
        )
    if artifacts["planning/product-brief.md"] == "invalid":
        return _status(
            stage="brief_invalid",
            blocked_by=["planning/product-brief.md 的 frontmatter 无效"],
            recommended_skill="kp-create",
            next_command="修复产品 Brief 的 YAML frontmatter 后重新运行 kp status",
            artifacts=artifacts,
            declared_claim_ids=[],
            events=events,
        )
    if artifacts["planning/product-brief.md"] != ArtifactStatus.APPROVED.value:
        return _status(
            stage="brief_awaiting_approval",
            blocked_by=["产品 Brief 尚未获得用户审批"],
            recommended_skill="kp-create",
            next_command=f'kp approve "{brief}" --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=[],
            events=events,
        )
    constraints = parse_detail_constraints(_artifact_body(brief))
    requires_pdf = constraints.requires_pdf if constraints is not None else True
    if artifacts["planning/curriculum-blueprint.md"] == "missing":
        recommendation_ready = (workspace.root / "planning" / "teaching-length-recommendation.yaml").is_file()
        if recommendation_ready:
            blocked_by = ["教学篇幅评估已提交，等待主题化课程蓝图"]
        else:
            blocked_by = ["缺少课程蓝图：planning/curriculum-blueprint.md"]
        return _status(
            stage="blueprint_missing",
            blocked_by=blocked_by,
            recommended_skill="kp-curriculum-architect",
            next_command=(
                f'kp outline --workspace "{workspace_arg}"，由 kp-curriculum-architect 填写主题化 YAML 后，'
                f'运行 kp submit-outline --workspace "{workspace_arg}" --from <blueprint.yaml>'
            ),
            artifacts=artifacts,
            declared_claim_ids=[],
            events=events,
        )
    if artifacts["planning/curriculum-blueprint.md"] == "invalid":
        return _status(
            stage="blueprint_invalid",
            blocked_by=["planning/curriculum-blueprint.md 的 frontmatter 无效"],
            recommended_skill="kp-curriculum-architect",
            next_command="修复课程蓝图的 YAML frontmatter 后重新运行 kp status",
            artifacts=artifacts,
            declared_claim_ids=[],
            events=events,
        )
    if artifacts["planning/curriculum-blueprint.md"] != ArtifactStatus.APPROVED.value:
        return _status(
            stage="blueprint_awaiting_approval",
            blocked_by=["课程蓝图尚未获得用户审批"],
            recommended_skill="kp-curriculum-architect",
            next_command=f'kp approve "{blueprint}" --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=[],
            events=events,
        )

    try:
        _, _, submission = require_current_submitted_blueprint(workspace)
    except CurriculumPrerequisiteError as error:
        return _status(
            stage="blueprint_invalid",
            blocked_by=[str(error)],
            recommended_skill="kp-curriculum-architect",
            next_command=(
                f'由 kp-curriculum-architect 通过受控修订恢复当前 Brief 对应的主题化蓝图：'
                f'kp amend-outline --workspace "{workspace_arg}" --from <blueprint.yaml>'
            ),
            artifacts=artifacts,
            declared_claim_ids=[],
            events=events,
            next_actions=[
                WorkflowAction(
                    command="amend-outline",
                    argv=["kp", "amend-outline", "--workspace", workspace_arg, "--from", "<blueprint.yaml>"],
                    reason_code="BLUEPRINT_CONTENT_BINDING_STALE",
                )
            ],
        )
    chapter_numbers = _expected_chapters_from_submission(submission)
    contracts = [root / "chapters" / f"chapter-{number:02d}-contract.md" for number in chapter_numbers]
    missing_contracts: list[Path] = []
    for chapter_number, path in zip(chapter_numbers, contracts, strict=True):
        try:
            require_current_chapter_contract(workspace, chapter_number)
        except ValueError:
            missing_contracts.append(path)
    artifacts.update({_relative(workspace, path): _artifact_state(path) for path in contracts})
    if missing_contracts:
        missing_numbers = ", ".join(str(int(path.stem.split("-")[1])) for path in missing_contracts)
        return _status(
            stage="chapter_contracts_incomplete",
            blocked_by=[f"缺少章节合约：第 {missing_numbers} 章"],
            recommended_skill="kp-curriculum-architect",
            next_command=f'kp compile-contracts --all --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=[],
            events=events,
        )

    assumption_path = root / "research" / "assumption-ledger.jsonl"
    try:
        assumptions = latest_assumptions(assumption_path)
    except (OSError, ValueError):
        return _status(
            stage="assumption_ledger_invalid",
            blocked_by=["research/assumption-ledger.jsonl 格式无效"],
            recommended_skill="kp-research-verifier",
            next_command="修复假设台账 JSONL 后重新运行 kp status",
            artifacts=artifacts,
            declared_claim_ids=[],
            events=events,
        )
    unresolved_assumptions = [
        entry.assumption_id
        for entry in assumptions.values()
        if entry.disposition is AssumptionDisposition.EVIDENCE_NEEDED
    ]
    if unresolved_assumptions:
        return _status(
            stage="assumptions_require_resolution",
            blocked_by=[f"以下产品假设尚未证实或降级：{', '.join(unresolved_assumptions)}"],
            recommended_skill="kp-research-verifier",
            next_command=(
                f'kp assumption <assumption-id> --workspace "{workspace_arg}" '
                "--statement <陈述> --impact <影响范围> --disposition <verified|reframed_as_case> "
                "--rationale <理由>"
            ),
            artifacts=artifacts,
            declared_claim_ids=[],
            events=events,
        )
    try:
        claim_bindings = current_claim_contract_bindings(workspace)
    except ValueError as error:
        return _status(
            stage="chapter_contracts_incomplete",
            blocked_by=[str(error)],
            recommended_skill="kp-curriculum-architect",
            next_command=f'kp compile-contracts --all --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=[],
            events=events,
        )
    declared_claim_ids = sorted(
        set(claim_bindings) | required_assumption_claim_ids(assumption_path)
    )
    _covered_claim_ids, ledger_invalid = _covered_claims(workspace)
    if ledger_invalid:
        return _status(
            stage="research_ledger_invalid",
            blocked_by=["research/source-ledger.jsonl 格式无效"],
            recommended_skill="kp-research-verifier",
            next_command="修复来源台账 JSONL 后重新运行 kp status",
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    source_findings = validate_claims(
        set(claim_bindings), root / "research" / "source-ledger.jsonl", claim_bindings
    )
    source_findings.extend(
        validate_claims(required_assumption_claim_ids(assumption_path), root / "research" / "source-ledger.jsonl")
    )
    if source_findings:
        return _status(
            stage="research_required",
            blocked_by=[source_findings[0].message],
            recommended_skill="kp-research-verifier",
            next_command=(
                f'kp research <source-id> <url> --workspace "{workspace_arg}" '
                "--claim <claim-id> --excerpt <摘录> --license-note <许可说明>"
            ),
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )

    sample = root / "chapters" / "chapter-01.md"
    sample_approved = root / "chapters" / "chapter-01-sample.approved"
    if not sample.is_file():
        return _status(
            stage="sample_draft_missing",
            blocked_by=["缺少第一章样章"],
            recommended_skill="kp-chapter-writer",
            next_command=f'kp draft 1 --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    if not sample_approved.is_file():
        sample_findings = validate_current_chapter_compliance(workspace, sample)
        if sample_findings:
            return _status(
                stage="sample_compliance_pending",
                blocked_by=[sample_findings[0].message],
                recommended_skill="kp-chapter-writer",
                next_command=(
                    f'kp submit-chapter 1 --workspace "{workspace_arg}" --from <chapter.md> '
                    "--compliance <chapter-compliance.json>"
                ),
                artifacts=artifacts,
                declared_claim_ids=declared_claim_ids,
                events=events,
            )
        return _status(
            stage="sample_awaiting_approval",
            blocked_by=["第一章样章尚未获得用户审批"],
            recommended_skill="kp-chapter-writer",
            next_command=f'kp approve-sample --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )

    drafts = [root / "chapters" / f"chapter-{number:02d}.md" for number in chapter_numbers]
    missing_drafts = [path for path in drafts if not path.is_file()]
    artifacts.update({_relative(workspace, path): _artifact_state(path) for path in drafts})
    if missing_drafts:
        missing_numbers = ", ".join(str(int(path.stem.split("-")[1])) for path in missing_drafts)
        return _status(
            stage="chapter_drafts_incomplete",
            blocked_by=[f"缺少章节正文：第 {missing_numbers} 章"],
            recommended_skill="kp-chapter-writer",
            next_command=f'kp draft <章节号> --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )

    compliance_findings = [
        finding
        for draft in drafts
        for finding in validate_current_chapter_compliance(workspace, draft)
    ]
    if compliance_findings:
        return _status(
            stage="chapter_compliance_pending",
            blocked_by=[compliance_findings[0].message],
            recommended_skill="kp-chapter-writer",
            next_command=(
                f'kp submit-chapter <章节号> --workspace "{workspace_arg}" --from <chapter.md> '
                "--compliance <chapter-compliance.json>"
            ),
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )

    report_path = root / "delivery" / "qa-report.json"
    if not report_path.is_file() or not _has_event(events, "validation_completed"):
        return _status(
            stage="quality_validation_pending",
            blocked_by=["尚未完成全书确定性质量校验"],
            recommended_skill="kp-quality-editor",
            next_command=f'kp validate --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    try:
        quality_report = QualityReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _status(
            stage="quality_report_invalid",
            blocked_by=["delivery/qa-report.json 格式无效"],
            recommended_skill="kp-quality-editor",
            next_command=f'kp validate --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    upstream_report_validity = quality_report_upstream_validity(workspace, quality_report)
    if upstream_report_validity is QualityReportValidity.INVALID:
        return _status(
            stage="quality_report_invalid",
            blocked_by=["delivery/qa-report.json 缺少当前版本质量报告信封"],
            recommended_skill="kp-quality-editor",
            next_command=f'kp validate --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    if upstream_report_validity is QualityReportValidity.STALE:
        return _status(
            stage="quality_report_stale",
            blocked_by=["全书质量报告绑定的输入版本已变化"],
            recommended_skill="kp-quality-editor",
            next_command=f'kp validate --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    delivery_findings = [
        finding for finding in quality_report.findings if not finding.code.startswith("REVIEW_")
    ]
    if (
        quality_report.validation_stage == "quality"
        and QualityReport(findings=delivery_findings).publishable is False
    ):
        return _status(
            stage="quality_blocked",
            blocked_by=["质量报告仍包含 blocker finding"],
            recommended_skill="kp-quality-editor",
            next_command=f'kp validate --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    if artifacts["delivery/book.md"] != "missing" and artifacts["delivery/book.qmd"] != "missing":
        from knowledge_product_workflow.publishing import release_readiness_findings

        reassembly_findings = [
            finding
            for finding in release_readiness_findings(workspace)
            if finding.code
            in {
                "RELEASE_BOOK_INPUTS_MISSING",
                "RELEASE_BOOK_STALE",
                "RELEASE_BOOK_INPUT_MANIFEST_MISSING",
                "RELEASE_BOOK_INPUT_MANIFEST_INVALID",
                "RELEASE_INPUT_HASH_MISMATCH",
            }
        ]
        if reassembly_findings:
            return _status(
                stage="assembly_pending",
                blocked_by=[
                    "交付派生输入已过期："
                    + reassembly_findings[0].message
                    + "；CLI 将在重新汇编时受控重建清单。"
                ],
                recommended_skill="kp-publisher",
                next_command=f'kp assemble --workspace "{workspace_arg}"',
                artifacts=artifacts,
                declared_claim_ids=declared_claim_ids,
                events=events,
                next_actions=[
                    WorkflowAction(
                        command="assemble",
                        argv=["kp", "assemble", "--workspace", workspace_arg],
                        reason_code="RELEASE_INPUTS_STALE_REASSEMBLY_REQUIRED",
                    )
                ],
            )
    if artifacts["delivery/book.md"] == "missing" or artifacts["delivery/book.qmd"] == "missing":
        return _status(
            stage="assembly_pending",
            blocked_by=["尚未生成受控 Markdown 与 Quarto 源稿"],
            recommended_skill="kp-publisher",
            next_command=f'kp assemble --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    if requires_pdf and artifacts["delivery/book.pdf"] == "missing":
        quarto = resolve_quarto()
        if not quarto.available:
            return _status(
                stage="pdf_environment_unavailable",
                blocked_by=["未检测到可运行的 Quarto；请运行 kp doctor 查看已检查的位置。"],
                recommended_skill="kp-publisher",
                next_command="安装或配置 Quarto 后运行 kp doctor --json，再重新执行 kp render-pdf。",
                artifacts=artifacts,
                declared_claim_ids=declared_claim_ids,
                events=events,
            )
        return _status(
            stage="pdf_render_pending",
            blocked_by=["尚未生成受控 PDF 交付物"],
            recommended_skill="kp-publisher",
            next_command=f'kp render-pdf --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
            next_actions=[
                WorkflowAction(
                    command="render-pdf",
                    argv=["kp", "render-pdf", "--workspace", workspace_arg],
                    reason_code="PDF_REQUIRED_AND_MISSING",
                )
            ],
        )
    if not _has_event(events, "delivery_validation_completed"):
        return _status(
            stage="delivery_validation_pending",
            blocked_by=["尚未完成最终交付质量校验"],
            recommended_skill="kp-quality-editor",
            next_command=f'kp validate-delivery --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    try:
        delivery_report = QualityReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _status(
            stage="delivery_quality_report_invalid",
            blocked_by=["最终交付质量报告格式无效"],
            recommended_skill="kp-quality-editor",
            next_command=f'kp validate-delivery --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    delivery_report_validity = quality_report_validity(workspace, delivery_report, "delivery")
    if delivery_report_validity is QualityReportValidity.INVALID:
        return _status(
            stage="delivery_quality_report_invalid",
            blocked_by=["delivery/qa-report.json 缺少当前版本交付质量报告信封"],
            recommended_skill="kp-quality-editor",
            next_command=f'kp validate-delivery --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    if delivery_report_validity is QualityReportValidity.STALE:
        return _status(
            stage="delivery_quality_report_stale",
            blocked_by=["交付质量报告绑定的输入版本已变化"],
            recommended_skill="kp-quality-editor",
            next_command=f'kp validate-delivery --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    if not delivery_report.publishable:
        return _status(
            stage="delivery_quality_blocked",
            blocked_by=["最终交付质量报告仍包含 blocker finding"],
            recommended_skill="kp-quality-editor",
            next_command=f'kp validate-delivery --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    if artifacts["delivery/delivery-manifest.yaml"] == "missing":
        return _status(
            stage="publish_pending",
            blocked_by=["尚未生成交付清单"],
            recommended_skill="kp-publisher",
            next_command=f'kp publish --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    from knowledge_product_workflow.publishing import delivery_manifest_issues

    manifest_issues = delivery_manifest_issues(workspace)
    if manifest_issues:
        return _status(
            stage="delivery_manifest_stale",
            blocked_by=[manifest_issues[0]],
            recommended_skill="kp-publisher",
            next_command=f'kp publish --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    return _status(
        stage="completed",
        blocked_by=[],
        recommended_skill="kp-publisher",
        next_command="无需执行命令；交付已完成。",
        artifacts=artifacts,
        declared_claim_ids=declared_claim_ids,
        events=events,
    )


def inspect_workflow(workspace: Workspace) -> WorkflowStatus:
    """Read legacy gates first, then project them into the opt-in v1.1 control plane."""
    return _enrich_orchestration_status(workspace, _inspect_workflow(workspace))


def route_workflow(workspace: Workspace) -> RouteDecision:
    """Return the one Skill allowed to handle the current persisted stage."""
    status = inspect_workflow(workspace)
    return RouteDecision(status=status, skill=status.recommended_skill)


_COMMAND_STAGES: dict[str, frozenset[str]] = {
    "validate": frozenset(
        {"quality_validation_pending", "quality_report_invalid", "quality_report_stale", "quality_blocked"}
    ),
    "assemble": frozenset({"assembly_pending"}),
    "render-pdf": frozenset({"pdf_render_pending"}),
    "validate-delivery": frozenset(
        {
            "delivery_validation_pending",
            "delivery_quality_report_invalid",
            "delivery_quality_report_stale",
            "delivery_quality_blocked",
        }
    ),
    "publish": frozenset({"publish_pending", "delivery_manifest_stale"}),
}


def _delivery_blocker_details(workspace: Workspace) -> str:
    """交付质量报告的 blocker 摘要；报告缺失或不可解析时给出通用说明。"""
    report_path = workspace.root / "delivery" / "qa-report.json"
    if not report_path.is_file():
        return "交付质量报告缺失"
    try:
        report = QualityReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "交付质量报告无法解析"
    blockers = [finding for finding in report.findings if finding.severity.value == "blocker"]
    if not blockers:
        return "交付质量报告不包含 blocker finding"
    parts = []
    for finding in blockers[:3]:
        message = " ".join(finding.message.strip().split())
        parts.append(f"{finding.code}：{message[:80]}")
    return "；".join(parts)


def authorize_command(workspace: Workspace, command: str) -> WorkflowStatus:
    """Authorize a state-changing delivery command against persisted workflow state."""
    if command not in _COMMAND_STAGES:
        raise ValueError(f"未知受控命令：{command}")
    authorize_workflow_transition(workspace, command)
    status = inspect_workflow(workspace)
    if status.stage not in _COMMAND_STAGES[command]:
        if command == "publish" and status.stage == "delivery_quality_blocked":
            raise WorkflowAuthorizationError(
                f"最终交付质量门未通过，不能发布：{_delivery_blocker_details(workspace)}。"
                "请修复内容性原因（调整 Brief 页数目标或扩展/精简正文）后重新运行 "
                "kp validate-delivery；重复运行 validate-delivery 不会改变内容性 blocker。"
            )
        raise WorkflowAuthorizationError(
            f"当前工作流阶段不允许运行 kp {command}；请先执行：{status.next_command}"
        )
    return status


_SKILL_SCOPES: dict[str, tuple[list[str], list[str], list[str]]] = {
    "kp-product-strategist": (["planning/"], ["planning/product-brief.md"], ["planning/product-brief.md"]),
    "kp-curriculum-architect": (
        ["planning/", "chapters/"],
        ["planning/curriculum-blueprint.md", "chapters/"],
        ["planning/curriculum-blueprint.md", "chapters/chapter-XX-contract.md"],
    ),
    "kp-research-verifier": (
        ["planning/", "research/", "chapters/"],
        ["research/"],
        ["research/source-ledger.jsonl"],
    ),
    "kp-chapter-writer": (
        ["planning/", "research/", "chapters/"],
        ["chapters/"],
        ["chapters/chapter-XX-contract.md", "chapters/chapter-XX.md"],
    ),
    "kp-chinese-humanize-editor": (
        ["planning/", "chapters/", "research/", "assets/"],
        ["runs/humanize/"],
        ["runs/humanize/chapter-XX.json"],
    ),
    "kp-diagram-designer": (
        ["planning/", "chapters/", "assets/"],
        ["assets/figures/", "runs/diagrams/"],
        [
            "assets/figures/<diagram-id>.pdf",
            "assets/figures/<diagram-id>.png",
            "runs/diagrams/<diagram-id>.json",
        ],
    ),
    "kp-quality-editor": (
        ["planning/", "research/", "chapters/", "delivery/"],
        ["runs/review-batches/", "delivery/qa-report.json"],
        ["runs/review-batches/review-batch-*/batch.json", "delivery/qa-report.json"],
    ),
    "kp-independent-reviewer": (
        ["runs/review-batches/review-batch-*/"],
        ["runs/review-batches/review-batch-*/reviews/"],
        ["runs/review-batches/review-batch-*/reviews/chapter-XX-review.json"],
    ),
    "kp-publisher": (
        ["planning/", "chapters/", "delivery/"],
        ["delivery/"],
        ["delivery/book.md", "delivery/book.qmd", "delivery/book.pdf", "delivery/delivery-manifest.yaml"],
    ),
    "kp-create": (["planning/", "chapters/", "delivery/", "runs/"], [], []),
}


_STAGE_ACTION_ALIASES: dict[str, set[str]] = {
    # The template-generation command is preparatory. Submitting the completed
    # topic-specific blueprint is what actually closes this architecture handoff.
    "blueprint_missing": {"submit-outline"},
}


def _orchestration_idempotency_key(
    status: WorkflowStatus, manifest_sha256: str, target_chapter_number: int | None
) -> str:
    payload = {
        "macro_phase": status.macro_phase.value,
        "stage": status.stage,
        "skill": status.recommended_skill,
        "target_chapter_number": target_chapter_number,
        "input_manifest_sha256": manifest_sha256,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _next_work_order_id(workspace: Workspace, idempotency_key: str) -> str:
    """Keep retries auditable without changing their logical idempotency key."""
    prefix = f"wo-{idempotency_key[:12]}"
    existing = {order.work_order_id for _, order in _read_all_orchestration_work_orders(workspace)}
    if prefix not in existing:
        return prefix
    attempt = 2
    while f"{prefix}-r{attempt}" in existing:
        attempt += 1
    return f"{prefix}-r{attempt}"


def create_orchestration_work_order(
    workspace: Workspace, *, ttl: timedelta = timedelta(minutes=30), adopt_legacy: bool = False
) -> Path:
    """Create or reuse the exact v1.1 handoff required by the current status.

    This is intentionally separate from :func:`create_work_order`, which remains
    the legacy manual-recovery record under ``runs/work-orders``.
    """
    preview = orchestration_migration_preview(workspace)
    if bool(preview["requires_confirmation"]) and not adopt_legacy:
        raise OrchestrationWorkOrderError(
            "这是尚未纳入 v1.1 编排的旧工作区；请先运行 kp orchestration-migration-preview 查看只读范围，"
            "经用户确认后以 --adopt-legacy 创建编排工单。"
        )
    ensure_orchestration(workspace)
    status = inspect_workflow(workspace)
    entries = _input_manifest(workspace, status)
    manifest_sha256 = _manifest_sha256(entries)
    target_chapter_number = _target_chapter_from_status(status)
    idempotency_key = _orchestration_idempotency_key(status, manifest_sha256, target_chapter_number)
    for path, existing in reversed(_read_all_orchestration_work_orders(workspace)):
        if existing.idempotency_key == idempotency_key and _is_live(existing):
            return path

    reads, writes, outputs = _SKILL_SCOPES[status.recommended_skill]
    actions = status.next_actions or _fallback_next_actions(workspace, status)
    now = datetime.now(UTC)
    order = OrchestrationWorkOrder(
        work_order_id=_next_work_order_id(workspace, idempotency_key),
        created_at=now,
        expires_at=now + ttl,
        macro_phase=status.macro_phase,
        stage=status.stage,
        skill=status.recommended_skill,
        target_chapter_number=target_chapter_number,
        inputs=entries,
        input_manifest_sha256=manifest_sha256,
        allowed_read_paths=reads,
        allowed_write_paths=writes,
        outputs=outputs,
        allowed_commands=sorted(
            {action.command for action in actions} | _STAGE_ACTION_ALIASES.get(status.stage, set())
        ),
        preconditions=[f"stage={status.stage}", *status.blocked_by],
        success_transition="重新读取 kp status --json 并由控制面选择下一动作",
        failure_transition="保留当前工件并按 kp status --json 的恢复路径处理",
        idempotency_key=idempotency_key,
    )
    if order.skill == "kp-chapter-writer" and target_chapter_number is not None:
        lease = acquire_chapter_lease(
            workspace,
            order.work_order_id,
            target_chapter_number,
            owner_skill=order.skill,
            ttl=ttl,
        )
        order = order.model_copy(update={"lifecycle": WorkOrderLifecycle.ACTIVE, "lease": lease})
    output = _orchestration_work_order_root(workspace) / f"{order.work_order_id}.json"
    _atomic_json_write(output, order)
    workspace.append_event(
        "orchestration_work_order_created",
        {
            "work_order_id": order.work_order_id,
            "stage": order.stage,
            "macro_phase": order.macro_phase.value,
            "skill": order.skill,
            "target_chapter_number": order.target_chapter_number,
            "path": _relative(workspace, output),
        },
    )
    return output


def read_orchestration_work_order(workspace: Workspace, path_or_id: Path | str) -> OrchestrationWorkOrder:
    """Read one v1.1 work order without accepting paths outside the workspace."""
    candidate = Path(path_or_id)
    if not candidate.is_absolute():
        candidate = (
            candidate
            if candidate.parent != Path(".")
            else _orchestration_work_order_root(workspace) / f"{candidate.name.removesuffix('.json')}.json"
        )
        if not candidate.is_absolute():
            candidate = workspace.root / candidate
    resolved = candidate.resolve()
    root = _orchestration_work_order_root(workspace).resolve()
    if not resolved.is_relative_to(root):
        raise OrchestrationWorkOrderError("编排工单必须位于当前工作区的 runs/orchestration/work-orders 中")
    try:
        return OrchestrationWorkOrder.model_validate_json(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"找不到编排工单：{path_or_id}") from None
    except ValueError as error:
        raise OrchestrationWorkOrderError("编排工单 JSON 无效") from error


def _work_order_path(workspace: Workspace, work_order_id: str) -> Path:
    return _orchestration_work_order_root(workspace) / f"{work_order_id}.json"


def _output_manifest(
    workspace: Workspace, output_paths: list[Path | str] | tuple[Path | str, ...]
) -> list[OutputManifestEntry]:
    """Hash only concrete files inside the workspace; never infer undeclared output."""
    entries: dict[str, OutputManifestEntry] = {}
    root = workspace.root.resolve()
    for value in output_paths:
        candidate = Path(value)
        resolved = candidate.resolve() if candidate.is_absolute() else (workspace.root / candidate).resolve()
        if not resolved.is_relative_to(root):
            raise OrchestrationWorkOrderError("工单完成输出必须位于当前工作区内")
        digest = _hash_bytes(resolved)
        if digest is None:
            continue
        relative = str(resolved.relative_to(root)).replace("\\", "/")
        entries[relative] = OutputManifestEntry(path=relative, sha256=digest)
    return [entries[path] for path in sorted(entries)]


def _live_order_for_action(workspace: Workspace, action: str) -> OrchestrationWorkOrder | None:
    """Return the newest unexpired controller handoff explicitly permitting an action."""
    for _, order in reversed(_read_all_orchestration_work_orders(workspace)):
        if _is_live(order) and action in order.allowed_commands:
            return order
    return None


def capture_orchestration_action(workspace: Workspace, action: str) -> str | None:
    """Freeze the one live handoff before a command writes stage-changing output.

    Commands commonly change the workflow stage as part of producing their output.
    Looking up a handoff afterwards can therefore miss the order that authorized
    the command.  The caller keeps this opaque identifier only for the matching
    completion call; it is never a user-facing recovery mechanism.
    """
    if _read_orchestration_state(workspace) is None:
        return None
    matches = [
        order
        for _, order in _read_all_orchestration_work_orders(workspace)
        if _is_live(order) and action in order.allowed_commands
    ]
    if not matches:
        # A controlled command can legitimately be the first v1.1 action after
        # an in-process compatibility fixture or a recovered legacy mutation.
        # Route the *current* persisted stage once, then freeze the resulting
        # handoff.  Do not ask callers to manufacture a work-order ID.
        create_orchestration_work_order(workspace)
        matches = [
            order
            for _, order in _read_all_orchestration_work_orders(workspace)
            if _is_live(order) and action in order.allowed_commands
        ]
    if not matches:
        raise OrchestrationWorkOrderError(f"当前托管工作区没有允许执行 kp {action} 的有效工单")
    if len(matches) > 1:
        raise OrchestrationWorkOrderError(f"当前托管工作区存在多个允许执行 kp {action} 的有效工单")
    order = matches[0]
    validate_orchestration_work_order(workspace, order.work_order_id, command=action)
    return order.work_order_id


def validate_orchestration_work_order(
    workspace: Workspace,
    work_order_id: str,
    *,
    command: str | None = None,
    chapter_number: int | None = None,
) -> OrchestrationWorkOrder:
    """Verify lifecycle, snapshot and scoped command authority before a Skill acts."""
    order = read_orchestration_work_order(workspace, work_order_id)
    if not _is_live(order):
        raise OrchestrationWorkOrderError("编排工单已过期、已完成或已取消；请重新运行 kp status --json 获取新的恢复动作")
    status = inspect_workflow(workspace)
    current_manifest = _manifest_sha256(_input_manifest(workspace, status))
    if current_manifest != order.input_manifest_sha256:
        raise WorkOrderInputChangedError("工单输入工件已变化；请重新路由并创建新的编排工单")
    if command is not None and command not in order.allowed_commands:
        raise OrchestrationWorkOrderError(f"工单不允许执行 kp {command}")
    if chapter_number is not None and order.target_chapter_number not in {None, chapter_number}:
        raise OrchestrationWorkOrderError("工单不拥有该章节；不得跨章节写入")
    return order


def complete_orchestration_work_order(
    workspace: Workspace,
    work_order_id: str,
    proof: str,
    *,
    action: str = "manual",
    output_paths: list[Path | str] | tuple[Path | str, ...] = (),
    allow_same_stage_output: bool = False,
) -> Path:
    """Close a current work order with controller-owned version evidence."""
    if not proof.strip():
        raise OrchestrationWorkOrderError("完成证明不能为空")
    order = read_orchestration_work_order(workspace, work_order_id)
    if order.lifecycle is WorkOrderLifecycle.COMPLETED:
        return _work_order_path(workspace, work_order_id)
    if not _is_live(order):
        raise OrchestrationWorkOrderError("编排工单已过期、已完成或已取消；请重新运行 kp status --json 获取新的恢复动作")
    status = inspect_workflow(workspace)
    current_manifest = _manifest_sha256(_input_manifest(workspace, status))
    outputs = _output_manifest(workspace, output_paths)
    if (
        current_manifest != order.input_manifest_sha256
        and status.stage == order.stage
        and not (allow_same_stage_output and outputs)
    ):
        raise WorkOrderInputChangedError("工单输入工件已变化；请重新路由并创建新的编排工单")
    event_id = f"event-{uuid.uuid4().hex}"
    completed = order.model_copy(
        update={
            "lifecycle": WorkOrderLifecycle.COMPLETED,
            "completion_proof": CompletionProof(
                completed_at=datetime.now(UTC),
                completed_by="kp-control-plane",
                transition=action,
                event_id=event_id,
                proof=proof.strip(),
                input_manifest_sha256=order.input_manifest_sha256,
                output_manifest=outputs,
            ),
        }
    )
    output = _work_order_path(workspace, work_order_id)
    _atomic_json_write(output, completed)
    if order.lease is not None:
        release_chapter_lease(workspace, order.lease.lease_id, work_order_id)
    workspace.append_event(
        "orchestration_work_order_completed",
        {
            "work_order_id": work_order_id,
            "transition": action,
            "proof": proof.strip(),
            "outputs": [entry.path for entry in outputs],
        },
        event_id=event_id,
    )
    _advance_run_intent_after_completion(workspace, order)
    return output


def complete_orchestration_action(
    workspace: Workspace,
    action: str,
    *,
    proof: str,
    output_paths: list[Path | str] | tuple[Path | str, ...] = (),
    work_order_id: str | None = None,
) -> Path | None:
    """Close the one matching current handoff after a successful controlled action.

    CLI commands call this only after their domain operation has succeeded. If an
    installation predates orchestration, or the action is supplementary and has
    no matching work order, it remains a no-op rather than fabricating history.
    """
    if _read_orchestration_state(workspace) is None:
        return None
    order = (
        read_orchestration_work_order(workspace, work_order_id)
        if work_order_id is not None
        else _live_order_for_action(workspace, action)
    )
    if order is None:
        return None
    if action not in order.allowed_commands:
        raise OrchestrationWorkOrderError(f"工单不允许执行 kp {action}")
    completed_path = complete_orchestration_work_order(
        workspace,
        order.work_order_id,
        proof,
        action=action,
        output_paths=output_paths,
        allow_same_stage_output=True,
    )
    _resume_run_intent_after_approved_decision(workspace, action)
    current = inspect_workflow(workspace)
    successor: OrchestrationWorkOrder | None = None
    if current.stage != "completed":
        successor_path = create_orchestration_work_order(workspace)
        successor = read_orchestration_work_order(workspace, successor_path)
    else:
        _terminalize_run_intent(workspace, delivery_complete=True)
    completed = read_orchestration_work_order(workspace, completed_path)
    if completed.completion_proof is not None and successor is not None:
        _atomic_json_write(
            completed_path,
            completed.model_copy(
                update={
                    "completion_proof": completed.completion_proof.model_copy(
                        update={"successor_work_order_id": successor.work_order_id}
                    )
                }
            ),
        )
    return completed_path


def _resume_run_intent_after_approved_decision(workspace: Workspace, action: str) -> RunIntent | None:
    """Resume a user-authorized automatic run after its approved decision is consumed.

    A run intent pauses at a genuine product decision.  The protected approval
    command is the only point at which the controller can prove that the user
    accepted the displayed version, so resumption happens here rather than when
    the host merely records an ambiguous natural-language response.
    """
    if action not in {"approve", "approve-sample"}:
        return None
    intent = read_run_intent(workspace)
    if (
        intent is None
        or not intent.auto_to_delivery
        or intent.state is not RunIntentState.WAITING_FOR_DECISION
    ):
        return intent
    resumed = intent.model_copy(
        update={
            "state": RunIntentState.ACTIVE,
            "updated_at": datetime.now(UTC),
            "stop_reason": None,
            "consecutive_no_progress": 0,
        }
    )
    _atomic_json_write(_run_intent_path(workspace), resumed)
    workspace.append_event(
        "run_intent_resumed",
        {"intent_id": resumed.intent_id, "approved_transition": action},
    )
    return resumed


def _terminalize_run_intent(workspace: Workspace, *, delivery_complete: bool) -> RunIntent | None:
    """Mark an existing automatic run terminal once its delivery is truly complete."""
    intent = read_run_intent(workspace)
    if intent is None or not delivery_complete:
        return intent
    if intent.state is RunIntentState.COMPLETED:
        return intent
    terminal = intent.model_copy(
        update={
            "state": RunIntentState.COMPLETED,
            "updated_at": datetime.now(UTC),
            "stop_reason": "交付已完成",
        }
    )
    _atomic_json_write(_run_intent_path(workspace), terminal)
    workspace.append_event("run_intent_terminal", {"intent_id": terminal.intent_id, "state": terminal.state.value})
    return terminal


def supersede_orchestration_work_orders(workspace: Workspace, reason: str) -> list[Path]:
    """Close live handoffs invalidated by a controlled revision without deleting history."""
    if _read_orchestration_state(workspace) is None:
        return []
    now = datetime.now(UTC)
    changed: list[Path] = []
    for path, order in _read_all_orchestration_work_orders(workspace):
        if not _is_live(order):
            continue
        superseded = order.model_copy(
            update={
                "lifecycle": WorkOrderLifecycle.SUPERSEDED,
                "completion_proof": CompletionProof(
                    completed_at=now,
                    completed_by="kp-control-plane",
                    transition="superseded",
                    reason=reason.strip() or "受控修订",
                    input_manifest_sha256=order.input_manifest_sha256,
                ),
            }
        )
        _atomic_json_write(path, superseded)
        if order.lease is not None:
            release_chapter_lease(workspace, order.lease.lease_id, order.work_order_id)
        changed.append(path)
    if changed:
        workspace.append_event(
            "orchestration_work_orders_superseded",
            {"work_order_ids": [path.stem for path in changed], "reason": reason.strip() or "受控修订"},
        )
    return changed


def fail_orchestration_action(workspace: Workspace, action: str, reason: str) -> Path | None:
    """Terminally close a failed action and create a fresh retry handoff when safe."""
    if _read_orchestration_state(workspace) is None:
        return None
    order = _live_order_for_action(workspace, action)
    if order is None:
        return None
    event_id = f"event-{uuid.uuid4().hex}"
    failed = order.model_copy(
        update={
            "lifecycle": WorkOrderLifecycle.FAILED,
            "completion_proof": CompletionProof(
                completed_at=datetime.now(UTC),
                completed_by="kp-control-plane",
                transition=action,
                event_id=event_id,
                reason=reason.strip()[:500] or "受控动作失败",
                input_manifest_sha256=order.input_manifest_sha256,
            ),
        }
    )
    output = _work_order_path(workspace, order.work_order_id)
    _atomic_json_write(output, failed)
    if order.lease is not None:
        release_chapter_lease(workspace, order.lease.lease_id, order.work_order_id)
    workspace.append_event(
        "orchestration_work_order_failed",
        {"work_order_id": order.work_order_id, "transition": action, "reason": failed.completion_proof.reason},
        event_id=event_id,
    )
    if inspect_workflow(workspace).stage != "completed":
        create_orchestration_work_order(workspace)
    return output


def reconcile_orchestration(
    workspace: Workspace, *, allowed_current_action: str | None = None
) -> OrchestrationReconciliation:
    """Conservatively inspect orchestration evidence without rerunning any work.

    Reconciliation intentionally does not promote old pending work to completed:
    without a version-bound event and output hashes that would fabricate audit
    history. It identifies that state as recoverable/blocking and lets the
    regular controller create a real retry handoff instead.
    """
    now = datetime.now(UTC)
    blockers: list[str] = []
    recoverable: list[str] = []
    completed = 0
    live = 0
    for _, order in _read_all_orchestration_work_orders(workspace):
        if order.lifecycle is WorkOrderLifecycle.COMPLETED:
            completed += 1
            if order.completion_proof is None:
                blockers.append(f"工单 {order.work_order_id} 已完成但缺少完成凭证")
            continue
        if _is_live(order, now):
            live += 1
            if allowed_current_action and allowed_current_action in order.allowed_commands:
                continue
            recoverable.append(f"工单 {order.work_order_id} 仍处于活动状态")
    return OrchestrationReconciliation(
        checked_at=now,
        blockers=blockers,
        recoverable=recoverable,
        completed_work_orders=completed,
        live_work_orders=live,
    )


def require_delivery_orchestration_ready(workspace: Workspace, action: str) -> None:
    """Reject publication only when a managed automatic run has unresolved evidence."""
    intent = read_run_intent(workspace)
    if intent is None:
        return
    reconciliation = reconcile_orchestration(workspace, allowed_current_action=action)
    blockers = [*reconciliation.blockers, *reconciliation.recoverable]
    if blockers:
        raise OrchestrationWorkOrderError("编排状态尚未收敛：" + "；".join(blockers))


def _advance_run_intent_after_completion(
    workspace: Workspace, completed_order: OrchestrationWorkOrder
) -> None:
    """Continue a persisted run only while no new product decision is required."""
    intent = read_run_intent(workspace)
    if intent is None or intent.state is not RunIntentState.ACTIVE or not intent.auto_to_delivery:
        return
    current = inspect_workflow(workspace)
    # ``inspect_workflow`` is necessarily called after the domain action has
    # produced output.  Compare the new state to the frozen work-order stage,
    # not to a second read of that same post-action state.
    no_progress = intent.consecutive_no_progress + 1 if current.stage == completed_order.stage else 0
    now = datetime.now(UTC)
    updates: dict[str, object] = {
        "updated_at": now,
        "last_work_order_id": completed_order.work_order_id,
        "last_stage": current.stage,
        "consecutive_no_progress": no_progress,
        "stop_reason": None,
    }
    if current.stage == "independent_review_capability_pending":
        updates.update(state=RunIntentState.BLOCKED, stop_reason="当前环境无法验证独立审稿")
    elif current.required_human_decision is not None:
        updates.update(
            state=RunIntentState.WAITING_FOR_DECISION,
            stop_reason="等待用户的产品级决定",
        )
    elif current.stage == "completed":
        updates.update(state=RunIntentState.COMPLETED, stop_reason="交付已完成")
    elif no_progress >= 3:
        updates.update(state=RunIntentState.BLOCKED, stop_reason="连续内部路由未取得进展")
    else:
        updates["state"] = RunIntentState.ACTIVE
    updated = intent.model_copy(update=updates)
    _atomic_json_write(_run_intent_path(workspace), updated)
    workspace.append_event(
        "run_intent_progressed",
        {
            "intent_id": updated.intent_id,
            "state": updated.state.value,
            "stage": current.stage,
            "completed_work_order_id": completed_order.work_order_id,
        },
    )
    if updated.state is RunIntentState.ACTIVE:
        # A fresh immutable handoff is created before the host returns.  The root
        # Skill must consume it in the same turn rather than asking the user to say
        # "continue" again.
        create_orchestration_work_order(workspace)


def cancel_orchestration_work_order(workspace: Workspace, work_order_id: str, reason: str) -> Path:
    """Cancel only the named work order and free its owned chapter lease."""
    order = read_orchestration_work_order(workspace, work_order_id)
    if order.lifecycle in {WorkOrderLifecycle.COMPLETED, WorkOrderLifecycle.CANCELLED}:
        raise OrchestrationWorkOrderError("已完成或已取消的工单不能再次取消")
    cancelled = order.model_copy(
        update={
            "lifecycle": WorkOrderLifecycle.CANCELLED,
            "completion_proof": CompletionProof(
                completed_at=datetime.now(UTC),
                completed_by="kp-control-plane",
                transition="cancelled",
                reason=reason.strip() or "用户取消",
                input_manifest_sha256=order.input_manifest_sha256,
            ),
        }
    )
    output = _work_order_path(workspace, work_order_id)
    _atomic_json_write(output, cancelled)
    if order.lease is not None:
        release_chapter_lease(workspace, order.lease.lease_id, work_order_id)
    workspace.append_event("orchestration_work_order_cancelled", {"work_order_id": work_order_id, "reason": reason})
    return output


def _lease_path(workspace: Workspace, chapter_number: int) -> Path:
    return _orchestration_lease_root(workspace) / f"chapter-{chapter_number:02d}.json"


def _read_lease(path: Path) -> ChapterLease | None:
    try:
        return ChapterLease.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def acquire_chapter_lease(
    workspace: Workspace,
    owner_work_order_id: str,
    chapter_number: int,
    *,
    owner_skill: str,
    ttl: timedelta = timedelta(minutes=30),
) -> ChapterLease:
    """Acquire a fail-closed lease; another live author can never be overwritten."""
    if chapter_number <= 0:
        raise ValueError("章节号必须为正整数")
    ensure_orchestration(workspace)
    path = _lease_path(workspace, chapter_number)
    existing = _read_lease(path)
    now = datetime.now(UTC)
    if existing is not None and existing.state is WorkOrderLifecycle.ACTIVE and existing.expires_at > now:
        if existing.owner_work_order_id == owner_work_order_id:
            return existing
        raise ChapterLeaseConflictError(
            f"第 {chapter_number} 章正文租约仍被 {existing.owner_skill}（{existing.owner_work_order_id}）占用"
        )
    lease = ChapterLease(
        lease_id=f"lease-chapter-{chapter_number:02d}-{uuid.uuid4().hex[:12]}",
        chapter_number=chapter_number,
        owner_work_order_id=owner_work_order_id,
        owner_skill=owner_skill,
        acquired_at=now,
        expires_at=now + ttl,
    )
    _atomic_json_write(path, lease)
    workspace.append_event(
        "chapter_lease_acquired",
        {"lease_id": lease.lease_id, "chapter_number": chapter_number, "owner_work_order_id": owner_work_order_id},
    )
    return lease


def release_chapter_lease(workspace: Workspace, lease_id: str, owner_work_order_id: str) -> Path:
    """Release only a lease owned by the current work order."""
    candidates = list(_orchestration_lease_root(workspace).glob("chapter-*.json"))
    for path in candidates:
        lease = _read_lease(path)
        if lease is None or lease.lease_id != lease_id:
            continue
        if lease.owner_work_order_id != owner_work_order_id:
            raise ChapterLeaseConflictError("不能释放其他工单持有的章节租约")
        released = lease.model_copy(update={"state": WorkOrderLifecycle.COMPLETED})
        _atomic_json_write(path, released)
        workspace.append_event("chapter_lease_released", {"lease_id": lease_id, "chapter_number": lease.chapter_number})
        return path
    raise FileNotFoundError("找不到章节正文租约")


def record_research_impact(
    workspace: Workspace, impact: ResearchImpact | str, summary: str
) -> Path:
    """Persist a bounded research impact decision before sample production."""
    ensure_orchestration(workspace)
    normalized = ResearchImpact(impact)
    routes = {
        ResearchImpact.NO_PRODUCT_CHANGE: "continue_production",
        ResearchImpact.PACKAGING_CHANGE: "amend_brief_delivery_only",
        ResearchImpact.CONTENT_CHANGE: "amend_brief_then_outline_then_compile_contracts",
        ResearchImpact.UNVERIFIED: "record_reader_visible_case_assumption",
    }
    decision = ResearchImpactDecision(
        decision_id=f"research-impact-{uuid.uuid4().hex[:12]}",
        recorded_at=datetime.now(UTC),
        impact=normalized,
        summary=summary.strip(),
        route=routes[normalized],
        requires_human_approval=normalized in {ResearchImpact.PACKAGING_CHANGE, ResearchImpact.CONTENT_CHANGE},
    )
    output = _orchestration_root(workspace) / "research-impacts" / f"{decision.decision_id}.json"
    _atomic_json_write(output, decision)
    workspace.append_event(
        "research_impact_recorded",
        {"decision_id": decision.decision_id, "impact": decision.impact.value, "route": decision.route},
    )
    return output


def preflight_independent_review(
    workspace: Workspace,
    *,
    platform: str,
    version: str | None = None,
    supports_real_subtasks: bool = False,
    production_verified: bool = False,
    max_parallelism: int = 0,
    reason: str | None = None,
) -> HostCapabilitySnapshot:
    """Freeze an honest independent-review capability result before batch writing."""
    ensure_orchestration(workspace)
    brief = workspace.root / "planning" / "product-brief.md"
    constraints = parse_detail_constraints(_artifact_body(brief))
    policy = constraints.review_policy.value if constraints is not None else "deterministic_only"
    binding = _brief_capability_binding(workspace)
    capabilities = HostCapabilities(
        platform=platform.strip() or "当前宿主",
        version=version,
        supports_real_subtasks=supports_real_subtasks,
        production_verified=production_verified,
        max_parallelism=max_parallelism,
        reason=reason
        or (
            "宿主能力已由适配器确认。"
            if supports_real_subtasks and production_verified
            else "当前宿主未提供可验证的真实父子任务回执。"
        ),
    )
    can_proceed = policy != "independent_review_required" or (
        capabilities.supports_real_subtasks and capabilities.production_verified
    )
    snapshot = HostCapabilitySnapshot(
        checked_at=datetime.now(UTC),
        review_policy=policy,
        capabilities=capabilities,
        decision="proceed" if can_proceed else "blocked",
        brief_content_sha256=binding[0] if binding is not None else None,
        brief_version=binding[1] if binding is not None else None,
    )
    _atomic_json_write(_orchestration_root(workspace) / "capability-snapshot.json", snapshot)
    workspace.append_event(
        "independent_review_capability_preflight",
        {"review_policy": policy, "decision": snapshot.decision, "platform": capabilities.platform},
    )
    if not can_proceed:
        raise HostCapabilityPreflightError(
            "当前宿主无法创建带真实回执的独立审稿子任务；请更换宿主或在 Brief 中选择 deterministic_only。"
        )
    return snapshot


def create_work_order(workspace: Workspace, decision: RouteDecision) -> Path:
    """Write an auditable, scoped work order for the current route decision."""
    reads, writes, outputs = _SKILL_SCOPES[decision.skill]
    existing_inputs = sorted(
        path for path, state in decision.status.artifacts.items() if state not in {"missing", "invalid"}
    )
    now = datetime.now(UTC)
    order = WorkOrder(
        work_order_id=f"wo-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
        created_at=now,
        stage=decision.status.stage,
        skill=decision.skill,
        inputs=existing_inputs,
        allowed_read_paths=reads,
        allowed_write_paths=writes,
        outputs=outputs,
        acceptance_commands=[decision.status.next_command],
    )
    directory = workspace.root / "runs" / "work-orders"
    directory.mkdir(exist_ok=True)
    output = directory / f"{order.work_order_id}.json"
    output.write_text(order.model_dump_json(indent=2), encoding="utf-8")
    workspace.append_event(
        "work_order_created",
        {
            "work_order_id": order.work_order_id,
            "stage": order.stage,
            "skill": order.skill,
            "path": _relative(workspace, output),
        },
    )
    return output
