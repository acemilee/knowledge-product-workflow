"""Workspace initialization and append-only workflow events."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    unlink as _v_unlink,
)
from knowledge_product_workflow.verification_context import (
    write_open as _v_write_open,
)
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)

WORKSPACE_DIRECTORIES = ("planning", "research", "chapters", "delivery", "runs")
WORKSPACE_OPTIONAL_DIRECTORIES = ("assets",)
_APPROVAL_PROFILE_FILE = "approval-profile.json"


@dataclass(frozen=True)
class Workspace:
    """Filesystem boundary for a single auditable book run."""

    root: Path

    @classmethod
    def create(cls, root: Path, book_id: str, *, current_policy: bool = False,
               binding_policy_revision: int = 1) -> Workspace:
        """Create an empty workspace without overwriting an existing run."""
        workspace = cls(root=(root.expanduser() / book_id).resolve())
        if _v_exists(workspace.root):
            raise FileExistsError(f"书稿工作区已存在：{workspace.root}")
        for directory in (*WORKSPACE_DIRECTORIES, *WORKSPACE_OPTIONAL_DIRECTORIES):
            _v_mkdir(workspace.root / directory,parents=True,exist_ok=False)
        (workspace.root / "runs" / "events.jsonl").touch(exist_ok=False)
        workspace.append_event("workspace_initialized", {"book_id": book_id})
        # A workspace created by the current CLI is never a legacy workspace.  Keep
        # this control-plane marker separate from reader-facing book artifacts.
        from knowledge_product_workflow.runtime import initialize_new_orchestration

        initialize_new_orchestration(workspace, current_policy=current_policy,
                                     binding_policy_revision=binding_policy_revision)

        return workspace

    @classmethod
    def open(cls, root: Path) -> Workspace:
        """Open an existing workspace after checking its required structure."""
        normalized = _locate_workspace_root(root)
        if normalized is None:
            raise FileNotFoundError(f"不是有效的知识产品工作区：{root.expanduser().resolve()}")
        return cls(root=normalized)

    def append_event(
        self, event_type: str, payload: dict[str, object], *, event_id: str | None = None
    ) -> Path:
        """Record a structured event without mutating prior event rows."""
        event = {
            "event_id": event_id or f"event-{uuid4().hex}",
            "at": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
        event_path = self.root / "runs" / "events.jsonl"
        with _v_write_open(event_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True) + "\n")
        return event_path

    def approval_identity(self) -> str | None:
        """Return the one locally configured human approval identity, if present."""
        profile_path = self.root / "runs" / _APPROVAL_PROFILE_FILE
        if not _v_is_file(profile_path):
            return None
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("审批身份配置格式无效") from error
        identity = profile.get("approval_identity") if isinstance(profile, dict) else None
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("审批身份配置缺少有效身份")
        return identity.strip()

    def configure_approval_identity(self, identity: str) -> str:
        """Persist the first human approval identity for later approval gates."""
        normalized = identity.strip()
        if not normalized:
            raise ValueError("审批身份不能为空")
        profile_path = self.root / "runs" / _APPROVAL_PROFILE_FILE
        _v_write_text(profile_path,
            json.dumps({"approval_identity": normalized}, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.append_event("approval_identity_configured", {"approval_identity": normalized})
        return normalized

    @contextmanager
    def review_batch_lock(self) -> Iterator[None]:
        """Serialize review-batch commits without changing unrelated workspace files."""
        lock_path = self.root / "runs" / "review-batches.lock"
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise RuntimeError("已有审稿批次正在提交，请稍后重试。") from error
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            yield
        finally:
            try:
                _v_unlink(lock_path)
            except FileNotFoundError:
                pass


def _locate_workspace_root(root: Path) -> Path | None:
    """Resolve a workspace path, including a relative path used below its original parent."""
    expanded = root.expanduser()
    candidates = [expanded.resolve()]
    if not expanded.is_absolute():
        for parent in Path.cwd().resolve().parents:
            candidates.append((parent / expanded).resolve())
    for candidate in candidates:
        if candidate.is_dir() and all((candidate / directory).is_dir() for directory in WORKSPACE_DIRECTORIES):
            return candidate
    return None
