"""Atomic, backend-neutral image-call reservations; KP never calls a model here."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from knowledge_product_workflow.atomic_files import replace_file
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    unlink as _v_unlink,
)
from knowledge_product_workflow.verification_context import (
    write_bytes as _v_write_bytes,
)
from knowledge_product_workflow.verification_context import (
    write_open as _v_write_open,
)
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workspace import Workspace


class ImageCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    cache_key: str
    state: Literal["reserved", "dispatched", "completed", "failed", "unknown", "cancelled"] = "reserved"
    reserved_at: datetime
    prompt_sha256: str
    model: str | None = None
    backend: str = "host-image"
    size: str = "1672x941"
    quality: str = "high"
    reserved_cost: Decimal = Field(default=Decimal(0), ge=0)
    actual_cost: Decimal | None = Field(default=None, ge=0)
    usage_source: Literal["actual", "estimated", "unavailable"] = "unavailable"
    image_sha256: str | None = None
    image_path: str | None = None
    input_references: list[dict[str, str]] = Field(default_factory=list)
    material_origin: Literal["host-image", "synthetic", "unknown"] = "unknown"
    source_evidence_path: str | None = None
    source_evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_serializer(mode="wrap")
    def preserve_unknown_origin(self, handler):
        value = handler(self)
        if self.material_origin == "unknown":
            value.pop("material_origin", None)
        for name in ("source_evidence_path", "source_evidence_sha256"):
            if getattr(self, name) is None:
                value.pop(name, None)
        return value


class ImageSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str
    diagram_ids: list[str]
    chapter_number: int | None
    semantic_hashes: list[str]
    calls: list[ImageCall] = Field(default_factory=list)
    task_id: str | None = None
    inherited_attempts: int = Field(default=0, ge=0)

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        value = handler(self)
        if self.task_id is None and self.inherited_attempts == 0:
            value.pop("task_id", None)
            value.pop("inherited_attempts", None)
        return value


class ImageBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    mode: Literal["lean", "custom"] = "lean"
    revision: int = Field(default=1, ge=1)
    approved_by: str = Field(min_length=1)
    chapter_slots: int = Field(default=1, ge=1)
    book_slots: int = Field(default=6, ge=1)
    calls_per_slot: int = Field(default=2, ge=1)
    concurrency: Literal[1] = 1
    currency: str | None = None
    price_version: str | None = None
    money_limit: Decimal | None = Field(default=None, gt=0)
    slots: list[ImageSlot] = Field(default_factory=list)
    illustration_policy_revision: Literal[1, 2] = 1
    plan_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    approved_tasks: dict[str, int] = Field(default_factory=dict)
    authorized_total_calls: int | None = Field(default=None, ge=0)

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        value = handler(self)
        if self.illustration_policy_revision == 1:
            for key in ("illustration_policy_revision", "plan_sha256", "approved_tasks", "authorized_total_calls"):
                value.pop(key, None)
        else:
            value.pop("chapter_slots", None)
            value.pop("book_slots", None)
        return value

    @model_validator(mode="after")
    def valid_limits(self):
        self.approved_by = self.approved_by.strip()
        if not self.approved_by:
            raise ValueError("生图预算需要明确的确认身份")
        if self.illustration_policy_revision == 2:
            if self.plan_sha256 is None or self.authorized_total_calls is None or self.calls_per_slot != 2:
                raise ValueError("政策2须冻结批准任务、总调用预算和每任务两次上限")
            if any(chapter <= 0 for chapter in self.approved_tasks.values()):
                raise ValueError("批准任务必须绑定正章节号")
        elif self.mode == "lean" and (self.chapter_slots, self.book_slots, self.calls_per_slot) != (1, 6, 2):
            raise ValueError("精简模式固定为每章1张、全书6张、每图2次；更大额度需明确批准custom模式")
        if self.money_limit is not None and (not self.currency or not self.price_version):
            raise ValueError("货币预算须同时冻结currency与price_version")
        return self

    @property
    def reserved_money(self) -> Decimal:
        return sum((call.actual_cost if call.actual_cost is not None else call.reserved_cost
                    for slot in self.slots for call in slot.calls
                    if call.state != "cancelled"), Decimal(0))


def budget_path(workspace: Workspace) -> Path:
    return workspace.root / "runs" / "illustrations" / "budget.json"


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


@contextmanager
def _locked(workspace: Workspace):
    """OS locks release on crash; a leftover file never causes a 30-minute wait."""
    path = budget_path(workspace).with_suffix(".lock")
    _v_mkdir(path.parent,parents=True,exist_ok=True)
    with _v_write_open(path, "a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            yield
        except OSError as error:
            if not acquired:
                raise ValueError("生图预算正被另一进程预约；未发送请求，请稍后重试") from error
            raise
        finally:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _save(workspace: Workspace, budget: ImageBudget) -> None:
    path = budget_path(workspace)
    previous = path.read_bytes() if _v_is_file(path) else None
    temporary = path.with_name(f".{uuid.uuid4().hex}.tmp")
    _v_write_text(temporary, budget.model_dump_json(indent=2) + "\n", encoding="utf-8")
    try:
        replace_file(temporary, path)
    finally:
        _v_unlink(temporary, missing_ok=True)
    try:
        workspace.append_event("image_budget_updated", {"sha256": _hash(path), "revision": budget.revision})
    except OSError:
        if previous is None:
            _v_unlink(path, missing_ok=True)
        else:
            _v_write_bytes(path, previous)
        raise


def load_image_budget(workspace: Workspace) -> ImageBudget:
    path = budget_path(workspace)
    if not _v_is_file(path):
        raise ValueError("先运行kp freeze-illustration-budget冻结生图预算")
    budget = ImageBudget.model_validate_json(path.read_text(encoding="utf-8"))
    events = workspace.root / "runs" / "events.jsonl"
    records = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    seals = [record for record in records if record.get("event_type") == "image_budget_updated"]
    if not seals or seals[-1]["payload"]["sha256"] != _hash(path):
        raise ValueError("生图账本与受控事件不一致；禁止手写或清空消费历史")
    return budget


def _sent(slot: ImageSlot) -> int:
    return sum(call.state not in {"reserved", "cancelled"} for call in slot.calls)


def _current_task_plan(workspace: Workspace):
    from knowledge_product_workflow.illustration_tasks import approved_illustration_tasks
    policy, digest, tasks = approved_illustration_tasks(workspace)
    selected = {task.task_id: chapter for chapter, task in tasks
                if task.production == "create" and task.backend == "host-image"}
    return policy, digest, selected


def _assert_current_plan(workspace: Workspace, budget: ImageBudget) -> None:
    if budget.illustration_policy_revision == 2:
        policy, digest, selected = _current_task_plan(workspace)
        if policy != 2 or digest != budget.plan_sha256 or selected != budget.approved_tasks:
            raise ValueError("生图预算绑定的蓝图已过期；先确认修订并受控冻结新预算，不免费复位")


def _desired_policy(workspace: Workspace, explicit: int | None) -> int:
    from knowledge_product_workflow.frontmatter import parse_markdown_artifact
    from knowledge_product_workflow.workflows.curriculum import parse_blueprint_submission
    path = workspace.root / "planning/curriculum-blueprint.md"
    current = 1
    if _v_is_file(path):
        _meta, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
        blueprint = parse_blueprint_submission(body)
        if blueprint is not None:
            current = blueprint.illustration_policy_revision
    if explicit is not None and explicit != current:
        raise ValueError("预算政策必须与当前蓝图一致，不能用旧数量/任意参数绕过授权")
    return current


def freeze_image_budget(workspace: Workspace, *, approved_by: str, mode: str = "lean",
                        chapter_slots: int = 1, book_slots: int = 6, calls_per_slot: int = 2,
                        money_limit: Decimal | None = None, currency: str | None = None,
                        price_version: str | None = None,
                        illustration_policy_revision: int | None = None) -> ImageBudget:
    with _locked(workspace):
        previous = load_image_budget(workspace) if _v_is_file(budget_path(workspace)) else None
        policy = _desired_policy(workspace, illustration_policy_revision)
        if previous is not None and previous.illustration_policy_revision == 2 and policy == 1:
            raise ValueError("不能退回旧预算政策绕过图任务/消费绑定")
        if policy == 2:
            if calls_per_slot != 2:
                raise ValueError("政策2每任务固定最多两次，不能用custom参数自动扩额")
            _policy, plan_hash, selected = _current_task_plan(workspace)
            slots = previous.slots if previous else []
            for slot in slots:
                if slot.task_id is None:
                    candidates = set(slot.diagram_ids) & set(selected)
                    if len(candidates) > 1:
                        raise ValueError("旧槽身份匹配多个新任务；不能合并或丢历史")
                    if candidates:
                        slot.task_id = candidates.pop()
                        slot.inherited_attempts = _sent(slot)
                if slot.task_id in selected:
                    slot.chapter_number = selected[slot.task_id]
            if len([slot.task_id for slot in slots if slot.task_id]) != len({slot.task_id for slot in slots if slot.task_id}):
                raise ValueError("图任务映射冲突；不能合并已有槽位")
            consumed = sum(_sent(slot) for slot in slots)
            remaining = sum(max(0, 2 - sum(_sent(slot) for slot in slots if slot.task_id == task_id))
                            for task_id in selected)
            budget = ImageBudget(approved_by=approved_by, mode=mode, slots=slots,
                                 revision=previous.revision + 1 if previous else 1,
                                 illustration_policy_revision=2, plan_sha256=plan_hash,
                                 approved_tasks=selected, authorized_total_calls=consumed + remaining,
                                 money_limit=money_limit, currency=currency, price_version=price_version)
            _save(workspace, budget)
            return budget
        budget = ImageBudget(approved_by=approved_by, mode=mode, chapter_slots=chapter_slots,
                             book_slots=book_slots, calls_per_slot=calls_per_slot,
                             money_limit=money_limit, currency=currency, price_version=price_version,
                             slots=previous.slots if previous else [],
                             revision=previous.revision + 1 if previous else 1)
        if len(budget.slots) > budget.book_slots or any(sum(call.state not in {"reserved", "cancelled"}
                                                         for call in slot.calls) > budget.calls_per_slot
                                                       for slot in budget.slots):
            raise ValueError("新预算不能抹去消费或低于已有槽位/尝试")
        chapters = {slot.chapter_number for slot in budget.slots if slot.chapter_number is not None}
        if any(sum(slot.chapter_number == chapter for slot in budget.slots) > budget.chapter_slots
               for chapter in chapters):
            raise ValueError("新预算不能低于已有章节槽位；消费历史不会因缩减额度消失")
        _save(workspace, budget)
        return budget


def reserve_image(workspace: Workspace, *, diagram_id: str, chapter_number: int | None,
                  semantic_sha256: str, cache_key: str, prompt_sha256: str,
                  model: str | None = None, size: str = "1672x941", quality: str = "high",
                  reserved_cost: Decimal = Decimal(0), repair: bool = False,
                  input_references: list[dict[str, str]] | None = None,
                  task_id: str | None = None) -> tuple[ImageCall, bool]:
    with _locked(workspace):
        budget = load_image_budget(workspace)
        _assert_current_plan(workspace, budget)
        if budget.illustration_policy_revision == 2:
            if task_id is None or budget.approved_tasks.get(task_id) != chapter_number:
                raise ValueError("图任务未获当前已批准计划的生图授权，不能新增ID无限扩额")
        elif task_id is not None:
            raise ValueError("政策2图任务不能消费旧政策预算")
        matches = [item for item in budget.slots
                   if diagram_id in item.diagram_ids or semantic_sha256 in item.semantic_hashes
                   or (task_id is not None and item.task_id == task_id)]
        if len(matches) > 1:
            raise ValueError("图身份别名冲突；不能合并不同槽位或丢失调用历史")
        slot = matches[0] if matches else None
        if budget.illustration_policy_revision == 2 and slot is not None and slot.task_id != task_id:
            raise ValueError("同义图/旧槽身份不同；先受控保留原task_id和消费历史，不能开新额度")
        if slot is not None and slot.chapter_number != chapter_number:
            raise ValueError("图槽位已有章节归属；改ID或移章不能生成免费配额")
        if slot:
            for call in reversed(slot.calls):
                if not repair and call.cache_key == cache_key and call.state == "completed" and call.image_path:
                    image = workspace.root / call.image_path
                    if _v_is_file(image) and _hash(image) == call.image_sha256:
                        if diagram_id not in slot.diagram_ids:
                            slot.diagram_ids.append(diagram_id)
                            _save(workspace, budget)
                        return call, True
            attempts = sum(call.state not in {"cancelled", "reserved"} for call in slot.calls)
            if attempts >= budget.calls_per_slot:
                raise ValueError("同图槽位已用完两次调用；停止并返回预算/范围选择")
        for item in budget.slots:
            if any(call.state in {"reserved", "dispatched", "unknown"} for call in item.calls):
                raise ValueError("已有未结算生图预约；先查状态/结算，不自动重复下单")
        if slot is None:
            if budget.illustration_policy_revision == 1 and len(budget.slots) >= budget.book_slots:
                raise ValueError("全书AI图槽位达到上限；取消旧图不清空消费")
            if budget.illustration_policy_revision == 1 and chapter_number is not None and sum(item.chapter_number == chapter_number
                                                  for item in budget.slots) >= budget.chapter_slots:
                raise ValueError("当前章节AI图已达1张上限")
            slot = ImageSlot(slot_id=f"image-slot-{uuid.uuid4().hex[:16]}", diagram_ids=[diagram_id],
                             chapter_number=chapter_number, semantic_hashes=[semantic_sha256], task_id=task_id)
            budget.slots.append(slot)
        if budget.money_limit is not None and (reserved_cost <= 0 or budget.reserved_money + reserved_cost > budget.money_limit):
            raise ValueError("单次费用预约缺失或超出货币预算；不自动提价")
        if budget.illustration_policy_revision == 2 and sum(_sent(item) for item in budget.slots) >= budget.authorized_total_calls:
            raise ValueError("已达到批准的总发送预算；停止，不按新任务/新工作区重置")
        if diagram_id not in slot.diagram_ids:
            slot.diagram_ids.append(diagram_id)
        if semantic_sha256 not in slot.semantic_hashes:
            slot.semantic_hashes.append(semantic_sha256)
        call = ImageCall(call_id=f"image-call-{uuid.uuid4().hex[:16]}", cache_key=cache_key,
                         reserved_at=datetime.now(UTC), prompt_sha256=prompt_sha256,
                         model=model, size=size, quality=quality, reserved_cost=reserved_cost,
                         input_references=input_references or [])
        slot.calls.append(call)
        _save(workspace, budget)
        return call, False


def record_image_call(workspace: Workspace, call_id: str, *, state: str,
                      image_path: Path | None = None, actual_cost: Decimal | None = None,
                      usage_source: str = "unavailable", material_origin: str | None = None,
                      source_evidence: Path | None = None) -> ImageCall:
    with _locked(workspace):
        if actual_cost is not None and actual_cost < 0:
            raise ValueError("实际费用不能为负；不修改原账本")
        budget = load_image_budget(workspace)
        call = next((call for slot in budget.slots for call in slot.calls if call.call_id == call_id), None)
        if call is None:
            raise ValueError("未找到该生图授权")
        allowed = {"reserved": {"dispatched", "cancelled"},
                   "dispatched": {"completed", "failed", "unknown"},
                   "unknown": {"completed", "failed"}}
        if state not in allowed.get(call.state, set()):
            raise ValueError("生图凭证已消费或状态迁移不合法；禁止隐式重试")
        if state == "dispatched":
            _assert_current_plan(workspace, budget)
            if budget.illustration_policy_revision == 2:
                slot = next(slot for slot in budget.slots if call in slot.calls)
                if slot.task_id not in budget.approved_tasks or _sent(slot) >= 2:
                    raise ValueError("图任务已停用或尝试耗尽，旧预约不能继续发送")
                if sum(_sent(item) for item in budget.slots) >= budget.authorized_total_calls:
                    raise ValueError("已达到批准的总发送预算，不发送")
        if usage_source == "unavailable" and actual_cost is not None:
            raise ValueError("未知费用不能填写实际金额或0；保留预约及unavailable")
        if state == "completed":
            if image_path is None:
                raise ValueError("完成调用须提供实际返回的工作区内图片")
            image = image_path.resolve()
            if not image.is_relative_to(workspace.root.resolve()) or not _v_is_file(image):
                raise ValueError("生图返回资产必须先保存在当前工作区")
            call.image_sha256 = _hash(image)
            call.image_path = image.relative_to(workspace.root.resolve()).as_posix()
            if material_origin is not None:
                if material_origin not in {"host-image", "synthetic", "unknown"}:
                    raise ValueError("material_origin只声明host-image/synthetic/unknown，不证明实际AI来源")
                call.material_origin = material_origin
            if source_evidence is not None:
                evidence = source_evidence.resolve()
                if (not evidence.is_relative_to(workspace.root.resolve()) or not _v_is_file(evidence)
                        or evidence.stat().st_size == 0):
                    raise ValueError("来源回执须是当前工作区内保留的非空原工具／会话证据")
                call.source_evidence_path = evidence.relative_to(workspace.root.resolve()).as_posix()
                call.source_evidence_sha256 = _hash(evidence)
        elif material_origin is not None or source_evidence is not None:
            raise ValueError("只有completed原件能记录material_origin声明及来源回执")
        call.state = state
        call.actual_cost = actual_cost
        call.usage_source = usage_source
        if usage_source not in {"actual", "estimated", "unavailable"}:
            raise ValueError("usage_source必须明确actual/estimated/unavailable")
        _save(workspace, budget)
        return call


def image_call_for_asset(workspace: Workspace, call_id: str, diagram_id: str, image: Path) -> ImageCall:
    budget = load_image_budget(workspace)
    slot = next((slot for slot in budget.slots if diagram_id in slot.diagram_ids), None)
    call = next((call for call in slot.calls if call.call_id == call_id), None) if slot else None
    if call is None or call.state != "completed" or call.image_sha256 != _hash(image):
        raise ValueError("图片与已完成的受控生图调用不匹配")
    return call


def image_budget_findings(workspace: Workspace):
    """Unfinished image calls cannot disappear from a delivery cost boundary."""
    from knowledge_product_workflow.quality import QualityFinding, Severity
    if not _v_is_file(budget_path(workspace)):
        return []
    try:
        budget = load_image_budget(workspace)
    except (OSError, ValueError, KeyError) as error:
        return [QualityFinding(code="ILLUSTRATION_BUDGET_INVALID", severity=Severity.BLOCKER,
                               location="runs/illustrations/budget.json", message=str(error))]
    findings = []
    if budget.illustration_policy_revision == 2:
        try:
            _assert_current_plan(workspace, budget)
        except ValueError as error:
            findings.append(QualityFinding(code="ILLUSTRATION_PLAN_STALE", severity=Severity.BLOCKER,
                                           location="runs/illustrations/budget.json", message=str(error)))
    if any(call.state in {"reserved", "dispatched", "unknown"} for slot in budget.slots for call in slot.calls):
        findings.append(QualityFinding(code="ILLUSTRATION_CALL_UNFINISHED", severity=Severity.BLOCKER,
                                       location="runs/illustrations/budget.json", message="有未结算生图调用，先核对/记录状态，不重复下单或假写0费用"))
    exceeded = (len(budget.slots) > budget.book_slots or any(_sent(slot) > budget.calls_per_slot for slot in budget.slots)) if budget.illustration_policy_revision == 1 else (
        sum(_sent(slot) for slot in budget.slots) > budget.authorized_total_calls
        or any(_sent(slot) > max(2, slot.inherited_attempts) for slot in budget.slots))
    if exceeded:
        findings.append(QualityFinding(code="ILLUSTRATION_BUDGET_EXCEEDED", severity=Severity.BLOCKER,
                                       location="runs/illustrations/budget.json", message="生图消费超过冻结上限，不能发布"))
    return findings
