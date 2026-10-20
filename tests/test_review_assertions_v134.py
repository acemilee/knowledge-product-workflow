"""Rubric3验证引用与处置的一致性；这些夹具不是模型语义盲评。"""
import json

import pytest

from knowledge_product_workflow.developmental import (
    DevelopmentalEditError,
    StructureReviewInput,
    create_structure_review_template,
    record_structure_review,
)
from knowledge_product_workflow.runtime import (
    _atomic_json_write,
    _orchestration_state_path,
    _read_orchestration_state,
)
from tests.test_structure_review_v133 import _fixture, fixture_teaching_checks


def assertion_fixture(tmp_path):
    workspace, source, body = _fixture(tmp_path)
    _atomic_json_write(_orchestration_state_path(workspace), _read_orchestration_state(workspace).model_copy(update={'reading_review_rubric_revision':3}))
    source.write_text(body + '\n运行检索后得到得分为 0.8，取回第二条给定资料。\n', encoding='utf-8')
    return workspace, source, create_structure_review_template(workspace, 1, source)


def payload_for(template, status='supported'):
    assessments = 'revise' if status == 'unsupported' else 'uncertain' if status == 'uncertain' else 'passed'
    checks = []
    for item in template['assertion_prompts']:
        checks.append({'assertion_id':item['assertion_id'], 'quote':item['quote'], 'assessment':status,
            'support_refs':['content_plan'], 'missing_premises':[] if status=='supported' else ['缺实际执行输出'],
            'counterexample':'其他环节也可能改变同一输出，当前未给证据。' if status=='unsupported' else None,
            'note':'以当前语料和实际输出对照命题，未额外补造查询向量或分数。'})
    return StructureReviewInput(template_id=template['template_id'], observations=[{
        'unit_id':o['unit_id'], 'assessment':assessments, 'note':'测试具体观察：当前命题与给定输入及对照产出逐项核对。',
        'teaching_checks':fixture_teaching_checks(assessments),
        'assertion_checks':[c for c in checks if next(p for p in template['assertion_prompts'] if p['assertion_id']==c['assertion_id'])['unit_id']==o['unit_id']]
    } for o in template['observations']])


def test_new_rubric_template_returns_current_quotes_and_required_assertions(tmp_path):
    _, _, template = assertion_fixture(tmp_path)
    assert template['assertion_prompts']
    assert template['binding']['rubric_revision'] == 3
    assert any('0.8' in item['quote'] for item in template['assertion_prompts'])


def test_supported_bound_assertions_round_trip(tmp_path):
    workspace, source, template = assertion_fixture(tmp_path)
    path = record_structure_review(workspace, 1, source, payload_for(template))
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['observations'][0]['assertion_checks']


def test_quote_mismatch_is_rejected_before_receipt(tmp_path):
    workspace, source, template = assertion_fixture(tmp_path)
    payload = payload_for(template)
    payload.observations[0].assertion_checks[0].quote = '不在当前正文中的主张。'
    with pytest.raises(DevelopmentalEditError, match='引文'):
        record_structure_review(workspace, 1, source, payload)


def test_unsupported_cannot_be_reported_as_passed(tmp_path):
    workspace, source, template = assertion_fixture(tmp_path)
    payload = payload_for(template, 'unsupported')
    payload.observations[0].assessment = 'passed'
    from knowledge_product_workflow.developmental import TeachingCheck
    payload.observations[0].teaching_checks = {key:TeachingCheck.model_validate(value) for key,value in fixture_teaching_checks().items()}
    with pytest.raises(DevelopmentalEditError, match='命题'):
        record_structure_review(workspace, 1, source, payload)


def test_missing_assertion_is_rejected(tmp_path):
    workspace, source, template = assertion_fixture(tmp_path)
    payload = payload_for(template)
    payload.observations[0].assertion_checks = []
    with pytest.raises(DevelopmentalEditError, match='命题'):
        record_structure_review(workspace, 1, source, payload)
