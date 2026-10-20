import pytest


@pytest.mark.parametrize('through_session', [False, True])
def test_diagnostic_can_request_extra_review_without_claiming_production_capability(tmp_path, through_session):
    from types import SimpleNamespace

    from knowledge_product_workflow.production_controller import NativeProductionSession
    from knowledge_product_workflow.production_tasks import (
        ProductionHostCapabilities,
        create_production_run,
        production_review_policy,
        resume_production_run,
    )
    from tests.test_deferred_repairs_v140 import serial_book

    workspace = serial_book(tmp_path)
    brief = workspace.root / 'planning/product-brief.md'
    before = brief.read_bytes()
    assert production_review_policy(workspace) == 'deterministic_only'
    if through_session:
        session = NativeProductionSession(workspace, SimpleNamespace(execution_kind='program_delegated'))
        reply = session.apply({'op': 'start', 'diagnostic': True,
            'diagnostic_review_policy': 'independent_review_required'})
        run = resume_production_run(workspace, reply['run_id'])
    else:
        run = create_production_run(workspace, ProductionPolicy(requested_parallelism=4),
            ProductionHostCapabilities(), diagnostic_only=True,
            diagnostic_review_policy='independent_review_required')
    assert run.review_policy == 'independent_review_required'
    assert run.diagnostic_only and not run.synthetic_local_checks
    assert not run.capabilities.admits_live_parallelism()
    assert run.tasks and all(task.input_bindings for task in run.tasks)
    assert brief.read_bytes() == before
    assert production_review_policy(workspace) == 'deterministic_only'


@pytest.mark.parametrize('diagnostic,policy', [
    (False, 'independent_review_required'), (True, 'deterministic_only'),
])
def test_extra_review_selector_cannot_change_production_or_remove_review(tmp_path, diagnostic, policy):
    from knowledge_product_workflow.production_tasks import (
        ProductionHostCapabilities,
        create_production_run,
    )
    from tests.test_chapter_content_plan import _approved_workspace

    workspace = _approved_workspace(tmp_path)
    before = {p.relative_to(workspace.root): p.read_bytes()
        for p in workspace.root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='诊断|diagnostic'):
        create_production_run(workspace, ProductionPolicy(), ProductionHostCapabilities(),
            diagnostic_only=diagnostic, diagnostic_review_policy=policy)
    assert {p.relative_to(workspace.root): p.read_bytes()
        for p in workspace.root.rglob('*') if p.is_file()} == before

from knowledge_product_workflow.production_tasks import (
    ProductionPolicy,
    ProductionRun,
    next_dispatches,
)


def test_review_is_version_bound_and_does_not_wait_for_whole_book():
    run = ProductionRun.diagnostic_run(20, ProductionPolicy(requested_parallelism=4))
    writer = next_dispatches(run)[0]
    run.complete_edit(writer.task_id, "原稿", input_manifest_sha256=writer.input_manifest_sha256)
    reviewer = next_dispatches(run)[0]
    assert reviewer.role == "reviewer"
    assert reviewer.candidate_version == writer.candidate_version
    run.supersede_candidate(1)
    with pytest.raises(ValueError, match="状态"):
        run.complete_review(reviewer.task_id, passed=True,
                            input_manifest_sha256=reviewer.input_manifest_sha256)


def test_same_result_is_idempotent_and_changed_result_is_rejected():
    run = ProductionRun.diagnostic_run(1, ProductionPolicy())
    writer = next_dispatches(run)[0]
    for _ in range(2):
        run.complete_edit(writer.task_id, "原稿", input_manifest_sha256=writer.input_manifest_sha256)
    assert len(run.tasks) == 2
    assert run.duplicate_results == 1
    with pytest.raises(ValueError, match="版本"):
        run.complete_edit(writer.task_id, "改稿", input_manifest_sha256=writer.input_manifest_sha256)


def test_private_whole_candidate_review_preserves_rubric_and_rejects_self_or_old_version(tmp_path):
    from pathlib import Path

    from knowledge_product_workflow.production_context import (
        evaluate_candidate,
        freeze_candidate_context,
    )
    from knowledge_product_workflow.production_tasks import ProductionHostCapabilities
    from knowledge_product_workflow.review_batches import ReviewResultInput, record_candidate_review
    from tests.test_structure_review_v133 import _fixture
    workspace, source, body = _fixture(tmp_path)
    body = body.replace("\n\n## ", "\n\n::: {.kp-chapter-lead}\n本章从输入、使用条件和可检查产出展开。\n:::\n\n## ", 1)
    source.write_text(body, encoding="utf-8")
    paths = [p.relative_to(workspace.root).as_posix() for p in workspace.root.rglob("*") if p.is_file()]
    run = ProductionRun.diagnostic_run(21, ProductionPolicy())
    writer = next_dispatches(run)[0]
    run.complete_edit(writer.task_id, body, input_manifest_sha256=writer.input_manifest_sha256)
    reviewer = next_dispatches(run)[0]
    context = freeze_candidate_context(workspace, chapter_number=1, candidate_version=1, source=source,
        input_paths=paths, run_id=run.run_id, task_id=writer.task_id)
    evaluate_candidate(context)
    result = ReviewResultInput(status="passed", model="offline-fixture", model_version="synthetic",
        prompt_hash="0"*64, reviewer_identity="reviewer", reviewer_session_id="reviewer-session",
        scores={k:4 for k in ("factual_grounding", "teaching_progression", "interview_actionability", "case_specificity", "expression_novelty")},
        unit_evidence=[])
    with pytest.raises(ValueError, match="作者身份"):
        record_candidate_review(context, author_identity="reviewer", author_session_id="author-session",
            reviewer_task=reviewer, result=result, capabilities=ProductionHostCapabilities())
    with pytest.raises(ValueError, match="跨任务"):
        record_candidate_review(context.model_copy(update={"candidate_version": 2}), author_identity="author",
            author_session_id="author-session", reviewer_task=reviewer, result=result,
            capabilities=ProductionHostCapabilities())
    with pytest.raises(ValueError, match="宿主资格"):
        record_candidate_review(context, author_identity="author", author_session_id="author-session",
            reviewer_task=reviewer, result=result, capabilities=ProductionHostCapabilities(), diagnostic_only=False)
    record = record_candidate_review(context, author_identity="author", author_session_id="author-session",
        reviewer_task=reviewer, result=result, capabilities=ProductionHostCapabilities())
    assert record["diagnostic_only"] and not record["acceptance_ready"]
    assert any("必需教学单元" in issue for issue in record["issues"])
    assert Path(context.private_result_root, "candidate-review.json").is_file()
    assert not list((workspace.root / "chapters").glob("*-review.json"))


@pytest.mark.parametrize("policy,expected_reviewers",[("deterministic_only",0),("independent_review_required",1)])
def test_local_completion_uses_bound_brief_policy_without_adding_blind_review(policy,expected_reviewers):
    run=ProductionRun.diagnostic_run(1,ProductionPolicy())
    run.review_policy=policy
    writer=next_dispatches(run)[0]
    run.complete_edit(writer.task_id,"明确标注的状态机夹具",input_manifest_sha256=writer.input_manifest_sha256)
    assert len([t for t in run.tasks if t.role=="reviewer"])==expected_reviewers
    assert writer.state==("acceptance_ready" if not expected_reviewers else "review_ready")


def test_new_run_inherits_default_policy_from_approved_brief(tmp_path):
    from knowledge_product_workflow.production_tasks import (
        ProductionHostCapabilities,
        create_production_run,
    )
    from tests.test_chapter_content_plan import _approved_workspace
    workspace=_approved_workspace(tmp_path)
    run=create_production_run(workspace,ProductionPolicy(),ProductionHostCapabilities(),diagnostic_only=True)
    assert run.review_policy=="deterministic_only" and run.diagnostic_only
