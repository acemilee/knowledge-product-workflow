import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.review_batches import (
    FakeHostTaskDispatcher,
    ReviewMode,
    ReviewResultInput,
    ReviewTaskState,
    submit_review_batch,
    submit_review_result,
    validate_review_batches,
)
from knowledge_product_workflow.runtime import HostCapabilityPreflightError
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.authoring import (
    compile_chapter_contracts,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workflows.validation import run_validation
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint, submit_test_chapter


def _write_chapter(workspace: Workspace, number: int) -> None:
    (workspace.root / "chapters" / f"chapter-{number:02d}.md").write_text(
        "---\n"
        f"book_id: {workspace.root.name}\nartifact_type: chapter\nstatus: in_production\n"
        "version: 1\ndepends_on: []\napproved_by: null\n---\n"
        f"# 第 {number} 章\n\n第 {number} 章正文。\n",
        encoding="utf-8",
    )
    (workspace.root / "chapters" / f"chapter-{number:02d}-contract.md").write_text(
        "## 章节预算\n\n```yaml\nrequired_units:\n  - concept_explanation\n  - worked_case\n```\n",
        encoding="utf-8",
    )


def _result_input() -> ReviewResultInput:
    return ReviewResultInput.model_validate(
        {
            "status": "passed",
            "model": "test-reviewer",
            "model_version": "test",
            "prompt_hash": "a" * 64,
            "reviewer_identity": "independent-reviewer",
            "reviewer_session_id": "review-session-01",
            "scores": {
                "factual_grounding": 4,
                "teaching_progression": 4,
                "interview_actionability": 4,
                "case_specificity": 4,
                "expression_novelty": 4,
            },
            "unit_evidence": [
                {"unit": "concept_explanation", "location": "第 1 节", "note": "已验证"},
                {"unit": "worked_case", "location": "第 2 节", "note": "已验证"},
            ],
        }
    )


def test_submit_batch_creates_all_snapshots_before_any_dispatch(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _write_chapter(workspace, 1)
    _write_chapter(workspace, 2)

    batch = submit_review_batch(workspace, dispatcher=FakeHostTaskDispatcher())

    assert batch is not None
    assert len(batch.tasks) == 2
    assert len({task.receipt.task_id for task in batch.tasks if task.receipt}) == 2
    for task in batch.tasks:
        snapshot = workspace.root / task.snapshot_directory / "chapter.md"
        assert snapshot.is_file()
        assert task.receipt is not None
        assert task.receipt.task_input_manifest_sha256 == task.task_input_manifest_sha256


def test_snapshot_failure_leaves_no_formal_partial_batch(tmp_path: Path, monkeypatch) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _write_chapter(workspace, 1)
    _write_chapter(workspace, 2)
    original_copy = __import__("shutil").copyfile

    def fail_on_second(source, destination, *args, **kwargs):
        if str(destination).endswith("chapter-02\\chapter.md"):
            raise OSError("injected snapshot failure")
        return original_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr("knowledge_product_workflow.review_batches.shutil.copyfile", fail_on_second)

    with pytest.raises(OSError, match="injected snapshot failure"):
        submit_review_batch(workspace, dispatcher=FakeHostTaskDispatcher())

    assert not list((workspace.root / "runs" / "review-batches").glob("review-batch-*"))


def test_repeated_submission_reuses_the_same_inflight_batch(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _write_chapter(workspace, 1)

    first = submit_review_batch(workspace, dispatcher=FakeHostTaskDispatcher())
    second = submit_review_batch(workspace, dispatcher=FakeHostTaskDispatcher())

    assert first is not None and second is not None
    assert first.batch_id == second.batch_id


def test_default_dispatcher_prepares_auditable_host_skill_review_batch(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _write_chapter(workspace, 1)

    batch = submit_review_batch(workspace)

    assert batch is not None
    assert batch.state.value == "review_tasks_pending"
    assert batch.review_mode is ReviewMode.HOST_SKILL
    assert batch.tasks[0].state is ReviewTaskState.PENDING
    assert (workspace.root / "runs" / "review-batches" / batch.batch_id / "manifest.json").is_file()
    assert [finding.code for finding in validate_review_batches(workspace)] == [
        "REVIEW_INDEPENDENCE_UNVERIFIED",
        "REVIEW_TASK_PENDING"
    ]
    submit_review_result(workspace, batch.batch_id, batch.tasks[0].task_token, _result_input())
    assert [finding.code for finding in validate_review_batches(workspace)] == [
        "REVIEW_INDEPENDENCE_UNVERIFIED"
    ]


def test_result_submission_cannot_handwrite_controller_binding_fields(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _write_chapter(workspace, 1)
    batch = submit_review_batch(workspace, dispatcher=FakeHostTaskDispatcher())
    assert batch is not None
    result_path = tmp_path / "review.json"
    payload = _result_input().model_dump(mode="json")
    payload["task_id"] = "forged"
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "submit-review-result",
            "--workspace",
            str(workspace.root),
            "--batch",
            batch.batch_id,
            "--task-token",
            batch.tasks[0].task_token,
            "--input",
            str(result_path),
        ],
    )

    assert result.exit_code == 1
    assert "不得手填控制面字段" in result.output


def test_fake_dispatcher_can_exercise_submission_but_never_production_validation(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _write_chapter(workspace, 1)
    batch = submit_review_batch(workspace, dispatcher=FakeHostTaskDispatcher())
    assert batch is not None

    output = submit_review_result(workspace, batch.batch_id, batch.tasks[0].task_token, _result_input())

    assert output.is_file()
    findings = validate_review_batches(workspace)
    assert [finding.code for finding in findings] == ["REVIEW_DISPATCH_NOT_PRODUCTION"]


def test_chapter_change_only_invalidates_the_changed_chapter_on_next_submission(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _write_chapter(workspace, 1)
    _write_chapter(workspace, 2)
    first = submit_review_batch(workspace, dispatcher=FakeHostTaskDispatcher())
    assert first is not None
    for task in first.tasks:
        submit_review_result(workspace, first.batch_id, task.task_token, _result_input())

    chapter = workspace.root / "chapters" / "chapter-02.md"
    chapter.write_text(chapter.read_text(encoding="utf-8") + "\n修订内容。\n", encoding="utf-8")
    findings = validate_review_batches(workspace)
    assert [finding.code for finding in findings].count("REVIEW_BATCH_SUPERSEDED") == 1

    second = submit_review_batch(workspace, dispatcher=FakeHostTaskDispatcher())

    assert second is not None
    assert [task.chapter_number for task in second.tasks] == [2]


def test_author_session_cannot_submit_its_own_review(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _write_chapter(workspace, 1)
    batch = submit_review_batch(workspace, dispatcher=FakeHostTaskDispatcher())
    assert batch is not None
    forbidden = _result_input().model_copy(
        update={"reviewer_session_id": batch.tasks[0].target.author_session_id}
    )

    with pytest.raises(ValueError, match="作者身份或作者会话"):
        submit_review_result(workspace, batch.batch_id, batch.tasks[0].task_token, forbidden)


def test_review_check_reports_new_batch_blocker(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _write_chapter(workspace, 1)

    result = CliRunner().invoke(app, ["review-check", "--workspace", str(workspace.root)])

    assert result.exit_code == 0, result.output
    report = json.loads((workspace.root / "runs" / "review-report.json").read_text(encoding="utf-8"))
    assert [finding["code"] for finding in report["findings"]] == ["REVIEW_BATCH_MISSING"]


def test_review_metadata_never_blocks_delivery_quality_validation(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(workspace, "专业教程", target_audience="工程师")
    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    brief.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="reviewer"), body
        ),
        encoding="utf-8",
    )
    blueprint = submit_test_blueprint(workspace, brief, titles=("基础判断",))
    metadata, body = parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))
    blueprint.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="reviewer"), body
        ),
        encoding="utf-8",
    )
    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    submit_test_chapter(workspace, tmp_path, 1)

    _, report = run_validation(workspace, set())

    assert report.publishable is True
    assert not any(finding.code.startswith("REVIEW_") for finding in report.findings)


def test_independent_review_policy_blocks_before_blueprint_when_no_real_review_task_exists(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(
        workspace,
        "专业教程",
        DetailConstraints(review_policy="independent_review_required"),
        target_audience="工程师",
    )
    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    brief.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="reviewer"), body
        ),
        encoding="utf-8",
    )
    with pytest.raises(HostCapabilityPreflightError, match="无法提供可验证的独立审稿"):
        submit_test_blueprint(workspace, brief, titles=("基础判断",))

    assert not (workspace.root / "planning" / "curriculum-blueprint.md").exists()


def test_low_level_assembly_ignores_missing_or_stale_review_metadata(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _write_chapter(workspace, 1)

    from knowledge_product_workflow.publishing import assemble_book

    assemble_book(workspace)
    assert (workspace.root / "delivery" / "book.md").is_file()
