"""Owner metadata lifecycle is independent of book bytes; content writes stay fresh."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_product_workflow import production_tasks as tasks
from knowledge_product_workflow import verification_context as verification
from knowledge_product_workflow.workspace import Workspace


class BusinessFailure(RuntimeError):
    pass


def fixture(tmp_path):
    workspace=Workspace.create(tmp_path,'control-lifecycle')
    (workspace.root/'runs/production').mkdir(parents=True,exist_ok=True)
    fact=workspace.root/'fact.txt'
    fact.write_bytes(b'a'*65536)
    return workspace,fact,workspace.root/'runs/production/control.lock'


@pytest.mark.parametrize('business_error',[False,True])
def test_input_change_cleans_own_lock_and_preserves_original_error(tmp_path,business_error):
    workspace,fact,lock=fixture(tmp_path)
    expected=BusinessFailure if business_error else verification.SnapshotChangedError
    with pytest.raises(expected), verification.verification_scope(workspace):
        verification.read_bytes(fact)
        with tasks.production_lock(workspace):
            fact.write_bytes(b'b'*65536)
            if business_error:raise BusinessFailure('original-business-error')
    assert not lock.exists()
    assert list(lock.parent.glob('.lock-owner-*.json'))==[]


def test_changed_business_input_does_not_prevent_mutex_allocation_but_still_fails_scope(tmp_path):
    workspace,fact,lock=fixture(tmp_path)
    entered=[]
    with pytest.raises(verification.SnapshotChangedError), verification.verification_scope(workspace):
        verification.read_bytes(fact);fact.write_bytes(b'b'*65536)
        with tasks.production_lock(workspace):entered.append(True)
    assert entered==[True] and not lock.exists()


@pytest.mark.parametrize('copy_nonce',[False,True])
def test_observed_foreign_owner_or_inode_is_never_deleted_or_still_owned(tmp_path,copy_nonce):
    workspace,_,lock=fixture(tmp_path)
    replacement=lock.parent/'foreign-control'
    with pytest.raises(ValueError), tasks.production_lock(workspace):
        raw=lock.read_bytes() if copy_nonce else json.dumps({'lock_id':'different-owner'}).encode()
        replacement.write_bytes(raw);os.replace(replacement,lock)
        tasks.controller_lock_owned(workspace)  # Must raise unknown, never return false/reacquire.
    assert lock.exists() and lock.read_bytes()==raw


def test_same_path_new_root_epoch_cannot_authorize_or_delete_foreign_root(tmp_path):
    workspace,fact,lock=fixture(tmp_path)
    moved=workspace.root.with_name(workspace.root.name+'-moved')
    assert moved.resolve().is_relative_to(tmp_path.resolve())
    ownership_rejected=[]
    with pytest.raises(ValueError), verification.verification_scope(workspace):
        verification.read_bytes(fact)
        with tasks.production_lock(workspace):
            owner=lock.read_bytes();prepared=next(lock.parent.glob('.lock-owner-*.json')).name
            workspace.root.rename(moved)
            lock.parent.mkdir(parents=True);fact.write_bytes(b'a'*65536)
            lock.write_bytes(owner);(lock.parent/prepared).write_bytes(owner)
            try:tasks.controller_lock_owned(workspace)
            except ValueError:ownership_rejected.append(True)
            verification.write_text(workspace.root/'foreign-canonical.txt','must not publish')
    assert ownership_rejected==[True]
    assert lock.exists() and (moved/'runs/production/control.lock').exists()
    assert not (workspace.root/'foreign-canonical.txt').exists()


def test_direct_nested_acquire_never_takes_over_a_new_root_epoch(tmp_path):
    workspace,_,lock=fixture(tmp_path)
    moved=workspace.root.with_name(workspace.root.name+'-moved')
    entered=[]
    with pytest.raises(ValueError), tasks.production_lock(workspace):
        workspace.root.rename(moved);lock.parent.mkdir(parents=True)
        try:
            with tasks.production_lock(workspace):entered.append(True)
        except ValueError:
            pass
    assert entered==[] and not lock.exists()
    assert list(lock.parent.glob('.lock-owner-*.json'))==[]


def test_cleanup_identity_failure_is_not_the_primary_business_exception(tmp_path):
    workspace,_,lock=fixture(tmp_path)
    with pytest.raises(BusinessFailure) as caught, tasks.production_lock(workspace):
        other=lock.parent/'other-owner'
        other.write_bytes(lock.read_bytes());os.replace(other,lock)
        raise BusinessFailure('original-business-error')
    assert lock.exists()
    assert any('保留未知' in note for note in caught.value.__notes__)


@pytest.mark.parametrize('relative',[
    'chapters/candidate.md','runs/production/run-a/run.json','runs/orchestration/events.jsonl',
    'runs/orchestration/operations/op-test.wait-owner.json',
])
def test_content_run_event_and_lease_writes_keep_full_fresh(tmp_path,relative):
    workspace,fact,lock=fixture(tmp_path)
    target=workspace.root/relative;target.parent.mkdir(parents=True,exist_ok=True)
    with pytest.raises(verification.SnapshotChangedError), verification.verification_scope(workspace):
        verification.read_bytes(fact)
        with tasks.production_lock(workspace):
            fact.write_bytes(b'b'*65536)
            verification.write_text(target,'must fail')
    assert not target.exists() and not lock.exists()


def test_owner_prepared_allocation_is_exclusive_and_never_overwrites_foreign_file(tmp_path,monkeypatch):
    workspace,_,lock=fixture(tmp_path)
    monkeypatch.setattr(tasks,'uuid4',lambda:SimpleNamespace(hex='a'*32))
    prepared=lock.parent/('.lock-owner-'+'a'*32+'.json')
    prepared.write_text('foreign owner',encoding='utf8')
    with pytest.raises(FileExistsError), tasks.production_lock(workspace):pass
    assert prepared.read_text(encoding='utf8')=='foreign owner' and not lock.exists()


def test_three_empty_locks_do_not_rescan_business_read_set(tmp_path,monkeypatch):
    workspace,fact,lock=fixture(tmp_path)
    original=verification.VerificationContext.verify_read_set
    checks=[]
    def counted(context):
        checks.append(1);return original(context)
    monkeypatch.setattr(verification.VerificationContext,'verify_read_set',counted)
    with verification.verification_scope(workspace):
        verification.read_bytes(fact)
        for _ in range(3):
            with tasks.production_lock(workspace):assert tasks.controller_lock_owned(workspace)
    assert len(checks)==1  # Normal scope completion remains full fresh.
    assert not lock.exists()


def test_half_lock_and_live_process_recovery_remain_unknown(tmp_path):
    workspace,_,lock=fixture(tmp_path)
    with tasks.production_lock(workspace),pytest.raises(ValueError):
        tasks.recover_production_lock(workspace)
    lock.write_text('{',encoding='utf8')
    with pytest.raises(ValueError):tasks.recover_production_lock(workspace)
    assert lock.read_text(encoding='utf8')=='{'


def test_initially_missing_root_allows_readonly_empty_scope_and_status(tmp_path):
    from knowledge_product_workflow.runtime import inspect_workflow

    workspace=Workspace(tmp_path/'initially-missing')
    with verification.verification_scope(workspace):
        assert not verification.exists(workspace.root)
    assert inspect_workflow(workspace).stage=='brief_missing'
    assert not workspace.root.exists()


def test_initially_missing_root_cannot_adopt_external_creation(tmp_path):
    workspace=Workspace(tmp_path/'initially-missing')
    with pytest.raises(verification.SnapshotChangedError), verification.verification_scope(workspace):
        workspace.root.mkdir()
        verification.write_text(workspace.root/'foreign.txt','must fail')
    assert not (workspace.root/'foreign.txt').exists()


@pytest.mark.parametrize('nested',[False,True])
def test_owned_root_creation_binds_identity_and_still_rejects_replacement(tmp_path,nested):
    workspace=Workspace(tmp_path/'initially-missing')
    moved=tmp_path/'original-root'
    with pytest.raises(verification.SnapshotChangedError), verification.verification_scope(workspace):
        target=workspace.root/'child' if nested else workspace.root
        verification.mkdir(target,parents=True,exist_ok=True)
        verification.write_text(workspace.root/'owned.txt','valid owned write')
        workspace.root.rename(moved)
        workspace.root.mkdir()
        verification.write_text(workspace.root/'foreign.txt','must fail')
    assert (moved/'owned.txt').read_text()=='valid owned write'
    assert not (workspace.root/'foreign.txt').exists()


def test_missing_root_nested_mkdir_without_parents_does_not_create_or_bind(tmp_path):
    workspace=Workspace(tmp_path/'initially-missing')
    with verification.verification_scope(workspace), pytest.raises(FileNotFoundError):
        verification.mkdir(workspace.root/'child')
    assert not workspace.root.exists()


def test_missing_root_owned_mkdir_never_adopts_a_competing_creation(tmp_path,monkeypatch):
    workspace=Workspace(tmp_path/'initially-missing')
    original=Path.mkdir
    collided=[]
    def competing(path,*args,**kwargs):
        if path==workspace.root and not collided:
            collided.append(True)
            original(path)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'mkdir',competing)
    with (pytest.raises(verification.SnapshotChangedError),
          verification.verification_scope(workspace), pytest.raises(FileExistsError)):
        verification.mkdir(workspace.root,0o777,True,True)
    assert collided==[True] and list(workspace.root.iterdir())==[]
