"""An index hint is not a claim that a worker read a comparison body or plan."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_product_workflow.production_context import freeze_candidate_context
from knowledge_product_workflow.production_controller import NativeProductionSession
from knowledge_product_workflow.workspace import Workspace


def _case(tmp_path, role='semantic'):
    workspace = Workspace.create(tmp_path, 'comparison')
    source = workspace.root / 'runs/body.md'
    source.write_text('# 1. 教程\n\n## 1.1 当前单元\n\n本单元给定输入A与B不同，按规则保存待核清单。', encoding='utf8')
    other = workspace.root / 'chapters/chapter-03.md'
    other.write_text('# 3. 额外材料\n\n## 3.1 正文比较\n\n只比较这段实际提供的文本。', encoding='utf8')
    report = workspace.root / 'runs/editorial/chapter-01-structure-review.json'
    report.parent.mkdir()
    report.write_text(json.dumps({'comparison_context': {'3.1': 'fixture'}}), encoding='utf8')
    context = freeze_candidate_context(workspace, chapter_number=1, candidate_version=1,
        source=source, input_paths=['chapters/chapter-03.md', 'runs/editorial/chapter-01-structure-review.json'],
        run_id='run-one', task_id='writer-one')
    index = [
        {'unit_id': '1.1', 'chapter_number': 1, 'plan_status': 'legacy_not_required', 'title': '当前单元',
            'source': 'chapters/chapter-01.md', 'unit_sha256': 'a'*64, 'comparison_sha256': 'b'*64},
        {'unit_id': '2.1', 'chapter_number': 2, 'plan_status': 'invalid_plan_deferred', 'title': '待修索引',
            'source': 'chapters/chapter-02.md', 'plan_error': '不应传作正文证据的内部错误与路径'*60,
            'unit_sha256': 'c'*64, 'comparison_sha256': 'd'*64},
        {'unit_id': '3.1', 'chapter_number': 3, 'plan_status': 'body_only_undeclared', 'title': '正文比较',
            'source': 'chapters/chapter-03.md', 'unit_sha256': 'e'*64, 'comparison_sha256': 'f'*64},
    ]
    template = {'template_id': 'fixture-only', 'book_reading_index': index, 'observations': [{'unit_id':'1.1'}]}
    Path(context.private_result_root).mkdir()
    path = Path(context.private_result_root, 'structure-template.json')
    path.write_text(json.dumps(template, ensure_ascii=False), encoding='utf8')
    task = SimpleNamespace(task_id=role+'-one', role=role, chapter_number=1, candidate_version=1,
        input_manifest_sha256=context.input_manifest_sha256, input_bindings={}, state='review_ready',
        candidate_context_path=str(Path(context.snapshot_root).parent / 'context.json'))
    session = NativeProductionSession(workspace, object()); session.run_id = 'run-one'
    return session, task, path, template


@pytest.mark.parametrize('role', ['semantic', 'reviewer'])
def test_worker_has_explicit_comparison_scope_without_granting_index_only_evidence(tmp_path, role):
    session, task, path, _ = _case(tmp_path, role)
    before = path.read_bytes()
    envelope = session._envelope(task)
    scope = envelope['comparison_scope']
    assert scope['index_is_comparison_evidence'] is False
    assert scope['exclude_current_observation_unit'] is True
    assert set(scope['eligible_compared_unit_ids']) == {'1.1', '3.1'}
    assert scope['unavailable_index_units']['2.1'] == 'invalid_plan_deferred'
    assert '3.1' in scope['body_only_comparison_unit_ids']
    assert scope['missing_evidence_action'] == ('uncertain_or_revise_not_passed'
        if role == 'semantic' else 'revise_not_passed')
    assert path.read_bytes() == before  # Only the inline view changes.


def test_inline_index_compacts_metadata_without_rewriting_template_or_output_schema(tmp_path):
    session, task, path, template = _case(tmp_path)
    before = path.read_bytes()
    envelope = session._envelope(task)
    projected = envelope['structure_template']
    assert projected['template_id'] == template['template_id']
    assert {x['unit_id'] for x in projected['book_reading_index']} == {'1.1','2.1','3.1'}
    assert all('unit_sha256' not in x and 'comparison_sha256' not in x and 'plan_error' not in x
        for x in projected['book_reading_index'])
    assert len(json.dumps(projected, ensure_ascii=False)) < len(json.dumps(template, ensure_ascii=False))
    assert path.read_bytes() == before
    from knowledge_product_workflow.developmental import StructureReviewInput
    assert envelope['structure_review_schema'] == StructureReviewInput.model_json_schema()


def test_current_plan_without_plan_payload_is_not_comparison_evidence(tmp_path):
    session, task, path, template = _case(tmp_path)
    template['book_reading_index'][2]['plan_status'] = 'current'
    path.write_text(json.dumps(template), encoding='utf8')
    scope = session._envelope(task)['comparison_scope']
    assert '3.1' not in scope['eligible_compared_unit_ids']
    assert scope['unavailable_index_units']['3.1'] == 'missing_plan_payload'


def test_both_transports_instruct_that_index_is_not_comparison_evidence():
    import inspect

    from knowledge_product_workflow.production_controller import NativeInlineBridge
    from knowledge_product_workflow.production_delegated_host import DelegatedInlineBridge
    assert 'comparison_scope' in inspect.getsource(NativeInlineBridge.dispatch)
    assert 'comparison_scope' in inspect.getsource(DelegatedInlineBridge.dispatch)


def test_duplicate_index_units_are_rejected_without_model_work(tmp_path):
    session, task, path, template = _case(tmp_path)
    template['book_reading_index'].append(dict(template['book_reading_index'][0]))
    path.write_text(json.dumps(template), encoding='utf8')
    with pytest.raises(ValueError, match='重复'):
        session._envelope(task)


def test_unknown_status_is_unavailable_and_index_source_does_not_trigger_file_read(tmp_path, monkeypatch):
    session, task, path, template = _case(tmp_path)
    template['book_reading_index'][1].update(plan_status='unknown_future', source='../../outside-secret.md')
    path.write_text(json.dumps(template), encoding='utf8')
    opened = []
    original = Path.open
    def observed(path, *args, **kwargs):
        opened.append(path.name)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', observed)
    scope = session._envelope(task)['comparison_scope']
    assert scope['unavailable_index_units']['2.1'] == 'unknown_plan_status'
    assert 'outside-secret.md' not in opened


def test_complete_current_comparison_plan_is_retained(tmp_path, monkeypatch):
    from knowledge_product_workflow.production_controller import _inline_comparison_scope
    session, task, _, template = _case(tmp_path)
    envelope = session._envelope(task)
    envelope['comparison_plans']['chapters/chapter-03-content-plan.yaml'] = 'supplied plan'
    template['book_reading_index'][2]['plan_status'] = 'current'
    scope = _inline_comparison_scope(envelope, template['book_reading_index'])
    assert '3.1' in scope['eligible_compared_unit_ids']
    assert '3.1' not in scope['body_only_comparison_unit_ids']


def test_reviewer_scope_does_not_instruct_out_of_schema_comparison_or_uncertain_status(tmp_path):
    session, task, _, _ = _case(tmp_path, 'reviewer')
    envelope = session._envelope(task)
    scope = envelope['comparison_scope']
    assert scope['missing_evidence_action'] == 'revise_not_passed'
    assert 'unit_evidence' in scope['rule']
    assert '未比较填[]' not in scope['rule']
    assert 'compared_unit_ids' not in envelope['review_schema']['properties']


def test_native_windows_payload_paths_keep_supplied_body_and_plan_eligible(tmp_path):
    from knowledge_product_workflow.production_controller import _inline_comparison_scope
    session, task, _, template = _case(tmp_path)
    envelope = session._envelope(task)
    body = envelope['comparison_units'].pop('3.1')
    envelope['inputs']['chapters\\chapter-03.md'] = body
    envelope['inputs']['chapters\\chapter-03-content-plan.yaml'] = 'supplied plan'
    template['book_reading_index'][2]['plan_status'] = 'current'
    scope = _inline_comparison_scope(envelope, template['book_reading_index'])
    assert '3.1' in scope['eligible_compared_unit_ids']
