"""One controller queue for authors, reviewers and semantic checks; no model polling."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from knowledge_product_workflow.atomic_files import replace_file
from knowledge_product_workflow.production_dependencies import (
    binding_digest,
    build_dependency_plan,
    build_task_context,
    payload_digest,
)
from knowledge_product_workflow.verification_context import _write_contexts as _v_write_contexts
from knowledge_product_workflow.verification_context import after_write as _v_after_write
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    unlink as _v_unlink,
)
from knowledge_product_workflow.verification_context import write_open as _v_write_open
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workspace import Workspace

_OWN_PROCESS_IDENTITIES: dict[int, str | None] = {}
_CONTROL_LOCK_STATE = threading.local()


def controller_lock_owned(workspace: Workspace) -> bool:
    owners = getattr(_CONTROL_LOCK_STATE, "owners", {})
    original = workspace.root.absolute()
    owner = owners.get(str(original))
    if owner is None:
        owner = next((value for value in owners.values() if original.resolve() == value.physical_root), None)
    if owner is None:
        return False
    owner.assert_held()  # An observed epoch/owner change is unknown, never a false/reacquire signal.
    return True


class ProductionPolicy(BaseModel):
    mode: Literal["serial", "adaptive"] = "adaptive"
    requested_parallelism: int = Field(default=4, ge=1, le=64)


class ProductionHostCapabilities(BaseModel):
    # Documentary JSON flags are not a qualification grant. A future trusted
    # native adapter must verify complete evidence in-process before admission.
    _trusted_host_evidence: bool = PrivateAttr(default=False)
    _scope_workspace: str | None = PrivateAttr(default=None)
    _scope_run_id: str | None = PrivateAttr(default=None)
    _native_session_id: str | None = PrivateAttr(default=None)
    _grant_liveness: object = PrivateAttr(default=None)
    host_kind: str = "unavailable"
    max_parallelism: int = Field(default=0, ge=0)
    production_verified: bool = False
    permissions_verified: bool = False
    parent_child_receipts_verified: bool = False
    release_verified: bool = False
    reason: str = "真实宿主资格尚未通过；不得启用原生并行"

    def admits_live_parallelism(self) -> bool:
        return bool(self._trusted_host_evidence and self._native_session_id
                    and callable(self._grant_liveness) and self._grant_liveness()
                    and self.host_kind == "codex-app-server"
                    and self.production_verified and self.permissions_verified
                    and self.parent_child_receipts_verified and self.release_verified
                    and self.max_parallelism > 0)


class DelegatedTaskClaim(BaseModel):
    """Persisted routing data only; never a host permission or qualification grant."""
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    claim_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    role: Literal["writer", "reviewer", "semantic"]
    candidate_version: int = Field(ge=1, strict=True)
    input_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_id: str = Field(min_length=1)
    connection_id: str = Field(min_length=1)
    control_thread_id: str = Field(min_length=1)
    control_turn_id: str = Field(min_length=1)
    root_turn_id: str = Field(min_length=1)
    phase: Literal["reserved", "send_started", "running", "unknown", "terminal_recorded"] = "reserved"
    thread_id: str | None = Field(default=None, min_length=1)
    turn_id: str | None = Field(default=None, min_length=1)
    receipt_relative: str | None = None
    receipt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _ack_fields(self):
        if self.phase in {"running", "terminal_recorded"} and (not self.thread_id or not self.turn_id):
            raise ValueError("running claim必须保留真实thread/turn确认字段")
        if (bool(self.thread_id) != bool(self.turn_id)
                or (self.phase in {"reserved", "send_started"} and self.thread_id is not None)):
            raise ValueError("claim确认字段与派单阶段不一致")
        if self.phase == "terminal_recorded" and (not self.receipt_relative or not self.receipt_sha256):
            raise ValueError("终态记录缺不可变结果收据")
        return self


class DelegatedControlAnchor(BaseModel):
    """Documentary control-turn claim; never a restored connection or worker grant."""
    model_config = ConfigDict(extra="forbid")
    owner_id: str = Field(min_length=1)
    connection_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    phase: Literal["send_started", "ready", "unknown"] = "send_started"
    turn_id: str | None = None
    root_turn_id: str | None = None

    @model_validator(mode="after")
    def _ready(self):
        if self.phase == "ready" and (not self.turn_id or not self.root_turn_id):
            raise ValueError("控制锚点缺原生turn/root确认")
        return self


class ProductionTask(BaseModel):
    task_id: str
    run_id: str
    chapter_number: int
    candidate_version: int = 1
    role: Literal["writer", "reviewer", "semantic"]
    state: str = "edit_ready"
    input_manifest_sha256: str
    input_bindings: dict[str, str] = Field(default_factory=dict)
    boundary_repetition_advice: dict = Field(default_factory=dict)
    source_relative: str | None = None
    candidate_context_path: str | None = None
    candidate_source_sha256: str | None = None
    result_digest: str | None = None
    native_result_digest: str | None = None
    native_claim_digest: str | None = None
    result: dict | None = None
    host_task_id: str | None = None
    host_parent_id: str | None = None
    host_dispatched_at: datetime | None = None
    release_confirmed: bool = False
    dispatch_index: int | None = None
    ready_since_dispatch: int = 0
    revision_reason: str | None = None
    delegated_claim: DelegatedTaskClaim | None = None

    @model_validator(mode="after")
    def _claim_binding(self):
        if self.delegated_claim is not None:
            _require_delegated_binding(self)
            if self.dispatch_index is None:
                raise ValueError("持久delegated claim缺少原派单序号；拒绝加载或重建任务")
        return self


class ProductionRun(BaseModel):
    _delegated_release_check: object = PrivateAttr(default=None)
    schema_version: int = 1
    run_id: str
    policy: ProductionPolicy
    review_policy: Literal["deterministic_only", "independent_review_required"] | None = None
    effective_parallelism: int = Field(ge=0)
    diagnostic_only: bool = True
    synthetic_local_checks: bool = False
    tasks: list[ProductionTask] = Field(default_factory=list)
    baseline_bindings: dict[str, str] = Field(default_factory=dict)
    capabilities: ProductionHostCapabilities = Field(default_factory=ProductionHostCapabilities)
    dispatch_count: int = 0
    duplicate_results: int = 0
    accepted_chapters: list[int] = Field(default_factory=list)
    blocked_chapters: dict[int, list[str]] = Field(default_factory=dict)
    nested_active_ids: set[str] = Field(default_factory=set)
    resource_reservations: dict[str, str] = Field(default_factory=dict)
    shared_requests: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    delegated_control: DelegatedControlAnchor | None = None

    @classmethod
    def diagnostic_run(cls, count: int, policy: ProductionPolicy) -> ProductionRun:
        if count < 1:
            raise ValueError("诊断章节数必须为正数")
        run_id = "diagnostic-" + uuid4().hex[:12]
        limit = 1 if policy.mode == "serial" else policy.requested_parallelism
        run = cls(run_id=run_id, policy=policy, effective_parallelism=limit, synthetic_local_checks=True,
                  review_policy="independent_review_required")
        for number in range(1, count + 1):
            run.add_writer(number, {})
        return run

    @property
    def active_count(self) -> int:
        # Serialized terminal records alone cannot release a claim. This callback
        # closes over the original bridge and observes later revocation as well.
        return (sum((not (callable(self._delegated_release_check)
                         and self._delegated_release_check(t))) if t.delegated_claim is not None
                    else (t.dispatch_index is not None and not t.release_confirmed) for t in self.tasks)
                + len(self.nested_active_ids))

    def task(self, task_id: str) -> ProductionTask:
        item = next((t for t in self.tasks if t.task_id == task_id), None)
        if item is None:
            raise ValueError("任务不属于当前运行")
        return item

    def add_writer(self, number: int, bindings: dict, *, version: int = 1, advice: dict | None = None) -> ProductionTask:
        if any(t.chapter_number == number and t.role == "writer"
               and t.state not in {"accepted", "superseded", "cancelled"} for t in self.tasks):
            raise ValueError("同章已有作者任务")
        task_id = f"writer-{number:02d}-v{version}-{uuid4().hex[:8]}"
        task = ProductionTask(
            task_id=task_id, run_id=self.run_id, chapter_number=number,
            candidate_version=version, role="writer", input_bindings=bindings, ready_since_dispatch=self.dispatch_count,
            input_manifest_sha256=payload_digest({"chapter": number, "version": version,
                                                  "role": "writer", "inputs": bindings,
                                                  **({"boundary_advice": advice} if advice else {})}),
            boundary_repetition_advice=advice or {},
        )
        self.tasks.append(task)
        return task

    def mark_unknown(self, task_id: str) -> None:
        self.task(task_id).state = "unknown"

    def confirm_release(self, task_id: str) -> None:
        task = self.task(task_id)
        if task.state in {"editing", "reviewing", "unknown"}:
            raise ValueError("任务尚未终结，不能释放槽位")
        task.release_confirmed = True

    def resize(self, available: int) -> None:
        """Capacity shrink never guesses cancellation or release of existing tasks."""
        if available < 0:
            raise ValueError("可用容量不能为负数")
        self.effective_parallelism = min(available, self.policy.requested_parallelism)

    def reserve_resources(self, task_id: str, keys: list[str], *, quota_key: str | None = None,
                          quota_limit: int | None = None) -> None:
        self.task(task_id)
        if any(key in self.resource_reservations and self.resource_reservations[key] != task_id
               for key in keys):
            raise ValueError("全书资源ID已被其他候选预约")
        if quota_key is not None:
            if quota_limit is None or quota_limit < 0:
                raise ValueError("配额必须来自明确批准策略")
            counted = {key for key in self.resource_reservations if key.startswith(quota_key + ":")}
            if len(counted | set(keys)) > quota_limit:
                raise ValueError("全书共享配额不足；写作前调整分工，禁止重复消耗")
        self.resource_reservations.update({key: task_id for key in keys})

    def queue_shared_request(self, task_id: str, kind: str, payload: dict) -> dict:
        task = self.task(task_id)
        if kind not in {"source", "assumption", "claim", "diagram", "terminology", "case"}:
            raise ValueError("共享请求类型不受支持")
        digest = payload_digest({"task": task_id, "version": task.candidate_version,
                                 "kind": kind, "payload": payload})
        previous = next((r for r in self.shared_requests if r["request_id"] == digest), None)
        if previous:
            return previous
        request = {"request_id": digest, "task_id": task_id, "candidate_version": task.candidate_version,
                   "kind": kind, "payload": payload, "state": "pending_domain_validation"}
        self.shared_requests.append(request)
        # The controller must consume through the original domain validator/CLI.
        # Queueing a claim or an approval never grants it factual/approval validity.
        return request

    def _bind_result(self, task: ProductionTask, result: dict, manifest: str) -> bool:
        if manifest != task.input_manifest_sha256:
            raise ValueError("输入版本不相等，拒绝晚到结果")
        digest = payload_digest(result)
        if task.result_digest:
            if task.result_digest != digest:
                raise ValueError("同一任务已接收不同版本结果，需建立新候选")
            self.duplicate_results += 1
            return False
        if task.dispatch_index is None or task.state not in {"editing", "reviewing", "unknown"}:
            raise ValueError("任务尚未派发或不处于可接收状态")
        task.result_digest, task.result = digest, result
        task.release_confirmed = self.diagnostic_only
        return True

    def complete_edit(self, task_id: str, body: str, *, input_manifest_sha256: str) -> None:
        task = self.task(task_id)
        if task.role != "writer" or not body.strip():
            raise ValueError("作者任务必须返回非空候选正文")
        if not self._bind_result(task, {"body": body}, input_manifest_sha256):
            return
        task.state = "local_check"
        if self.diagnostic_only and self.synthetic_local_checks:
            self.local_check_complete(task_id, passed=True)
        else:
            self.tasks.append(ProductionTask(task_id=f"semantic-{task.chapter_number:02d}-v{task.candidate_version}-{uuid4().hex[:8]}",
                run_id=self.run_id, chapter_number=task.chapter_number, candidate_version=task.candidate_version,
                role="semantic", state="waiting_snapshot", ready_since_dispatch=self.dispatch_count,
                input_bindings=task.input_bindings, input_manifest_sha256=payload_digest(
                    {"source": task.result_digest, "role": "semantic", "inputs": task.input_bindings})))

    def local_check_complete(self, task_id: str, *, passed: bool) -> None:
        task = self.task(task_id)
        if task.state != "local_check":
            raise ValueError("候选尚未冻结或已有检查结果")
        task.state = ("review_ready" if self.review_policy != "deterministic_only" else "acceptance_ready") if passed else "revision_required"
        if self.review_policy == "deterministic_only":
            return
        if passed:
            review_id = f"review-{task.chapter_number:02d}-v{task.candidate_version}-{uuid4().hex[:8]}"
            self.tasks.append(ProductionTask(
                task_id=review_id, run_id=self.run_id, chapter_number=task.chapter_number,
                candidate_version=task.candidate_version, role="reviewer", state="review_ready", ready_since_dispatch=self.dispatch_count,
                input_bindings=task.input_bindings,
                input_manifest_sha256=payload_digest({"source": task.result_digest,
                                                       "role": "reviewer",
                                                       "inputs": task.input_bindings}),
            ))

    def complete_review(self, task_id: str, *, passed: bool, input_manifest_sha256: str,
                        issues: list[str] | None = None) -> None:
        task = self.task(task_id)
        if task.role not in {"reviewer", "semantic"}:
            raise ValueError("作者不能在自己的任务内签审稿")
        if not self._bind_result(task, {"passed": passed, "issues": issues or []},
                                 input_manifest_sha256):
            return
        task.state = "acceptance_ready" if passed else "revision_required"

    def supersede_candidate(self, number: int) -> int:
        previous = [t for t in self.tasks if t.chapter_number == number]
        version = max((t.candidate_version for t in previous), default=0) + 1
        for task in previous:
            if task.state != "accepted":
                task.state = "superseded"
                task.release_confirmed = self.diagnostic_only
        return version


def next_dispatches(run: ProductionRun) -> list[ProductionTask]:
    """Only ready work; finished-but-unreleased and unknown tasks keep their slots."""
    free = run.effective_parallelism - run.active_count
    ready = [t for t in run.tasks if t.dispatch_index is None and t.delegated_claim is None
             and ((t.role == "writer" and t.state == "edit_ready")
                  or (t.role != "writer" and t.state == "review_ready"))]
    def priority(task):
        aged = run.dispatch_count - task.ready_since_dispatch >= 2 * run.effective_parallelism
        return (not aged, task.ready_since_dispatch if aged else task.role == "writer", run.tasks.index(task))
    ready.sort(key=priority)
    selected = ready[:max(0, free)]
    for task in selected:
        run.dispatch_count += 1
        task.dispatch_index = run.dispatch_count
        task.state = "editing" if task.role == "writer" else "reviewing"
    return selected


class ControlLockBusy(ValueError):
    """Bounded observation of a competing owner, never authority to remove it."""
    def __init__(self, path, workspace):
        self.owner = {"state": "unknown", "action": "unknown", "operation_id": "unknown"}
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(workspace.root.resolve()):
                raise ValueError("锁路径归属未知")
            with path.open("rb") as stream:
                data = stream.read(4097)
            if len(data) > 4096:
                raise ValueError("锁元数据超限")
            raw = json.loads(data)
            pid = raw.get("pid")
            if type(pid) is not int or pid < 1 or raw.get("workspace") != str(workspace.root.resolve()):
                raise ValueError("锁身份无效")
            identity = raw.get("process_identity")
            observed = _process_identity(pid)
            state = "active" if identity and identity == observed else "unknown"
            self.owner.update(state=state, pid=pid, created_at=str(raw.get("created_at", "unknown"))[:80])
            if path.read_bytes() != data:
                self.owner["state"] = "changed_during_observation"
        except FileNotFoundError:
            self.owner["state"] = "disappeared_during_observation"
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            self.owner["state"] = "invalid_or_unknown"
        super().__init__("CONTROL_LOCK_BUSY: 同工作区控制查询/写入互斥；" + json.dumps(self.owner, ensure_ascii=False)
                         + "；等待原命令实际终态后再查询，未知身份不得按死锁接管；不自动等待/重试/删除")


class _ControlLockOwner:
    """Only this lock's two metadata files; content/lease publication never enters here."""
    def __init__(self, workspace: Workspace):
        self.root = workspace.root.absolute()
        self.physical_root = self.root.resolve()
        self.directory = self.root / "runs/production"
        self.path = self.directory / "control.lock"
        self.owner = {"pid":os.getpid(),"process_identity":_process_identity(os.getpid()),
            "workspace":str(self.physical_root),"created_at":datetime.now(UTC).isoformat(),"lock_id":uuid4().hex}
        self.raw = json.dumps(self.owner).encode("utf-8")
        self.prepared = self.directory / (".lock-owner-" + uuid4().hex + ".json")
        self.anchors = {}
        for path in (self.root, self.root / "runs", self.directory, self.physical_root):
            info = path.stat(follow_symlinks=False)
            self.anchors[path] = (path.resolve(), info.st_dev, info.st_ino)
        self.identity = None
        self.complete = False
        self.published = False

    def _check_paths(self):
        for path, (resolved, device, inode) in self.anchors.items():
            info = path.stat(follow_symlinks=False)
            if path.resolve() != resolved or (info.st_dev, info.st_ino) != (device, inode):
                raise ValueError("控制锁根／目录身份已变化，保留未知，不触替换目标")
        if (not self.directory.resolve().is_relative_to(self.physical_root)
                or any(path.is_symlink() or getattr(path, "is_junction", lambda:False)()
                    for path in (self.root / "runs", self.directory))):
            raise ValueError("控制锁目录路径归属变化，保留未知")
        tuple(_v_write_contexts(self.path))  # Root/lexical guards; never scans book bytes.

    def _owned_file(self, path: Path, *, incomplete=False) -> bool:
        if path not in {self.path, self.prepared} or self.identity is None:
            raise ValueError("不是本次控制锁拥有的元数据路径")
        self._check_paths()
        try:
            info = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return False
        if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
                or (info.st_dev, info.st_ino) != self.identity):
            raise ValueError("控制锁文件inode／路径已换owner，保留未知")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != self.identity:
                raise ValueError("控制锁打开期间身份变化，保留未知")
            data = stream.read(len(self.raw) + 1)
        if data != self.raw and not (incomplete and self.raw.startswith(data)):
            raise ValueError("控制锁nonce／内容已换owner，保留未知")
        self._check_paths()
        current = path.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != self.identity:
            raise ValueError("控制锁最后检查时身份变化，保留未知")
        return True

    def acquire(self):
        self._check_paths()
        with self.prepared.open("xb") as stream:
            info = os.fstat(stream.fileno())
            self.identity = (info.st_dev, info.st_ino)
            stream.write(self.raw)
        self.complete = True
        _v_after_write(self.prepared)
        if not self._owned_file(self.prepared):
            raise ValueError("控制锁私有owner文件已消失，停止发布")
        try:
            os.link(self.prepared, self.path)
        except FileExistsError as error:
            raise ControlLockBusy(self.path, Workspace.open(self.root)) from error
        self.published = True
        _v_after_write(self.path)

    def assert_held(self):
        if not self.published or not self._owned_file(self.path):
            raise ValueError("本次控制锁权威已丢失，保留未知，不自动接管")

    def cleanup(self) -> list[Exception]:
        failures = []
        if self.identity is None:
            return failures  # Exclusive creation failed; no file belongs to this attempt.
        for path in ([self.path] if self.published else []) + [self.prepared]:
            try:
                if self._owned_file(path, incomplete=not self.complete and path == self.prepared):
                    path.unlink()
                    _v_after_write(path)
            except (OSError, ValueError) as error:
                failures.append(error)
        return failures


@contextmanager
def production_lock(workspace: Workspace):
    if controller_lock_owned(workspace):
        raise ValueError("已有控制面写入，先查终态；不能嵌套取得同一控制锁")
    root = workspace.root / "runs/production"
    _v_mkdir(root,parents=True,exist_ok=True)
    owner = _ControlLockOwner(workspace)
    owners = getattr(_CONTROL_LOCK_STATE, "owners", {})
    key = str(owner.root)
    entered = False
    try:
        owner.acquire()
        owners[key] = owner
        _CONTROL_LOCK_STATE.owners = owners
        entered = True
        yield
    finally:
        original_error = sys.exception()
        failures = owner.cleanup()
        if entered and owners.get(key) is owner:
            owners.pop(key)
        if failures:
            if original_error is not None:
                for failure in failures:
                    original_error.add_note("控制锁清理保留未知：" + str(failure))
            else:
                raise failures[0]


def _process_identity(pid: int) -> str | None:
    if pid < 1:
        raise ValueError("锁进程PID不合法")
    if pid == os.getpid() and pid in _OWN_PROCESS_IDENTITIES:
        return _OWN_PROCESS_IDENTITIES[pid]
    if sys.platform == "win32":
        command = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
            f"try {{$p=[System.Diagnostics.Process]::GetProcessById({pid}); [Console]::Write($p.StartTime.ToUniversalTime().Ticks.ToString()); exit 0}} catch {{if ($_.Exception.InnerException -is [System.ArgumentException]) {{exit 0}}; exit 2}}"]
    else:
        command = ["ps", "-p", str(pid), "-o", "lstart="]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0, check=False)
    if result.returncode not in ({0} if sys.platform == "win32" else {0, 1}):
        raise ValueError("无法核实锁持有进程生命周期，保留未知")
    identity = result.stdout.strip() or None
    if pid == os.getpid():
        _OWN_PROCESS_IDENTITIES[pid] = identity
    return identity


def recover_production_lock(workspace: Workspace) -> dict:
    path = workspace.root / "runs/production/control.lock"
    if not _v_exists(path):
        return {"state": "no_lock"}
    before = path.read_bytes()
    try:
        owner = json.loads(before)
        if owner["workspace"] != str(workspace.root.resolve()) or not owner["process_identity"]:
            raise ValueError("锁归属／创建身份不明，禁止猜测释放")
        current = _process_identity(owner["pid"])
    except (ValueError, KeyError, subprocess.TimeoutExpired) as error:
        raise ValueError("锁身份未知，保留锁并人工核实") from error
    if current == owner["process_identity"]:
        raise ValueError("锁持有进程仍活动，不能释放或重复提交")
    if path.read_bytes() != before:
        raise ValueError("核实期间锁发生变化，禁止释放")
    _v_unlink(path)
    return {"state": "released_dead_owner", "owner": owner}


def _require_run_membership(run: ProductionRun) -> None:
    if any(task.run_id != run.run_id for task in run.tasks):
        raise ValueError("任务运行归属与所在运行ID不匹配，拒绝读写损坏状态")
    identities = [task.task_id for task in run.tasks]
    if len(set(identities)) != len(identities):
        raise ValueError("运行包含重复task ID，不能猜测任务身份或重建claim")


def _production_run_path(workspace: Workspace, run_id: str) -> Path:
    if not isinstance(run_id, str) or not run_id.replace("-", "").isalnum():
        raise ValueError("运行ID越界")
    path = workspace.root / "runs/production" / run_id / "run.json"
    if (not path.resolve().is_relative_to(workspace.root.resolve())
            or not path.resolve().is_relative_to((workspace.root / "runs/production").resolve())):
        raise ValueError("运行路径越界")
    return path


def save_run(workspace: Workspace, run: ProductionRun) -> Path:
    _require_run_membership(run)  # Check before mkdir, temporary writes, or replacement.
    path = _production_run_path(workspace, run.run_id)
    root = path.parent
    _v_mkdir(root,parents=True,exist_ok=True)
    temporary = root / (".run-" + uuid4().hex + ".tmp")
    _v_write_text(temporary, run.model_dump_json(indent=2), encoding="utf-8")
    replace_file(temporary, path)
    return path


def _delegated_fault(phase: str) -> None:
    """Deterministic durable-boundary test hook; not configurable by commands."""


def _require_delegated_binding(task: ProductionTask) -> DelegatedTaskClaim:
    claim = task.delegated_claim
    if claim is None or any(getattr(claim, key) != getattr(task, key)
            for key in ("task_id", "role", "candidate_version", "input_manifest_sha256")):
        raise ValueError("持久claim与当前任务角色／版本／清单不匹配")
    return claim


def _delegated_run(workspace: Workspace, run_id: str) -> ProductionRun:
    if not controller_lock_owned(workspace):
        raise ValueError("delegated写入必须持有原production_lock")
    run = resume_production_run(workspace, run_id)
    if not run.diagnostic_only:
        raise ValueError("D10-1只允许诊断claim；正式delegated资格尚未定义")
    return run


def reserve_delegated_tasks(workspace: Workspace, run_id: str, *, owner_id: str,
        connection_id: str, control_thread_id: str, control_turn_id: str,
        root_turn_id: str) -> list[ProductionTask]:
    """Atomically reserve original ready tasks and narrow claims before any host call."""
    ownership = (owner_id, connection_id, control_thread_id, control_turn_id, root_turn_id)
    if any(not isinstance(value, str) or not value.strip() for value in ownership):
        raise ValueError("必须绑定明确owner、活连接及真实控制因果ID")
    with production_lock(workspace):
        run = _delegated_run(workspace, run_id)
        _check_delegated_owner(run, ownership)
        selected = next_dispatches(run)
        _attach_delegated_claims(workspace, run, selected, ownership)
        if selected:
            save_run(workspace, run)
            _delegated_fault("after_reserve_save")
        return selected


def _check_delegated_owner(run, ownership):
    for task in run.tasks:
        if task.delegated_claim:
            claim = _require_delegated_binding(task)
            if (claim.owner_id, claim.connection_id, claim.control_thread_id,
                    claim.control_turn_id, claim.root_turn_id) != ownership:
                raise ValueError("运行已绑定其他owner或连接；不能从JSON接管或重发")


def _attach_delegated_claims(workspace, run, selected, ownership):
    if not controller_lock_owned(workspace) or not run.diagnostic_only:
        raise ValueError("仅受锁保护的诊断运行可附delegated claim")
    _check_delegated_owner(run, ownership)
    owner_id, connection_id, control_thread_id, control_turn_id, root_turn_id = ownership
    for task in selected:
        if (task.delegated_claim is not None or task.chapter_number in run.blocked_chapters
                or any(binding_digest(workspace, path) != sha for path, sha in task.input_bindings.items())):
            raise ValueError("任务已有claim或实际依赖尚未ready，未派发")
        task.delegated_claim = DelegatedTaskClaim(claim_id="delegated-" + uuid4().hex,
            task_id=task.task_id, role=task.role, candidate_version=task.candidate_version,
            input_manifest_sha256=task.input_manifest_sha256, owner_id=owner_id,
            connection_id=connection_id, control_thread_id=control_thread_id,
            control_turn_id=control_turn_id, root_turn_id=root_turn_id)


def _owned_delegated_claim(run: ProductionRun, task_id: str, *, claim_id: str,
        owner_id: str, connection_id: str) -> DelegatedTaskClaim:
    claim = _require_delegated_binding(run.task(task_id))
    if (claim.claim_id, claim.owner_id, claim.connection_id) != (claim_id, owner_id, connection_id):
        raise ValueError("陈旧owner／连接／claim，拒绝修改原派单")
    return claim


def mark_delegated_send_started(workspace: Workspace, run_id: str, task_id: str, *,
        claim_id: str, owner_id: str, connection_id: str) -> DelegatedTaskClaim:
    with production_lock(workspace):
        run = _delegated_run(workspace, run_id)
        claim = _owned_delegated_claim(run, task_id, claim_id=claim_id,
            owner_id=owner_id, connection_id=connection_id)
        if claim.phase != "reserved":
            raise ValueError("claim已尝试或终态未知，不能再次开始发送")
        claim.phase = "send_started"
        save_run(workspace, run)
        _delegated_fault("after_send_started_save")
        return claim


def record_delegated_ack(workspace: Workspace, run_id: str, task_id: str, *, claim_id: str,
        owner_id: str, connection_id: str, thread_id: str, turn_id: str,
        root_turn_id: str) -> DelegatedTaskClaim:
    with production_lock(workspace):
        run = _delegated_run(workspace, run_id)
        claim = _owned_delegated_claim(run, task_id, claim_id=claim_id,
            owner_id=owner_id, connection_id=connection_id)
        if root_turn_id != claim.root_turn_id:
            raise ValueError("原生root回显与原因果请求不匹配")
        if (not isinstance(thread_id, str) or not thread_id.strip()
                or not isinstance(turn_id, str) or not turn_id.strip()
                or thread_id == claim.control_thread_id or turn_id in {claim.control_turn_id, claim.root_turn_id}
                or any(task.task_id != task_id and task.delegated_claim is not None
                    and (task.delegated_claim.thread_id == thread_id or task.delegated_claim.turn_id == turn_id)
                    for task in run.tasks)):
            raise ValueError("worker确认不能复用控制线程或其他任务的fresh线程")
        if claim.phase == "running" and (claim.thread_id, claim.turn_id) == (thread_id, turn_id):
            return claim  # Duplicate local acknowledgement, never a second host dispatch.
        if claim.phase != "send_started":
            raise ValueError("claim未开始发送或已有不同确认，不能覆盖原thread/turn")
        updated = claim.model_copy(update={"thread_id":thread_id,"turn_id":turn_id,"phase":"running"})
        run.task(task_id).delegated_claim = DelegatedTaskClaim.model_validate(updated.model_dump())
        save_run(workspace, run)
        _delegated_fault("after_ack_save")
        return run.task(task_id).delegated_claim


def recover_delegated_unknown(workspace: Workspace, run_id: str) -> dict:
    """Monotone recovery only: no transport, new owner, release, or qualification."""
    with production_lock(workspace):
        run = _delegated_run(workspace, run_id)
        unknown, never_sent = [], []
        if run.delegated_control is not None and run.delegated_control.phase == "send_started":
            run.delegated_control = run.delegated_control.model_copy(update={"phase": "unknown"})
        for task in run.tasks:
            if task.delegated_claim is None:
                continue
            claim = _require_delegated_binding(task)
            if claim.phase == "reserved":
                never_sent.append(task.task_id)
            elif claim.phase == "terminal_recorded":
                continue  # Local replay remains possible; no live callback is restored.
            else:
                claim.phase = "unknown"
                run.mark_unknown(task.task_id)
                task.release_confirmed = False
                unknown.append(task.task_id)
        save_run(workspace, run)
        return {"unknown":unknown,"never_sent":never_sent,"resubmitted":False,
            "production_qualification":False,"native_auth_restored":False}


def validate_delegated_result(task: ProductionTask, receipt: dict) -> None:
    """Validate attribution data only. This cannot verify host liveness or grant admission."""
    claim = _require_delegated_binding(task)
    expected = {"task_id":task.task_id,"role":task.role,"candidate_version":task.candidate_version,
        "input_manifest_sha256":task.input_manifest_sha256,"host_task_id":claim.thread_id,
        "host_turn_id":claim.turn_id,"host_root_turn_id":claim.root_turn_id}
    if (claim.phase not in {"running", "terminal_recorded"} or type(receipt.get("candidate_version")) is not int
            or any(receipt.get(key) != value for key, value in expected.items())
            or not isinstance(receipt.get("result"), dict)):
        raise ValueError("delegated结果thread/turn/root/角色/版本/清单不匹配或连接已未知")
    field = {"writer":"body","reviewer":"review","semantic":"structure_review"}[task.role]
    value = receipt["result"].get(field)
    valid = isinstance(value, str) and bool(value.strip()) if field == "body" else isinstance(value, dict)
    if not valid:
        raise ValueError("delegated结果不符合当前角色")


def record_delegated_terminal(workspace, run_id, task_id, *, bridge, receipt):
    """Persist evidence only; live capacity still belongs to the original bridge instance."""
    from knowledge_product_workflow.production_delegated_host import DelegatedInlineBridge
    if type(bridge) is not DelegatedInlineBridge or bridge.workspace != workspace:
        raise ValueError("终态事务仅接受此工作区具体delegated bridge")
    with production_lock(workspace):
        run = _delegated_run(workspace, run_id)
        task = run.task(task_id)
        bridge._require_current_receipt(task, receipt)
        digest = payload_digest(receipt)
        claim = _require_delegated_binding(task)
        relative = f"runs/production/{run_id}/receipts/delegated-{digest}.json"
        if claim.phase == "terminal_recorded" and (claim.receipt_sha256 != digest
                or claim.receipt_relative != relative):
            raise ValueError("同claim终态收据不同，禁止覆盖")
        path = workspace.root / relative
        _v_mkdir(path.parent, parents=True, exist_ok=True)
        raw = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
        if path.exists():
            if path.read_text(encoding="utf-8") != raw:
                raise ValueError("不可变delegated收据内容已变化")
        else:
            with _v_write_open(path, "x", encoding="utf-8") as stream:
                stream.write(raw)
        _delegated_fault("after_terminal_receipt_write")
        updated = claim.model_dump() | {"phase": "terminal_recorded",
            "receipt_relative": relative, "receipt_sha256": digest}
        task.delegated_claim = DelegatedTaskClaim.model_validate(updated)
        save_run(workspace, run)
        _delegated_fault("after_terminal_save")
        bridge._persisted[task_id] = digest  # Only after the original atomic save succeeded.
    return digest


def read_delegated_receipt(workspace, run, task):
    """Local recovery data only; never restores liveness, permissions or capacity."""
    claim = _require_delegated_binding(task)
    if not run.diagnostic_only or claim.phase != "terminal_recorded":
        raise ValueError("仅诊断持久终态可重放本地域步骤")
    expected = f"runs/production/{run.run_id}/receipts/delegated-{claim.receipt_sha256}.json"
    if claim.receipt_relative != expected:
        raise ValueError("持久结果收据路径与当前run／digest不匹配")
    path = workspace.root / expected
    if not path.resolve().is_relative_to(workspace.root.resolve()):
        raise ValueError("持久结果收据越界")
    raw = path.read_bytes()
    receipt = json.loads(raw)
    if (hashlib.sha256(raw).hexdigest() != claim.receipt_sha256 or receipt.get("claim_id") != claim.claim_id
            or receipt.get("task_id") != task.task_id or receipt.get("execution_kind") != "program_delegated"):
        raise ValueError("持久结果收据归属／摘要不符")
    if (tuple(receipt.get(k) for k in ("owner_id", "connection_id", "control_thread_id", "control_turn_id", "root_turn_id"))
            != (claim.owner_id, claim.connection_id, claim.control_thread_id, claim.control_turn_id, claim.root_turn_id)
            or receipt.get("host_task_id") != claim.thread_id or receipt.get("host_parent_id") is not None
            or receipt["result"].get("host_parent_id") is not None):
        raise ValueError("本地delegated收据连接归属不符或冒充spawn父关系")
    validate_delegated_result(task, receipt["result"])
    return receipt


def create_production_run(workspace: Workspace, policy: ProductionPolicy,
                          capabilities: ProductionHostCapabilities, *, diagnostic_only=False,
                          diagnostic_review_policy: str | None = None):
    if diagnostic_review_policy is not None and (
            not diagnostic_only or diagnostic_review_policy != "independent_review_required"):
        raise ValueError("诊断额外审稿只能显式增加独立审稿，不能更改生产或取消审稿")
    if not diagnostic_only and not capabilities.admits_live_parallelism():
        raise ValueError("宿主资格未通过：保留串行；禁止以离线／fake资格启用并行")
    from knowledge_product_workflow.production_intake import require_consistent_production
    require_consistent_production(workspace)
    plan = build_dependency_plan(workspace)
    if not diagnostic_only and plan.phase != "approved_production":
        raise ValueError("样章必须沿原串行路径提交并由用户批准，不能用原生队列绕过")
    if not diagnostic_only and (capabilities._scope_workspace != str(workspace.root.resolve())
            or capabilities._scope_run_id is not None):
        raise ValueError("原生资格不属于当前工作区或已绑定其他运行")
    run = ProductionRun(
        run_id="production-" + uuid4().hex[:12], policy=policy,
        review_policy=diagnostic_review_policy or production_review_policy(workspace),
        effective_parallelism=(1 if policy.mode == "serial" else min(
            policy.requested_parallelism,
            policy.requested_parallelism if diagnostic_only else capabilities.max_parallelism,
        )), diagnostic_only=diagnostic_only, baseline_bindings=plan.baseline_bindings,
        capabilities=capabilities, blocked_chapters=plan.blocked_chapters,
    )
    if not diagnostic_only:
        capabilities._scope_run_id = run.run_id
    for number in plan.ready_chapters:
        if not diagnostic_only and number == 1:
            continue  # Approved sample is an input, never an automatic rewrite task.
        context = build_task_context(workspace, number, "writer")
        from knowledge_product_workflow.developmental import boundary_repetition_context
        run.add_writer(number, context["live_bindings"], advice=boundary_repetition_context(workspace, number))
    with production_lock(workspace):
        for path in (workspace.root / "runs/production").glob("*/run.json"):
            existing = ProductionRun.model_validate_json(path.read_text(encoding="utf-8"))
            if any(t.role == "writer" and t.chapter_number in plan.ready_chapters
                   and t.state not in {"accepted", "cancelled", "superseded"}
                   for t in existing.tasks):
                raise ValueError("已有运行持有同章作者；先查终态，不重复启动")
        leases = workspace.root / "runs/orchestration/leases"
        for path in leases.glob("*.json"):
            lease = json.loads(path.read_text(encoding="utf-8"))
            if lease.get("chapter_number") in plan.ready_chapters and lease.get("state") == "active":
                raise ValueError("原串行作者租约尚未释放，禁止并行抢写")
        save_run(workspace, run)
    return run


def production_review_policy(workspace: Workspace) -> str:
    from knowledge_product_workflow.frontmatter import parse_markdown_artifact
    from knowledge_product_workflow.workflows.product_brief import parse_detail_constraints
    _,body=parse_markdown_artifact((workspace.root/"planning/product-brief.md").read_text(encoding="utf-8"))
    constraints=parse_detail_constraints(body)
    return constraints.review_policy.value if constraints else "deterministic_only"


def resume_production_run(workspace: Workspace, run_id: str, *,
                          capabilities: ProductionHostCapabilities | None = None) -> ProductionRun:
    path = _production_run_path(workspace, run_id)
    run = ProductionRun.model_validate_json(path.read_text(encoding="utf-8"))
    if run.run_id != run_id:
        raise ValueError("文件内部运行ID与请求运行归属不匹配，禁止重定向写回")
    _require_run_membership(run)
    if capabilities is not None:
        if (not capabilities.admits_live_parallelism()
                or capabilities._scope_workspace != str(workspace.root.resolve())
                or capabilities._scope_run_id != run_id):
            raise ValueError("不能通过JSON或其他会话恢复原生资格")
        run.capabilities = capabilities
    return run


def record_task_result(workspace: Workspace, task_id: str, result: dict,
                       *, run_id: str,
                       capabilities: ProductionHostCapabilities | None = None) -> ProductionRun:
    with production_lock(workspace):
        run = resume_production_run(workspace, run_id, capabilities=capabilities)
        if not run.diagnostic_only and not run.capabilities.admits_live_parallelism():
            raise ValueError("原生宿主资格未通过，禁止接受正式结果")
        task = run.task(task_id)
        if any(binding_digest(workspace, p) != sha for p, sha in task.input_bindings.items()):
            task.state = "superseded"
            save_run(workspace, run)
            raise ValueError("任务实际依赖已变化；保留稿件，仅刷新受影响任务")
        if task.role == "writer":
            run.complete_edit(task_id, result["body"],
                              input_manifest_sha256=result["input_manifest_sha256"])
            source = workspace.root / "runs/production" / run_id / "tasks" / task_id / f"v{task.candidate_version}" / "candidate.md"
            _v_mkdir(source.parent,parents=True,exist_ok=True)
            if _v_exists(source) and source.read_text(encoding="utf-8") != result["body"]:
                raise ValueError("候选已冻结，不允许原地修改")
            _v_write_text(source, result["body"], encoding="utf-8")
            task.source_relative = source.relative_to(workspace.root).as_posix()
        else:
            run.complete_review(task_id, passed=result["passed"], issues=result.get("issues"),
                                input_manifest_sha256=result["input_manifest_sha256"])
        save_run(workspace, run)
    return run


def refresh_production_dependencies(workspace: Workspace, run: ProductionRun) -> None:
    """Before dispatch/intake, revoke only actual stale inputs and enqueue newly ready work."""
    from knowledge_product_workflow.production_intake import require_consistent_production
    require_consistent_production(workspace)
    plan = build_dependency_plan(workspace)
    run.blocked_chapters = plan.blocked_chapters
    for task in run.tasks:
        try:
            drift = any(binding_digest(workspace, p) != sha for p, sha in task.input_bindings.items())
        except (OSError, ValueError, KeyError):
            drift = True
        if drift or task.chapter_number in plan.blocked_chapters:
            task.state = "dependency_recheck_required" if task.state == "accepted" else "superseded"
            # No release guess for live tasks. Private candidates/results remain available.
            if run.diagnostic_only:
                task.release_confirmed = True
            if task.chapter_number in run.accepted_chapters:
                run.accepted_chapters.remove(task.chapter_number)
    existing = {t.chapter_number for t in run.tasks}
    for number in plan.ready_chapters:
        if not run.diagnostic_only and number == 1:
            continue
        if number not in existing:
            context = build_task_context(workspace, number, "writer")
            from knowledge_product_workflow.developmental import boundary_repetition_context
            run.add_writer(number, context["live_bindings"], advice=boundary_repetition_context(workspace, number))
