"""出版准入只复用同一动作的写前状态，原工单和写后检查仍执行。"""
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow import cli, runtime
from tests.test_orchestration_efficiency_v140 import setup_control


def test_validation_cli_has_one_pre_state_and_one_fresh_post_state(tmp_path, monkeypatch):
    workspace, output, _phase, calls, order = setup_control(tmp_path, monkeypatch)
    observed = []
    def domain(current):
        observed.append(('domain', len(calls)))
        return output, SimpleNamespace(publishable=True)
    monkeypatch.setattr(cli, 'run_validation', domain)
    result = CliRunner().invoke(cli.app, ['validate','--workspace',str(workspace.root)])
    assert result.exit_code == 0, result.output
    assert observed == [('domain', 1)]
    assert len(calls) == 2  # The domain result is followed by a new status computation.
    assert runtime.read_orchestration_work_order(workspace, order.work_order_id).completion_proof


def test_direct_ordinary_capture_keeps_its_own_fresh_inspection(tmp_path, monkeypatch):
    workspace, _, _, calls, order = setup_control(tmp_path, monkeypatch)
    assert runtime.capture_orchestration_action(workspace, 'validate') == order.work_order_id
    assert len(calls) == 1
    calls.clear()
    assert runtime.capture_orchestration_action(workspace, 'validate') == order.work_order_id
    assert len(calls) == 1


def test_preparation_rejects_input_drift_after_authorization(tmp_path, monkeypatch):
    workspace, output, _, _, order = setup_control(tmp_path, monkeypatch)
    original = runtime.authorize_command
    def change_after_authorization(current, action):
        result = original(current, action)
        output.write_text('{"external":true}', encoding='utf8')
        return result
    monkeypatch.setattr(runtime, 'authorize_command', change_after_authorization)
    with pytest.raises((runtime.WorkOrderInputChangedError, ValueError)):
        runtime._prepare_delivery_action(workspace, 'validate')
    assert runtime.read_orchestration_work_order(workspace, order.work_order_id).completion_proof is None


def test_a_new_controlled_phase_does_not_reuse_authorization_status(tmp_path, monkeypatch):
    from knowledge_product_workflow.verification_context import write_text
    workspace, output, _, calls, order = setup_control(tmp_path, monkeypatch)
    def publish_status(current):
        calls.append(current.root)
        return runtime.WorkflowStatus(stage='publish_pending', recommended_skill='kp-publisher',
            next_command='kp publish', artifacts={'runs/control-proof.json':'present'})
    monkeypatch.setattr(runtime, 'inspect_workflow', publish_status)
    order = runtime.read_orchestration_work_order(workspace,
        runtime.create_orchestration_work_order(workspace, refresh_reason='合成控制夹具切换publish阶段，非内容批准'))
    calls.clear()
    def write_same_bytes_in_readiness(current, action):
        write_text(output, output.read_text(encoding='utf8'), encoding='utf8')
    monkeypatch.setattr(runtime, 'require_delivery_orchestration_ready', write_same_bytes_in_readiness)
    assert runtime._prepare_delivery_action(workspace, 'publish') == order.work_order_id
    assert len(calls) == 2


def test_authorization_own_read_locks_do_not_invalidate_its_completed_status(tmp_path, monkeypatch):
    from knowledge_product_workflow.production_intake import consistent_production_read
    workspace, _, _, calls, order = setup_control(tmp_path, monkeypatch)
    original = runtime.authorize_command
    def authorization_with_consistency_lock(current, action):
        with consistent_production_read(current):
            return original(current, action)
    monkeypatch.setattr(runtime, 'authorize_command', authorization_with_consistency_lock)
    assert runtime._prepare_delivery_action(workspace, 'validate') == order.work_order_id
    assert len(calls) == 1


def test_no_matching_order_recomputes_after_creating_control_state(tmp_path, monkeypatch):
    workspace, _, _, calls, order = setup_control(tmp_path, monkeypatch)
    runtime._atomic_json_write(runtime._work_order_path(workspace, order.work_order_id),
        order.model_copy(update={'lifecycle':runtime.WorkOrderLifecycle.CANCELLED}))
    calls.clear()
    prepared = runtime._prepare_delivery_action(workspace, 'validate')
    # The original controller may reuse the same content-derived identifier.
    assert runtime._is_live(runtime.read_orchestration_work_order(workspace, prepared))
    assert len(calls) >= 2  # Creating a handoff invalidates the captured state.


def test_preparation_cannot_authorize_non_delivery_actions(tmp_path, monkeypatch):
    workspace, _, _, calls, _ = setup_control(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        runtime._prepare_delivery_action(workspace, 'submit-chapter')
    assert not calls
