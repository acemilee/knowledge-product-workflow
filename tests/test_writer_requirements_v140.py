"""Writer output syntax must arrive before inference, without broad style invalidation."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from knowledge_product_workflow.presentation import _read_profile, publication_profile_path
from knowledge_product_workflow.production_dependencies import binding_digest, build_task_context
from tests.test_production_delegated_host_v140 import prepared

FORMAT_KEY = 'planning/publication-profile.yaml#authoring-format=1'


def _profile(workspace, revision):
    path = publication_profile_path(workspace)
    profile = _read_profile(path).model_copy(update={'reader_layout_revision': revision})
    path.write_text(yaml.safe_dump(profile.model_dump(mode='json'), allow_unicode=True), encoding='utf8')
    return path


def _writer(session, run, workspace):
    task = run.tasks[0]
    context = build_task_context(workspace, task.chapter_number, 'writer')
    task.input_bindings = context['live_bindings']
    return task


def test_writer_receives_required_lead_syntax_and_plan_blocks_before_model_call(tmp_path):
    workspace, run, _, client, _, session = prepared(tmp_path)
    _profile(workspace, 2)
    task = _writer(session, run, workspace)
    assert FORMAT_KEY in task.input_bindings
    envelope = session._envelope(task)
    requirements = envelope['writer_requirements']
    assert requirements['reader_layout_revision'] == 2
    assert requirements['chapter_lead']['required'] is True
    assert requirements['chapter_lead']['open'] == '::: {.kp-chapter-lead}'
    assert requirements['chapter_lead']['close'] == ':::'
    assert requirements['chapter_lead']['placement'] == 'after_h1_before_first_h2'
    assert requirements['chapter_heading'].startswith('# 1. ')
    assert requirements['sections']
    assert requirements['worksheet_labels'] == ['使用说明', '输入', '输出示例', '复盘']
    assert json.loads(envelope['inputs'][FORMAT_KEY]) == {'reader_layout_revision': 2}
    assert client.model_turns == 1  # Only the fixture's control turn, no author inference.


def test_only_reader_format_changes_writer_format_binding(tmp_path):
    workspace, _, _, _, _, _ = prepared(tmp_path)
    path = _profile(workspace, 2)
    before = binding_digest(workspace, FORMAT_KEY)
    profile = _read_profile(path).model_copy(update={'body_font_size': 14})
    path.write_text(yaml.safe_dump(profile.model_dump(mode='json')), encoding='utf8')
    assert binding_digest(workspace, FORMAT_KEY) == before
    _profile(workspace, 1)
    assert binding_digest(workspace, FORMAT_KEY) != before
    with pytest.raises(ValueError):
        binding_digest(workspace, 'research/other.yaml#authoring-format=1')


@pytest.mark.parametrize('revision,archetype,required', [(1, 'strategy', False),
    (2, 'reference', False), (2, 'appendix', False), (2, 'strategy', True)])
def test_requirements_match_existing_lead_policy_without_forcing_legacy_or_appendix(tmp_path, revision, archetype, required):
    workspace, run, _, _, _, session = prepared(tmp_path)
    _profile(workspace, revision)
    task = _writer(session, run, workspace)
    from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
    path, chapter = require_current_chapter_contract(workspace, task.chapter_number)
    selected = chapter.model_copy(update={'chapter_archetype': SimpleNamespace(value=archetype)})
    with patch('knowledge_product_workflow.workflows.authoring.require_current_chapter_contract', return_value=(path, selected)):
        requirements = session._envelope(task)['writer_requirements']
    assert requirements['chapter_lead']['required'] is required


def test_both_inline_transports_explicitly_instruct_writer_to_follow_requirements(tmp_path):
    _workspace, _, _, client, _, session = prepared(tmp_path)
    session.dispatch()
    prompt = next(r['params']['input'][0]['text'] for r in reversed(client.request_receipts)
        if r['method'] == 'turn/start' and 'parentTurnId' in r['params'])
    assert 'writer_requirements' in prompt.split('\n', 1)[0]
    assert next(iter(client.envelopes.values()))['writer_requirements']
    assert '教学/工件' in prompt.split('\n', 1)[0]
    assert next(iter(client.envelopes.values()))['writer_requirements']['artifact_completion']
    # The native transport must forward the same field; it cannot rely on parent history.
    import inspect

    from knowledge_product_workflow.production_controller import NativeInlineBridge
    assert 'writer_requirements' in inspect.getsource(NativeInlineBridge.dispatch)


def test_native_spawn_forwards_the_same_artifact_requirements_without_a_self_review_turn(tmp_path):
    from knowledge_product_workflow.production_controller import NativeInlineBridge
    from tests.test_production_native_origin_v140 import Client

    workspace, run, _, _, _, session = prepared(tmp_path)
    _rubric(workspace, 2)
    envelope = session._envelope(_writer(session, run, workspace))
    client = Client()
    bridge = NativeInlineBridge(client, model='fixture', max_children=4)
    bridge.parent_id = 'fixture-parent'
    bridge._root_scope_verified = True
    bridge.dispatch([envelope])
    text = client.params['input'][0]['text']
    prefix, raw = text.split('\n', 1)
    assert '教学/工件' in prefix and '不新增自评报告' in prefix
    forwarded = json.loads(raw)[0]
    assert forwarded['writer_requirements'] == envelope['writer_requirements']
    assert client.model_turns == 1


def test_frozen_quality_context_keeps_full_profile_binding_after_writer_projection(tmp_path):
    from knowledge_product_workflow.production_context import prepare_candidate_context
    workspace, run, body, _, _, _ = prepared(tmp_path)
    _profile(workspace, 2)
    (workspace.root / 'runs/format-candidate.md').write_text(body, encoding='utf8')
    task = run.tasks[0].model_copy(update={'source_relative': 'runs/format-candidate.md',
        'input_bindings': {FORMAT_KEY: binding_digest(workspace, FORMAT_KEY)}})
    context = prepare_candidate_context(workspace, task)
    assert context.live_bindings[FORMAT_KEY] == binding_digest(workspace, FORMAT_KEY)
    assert context.live_bindings['planning/publication-profile.yaml'] == binding_digest(workspace, 'planning/publication-profile.yaml')


def test_freeze_reports_original_quality_failure_before_template_lookup(tmp_path):
    import re

    from knowledge_product_workflow.production_tasks import resume_production_run
    workspace, _, body, client, _, session = prepared(tmp_path)
    _profile(workspace, 2)
    ids = session.dispatch()['task_ids']
    thread, envelope = next(iter(client.envelopes.items()))
    bad_body = re.sub(r'::: \{\.kp-chapter-lead\}.*?:::\s*', '', body, flags=re.DOTALL)
    result = {k:envelope[k] for k in ('task_id','role','candidate_version','input_manifest_sha256')}
    client.finish(thread, {**result, 'result': {'body': bad_body}})
    session.apply({'op':'collect','timeout':0.1})
    outcome = session.apply({'op':'freeze','task_id':ids[0]})
    assert outcome['attention']['kind'] == 'candidate_quality_blocked'
    assert 'CHAPTER_LEAD_REQUIRED' in outcome['attention']['codes']
    assert 'structure_template' not in outcome['proof']['reports']
    assert not any(t.role == 'reviewer' for t in resume_production_run(workspace, session.run_id).tasks)
    assert client.model_turns == 2


def test_late_format_change_during_requirement_derivation_is_rejected_before_dispatch(tmp_path):
    import os
    workspace, run, _, client, _, session = prepared(tmp_path)
    path = _profile(workspace, 2)
    task = _writer(session, run, workspace)
    from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
    def changed_contract(*args, **kwargs):
        value = require_current_chapter_contract(*args, **kwargs)
        before = path.stat()
        _profile(workspace, 1)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        return value
    with patch('knowledge_product_workflow.workflows.authoring.require_current_chapter_contract', side_effect=changed_contract), pytest.raises(ValueError, match='输入|格式'):
        session._envelope(task)
    assert client.model_turns == 1


def _rubric(workspace, revision):
    path = workspace.root / 'runs/orchestration/state.json'
    state = json.loads(path.read_text(encoding='utf8'))
    state['reading_review_rubric_revision'] = revision
    path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf8')


def test_writer_receives_existing_teaching_rubric_and_promised_artifact_before_inference(tmp_path):
    from knowledge_product_workflow.content_plan import require_current_content_plan
    from knowledge_product_workflow.developmental import TEACHING_CHECKS

    workspace, run, _, client, _, session = prepared(tmp_path)
    _rubric(workspace, 2)
    task = _writer(session, run, workspace)
    plan = require_current_content_plan(workspace, 1)
    requirements = session._envelope(task)['writer_requirements']
    assert requirements['teaching_rubric'] == TEACHING_CHECKS
    for promised, section in zip(plan.sections, requirements['sections'], strict=True):
        assert section['reader_job'] == promised.reader_job
        assert section['artifact'] == promised.artifact
        assert section['asset_decision'] == promised.asset_decision
        assert section['artifact_kind'] == promised.artifact_kind
    assert requirements['artifact_completion']['scope'] == 'promised_artifacts_only'
    assert requirements['artifact_completion']['given_input']
    assert requirements['artifact_completion']['worked_output']
    assert requirements['artifact_completion']['evidence_boundary']
    assert client.model_turns == 1


def test_no_artifact_plan_does_not_invent_a_worksheet_or_new_legacy_rubric(tmp_path):
    workspace, run, _, client, _, session = prepared(tmp_path)
    _rubric(workspace, 1)
    path = workspace.root / 'chapters/chapter-01-content-plan.yaml'
    raw = yaml.safe_load(path.read_text(encoding='utf8'))
    for section in raw['sections']:
        section.update(artifact='none', asset_decision='none', asset_rationale='散文给出判断，不承诺额外工件。')
        section.pop('artifact_kind', None)
        section['block_sequence'] = ['explanation', 'example']
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding='utf8')
    task = _writer(session, run, workspace)
    requirements = session._envelope(task)['writer_requirements']
    assert requirements['teaching_rubric'] == {}
    assert requirements['artifact_completion'] is None
    assert all(section['artifact'] == 'none' for section in requirements['sections'])
    assert client.model_turns == 1


def test_returned_writer_checks_cannot_mutate_the_shared_review_rubric(tmp_path):
    from knowledge_product_workflow.developmental import TEACHING_CHECKS

    workspace, run, _, _, _, session = prepared(tmp_path)
    _rubric(workspace, 2)
    task = _writer(session, run, workspace)
    requirements = session._envelope(task)['writer_requirements']
    requirements['teaching_rubric']['actionability'] = 'polluted'
    assert TEACHING_CHECKS['actionability'] != 'polluted'
    assert session._envelope(task)['writer_requirements']['teaching_rubric'] == TEACHING_CHECKS


def test_late_plan_change_during_teaching_requirement_derivation_stops_before_inference(tmp_path):
    import os
    from collections.abc import Mapping

    from knowledge_product_workflow.developmental import TEACHING_CHECKS

    workspace, run, _, client, _, session = prepared(tmp_path)
    _rubric(workspace, 2)
    task = _writer(session, run, workspace)
    path = workspace.root / 'chapters/chapter-01-content-plan.yaml'

    class ChangedChecks(Mapping):
        def __iter__(self):
            before = path.stat()
            path.write_text(path.read_text(encoding='utf8') + '\n', encoding='utf8')
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            return iter(TEACHING_CHECKS)

        def __len__(self):
            return len(TEACHING_CHECKS)

        def __getitem__(self, key):
            return TEACHING_CHECKS[key]

    with patch('knowledge_product_workflow.developmental.TEACHING_CHECKS', ChangedChecks()), \
            pytest.raises(ValueError, match='输入|格式'):
        session._envelope(task)
    assert client.model_turns == 1
