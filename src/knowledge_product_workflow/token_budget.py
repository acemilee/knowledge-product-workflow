"""Deterministic token-budget accounting for host-driven model actions."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.workspace import Workspace

TOKEN_BUDGET_FILENAME = "token-budget.json"
TOKEN_BUDGET_SCHEMA_VERSION = 1
_WARNING_RATIO = 0.80
_DEFAULT_LIMITS: dict[TokenMode, tuple[int, int, int]] = {
    "lean": (60_000, 12_000, 18_000),
    "standard": (120_000, 25_000, 36_000),
    "premium": (240_000, 50_000, 72_000),
}


class TokenMode(StrEnum):
    """User-selected quality and cost envelope for one book workspace."""

    LEAN = "lean"
    STANDARD = "standard"
    PREMIUM = "premium"


class TokenActionState(StrEnum):
    AUTHORIZED = "authorized"
    COMPLETED = "completed"
    FAILED = "failed"


class TokenBudgetActionRequest(BaseModel):
    """Host-supplied estimate for one model call before it is made."""

    model_config = ConfigDict(extra="forbid")

    skill: str = Field(pattern=r"^kp-[a-z0-9-]+$")
    action: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_number: int | None = Field(default=None, gt=0)
    estimated_input_tokens: int = Field(gt=0)
    estimated_output_tokens: int = Field(ge=0)
    optional: bool = False

    @property
    def estimated_total_tokens(self) -> int:
        return self.estimated_input_tokens + self.estimated_output_tokens


class TokenBudgetAction(BaseModel):
    """One immutable authorization lifecycle inside the budget ledger."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(pattern=r"^token-[0-9a-f]{16}$")
    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill: str
    action: str
    artifact_sha256: str
    chapter_number: int | None = None
    optional: bool
    attempt: int = Field(ge=1, le=2)
    estimated_input_tokens: int = Field(ge=0)
    estimated_output_tokens: int = Field(ge=0)
    state: TokenActionState = TokenActionState.AUTHORIZED
    authorized_at: datetime
    completed_at: datetime | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    usage_source: Literal["actual", "estimated", "unavailable"] | None = None

    @property
    def reserved_tokens(self) -> int:
        return self.estimated_input_tokens + self.estimated_output_tokens

    @property
    def consumed_tokens(self) -> int:
        if self.state is TokenActionState.AUTHORIZED:
            return self.reserved_tokens
        if self.input_tokens is None or self.output_tokens is None:
            return self.reserved_tokens
        return self.input_tokens + self.output_tokens


class TokenBudget(BaseModel):
    """A frozen, workspace-local token envelope and its auditable actions."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=TOKEN_BUDGET_SCHEMA_VERSION, ge=1)
    mode: TokenMode = TokenMode.LEAN
    book_limit_tokens: int = Field(gt=0)
    chapter_limit_tokens: int = Field(gt=0)
    action_limit_tokens: int = Field(gt=0)
    warning_ratio: float = Field(default=_WARNING_RATIO, gt=0, lt=1)
    frozen_at: datetime
    entries: list[TokenBudgetAction] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def require_current_schema(cls, value: int) -> int:
        if value != TOKEN_BUDGET_SCHEMA_VERSION:
            raise ValueError("不支持的 token 预算版本")
        return value

    @property
    def used_tokens(self) -> int:
        return sum(entry.consumed_tokens for entry in self.entries if entry.state is not TokenActionState.FAILED)

    @property
    def usage_ratio(self) -> float:
        return self.used_tokens / self.book_limit_tokens


class TokenBudgetPreflight(BaseModel):
    """Deterministic preflight result consumed by a host before a model call."""

    model_config = ConfigDict(extra="forbid")

    cache_hit: bool
    optional_actions_allowed: bool
    action: TokenBudgetAction | None = None
    warnings: list[str] = Field(default_factory=list)


class TokenBudgetExceededError(ValueError):
    """Raised before a new host model call would exceed the frozen envelope."""


def token_budget_path(workspace: Workspace) -> Path:
    return workspace.root / "runs" / TOKEN_BUDGET_FILENAME


def _default_budget(mode: TokenMode) -> TokenBudget:
    book_limit, chapter_limit, action_limit = _DEFAULT_LIMITS[mode]
    return TokenBudget(
        mode=mode,
        book_limit_tokens=book_limit,
        chapter_limit_tokens=chapter_limit,
        action_limit_tokens=action_limit,
        frozen_at=datetime.now(UTC),
    )


def _atomic_write(path: Path, payload: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_budget(workspace: Workspace, budget: TokenBudget) -> TokenBudget:
    path = token_budget_path(workspace)
    _atomic_write(path, budget.model_dump_json(indent=2) + "\n")
    return budget


def initialize_token_budget(
    workspace: Workspace, *, mode: TokenMode = TokenMode.LEAN, overwrite: bool = False
) -> TokenBudget:
    """Create the default frozen budget for a newly managed workspace."""
    path = token_budget_path(workspace)
    if path.is_file() and not overwrite:
        return load_token_budget(workspace)
    budget = _write_budget(workspace, _default_budget(mode))
    workspace.append_event(
        "token_budget_initialized",
        {"mode": budget.mode.value, "book_limit_tokens": budget.book_limit_tokens},
    )
    return budget


def load_token_budget(workspace: Workspace) -> TokenBudget:
    """Load the current token ledger without creating a new budget implicitly."""
    path = token_budget_path(workspace)
    try:
        return TokenBudget.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as error:
        raise ValueError("token 预算账本格式无效") from error


def freeze_token_budget(
    workspace: Workspace,
    *,
    mode: TokenMode,
    book_limit_tokens: int | None = None,
    chapter_limit_tokens: int | None = None,
    action_limit_tokens: int | None = None,
) -> TokenBudget:
    """Freeze an explicit envelope before batch model work begins."""
    current = load_token_budget(workspace)
    if current.entries:
        raise ValueError("已有 token 调用记录，不能重新冻结预算模式或上限")
    defaults = _DEFAULT_LIMITS[mode]
    budget = TokenBudget(
        mode=mode,
        book_limit_tokens=book_limit_tokens or defaults[0],
        chapter_limit_tokens=chapter_limit_tokens or defaults[1],
        action_limit_tokens=action_limit_tokens or defaults[2],
        frozen_at=datetime.now(UTC),
    )
    _write_budget(workspace, budget)
    workspace.append_event(
        "token_budget_frozen",
        {
            "mode": budget.mode.value,
            "book_limit_tokens": budget.book_limit_tokens,
            "chapter_limit_tokens": budget.chapter_limit_tokens,
            "action_limit_tokens": budget.action_limit_tokens,
        },
    )
    return budget


def _cache_key(workspace: Workspace, request: TokenBudgetActionRequest, mode: TokenMode) -> str:
    payload = "\n".join(
        (str(workspace.root), request.artifact_sha256, request.skill, request.action, mode.value)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chapter_tokens(budget: TokenBudget, chapter_number: int | None) -> int:
    if chapter_number is None:
        return 0
    return sum(
        entry.consumed_tokens
        for entry in budget.entries
        if entry.chapter_number == chapter_number and entry.state is not TokenActionState.FAILED
    )


def _action_attempts(budget: TokenBudget, cache_key: str) -> int:
    return sum(entry.cache_key == cache_key for entry in budget.entries)


def _optional_actions_allowed(budget: TokenBudget) -> bool:
    return budget.usage_ratio < budget.warning_ratio


def preflight_model_action(workspace: Workspace, request: TokenBudgetActionRequest) -> TokenBudgetPreflight:
    """Reserve a bounded host call or reuse a current completed result by SHA cache key."""
    budget = load_token_budget(workspace)
    cache_key = _cache_key(workspace, request, budget.mode)
    completed = next(
        (
            entry
            for entry in reversed(budget.entries)
            if entry.cache_key == cache_key and entry.state is TokenActionState.COMPLETED
        ),
        None,
    )
    if completed is not None:
        workspace.append_event("token_budget_cache_hit", {"action_id": completed.action_id})
        return TokenBudgetPreflight(
            cache_hit=True,
            optional_actions_allowed=_optional_actions_allowed(budget),
            action=completed,
        )

    pending = next(
        (
            entry
            for entry in reversed(budget.entries)
            if entry.cache_key == cache_key and entry.state is TokenActionState.AUTHORIZED
        ),
        None,
    )
    if pending is not None:
        return TokenBudgetPreflight(
            cache_hit=False,
            optional_actions_allowed=_optional_actions_allowed(budget),
            action=pending,
            warnings=["该模型动作已有未完成预约；请完成或标记失败后再继续。"],
        )

    optional_actions_allowed = _optional_actions_allowed(budget)
    if request.optional and not optional_actions_allowed:
        return TokenBudgetPreflight(
            cache_hit=False,
            optional_actions_allowed=False,
            warnings=["已达到 token 预算预警线，禁止启动新的可选模型动作。"],
        )

    estimate = request.estimated_total_tokens
    if estimate > budget.action_limit_tokens:
        raise TokenBudgetExceededError("单次模型动作预计 token 超出已冻结的动作上限")
    if budget.used_tokens + estimate > budget.book_limit_tokens:
        raise TokenBudgetExceededError("模型动作预计 token 超出全书预算；请减少范围或提高预算后重新确认")
    if request.chapter_number is not None and _chapter_tokens(budget, request.chapter_number) + estimate > budget.chapter_limit_tokens:
        raise TokenBudgetExceededError("模型动作预计 token 超出当前章节预算；请减少范围或提高预算后重新确认")
    attempt = _action_attempts(budget, cache_key) + 1
    if attempt > 2:
        raise TokenBudgetExceededError("同一模型动作已达到一次自动重试上限；请停止自动运行并报告。")

    action = TokenBudgetAction(
        action_id=f"token-{uuid.uuid4().hex[:16]}",
        cache_key=cache_key,
        skill=request.skill,
        action=request.action,
        artifact_sha256=request.artifact_sha256,
        chapter_number=request.chapter_number,
        optional=request.optional,
        attempt=attempt,
        estimated_input_tokens=request.estimated_input_tokens,
        estimated_output_tokens=request.estimated_output_tokens,
        authorized_at=datetime.now(UTC),
    )
    budget.entries.append(action)
    _write_budget(workspace, budget)
    workspace.append_event(
        "token_budget_action_authorized",
        {
            "action_id": action.action_id,
            "skill": action.skill,
            "action": action.action,
            "chapter_number": action.chapter_number,
            "estimated_tokens": action.reserved_tokens,
            "attempt": action.attempt,
        },
    )
    warnings = []
    if budget.used_tokens / budget.book_limit_tokens >= budget.warning_ratio:
        warnings.append("本次动作后将达到 token 预算预警线；不得启动新的可选模型动作。")
    return TokenBudgetPreflight(
        cache_hit=False,
        optional_actions_allowed=optional_actions_allowed,
        action=action,
        warnings=warnings,
    )


def _find_action(budget: TokenBudget, action_id: str) -> TokenBudgetAction:
    for action in budget.entries:
        if action.action_id == action_id:
            return action
    raise ValueError("找不到 token 预算动作记录")


def complete_model_action(
    workspace: Workspace,
    action_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    usage_source: Literal["actual", "estimated", "unavailable"],
) -> TokenBudgetAction:
    """Close a reserved action with host-reported or explicitly estimated usage."""
    budget = load_token_budget(workspace)
    action = _find_action(budget, action_id)
    if action.state is not TokenActionState.AUTHORIZED:
        raise ValueError("token 预算动作不是可完成状态")
    action.state = TokenActionState.COMPLETED
    action.completed_at = datetime.now(UTC)
    action.input_tokens = input_tokens
    action.output_tokens = output_tokens
    action.usage_source = usage_source
    _write_budget(workspace, budget)
    workspace.append_event(
        "token_budget_action_completed",
        {
            "action_id": action.action_id,
            "usage_source": usage_source,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )
    return action


def fail_model_action(workspace: Workspace, action_id: str) -> TokenBudgetAction:
    """Close an action that did not yield a reusable model result."""
    budget = load_token_budget(workspace)
    action = _find_action(budget, action_id)
    if action.state is not TokenActionState.AUTHORIZED:
        raise ValueError("token 预算动作不是可失败状态")
    action.state = TokenActionState.FAILED
    action.completed_at = datetime.now(UTC)
    _write_budget(workspace, budget)
    workspace.append_event("token_budget_action_failed", {"action_id": action.action_id, "attempt": action.attempt})
    return action


def token_budget_findings(workspace: Workspace, *, require_budget: bool = False) -> list[QualityFinding]:
    """Return delivery blockers for invalid, unfinished, or exhausted token accounting."""
    path = token_budget_path(workspace)
    if not path.is_file():
        if not require_budget:
            return []
        return [
            QualityFinding(
                code="TOKEN_BUDGET_MISSING",
                severity=Severity.BLOCKER,
                location="runs/token-budget.json",
                message="当前工作区缺少已冻结的 token 预算账本，不能确认成本边界。",
            )
        ]
    try:
        budget = load_token_budget(workspace)
    except ValueError:
        return [
            QualityFinding(
                code="TOKEN_BUDGET_INVALID",
                severity=Severity.BLOCKER,
                location="runs/token-budget.json",
                message="token 预算账本无效，无法确认模型调用成本。",
            )
        ]
    findings: list[QualityFinding] = []
    for action in budget.entries:
        if action.state is TokenActionState.AUTHORIZED:
            findings.append(
                QualityFinding(
                    code="TOKEN_BUDGET_ACTION_UNFINISHED",
                    severity=Severity.BLOCKER,
                    location="runs/token-budget.json",
                    message="存在未完成的模型调用预约；请记录用量或标记失败后再交付。",
                )
            )
        elif action.state is TokenActionState.FAILED:
            findings.append(
                QualityFinding(
                    code="TOKEN_BUDGET_ACTION_FAILED",
                    severity=Severity.BLOCKER,
                    location="runs/token-budget.json",
                    message="存在失败的模型调用；只能在一次受控重试后继续，不能静默跳过。",
                )
            )
    if budget.used_tokens > budget.book_limit_tokens:
        findings.append(
            QualityFinding(
                code="TOKEN_BUDGET_EXHAUSTED",
                severity=Severity.BLOCKER,
                location="runs/token-budget.json",
                message="模型调用已超过全书 token 预算；请减少范围、提高预算或停止交付。",
            )
        )
    return findings
