"""Receipts describe owned work; they never grant new domain authority."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from knowledge_product_workflow import operation_receipts as operations
from knowledge_product_workflow.workspace import Workspace
from knowledge_product_workflow.workspace_transactions import (
    commit_workspace_transaction,
    prepare_workspace_transaction,
)


def test_domain_commit_is_queryable_without_deep_status_or_writes(tmp_path, monkeypatch):
    w = Workspace.create(tmp_path, "receipt")
    with operations.operation_scope(w, action="submit-chapter", chapter_number=2) as op:
        tx = prepare_workspace_transaction(
            w, {"chapters/chapter-02.md": b"new"}, kind="chapter_submission", scope_chapters=[2]
        )
        # This storage fixture has no contracts/exports to finalize.
        monkeypatch.setattr(
            "knowledge_product_workflow.workspace_transactions._after_commit",
            lambda workspace, record: operations.transaction_completed(workspace, record),
        )
        commit_workspace_transaction(w, tx)
        status = operations.operation_status(w, op.operation_id)
        assert status["phase"] == "domain_committed_control_pending"
        assert status["transaction_id"] == tx.operation_id
    before = {p: p.read_bytes() for p in w.root.rglob("*") if p.is_file()}
    monkeypatch.setattr(
        "knowledge_product_workflow.runtime.inspect_workflow",
        lambda _: pytest.fail("light query must not inspect"),
    )
    assert (
        operations.operation_status(w, op.operation_id)["phase"]
        == "domain_committed_control_pending"
    )
    assert before == {p: p.read_bytes() for p in w.root.rglob("*") if p.is_file()}


def test_duplicate_unknown_operation_cannot_resubmit(tmp_path):
    w = Workspace.create(tmp_path, "receipt")
    with operations.operation_scope(
        w, action="submit-chapter", chapter_number=2, operation_id="op-owned"
    ):
        pass
    with (
        pytest.raises(ValueError, match="已有|重发"),
        operations.operation_scope(
            w, action="submit-chapter", chapter_number=2, operation_id="op-owned"
        ),
    ):
        pytest.fail("must not execute domain operation again")


def test_bad_cross_workspace_and_oversized_receipts_fail_closed(tmp_path):
    w = Workspace.create(tmp_path, "receipt")
    other = Workspace.create(tmp_path, "other")
    with operations.operation_scope(w, action="submit-chapter", chapter_number=2) as op:
        pass
    raw = operations.receipt_path(w, op.operation_id).read_bytes()
    target = operations.receipt_path(other, op.operation_id)
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    assert operations.operation_status(other, op.operation_id)["phase"] == "recovery_required"
    target.write_bytes(b" " * 65537)
    assert operations.operation_status(other, op.operation_id)["phase"] == "recovery_required"
    with pytest.raises(ValueError):
        operations.operation_status(w, "../elsewhere")


def test_failure_before_any_transaction_is_not_reported_committed(tmp_path):
    w = Workspace.create(tmp_path, "receipt")
    with (
        pytest.raises(ValueError, match="validation"),
        operations.operation_scope(w, action="submit-chapter", chapter_number=2) as op,
    ):
        raise ValueError("validation failed")
    assert operations.operation_status(w, op.operation_id)["phase"] == "failed_before_commit"


@pytest.mark.parametrize("continuation", ["finish", "recover"])
def test_valid_pending_handoff_can_finish_its_committed_operation(tmp_path, continuation):
    from knowledge_product_workflow import runtime
    from knowledge_product_workflow.chapter_compliance import submit_chapter

    w, source, mapping = _submission(tmp_path)
    order_id = runtime.capture_orchestration_action(w, "submit-chapter", chapter_number=1)
    order = runtime.read_orchestration_work_order(w, order_id)
    if order.lease:
        runtime.release_chapter_lease(w, order.lease.lease_id, order_id)
    pending = order.model_copy(
        update={"lifecycle": runtime.WorkOrderLifecycle.PENDING, "lease": None}
    )
    runtime._atomic_json_write(runtime._work_order_path(w, order_id), pending)
    assert runtime.validate_orchestration_work_order(w, order_id, command="submit-chapter")
    with operations.operation_scope(
        w, action="submit-chapter", chapter_number=1, work_order_id=order_id
    ) as op:
        submit_chapter(w, 1, source, mapping)
        resume = (
            operations.finish_operation
            if continuation == "finish"
            else operations.recover_operation
        )
        assert resume(w, op.operation_id).phase == "completed"


def _submission(tmp_path):
    from knowledge_product_workflow.humanize import record_humanize_check
    from tests.test_chapter_compliance import _body, _mapping, _setup, _workspace_input

    w, _ = _setup(tmp_path)
    source = w.root / "runs/body.md"
    source.write_text(_body(), encoding="utf-8")
    record_humanize_check(w, 1, source)
    mapping = _workspace_input(w, "mapping.json", _mapping())
    return w, source, mapping


@pytest.mark.parametrize(
    "phase",
    [
        "transaction_prepared",
        "domain_committed",
        "close_saved",
        "after_intent",
        "after_successor",
        "before_final_receipt",
    ],
)
def test_real_process_exit_recovers_original_operation_only(tmp_path, phase):
    import knowledge_product_workflow
    from knowledge_product_workflow.production_tasks import recover_production_lock

    w, source, mapping = _submission(tmp_path)
    site = str(Path(knowledge_product_workflow.__file__).resolve().parent.parent)
    script = f"""import os, sys
sys.path.insert(0, {site!r})
import knowledge_product_workflow.operation_receipts as ops
import knowledge_product_workflow.workspace_transactions as tx
import knowledge_product_workflow.runtime as rt
from knowledge_product_workflow.cli import app
def stop(phase):
    if phase == {phase!r}: os._exit(23)
ops._fault=stop
rt._completion_fault=stop
tx._fault=lambda phase: stop('transaction_prepared') if phase == 'prepared' else None
sys.argv=['kp','submit-chapter','1','--workspace',{str(w.root)!r},'--from',{str(source)!r},'--compliance',{str(mapping)!r},'--operation-id','op-killed']
app()
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        capture_output=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": site},
        check=False,
    )
    assert result.returncode == 23, result.stderr.decode(errors="replace")
    status = operations.operation_status(w, "op-killed")
    assert status["phase"] != "completed"
    assert status["transaction_id"]
    recover_production_lock(w)
    first = operations.recover_operation(w, "op-killed")
    body = (w.root / "chapters/chapter-01.md").read_bytes()
    events = (w.root / "runs/events.jsonl").read_bytes()
    assert first.phase == "completed"
    assert operations.recover_operation(w, "op-killed") == first
    assert (w.root / "chapters/chapter-01.md").read_bytes() == body
    assert (w.root / "runs/events.jsonl").read_bytes() == events
    assert (
        sum(e["event_type"] == "chapter_submitted" for e in map(json.loads, events.splitlines()))
        == 1
    )
    assert (
        sum(
            e["event_type"] == "orchestration_work_order_completed"
            for e in map(json.loads, events.splitlines())
        )
        == 1
    )


def test_elapsed_ttl_closes_only_committed_action_without_new_domain_authority(
    tmp_path, monkeypatch
):
    from knowledge_product_workflow import runtime
    from knowledge_product_workflow.chapter_compliance import submit_chapter

    w, source, mapping = _submission(tmp_path)
    order = runtime.capture_orchestration_action(w, "submit-chapter", chapter_number=1)
    with operations.operation_scope(
        w, action="submit-chapter", chapter_number=1, work_order_id=order
    ) as op:
        submit_chapter(w, 1, source, mapping)
        before = (w.root / "chapters/chapter-01.md").read_bytes()
        original = runtime._is_live
        monkeypatch.setattr(
            runtime,
            "_is_live",
            lambda value: False if value.work_order_id == order else original(value),
        )
        assert operations.finish_operation(w, op.operation_id).phase == "completed"
        assert (w.root / "chapters/chapter-01.md").read_bytes() == before
        with pytest.raises(ValueError, match="过期|完成|取消"):
            runtime.validate_orchestration_work_order(w, order, command="submit-chapter")


def test_external_changes_or_revocation_do_not_use_receipt_to_write(tmp_path):
    from knowledge_product_workflow import runtime
    from knowledge_product_workflow.chapter_compliance import submit_chapter

    w, source, mapping = _submission(tmp_path)
    order = runtime.capture_orchestration_action(w, "submit-chapter", chapter_number=1)
    with operations.operation_scope(
        w, action="submit-chapter", chapter_number=1, work_order_id=order
    ) as op:
        submit_chapter(w, 1, source, mapping)
    runtime.cancel_orchestration_work_order(w, order, "测试撤销")
    before = (w.root / "chapters/chapter-01.md").read_bytes()
    with pytest.raises(ValueError, match="撤销"):
        operations.recover_operation(w, op.operation_id)
    assert (w.root / "chapters/chapter-01.md").read_bytes() == before
