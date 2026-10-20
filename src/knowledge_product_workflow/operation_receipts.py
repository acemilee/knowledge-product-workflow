"""Bounded operation observations and control continuations; domain journals stay authoritative."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from knowledge_product_workflow.atomic_files import replace_file
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import write_open, write_text
from knowledge_product_workflow.workspace import Workspace

Phase = Literal[
    "prepared",
    "running",
    "domain_committed_control_pending",
    "completed",
    "failed_before_commit",
    "unknown",
    "recovery_required",
    "closed_failed",
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class OperationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^op-[a-z0-9-]+$")
    workspace: str
    book_id: str
    action: Literal["submit-chapter", "assemble"]
    chapter_number: int = Field(ge=0)
    owner_pid: int
    owner_nonce: str
    work_order_id: str | None = None
    input_manifest_sha256: str | None = None
    order_expires_at: str | None = None
    phase: Phase = "prepared"
    revision: int = 1
    created_at: str = Field(default_factory=_now)
    last_progress: str = Field(default_factory=_now)
    transaction_id: str | None = None
    successor_work_order_id: str | None = None
    error: str | None = None
    domain_stage: Literal['precheck', 'transaction_preparing'] | None = None
    failure_evidence_sha256: str | None = None

    @model_validator(mode='after')
    def action_scope(self):
        if (self.action == 'assemble') != (self.chapter_number == 0):
            raise ValueError('出版操作使用全书范围0；章节提交仍须正章节号')
        return self


_ACTIVE: ContextVar[tuple[Workspace, OperationReceipt, object] | None] = ContextVar(
    "kp_operation", default=None
)


def _fault(phase: str) -> None:
    """In-process test boundary; no product switch for injecting faults."""


def receipt_path(workspace: Workspace, operation_id: str) -> Path:
    if not re.fullmatch(r"op-[a-z0-9-]+", operation_id):
        raise ValueError("操作ID越界")
    path = workspace.root / "runs/orchestration/operations" / f"{operation_id}.json"
    if not path.resolve().is_relative_to(workspace.root.resolve()):
        raise ValueError("操作路径物理越界")
    return path


def _load(workspace: Workspace, operation_id: str) -> OperationReceipt:
    from knowledge_product_workflow.workspace_transactions import _book
    path = receipt_path(workspace, operation_id)
    receipt = OperationReceipt.model_validate_json(_bounded_read(path, 16384))
    if (
        receipt.operation_id != operation_id
        or receipt.workspace != str(workspace.root.resolve())
        or receipt.book_id != _book(workspace)
    ):
        raise ValueError("操作不属于当前书稿／工作区")
    return receipt


def _bounded_read(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit)
        if os.fstat(stream.fileno()).st_size > limit:
            raise ValueError("元数据超过轻查询上限，需显式诊断")
        return data


def _save(workspace: Workspace, receipt: OperationReceipt, **updates) -> OperationReceipt:
    receipt = receipt.model_copy(
        update={**updates, "revision": receipt.revision + 1, "last_progress": _now()}
    )
    path = receipt_path(workspace, receipt.operation_id)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    write_text(temporary, receipt.model_dump_json(indent=2), encoding="utf-8")
    replace_file(temporary, path)
    return receipt


def operation_status(workspace: Workspace, operation_id: str) -> dict:
    """At most one bounded receipt and one bounded journal. No glob, write or deep status."""
    receipt_path(workspace, operation_id)  # Reject unsafe identifiers even when files are absent.
    try:
        receipt = _load(workspace, operation_id)
        result = receipt.model_dump(mode="json")
        if receipt.transaction_id and receipt.phase != "completed":
            if not re.fullmatch(r"tx-[a-z0-9-]+", receipt.transaction_id):
                raise ValueError("事务ID无效")
            path = (
                workspace.root
                / "runs/orchestration/transactions"
                / f"{receipt.transaction_id}.json"
            )
            if not path.resolve().is_relative_to(workspace.root.resolve()):
                raise ValueError("事务元数据物理越界")
            journal = json.loads(_bounded_read(path, 49152))
            if (
                journal["operation_id"] != receipt.transaction_id
                or journal.get("metadata", {}).get("control_operation_id") != operation_id
                or journal.get("book_id") != receipt.book_id
            ):
                raise ValueError("原事务绑定不匹配")
            result["domain_journal_state"] = journal["state"]
            # completed storage alone may still need deterministic export finalization.
            if journal["state"] == "completed" and receipt.phase in {
                "prepared",
                "running",
                "unknown",
            }:
                result["phase"] = "recovery_required"
                result["reason"] = "journal_saved_finish_original_exports_and_control_only"
        result["next_action"] = (
            "none"
            if result["phase"] in {"completed", "closed_failed"}
            else "recover-operation"
            if receipt.transaction_id
            else "close-failed-operation"
            if receipt.phase == 'failed_before_commit' and receipt.domain_stage == 'precheck' and receipt.failure_evidence_sha256
            else "verify_original_owner_do_not_resubmit"
        )
        result["owner_pid_is_completion_evidence"] = False
        action = result['next_action']
        if action in {'recover-operation', 'close-failed-operation'}:
            result['next_argv'] = ['kp', action, '--workspace', str(workspace.root), '--operation-id', operation_id]
            if action == 'close-failed-operation':
                result['next_argv'] += ['--reason', '<核对失败证据后的实际原因>']
                result['closure_eligibility'] = 'candidate_only_full_seal_and_journal_checks_still_required'
        return result
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {
            "operation_id": operation_id,
            "phase": "recovery_required",
            "error": str(error),
            "next_action": "explicit_diagnosis_do_not_resubmit",
        }


@contextmanager
def operation_scope(
    workspace: Workspace,
    *,
    action: str,
    chapter_number: int,
    work_order_id: str | None = None,
    operation_id: str | None = None,
    emit=None,
):
    """Called after cheap input checks and captured admission. Domain APIs still authorize writes."""
    from knowledge_product_workflow.runtime import _is_live, read_orchestration_work_order

    if _ACTIVE.get() is not None:
        raise ValueError("不能嵌套或重复拥有操作")
    from knowledge_product_workflow.workspace_transactions import _book
    receipt = OperationReceipt(
        operation_id=operation_id or "op-" + uuid4().hex,
        workspace=str(workspace.root.resolve()),
        book_id=_book(workspace),
        action=action,
        chapter_number=chapter_number,
        owner_pid=os.getpid(),
        owner_nonce=uuid4().hex,
        work_order_id=work_order_id,
        domain_stage='precheck',
    )
    if work_order_id:
        order = read_orchestration_work_order(workspace, work_order_id)
        if (
            not _is_live(order)
            or action not in order.allowed_commands
            or order.target_chapter_number not in {None, chapter_number}
        ):
            raise ValueError("原工单没有本动作权限")
        receipt.input_manifest_sha256 = order.input_manifest_sha256
        receipt.order_expires_at = order.expires_at.isoformat()
    path = receipt_path(workspace, receipt.operation_id)
    _v_mkdir(path.parent, parents=True, exist_ok=True)
    try:
        with write_open(path, "x", encoding="utf-8") as stream:
            stream.write(receipt.model_dump_json(indent=2))
    except FileExistsError as error:
        raise ValueError("已有该操作；先查询原回执，禁止重发正文") from error
    receipt = _save(workspace, receipt, phase="running")
    token = _ACTIVE.set((workspace, receipt, emit))
    if emit:
        emit(operation_status(workspace, receipt.operation_id))
    try:
        yield receipt
    except BaseException as error:
        try:
            current = _load(workspace, receipt.operation_id)
        except (OSError, ValueError) as receipt_error:
            error.add_note(f'原回执无法安全更新：{receipt_error}')
            raise error from receipt_error
        if current.phase not in {"completed", "domain_committed_control_pending"}:
            phase = "unknown" if current.transaction_id else "failed_before_commit"
            evidence = None
            if current.domain_stage == 'precheck' and not current.transaction_id:
                evidence = hashlib.sha256(json.dumps([current.operation_id,current.workspace,
                    current.book_id,current.owner_nonce,'precheck',type(error).__name__],ensure_ascii=False).encode()).hexdigest()
                workspace.append_event('operation_precheck_failed', {'operation_id':current.operation_id,'evidence':evidence})
            _save(workspace, current, phase=phase, error=str(error)[:1000], failure_evidence_sha256=evidence)
        raise
    finally:
        _ACTIVE.reset(token)


def domain_transaction_preparing(workspace: Workspace, kind: str) -> None:
    active = _ACTIVE.get()
    if active and active[0].root == workspace.root:
        _save(workspace, _load(workspace, active[1].operation_id), domain_stage='transaction_preparing')


def close_failed_operation(workspace: Workspace, operation_id: str, *, reason: str) -> OperationReceipt:
    """Close only a durably observed pre-domain failure; never infer success from no tx."""
    from knowledge_product_workflow.production_tasks import production_lock
    from knowledge_product_workflow.runtime import require_workflow_mutation_allowed
    require_workflow_mutation_allowed(workspace, 'recover-failed-operation')
    if not reason.strip():
        raise ValueError('失败结案需要说明原因')
    with production_lock(workspace):
        receipt = _load(workspace, operation_id)
        if receipt.phase == 'closed_failed':
            return receipt
        if (receipt.phase != 'failed_before_commit' or receipt.transaction_id
                or receipt.domain_stage != 'precheck' or not receipt.failure_evidence_sha256):
            raise ValueError('缺少新操作的提交前失败证据；保留未知或恢复原事务，不得结案')
        events = [json.loads(line) for line in (workspace.root/'runs/events.jsonl').read_text(encoding='utf8').splitlines() if line.strip()]
        if not any(e.get('event_type') == 'operation_precheck_failed' and e['payload'].get('operation_id') == operation_id
                   and e['payload'].get('evidence') == receipt.failure_evidence_sha256 for e in events):
            raise ValueError('提交前失败证据封印缺失，不得结案')
        journals = list((workspace.root/'runs/orchestration/transactions').glob('*.json'))
        if len(journals) > 1024:
            raise ValueError('事务诊断超过边界，保留原状态')
        for path in journals:
            journal = json.loads(_bounded_read(path, 49152))
            if journal.get('metadata',{}).get('control_operation_id') == operation_id:
                raise ValueError('存在未链接事务，必须核对原事务，不能结案')
        closed = _save(workspace, receipt, phase='closed_failed')
        workspace.append_event('operation_failed_closed',{'operation_id':operation_id,'reason':reason.strip(),'result':'failed_not_committed'})
        return closed


def link_transaction(workspace: Workspace, transaction) -> None:
    """Persist the exact existing journal ID before its first domain replacement."""
    active = _ACTIVE.get()
    if active is None:
        return
    owned, receipt, _ = active
    if transaction.kind != ("delivery_assembly" if receipt.action == "assemble" else "chapter_submission"):
        return  # Derived exports keep their original journal, not a second domain submission.
    if owned.root.resolve() != workspace.root.resolve():
        raise ValueError("操作事务范围不匹配")
    if transaction.scope_chapters != ([] if receipt.action == "assemble" else [receipt.chapter_number]):
        raise ValueError("操作事务跨章")
    transaction.metadata["control_operation_id"] = receipt.operation_id
    from knowledge_product_workflow.workspace_transactions import _save as save_transaction

    save_transaction(workspace, transaction)
    _save(
        workspace, _load(workspace, receipt.operation_id), transaction_id=transaction.operation_id
    )


def transaction_completed(workspace: Workspace, transaction) -> None:
    operation_id = transaction.metadata.get("control_operation_id")
    if not operation_id:
        return
    receipt = _load(workspace, operation_id)
    if receipt.transaction_id != transaction.operation_id:
        raise ValueError("操作与提交事务不一致")
    if receipt.phase != "completed":
        receipt = _save(workspace, receipt, phase="domain_committed_control_pending", error=None)
        active = _ACTIVE.get()
        if active and active[2]:
            active[2](receipt.model_dump(mode="json"))
        _fault("domain_committed")


def _committed_evidence(workspace: Workspace, receipt: OperationReceipt):
    from knowledge_product_workflow.production_dependencies import binding_digest
    from knowledge_product_workflow.workspace_transactions import WorkspaceTransaction, _record_path

    if not receipt.transaction_id:
        raise ValueError("领域完成未知；先核原owner，不得重发")
    tx = WorkspaceTransaction.model_validate_json(
        _record_path(workspace, receipt.transaction_id).read_bytes()
    )
    if (
        tx.state != "completed"
        or tx.kind != ("delivery_assembly" if receipt.action == "assemble" else "chapter_submission")
        or tx.book_id != receipt.book_id
        or tx.scope_chapters != ([] if receipt.action == "assemble" else [receipt.chapter_number])
        or tx.metadata.get("control_operation_id") != receipt.operation_id
    ):
        raise ValueError("原领域事务尚未确认完成")
    for relative, item in tx.outputs.items():
        target = (workspace.root / relative).resolve()
        if not target.is_relative_to(workspace.root.resolve()):
            raise ValueError("事务输出越界")
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if _v_is_file(target) else None
        if actual != item["sha256"]:
            raise ValueError("已提交输出被外部修改，保留原操作")
    for key, expected in tx.input_bindings.items():
        if key not in tx.outputs and binding_digest(workspace, key) != expected:
            raise ValueError("原提交依赖已变化，不能补旧控制面")
    return tx


def verify_committed_completion(
    workspace: Workspace, operation_id: str, order, action: str
) -> None:
    """An expired order can only be closed for this already-committed transaction."""
    from knowledge_product_workflow.runtime import WorkOrderLifecycle

    receipt = _load(workspace, operation_id)
    _committed_evidence(workspace, receipt)
    if (
        receipt.work_order_id != order.work_order_id
        or receipt.action != action
        or receipt.input_manifest_sha256 != order.input_manifest_sha256
        or receipt.order_expires_at != order.expires_at.isoformat()
        or datetime.fromisoformat(receipt.created_at) > order.expires_at
        or order.lifecycle
        not in {WorkOrderLifecycle.PENDING, WorkOrderLifecycle.ACTIVE, WorkOrderLifecycle.COMPLETED}
    ):
        raise ValueError("原操作授权已改变或撤销；不能以回执授予新写权")
    if order.lease and order.lifecycle.value != "completed":
        from knowledge_product_workflow.runtime import _lease_path, _read_lease

        lease = _read_lease(_lease_path(workspace, receipt.chapter_number))
        if (
            not lease
            or lease.lease_id != order.lease.lease_id
            or lease.owner_work_order_id != order.work_order_id
            or lease.state not in {WorkOrderLifecycle.ACTIVE, WorkOrderLifecycle.PENDING}
        ):
            raise ValueError("原租约被撤销或替换，禁止自动控制续接")


def finish_operation(workspace: Workspace, operation_id: str) -> OperationReceipt:
    from contextlib import nullcontext

    from knowledge_product_workflow.production_tasks import controller_lock_owned, production_lock

    with nullcontext() if controller_lock_owned(workspace) else production_lock(workspace):
        return _finish_operation(workspace, operation_id)


def _finish_operation(workspace: Workspace, operation_id: str) -> OperationReceipt:
    receipt = _load(workspace, operation_id)
    if receipt.phase == "completed":
        return receipt
    tx = _committed_evidence(workspace, receipt)
    _fault("before_control")
    successor = None
    if receipt.work_order_id:
        from knowledge_product_workflow.runtime import (
            finish_controlled_action,
            read_orchestration_work_order,
        )

        path = finish_controlled_action(
            workspace,
            receipt.work_order_id,
            "原领域事务已完成，续接控制面",
            action=receipt.action,
            output_paths=[workspace.root / p for p in tx.outputs if _v_is_file(workspace.root / p)],
            allow_same_stage_output=True,
            route_without_intent=True,
            committed_operation_id=operation_id,
        )
        successor = read_orchestration_work_order(
            workspace, path
        ).completion_proof.successor_work_order_id
    _fault("before_final_receipt")
    return _save(
        workspace, receipt, phase="completed", successor_work_order_id=successor, error=None
    )


def recover_operation(workspace: Workspace, operation_id: str) -> OperationReceipt:
    """Resume the original journal and control tail only; never call submit_chapter."""
    receipt = _load(workspace, operation_id)
    if receipt.phase == "completed":
        return receipt
    if not receipt.transaction_id:
        if receipt.phase == 'failed_before_commit' and receipt.failure_evidence_sha256:
            raise ValueError('新操作已有前置失败证据；用close-failed-operation核验并结案，失败不记成功')
        raise ValueError("结果未知，必须核实原owner，不能自动重发")
    if receipt.work_order_id:
        from knowledge_product_workflow.runtime import (
            WorkOrderLifecycle,
            read_orchestration_work_order,
        )

        order = read_orchestration_work_order(workspace, receipt.work_order_id)
        if order.lifecycle not in {
            WorkOrderLifecycle.ACTIVE,
            WorkOrderLifecycle.PENDING,
            WorkOrderLifecycle.COMPLETED,
        }:
            raise ValueError("原工单已撤销；保留事务，不能自动续接")
    from knowledge_product_workflow.workspace_transactions import recover_workspace_transaction

    recover_workspace_transaction(workspace, receipt.transaction_id)
    return finish_operation(workspace, operation_id)


@contextmanager
def wait_owner(workspace: Workspace, operation_id: str):
    """One local waiter; does not hold the domain/controller write lock while waiting."""
    path = receipt_path(workspace, operation_id).with_suffix(".wait-owner.json")
    _v_mkdir(path.parent, parents=True, exist_ok=True)
    from knowledge_product_workflow.production_tasks import _process_identity

    owner = {
        "pid": os.getpid(),
        "process_identity": _process_identity(os.getpid()),
        "workspace": str(workspace.root.resolve()),
        "nonce": uuid4().hex,
    }
    raw = json.dumps(owner, sort_keys=True)
    try:
        with write_open(path, "x", encoding="utf-8") as stream:
            stream.write(raw)
    except FileExistsError as error:
        raise ValueError("已有等待owner；核原句柄，不得新增协调者") from error
    try:
        yield
    finally:
        if _v_is_file(path) and path.read_text(encoding="utf-8") == raw:
            from knowledge_product_workflow.verification_context import unlink

            unlink(path)


def recover_wait_owner(workspace: Workspace, operation_id: str) -> None:
    """Explicit recovery only after the original waiter's process identity is proven dead."""
    path = receipt_path(workspace, operation_id).with_suffix(".wait-owner.json")
    if not _v_is_file(path):
        raise ValueError('本次没有可释放的等待owner；原操作未知状态未改变，先查operation-status')
    raw = path.read_bytes()
    owner = json.loads(raw)
    from knowledge_product_workflow.production_tasks import _process_identity

    if (
        owner["workspace"] != str(workspace.root.resolve())
        or not owner["process_identity"]
        or _process_identity(owner["pid"]) == owner["process_identity"]
    ):
        raise ValueError("原等待owner活动或身份未知，不能释放")
    if path.read_bytes() != raw:
        raise ValueError("等待owner已变化")
    from knowledge_product_workflow.verification_context import unlink

    unlink(path)


def wait_operation(
    workspace: Workspace,
    operation_id: str,
    *,
    timeout: float = 60,
    emit=None,
    clock=time.monotonic,
    sleep=time.sleep,
) -> dict:
    """Program-only bounded receipt wait. Local frames are not claims about host UI delivery."""
    if not 0 < timeout <= 3600:
        raise ValueError("等待总时限须在0到3600秒内")
    from knowledge_product_workflow.runtime import RunIntentState, read_run_intent

    started = clock()
    emit = emit or (lambda _: None)
    with wait_owner(workspace, operation_id):
        last_revision = None
        while True:
            elapsed = clock() - started
            status = operation_status(workspace, operation_id)
            intent = read_run_intent(workspace)
            reason = None
            if status["phase"] in {
                "completed",
                "closed_failed",
                "failed_before_commit",
                "unknown",
                "recovery_required",
            }:
                reason = status["phase"]
            elif intent and intent.state in {RunIntentState.PAUSED, RunIntentState.STOP_REQUESTED}:
                reason = "paused"
            elif elapsed >= timeout:
                reason = "deadline_unknown"
            frame = {
                "schema_version": 1,
                "operation_id": operation_id,
                "phase": status["phase"],
                "revision": status.get("revision"),
                "chapter": status.get("chapter_number"),
                "last_progress": status.get("last_progress"),
                "elapsed": elapsed,
                "host_ui_delivery_verified": False,
            }
            if reason:
                result = {
                    **frame,
                    "kind": "attention",
                    "reason": reason,
                    "next_action": status.get("next_action"),
                    "unknown_owner_retained": reason != "completed",
                }
                emit(result)
                return result
            if status.get("revision") != last_revision:
                emit({**frame, "kind": "progress"})
                last_revision = status.get("revision")
            emit({**frame, "kind": "heartbeat"})
            sleep(min(30, timeout - elapsed))
