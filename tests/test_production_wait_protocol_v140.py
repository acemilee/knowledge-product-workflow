"""Offline protocol only: fake clocks are not real host UI or capacity evidence."""

from pathlib import Path

import pytest

from knowledge_product_workflow import operation_receipts as operations
from knowledge_product_workflow.production_controller import NativeProductionSession
from knowledge_product_workflow.workspace import Workspace


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def test_qualification_observation_timeout_keeps_original_task_and_connection(tmp_path):
    from types import SimpleNamespace

    from knowledge_product_workflow.production_controller import NativeInlineBridge
    from knowledge_product_workflow.production_host import NativeTaskLedger

    workspace = Workspace.create(tmp_path, "qualification-observation")
    client = SimpleNamespace(native_origin=False, next_event=lambda **_: None, model_turns=1)
    bridge = NativeInlineBridge(client, model="fixture", max_children=4)
    bridge.parent_id = "parent"
    bridge.ledger = NativeTaskLedger("parent", {"waiting": {"candidate_version": 1,
        "input_manifest_sha256": "a" * 64}})
    bridge.ledger.consume({"method": "item/completed", "params": {"threadId": "parent",
        "item": {"type": "subAgentActivity", "kind": "started", "agentThreadId": "child",
                 "agentPath": "/root/waiting"}}})
    interruptions = []
    bridge.interrupt = lambda: interruptions.append(True)
    session = NativeProductionSession(workspace, bridge)

    result = session.apply({"op": "collect-qualification", "timeout": 0.002})

    assert result["status"] == "pending"
    assert bridge.ledger.active_children == {"child"}
    assert client.model_turns == 1 and interruptions == []


def test_qualification_transport_failure_still_stops_original_observation(tmp_path):
    from types import SimpleNamespace

    from knowledge_product_workflow.production_controller import NativeInlineBridge
    from knowledge_product_workflow.production_host import NativeProtocolError, NativeTaskLedger

    workspace = Workspace.create(tmp_path, "qualification-eof")

    def ended(**_):
        raise NativeProtocolError("fixture EOF; outcome unknown")

    client = SimpleNamespace(native_origin=False, next_event=ended, model_turns=1)
    bridge = NativeInlineBridge(client, model="fixture", max_children=4)
    bridge.parent_id = "parent"
    bridge.ledger = NativeTaskLedger("parent", {})
    interruptions = []
    bridge.interrupt = lambda: interruptions.append(True)
    session = NativeProductionSession(workspace, bridge)

    with pytest.raises(NativeProtocolError, match="EOF"):
        session.apply({"op": "collect-qualification", "timeout": 0.002})

    assert client.model_turns == 1 and interruptions == [True]


def test_serial_twenty_minutes_one_owner_heartbeats_and_one_attention(tmp_path):
    w = Workspace.create(tmp_path, "wait")
    frames = []
    clock = Clock()
    with operations.operation_scope(w, action="submit-chapter", chapter_number=2) as op:

        def emit(frame):
            frames.append(frame)
            if frame["kind"] == "heartbeat":
                with pytest.raises(ValueError, match="等待|owner"):
                    operations.wait_operation(w, op.operation_id, timeout=1)
            if clock.value >= 1200 and frame["kind"] == "heartbeat":
                operations._save(w, operations._load(w, op.operation_id), phase="completed")

        result = operations.wait_operation(
            w, op.operation_id, timeout=1230, emit=emit, clock=clock, sleep=clock.sleep
        )
    assert result["reason"] == "completed"
    beats = [f["elapsed"] for f in frames if f["kind"] == "heartbeat"]
    assert len(beats) >= 40 and max(b - a for a, b in zip([0, *beats], beats)) <= 60
    assert sum(f["kind"] == "attention" for f in frames) == 1
    assert clock.value >= 1200


def test_serial_timeout_retains_unknown_owner_and_does_not_recover(tmp_path):
    w = Workspace.create(tmp_path, "wait")
    clock = Clock()
    with operations.operation_scope(w, action="submit-chapter", chapter_number=2) as op:
        before = operations.receipt_path(w, op.operation_id).read_bytes()
        result = operations.wait_operation(
            w, op.operation_id, timeout=61, clock=clock, sleep=clock.sleep
        )
        assert result["reason"] == "deadline_unknown"
        assert operations.receipt_path(w, op.operation_id).read_bytes() == before


def test_native_twenty_minutes_internal_collects_no_model_poll_or_coordination(
    tmp_path, monkeypatch
):
    w = Workspace.create(tmp_path, "wait")
    session = NativeProductionSession(w, object())
    session.run_id = "offline-protocol"
    clock, frames, calls = Clock(), [], []

    def apply(command):
        calls.append(command)
        assert command["op"] == "collect"
        clock.sleep(command["timeout"])
        return {"received": ["one-real-event"] if clock.value >= 1200 else [], "failed": {}}

    monkeypatch.setattr(session, "apply", apply)
    result = session.run_until_attention(timeout=1230, emit=frames.append, clock=clock)
    assert result["reason"] == "results_ready"
    assert len(calls) == 40 and all(c["timeout"] <= 30 for c in calls)
    assert sum(f["kind"] == "attention" for f in frames) == 1
    assert max(f["elapsed"] for f in frames if f["kind"] == "heartbeat") >= 1170


def test_native_timeout_and_pause_never_release_or_redispatch(tmp_path, monkeypatch):
    from knowledge_product_workflow.runtime import pause_run_intent, start_run_intent

    w = Workspace.create(tmp_path, "wait")
    session = NativeProductionSession(w, object())
    session.run_id = "offline-protocol"
    clock, calls = Clock(), []

    def apply(command):
        calls.append(command["op"])
        clock.sleep(command["timeout"])
        return {"received": [], "failed": {}}

    monkeypatch.setattr(session, "apply", apply)
    assert session.run_until_attention(timeout=60, clock=clock)["reason"] == "deadline_unknown"
    start_run_intent(w)
    pause_run_intent(w)
    before = len(calls)
    assert session.run_until_attention(timeout=60, clock=clock)["reason"] == "paused"
    assert len(calls) == before


def test_native_single_owner_rejects_nested_wait_and_transport_failure_is_once(
    tmp_path, monkeypatch
):
    w = Workspace.create(tmp_path, "wait")
    session = NativeProductionSession(w, object())
    session.run_id = "offline-protocol"
    frames, calls = [], []

    def collect(command):
        calls.append(command)
        with pytest.raises(ValueError, match="owner"):
            session.run_until_attention(timeout=1)
        raise OSError("simulated EOF; remote slots unknown")

    monkeypatch.setattr(session, "apply", collect)
    result = session.run_until_attention(timeout=60, emit=frames.append)
    assert result["reason"] == "transport_unknown" and result["retry"] is False
    assert len(calls) == 1 and len(frames) == 1


def test_existing_controller_can_freeze_with_its_owned_lock(tmp_path):
    from knowledge_product_workflow.production_context import prepare_candidate_context
    from knowledge_product_workflow.production_tasks import ProductionTask, production_lock
    from tests.test_chapter_content_plan import _approved_workspace

    w = _approved_workspace(tmp_path)
    source = w.root / "runs/body.md"
    source.write_text("# 1. 候选\n\n## 1.1 判断\n\n候选材料。", encoding="utf-8")
    task = ProductionTask(
        task_id="writer-test",
        run_id="run-test",
        chapter_number=1,
        role="writer",
        input_manifest_sha256="fixture",
        source_relative="runs/body.md",
    )
    with production_lock(w):
        context = prepare_candidate_context(w, task)
    assert context.read_input("runs/body.md") == source.read_text(encoding="utf-8")


def test_first_status_commands_expose_wait_protocol_without_claiming_host_integration(tmp_path):
    import json

    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app
    from knowledge_product_workflow.runtime import inspect_workflow

    w = Workspace.create(tmp_path, "wait")
    guide = inspect_workflow(w).execution_guidance
    assert "pending_U11" in guide["host_no_inference_wait"]
    assert "--claim" in guide["research_batch"]
    for command in ["operation-status", "wait-operation", "recover-operation"]:
        result = CliRunner().invoke(app, ["commands", "--all", "--json", "--command", command])
        assert result.exit_code == 0 and json.loads(result.output)["commands"][0]["purpose"]


def test_native_eof_preempts_wait_without_receiving_or_releasing_remote_tasks(
    tmp_path, monkeypatch
):
    import io
    import json
    from types import SimpleNamespace

    from knowledge_product_workflow.production_controller import serve_native_session

    w = Workspace.create(tmp_path, "wait")
    bridge = SimpleNamespace(connect=lambda: None)
    session = NativeProductionSession(w, bridge)
    session.run_id = "offline-protocol"
    monkeypatch.setattr(
        session, "apply", lambda _: pytest.fail("EOF must not start collect or dispatch")
    )
    output = io.StringIO()
    serve_native_session(
        session, io.StringIO('{"op":"run_until_attention","timeout":1200}\n'), output
    )
    frames = [json.loads(line) for line in output.getvalue().splitlines()]
    assert frames[0]["event"]["reason"] == "input_closed_or_control_requested"
    assert len([f for f in frames if f.get("event", {}).get("kind") == "attention"]) == 1
    assert frames[-1]["ok"] and frames[-1]["result"]["unknown_slots_retained"]


def test_native_control_tail_checks_deadline_before_any_automatic_action(tmp_path):
    w = Workspace.create(tmp_path, "wait")
    session = NativeProductionSession(w, object())
    session.run_id = "offline-protocol"
    clock = Clock()
    assert (
        session._advance_wait_actions({"dispatch"}, (), deadline=0, clock=clock)
        == "deadline_unknown"
    )


def test_ready_read_that_consumes_deadline_cannot_start_freeze(tmp_path, monkeypatch):
    from types import SimpleNamespace

    w = Workspace.create(tmp_path, "wait")
    session = NativeProductionSession(w, object())
    session.run_id = "offline-protocol"
    clock = Clock()
    writer = SimpleNamespace(role="writer", state="local_check", candidate_context_path=None)

    def resumed(*args, **kwargs):
        clock.sleep(2)
        return SimpleNamespace(tasks=[writer])

    monkeypatch.setattr(
        "knowledge_product_workflow.production_tasks.resume_production_run", resumed
    )
    monkeypatch.setattr(
        session, "apply", lambda _: pytest.fail("deadline elapsed during ready read")
    )
    assert (
        session._advance_wait_actions({"freeze"}, (), deadline=1, clock=clock) == "deadline_unknown"
    )


def test_native_idle_collect_does_not_reprocess_previous_receipts(tmp_path, monkeypatch):
    from types import SimpleNamespace

    w = Workspace.create(tmp_path, "wait")
    bridge = SimpleNamespace(collect=lambda **_: {"results": {}, "failed": {}, "status": "pending"})
    session = NativeProductionSession(w, bridge)
    session.run_id = "offline-protocol"
    monkeypatch.setattr(
        "knowledge_product_workflow.production_controller.receive_native_results",
        lambda *_, **__: pytest.fail("idle must not revisit completed task proofs"),
    )
    assert session.apply({"op": "collect", "timeout": 30})["received"] == []


def test_quality_attention_keeps_same_session_available_for_explicit_collection(
    tmp_path, monkeypatch
):
    import io
    import json
    import queue
    from types import SimpleNamespace

    from knowledge_product_workflow.production_controller import serve_native_session

    workspace = Workspace.create(tmp_path, "quality-attention")
    commands = queue.Queue()
    commands.put(json.dumps({"op": "run_until_attention", "timeout": 10,
                             "authorized_actions": ["dispatch"]}))
    stop = object()
    calls = []
    bridge = SimpleNamespace(
        connect=lambda: calls.append("connect"),
        collect=lambda **_: calls.append("collect") or {
            "results": {"other-task": {}}, "failed": {}
        },
    )
    session = NativeProductionSession(workspace, bridge)
    session.run_id = "offline-attention"
    run = SimpleNamespace(tasks=[SimpleNamespace(state="revision_required")])
    monkeypatch.setattr(
        "knowledge_product_workflow.production_tasks.resume_production_run", lambda *_, **__: run
    )
    monkeypatch.setattr(
        "knowledge_product_workflow.production_controller.receive_native_results",
        lambda *_, **__: ["other-task"],
    )
    original_apply = session.apply

    def apply(command):
        if command["op"] == "status":
            return {"quality": "revise"}
        assert command["op"] == "collect"  # No new dispatch, freeze, commit or recovery.
        return original_apply(command)

    monkeypatch.setattr(session, "apply", apply)

    class Input:
        def __iter__(self):
            while True:
                line = commands.get(timeout=3)
                if line is stop:
                    return
                yield line + "\n"

    class Output(io.StringIO):
        def write(self, line):
            frame = json.loads(line)
            result = frame.get("result", {})
            if result.get("reason") == "task_attention_required":
                commands.put(json.dumps({"op": "collect", "timeout": 1}))
            elif result.get("received") == ["other-task"]:
                commands.put(json.dumps({"op": "close"}))
                commands.put(stop)
            return super().write(line)

    output = Output()
    serve_native_session(session, Input(), output)
    frames = [json.loads(line) for line in output.getvalue().splitlines()]
    assert calls == ["connect", "collect"]
    assert frames[0]["event"]["reason"] == "task_attention_required"
    assert any(frame.get("result", {}).get("received") == ["other-task"] for frame in frames)
    assert frames[-1]["result"] == {"quality": "revise"}
    assert not session._waiting


def test_required_comparisons_are_frozen_and_inline_without_unrelated_sections(tmp_path):
    import json

    from knowledge_product_workflow.developmental import (
        StructureReviewInput,
        create_structure_review_template,
        record_structure_review,
    )
    from knowledge_product_workflow.production_context import prepare_candidate_context
    from knowledge_product_workflow.production_tasks import ProductionTask
    from tests.test_structure_review_v133 import _fixture, simulated_review

    w, source, body = _fixture(tmp_path)
    source.write_text(body, encoding="utf-8")
    other = w.root / "chapters/chapter-02.md"
    other.write_text(
        "# 2. 对照\n\n## 2.1 输入\n必需对照原文。\n\n## 2.2 其他\nUNRELATED-SECTION\n",
        encoding="utf-8",
    )
    original = json.loads(simulated_review(w, source).read_text(encoding="utf-8"))
    template = create_structure_review_template(w, 1, source)
    record_structure_review(
        w,
        1,
        source,
        StructureReviewInput(
            template_id=template["template_id"],
            observations=[{**o, "compared_unit_ids": ["2.1"]} for o in original["observations"]],
        ),
    )
    writer = ProductionTask(
        task_id="writer-test",
        run_id="run-compare",
        chapter_number=1,
        role="writer",
        input_manifest_sha256="fixture",
        source_relative=source.relative_to(w.root).as_posix(),
    )
    context = prepare_candidate_context(w, writer)
    assert "chapters/chapter-02.md" in context.input_bindings
    assert "chapters/chapter-02.md#unit=2.1" in context.live_bindings
    reviewer = ProductionTask(
        task_id="review-test",
        run_id="run-compare",
        chapter_number=1,
        role="reviewer",
        input_manifest_sha256="fixture",
        candidate_context_path=str(Path(context.snapshot_root).parent / "context.json"),
    )
    session = NativeProductionSession(w, object())
    session.run_id = "run-compare"
    envelope = session._envelope(reviewer)
    assert envelope["candidate_body"] == body
    assert "必需对照原文" in envelope["comparison_units"]["2.1"]
    assert "UNRELATED-SECTION" not in envelope["comparison_units"]["2.1"]
    assert not ({"chat", "messages", "history"} & set(envelope))
