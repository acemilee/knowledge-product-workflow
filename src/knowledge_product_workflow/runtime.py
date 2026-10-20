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
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from knowledge_product_workflow.assumption_ledger import (
    AssumptionDisposition,
    latest_assumptions,
    required_assumption_claim_ids,
)
from knowledge_product_workflow.chapter_compliance import (
    validate_current_chapter_compliance_in_view as validate_current_chapter_compliance,
)
from knowledge_product_workflow.claim_classification import factual_claim_ids
from knowledge_product_workflow.claims import (
    claim_scoped_evidence_digests,
    current_claim_contract_bindings,
)
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
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    read_bytes as _v_read_bytes,
)
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.verification_context import (
    verified_read as _verification_read,
)
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workflows.authoring import (
    require_current_chapter_contract_in_view as require_current_chapter_contract,
)
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
    pending_claim_ids: list[str] = Field(default_factory=list)
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
    target_chapter_number: int | None = None
    readonly_diagnostics: list[WorkflowAction] = Field(default_factory=list)
    production_overview: dict[str, object] = Field(default_factory=dict)
    deferred_repairs: list[dict] = Field(default_factory=list)
    execution_guidance: dict[str, object] = Field(default_factory=dict)


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
    selector: str | None = None


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
    control_finalized_at: datetime | None = None
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

    schema_version: int = 3
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
    actions: list[WorkflowAction] = Field(default_factory=list)
    command_discovery: list[str] = Field(default_factory=lambda: ["kp", "commands", "--all", "--json"])
    preconditions: list[str]
    success_transition: str
    failure_transition: str
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    lease: ChapterLease | None = None
    completion_proof: CompletionProof | None = None
    progress_snapshot: dict[str, object] | None = None

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
    reading_structure_revision: int = Field(default=1, ge=1, le=2)
    reading_review_rubric_revision: int = Field(default=1, ge=1, le=3)
    request_capture_revision: int = Field(default=0, ge=0, le=1)
    review_evidence_revision: int = Field(default=0, ge=0, le=1)
    production_mode: str = "serial"
    binding_policy_revision: int = Field(default=1, strict=True, ge=1, le=2)


class RunIntentState(StrEnum):
    """Lifecycle of a user-authorized controller run, never a reader artifact."""

    ACTIVE = "active"
    STOP_REQUESTED = "stop_requested"
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
    pending_operations: list[str] = Field(default_factory=list)
    unknown_tasks: list[str] = Field(default_factory=list)
    pause_assurance: str | None = None


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
    if not _v_is_file(path):
        return "missing"
    try:
        metadata, _ = parse_markdown_artifact(_v_read_text(path, encoding="utf-8"))
    except (OSError, ValueError):
        return "invalid"
    return metadata.status.value


def _artifact_body(path: Path) -> str:
    try:
        return parse_markdown_artifact(_v_read_text(path, encoding="utf-8"))[1]
    except (OSError, ValueError):
        return ""


def _events(workspace: Workspace) -> list[dict[str, object]]:
    event_path = workspace.root / "runs" / "events.jsonl"
    if not _v_is_file(event_path):
        return []
    events: list[dict[str, object]] = []
    for line in _v_read_text(event_path, encoding="utf-8").splitlines():
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
    if not _v_is_file(ledger):
        return set(), False
    claims: set[str] = set()
    try:
        for line in _v_read_text(ledger, encoding="utf-8").splitlines():
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
    pending_claim_ids: list[str] | None = None,
    next_actions: list[WorkflowAction] | None = None,
    target_chapter_number: int | None = None,
) -> WorkflowStatus:
    return WorkflowStatus(
        stage=stage,
        blocked_by=blocked_by,
        recommended_skill=recommended_skill,
        next_command=next_command,
        artifacts=artifacts,
        declared_claim_ids=declared_claim_ids,
        pending_claim_ids=pending_claim_ids or [],
        recent_events=[str(event.get("event_type")) for event in events[-5:]],
        next_actions=next_actions or [],
        target_chapter_number=target_chapter_number,
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
        "pdf_preview_repair_pending",
        "pdf_visual_audit_pending",
        "pdf_manual_review_pending",
        "delivery_preflight_pending",
        "delivery_preflight_blocked",
        "pdf_visual_repair_pending",
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
        "pdf_preview_repair_pending": "PDF 已生成，正在修复页面预览。",
        "pdf_visual_audit_pending": "PDF 已生成，下一步量测各页并检查清晰预览。",
        "pdf_manual_review_pending": "页面量测已完成，请查看当前 PDF 并确认视觉复核结果。",
        "delivery_preflight_pending": "当前PDF准备进行确认前的自动交付预检。",
        "delivery_preflight_blocked": "自动交付预检发现阻断项，先修复再请求用户确认。",
        "pdf_visual_repair_pending": "当前页面仍有视觉缺陷，先按报告修订再复核。",
        "chapter_assets_pending": "全部正文已就绪，图资产/完整提交仍有待办；可查看完整诊断预览，正式交付尚未通过。",
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
        "pdf_manual_review_pending": "请查看当前 PDF，确认视觉复核通过或指出需修改的页面。",
        "independent_review_capability_pending": "选择可验证独立审稿宿主，或将审稿策略改为 deterministic_only",
    }.get(stage)


def _user_decision_target_for_stage(stage: str) -> UserDecisionTarget | None:
    return {
        "brief_awaiting_approval": UserDecisionTarget.BRIEF,
        "blueprint_awaiting_approval": UserDecisionTarget.BLUEPRINT,
        "sample_awaiting_approval": UserDecisionTarget.SAMPLE,
        "pdf_manual_review_pending": UserDecisionTarget.PDF,
    }.get(stage)


def _fallback_next_actions(workspace: Workspace, status: WorkflowStatus) -> list[WorkflowAction]:
    """Provide a command-shaped action when legacy stages only contain prose."""
    root = str(workspace.root)
    if status.stage == 'pdf_manual_review_pending':
        from knowledge_product_workflow.user_decisions import UserDecisionState, _read_record
        record = _read_record(workspace, UserDecisionTarget.PDF)
        requirement = status.user_decision
        if requirement is None and record is not None:
            requirement = user_decision_requirement(workspace, UserDecisionTarget.PDF)
        stale = requirement is not None and requirement.state == 'stale'
        if record is None or stale or record.state not in {UserDecisionState.PENDING, UserDecisionState.APPROVED}:
            scopes = record.requested_scope if record is not None else ['visual']
            return [WorkflowAction(command='request-user-decision',
                argv=['kp','request-user-decision','pdf','--workspace',root,
                      *[part for scope in scopes for part in ['--scope',scope]]],
                reason_code='DISPLAY_CURRENT_PDF_AND_SCOPE_BEFORE_ASKING')]
        if record.state is UserDecisionState.PENDING:
            return [WorkflowAction(command='record-user-decision',
                argv=['kp','record-user-decision','pdf','--workspace',root,'--decision','<approve|revise|clarify>',
                      '--response','<用户实际答复>','--scope','<用户实际确认的已请求范围，可重复>'],
                reason_code='RECORD_ACTUAL_USER_RESPONSE_WITH_EXPLICIT_SCOPE')]
        flags = {'manual_full_coverage':'--confirm-manual-coverage','content':'--confirm-content',
                 'style_anchor':'--confirm-style-anchor'}
        return [WorkflowAction(command='record-pdf-visual-review',
            argv=['kp','record-pdf-visual-review','--workspace',root,'--response','<用户实际答复>',
                  *[flags[scope] for scope in record.confirmed_scope if scope in flags]],
            reason_code='CURRENT_PDF_HUMAN_REVIEW_REQUIRED')]
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
        "research_impact_decision_pending": (
            "record-research-impact",
            ["kp", "record-research-impact", "--workspace", root, "--impact", "<影响分类>", "--summary", "<研究结论>"],
            "RESEARCH_IMPACT_REQUIRED",
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
        "pdf_preview_repair_pending": (
            "repair-pdf-preview",
            ["kp", "repair-pdf-preview", "--workspace", root],
            "PDF_PREVIEW_REPAIR_REQUIRED",
        ),
        "pdf_visual_audit_pending": ("audit-pdf-visual", ["kp", "audit-pdf-visual", "--workspace", root], "PDF_VISUAL_AUDIT_REQUIRED"),
        "pdf_manual_review_pending": ("record-pdf-visual-review", ["kp", "record-pdf-visual-review", "--workspace", root], "CURRENT_PDF_HUMAN_REVIEW_REQUIRED"),
        "delivery_preflight_pending": ("delivery-preflight", ["kp", "delivery-preflight", "--workspace", root], "DELIVERY_PREFLIGHT_REQUIRED"),
        "delivery_preflight_blocked": ("delivery-preflight", ["kp", "delivery-preflight", "--workspace", root], "DELIVERY_PREFLIGHT_BLOCKED"),
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
        "delivery_manifest_stale": ("publish", ["kp", "publish", "--workspace", root], "DELIVERY_MANIFEST_STALE"),
    }
    entry = stage_commands.get(status.stage)
    if status.stage == "chapter_proofs_pending" and status.target_chapter_number:
        number = status.target_chapter_number
        return [WorkflowAction(command="check-chapter", argv=["kp", "check-chapter", str(number),
            "--workspace", root, "--from", str(workspace.root / f"chapters/chapter-{number:02d}.md"),
            "--json"], reason_code="PROOF_ONLY_RECHECK_NO_BODY_AMENDMENT")]
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
    _v_mkdir(path.parent,parents=True,exist_ok=True)
    text = payload.model_dump_json(indent=2) if isinstance(payload, BaseModel) else json.dumps(payload, ensure_ascii=False, indent=2)
    from knowledge_product_workflow.verification_context import atomic_metadata_text
    atomic_metadata_text(path, text + "\n")


def _read_orchestration_state(workspace: Workspace) -> OrchestrationState | None:
    try:
        return OrchestrationState.model_validate_json(_v_read_text(_orchestration_state_path(workspace), encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise ValueError("编排状态损坏或不可读，先恢复，不得重新初始化") from error


def read_run_intent(workspace: Workspace) -> RunIntent | None:
    """Read the latest persisted controller intent without changing it."""
    try:
        return RunIntent.model_validate_json(_v_read_text(_run_intent_path(workspace), encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise ValueError("运行意图损坏或不可读，先恢复") from error


def start_run_intent(workspace: Workspace, *, auto_to_delivery: bool = True,
                     _continuation_capabilities=None) -> RunIntent:
    """Persist a user-approved request to keep routing safe internal work."""
    view = recovery_view(workspace)
    if not view["can_resume"]:
        same_session = (not view["pending_operations"] and not view["state_errors"]
            and _continuation_capabilities is not None
            and _continuation_capabilities.admits_live_parallelism()
            and _continuation_capabilities._scope_workspace == str(workspace.root.resolve())
            and bool(view["task_contexts"])
            and len(view["unknown_tasks"]) == len(view["task_contexts"])
            and all(t["run_id"] == _continuation_capabilities._scope_run_id
                    and t.get("host_parent_id") in {None, _continuation_capabilities._native_session_id}
                    for t in view["task_contexts"]))
        if not same_session:
            raise ValueError("仍有待恢复事务、损坏状态或未知任务；先查原动作，不重复派单")
    ensure_orchestration(workspace)
    now = datetime.now(UTC)
    current = read_run_intent(workspace)
    if current is not None and current.state is RunIntentState.ACTIVE and current.auto_to_delivery == auto_to_delivery:
        return current
    if current is not None and current.state in {RunIntentState.PAUSED, RunIntentState.STOP_REQUESTED}:
        resumed = current.model_copy(update={"state": RunIntentState.ACTIVE, "updated_at": now,
            "stop_reason": None, "pending_operations": view["pending_operations"],
            "unknown_tasks": view["unknown_tasks"]})
        _atomic_json_write(_run_intent_path(workspace), resumed)
        workspace.append_event("run_intent_resumed", {"intent_id": resumed.intent_id})
        return resumed
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
    view = recovery_view(workspace)
    paused = current.model_copy(
        update={
            "state": RunIntentState.PAUSED if view["can_resume"] else RunIntentState.STOP_REQUESTED,
            "updated_at": datetime.now(UTC),
            "stop_reason": reason.strip() or "用户暂停",
            "pending_operations": view["pending_operations"],
            "unknown_tasks": view["unknown_tasks"],
            "pause_assurance": "control_plane_only",
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


def initialize_new_orchestration(workspace: Workspace, *, current_policy: bool = False,
                                 binding_policy_revision: int = 1) -> OrchestrationState:
    """Create the managed controller records required by a freshly initialized book.

    This is intentionally distinct from :func:`ensure_orchestration`: the latter is
    an explicit, non-destructive adoption path for historic workspaces, whereas a
    new ``kp init`` run must never be mistaken for legacy content.
    """
    state = _read_orchestration_state(workspace)
    if state is None:
        state = OrchestrationState(initialized_at=datetime.now(UTC), migrated_from_legacy=False,
                                   reading_structure_revision=2, reading_review_rubric_revision=3 if current_policy else 2,
                                   request_capture_revision=1 if current_policy else 0,
                                   review_evidence_revision=1 if current_policy else 0,
                                   production_mode="adaptive" if current_policy else "serial",
                                   binding_policy_revision=binding_policy_revision)
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
    if not _v_is_file(path):
        return None
    return hashlib.sha256(_v_read_bytes(path)).hexdigest()


def _input_manifest(workspace: Workspace, status: WorkflowStatus) -> list[InputManifestEntry]:
    """Freeze actual artifact bytes, including missing declared inputs, but never logs."""
    request_path = "runs/request-contract.yaml"
    request_entry = InputManifestEntry(path=request_path,
        state="present" if _v_is_file(workspace.root / request_path) else "missing",
        sha256=_hash_bytes(workspace.root / request_path))
    if status.stage in {
        "sample_draft_missing", "sample_compliance_pending",
        "chapter_drafts_incomplete", "chapter_compliance_pending",
    }:
        chapter_number = _target_chapter_from_status(status)
        if chapter_number is not None:
            _, chapter = require_current_chapter_contract(workspace, chapter_number)
            paths = [
                "planning/curriculum-blueprint.md",
                f"chapters/chapter-{chapter_number:02d}-contract.md",
            ]
            entries = [
                InputManifestEntry(
                    path=path,
                    state="present" if _v_is_file(workspace.root / path) else "missing",
                    sha256=_hash_bytes(workspace.root / path),
                )
                for path in paths
            ]
            brief = workspace.root / "planning" / "product-brief.md"
            entries.append(InputManifestEntry(
                path="planning/product-brief.md", state="present", selector="content_contract",
                sha256=brief_contract_fingerprints(_artifact_body(brief))["content_contract_sha256"],
            ))
            for relative, digest in claim_scoped_evidence_digests(workspace, set(chapter.claim_ids)).items():
                entries.append(InputManifestEntry(
                    path=relative, state="scoped", selector=f"chapter-{chapter_number:02d}-claims",
                    sha256=digest,
                ))
            return [*entries, request_entry]
    return [
        InputManifestEntry(path=path, state=state, sha256=_hash_bytes(workspace.root / path))
        for path, state in sorted(status.artifacts.items()) if path != request_path
    ] + [request_entry]


def _manifest_sha256(entries: list[InputManifestEntry]) -> str:
    payload = [entry.model_dump(mode="json") for entry in entries]
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _target_chapter_from_status(status: WorkflowStatus) -> int | None:
    if status.target_chapter_number is not None:
        return status.target_chapter_number
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
            orders.append((path, OrchestrationWorkOrder.model_validate_json(_v_read_text(path, encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return sorted(orders, key=lambda pair: pair[1].created_at)


def _is_live(order: OrchestrationWorkOrder, now: datetime | None = None) -> bool:
    return order.lifecycle in {WorkOrderLifecycle.PENDING, WorkOrderLifecycle.ACTIVE} and order.expires_at > (now or datetime.now(UTC))


_CHAPTER_PRODUCTION_STAGES = frozenset(
    {"sample_draft_missing", "sample_compliance_pending", "chapter_drafts_incomplete", "chapter_compliance_pending"}
)


def _live_chapter_order(
    workspace: Workspace, status: WorkflowStatus, manifest_sha256: str,
    *, orders: list[tuple[Path, OrchestrationWorkOrder]] | None = None,
) -> tuple[Path, OrchestrationWorkOrder] | None:
    if status.stage not in _CHAPTER_PRODUCTION_STAGES:
        return None
    target = _target_chapter_from_status(status)
    for path, order in reversed(_read_all_orchestration_work_orders(workspace) if orders is None else orders):
        if (
            _is_live(order)
            and order.schema_version >= 3
            and order.stage in _CHAPTER_PRODUCTION_STAGES
            and order.skill == "kp-chapter-writer"
            and order.target_chapter_number == target
            and order.input_manifest_sha256 == manifest_sha256
        ):
            return path, order
    return None


def _current_orchestration_work_order_path(
    workspace: Workspace, status: WorkflowStatus
) -> Path | None:
    if _read_orchestration_state(workspace) is None:
        return None
    entries = _input_manifest(workspace, status)
    manifest_sha = _manifest_sha256(entries)
    orders = _read_all_orchestration_work_orders(workspace)
    chapter_order = _live_chapter_order(workspace, status, manifest_sha, orders=orders)
    if chapter_order is not None:
        return chapter_order[0]
    target = _target_chapter_from_status(status)
    key_payload = {
        "input_policy_version": 3,
        "macro_phase": _macro_phase_for_stage(status.stage).value,
        "stage": status.stage,
        "skill": status.recommended_skill,
        "target_chapter_number": target,
        "input_manifest_sha256": manifest_sha,
    }
    key = hashlib.sha256(
        json.dumps(key_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    for path, order in reversed(orders):
        if order.idempotency_key == key and order.schema_version >= 3 and _is_live(order):
            return path
    return None


def _latest_research_impact(workspace: Workspace) -> ResearchImpactDecision | None:
    root = _orchestration_root(workspace) / "research-impacts"
    decisions: list[ResearchImpactDecision] = []
    if root.is_dir():
        for path in root.glob("*.json"):
            try:
                decisions.append(ResearchImpactDecision.model_validate_json(_v_read_text(path, encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return max(decisions, key=lambda item: item.recorded_at) if decisions else None


def _capability_snapshot(workspace: Workspace) -> HostCapabilitySnapshot | None:
    path = _orchestration_root(workspace) / "capability-snapshot.json"
    try:
        return HostCapabilitySnapshot.model_validate_json(_v_read_text(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _brief_capability_binding(workspace: Workspace) -> tuple[str, int] | None:
    """Return the reader-content binding protected by a capability preflight."""
    brief = workspace.root / "planning" / "product-brief.md"
    if not _v_is_file(brief):
        return None
    try:
        metadata, body = parse_markdown_artifact(_v_read_text(brief, encoding="utf-8"))
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


def require_workflow_mutation_allowed(workspace: Workspace, action: str) -> None:
    """A shared pause boundary; recovery of an owned action remains available."""
    if not action.strip():
        raise ValueError("受控工作流动作不能为空")
    from knowledge_product_workflow.change_impact import binding_policy_revision
    intent = read_run_intent(workspace)
    if (binding_policy_revision(workspace) == 2 and intent is not None
            and intent.state in {RunIntentState.PAUSED, RunIntentState.STOP_REQUESTED}
            and not action.startswith(("recover", "production-recover", "cancel", "pause"))):
        raise WorkflowAuthorizationError("工作流已请求暂停；仅可核对／恢复原动作，不得产生新副作用")


def authorize_workflow_transition(workspace: Workspace, action: str) -> None:
    """Enforce global policy before any state-changing workflow operation."""
    require_workflow_mutation_allowed(workspace, action)
    if not _independent_review_capability_is_current(workspace):
        raise HostCapabilityPreflightError(
            "当前环境无法提供可验证的独立审稿。本书可改用确定性质量检查，或在支持独立审稿的环境继续。"
        )


def _enrich_orchestration_status(workspace: Workspace, status: WorkflowStatus) -> WorkflowStatus:
    """Add v1.1 fields without mutating a read-only status call."""
    managed = _read_orchestration_state(workspace) is not None
    adjusted = status.model_copy(update={'execution_guidance': {
        'wait_owner': 'one_parent_tool_or_program_no_llm_coordinator',
        'receipt_query': 'kp operation-status --operation-id <原ID> --json',
        'serial_wait': 'kp wait-operation --operation-id <原ID> --timeout 60',
        'native_wait': 'same-session run_until_attention; explicit authorized_actions; qualification required',
        'host_no_inference_wait': 'pending_U11_use_single_parent_bounded_fallback',
        'research_batch': '逐声明核验后同证据组一次research重复--claim',
        'repair_scope': '按真实依赖局部复核；最终质量门收齐债务',
    }})
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
        and _v_is_file(research_ledger)
        and _v_read_text(research_ledger, encoding="utf-8").strip()
        and set(adjusted.declared_claim_ids).intersection(_covered_claims(workspace)[0])
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
    work_order = _current_orchestration_work_order_path(workspace, adjusted) if managed else None
    decision = None
    target = _user_decision_target_for_stage(adjusted.stage)
    if target is not None:
        try:
            decision = user_decision_requirement(workspace, target)
        except (FileNotFoundError, ValueError):
            decision = None
    adjusted = adjusted.model_copy(update={'user_decision':decision})
    actions = adjusted.next_actions or _fallback_next_actions(workspace, adjusted)
    summary = _user_summary_for_stage(adjusted.stage, adjusted.blocked_by)
    if decision is not None and decision.state == "revise" and adjusted.stage in {
        "brief_awaiting_approval", "blueprint_awaiting_approval"
    }:
        command = "brief" if adjusted.stage == "brief_awaiting_approval" else "amend-outline"
        actions = [WorkflowAction(
            command=command,
            argv=["kp", command, "--workspace", str(workspace.root), "--from", "<按反馈更新的完整输入>"],
            reason_code="USER_REQUESTED_DRAFT_REVISION",
        )]
        summary = "已收到修改意见，请更新草案并重新展示确认。"
    if decision is not None and decision.state == "stale":
        summary = "内容已更新，请确认最新版本。"
    intent = read_run_intent(workspace)
    return adjusted.model_copy(
        update={
            "macro_phase": _macro_phase_for_stage(adjusted.stage),
            "user_summary": summary,
            "required_human_decision": _human_decision_for_stage(adjusted.stage),
            "user_decision": decision,
            "current_work_order": _relative(workspace, work_order) if work_order is not None else None,
            "orchestration_mode": "managed" if managed else "legacy",
            "run_intent_state": intent.state.value if intent is not None else None,
            "auto_to_delivery": bool(
                intent is not None
                and intent.state is RunIntentState.ACTIVE
                and intent.auto_to_delivery
            ),
            "next_actions": actions,
            "target_chapter_number": _target_chapter_from_status(adjusted) if adjusted.stage in _CHAPTER_PRODUCTION_STAGES or adjusted.stage == "sample_awaiting_approval" else adjusted.target_chapter_number,
            "readonly_diagnostics": [WorkflowAction(
                command="check-chapter",
                argv=["kp", "check-chapter", str(_target_chapter_from_status(adjusted)), "--workspace", str(workspace.root), "--from", "<当前候选源稿>", "--json"],
                reason_code="CHAPTER_PROOF_REFRESH_DIAGNOSTIC",
            )] if adjusted.stage in _CHAPTER_PRODUCTION_STAGES else [],
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
        if _v_is_file(root / "research" / "source-ledger.jsonl")
        else "missing",
        "research/assumption-ledger.jsonl": "present"
        if _v_is_file(root / "research" / "assumption-ledger.jsonl")
        else "missing",
        "delivery/book.md": "present" if _v_is_file(root / "delivery" / "book.md") else "missing",
        "delivery/book.qmd": "present" if _v_is_file(root / "delivery" / "book.qmd") else "missing",
        "delivery/book.pdf": "present" if _v_is_file(root / "delivery" / "book.pdf") else "missing",
        "runs/visual-audit-report.json": "present" if _v_is_file(root / "runs" / "visual-audit-report.json") else "missing",
        "runs/delivery-preflight.json": "present" if _v_is_file(root / "runs/delivery-preflight.json") else "missing",
        "runs/pdf-visual-review.json": "present" if _v_is_file(root / "runs" / "pdf-visual-review.json") else "missing",
        "runs/interaction/pdf.json": _artifact_state(root / "runs/interaction/pdf.json"),
        "delivery/delivery-manifest.yaml": "present"
        if _v_is_file(root / "delivery" / "delivery-manifest.yaml")
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
        recommendation_ready = _v_is_file(workspace.root / "planning" / "teaching-length-recommendation.yaml")
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
    try:
        _, _, submission = require_current_submitted_blueprint(workspace, require_approved=False)
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
    required_source_claims = factual_claim_ids(workspace, claim_bindings) | required_assumption_claim_ids(assumption_path)
    source_findings = validate_claims(
        required_source_claims, root / "research" / "source-ledger.jsonl", claim_bindings
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
            pending_claim_ids=sorted({
                finding.location for finding in source_findings
                if finding.code in {"MISSING_CLAIM_SOURCE", "CLAIM_SOURCE_CONTRACT_MISMATCH"}
                and finding.location in {claim.casefold() for claim in required_source_claims}
            }),
            events=events,
        )

    sample = root / "chapters" / "chapter-01.md"
    if not _v_is_file(sample):
        return _status(
            stage="sample_draft_missing",
            blocked_by=["缺少第一章样章"],
            recommended_skill="kp-chapter-writer",
            next_command=f'kp draft 1 --workspace "{workspace_arg}"',
            artifacts=artifacts,
            declared_claim_ids=declared_claim_ids,
            events=events,
        )
    from knowledge_product_workflow.user_decisions import sample_approval_is_current
    if not sample_approval_is_current(workspace):
        from knowledge_product_workflow.content_checkpoint import current_content_checkpoint
        sample_findings = [item for item in validate_current_chapter_compliance(workspace, sample)
                           if item.severity.value == "blocker"]
        if current_content_checkpoint(workspace, 1) is not None:
            sample_findings = []
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
    artifacts.update({_relative(workspace, path): _artifact_state(path) for path in drafts})
    deferred_chapters = []
    deferred_semantic_reviews = []
    # Finish the reopened draft before repairing proofs of chapters referring
    # to it. Those stale proofs still block the subsequent quality/delivery gate.
    chapter_drafts = sorted(zip(chapter_numbers, drafts, strict=True),
                            key=lambda item: artifacts[_relative(workspace, item[1])] != "in_production")
    for chapter_number, draft in chapter_drafts:
        if not _v_is_file(draft):
            return _status(
                stage="chapter_drafts_incomplete",
                blocked_by=[f"缺少第 {chapter_number} 章正文骨架"],
                recommended_skill="kp-chapter-writer",
                next_command=f'kp draft {chapter_number} --workspace "{workspace_arg}"',
                artifacts=artifacts,
                declared_claim_ids=declared_claim_ids,
                events=events,
                target_chapter_number=chapter_number,
            )
        compliance_findings = [item for item in validate_current_chapter_compliance(workspace, draft)
                               if item.severity.value == "blocker"]
        if compliance_findings:
            from knowledge_product_workflow.content_checkpoint import current_content_checkpoint
            checkpoint = current_content_checkpoint(workspace, chapter_number)
            if checkpoint is not None:
                deferred_chapters.append(chapter_number)
                continue
            only_external_review_stale = all(
                item.code == "DEVELOPMENTAL_REPORT_REQUIRED"
                and ("review_context_current" in item.message or "实际比较的案例单元已变化" in item.message)
                for item in compliance_findings)
            if only_external_review_stale and artifacts[_relative(workspace, draft)] != "in_production":
                # Body/contract/plan compliance has already passed above. Queue
                # only the external-context review; final assurance still blocks.
                deferred_semantic_reviews.append((chapter_number, compliance_findings[0].message))
                continue
            return _status(
                stage="chapter_compliance_pending",
                blocked_by=[compliance_findings[0].message],
                recommended_skill="kp-chapter-writer",
                next_command=(
                    f'kp submit-chapter {chapter_number} --workspace "{workspace_arg}" '
                    "--from <chapter.md> --compliance <chapter-compliance.json>"
                ),
                artifacts=artifacts,
                declared_claim_ids=declared_claim_ids,
                events=events,
                target_chapter_number=chapter_number,
            )

    if deferred_semantic_reviews:
        number, reason = deferred_semantic_reviews[0]
        return _status(stage="chapter_compliance_pending", blocked_by=[reason],
                       recommended_skill="kp-chapter-writer",
                       next_command=f'kp submit-chapter {number} --workspace "{workspace_arg}" --from <chapter.md> --compliance <chapter-compliance.json>',
                       artifacts=artifacts, declared_claim_ids=declared_claim_ids, events=events,
                       target_chapter_number=number)
    if deferred_chapters:
        number = deferred_chapters[0]
        return _status(stage="chapter_assets_pending", blocked_by=[f"正文已就绪；第{number}章仍有待办资产/完整提交，正式交付阻断"],
                       recommended_skill="kp-chapter-writer",
                       next_command=f'kp render-review-preview --workspace "{workspace_arg}"',
                       artifacts=artifacts, declared_claim_ids=declared_claim_ids, events=events,
                       target_chapter_number=number)
    report_path = root / "delivery" / "qa-report.json"
    if not _v_is_file(report_path) or not _has_event(events, "validation_completed"):
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
        quality_report = QualityReport.model_validate_json(_v_read_text(report_path, encoding="utf-8"))
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
        from knowledge_product_workflow.publishing import (
            pdf_template_input_drift,
            release_readiness_findings,
        )

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
        template_drift = pdf_template_input_drift(workspace) if requires_pdf else []
        if reassembly_findings or template_drift:
            return _status(
                stage="assembly_pending",
                blocked_by=[
                    "交付派生输入已过期："
                    + (reassembly_findings[0].message if reassembly_findings else
                       "当前运行包的排版模板已更新：" + "、".join(template_drift))
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
    if requires_pdf:
        from knowledge_product_workflow.pdf_visual import (
            manual_visual_review_needed,
            preview_repair_needed,
            uses_v6_profile,
        )
        from knowledge_product_workflow.visual_audit import current_visual_audit

        try:
            if len(PdfReader(root / "delivery" / "book.pdf").pages) == 0:
                raise ValueError("PDF 没有页面")
        except (OSError, PdfReadError, ValueError):
            return _status(
                stage="pdf_render_pending",
                blocked_by=["当前 PDF 损坏或无法读取，需要重新受控渲染"],
                recommended_skill="kp-publisher",
                next_command=f'kp render-pdf --workspace "{workspace_arg}"',
                artifacts=artifacts,
                declared_claim_ids=declared_claim_ids,
                events=events,
                next_actions=[
                    WorkflowAction(
                        command="render-pdf",
                        argv=["kp", "render-pdf", "--workspace", workspace_arg],
                        reason_code="PDF_INVALID_AND_REBUILD_REQUIRED",
                    )
                ],
            )
        if preview_repair_needed(workspace):
            return _status(
                stage="pdf_preview_repair_pending",
                blocked_by=["当前 PDF 的页面预览清单或图片缺失、损坏或过期"],
                recommended_skill="kp-publisher",
                next_command=f'kp repair-pdf-preview --workspace "{workspace_arg}"',
                artifacts=artifacts,
                declared_claim_ids=declared_claim_ids,
                events=events,
                next_actions=[
                    WorkflowAction(
                        command="repair-pdf-preview",
                        argv=["kp", "repair-pdf-preview", "--workspace", workspace_arg],
                        reason_code="PDF_PREVIEW_REPAIR_REQUIRED",
                    )
                ],
            )
        if uses_v6_profile(workspace) and current_visual_audit(workspace) is None:
            return _status(
                stage="pdf_visual_audit_pending", blocked_by=["当前 PDF 缺少有效逐页视觉报告"],
                recommended_skill="kp-visual-reviewer", next_command=f'kp audit-pdf-visual --workspace "{workspace_arg}"',
                artifacts=artifacts, declared_claim_ids=declared_claim_ids, events=events,
            )
        audit = current_visual_audit(workspace) if uses_v6_profile(workspace) else None
        if audit is not None and (
            any(finding.severity.value == "blocker" for finding in audit.findings)
            or any(observation.assessment == "failed" for observation in audit.observations)
        ):
            failed = next((item for item in audit.observations if item.assessment == "failed"), None)
            content_failure = (failed is not None and failed.responsible_skill == "kp-chapter-writer") or any(
                finding.code == "ILLUSTRATION_CONTENT_FAILED" for finding in audit.findings)
            missing_style = any(finding.code == "ILLUSTRATION_STYLE_PROOF_REQUIRED" for finding in audit.findings)
            action = "record-illustration-style-check" if missing_style else "amend-chapter" if content_failure else "update-publication"
            argv = ["kp", action, "--workspace", workspace_arg, "--from", "<出版修订输入>"] if action == "update-publication" else ["kp", action, "<受影响章节号>", "--workspace", workspace_arg, "--reason", "<视觉报告修订原因>"]
            if missing_style:
                argv = ["kp", action, "--workspace", workspace_arg, "--from", "<当前资产风格观察>"]
            if missing_style:
                from knowledge_product_workflow.illustration_updates import (
                    presentation_update_needed,
                )
                from knowledge_product_workflow.illustrations import current_profile
                if current_profile(workspace).illustration_style_revision == 2 and presentation_update_needed(workspace):
                    action = "update-diagram-presentation"
                    argv = ["kp",action,"<受影响章节号>","<图ID>","--workspace",workspace_arg,"--chapter-source","<当前已提交章节>"]
                    missing_style = False
            style_failure = any(finding.code == "ILLUSTRATION_STYLE_FAILED" for finding in audit.findings)
            if style_failure:
                action = "illustration-preflight"
                argv = ["kp", action, "<受影响章节号>", "--workspace", workspace_arg,
                        "--from", "<已冻结语义输入>", "--chapter-source", "<已提交章节>", "--supports-reference",
                        "--repair-reason", "<当前报告的明确样式缺陷>", "--reference-capacity", "3"]
            return _status(
                stage="pdf_visual_repair_pending", blocked_by=["视觉报告仍有 blocker 或失败观察；不能记录人工通过或反复校验"],
                recommended_skill="kp-diagram-designer" if style_failure or action == "update-diagram-presentation" else "kp-visual-reviewer" if missing_style else "kp-publisher" if action == "update-publication" else "kp-quality-editor",
                next_command="缺证明先记录当前资产检查；样式错误在原槽位预算内重新生图/提交，保持正文；内容错误amend-chapter。修复后受控重渲染，不重复validate。",
                artifacts=artifacts, declared_claim_ids=declared_claim_ids, events=events,
                next_actions=[WorkflowAction(command=action, argv=argv, reason_code="PDF_VISUAL_REPAIR_REQUIRED")],
            )
        if manual_visual_review_needed(workspace):
            from knowledge_product_workflow.delivery_preflight import current_delivery_preflight
            preflight = current_delivery_preflight(workspace)
            if preflight is None or not preflight.ready_for_human_review:
                return _status(
                    stage="delivery_preflight_pending" if preflight is None else "delivery_preflight_blocked",
                    blocked_by=["先完成当前PDF自动交付预检，不先请求用户确认。"],
                    recommended_skill="kp-quality-editor",
                    next_command=f'kp delivery-preflight --workspace "{workspace_arg}"',
                    artifacts=artifacts, declared_claim_ids=declared_claim_ids, events=events,
                )
            return _status(
                stage="pdf_manual_review_pending", blocked_by=["当前 PDF 尚未获得版本绑定的人工视觉复核"],
                recommended_skill="kp-create", next_command="展示当前 PDF 和视觉报告，用户确认后记录复核。",
                artifacts=artifacts, declared_claim_ids=declared_claim_ids, events=events,
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
        delivery_report = QualityReport.model_validate_json(_v_read_text(report_path, encoding="utf-8"))
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


@_verification_read(transaction_view=True)
def inspect_workflow(workspace: Workspace) -> WorkflowStatus:
    from knowledge_product_workflow.production_intake import (
        ProductionConsistencyError,
        consistent_production_read,
    )
    try:
        from knowledge_product_workflow.change_impact import binding_policy_revision
        if binding_policy_revision(workspace) == 2:
            try:
                with consistent_production_read(workspace, chapters=set()):
                    precise = _independent_chapter_status(workspace)
                    if precise is not None:
                        return _enrich_orchestration_status(workspace, precise)
            except ProductionConsistencyError as error:
                from knowledge_product_workflow.production_tasks import ControlLockBusy
                if isinstance(error.__cause__, ControlLockBusy):
                    raise
        with consistent_production_read(workspace):
            return _inspect_current_workflow(workspace)
    except ProductionConsistencyError as error:
        from knowledge_product_workflow.production_tasks import ControlLockBusy
        if isinstance(error.__cause__, ControlLockBusy) and error.__cause__.owner["state"] == "active":
            return WorkflowStatus(stage="production_busy", blocked_by=[str(error)],
                recommended_skill="kp-create", next_command="同工作区串行查询；等原命令终态，不创建工单或自动接管")
        return WorkflowStatus(stage="production_recovery_pending", blocked_by=[str(error)],
            recommended_skill="kp-create", next_command="核受控提交／锁终态，按production-recover／production-recover-lock恢复")
    except (ValueError, TypeError, OSError) as error:
        return WorkflowStatus(stage="orchestration_recovery_required", blocked_by=[str(error)],
            recommended_skill="kp-create", next_command="kp recovery-view --json；按原工件核对，不重新初始化")


def _independent_chapter_status(workspace: Workspace) -> WorkflowStatus | None:
    from knowledge_product_workflow.change_impact import binding_policy_revision
    from knowledge_product_workflow.production_dependencies import (
        build_dependency_plan,
        deferred_repairs,
    )
    from knowledge_product_workflow.user_decisions import sample_approval_is_current
    if binding_policy_revision(workspace) != 2:
        return None
    try:
        if not sample_approval_is_current(workspace):
            return None
        plan = build_dependency_plan(workspace)
        if plan.legacy:
            return None  # Unknown legacy dependencies never mean independent work.
        missing = []
        from knowledge_product_workflow.workspace_transactions import pending_workspace_transactions
        pending_numbers = {n for tx in pending_workspace_transactions(workspace) for n in tx.get("scope_chapters", [])}
        for chapter in plan.chapters:
            number = chapter["chapter_number"]
            if number in pending_numbers:
                continue
            path = workspace.root / f"chapters/chapter-{number:02d}.md"
            if not _v_is_file(path):
                missing.append(number)
            elif _artifact_state(path) == "in_production":
                return None  # Keep the existing author lease before scheduling another.
        debts = deferred_repairs(workspace)
        ready = [n for n in plan.ready_chapters if n in missing]
        if ready:
            number = ready[0]
            status = _status(stage="chapter_drafts_incomplete", blocked_by=[f"第{number}章就绪"],
                recommended_skill="kp-chapter-writer",
                next_command=f'kp draft {number} --workspace "{workspace.root}"', artifacts={},
                declared_claim_ids=[], events=[], target_chapter_number=number)
        elif debts and debts[0]["kind"] == "independent_review":
            number = debts[0]["chapter_number"]
            status = _status(stage="review_batch_pending", blocked_by=debts[0]["reasons"],
                recommended_skill="kp-quality-editor", next_command=f'kp submit-review-batch --workspace "{workspace.root}"',
                artifacts={}, declared_claim_ids=[], events=[], target_chapter_number=number)
        elif debts:
            number = debts[0]["chapter_number"]
            status = _status(stage="chapter_proofs_pending", blocked_by=debts[0]["reasons"],
                recommended_skill="kp-chapter-writer", next_command=(f'kp check-chapter {number} '
                f'--workspace "{workspace.root}" --from "{workspace.root / "chapters" / f"chapter-{number:02d}.md"}" --json'),
                artifacts={p: "present" if _v_is_file(workspace.root / p) else "missing" for p in
                    ["planning/curriculum-blueprint.md", "planning/product-brief.md",
                     f"chapters/chapter-{number:02d}.md", f"chapters/chapter-{number:02d}-contract.md",
                     f"chapters/chapter-{number:02d}-content-plan.yaml",
                     f"runs/humanize/chapter-{number:02d}.json",
                     f"runs/chapter-compliance/chapter-{number:02d}.json",
                     f"runs/editorial/chapter-{number:02d}-structure-review.json",
                     f"runs/editorial/chapter-{number:02d}-developmental-report.json"]},
                declared_claim_ids=[], events=[], target_chapter_number=number)
        else:
            return None
        status.deferred_repairs = debts
        return status
    except (OSError, ValueError):
        return None  # Original policy/gates explain missing, malformed or unapproved inputs.


def _inspect_current_workflow(workspace: Workspace) -> WorkflowStatus:
    """Read legacy gates first, then project them into the opt-in v1.1 control plane."""
    from knowledge_product_workflow.production_intake import pending_production_commits

    pending = pending_production_commits(workspace)
    if pending:
        return WorkflowStatus(stage="production_recovery_pending", blocked_by=pending,
            recommended_skill="kp-create", next_command="先恢复受控章节提交，禁止读取半提交正文")
    from knowledge_product_workflow.production_intake import production_read_token
    before = production_read_token(workspace)
    status = _enrich_orchestration_status(workspace, _inspect_workflow(workspace))
    state = _read_orchestration_state(workspace)
    if state:
        status.production_overview = {"requested_mode": state.production_mode,
            "effective_mode": "serial", "requested_parallelism": 4,
            "effective_parallelism": 1, "qualification": "pending",
            "reason": "原生宿主资格未通过；保留原串行权限和质量门"}
    if production_read_token(workspace) != before:
        return WorkflowStatus(stage="production_recovery_pending", blocked_by=["读取期间提交版本变化"],
            recommended_skill="kp-create", next_command="核受控提交终态后重新读取，不消费混合版本")
    return status


def route_workflow(workspace: Workspace) -> RouteDecision:
    """Return the one Skill allowed to handle the current persisted stage."""
    status = inspect_workflow(workspace)
    if status.stage == "production_busy":
        raise ValueError(status.blocked_by[0])
    return RouteDecision(status=status, skill=status.recommended_skill)


_COMMAND_STAGES: dict[str, frozenset[str]] = {
    "validate": frozenset(
        {"quality_validation_pending", "quality_report_invalid", "quality_report_stale", "quality_blocked"}
    ),
    "assemble": frozenset({"assembly_pending"}),
    "render-pdf": frozenset({"pdf_render_pending"}),
    "repair-pdf-preview": frozenset({"pdf_preview_repair_pending"}),
    "audit-pdf-visual": frozenset({"pdf_visual_audit_pending"}),
    "record-pdf-visual-review": frozenset({"pdf_manual_review_pending"}),
    "delivery-preflight": frozenset({"delivery_preflight_pending", "delivery_preflight_blocked", "pdf_manual_review_pending"}),
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
    if not _v_is_file(report_path):
        return "交付质量报告缺失"
    try:
        report = QualityReport.model_validate_json(_v_read_text(report_path, encoding="utf-8"))
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
    "kp-publication-designer": (
        ["planning/", "runs/"],
        ["runs/publication-design/"],
        ["runs/publication-design/preview.json"],
    ),
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
    "kp-developmental-editor": (
        ["planning/", "chapters/", "runs/"],
        ["runs/editorial/"],
        ["runs/editorial/chapter-XX-developmental-report.json"],
    ),
    "kp-chinese-humanize-editor": (
        ["planning/", "chapters/", "research/", "assets/"],
        ["runs/humanize/"],
        ["runs/humanize/chapter-XX.json"],
    ),
    "kp-diagram-designer": (
        ["planning/", "chapters/", "assets/"],
        ["assets/figures/", "runs/diagrams/", "runs/illustrations/", "planning/illustration-plan.json"],
        [
            "assets/figures/<diagram-id>.pdf",
            "assets/figures/<diagram-id>.png",
            "runs/diagrams/<diagram-id>.json",
        ],
    ),
    "kp-quality-editor": (
        ["planning/", "research/", "chapters/", "delivery/"],
        ["runs/review-batches/", "delivery/qa-report.json", "runs/delivery-preflight.json"],
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
    "kp-visual-reviewer": (
        ["planning/publication-profile.yaml", "delivery/", "runs/pdf-preview/", "runs/diagrams/", "assets/figures/", "runs/illustrations/"],
        ["runs/visual-audit-report.json", "runs/visual-audit-pages/", "runs/illustrations/asset-checks/", "runs/illustrations/candidates/"],
        ["runs/visual-audit-report.json"],
    ),
    "kp-create": (["planning/", "chapters/", "delivery/", "runs/"], [], []),
}


_STAGE_ACTION_ALIASES: dict[str, set[str]] = {
    "sample_compliance_pending": {"submit-chapter", "checkpoint-chapter-content"},
    "chapter_compliance_pending": {"submit-chapter", "checkpoint-chapter-content"},
    "chapter_assets_pending": {"submit-chapter", "update-diagram-presentation", "activate-illustration-candidate"},
    # The template-generation command is preparatory. Submitting the completed
    # topic-specific blueprint is what actually closes this architecture handoff.
    "blueprint_missing": {"submit-outline"},
    "sample_draft_missing": {"submit-chapter", "checkpoint-chapter-content", "update-diagram-presentation", "activate-illustration-candidate"},
    "chapter_drafts_incomplete": {"submit-chapter", "checkpoint-chapter-content", "update-diagram-presentation", "activate-illustration-candidate"},
}


def _orchestration_idempotency_key(
    status: WorkflowStatus, manifest_sha256: str, target_chapter_number: int | None
) -> str:
    payload = {
        "input_policy_version": 3,
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


@_verification_read(transaction_view=True)
def create_orchestration_work_order(
    workspace: Workspace, *, ttl: timedelta = timedelta(minutes=30), adopt_legacy: bool = False,
    refresh_reason: str | None = None,
    _post_status: WorkflowStatus | None = None,
) -> Path:
    """Create or reuse the exact v1.1 handoff required by the current status.

    This is intentionally separate from :func:`create_work_order`, which remains
    the legacy manual-recovery record under ``runs/work-orders``.
    """
    preview = orchestration_migration_preview(workspace) if _read_orchestration_state(workspace) is None else {"requires_confirmation": False}
    if bool(preview["requires_confirmation"]) and not adopt_legacy:
        raise OrchestrationWorkOrderError(
            "这是尚未纳入 v1.1 编排的旧工作区；请先运行 kp orchestration-migration-preview 查看只读范围，"
            "经用户确认后以 --adopt-legacy 创建编排工单。"
        )
    status = _post_status if _post_status is not None else inspect_workflow(workspace)
    if status.stage == "production_busy":
        raise OrchestrationWorkOrderError(status.blocked_by[0])
    ensure_orchestration(workspace)
    intent = read_run_intent(workspace)
    if intent is not None and intent.auto_to_delivery and intent.state is RunIntentState.WAITING_FOR_DECISION and status.required_human_decision is None and status.stage != "completed":
        _atomic_json_write(_run_intent_path(workspace), intent.model_copy(update={"state": RunIntentState.ACTIVE, "updated_at": datetime.now(UTC), "stop_reason": None}))
        workspace.append_event("run_intent_resumed", {"intent_id": intent.intent_id, "reason": "当前失效决定已转为受控内部恢复，无需新的产品审批"})
    entries = _input_manifest(workspace, status)
    manifest_sha256 = _manifest_sha256(entries)
    chapter_order = _live_chapter_order(workspace, status, manifest_sha256)
    if chapter_order is not None:
        supersede_orchestration_work_orders(
            workspace,
            refresh_reason or "存在与当前章节工单冲突的旧路由",
            keep_ids=frozenset({chapter_order[1].work_order_id}),
        )
        return chapter_order[0]
    target_chapter_number = _target_chapter_from_status(status)
    idempotency_key = _orchestration_idempotency_key(status, manifest_sha256, target_chapter_number)
    for path, existing in reversed(_read_all_orchestration_work_orders(workspace)):
        if (existing.idempotency_key == idempotency_key and existing.schema_version >= 3 and _is_live(existing)
                and (status.stage != 'delivery_manifest_stale' or existing.allowed_commands == ['publish'])):
            supersede_orchestration_work_orders(
                workspace,
                refresh_reason or "存在与当前唯一工单冲突的旧路由",
                keep_ids=frozenset({existing.work_order_id}),
            )
            return path

    # A newly routed stage must not leave an older, equally authorized handoff
    # alive for thirty minutes. The exact current order was reused above.
    supersede_orchestration_work_orders(workspace, refresh_reason or "当前工作流阶段或输入版本已变化")

    reads, writes, outputs = _SKILL_SCOPES[status.recommended_skill]
    if status.stage == "chapter_proofs_pending":
        writes = ["runs/editorial/", "runs/humanize/", "runs/orchestration/proof-refresh/"]
        outputs = ["runs/editorial/", "runs/humanize/", "runs/orchestration/proof-refresh/"]
    actions = status.next_actions or _fallback_next_actions(workspace, status)
    now = datetime.now(UTC)
    preconditions = [f"stage={status.stage}", *status.blocked_by]
    if status.recommended_skill == 'kp-chapter-writer':
        from knowledge_product_workflow.content_plan import CONTENT_PLAN_SCHEMA_VERSION
        preconditions.append('首次内容计划用本章content-plan-template --json一次读取模板、上下文和真实提交Schema；不要逐字段猜枚举，也不要每轮重复读取全量Schema；'
            f'Schema版本{CONTENT_PLAN_SCHEMA_VERSION}，同版本复用，禁止失败提交探字段。'
            '最小section样例：{"section_id":"1.1","reader_job":"比较输入与条件后做出可检验决定",'
            '"opening_mode":"claim","block_sequence":["explanation","example"],'
            '"artifact":"none","asset_decision":"none","asset_rationale":"本节散文示例足以说明判断"}。'
            '样例不代替当前章合约及必需图任务；asset_decision=none且artifact=none时省略artifact_kind；'
            '非图表产物的artifact_kind仅code/derivation/decision。')
    if status.stage == "research_required":
        preconditions.append(
            "登记前读取pending_claim_ids，逐声明核验证据后按来源身份／版本、支持范围和许可分组；"
            "同一证据组用一次research串行提交，例如 --claim <claim-1> --claim <claim-2>；有效覆盖不重复登记。"
        )
    if status.readonly_diagnostics:
        preconditions.append(
            "当前候选源稿已存在时先消费readonly_diagnostics的check-chapter --json；"
            "占位源稿须替换为作者交接的工作区内文件。缺源稿先draft；只读诊断不扩权、不收尾，"
            "仅刷新refresh_actions中缺失／过期项，结构blocker先修正文。"
        )
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
        actions=actions,
        preconditions=preconditions,
        success_transition="重新读取 kp status --json 并由控制面选择下一动作",
        failure_transition="保留当前工件并按 kp status --json 的恢复路径处理",
        idempotency_key=idempotency_key,
        progress_snapshot=_progress_snapshot(status),
    )
    if (order.skill == "kp-chapter-writer" and target_chapter_number is not None
            and status.stage != "chapter_proofs_pending"):
        lease = acquire_chapter_lease(
            workspace,
            order.work_order_id,
            target_chapter_number,
            owner_skill=order.skill,
            ttl=ttl,
        )
        order = order.model_copy(update={"lifecycle": WorkOrderLifecycle.ACTIVE, "lease": lease})
    output = _orchestration_work_order_root(workspace) / f"{order.work_order_id}.json"
    try:
        _atomic_json_write(output, order)
    except OSError:
        if order.lease is not None:
            release_chapter_lease(workspace, order.lease.lease_id, order.work_order_id)
        raise
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
        return OrchestrationWorkOrder.model_validate_json(_v_read_text(resolved, encoding="utf-8"))
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


def capture_orchestration_action(workspace: Workspace, action: str, *, chapter_number: int | None = None) -> str | None:
    """Freeze the one live handoff before a command writes stage-changing output.

    Commands commonly change the workflow stage as part of producing their output.
    Looking up a handoff afterwards can therefore miss the order that authorized
    the command.  The caller keeps this opaque identifier only for the matching
    completion call; it is never a user-facing recovery mechanism.
    """
    return _capture_live_action(workspace, action, chapter_number=chapter_number)


@_verification_read
def _prepare_delivery_action(workspace: Workspace, action: str) -> str | None:
    """Authorize and capture one delivery action before any domain output is written."""
    from knowledge_product_workflow.verification_context import current_verification

    if action not in {"validate", "assemble", "validate-delivery", "publish"}:
        raise ValueError("写前状态复用仅用于四个出版动作")
    context = current_verification(workspace)
    status = authorize_command(workspace, action)
    # A status inspection acquires/releases its own consistency locks. Its
    # completed read is the snapshot boundary, not the phase before it started.
    phase = context.phase
    if action in {"validate-delivery", "publish"}:
        require_delivery_orchestration_ready(workspace, action)
    captured = status if current_verification(workspace) is context and not context.closed and context.phase == phase else None
    return _capture_live_action(workspace, action, _captured_status=captured)


def _capture_live_action(workspace: Workspace, action: str, *,
                         chapter_number: int | None = None,
                         _captured_status: WorkflowStatus | None = None) -> str | None:
    if _read_orchestration_state(workspace) is None:
        return None
    matches = [
        order
        for _, order in _read_all_orchestration_work_orders(workspace)
        if _is_live(order) and action in order.allowed_commands
    ]
    if not matches:
        # New managed books must not adopt a changed stage simply because the
        # old handoff lacks the newly requested command. Explicit legacy library
        # fixtures retain their prior first-action recovery behavior.
        state = _read_orchestration_state(workspace)
        live = [order for _, order in _read_all_orchestration_work_orders(workspace) if _is_live(order)]
        if state is not None and state.request_capture_revision >= 1 and live:
            validate_orchestration_work_order(workspace, live[-1].work_order_id)
        # A controlled command can legitimately be the first v1.1 action after
        # an in-process compatibility fixture or a recovered legacy mutation.
        # Route the *current* persisted stage once, then freeze the resulting
        # handoff.  Do not ask callers to manufacture a work-order ID.
        _captured_status = None
        create_orchestration_work_order(workspace)
        matches = [
            order
            for _, order in _read_all_orchestration_work_orders(workspace)
            if _is_live(order) and action in order.allowed_commands
        ]
    if not matches:
        raise OrchestrationWorkOrderError(f"当前托管工作区没有允许执行 kp {action} 的有效工单")
    if len(matches) > 1:
        _captured_status = None
        create_orchestration_work_order(workspace)
        matches = [
            order
            for _, order in _read_all_orchestration_work_orders(workspace)
            if _is_live(order) and action in order.allowed_commands
        ]
    if len(matches) > 1:
        ids = ", ".join(order.work_order_id for order in matches)
        raise OrchestrationWorkOrderError(
            f"当前存在多个允许 kp {action} 的工单：{ids}；请用 kp orchestration-plan --refresh --reason <原因> "
            "受控重路由，无法自动证明失效时用 kp cancel-orchestration-work-order 指定冲突工单。"
        )
    order = matches[0]
    validate_orchestration_work_order(workspace, order.work_order_id, command=action,
        chapter_number=chapter_number, _captured_status=_captured_status)
    return order.work_order_id


def validate_orchestration_work_order(
    workspace: Workspace,
    work_order_id: str,
    *,
    command: str | None = None,
    chapter_number: int | None = None,
    _captured_status: WorkflowStatus | None = None,
) -> OrchestrationWorkOrder:
    """Verify lifecycle, snapshot and scoped command authority before a Skill acts."""
    order = read_orchestration_work_order(workspace, work_order_id)
    if not _is_live(order):
        raise OrchestrationWorkOrderError("编排工单已过期、已完成或已取消；请重新运行 kp status --json 获取新的恢复动作")
    status = _captured_status if _captured_status is not None else inspect_workflow(workspace)
    current_manifest = _manifest_sha256(_input_manifest(workspace, status))
    if current_manifest != order.input_manifest_sha256:
        before = {entry.path: entry for entry in order.inputs}
        after = {entry.path: entry for entry in _input_manifest(workspace, status)}
        changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
        raise WorkOrderInputChangedError(
            "工单输入工件已变化：" + (", ".join(changed[:8]) or "输入策略或工作流阶段")
            + "；请运行 kp orchestration-plan --refresh --reason <原因>。"
        )
    if command is not None and command not in order.allowed_commands:
        raise OrchestrationWorkOrderError(f"工单不允许执行 kp {command}")
    if chapter_number is not None and order.target_chapter_number not in {None, chapter_number}:
        raise OrchestrationWorkOrderError("工单不拥有该章节；不得跨章节写入")
    return order


@_verification_read(transaction_view=True)
def capture_orchestration_update(workspace: Workspace) -> tuple[WorkflowStatus, list[InputManifestEntry]] | None:
    """Snapshot before a supplementary controlled write; new books refuse preexisting drift."""
    require_workflow_mutation_allowed(workspace, "controlled-update")
    state = _read_orchestration_state(workspace)
    if state is None:
        return None
    status = inspect_workflow(workspace)
    live = [order for _, order in _read_all_orchestration_work_orders(workspace) if _is_live(order)]
    if state.request_capture_revision >= 1 and live:
        validate_orchestration_work_order(workspace, live[-1].work_order_id, _captured_status=status)
    return status, _input_manifest(workspace, status)


def refresh_orchestration_after_controlled_update(
    workspace: Workspace, action: str, *,
    snapshot: tuple[WorkflowStatus, list[InputManifestEntry]] | None,
    output_paths: list[Path | str] | tuple[Path | str, ...],
) -> Path | None:
    """Refresh a successor only after observed writes changed their declared inputs."""
    if snapshot is None:
        return None
    status, previous = snapshot
    before = {entry.path: entry for entry in previous}
    # Compare the same dependency scope, even if producing a legitimate result
    # now routes to a different chapter or stage.
    after = {entry.path: entry for entry in _input_manifest(workspace, status)}
    changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
    outputs = _output_manifest(workspace, output_paths)
    if not changed.issubset({entry.path for entry in outputs}):
        raise WorkOrderInputChangedError("受控更新期间出现非输出输入变化；请运行 kp orchestration-plan --refresh --reason <原因>。")
    workspace.append_event("orchestration_controlled_update", {
        "action": action, "changed_inputs": sorted(changed),
        "outputs": [entry.model_dump(mode="json") for entry in outputs],
    })
    return create_orchestration_work_order(workspace, refresh_reason=f"kp {action} 已成功更新当前工件")


def _progress_snapshot(status: WorkflowStatus) -> dict[str, object]:
    return {"stage": status.stage, "target": _target_chapter_from_status(status),
        "artifacts": dict(status.artifacts), "obligations": sorted(status.blocked_by),
        "actions": sorted((a.command, a.reason_code) for a in status.next_actions),
        "human_decision": status.required_human_decision}


def _completion_fault(phase: str) -> None:
    """Deterministic test boundary, never selectable by a product command."""


def _append_completion_event_once(workspace: Workspace, order: OrchestrationWorkOrder) -> None:
    proof = order.completion_proof
    if proof is None or proof.event_id is None:
        return
    if order.lease:
        lease = _read_lease(_lease_path(workspace, order.lease.chapter_number))
        if lease and lease.lease_id == order.lease.lease_id and lease.owner_work_order_id == order.work_order_id and lease.state is not WorkOrderLifecycle.COMPLETED:
            release_chapter_lease(workspace, order.lease.lease_id, order.work_order_id)
    # The persisted completion event ID is reused even if its original reply was lost.
    events = workspace.root / 'runs/events.jsonl'
    for line in _v_read_text(events, encoding='utf-8').splitlines():
        if line.strip() and json.loads(line).get('event_id') == proof.event_id:
            return
    workspace.append_event('orchestration_work_order_completed', {
        'work_order_id': order.work_order_id, 'transition': proof.transition,
        'proof': proof.proof, 'outputs': [entry.path for entry in proof.output_manifest],
    }, event_id=proof.event_id)


def complete_orchestration_work_order(
    workspace: Workspace, work_order_id: str, proof: str, *, action: str = "manual",
    output_paths: list[Path | str] | tuple[Path | str, ...] = (), allow_same_stage_output: bool = False,
) -> Path:
    return finish_controlled_action(workspace, work_order_id, proof, action=action,
        output_paths=output_paths, allow_same_stage_output=allow_same_stage_output)


def finish_controlled_action(
    workspace: Workspace, work_order_id: str, proof: str, *, action: str,
    output_paths: list[Path | str] | tuple[Path | str, ...] = (),
    allow_same_stage_output: bool = False, route_without_intent: bool = False,
    committed_operation_id: str | None = None,
) -> Path:
    """One post-domain inspection, one owner for close/intent/successor, idempotent replay."""
    order = read_orchestration_work_order(workspace, work_order_id)
    if committed_operation_id:
        from knowledge_product_workflow.operation_receipts import verify_committed_completion
        verify_committed_completion(workspace, committed_operation_id, order, action)
    output = _work_order_path(workspace, work_order_id)
    if order.lifecycle is WorkOrderLifecycle.COMPLETED and order.completion_proof is not None and order.completion_proof.control_finalized_at is not None:
        return output
    # Domain state is now frozen; closing the old lease changes control metadata only.
    current = inspect_workflow(workspace)
    output = _close_orchestration_work_order(workspace, work_order_id, proof, action=action,
        output_paths=output_paths, allow_same_stage_output=allow_same_stage_output, _post_status=current,
        _committed_operation_id=committed_operation_id)
    _completion_fault('after_close')
    _resume_run_intent_after_approved_decision(workspace, action)
    _advance_run_intent_after_completion(workspace, order, _post_status=current)
    _completion_fault('after_intent')
    intent = read_run_intent(workspace)
    successor = None
    if current.stage == "completed":
        if intent is not None and intent.state is not RunIntentState.COMPLETED:
            _terminalize_run_intent(workspace, delivery_complete=True)
    elif ((intent is None or intent.state not in {RunIntentState.PAUSED, RunIntentState.STOP_REQUESTED, RunIntentState.BLOCKED, RunIntentState.COMPLETED})
            and (route_without_intent or (intent is not None and intent.auto_to_delivery and intent.state is RunIntentState.ACTIVE))):
        successor = read_orchestration_work_order(workspace, create_orchestration_work_order(workspace, _post_status=current))
    _completion_fault('after_successor')
    completed = read_orchestration_work_order(workspace, output)
    if completed.completion_proof is not None:
        _atomic_json_write(output, completed.model_copy(update={"completion_proof":completed.completion_proof.model_copy(update={
            "successor_work_order_id":successor.work_order_id if successor else None,
            "control_finalized_at":datetime.now(UTC)})}))
    return output


def _close_orchestration_work_order(
    workspace: Workspace,
    work_order_id: str,
    proof: str,
    *,
    action: str = "manual",
    output_paths: list[Path | str] | tuple[Path | str, ...] = (),
    allow_same_stage_output: bool = False,
    _post_status: WorkflowStatus | None = None,
    _committed_operation_id: str | None = None,
) -> Path:
    """Close a current work order with controller-owned version evidence."""
    if not proof.strip():
        raise OrchestrationWorkOrderError("完成证明不能为空")
    order = read_orchestration_work_order(workspace, work_order_id)
    if order.lifecycle is WorkOrderLifecycle.COMPLETED:
        _append_completion_event_once(workspace, order)
        return _work_order_path(workspace, work_order_id)
    if _committed_operation_id:
        from knowledge_product_workflow.operation_receipts import verify_committed_completion
        verify_committed_completion(workspace, _committed_operation_id, order, action)
    elif not _is_live(order):
        raise OrchestrationWorkOrderError("编排工单已过期、已完成或已取消；请重新运行 kp status --json 获取新的恢复动作")
    status = _post_status if _post_status is not None else inspect_workflow(workspace)
    current_manifest = _manifest_sha256(_input_manifest(workspace, status))
    outputs = _output_manifest(workspace, output_paths)
    if current_manifest != order.input_manifest_sha256 and status.stage == order.stage:
        before = {entry.path: entry for entry in order.inputs}
        after = {entry.path: entry for entry in _input_manifest(workspace, status)}
        changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
        observed_outputs = {entry.path for entry in outputs}
        scoped_submission = False
        if (action == "submit-chapter" and order.target_chapter_number is not None
                and _target_chapter_from_status(status) != order.target_chapter_number
                and status.stage in _CHAPTER_PRODUCTION_STAGES):
            number = order.target_chapter_number
            previous_scope = status.model_copy(update={"target_chapter_number": number, "stage": order.stage})
            scoped_submission = (
                _manifest_sha256(_input_manifest(workspace, previous_scope)) == order.input_manifest_sha256
                and {f"chapters/chapter-{number:02d}.md", f"runs/chapter-compliance/chapter-{number:02d}.json"}.issubset(observed_outputs)
            )
        if not (allow_same_stage_output and outputs and (changed.issubset(observed_outputs) or scoped_submission)):
            raise WorkOrderInputChangedError("工单输入工件已变化且不属于本动作实际输出；请运行 kp orchestration-plan --refresh --reason <原因>。")
    event_id = f"event-{uuid.uuid4().hex}"
    completed = order.model_copy(
        update={
            "lifecycle": WorkOrderLifecycle.COMPLETED,
            "completion_proof": CompletionProof(
                completed_at=datetime.now(UTC),
                completed_by="kp-control-plane",
                transition="chapter_content_checkpointed" if action == "checkpoint-chapter-content" else action,
                event_id=event_id,
                proof=proof.strip(),
                input_manifest_sha256=order.input_manifest_sha256,
                output_manifest=outputs,
            ),
        }
    )
    output = _work_order_path(workspace, work_order_id)
    _completion_fault('before_close')
    _atomic_json_write(output, completed)
    _completion_fault('close_saved')
    _append_completion_event_once(workspace, completed)
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
    return finish_controlled_action(workspace, order.work_order_id, proof, action=action,
        output_paths=output_paths, allow_same_stage_output=True, route_without_intent=True)


def _resume_run_intent_after_approved_decision(workspace: Workspace, action: str) -> RunIntent | None:
    """Resume a user-authorized automatic run after its approved decision is consumed.

    A run intent pauses at a genuine product decision.  The protected approval
    command is the only point at which the controller can prove that the user
    accepted the displayed version, so resumption happens here rather than when
    the host merely records an ambiguous natural-language response.
    """
    if action not in {"approve", "approve-sample", "record-pdf-visual-review"}:
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


def supersede_orchestration_work_orders(
    workspace: Workspace, reason: str, *, keep_ids: frozenset[str] = frozenset()
) -> list[Path]:
    """Close live handoffs invalidated by a controlled revision without deleting history."""
    if _read_orchestration_state(workspace) is None:
        return []
    now = datetime.now(UTC)
    changed: list[Path] = []
    for path, order in _read_all_orchestration_work_orders(workspace):
        if order.lifecycle not in {WorkOrderLifecycle.PENDING, WorkOrderLifecycle.ACTIVE} or order.work_order_id in keep_ids:
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
            current_lease = _read_lease(_lease_path(workspace, order.lease.chapter_number))
            if current_lease is not None and current_lease.lease_id == order.lease.lease_id and current_lease.owner_work_order_id == order.work_order_id:
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
    workspace: Workspace, completed_order: OrchestrationWorkOrder, *, _post_status: WorkflowStatus | None = None
) -> None:
    """Continue a persisted run only while no new product decision is required."""
    intent = read_run_intent(workspace)
    if intent is None or intent.state is not RunIntentState.ACTIVE or not intent.auto_to_delivery:
        return
    if intent.last_work_order_id == completed_order.work_order_id:
        return
    current = _post_status if _post_status is not None else inspect_workflow(workspace)
    # ``inspect_workflow`` is necessarily called after the domain action has
    # produced output.  Compare the new state to the frozen work-order stage,
    # not to a second read of that same post-action state.
    before = completed_order.progress_snapshot
    progressed = (_progress_snapshot(current) != before if before is not None else
        current.stage != completed_order.stage or _target_chapter_from_status(current) != completed_order.target_chapter_number)
    no_progress = 0 if progressed else intent.consecutive_no_progress + 1
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
            stop_reason="等待用户对当前 PDF 的视觉判断" if current.stage == "pdf_manual_review_pending" else "等待用户的产品级决定",
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
        return ChapterLease.model_validate_json(_v_read_text(path, encoding="utf-8"))
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
    if decision.status.stage == "production_busy":
        raise ValueError("CONTROL_LOCK_BUSY: 不创建或刷新业务工单")
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
    _v_mkdir(directory,exist_ok=True)
    output = directory / f"{order.work_order_id}.json"
    _v_write_text(output, order.model_dump_json(indent=2), encoding="utf-8")
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


def recovery_view(workspace: Workspace) -> dict:
    """Read-only minimal re-entry facts; no claim that a UI stopped every host process."""
    from knowledge_product_workflow.production_intake import pending_production_commits
    from knowledge_product_workflow.workspace_transactions import pending_workspace_transactions
    state_errors = []
    try:
        state = _read_orchestration_state(workspace)
        intent = read_run_intent(workspace)
    except (ValueError, OSError) as error:
        state_errors.append(str(error))
        state, intent = None, None
    pending = [r["operation_id"] for r in pending_workspace_transactions(workspace)]
    pending.extend(pending_production_commits(workspace))
    from knowledge_product_workflow.operation_receipts import operation_status
    operation_diagnostics = []
    for receipt in (workspace.root / 'runs/orchestration/operations').glob('op-*.json'):
        details = operation_status(workspace, receipt.stem)
        if details.get('phase') not in {'completed', 'closed_failed'}:
            pending.append(receipt.stem)
            operation_diagnostics.append({key: details.get(key) for key in (
                'operation_id','phase','domain_stage','error','transaction_id','failure_evidence_sha256',
                'next_action','next_argv','closure_eligibility')})
    unknown = []
    tasks = []
    for path in sorted((workspace.root / "runs/production").glob("*/run.json")):
        try:
            run = json.loads(_v_read_text(path, encoding="utf-8"))
            from knowledge_product_workflow.production_tasks import ProductionRun
            try:
                ProductionRun.model_validate(run)
            except ValueError:
                state_errors.append(f"原生运行Schema不完整：{path.parent.name}")
            for task in run.get("tasks", []):
                if (not task.get("release_confirmed", False)
                        and (task.get("host_task_id") or task.get("dispatch_index") is not None)):
                    unknown.append(task["task_id"])
                    tasks.append({"run_id": run.get("run_id", path.parent.name), **{k: task.get(k) for k in ("task_id", "chapter_number", "state",
                        "host_task_id", "host_parent_id", "candidate_version", "input_manifest_sha256")}})
            unknown.extend(sorted(run.get("nested_active_ids", [])))
        except (ValueError, OSError, KeyError, TypeError) as error:
            state_errors.append(f"原生任务记录不可读：{path.name}：{error}")
    work_orders = []
    for path in sorted(_orchestration_work_order_root(workspace).glob("*.json")):
        try:
            raw = json.loads(_v_read_text(path, encoding="utf-8"))
            OrchestrationWorkOrder.model_validate(raw)
            if raw.get("lifecycle") in {"created", "active"}:
                work_orders.append({k: raw.get(k) for k in ("work_order_id", "stage", "skill", "target_chapter_number")})
        except (ValueError, OSError) as error:
            state_errors.append(f"工单记录损坏：{path.name}：{error}")
    saved = sorted(p.relative_to(workspace.root).as_posix() for p in (workspace.root / "chapters").glob("chapter-*.md"))
    return {"workspace": str(workspace.root), "production_mode": state.production_mode if state else None,
        "intent_id": intent.intent_id if intent else None, "intent_state": intent.state.value if intent else None,
         "pending_operations": sorted(set(pending)), "operation_diagnostics": operation_diagnostics,
         "unknown_tasks": sorted(set(unknown)),
        "task_contexts": tasks, "active_work_orders": work_orders, "saved_chapters": saved,
        "state_errors": state_errors, "can_resume": not (pending or unknown or state_errors),
        "host_pause_verified": False, "pause_assurance": "仅停止本控制面的新派单，外部宿主动作须另核终态",
        "next_actions": ["核对待恢复事务／原生回执，未知不重发"] if pending or unknown or state_errors
                        else ["读取当前status／工单，从首个未完成动作接续，不从第一章重做"]}
