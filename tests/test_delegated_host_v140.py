"""D10-1 persistent claims and receipt seam; no delegated transport or qualification."""
import json
from types import SimpleNamespace

import pytest

from knowledge_product_workflow import production_tasks as tasks
from knowledge_product_workflow.production_controller import (
    NativeInlineBridge,
    receive_native_results,
)
from knowledge_product_workflow.production_host import NativeTaskLedger
from knowledge_product_workflow.workspace import Workspace


def prepared(tmp_path):
    workspace=Workspace.create(tmp_path,'delegated-claims')
    run=tasks.ProductionRun.diagnostic_run(4,tasks.ProductionPolicy(requested_parallelism=4))
    run.synthetic_local_checks=False
    tasks.save_run(workspace,run)
    return workspace,run


def reserve(workspace,run_id,**extra):
    options={'owner_id':'owner-a','connection_id':'connection-a','control_thread_id':'control-thread',
        'control_turn_id':'control-turn','root_turn_id':'control-root'}
    options.update(extra)
    return tasks.reserve_delegated_tasks(workspace,run_id,**options)


def start(workspace,run_id,task):
    return tasks.mark_delegated_send_started(workspace,run_id,task.task_id,
        claim_id=task.delegated_claim.claim_id,owner_id='owner-a',connection_id='connection-a')


def ack(workspace,run_id,task,**changes):
    values={'claim_id':task.delegated_claim.claim_id,'owner_id':'owner-a','connection_id':'connection-a',
        'thread_id':'worker-thread','turn_id':'worker-turn','root_turn_id':'control-root'}
    values.update(changes)
    return tasks.record_delegated_ack(workspace,run_id,task.task_id,**values)


def test_claim_reserve_send_ack_share_original_atomic_run_and_real_lock(tmp_path,monkeypatch):
    workspace,run=prepared(tmp_path)
    original=tasks.save_run
    writes=[]
    def observed(w,r):
        writes.append(tasks.controller_lock_owned(w))
        return original(w,r)
    monkeypatch.setattr(tasks,'save_run',observed)
    selected=reserve(workspace,run.run_id)
    assert len(selected)==4
    assert all(t.delegated_claim.phase=='reserved' for t in selected)
    assert tasks.resume_production_run(workspace,run.run_id).active_count==4
    start(workspace,run.run_id,selected[0])
    ack(workspace,run.run_id,selected[0])
    loaded=tasks.resume_production_run(workspace,run.run_id)
    claim=loaded.task(selected[0].task_id).delegated_claim
    assert claim.phase=='running' and claim.turn_id=='worker-turn'
    assert all(writes) and len(writes)==3
    assert loaded.task(selected[0].task_id).host_parent_id is None
    assert not loaded.capabilities.admits_live_parallelism()


@pytest.mark.parametrize('phase',['after_reserve_save','after_send_started_save','after_ack_save'])
def test_fault_boundaries_keep_claim_and_unknown_never_resends(tmp_path,monkeypatch,phase):
    workspace,run=prepared(tmp_path)
    def crash(current):
        if current==phase:raise RuntimeError('controlled crash')
    monkeypatch.setattr(tasks,'_delegated_fault',crash,raising=False)
    if phase=='after_reserve_save':
        with pytest.raises(RuntimeError):reserve(workspace,run.run_id)
        task=tasks.resume_production_run(workspace,run.run_id).tasks[0]
    else:
        task=reserve(workspace,run.run_id)[0]
        if phase=='after_send_started_save':
            with pytest.raises(RuntimeError):start(workspace,run.run_id,task)
        else:
            start(workspace,run.run_id,task)
            with pytest.raises(RuntimeError):ack(workspace,run.run_id,task)
    monkeypatch.setattr(tasks,'_delegated_fault',lambda _:None)
    report=tasks.recover_delegated_unknown(workspace,run.run_id)
    restored=tasks.resume_production_run(workspace,run.run_id)
    current=restored.task(task.task_id)
    if phase=='after_reserve_save':
        assert task.task_id in report['never_sent'] and current.delegated_claim.phase=='reserved'
    else:
        assert task.task_id in report['unknown'] and current.delegated_claim.phase=='unknown'
        with pytest.raises(ValueError):start(workspace,run.run_id,current)
    assert not current.release_confirmed and restored.active_count==4
    assert reserve(workspace,run.run_id)==[]


def test_stale_owner_connection_claim_or_root_cannot_advance(tmp_path):
    workspace,run=prepared(tmp_path)
    task=reserve(workspace,run.run_id)[0]
    with pytest.raises(ValueError):reserve(workspace,run.run_id,owner_id='other-owner')
    start(workspace,run.run_id,task)
    for wrong in [{'owner_id':'other'},{'connection_id':'other'},{'claim_id':'other'},{'root_turn_id':'other'}]:
        with pytest.raises(ValueError):ack(workspace,run.run_id,task,**wrong)
    assert tasks.resume_production_run(workspace,run.run_id).task(task.task_id).delegated_claim.phase=='send_started'
    ack(workspace,run.run_id,task)
    with pytest.raises(ValueError):ack(workspace,run.run_id,task,turn_id='another-turn')


@pytest.mark.parametrize('field,wrong',[
    ('host_task_id','foreign-thread'),('host_turn_id','foreign-turn'),('host_root_turn_id','foreign-root'),
    ('role','reviewer'),('candidate_version',2),('input_manifest_sha256','b'*64),
])
def test_result_binding_rejects_foreign_turn_role_version_and_manifest(tmp_path,field,wrong):
    workspace,run=prepared(tmp_path)
    task=reserve(workspace,run.run_id)[0];start(workspace,run.run_id,task);ack(workspace,run.run_id,task)
    loaded=tasks.resume_production_run(workspace,run.run_id).task(task.task_id)
    receipt={'task_id':loaded.task_id,'role':'writer','candidate_version':1,'input_manifest_sha256':loaded.input_manifest_sha256,
        'host_task_id':'worker-thread','host_turn_id':'worker-turn','host_root_turn_id':'control-root','result':{'body':'私有候选'}}
    tasks.validate_delegated_result(loaded,receipt)
    with pytest.raises(ValueError):tasks.validate_delegated_result(loaded,{**receipt,field:wrong})
    assert loaded.result is None and not loaded.release_confirmed


def test_json_claim_flags_cannot_grant_and_corrupt_claim_does_not_rebuild(tmp_path):
    workspace,run=prepared(tmp_path)
    task=reserve(workspace,run.run_id)[0]
    raw=tasks.resume_production_run(workspace,run.run_id).model_dump(mode='json')
    raw['capabilities'].update(host_kind='codex-app-server',production_verified=True,permissions_verified=True,
        parent_child_receipts_verified=True,release_verified=True,max_parallelism=6)
    restored=tasks.ProductionRun.model_validate(raw)
    assert not restored.capabilities.admits_live_parallelism()
    raw['tasks'][0]['delegated_claim']['phase']='invented-terminal'
    path=workspace.root/'runs/production'/run.run_id/'run.json'
    path.write_text(json.dumps(raw),encoding='utf8')
    with pytest.raises(ValueError):tasks.recover_delegated_unknown(workspace,run.run_id)
    assert json.loads(path.read_text())['tasks'][0]['delegated_claim']['phase']=='invented-terminal'
    assert task.dispatch_index is not None


def test_reservation_refuses_formal_run_and_real_lock_contention(tmp_path):
    workspace,run=prepared(tmp_path)
    with tasks.production_lock(workspace),pytest.raises(ValueError):reserve(workspace,run.run_id)
    run.diagnostic_only=False;tasks.save_run(workspace,run)
    with pytest.raises(ValueError):reserve(workspace,run.run_id)


def test_ack_atomic_publish_failure_leaves_original_send_started_claim(tmp_path,monkeypatch):
    workspace,run=prepared(tmp_path)
    task=reserve(workspace,run.run_id)[0];start(workspace,run.run_id,task)
    def failed_publish(*_):
        raise OSError('controlled atomic publish failure')
    monkeypatch.setattr(tasks,'replace_file',failed_publish)
    with pytest.raises(OSError):ack(workspace,run.run_id,task)
    restored=tasks.resume_production_run(workspace,run.run_id).task(task.task_id)
    assert restored.delegated_claim.phase=='send_started'
    assert restored.delegated_claim.thread_id is None and not restored.release_confirmed
    assert not (workspace.root/'runs/production/control.lock').exists()


def test_unknown_claim_cannot_free_slots_via_documentary_release_flag(tmp_path):
    workspace,run=prepared(tmp_path)
    task=reserve(workspace,run.run_id)[0];start(workspace,run.run_id,task)
    tasks.recover_delegated_unknown(workspace,run.run_id)
    loaded=tasks.resume_production_run(workspace,run.run_id)
    loaded.task(task.task_id).release_confirmed=True
    tasks.save_run(workspace,loaded)
    assert tasks.resume_production_run(workspace,run.run_id).active_count==4
    assert reserve(workspace,run.run_id)==[]


def test_ack_cannot_reuse_control_or_another_tasks_worker(tmp_path):
    workspace,run=prepared(tmp_path)
    first,second=reserve(workspace,run.run_id)[:2]
    start(workspace,run.run_id,first);start(workspace,run.run_id,second)
    with pytest.raises(ValueError):ack(workspace,run.run_id,first,thread_id='control-thread')
    ack(workspace,run.run_id,first)
    with pytest.raises(ValueError):ack(workspace,run.run_id,second)


@pytest.mark.parametrize('phase',['reserved','send_started','running','unknown'])
@pytest.mark.parametrize('clear_dispatch',[False,True])
def test_every_inflight_claim_keeps_slot_despite_legacy_task_flags(tmp_path,phase,clear_dispatch):
    workspace,run=prepared(tmp_path)
    task=reserve(workspace,run.run_id)[0]
    if phase!='reserved':start(workspace,run.run_id,task)
    if phase=='running':ack(workspace,run.run_id,task)
    if phase=='unknown':tasks.recover_delegated_unknown(workspace,run.run_id)
    loaded=tasks.resume_production_run(workspace,run.run_id)
    current=loaded.task(task.task_id)
    current.release_confirmed=True
    current.state='edit_ready'
    if clear_dispatch:current.dispatch_index=None
    assert loaded.active_count==4
    loaded.effective_parallelism=6  # Spare slots must not make an existing claim ready again.
    assert tasks.next_dispatches(loaded)==[]
    if clear_dispatch:
        with pytest.raises(ValueError):tasks.ProductionRun.model_validate(loaded.model_dump())


def test_ack_cannot_reuse_another_task_turn_or_control_turn(tmp_path):
    workspace,run=prepared(tmp_path)
    first,second=reserve(workspace,run.run_id)[:2]
    start(workspace,run.run_id,first);start(workspace,run.run_id,second)
    with pytest.raises(ValueError):ack(workspace,run.run_id,first,turn_id='control-turn')
    ack(workspace,run.run_id,first)
    with pytest.raises(ValueError):ack(workspace,run.run_id,second,thread_id='another-worker',turn_id='worker-turn')


def test_spawn_receipt_seam_keeps_result_and_duplicate_domain_application_once(tmp_path):
    workspace,run=prepared(tmp_path)
    selected=tasks.next_dispatches(run);tasks.save_run(workspace,run);task=selected[0]
    client=SimpleNamespace(native_origin=False)
    bridge=NativeInlineBridge(client,model='fixture',max_children=4);bridge.parent_id='parent'
    expected=task.model_dump(mode='json');expected['agent_name']='worker'
    state=NativeTaskLedger('parent',{task.task_id:expected});bridge.ledger=state
    state.consume({'method':'item/completed','params':{'threadId':'parent','item':{
        'type':'subAgentActivity','kind':'started','agentThreadId':'child','agentPath':'/root/worker'}}})
    state.consume({'method':'turn/started','params':{'threadId':'child','turn':{'id':'turn-child'}}})
    payload={'task_id':task.task_id,'candidate_version':1,'input_manifest_sha256':task.input_manifest_sha256,'result':{'body':'仅私有候选'}}
    state.consume({'method':'item/completed','params':{'threadId':'child','turnId':'turn-child','item':{
        'type':'agentMessage','phase':'final_answer','text':json.dumps(payload)}}})
    state.consume({'method':'turn/completed','params':{'threadId':'child','turn':{'id':'turn-child','status':'completed'}}})
    state.consume({'method':'item/completed','params':{'threadId':'parent','item':{'type':'collabAgentToolCall',
        'tool':'closeAgent','status':'completed','senderThreadId':'parent','receiverThreadIds':['child'],
        'agentsStates':{'child':{'status':'shutdown'}}}}})
    assert not bridge.has_active_work()
    receipts=bridge.verified_receipts()
    assert len(receipts)==1 and receipts[0]['execution_kind']=='native_spawn'
    assert receipts[0]['result']['host_turn_id']=='turn-child'
    assert receive_native_results(workspace,run.run_id,bridge)==[task.task_id]
    assert receive_native_results(workspace,run.run_id,bridge)==[]
    bridge._received_tasks.clear()  # Restarted receipt consumer, same immutable domain receipt.
    assert receive_native_results(workspace,run.run_id,bridge)==[]
    loaded=tasks.resume_production_run(workspace,run.run_id)
    assert loaded.task(task.task_id).state=='local_check'
    assert len([t for t in loaded.tasks if t.role=='semantic'])==1
    assert not (workspace.root/'chapters/chapter-01.md').exists()


def test_foreign_embedded_run_cannot_redirect_read_or_claim_write(tmp_path):
    workspace,first=prepared(tmp_path)
    second=tasks.ProductionRun.diagnostic_run(1,tasks.ProductionPolicy(requested_parallelism=4))
    tasks.save_run(workspace,second)
    original=workspace.root/'runs/production'/first.run_id/'run.json'
    requested=workspace.root/'runs/production'/second.run_id/'run.json'
    requested.write_bytes(original.read_bytes())
    before={path:path.read_bytes() for path in [original,requested]}
    with pytest.raises(ValueError,match='运行.*归属|运行.*ID'):
        tasks.resume_production_run(workspace,second.run_id)
    with pytest.raises(ValueError):reserve(workspace,second.run_id)
    assert all(path.read_bytes()==data for path,data in before.items())


@pytest.mark.parametrize('corruption',['foreign-task-run','duplicate-task-id'])
def test_corrupt_task_membership_is_rejected_without_any_run_write(tmp_path,corruption):
    workspace,run=prepared(tmp_path)
    path=workspace.root/'runs/production'/run.run_id/'run.json'
    raw=json.loads(path.read_text(encoding='utf8'))
    if corruption=='foreign-task-run':raw['tasks'][0]['run_id']='foreign-run'
    else:raw['tasks'][1]['task_id']=raw['tasks'][0]['task_id']
    path.write_text(json.dumps(raw),encoding='utf8')
    before=path.read_bytes()
    with pytest.raises(ValueError):tasks.resume_production_run(workspace,run.run_id)
    with pytest.raises(ValueError):reserve(workspace,run.run_id)
    assert path.read_bytes()==before


def test_save_rejects_in_memory_foreign_membership_before_files_change(tmp_path):
    workspace,run=prepared(tmp_path)
    path=workspace.root/'runs/production'/run.run_id/'run.json'
    before=path.read_bytes()
    run.tasks[0].run_id='foreign'
    with pytest.raises(ValueError):tasks.save_run(workspace,run)
    assert path.read_bytes()==before
