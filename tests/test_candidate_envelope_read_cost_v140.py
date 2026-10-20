"""A review envelope must read a frozen manifest linearly and reject late drift."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_product_workflow.production_context import freeze_candidate_context
from knowledge_product_workflow.production_controller import NativeProductionSession
from knowledge_product_workflow.workspace import Workspace


def _case(tmp_path, units=6, *, binary_note=False, mixed_newlines=False):
    workspace = Workspace.create(tmp_path, 'envelope')
    body = '# 1. 教程\n\n' + '\n\n'.join(f'## 1.{n} 单元{n}\n\n本单元给定条件{n}与结果。' for n in range(1, units + 1))
    source = workspace.root / 'chapters/chapter-01.md'
    source.write_bytes((body.replace('\n', '\r\n').replace('本单元', '本\r单元')
        if mixed_newlines else body).encode('utf8'))
    plan = workspace.root / 'chapters/chapter-01-content-plan.yaml'
    plan.write_text('chapter: 1\n', encoding='utf8')
    report = workspace.root / 'runs/editorial/chapter-01-structure-review.json'
    report.parent.mkdir()
    report.write_text(json.dumps({'comparison_context': {f'1.{n}': {} for n in range(1, units + 1)}}), encoding='utf8')
    other = workspace.root / 'research/frozen-note.txt'
    other.write_bytes(b'\xff\x00\xfe' if binary_note else b'original')
    context = freeze_candidate_context(workspace, chapter_number=1, candidate_version=1,
        source=source, input_paths=[str(p.relative_to(workspace.root)) for p in (plan, report, other)],
        run_id='test-run', task_id='writer-one')
    context_path = Path(context.snapshot_root).parent / 'context.json'
    Path(context.private_result_root).mkdir()
    Path(context.private_result_root, 'structure-template.json').write_text('{}', encoding='utf8')
    task = SimpleNamespace(task_id='semantic-one', role='semantic', chapter_number=1,
        candidate_version=1, input_manifest_sha256=context.input_manifest_sha256,
        input_bindings={}, state='review_ready', candidate_context_path=str(context_path))
    session = NativeProductionSession(workspace, object())
    session.run_id = 'test-run'
    return session, task, context, body


@pytest.mark.parametrize('units', [1, 6, 12])
def test_review_envelope_manifest_reads_do_not_multiply_by_comparison_units(tmp_path, monkeypatch, units):
    session, task, context, body = _case(tmp_path, units)
    snapshot = Path(context.snapshot_root)
    reads = []
    original = Path.read_bytes
    def counted(path):
        if path.is_relative_to(snapshot):
            reads.append(path)
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', counted)
    envelope = session._envelope(task)
    assert envelope['candidate_body'] == body
    assert set(envelope['comparison_units']) == {f'1.{n}' for n in range(1, units + 1)}
    for number in range(1, units + 1):
        assert envelope['comparison_units'][f'1.{number}'].startswith(f'## 1.{number} ')
    assert envelope['comparison_plans'] == {'chapters/chapter-01-content-plan.yaml': 'chapter: 1\n'}
    assert len(reads) <= 2 * len(context.input_bindings)


def test_review_envelope_rejects_last_projection_same_mtime_manifest_drift(tmp_path, monkeypatch):
    from knowledge_product_workflow.developmental import StructureReviewInput
    session, task, context, _ = _case(tmp_path)
    path = Path(context.snapshot_root, 'research/frozen-note.txt')
    before = path.stat()
    schema = StructureReviewInput.model_json_schema
    def changed_schema(*args, **kwargs):
        value = schema(*args, **kwargs)
        path.write_text('modified', encoding='utf8')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        return value
    monkeypatch.setattr(StructureReviewInput, 'model_json_schema', changed_schema)
    with pytest.raises(ValueError, match='篡改'):
        session._envelope(task)


def test_public_single_input_read_still_checks_unrelated_manifest_member(tmp_path):
    _, _, context, _ = _case(tmp_path)
    Path(context.snapshot_root, 'research/frozen-note.txt').write_text('modified', encoding='utf8')
    with pytest.raises(ValueError, match='篡改'):
        context.read_input(context.source_relative)


def test_envelope_decodes_only_selected_text_and_preserves_universal_newlines(tmp_path):
    session, task, context, body = _case(tmp_path, units=2, binary_note=True, mixed_newlines=True)
    envelope = session._envelope(task)
    assert envelope['candidate_body'] == body.replace('本单元', '本\n单元')
    assert envelope['candidate_body'] == context.read_input(context.source_relative)


def test_missing_comparison_unit_input_fails_closed(tmp_path):
    session, task, context, _ = _case(tmp_path)
    report = Path(context.snapshot_root, 'runs/editorial/chapter-01-structure-review.json')
    data = json.loads(report.read_text(encoding='utf8'))
    data['comparison_context']['2.1'] = {}
    report.write_text(json.dumps(data), encoding='utf8')
    from knowledge_product_workflow.production_dependencies import file_digest, payload_digest
    context.input_bindings['runs/editorial/chapter-01-structure-review.json'] = file_digest(report)
    context.input_manifest_sha256 = payload_digest({k:getattr(context,k) for k in
        ('task_id','chapter_number','candidate_version','input_bindings','live_bindings')})
    Path(task.candidate_context_path).write_text(context.model_dump_json(), encoding='utf8')
    with pytest.raises(ValueError, match='清单'):
        session._envelope(task)
