"""A selected approved sample needs its existing plan in the same immutable review view."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from knowledge_product_workflow.content_plan import (
    require_current_content_plan,
    submit_content_plan,
)
from knowledge_product_workflow.production_context import (
    evaluate_candidate,
    freeze_candidate_context,
    prepare_candidate_context,
)
from knowledge_product_workflow.production_controller import NativeProductionSession
from knowledge_product_workflow.production_dependencies import (
    binding_digest,
    build_task_context,
    payload_digest,
)
from knowledge_product_workflow.production_tasks import ProductionTask
from tests.test_chapter_compliance import _body
from tests.test_deferred_repairs_v140 import serial_book
from tests.test_illustration_policy import plan

SAMPLE = 'chapters/chapter-01.md'
SAMPLE_PLAN = 'chapters/chapter-01-content-plan.yaml'


def prepared(tmp_path):
    w = serial_book(tmp_path, with_content_profile=True)
    payload = plan()
    payload['sections'][0].update(section_id='2.1', production_dependencies=[])
    path = w.root / 'runs/plan-2.yaml'
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding='utf8')
    submit_content_plan(w, 2, path)
    body = _body().replace('1.', '2.').replace('基础判断', '章节2')
    body = body.replace('\n\n## ', '\n\n::: {.kp-chapter-lead}\n本章基于输入和规则给出可核对的判断。\n:::\n\n## ', 1)
    body += '\n可查属性表\n\n| 输入 | 核验 |\n| --- | --- |\n| 证据不足 | 记录待核条件 |\n'
    source = w.root / 'runs/private-source-2.md'
    source.write_text(body, encoding='utf8')
    bindings = build_task_context(w, 2, 'writer')['live_bindings']
    assert SAMPLE in bindings and SAMPLE_PLAN not in bindings
    writer = ProductionTask(task_id='writer-pair', run_id='pair-case', chapter_number=2,
        role='writer', input_bindings=bindings, input_manifest_sha256=payload_digest(bindings),
        source_relative=source.relative_to(w.root).as_posix())
    return w, writer, source


def envelope(w, writer, context, role='semantic'):
    evaluate_candidate(context)
    task = SimpleNamespace(task_id=role+'-pair', role=role, chapter_number=2, candidate_version=1,
        input_manifest_sha256='fixture-only', input_bindings=writer.input_bindings, state='review_ready',
        candidate_context_path=str(Path(context.snapshot_root).parent / 'context.json'))
    session = NativeProductionSession(w, object())
    session.run_id = writer.run_id
    return session._envelope(task)


def test_selected_sample_plan_is_frozen_and_live_bound_without_other_prose(tmp_path):
    w, writer, _ = prepared(tmp_path)
    original_inputs = dict(writer.input_bindings)
    before = (w.root / SAMPLE_PLAN).read_bytes()
    other = w.root / 'chapters/chapter-99-content-plan.yaml'
    other.write_text('unrelated: must-not-be-read', encoding='utf8')
    context = prepare_candidate_context(w, writer)
    assert SAMPLE_PLAN in context.input_bindings
    assert context.live_bindings[SAMPLE_PLAN] == binding_digest(w, SAMPLE_PLAN)
    assert Path(context.snapshot_root, SAMPLE_PLAN).read_bytes() == before
    assert 'chapters/chapter-99-content-plan.yaml' not in context.input_bindings
    assert 'chapters/chapter-03.md' not in context.input_bindings
    assert writer.input_bindings == original_inputs


@pytest.mark.parametrize('role', ['semantic', 'reviewer'])
def test_approved_sample_comparison_gets_frozen_companion_plan_before_model(tmp_path, role):
    w, writer, _ = prepared(tmp_path)
    context = prepare_candidate_context(w, writer)
    packet = envelope(w, writer, context, role)
    assert SAMPLE in packet['inputs'] and SAMPLE_PLAN not in packet['inputs']
    assert packet['comparison_plans'][SAMPLE_PLAN] == Path(context.snapshot_root, SAMPLE_PLAN).read_text(encoding='utf8')
    assert '1.1' in packet['comparison_scope']['eligible_compared_unit_ids']
    assert packet['comparison_scope']['exclude_current_observation_unit']
    assert require_current_content_plan(w, 1) is not None


def test_old_snapshot_without_companion_stays_deferred_and_never_reads_live_fallback(tmp_path):
    w, writer, source = prepared(tmp_path)
    current = prepare_candidate_context(w, writer)
    old = freeze_candidate_context(w, chapter_number=2, candidate_version=1, source=source,
        input_paths=[p for p in current.input_bindings if p != SAMPLE_PLAN],
        run_id=writer.run_id, task_id='writer-old')
    packet = envelope(w, writer, old)
    assert SAMPLE_PLAN not in packet['comparison_plans']
    assert packet['comparison_scope']['unavailable_index_units']['1.1'] == 'invalid_plan_deferred'
    assert '1.1' not in packet['comparison_scope']['eligible_compared_unit_ids']


def test_companion_plan_tamper_same_mtime_is_rejected_before_dispatch(tmp_path):
    w, writer, _ = prepared(tmp_path)
    context = prepare_candidate_context(w, writer)
    evaluate_candidate(context)
    frozen = Path(context.snapshot_root, SAMPLE_PLAN)
    before = frozen.stat()
    frozen.write_text(frozen.read_text(encoding='utf8') + '\n', encoding='utf8')
    os.utime(frozen, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match='篡改'):
        envelope(w, writer, context)


def test_bad_frozen_plan_is_not_upgraded_using_a_good_live_plan(tmp_path):
    w, writer, source = prepared(tmp_path)
    current = prepare_candidate_context(w, writer)
    path = w.root / SAMPLE_PLAN
    before = path.read_bytes()
    path.write_text('sections: invalid-plan', encoding='utf8')
    bad = freeze_candidate_context(w, chapter_number=2, candidate_version=1, source=source,
        input_paths=list(current.input_bindings), run_id=writer.run_id, task_id='writer-bad')
    path.write_bytes(before)
    packet = envelope(w, writer, bad)
    assert packet['comparison_scope']['unavailable_index_units']['1.1'] == 'invalid_plan_deferred'
    assert SAMPLE_PLAN not in packet['comparison_plans']


def test_mixed_live_sample_body_and_frozen_plan_are_rejected(tmp_path):
    w, writer, _ = prepared(tmp_path)
    context = prepare_candidate_context(w, writer)
    source = w.root / SAMPLE
    source.write_text(source.read_text(encoding='utf8') + '\n新的样章内容。\n', encoding='utf8')
    writer.input_bindings[SAMPLE] = binding_digest(w, SAMPLE)
    with pytest.raises(ValueError, match='样章正文.*冻结版本'):
        envelope(w, writer, context)


def test_later_canonical_plan_change_keeps_frozen_evidence_and_is_live_bound(tmp_path):
    w, writer, _ = prepared(tmp_path)
    context = prepare_candidate_context(w, writer)
    original = Path(context.snapshot_root, SAMPLE_PLAN).read_text(encoding='utf8')
    path = w.root / SAMPLE_PLAN
    path.write_text(path.read_text(encoding='utf8') + '\n', encoding='utf8')
    assert binding_digest(w, SAMPLE_PLAN) != context.live_bindings[SAMPLE_PLAN]
    packet = envelope(w, writer, context)
    assert packet['comparison_plans'][SAMPLE_PLAN] == original
