"""Offline transport contracts; simulated native frames never grant production qualification."""
import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from knowledge_product_workflow import production_tasks as tasks
from knowledge_product_workflow.production_controller import (
    NativeProductionSession,
    receive_native_results,
)
from knowledge_product_workflow.production_delegated_host import DelegatedInlineBridge
from knowledge_product_workflow.production_host import NativeAppServerClient, NativeProtocolError
from knowledge_product_workflow.workspace import Workspace
from tests import blueprint_support
from tests.test_structure_review_v133 import _fixture


class ScriptedClient:
    """No subprocess or model. native_origin is a protocol fixture, not real host evidence."""
    native_origin = True
    _blocked = False
    model_turn_limit = 32
    model_turns = 0
    preflight_ephemeral_metadata = NativeAppServerClient.preflight_ephemeral_metadata

    def __init__(self, root):
        self.cwd = root
        self.events = deque()
        self.calls = []
        self.request_receipts = []
        self.process = SimpleNamespace(poll=lambda: None)
        self.envelopes = {}
        self.turns = {}

    def request(self, method, params, **_):
        self.calls.append((method, params))
        if method == 'config/read':
            result = {'config': {'agents': {'enabled': False}}}
        elif method == 'thread/start':
            identity = 'thread-' + str(len(self.calls))
            result = {'thread': {'id': identity, 'ephemeral': True, 'environments': []},
                'sandbox': {'type': 'readOnly', 'networkAccess': False}, 'runtimeWorkspaceRoots': []}
        elif method == 'turn/start':
            self.model_turns += 1
            identity = 'turn-' + str(self.model_turns)
            result = {'turn': {'id': identity, 'rootTurnId': params.get('rootTurnId', identity)}}
            self.turns[params['threadId']] = result['turn']
            if 'parentTurnId' in params:
                self.envelopes[params['threadId']] = json.loads(params['input'][0]['text'].split('\n', 1)[1])
        elif method == 'thread/read':
            result = {'thread': {'id': params['threadId'], 'ephemeral': True,
                'environments': [], 'turns': [], 'status': {'type': 'idle'}}}
        else:
            raise AssertionError(method)
        self.request_receipts.append({'method': method, 'params': params, 'result': result})
        return result

    def next_event(self, **_):
        return self.events.popleft() if self.events else None

    def finish(self, thread, payload):
        turn = self.turns[thread]
        self.events.extend([
            {'method': 'turn/started', 'emittedAtMs': 10, 'params': {'threadId': thread, 'turn': turn}},
            {'method': 'item/completed', 'params': {'threadId': thread, 'turnId': turn['id'],
                'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': json.dumps(payload)}}},
            {'method': 'turn/completed', 'emittedAtMs': 20, 'params': {'threadId': thread,
                'turn': {**turn, 'status': 'completed'}}},
        ])


def prepared(tmp_path):
    original = blueprint_support.submit_curriculum_blueprint
    def explicit_dependencies(workspace, path):
        payload = yaml.safe_load(path.read_text(encoding='utf8'))
        payload.update(production_policy_revision=1, shared_context={})
        for chapter in payload['chapters']:
            chapter.update(production_dependencies=[], production_exports=[])
        path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding='utf8')
        return original(workspace, path)
    with patch.object(blueprint_support, 'submit_curriculum_blueprint', explicit_dependencies):
        workspace, _, body = _fixture(tmp_path)
    body = body.replace('\n\n## ', '\n\n::: {.kp-chapter-lead}\n本章从输入、使用条件与可检查产出展开。\n:::\n\n## ', 1)
    run = tasks.ProductionRun.diagnostic_run(1, tasks.ProductionPolicy(requested_parallelism=4))
    run.synthetic_local_checks = False
    tasks.save_run(workspace, run)
    client = ScriptedClient(workspace.root)
    bridge = DelegatedInlineBridge(client, workspace=workspace, model='offline', max_children=4)
    bridge.create_parent()
    bridge.start_control(run.run_id)
    session = NativeProductionSession(workspace, bridge)
    session.run_id = run.run_id
    return workspace, run, body, client, bridge, session


def test_writer_terminal_private_intake_and_real_freeze_keep_formal_gate_closed(tmp_path):
    workspace, run, body, client, bridge, session = prepared(tmp_path)
    dispatched = session.dispatch()
    task_id = dispatched['task_ids'][0]
    current = tasks.resume_production_run(workspace, run.run_id)
    task = current.task(task_id)
    assert task.delegated_claim.phase == 'running'
    assert task.host_parent_id is None
    thread, envelope = next(iter(client.envelopes.items()))
    writer_prompt = next(params['input'][0]['text'] for method, params in client.calls
        if method == 'turn/start' and params['threadId'] == thread)
    assert 'result.body' in writer_prompt
    client.finish(thread, {k: envelope[k] for k in ('task_id', 'role', 'candidate_version', 'input_manifest_sha256')}
        | {'result': {'body': body}})
    _collect_ready(bridge)
    assert receive_native_results(workspace, run.run_id, bridge) == [task_id]
    assert receive_native_results(workspace, run.run_id, bridge) == []
    loaded = tasks.resume_production_run(workspace, run.run_id)
    assert loaded.active_count == 1  # JSON cannot restore the bridge's release authority.
    bridge.bind_live_releases(loaded)
    assert loaded.active_count == 0
    assert loaded.task(task_id).state == 'local_check'
    assert not loaded.capabilities.admits_live_parallelism()
    outcome = session.apply({'op': 'freeze', 'task_id': task_id})
    assert outcome['proof']['required_missing']
    loaded = tasks.resume_production_run(workspace, run.run_id)
    assert any(t.role == 'semantic' and t.state == 'review_ready' for t in loaded.tasks)
    assert not any(t.role == 'reviewer' for t in loaded.tasks)
    assert not (workspace.root / 'chapters/chapter-01.md').exists()
    assert Path(loaded.task(task_id).candidate_context_path).is_file()


def finished_writer(tmp_path):
    workspace, run, body, client, bridge, session = prepared(tmp_path)
    task_id = session.dispatch()['task_ids'][0]
    thread, envelope = next(iter(client.envelopes.items()))
    payload = {k: envelope[k] for k in ('task_id', 'role', 'candidate_version', 'input_manifest_sha256')}
    client.finish(thread, payload | {'result': {'body': body}})
    _collect_ready(bridge)
    return workspace, run, client, bridge, session, task_id, thread


@pytest.mark.parametrize('mutation', ['wrong_turn', 'missing_turn', 'wrong_role', 'old_version'])
def test_invalid_native_result_blocks_and_never_reissues(tmp_path, mutation):
    workspace, run, body, client, bridge, session = prepared(tmp_path)
    session.dispatch()
    thread, envelope = next(iter(client.envelopes.items()))
    payload = {k: envelope[k] for k in ('task_id', 'role', 'candidate_version', 'input_manifest_sha256')}
    if mutation == 'wrong_role':
        payload['role'] = 'reviewer'
    if mutation == 'old_version':
        payload['candidate_version'] = 2
    client.finish(thread, payload | {'result': {'body': body}})
    if mutation in {'wrong_turn', 'missing_turn'}:
        client.events[1]['params']['turnId'] = 'foreign' if mutation == 'wrong_turn' else None
    with pytest.raises(ValueError):
        _collect_ready(bridge)
    attempts = client.model_turns
    with pytest.raises(ValueError):
        session.dispatch()
    current = tasks.resume_production_run(workspace, run.run_id)
    assert current.active_count == 1 and current.tasks[0].delegated_claim.phase == 'unknown'
    assert current.tasks[0].result is None and client.model_turns == attempts


@pytest.mark.parametrize('during_idle_read', [False, True])
def test_new_turn_revokes_loaded_run_and_cannot_accept_old_payload(tmp_path, during_idle_read):
    if during_idle_read:
        workspace, run, body, client, bridge, session = prepared(tmp_path)
        session.dispatch()
        thread, envelope = next(iter(client.envelopes.items()))
        client.finish(thread, {k: envelope[k] for k in ('task_id', 'role', 'candidate_version', 'input_manifest_sha256')}
            | {'result': {'body': body}})
    else:
        workspace, run, client, bridge, session, _task_id, thread = finished_writer(tmp_path)
        receive_native_results(workspace, run.run_id, bridge)
    loaded = tasks.resume_production_run(workspace, run.run_id)
    bridge.bind_live_releases(loaded)
    def late_turn():
        client.events.append({'method': 'turn/started', 'params': {'threadId': thread,
            'turn': {'id': 'unexpected-turn', 'rootTurnId': bridge.control_root}}})
    if during_idle_read:
        original = client.request
        def raced(method, params, **kw):
            result = original(method, params, **kw)
            if method == 'thread/read':
                late_turn()
            return result
        client.request = raced
        with pytest.raises(ValueError):
            _collect_ready(bridge)
    else:
        assert loaded.active_count == 0
        late_turn()
        with pytest.raises(ValueError):
            bridge.check_boundary()
    assert loaded.active_count == 1
    invalidated = tasks.resume_production_run(workspace, run.run_id)
    assert invalidated.task(_task_id if not during_idle_read else next(iter(client.envelopes.values()))['task_id']).delegated_claim.phase == 'unknown'
    with pytest.raises(ValueError):
        receive_native_results(workspace, run.run_id, bridge)


def test_receipt_copy_or_new_bridge_cannot_grant_live_capacity(tmp_path):
    workspace, run, client, bridge, _, task_id, _ = finished_writer(tmp_path)
    receipt = bridge.verified_receipts()[0]
    copied = json.loads(json.dumps(receipt))
    with pytest.raises(ValueError):
        tasks.record_delegated_terminal(workspace, run.run_id, task_id, bridge=bridge, receipt=copied)
    tasks.record_delegated_terminal(workspace, run.run_id, task_id, bridge=bridge, receipt=receipt)
    loaded = tasks.resume_production_run(workspace, run.run_id)
    assert loaded.active_count == 1
    bridge.bind_live_releases(loaded)
    assert loaded.active_count == 0
    other = DelegatedInlineBridge(client, workspace=workspace, model='offline', max_children=4)
    with pytest.raises(ValueError):
        other.bind_live_releases(loaded)
    assert tasks.ProductionRun.model_validate_json(loaded.model_dump_json()).active_count == 1


def test_durable_result_replays_locally_after_eof_without_restoring_capacity(tmp_path):
    workspace, run, client, bridge, _, task_id, _ = finished_writer(tmp_path)
    receipt = bridge.verified_receipts()[0]
    tasks.record_delegated_terminal(workspace, run.run_id, task_id, bridge=bridge, receipt=receipt)
    bridge.interrupt()
    calls = len(client.calls)
    recovered = DelegatedInlineBridge(client, workspace=workspace, model='offline', max_children=4)
    assert recovered.replay_results(run.run_id) == [task_id]
    assert recovered.replay_results(run.run_id) == []
    loaded = tasks.resume_production_run(workspace, run.run_id)
    assert loaded.active_count == 1 and loaded.task(task_id).state == 'local_check'
    assert len([t for t in loaded.tasks if t.role == 'semantic']) == 1
    assert len(client.calls) == calls


@pytest.mark.parametrize('forged_metadata', [False, True])
def test_real_semantic_result_is_required_before_reviewer_is_created(tmp_path, forged_metadata):
    from tests.test_structure_review_v133 import fixture_teaching_checks
    workspace, run, client, bridge, session, task_id, _ = finished_writer(tmp_path)
    receive_native_results(workspace, run.run_id, bridge)
    session.apply({'op': 'freeze', 'task_id': task_id})
    semantic_id = session.dispatch()['task_ids'][0]
    thread, envelope = next((k, v) for k, v in client.envelopes.items() if v['task_id'] == semantic_id)
    from knowledge_product_workflow.developmental import StructureReviewInput
    assert envelope['structure_review_schema'] == StructureReviewInput.model_json_schema()
    semantic_prompt = next(params['input'][0]['text'] for method, params in client.calls
        if method == 'turn/start' and params['threadId'] == thread)
    assert 'result.structure_review' in semantic_prompt
    template = envelope['structure_template']
    observations = [{'unit_id': o['unit_id'], 'assessment': 'passed', 'teaching_checks': fixture_teaching_checks(),
        'assertion_checks': [{'assertion_id': a['assertion_id'], 'quote': a['quote'], 'assessment': 'supported',
            'support_refs': ['content_plan'], 'missing_premises': [], 'note': '离线夹具验证绑定，不代表真实模型检查。'}
            for a in template.get('assertion_prompts', []) if a['unit_id'] == o['unit_id']],
        'note': '离线夹具核验输入、方案条件、选择依据与可检查的输出对应，保持当前因果解释。'}
        for o in template['observations']]
    client.finish(thread, {k: envelope[k] for k in ('task_id', 'role', 'candidate_version', 'input_manifest_sha256')}
        | {'result': {'structure_review': {'template_id': template['template_id'], 'observations': observations}}})
    _collect_ready(bridge)
    assert receive_native_results(workspace, run.run_id, bridge) == [semantic_id]
    loaded = tasks.resume_production_run(workspace, run.run_id)
    reviewers = [t for t in loaded.tasks if t.role == 'reviewer']
    assert len(reviewers) == 1 and reviewers[0].candidate_context_path
    assert session.dispatch()['task_ids'] == [reviewers[0].task_id]
    reviewer_thread = next(k for k, v in client.envelopes.items() if v['task_id'] == reviewers[0].task_id)
    reviewer_prompt = next(params['input'][0]['text'] for method, params in client.calls
        if method == 'turn/start' and params['threadId'] == reviewer_thread)
    assert 'result.review' in reviewer_prompt
    review_envelope = client.envelopes[reviewer_thread]
    owned_fields = {'model', 'model_version', 'prompt_hash', 'reviewer_identity', 'reviewer_session_id'}
    assert not owned_fields.intersection(review_envelope['review_schema']['properties'])
    assert '控制端' in reviewer_prompt
    native = NativeProductionSession(workspace, SimpleNamespace())
    native.run_id = run.run_id
    assert not owned_fields.intersection(native._envelope(reviewers[0])['review_schema']['required'])
    assert not loaded.capabilities.admits_live_parallelism()
    for operation in ('commit', 'recover'):
        with pytest.raises(NativeProtocolError):
            session.apply({'op': operation})
    review = {'status': 'passed', 'scores': {name: 4 for name in (
        'factual_grounding', 'teaching_progression', 'interview_actionability',
        'case_specificity', 'expression_novelty')}, 'unit_evidence': []}
    if forged_metadata:
        review.update(dict.fromkeys(owned_fields, 'invented-by-worker'))
    client.finish(reviewer_thread, {k: review_envelope[k] for k in (
        'task_id', 'role', 'candidate_version', 'input_manifest_sha256')}
        | {'result': {'review': review}})
    _collect_ready(bridge)
    receipt = bridge.verified_receipts()[0]
    recorded = receipt['result']['result']['review']
    import hashlib
    assert recorded['model'] == bridge.model
    assert recorded['model_version'] == 'host-not-reported'
    assert recorded['prompt_hash'] == hashlib.sha256(reviewer_prompt.encode('utf8')).hexdigest()
    assert recorded['reviewer_identity'] == recorded['reviewer_session_id'] == reviewer_thread
    assert receive_native_results(workspace, run.run_id, bridge) == [reviewers[0].task_id]
    assert tasks.resume_production_run(workspace, run.run_id).task(reviewers[0].task_id).state == 'revision_required'


@pytest.mark.parametrize('limit', [4, 5, 6])
def test_all_three_roles_share_the_original_pool_and_unknown_cannot_free_it(tmp_path, limit):
    workspace = Workspace.create(tmp_path, 'mixed-pool')
    run = tasks.ProductionRun.diagnostic_run(limit + 1, tasks.ProductionPolicy(requested_parallelism=limit))
    run.synthetic_local_checks = False
    for index, task in enumerate(run.tasks):
        task.role = ('writer', 'semantic', 'reviewer')[index % 3]
        task.state = 'edit_ready' if task.role == 'writer' else 'review_ready'
    tasks.save_run(workspace, run)
    ownership = {'owner_id': 'offline', 'connection_id': 'connection', 'control_thread_id': 'control',
        'control_turn_id': 'control-turn', 'root_turn_id': 'root'}
    chosen = tasks.reserve_delegated_tasks(workspace, run.run_id, **ownership)
    assert len(chosen) == limit and {t.role for t in chosen} == {'writer', 'semantic', 'reviewer'}
    tasks.recover_delegated_unknown(workspace, run.run_id)
    loaded = tasks.resume_production_run(workspace, run.run_id)
    for task in loaded.tasks:
        task.release_confirmed = True
    assert loaded.active_count == limit and tasks.next_dispatches(loaded) == []


@pytest.mark.parametrize('phase', ['after_terminal_receipt_write', 'after_terminal_save'])
def test_terminal_durable_fault_keeps_slot_even_with_documentary_terminal(tmp_path, monkeypatch, phase):
    workspace, run, client, bridge, _, task_id, _ = finished_writer(tmp_path)
    receipt = bridge.verified_receipts()[0]
    def crash(current):
        if current == phase:
            raise RuntimeError('terminal durable fault')
    monkeypatch.setattr(tasks, '_delegated_fault', crash)
    with pytest.raises(RuntimeError):
        tasks.record_delegated_terminal(workspace, run.run_id, task_id, bridge=bridge, receipt=receipt)
    loaded = tasks.resume_production_run(workspace, run.run_id)
    bridge.bind_live_releases(loaded)
    assert loaded.active_count == 1 and client.model_turns == 2
    assert not (workspace.root / 'runs/production/control.lock').exists()


def test_control_ack_failure_is_persisted_unknown_and_never_retried(tmp_path):
    workspace = Workspace.create(tmp_path, 'control-ack')
    run = tasks.ProductionRun.diagnostic_run(1, tasks.ProductionPolicy())
    tasks.save_run(workspace, run)
    client = ScriptedClient(workspace.root)
    bridge = DelegatedInlineBridge(client, workspace=workspace, model='offline', max_children=4)
    bridge.create_parent()
    original = client.request
    def unknown(method, params, **kw):
        result = original(method, params, **kw)
        if method == 'turn/start':
            raise NativeProtocolError('ACK lost after request')
        return result
    client.request = unknown
    with pytest.raises(ValueError):
        bridge.start_control(run.run_id)
    with pytest.raises(ValueError):
        bridge.start_control(run.run_id)
    restored = tasks.resume_production_run(workspace, run.run_id)
    assert restored.delegated_control.phase == 'unknown' and client.model_turns == 1


def test_eof_and_observation_timeout_never_replace_or_release_worker(tmp_path):
    workspace, run, _, client, bridge, session = prepared(tmp_path)
    session.dispatch()
    assert bridge.collect(timeout=.001)['status'] == 'pending'
    calls = len(client.calls)
    def eof(**_):
        raise NativeProtocolError('fixture EOF')
    client.next_event = eof
    with pytest.raises(ValueError):
        bridge.collect(timeout=.001)
    assert len(client.calls) == calls
    loaded = tasks.resume_production_run(workspace, run.run_id)
    assert loaded.active_count == 1 and loaded.tasks[0].delegated_claim.phase == 'unknown'


def test_replay_refuses_changed_receipt_and_does_not_make_rpc(tmp_path):
    workspace, run, client, bridge, _, task_id, _ = finished_writer(tmp_path)
    receipt = bridge.verified_receipts()[0]
    tasks.record_delegated_terminal(workspace, run.run_id, task_id, bridge=bridge, receipt=receipt)
    loaded = tasks.resume_production_run(workspace, run.run_id)
    path = workspace.root / loaded.task(task_id).delegated_claim.receipt_relative
    path.write_text(path.read_text(encoding='utf8').replace('writer', 'foreign'), encoding='utf8')
    calls = len(client.calls)
    with pytest.raises(ValueError):
        bridge.replay_results(run.run_id)
    assert len(client.calls) == calls and loaded.active_count == 1


class VirtualClock:
    value = 0.0
    def __call__(self):
        return self.value


def _collect_ready(bridge):
    # Protocol fixtures use logical time; filesystem/CPU jitter is not a host event.
    clock = VirtualClock()
    original = bridge.client.next_event

    def next_event(*, timeout):
        event = original(timeout=timeout)
        if event is None:
            clock.value += timeout
        return event

    with patch.object(bridge.client, 'next_event', next_event):
        return bridge.collect(timeout=.01, clock=clock)


def test_scripted_receipts_survive_local_delay_without_extending_logical_window(tmp_path, monkeypatch):
    import time

    _, _, body, client, bridge, session = prepared(tmp_path)
    task_id = session.dispatch()['task_ids'][0]
    thread, envelope = next(iter(client.envelopes.items()))
    client.finish(thread, {k: envelope[k] for k in (
        'task_id', 'role', 'candidate_version', 'input_manifest_sha256')}
        | {'result': {'body': body}})
    original = bridge.check_boundary

    def delayed(**kwargs):
        time.sleep(.02)
        return original(**kwargs)

    monkeypatch.setattr(bridge, 'check_boundary', delayed)
    assert _collect_ready(bridge)['results'] == [task_id]
    assert len(bridge.verified_receipts()) == 1


@pytest.mark.parametrize('delay', [0, 2.1])
def test_collect_waits_locally_for_delayed_events_but_returns_ready_results_promptly(tmp_path, delay):
    _, _, body, client, bridge, session = prepared(tmp_path)
    session.dispatch()
    clock = VirtualClock()
    thread, envelope = next(iter(client.envelopes.items()))
    finished = []
    def delayed(*, timeout):
        if not finished and clock.value >= delay:
            client.finish(thread, {k: envelope[k] for k in ('task_id', 'role', 'candidate_version', 'input_manifest_sha256')}
                | {'result': {'body': body}})
            finished.append(True)
        if client.events:
            return client.events.popleft()
        clock.value += timeout
        return None
    client.next_event = delayed
    result = bridge.collect(timeout=30, clock=clock)
    assert result['results'] and delay <= clock.value < delay + 1.1


def test_idle_rpc_uses_remaining_collect_budget(tmp_path):
    _, _, body, client, bridge, session = prepared(tmp_path)
    session.dispatch()
    thread, envelope = next(iter(client.envelopes.items()))
    client.finish(thread, {k: envelope[k] for k in ('task_id', 'role', 'candidate_version', 'input_manifest_sha256')}
        | {'result': {'body': body}})
    clock = VirtualClock()
    limits = []
    original = client.request
    def next_event(*, timeout):
        if client.events:
            event = client.events.popleft()
            if event['method'] == 'turn/completed':
                clock.value = 29
            return event
        clock.value += timeout
        return None
    def checked(method, params, **kw):
        if method == 'thread/read':
            limits.append(kw['timeout'])
        return original(method, params, **kw)
    client.next_event, client.request = next_event, checked
    result = bridge.collect(timeout=30, clock=clock)
    assert result['results'] and len(limits) == 1 and 0 < limits[0] <= 1
    assert clock.value <= 30


def test_control_thread_preflights_real_metadata_rpc_before_login_or_model(tmp_path):
    workspace = Workspace.create(tmp_path, 'metadata-preflight')
    client = ScriptedClient(workspace.root)
    bridge = DelegatedInlineBridge(client, workspace=workspace, model='offline', max_children=4)
    bridge.create_parent()
    assert getattr(client, '_metadata_preflight_thread', None) == bridge.parent_id
    assert client.model_turns == 0
