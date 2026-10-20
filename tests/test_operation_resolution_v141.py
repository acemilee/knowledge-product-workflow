"""书稿身份与目录名分离，历史回执仍严格绑定工作区。"""
import json

import pytest

from knowledge_product_workflow import operation_receipts as operations
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def renamed_book(tmp_path):
    original = Workspace.create(tmp_path, 'book-id')
    create_product_brief(original, '身份测试书')
    destination = tmp_path / 'different-folder'
    assert destination.parent.resolve() == tmp_path.resolve()
    original.root.rename(destination)
    return Workspace(destination)


def test_new_operation_in_renamed_book_is_queryable(tmp_path):
    w = renamed_book(tmp_path)
    with operations.operation_scope(w, action='assemble', chapter_number=0, operation_id='op-renamed'):
        status = operations.operation_status(w, 'op-renamed')
        assert status['phase'] == 'running'
        assert status['book_id'] == 'book-id'


def test_receipt_with_forged_book_identity_is_rejected(tmp_path):
    w = renamed_book(tmp_path)
    with operations.operation_scope(w, action='assemble', chapter_number=0, operation_id='op-forged'):
        pass
    path = operations.receipt_path(w, 'op-forged')
    raw = json.loads(path.read_text(encoding='utf8'))
    raw['book_id'] = 'different-folder'
    path.write_text(json.dumps(raw), encoding='utf8')
    assert operations.operation_status(w, 'op-forged')['phase'] == 'recovery_required'


def failed(w):
    with pytest.raises(ValueError), operations.operation_scope(w,action='assemble',chapter_number=0,operation_id='op-failed'):
        raise ValueError('受控前置检查拒绝，没有进入事务')


def test_durable_precheck_failure_can_close_as_failure_and_is_idempotent(tmp_path):
    from knowledge_product_workflow.runtime import recovery_view
    w=Workspace.create(tmp_path,'failure')
    failed(w)
    assert not recovery_view(w)['can_resume']
    result=operations.close_failed_operation(w,'op-failed',reason='原检查已明确失败')
    assert result.phase=='closed_failed' and result.transaction_id is None
    before=(w.root/'runs/events.jsonl').read_bytes()
    assert operations.close_failed_operation(w,'op-failed',reason='重复查询')==result
    assert before==(w.root/'runs/events.jsonl').read_bytes()
    assert recovery_view(w)['can_resume']
    result=operations.wait_operation(w,'op-failed',timeout=1,
        sleep=lambda _:pytest.fail('closed failure must not wait'))
    assert result['phase']=='closed_failed'


def test_recovery_names_the_exact_existing_command_and_does_not_auto_close(tmp_path):
    from knowledge_product_workflow.runtime import recovery_view
    w=Workspace.create(tmp_path,'diagnosis');failed(w)
    before=operations.receipt_path(w,'op-failed').read_bytes()
    diagnostic=recovery_view(w)['operation_diagnostics'][0]
    assert diagnostic['next_action']=='close-failed-operation'
    assert diagnostic['next_argv'][1]=='close-failed-operation'
    assert '--reason' in diagnostic['next_argv']
    assert 'candidate_only' in diagnostic['closure_eligibility']
    assert operations.receipt_path(w,'op-failed').read_bytes()==before


@pytest.mark.parametrize('reason',['legacy','unlinked_journal','evidence_missing'])
def test_uncertain_or_unlinked_failures_cannot_be_closed(tmp_path,reason):
    w=Workspace.create(tmp_path,'failure');failed(w)
    path=operations.receipt_path(w,'op-failed')
    if reason=='legacy':
        raw=json.loads(path.read_text(encoding='utf8'));raw.pop('domain_stage');path.write_text(json.dumps(raw),encoding='utf8')
    elif reason=='evidence_missing':
        (w.root/'runs/events.jsonl').write_text('',encoding='utf8')
    else:
        directory=w.root/'runs/orchestration/transactions';directory.mkdir(parents=True,exist_ok=True)
        (directory/'tx-unlinked.json').write_text(json.dumps({'metadata':{'control_operation_id':'op-failed'}}),encoding='utf8')
    with pytest.raises(ValueError):
        operations.close_failed_operation(w,'op-failed',reason='不能猜测结果')
    assert operations.operation_status(w,'op-failed')['phase']=='failed_before_commit'
