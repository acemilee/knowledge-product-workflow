"""只拒本次已证的机械占位，新提交与旧已绑定观察分别处理。"""
import json

import pytest

from knowledge_product_workflow.developmental import (
    DevelopmentalEditError,
    StructureReviewInput,
    create_structure_review_template,
    record_structure_review,
    require_structure_review,
)
from tests.test_structure_review_v133 import _fixture, fixture_teaching_checks, simulated_review


def test_new_mechanical_teaching_pass_is_rejected_without_revoking_existing_report(tmp_path):
    w, source, body = _fixture(tmp_path)
    source.write_text(body, encoding='utf8')
    path = simulated_review(w, source)
    before = path.read_bytes()
    template = create_structure_review_template(w,1,source)
    observations = []
    for original in json.loads(before)['observations']:
        o = {k:v for k,v in original.items() if k in {'unit_id','assessment','note','teaching_checks','assertion_checks'}}
        o['teaching_checks'] = fixture_teaching_checks()
        o['teaching_checks']['representation_fit']['note'] = f"{o['unit_id']} 的representation_fit：与本节目标一致，按第 1 章整体结构核对后未见冲突。"
        observations.append(o)
    payload = StructureReviewInput(template_id=template['template_id'], observations=observations)
    with pytest.raises(DevelopmentalEditError, match='机械占位'):
        record_structure_review(w,1,source,payload)
    assert path.read_bytes()==before
    assert require_structure_review(w,1,body) is not None


def test_legacy_observation_is_not_retroactively_rejected_by_new_intake_rule(tmp_path):
    w, source, body = _fixture(tmp_path)
    path = simulated_review(w,source)
    old = json.loads(path.read_text(encoding='utf8'))
    for o in old['observations']:
        o['teaching_checks']['representation_fit']['note'] = f"{o['unit_id']} 的representation_fit：与本节目标一致，按第 1 章整体结构核对后未见冲突。"
    path.write_text(json.dumps(old,ensure_ascii=False),encoding='utf8')
    # A serialized old record fixture, not a claim that its content was reviewed.
    assert require_structure_review(w,1,body) is not None


@pytest.mark.parametrize('assessment',['passed','uncertain','revise'])
def test_specific_observations_keep_original_aggregation_policy(tmp_path, assessment):
    w, source, body = _fixture(tmp_path)
    simulated_review(w,source,assessment=assessment)
    if assessment=='revise':
        with pytest.raises(DevelopmentalEditError):
            require_structure_review(w,1,body)
    else:
        assert require_structure_review(w,1,body) is not None


def test_new_generic_reason_with_unit_suffix_is_still_rejected(tmp_path):
    w, source, _ = _fixture(tmp_path)
    template = create_structure_review_template(w, 1, source)
    checks = fixture_teaching_checks()
    checks['representation_fit']['note'] = '用文字、表格或图示中更贴合该结构的一种表达。（1.1）'
    payload = StructureReviewInput(template_id=template['template_id'], observations=[
        {'unit_id':o['unit_id'],'assessment':'passed','note':'本单元软件夹具保留比较表，不宣称语义已审。',
         'teaching_checks':checks} for o in template['observations']])
    with pytest.raises(DevelopmentalEditError, match='机械占位'):
        record_structure_review(w,1,source,payload)


def test_explicit_new_evidence_policy_requires_current_unit_quote(tmp_path):
    from knowledge_product_workflow.runtime import (
        _atomic_json_write,
        _orchestration_state_path,
        _read_orchestration_state,
    )
    w, source, body = _fixture(tmp_path)
    state = _read_orchestration_state(w)
    _atomic_json_write(_orchestration_state_path(w), state.model_copy(update={'review_evidence_revision':1}))
    template = create_structure_review_template(w,1,source)
    checks = fixture_teaching_checks()
    payload = StructureReviewInput(template_id=template['template_id'], observations=[
        {'unit_id':o['unit_id'],'assessment':'passed','note':'本单元软件夹具保留比较表，不宣称语义已审。',
         'teaching_checks':checks} for o in template['observations']])
    with pytest.raises(DevelopmentalEditError,match='原文依据'):
        record_structure_review(w,1,source,payload)
    for observation in payload.observations:
        unit = next(u for u in template['units'] if u['unit_id'] == observation.unit_id)
        quote = next(line for line in body.splitlines()[unit['start_line']:] if line.strip() and not line.startswith('#'))
        for check in observation.teaching_checks.values():
            check.evidence_quote = quote
    record_structure_review(w,1,source,payload)
    assert require_structure_review(w,1,body)
