"""前提内反例与适用域边界要在推理前明确；原拒绝器不能被引导取代。"""
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_product_workflow.developmental import (
    AssertionCheck,
    DevelopmentalEditError,
    create_structure_review_template,
    record_structure_review,
)
from knowledge_product_workflow.production_controller import NativeInlineBridge
from knowledge_product_workflow.production_delegated_host import DelegatedInlineBridge
from tests.test_premise_audit_o16 import audit_fixture, audit_payload


def test_generated_audit_protocol_defines_domain_boundary_and_supported_requirements(tmp_path):
    workspace, source = audit_fixture(tmp_path)
    template = create_structure_review_template(workspace, 1, source)
    protocol = template['assertion_audit_protocol']
    assert protocol['required'] is True
    assert '适用域内' in protocol['counterexample_rule']
    assert '全称' in protocol['counterexample_rule']
    assert 'scope_limit' in protocol['boundary_rule']
    assert 'uncertain' in protocol['counterexample_rule']
    assert protocol['supported_requirements'] == {
        'input_status': 'sufficient', 'inputs': '具体前提，不能复写结论',
        'support_refs': '当前支撑索引中的引用', 'missing_premises': [],
        'counterexample_status': 'none_found',
    }
    assert 'unsupported' in protocol['aggregation_rule']
    assert 'uncertain' in protocol['aggregation_rule']


def test_schema_explains_classification_without_adding_output_fields():
    schema = AssertionCheck.model_json_schema()
    for name in ('assessment', 'counterexample', 'scope_limit', 'counterexample_status'):
        assert schema['properties'][name].get('description')
    assert schema['properties']['counterexample_status']['anyOf'][0]['enum'] == ['none_found', 'found', 'uncertain']
    assert 'scope_boundary' not in schema['properties']
    assert schema['additionalProperties'] is False


@pytest.mark.parametrize('bridge', [NativeInlineBridge, DelegatedInlineBridge])
def test_both_transports_explicitly_direct_semantic_worker_to_protocol(bridge):
    assert 'assertion_audit_protocol' in inspect.getsource(bridge.dispatch)


def test_actual_frozen_semantic_envelope_carries_protocol_without_rewriting_template(tmp_path):
    from knowledge_product_workflow.production_context import (
        evaluate_candidate,
        freeze_candidate_context,
    )
    from knowledge_product_workflow.production_controller import NativeProductionSession
    workspace, source = audit_fixture(tmp_path)
    body = source.read_text(encoding='utf8').replace('\n\n## ',
        '\n\n::: {.kp-chapter-lead}\n本章从固定输入出发，核对判断条件与产出。\n:::\n\n## ', 1)
    source.write_text(body, encoding='utf8')
    inputs = [p.relative_to(workspace.root).as_posix() for p in workspace.root.rglob('*') if p.is_file()]
    context = freeze_candidate_context(workspace, chapter_number=1, candidate_version=1, source=source,
        input_paths=inputs, run_id='audit-one', task_id='writer-one')
    proof = evaluate_candidate(context)
    path = Path(proof.reports['structure_template'])
    before = path.read_bytes()
    task = SimpleNamespace(task_id='semantic-one', role='semantic', chapter_number=1, candidate_version=1,
        input_manifest_sha256=context.input_manifest_sha256, input_bindings={}, state='review_ready',
        candidate_context_path=str(Path(context.snapshot_root).parent / 'context.json'))
    session = NativeProductionSession(workspace, object()); session.run_id = 'audit-one'
    envelope = session._envelope(task)
    original = json.loads(before)
    assert envelope['structure_template']['assertion_audit_protocol'] == original['assertion_audit_protocol']
    assert 'counterexample_rule' in envelope['structure_template']['assertion_audit_protocol']
    assert envelope['structure_template']['template_id'] == original['template_id']
    assert path.read_bytes() == before


def test_boundary_note_does_not_relax_supported_field_consistency(tmp_path):
    workspace, source = audit_fixture(tmp_path, '给定记录数为8，计算10+8*1得到结果为18。')
    template = create_structure_review_template(workspace, 1, source)
    with pytest.raises(DevelopmentalEditError, match='前提不充分或仍存在反例'):
        record_structure_review(workspace, 1, source, audit_payload(template,
            counterexample_status='found', counterexample='改为3条时另一方案更快；这是边界，不推翻8条情境。'))
    assert record_structure_review(workspace, 1, source, audit_payload(template,
        inputs=['记录数8', '准备10分钟', '每条1分钟'], operation='10+8*1', observed_output='18',
        counterexample_status='none_found', counterexample=None,
        scope_limit='只对给定8条成立；3条是改变前提的适用边界，不能推广为所有数量。'))


def test_unknown_audit_does_not_become_supported_by_protocol(tmp_path):
    workspace, source = audit_fixture(tmp_path)
    template = create_structure_review_template(workspace, 1, source)
    payload = audit_payload(template, assessment='uncertain', input_status='uncertain',
        counterexample_status='uncertain', missing_premises=['当前没有可核的具体输入，不能确定结论。'])
    for observation in payload.observations:
        observation.assessment = 'uncertain'
        observation.teaching_checks['actionability'].assessment = 'uncertain'
    path = record_structure_review(workspace, 1, source, payload)
    assert '"uncertain"' in path.read_text(encoding='utf8')


def test_universal_claim_counterexample_still_rejects_supported(tmp_path):
    workspace, source = audit_fixture(tmp_path, '对所有记录数，计算甲方案得到的结果为更快。')
    template = create_structure_review_template(workspace, 1, source)
    with pytest.raises(DevelopmentalEditError, match='前提不充分或仍存在反例'):
        record_structure_review(workspace, 1, source, audit_payload(template,
            counterexample_status='found', counterexample='全称范围包含3条，3条时乙更快，推翻所有数量的结论。'))
