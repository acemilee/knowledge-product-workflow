"""File/event recovery at every durable boundary, without native model calls."""

import json

import pytest

import knowledge_product_workflow.workspace_transactions as module
from knowledge_product_workflow.workspace import Workspace


@pytest.mark.parametrize("stop", ["prepared", "before_replace:chapters/chapter-02.md",
    "after_replace:chapters/chapter-02.md", "after_replace:runs/humanize/chapter-02.json",
    "before_event", "after_event", "before_completed"])
def test_every_boundary_recovers_once_and_preserves_before_bytes(tmp_path, monkeypatch, stop):
    workspace = Workspace.create(tmp_path, "transaction")
    chapter = workspace.root / "chapters/chapter-02.md"
    chapter.write_bytes(b"old")
    transaction = module.prepare_workspace_transaction(workspace,
        {"chapters/chapter-02.md": b"new", "runs/humanize/chapter-02.json": b'{"ok":true}'},
        kind="chapter_submission", scope_chapters=[2], operation_id="tx-test",
        events=[("chapter_submitted", {"chapter_number": 2})])

    def fault(phase):
        if phase == stop:
            raise SystemExit("simulated termination")
    monkeypatch.setattr(module, "_fault", fault)
    with pytest.raises(SystemExit):
        module.commit_workspace_transaction(workspace, transaction)
    assert module.pending_workspace_transactions(workspace)
    with pytest.raises(ValueError, match="待恢复|半提交"):
        module.require_transaction_consistency(workspace, chapters={2})
    module.require_transaction_consistency(workspace, chapters={3})
    monkeypatch.setattr(module, "_fault", lambda _: None)
    first = module.recover_workspace_transaction(workspace, "tx-test")
    assert first["state"] == "completed"
    assert chapter.read_bytes() == b"new"
    assert (workspace.root / first["outputs"]["chapters/chapter-02.md"]["before_path"]).read_bytes() == b"old"
    events = (workspace.root / "runs/events.jsonl").read_bytes()
    assert module.recover_workspace_transaction(workspace, "tx-test")["state"] == "completed"
    assert (workspace.root / "runs/events.jsonl").read_bytes() == events
    assert sum(e.get("event_type") == "chapter_submitted"
               for e in map(json.loads, events.splitlines())) == 1


def test_recovery_refuses_third_party_write_and_scope_escape(tmp_path, monkeypatch):
    workspace = Workspace.create(tmp_path, "conflict")
    chapter = workspace.root / "chapters/chapter-02.md"
    chapter.write_bytes(b"old")
    transaction = module.prepare_workspace_transaction(workspace, {"chapters/chapter-02.md": b"new"},
        kind="chapter_submission", scope_chapters=[2], operation_id="tx-conflict")
    monkeypatch.setattr(module, "_fault", lambda _: (_ for _ in ()).throw(SystemExit()))
    with pytest.raises(SystemExit):
        module.commit_workspace_transaction(workspace, transaction)
    monkeypatch.setattr(module, "_fault", lambda _: None)
    chapter.write_bytes(b"other writer")
    with pytest.raises(ValueError, match="变化|冲突"):
        module.recover_workspace_transaction(workspace, "tx-conflict")
    assert chapter.read_bytes() == b"other writer"
    with pytest.raises(ValueError, match="范围|越界"):
        module.prepare_workspace_transaction(workspace, {"../outside.md": b"bad"},
            kind="chapter_submission", scope_chapters=[2])


def test_shared_half_commit_blocks_every_reader(tmp_path, monkeypatch):
    workspace = Workspace.create(tmp_path, "shared")
    transaction = module.prepare_workspace_transaction(workspace,
        {"planning/curriculum-blueprint.md": b"new"}, kind="blueprint_revision",
        scope_chapters=[2], shared_paths=["planning/curriculum-blueprint.md"])
    monkeypatch.setattr(module, "_fault", lambda _: (_ for _ in ()).throw(SystemExit()))
    with pytest.raises(SystemExit):
        module.commit_workspace_transaction(workspace, transaction)
    with pytest.raises(ValueError):
        module.require_transaction_consistency(workspace, chapters={3})


def test_unknown_torn_event_is_not_silently_removed(tmp_path):
    workspace = Workspace.create(tmp_path, "bad-tail")
    tx = module.prepare_workspace_transaction(workspace, {"chapters/chapter-02.md": b"new"},
        kind="chapter_submission", scope_chapters=[2], events=[("chapter_submitted", {})])
    events = workspace.root / "runs/events.jsonl"
    old = events.read_bytes() + b'{"unknown":'
    events.write_bytes(old)
    with pytest.raises(ValueError, match="事件|日志"):
        module.commit_workspace_transaction(workspace, tx)
    assert events.read_bytes() == old


def test_real_serial_submit_is_blocked_until_its_journal_recovers(tmp_path, monkeypatch):
    from knowledge_product_workflow.chapter_compliance import check_chapter, submit_chapter
    from knowledge_product_workflow.humanize import record_humanize_check
    from tests.test_chapter_compliance import _body, _mapping, _setup, _workspace_input
    workspace, _ = _setup(tmp_path)
    source = workspace.root / "runs/chapter.md"
    source.write_text(_body(), encoding="utf-8")
    record_humanize_check(workspace, 1, source)
    mapping = _workspace_input(workspace, "mapping.json", _mapping())
    def fault(phase):
        if phase == "after_replace:chapters/chapter-01.md":
            raise SystemExit("interrupt ordinary serial submission")
    monkeypatch.setattr(module, "_fault", fault)
    with pytest.raises(SystemExit):
        submit_chapter(workspace, 1, source, mapping)
    pending = module.pending_workspace_transactions(workspace)
    assert len(pending) == 1 and pending[0]["kind"] == "chapter_submission"
    with pytest.raises(ValueError, match="待恢复|半提交"):
        check_chapter(workspace, 1, source)
    monkeypatch.setattr(module, "_fault", lambda _: None)
    module.recover_workspace_transaction(workspace, pending[0]["operation_id"])
    assert not module.pending_workspace_transactions(workspace)
    assert check_chapter(workspace, 1, source)["checks"]
