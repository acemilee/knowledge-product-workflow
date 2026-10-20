"""One owned, recoverable file set and stable events; this module grants no authority."""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from knowledge_product_workflow.atomic_files import replace_file
from knowledge_product_workflow.verification_context import current_verification, private_staging
from knowledge_product_workflow.verification_context import glob_files as _v_glob_files
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import read_bytes as _v_read_bytes
from knowledge_product_workflow.verification_context import read_text as _v_read_text
from knowledge_product_workflow.verification_context import (
    unlink as _v_unlink,
)
from knowledge_product_workflow.verification_context import verified_read as _verification_read
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


def _fault(phase: str) -> None:
    """Deterministic test boundary, never configurable through product commands."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash(path: Path) -> str | None:
    return _sha(path.read_bytes()) if _v_is_file(path) else None


def _path(workspace: Workspace, relative: str) -> Path:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("事务路径越界")
    path = (workspace.root / relative).resolve()
    if not path.is_relative_to(workspace.root.resolve()):
        raise ValueError("事务路径越界")
    return path


def _book(workspace: Workspace) -> str:
    from knowledge_product_workflow.frontmatter import parse_markdown_artifact
    path = workspace.root / "planning/product-brief.md"
    if _v_is_file(path):
        return parse_markdown_artifact(_v_read_text(path, encoding="utf-8"))[0].book_id
    return workspace.root.name


class WorkspaceTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    operation_id: str = Field(pattern=r"^tx-[a-z0-9-]+$")
    book_id: str
    kind: str
    scope_chapters: list[int]
    shared_paths: list[str] = Field(default_factory=list)
    authority: dict = Field(default_factory=dict)
    input_bindings: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, dict]
    events: list[dict] = Field(default_factory=list)
    event_offsets: dict[str, dict] = Field(default_factory=dict)
    state: str = "prepared"
    metadata: dict = Field(default_factory=dict)


def _directory(workspace: Workspace) -> Path:
    return workspace.root / "runs/orchestration/transactions"


def _record_path(workspace: Workspace, operation_id: str) -> Path:
    if not operation_id.startswith("tx-") or not operation_id[3:].replace("-", "").isalnum():
        raise ValueError("事务标识越界")
    return _directory(workspace) / f"{operation_id}.json"


def _save(workspace: Workspace, record: WorkspaceTransaction) -> Path:
    path = _record_path(workspace, record.operation_id)
    _v_mkdir(path.parent,parents=True,exist_ok=True)
    from knowledge_product_workflow.verification_context import atomic_metadata_text
    atomic_metadata_text(path, record.model_dump_json(indent=2))
    return path


def _scope(record: WorkspaceTransaction, relative: str) -> None:
    if relative in record.shared_paths:
        if relative.split("/")[0] not in {"planning", "research", "chapters", "runs", "assets", "delivery"}:
            raise ValueError("共享事务目标超出范围")
        return
    for number in record.scope_chapters:
        prefix = f"chapter-{number:02d}"
        if (relative in {f"chapters/{prefix}.md", f"chapters/{prefix}-contract.md",
                         f"chapters/{prefix}-content-plan.yaml"}
                or any(relative.startswith(f"runs/{folder}/{prefix}") for folder in
                       ("chapter-compliance", "editorial", "humanize"))):
            return
    raise ValueError("事务目标不在授权章节范围")


@_verification_read
def prepare_workspace_transaction(workspace: Workspace, outputs: dict[str, bytes | None], *,
                                  kind: str, scope_chapters: list[int],
                                  shared_paths: list[str] | None = None,
                                  input_bindings: dict[str, str] | None = None,
                                  events: list[tuple[str, dict]] | None = None,
                                  authority: dict | None = None, operation_id: str | None = None,
                                  metadata: dict | None = None) -> WorkspaceTransaction:
    from knowledge_product_workflow.operation_receipts import domain_transaction_preparing
    domain_transaction_preparing(workspace, kind)
    operation_id = operation_id or "tx-" + uuid4().hex
    path = _record_path(workspace, operation_id)
    if _v_is_file(path):
        old = WorkspaceTransaction.model_validate_json(path.read_text(encoding="utf-8"))
        expected = {p: _sha(v) if v is not None else None for p, v in outputs.items()}
        if (old.book_id != _book(workspace) or old.kind != kind
                or {p: v["sha256"] for p, v in old.outputs.items()} != expected):
            raise ValueError("重复事务标识对应不同输入")
        return old
    record = WorkspaceTransaction(operation_id=operation_id, book_id=_book(workspace), kind=kind,
        scope_chapters=scope_chapters, shared_paths=shared_paths or [], outputs={},
        authority=authority or {}, input_bindings=input_bindings or {}, metadata=metadata or {})
    # Validate the complete write set before creating any staging files.
    stage = _directory(workspace) / operation_id
    for relative in outputs:
        target = _path(workspace, relative)
        if target.is_relative_to(stage) or stage.is_relative_to(target):
            raise ValueError("事务输出不得覆盖私有stage")
        _scope(record, relative)
    pending_workspace_transactions(workspace)  # Declare current journal bytes/membership.
    with private_staging(workspace, stage) as staging:
        for index, (relative, content) in enumerate(outputs.items()):
            target = _path(workspace, relative)
            before_data = _v_read_bytes(target) if _v_is_file(target) else None
            item = {"before_sha256": _sha(before_data) if before_data is not None else None,
                    "sha256": _sha(content) if content is not None else None,
                    "staged": None, "before_path": None}
            if before_data is not None:
                before = stage / f"{index}.before"
                staging.write_bytes(before, before_data)
                item["before_path"] = before.relative_to(workspace.root).as_posix()
            if content is not None:
                after = stage / f"{index}.after"
                staging.write_bytes(after, content)
                item["staged"] = after.relative_to(workspace.root).as_posix()
            record.outputs[relative] = item
    staging.verify()
    _fault("private_staging_complete")
    staging.verify()
    now = datetime.now(UTC).isoformat()
    record.events = [{"event_id": f"{operation_id}-{i}", "at": now,
        "event_type": event_type, "payload": payload} for i, (event_type, payload) in enumerate(events or [])]
    _save(workspace, record)
    from knowledge_product_workflow.operation_receipts import link_transaction
    link_transaction(workspace, record)
    return record


def pending_workspace_transactions(workspace: Workspace) -> list[dict]:
    # Admission reads current journal bytes on every call. Only model parsing is
    # shared by exact payload; a changed journal cannot reuse a prior decision.
    context = current_verification(workspace)
    result = []
    for path in _v_glob_files(_directory(workspace), "*.json", include_non_files=True):
        try:
            raw = _v_read_text(path, encoding="utf-8").encode("utf-8")
            record = (context.parsed_payload("workspace-transaction.pending.v1", raw,
                                            _parse_pending_transaction)
                      if context is not None else _parse_pending_transaction(raw))
            if record is not None:
                result.append(record)
        except (OSError, ValueError):
            result.append({"operation_id": path.stem, "state": "unknown", "shared_paths": ["unknown"]})
    return result


def _parse_pending_transaction(raw: bytes) -> dict | None:
    record = WorkspaceTransaction.model_validate_json(raw)
    return record.model_dump() if record.state != "completed" else None


def transaction_is_global(record: dict) -> bool:
    return (record.get("state") == "unknown" or not record.get("scope_chapters")
            or record.get("kind") in {"blueprint_revision", "binding_migration", "approval_mapping"}
            or any(p.startswith(("planning/", "research/")) or p == "runs/orchestration/state.json"
                   for p in record.get("outputs", {})))


def committed_reader_text(workspace: Workspace, relative: str) -> str | None:
    """Use the verified last committed version when another chapter is mid-commit."""
    for record in pending_workspace_transactions(workspace):
        if record.get("state") == "unknown":
            raise ValueError("未知事务，不能选择读版本")
        item = record["outputs"].get(relative)
        if item is not None:
            if item["before_sha256"] is None:
                return None
            before = _path(workspace, item["before_path"])
            if _hash(before) != item["before_sha256"]:
                raise ValueError("原读版本归档已变化")
            return before.read_text(encoding="utf-8")
    path = _path(workspace, relative)
    return path.read_text(encoding="utf-8") if _v_is_file(path) else None


def require_transaction_consistency(workspace: Workspace, *, chapters: set[int] | None = None,
                                    read_paths: set[str] | None = None) -> None:
    _require_consistent_records(pending_workspace_transactions(workspace), chapters=chapters,
                                read_paths=read_paths)


def _require_consistent_records(records, *, chapters=None, read_paths=None):
    pending = []
    for record in records:
        if read_paths is not None:
            affected = record["state"] == "unknown" or bool(set(record.get("outputs", {})) & read_paths)
        else:
            affected = (chapters is None or transaction_is_global(record)
                        or bool(set(record.get("scope_chapters", [])) & chapters))
        if affected:
            pending.append(record)
    if pending:
        raise ValueError("事务待恢复，禁止读取半提交版本：" + ",".join(r["operation_id"] for r in pending))


class _TransactionReadView:
    """One action's declared byte snapshot, never a later public admission."""

    def __init__(self, workspace, context):
        self.workspace = workspace
        self.context = context
        self.phase = None
        self.untracked = False
        self.records = []

    def current(self):
        self.context._ensure_open()
        if current_verification(self.workspace) is not self.context or _transaction_view.get() is not self:
            raise ValueError("事务读取视图不属于当前动作／验证上下文")
        if self.phase != self.context.phase or self.untracked:
            marker = self.context._untracked_reads
            self.records = pending_workspace_transactions(self.workspace)
            self.phase = self.context.phase
            self.untracked = self.context._untracked_reads != marker
        return copy.deepcopy(self.records)


_transaction_view = ContextVar("kp_transaction_read_view", default=None)


@contextmanager
def transaction_read_view(workspace):
    """Start a new action view, including when nested in another public action.

    Reads are lazy so malformed input still fails before deep state inspection.
    Every owned write advances phase; its next internal read renews the snapshot.
    The surrounding verification scope checks all bytes and directory membership
    before writes and before returning. Outside/aliased journals are never shared.
    """
    context = current_verification(workspace)
    if context is None:
        raise ValueError("事务读取视图必须绑定当前验证上下文")
    view = _TransactionReadView(workspace, context)
    parent = _transaction_view.get()
    token = _transaction_view.set(view)
    try:
        yield view
    finally:
        _transaction_view.reset(token)
        if parent is not None and parent.context is context:
            parent.phase = None  # A nested public action is a new admission boundary.


def has_transaction_read_view(workspace) -> bool:
    """Only the same lexical workspace and active action may use internal readers."""
    view = _transaction_view.get()
    return (view is not None and current_verification(workspace) is view.context
            and workspace.root.absolute() == view.workspace.root.absolute())


def require_transaction_consistency_in_view(workspace, *, chapters=None, read_paths=None):
    """Internal consumers of one admitted action; public admission stays fresh."""
    view = _transaction_view.get()
    if view is None or current_verification(workspace) is not view.context:
        return require_transaction_consistency(workspace, chapters=chapters, read_paths=read_paths)
    _require_consistent_records(view.current(), chapters=chapters, read_paths=read_paths)


def _append_event(workspace: Workspace, record: WorkspaceTransaction, event: dict) -> None:
    path = workspace.root / "runs/events.jsonl"
    raw = path.read_bytes()
    encoded = (json.dumps(event, ensure_ascii=True) + "\n").encode("utf-8")
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        try:
            existing = json.loads(line)
        except ValueError as error:
            offset = record.event_offsets.get(event["event_id"])
            prefix = b"".join(lines[:index])
            if (index != len(lines) - 1 or not offset or offset["length"] != len(prefix)
                    or offset["sha256"] != _sha(prefix) or not encoded.startswith(line)):
                raise ValueError("未知事件日志损坏，不能移除") from error
            _v_write_bytes(path, prefix + encoded)
            return
        if existing.get("event_id") == event["event_id"]:
            if existing != event:
                raise ValueError("事件标识冲突")
            if not line.endswith(b"\n") and index == len(lines) - 1:
                with _v_write_open(path, "ab") as handle:
                    handle.write(b"\n")
            return
    if raw and not raw.endswith(b"\n"):
        raise ValueError("未知事件日志边界")
    record.event_offsets[event["event_id"]] = {"length": len(raw), "sha256": _sha(raw)}
    _save(workspace, record)
    _fault("before_event")
    with _v_write_open(path, "ab") as handle:
        handle.write(encoded)
    _fault("after_event")


def commit_workspace_transaction(workspace: Workspace, transaction: WorkspaceTransaction) -> Path:
    # Reload the persisted journal; a caller-provided JSON state is not a completion receipt.
    _fault("prepared")
    recover_workspace_transaction(workspace, transaction.operation_id)
    return _record_path(workspace, transaction.operation_id)


def recover_workspace_transaction(workspace: Workspace, operation_id: str) -> dict:
    from knowledge_product_workflow.production_dependencies import binding_digest
    from knowledge_product_workflow.production_tasks import controller_lock_owned, production_lock

    lock = nullcontext() if controller_lock_owned(workspace) else production_lock(workspace)
    with lock:
        record = WorkspaceTransaction.model_validate_json(
            _record_path(workspace, operation_id).read_text(encoding="utf-8"))
        if record.book_id != _book(workspace) or record.operation_id != operation_id:
            raise ValueError("事务不属于当前书稿")
        for relative, item in record.outputs.items():
            _scope(record, relative)
            if item["before_sha256"] is not None:
                archived = _path(workspace, item["before_path"])
                if (not archived.is_relative_to(_directory(workspace) / operation_id)
                        or _hash(archived) != item["before_sha256"]):
                    raise ValueError("事务原稿归档缺失或变化")
            actual = _hash(_path(workspace, relative))
            allowed = {item["sha256"]} if record.state == "completed" else {
                item["before_sha256"], item["sha256"]}
            if actual not in allowed:
                raise ValueError("事务恢复目标已变化，不能覆盖其他修改")
        if record.state == "completed":
            _after_commit(workspace, record)
            return record.model_dump()
        for key, expected in record.input_bindings.items():
            output = record.outputs.get(key.split("#", 1)[0])
            if output and _hash(_path(workspace, key.split("#", 1)[0])) == output["sha256"]:
                continue
            if binding_digest(workspace, key) != expected:
                raise ValueError("事务实际依赖已变化")
        record.state = "applying"
        _save(workspace, record)
        for relative, item in record.outputs.items():
            target = _path(workspace, relative)
            if _hash(target) == item["sha256"]:
                continue
            _fault("before_replace:" + relative)
            if item["sha256"] is None:
                _v_unlink(target, missing_ok=True)
            else:
                staged = _path(workspace, item["staged"])
                if (not staged.is_relative_to(_directory(workspace) / operation_id)
                        or _hash(staged) != item["sha256"]):
                    raise ValueError("事务暂存文件缺失或变化")
                _v_mkdir(target.parent,parents=True,exist_ok=True)
                temporary = target.with_name(f".{target.name}.{operation_id}.tmp")
                _v_write_bytes(temporary, staged.read_bytes())
                replace_file(temporary, target)
            _fault("after_replace:" + relative)
        if record.kind == "production":
            from knowledge_product_workflow.production_intake import _finalize_commit_metadata
            native = dict(record.metadata["production_record"])
            _finalize_commit_metadata(workspace, native)
            native["state"] = "completed"
            native_path = workspace.root / "runs/production/commits" / f"{native['commit_id']}.json"
            temporary = native_path.with_suffix(".tmp")
            _v_write_text(temporary, json.dumps(native, ensure_ascii=False, indent=2), encoding="utf-8")
            replace_file(temporary, native_path)
        for event in record.events:
            _append_event(workspace, record, event)
        _fault("before_completed")
        record.state = "completed"
        _save(workspace, record)
        _after_commit(workspace, record)
        return record.model_dump()


def _after_commit(workspace: Workspace, record: WorkspaceTransaction) -> None:
    if record.kind == "chapter_submission":
        from knowledge_product_workflow.production_dependencies import finalize_serial_exports
        finalize_serial_exports(workspace, chapter_number=record.scope_chapters[0],
                                operation_id=record.operation_id)
    elif record.kind == "chapter_proof_recheck":
        from knowledge_product_workflow.production_dependencies import finalize_serial_exports
        finalize_serial_exports(workspace, chapter_number=record.scope_chapters[0])
    from knowledge_product_workflow.operation_receipts import transaction_completed
    transaction_completed(workspace, record)
