"""Control-plane transitions only; synthetic status is not domain acceptance."""

import json

from knowledge_product_workflow import runtime
from tests.test_chapter_content_plan import _approved_workspace


def test_active_author_lease_short_circuits_unused_debt_scan(tmp_path, monkeypatch):
    from knowledge_product_workflow import production_dependencies as dependencies
    from knowledge_product_workflow.workflows.authoring import create_chapter_draft
    from tests.test_deferred_repairs_v140 import serial_book

    workspace = serial_book(tmp_path)
    create_chapter_draft(workspace, 2)
    original = dependencies.deferred_repairs
    calls = []

    def observed(current):
        calls.append(current.root)
        return original(current)

    monkeypatch.setattr(dependencies, "deferred_repairs", observed)
    assert runtime._independent_chapter_status(workspace) is None
    assert calls == []
    assert runtime.inspect_workflow(workspace).target_chapter_number == 2


def test_ready_independent_chapter_keeps_current_debt_scan(tmp_path, monkeypatch):
    from knowledge_product_workflow import production_dependencies as dependencies
    from tests.test_deferred_repairs_v140 import serial_book

    workspace = serial_book(tmp_path)
    original = dependencies.deferred_repairs
    calls = []

    def observed(current):
        result = original(current)
        calls.append(result)
        return result

    monkeypatch.setattr(dependencies, "deferred_repairs", observed)
    status = runtime._independent_chapter_status(workspace)
    assert status.target_chapter_number == 2
    assert len(calls) == 1
    assert status.deferred_repairs == calls[0]


def setup_control(tmp_path, monkeypatch):
    w = _approved_workspace(tmp_path)
    output = w.root / "runs/control-proof.json"
    output.write_text("{}", encoding="utf-8")
    phase = {"target": 1, "reasons": ["还有必需证据待补齐"]}
    calls = []

    def status(workspace):
        calls.append(workspace.root)
        return runtime.WorkflowStatus(
            stage="quality_validation_pending",
            macro_phase=runtime.MacroPhase.ASSURANCE,
            target_chapter_number=phase["target"],
            recommended_skill="kp-quality-editor",
            next_command="kp validate",
            blocked_by=phase["reasons"],
            artifacts={"runs/control-proof.json": "present"},
        )

    monkeypatch.setattr(runtime, "inspect_workflow", status)
    runtime.start_run_intent(w)
    order = runtime.read_orchestration_work_order(w, runtime.create_orchestration_work_order(w))
    calls.clear()
    return w, output, phase, calls, order


def finish(w, output, order, value):
    output.write_text(json.dumps({"controlled_fixture": value}), encoding="utf-8")
    path = runtime.complete_orchestration_action(
        w,
        "validate",
        proof="合成控制动作完成，非领域资格",
        output_paths=[output],
        work_order_id=order.work_order_id,
    )
    return runtime.read_orchestration_work_order(w, path)


def test_auxiliary_capture_uses_one_pre_state_for_validation(tmp_path, monkeypatch):
    w, _output, _phase, calls, _order = setup_control(tmp_path, monkeypatch)
    state = runtime._read_orchestration_state(w)
    runtime._atomic_json_write(
        runtime._orchestration_state_path(w),
        state.model_copy(update={"request_capture_revision": 1}),
    )
    runtime.create_orchestration_work_order(w)
    calls.clear()
    assert runtime.capture_orchestration_update(w)
    assert len(calls) == 1


def test_completion_has_one_post_state_and_one_successor(tmp_path, monkeypatch):
    w, output, phase, calls, order = setup_control(tmp_path, monkeypatch)
    phase["target"] = 2
    completed = finish(w, output, order, 1)
    assert len(calls) == 1
    assert completed.completion_proof.successor_work_order_id
    count = len(list((w.root / "runs/orchestration/work-orders").glob("*.json")))
    calls.clear()
    finish(w, output, completed, 1)
    assert not calls
    assert len(list((w.root / "runs/orchestration/work-orders").glob("*.json"))) == count


def test_four_same_stage_target_advances_do_not_trigger_no_progress(tmp_path, monkeypatch):
    w, output, phase, _calls, order = setup_control(tmp_path, monkeypatch)
    for n in range(1, 5):
        phase["target"] = n + 1
        completed = finish(w, output, order, n)
        intent = runtime.read_run_intent(w)
        assert intent.state is runtime.RunIntentState.ACTIVE
        assert intent.consecutive_no_progress == 0
        order = runtime.read_orchestration_work_order(
            w, completed.completion_proof.successor_work_order_id
        )


def test_repeated_reports_without_new_obligations_still_stop_after_three(tmp_path, monkeypatch):
    w, output, _phase, _calls, order = setup_control(tmp_path, monkeypatch)
    for n in range(3):
        completed = finish(w, output, order, n)
        if n < 2:
            order = runtime.read_orchestration_work_order(
                w, completed.completion_proof.successor_work_order_id
            )
    intent = runtime.read_run_intent(w)
    assert intent.state is runtime.RunIntentState.BLOCKED
    assert intent.consecutive_no_progress == 3


def test_same_chapter_obligation_satisfied_is_progress(tmp_path, monkeypatch):
    w, output, phase, calls, order = setup_control(tmp_path, monkeypatch)
    phase["reasons"] = []
    finish(w, output, order, 1)
    assert runtime.read_run_intent(w).consecutive_no_progress == 0
    assert len(calls) == 1


def test_pause_after_domain_action_closes_without_successor(tmp_path, monkeypatch):
    w, output, phase, calls, order = setup_control(tmp_path, monkeypatch)
    count = len(list((w.root / "runs/orchestration/work-orders").glob("*.json")))
    runtime.pause_run_intent(w, "测试：领域完成后暂停")
    phase["target"] = 2
    completed = finish(w, output, order, 1)
    assert completed.completion_proof.successor_work_order_id is None
    assert runtime.read_run_intent(w).state is runtime.RunIntentState.PAUSED
    assert len(list((w.root / "runs/orchestration/work-orders").glob("*.json"))) == count
    assert len(calls) == 1
