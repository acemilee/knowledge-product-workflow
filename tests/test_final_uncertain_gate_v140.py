"""必审未知可在制作中记录，但最终交付不能把它当确定通过。"""
import json

import pytest

from knowledge_product_workflow.chapter_compliance import (
    submit_chapter,
    validate_current_chapter_compliance,
)
from knowledge_product_workflow.developmental import (
    DevelopmentalEditError,
    create_structure_review_template,
    record_developmental_check,
    record_structure_review,
    require_structure_review,
)
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.humanize import record_humanize_check
from knowledge_product_workflow.quality import (
    quality_report_inputs,
    quality_report_validity,
    write_quality_report,
)
from knowledge_product_workflow.verification_context import verification_scope
from knowledge_product_workflow.workflows.authoring import create_chapter_draft
from tests.test_chapter_compliance import _mapping
from tests.test_premise_audit_o16 import audit_fixture, audit_payload


def _review(workspace, source, *, uncertain):
    template = create_structure_review_template(workspace, 1, source)
    payload = audit_payload(template)
    if uncertain:
        for observation in payload.observations:
            observation.assessment = 'uncertain'
            observation.teaching_checks['learning_increment'].assessment = 'uncertain'
            observation.teaching_checks['learning_increment'].note = '当前比较证据不足，只保留本节输入核对，不宣称完成跨章增量判断。'
    path = record_structure_review(workspace, 1, source, payload)
    record_developmental_check(workspace, 1, source)
    return path


def _book(tmp_path):
    workspace, source = audit_fixture(tmp_path)
    body = source.read_text(encoding='utf8').replace('\n\n## ',
        '\n\n::: {.kp-chapter-lead}\n本章从固定输入和判断规则出发，生成可复核的方案工作表。\n:::\n\n## ', 1)
    source.write_text(body, encoding='utf8')
    create_chapter_draft(workspace, 1)
    review = _review(workspace, source, uncertain=True)
    record_humanize_check(workspace, 1, source)
    mapping = workspace.root / 'runs/mapping.json'
    mapping.write_text(json.dumps(_mapping(), ensure_ascii=False), encoding='utf8')
    chapter, _ = submit_chapter(workspace, 1, source, mapping)
    return workspace, chapter, review


def test_intermediate_submission_keeps_unknown_but_final_explicitly_rejects(tmp_path):
    workspace, chapter, _ = _book(tmp_path)
    body = parse_markdown_artifact(chapter.read_text(encoding='utf8'))[1]
    assert require_structure_review(workspace, 1, body)
    assert validate_current_chapter_compliance(workspace, chapter) == []
    with pytest.raises(DevelopmentalEditError, match='必审.*uncertain.*1.1'):
        require_structure_review(workspace, 1, body, final_review=True)


@pytest.mark.parametrize('final_first', [False, True])
def test_final_and_intermediate_validation_do_not_share_wrong_memo(tmp_path, final_first):
    workspace, chapter, _ = _book(tmp_path)
    with verification_scope(workspace):
        outputs = {mode: validate_current_chapter_compliance(workspace, chapter, final_review=mode)
            for mode in (final_first, not final_first)}
    assert outputs[False] == []
    assert any('uncertain' in finding.message and finding.severity.value == 'blocker' for finding in outputs[True])


def test_source_and_delivery_quality_report_block_unknown_without_rewriting_body(tmp_path):
    from knowledge_product_workflow.workflows.validation import (
        run_delivery_validation,
        run_validation,
    )
    workspace, chapter, review = _book(tmp_path)
    before = chapter.read_bytes(), review.read_bytes()
    for gate in (run_validation, run_delivery_validation):
        _, report = gate(workspace)
        assert any('uncertain' in f.message and f.severity.value == 'blocker' for f in report.findings)
        assert not report.publishable
    assert before == (chapter.read_bytes(), review.read_bytes())


def test_release_readiness_reports_unknown_before_delivery_files_exist(tmp_path):
    from knowledge_product_workflow.publishing import release_readiness_findings
    workspace, _, _ = _book(tmp_path)
    findings = release_readiness_findings(workspace)
    assert any('uncertain' in f.message and '1.1' in f.message for f in findings)


def test_resolved_current_review_clears_only_uncertain_gate(tmp_path):
    workspace, chapter, _ = _book(tmp_path)
    before = chapter.read_bytes()
    assert validate_current_chapter_compliance(workspace, chapter, final_review=True)
    _review(workspace, chapter, uncertain=False)
    assert validate_current_chapter_compliance(workspace, chapter, final_review=True) == []
    assert chapter.read_bytes() == before


def test_quality_report_directly_binds_structure_review_bytes(tmp_path):
    workspace, _, review = _book(tmp_path)
    relative = review.relative_to(workspace.root).as_posix()
    assert relative in {item.path for item in quality_report_inputs(workspace, 'quality')}
    _, report = write_quality_report(workspace, 'quality', [])
    assert quality_report_validity(workspace, report, 'quality').value == 'current'
    review.write_bytes(review.read_bytes() + b'\n')
    assert quality_report_validity(workspace, report, 'quality').value == 'stale'


def test_assembled_input_graph_directly_binds_structure_review(tmp_path):
    from knowledge_product_workflow.publishing import _book_input_paths
    workspace, chapter, review = _book(tmp_path)
    paths = _book_input_paths(workspace, [chapter], chapter.read_text(encoding='utf8'), workspace.root / 'delivery/book.qmd')
    assert review in paths


def test_omitted_structure_input_requires_reassembly_even_when_review_passed(tmp_path):
    from knowledge_product_workflow.publishing import (
        assemble_book,
        book_input_manifest_path,
        release_readiness_findings,
    )
    workspace, chapter, review = _book(tmp_path)
    _review(workspace, chapter, uncertain=False)
    assemble_book(workspace)
    path = book_input_manifest_path(workspace)
    data = json.loads(path.read_text(encoding='utf8'))
    relative = review.relative_to(workspace.root).as_posix()
    assert relative in {item['path'] for item in data['inputs']}
    data['inputs'] = [item for item in data['inputs'] if item['path'] != relative]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
    assert any(f.code == 'RELEASE_INPUT_HASH_MISMATCH' and '未绑定当前结构审核' in f.message
        for f in release_readiness_findings(workspace))


def test_unconsumed_structure_history_is_not_a_new_quality_dependency(tmp_path):
    workspace, _, _ = _book(tmp_path)
    unused = workspace.root / 'runs/editorial/chapter-99-structure-review.json'
    unused.write_text('{}', encoding='utf8')
    relative = unused.relative_to(workspace.root).as_posix()
    assert relative not in {item.path for item in quality_report_inputs(workspace, 'quality')}


def test_quality_input_graph_enumerates_canonical_chapters_once(tmp_path, monkeypatch):
    from pathlib import Path
    workspace, _, _ = _book(tmp_path)
    original = Path.glob
    calls = []
    def counted(path, pattern, **kwargs):
        if path == workspace.root / 'chapters' and pattern == 'chapter-[0-9][0-9].md':
            calls.append(pattern)
        return original(path, pattern, **kwargs)
    monkeypatch.setattr(Path, 'glob', counted)
    quality_report_inputs(workspace, 'quality')
    assert len(calls) == 1


def test_publish_rechecks_unknown_and_keeps_existing_manifest(tmp_path):
    from knowledge_product_workflow.publishing import assemble_book, create_delivery_manifest
    workspace, chapter, _ = _book(tmp_path)
    markdown = assemble_book(workspace)
    before = chapter.read_bytes()
    # Final QA must detect uncertain; publish consumes that exact immutable report.
    from knowledge_product_workflow.workflows.validation import run_delivery_validation
    _, report = run_delivery_validation(workspace)
    assert not report.publishable
    assert any('uncertain' in f.message and f.severity.value == 'blocker' for f in report.findings)
    manifest = workspace.root / 'delivery/delivery-manifest.yaml'
    manifest.write_text('previous delivery sentinel', encoding='utf8')
    with pytest.raises(ValueError, match='质量报告未通过'):
        create_delivery_manifest(workspace, markdown, None)
    assert manifest.read_text(encoding='utf8') == 'previous delivery sentinel'
    assert chapter.read_bytes() == before


def test_same_mtime_review_change_rejects_before_following_write(tmp_path):
    import os

    from knowledge_product_workflow.verification_context import SnapshotChangedError, write_text
    workspace, chapter, review = _book(tmp_path)
    _review(workspace, chapter, uncertain=False)
    marker = workspace.root / 'delivery/should-not-publish.txt'
    before = review.stat()
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        assert validate_current_chapter_compliance(workspace, chapter, final_review=True) == []
        review.write_bytes(review.read_bytes() + b'\n')
        os.utime(review, ns=(before.st_atime_ns, before.st_mtime_ns))
        write_text(marker, 'forbidden stale publication', encoding='utf8')
    assert not marker.exists()


def test_workspace_without_required_structure_review_keeps_legacy_behavior(tmp_path):
    from knowledge_product_workflow.workspace import Workspace
    workspace = Workspace.create(tmp_path, 'legacy')
    assert require_structure_review(workspace, 1, '# 1. 旧稿\n', final_review=True) is None


def test_policy2_final_debt_blocks_delivery_but_independent_chapters_continue(tmp_path):
    from knowledge_product_workflow.production_dependencies import (
        deferred_repairs,
        require_no_deferred_repairs,
    )
    from knowledge_product_workflow.publishing import (
        assemble_book,
        create_delivery_manifest,
        render_pdf,
    )
    from tests.test_deferred_repairs_v140 import serial_book, submit
    from tests.test_structure_review_v133 import simulated_review
    workspace = serial_book(tmp_path, with_content_profile=True)
    source = workspace.root / 'chapters/chapter-01.md'
    before = source.read_bytes()
    simulated_review(workspace, source, assessment='uncertain')
    record_developmental_check(workspace, 1, source)
    for number in (2, 3, 4):
        submit(workspace, number)
    assert source.read_bytes() == before
    assert deferred_repairs(workspace) == []
    final_debts = deferred_repairs(workspace, final_review=True)
    assert {debt['chapter_number'] for debt in final_debts} == {1}
    for operation in (
        lambda: require_no_deferred_repairs(workspace),
        lambda: assemble_book(workspace),
        lambda: render_pdf(workspace.root / 'delivery/book.qmd', workspace.root / 'delivery', workspace),
        lambda: create_delivery_manifest(workspace, None, None),
    ):
        with pytest.raises(ValueError, match='uncertain'):
            operation()
