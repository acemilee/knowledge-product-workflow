"""v1.1 orchestration control-plane specifications."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.runtime import (
    ChapterLeaseConflictError,
    HostCapabilityPreflightError,
    MacroPhase,
    OrchestrationWorkOrderError,
    WorkOrderInputChangedError,
    acquire_chapter_lease,
    cancel_orchestration_work_order,
    capture_orchestration_action,
    complete_orchestration_action,
    complete_orchestration_work_order,
    create_orchestration_work_order,
    ensure_orchestration,
    fail_orchestration_action,
    inspect_workflow,
    preflight_independent_review,
    read_orchestration_work_order,
    read_run_intent,
    reconcile_orchestration,
    require_delivery_orchestration_ready,
    start_run_intent,
)
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def _approve(path: Path) -> None:
    metadata, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    path.write_text(
        render_markdown_artifact(transition(metadata, ArtifactStatus.APPROVED, approved_by="用户"), body),
        encoding="utf-8",
    )


def test_orchestration_work_order_is_idempotent_snapshot_bound_and_visible(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")

    first = create_orchestration_work_order(workspace)
    second = create_orchestration_work_order(workspace)
    payload = json.loads(first.read_text(encoding="utf-8"))
    status = inspect_workflow(workspace)

    assert first == second
    assert first.parent == workspace.root / "runs" / "orchestration" / "work-orders"
    assert payload["macro_phase"] == MacroPhase.DEFINITION.value
    assert payload["input_manifest_sha256"]
    assert payload["allowed_commands"]
    assert payload["idempotency_key"]
    assert status.macro_phase is MacroPhase.DEFINITION
    assert status.current_work_order == str(first.relative_to(workspace.root)).replace("\\", "/")
    assert status.user_summary


def test_work_order_refuses_completion_after_an_input_snapshot_changes(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    ensure_orchestration(workspace)
    brief = create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))
    path = create_orchestration_work_order(workspace)
    order = read_orchestration_work_order(workspace, path)

    brief.write_text(brief.read_text(encoding="utf-8") + "\n补充说明。\n", encoding="utf-8")

    with pytest.raises(WorkOrderInputChangedError, match="输入工件已变化"):
        complete_orchestration_work_order(workspace, order.work_order_id, "brief-preview-created")


def test_chapter_lease_conflict_is_fail_closed(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    ensure_orchestration(workspace)
    brief = create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))
    _approve(brief)

    first_path = create_orchestration_work_order(workspace)
    first = read_orchestration_work_order(workspace, first_path)
    assert first.target_chapter_number is None

    # A chapter-scoped lease is controller-owned and cannot be silently replaced.
    lease = acquire_chapter_lease(workspace, "test-writer-a", 1, owner_skill="kp-chapter-writer")
    assert lease.chapter_number == 1
    with pytest.raises(ChapterLeaseConflictError, match="占用"):
        acquire_chapter_lease(workspace, "test-writer-b", 1, owner_skill="kp-chapter-writer")


def test_independent_review_capability_is_checked_before_brief_approval(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(
        workspace,
        "AI Agent",
        DetailConstraints(review_policy="independent_review_required"),
    )

    with pytest.raises(HostCapabilityPreflightError, match=r"真实.*子任务"):
        preflight_independent_review(workspace, platform="test-host")

    snapshot = preflight_independent_review(
        workspace,
        platform="test-host",
        supports_real_subtasks=True,
        production_verified=True,
        max_parallelism=2,
    )
    assert snapshot.review_policy == "independent_review_required"
    assert snapshot.capabilities.production_verified is True
    assert brief.is_file()


def test_true_legacy_workspace_is_only_migrated_when_orchestration_is_requested(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "legacy-guide")
    shutil.rmtree(workspace.root / "runs" / "orchestration")
    create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))

    assert not (workspace.root / "runs" / "orchestration").exists()
    assert inspect_workflow(workspace).orchestration_mode == "legacy"

    with pytest.raises(OrchestrationWorkOrderError, match="旧工作区"):
        create_orchestration_work_order(workspace)

    create_orchestration_work_order(workspace, adopt_legacy=True)

    assert (workspace.root / "runs" / "orchestration" / "state.json").is_file()
    assert inspect_workflow(workspace).orchestration_mode == "managed"


def test_completed_work_order_accepts_an_expected_stage_transition(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    path = create_orchestration_work_order(workspace)
    order = read_orchestration_work_order(workspace, path)

    create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))
    complete_orchestration_work_order(workspace, order.work_order_id, "已创建 Brief 预览")

    completed = read_orchestration_work_order(workspace, path)
    assert completed.lifecycle.value == "completed"
    assert completed.completion_proof is not None


def test_auto_delivery_intent_stops_only_at_the_next_real_user_decision(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    initial = create_orchestration_work_order(workspace)
    order = read_orchestration_work_order(workspace, initial)
    start_run_intent(workspace)
    create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))

    complete_orchestration_work_order(workspace, order.work_order_id, "Brief 预览已创建")

    intent = read_run_intent(workspace)
    assert intent is not None
    assert intent.state.value == "waiting_for_decision"
    assert intent.stop_reason == "等待用户的产品级决定"
    assert intent.consecutive_no_progress == 0


def test_captured_action_closes_the_original_handoff_after_the_stage_changes(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    initial = create_orchestration_work_order(workspace)
    captured = capture_orchestration_action(workspace, "brief")
    assert captured == read_orchestration_work_order(workspace, initial).work_order_id

    brief = create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))
    completed_path = complete_orchestration_action(
        workspace,
        "brief",
        proof="已物化待确认产品 Brief",
        output_paths=[brief],
        work_order_id=captured,
    )

    assert completed_path == initial
    completed = read_orchestration_work_order(workspace, initial)
    assert completed.lifecycle.value == "completed"
    assert completed.completion_proof is not None
    assert inspect_workflow(workspace).current_work_order is not None


def test_cli_actions_close_the_current_handoff_and_create_one_successor(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    runner = CliRunner()

    started = runner.invoke(app, ["start-run-intent", "--workspace", str(workspace.root)])
    assert started.exit_code == 0, started.output
    created = runner.invoke(
        app,
        [
            "brief",
            "AI Agent",
            "--workspace",
            str(workspace.root),
            "--no-interactive",
            "--target-audience",
            "希望转型 Agent 开发的工程师",
            "--audience-level",
            "专家",
            "--confirm",
        ],
    )
    assert created.exit_code == 0, created.output

    from knowledge_product_workflow.user_decisions import (
        UserDecisionTarget,
        UserResponseDecision,
        record_user_decision,
        request_user_decision,
    )

    request_user_decision(workspace, UserDecisionTarget.BRIEF)
    record_user_decision(
        workspace,
        UserDecisionTarget.BRIEF,
        UserResponseDecision.APPROVE,
        approved_by="用户",
        response="批准",
    )
    approved = runner.invoke(
        app,
        ["approve", str(workspace.root / "planning" / "product-brief.md"), "--workspace", str(workspace.root)],
    )
    assert approved.exit_code == 0, approved.output

    resumed = read_run_intent(workspace)
    assert resumed is not None
    assert resumed.state.value == "active"
    assert resumed.stop_reason is None

    orders = [order for _, order in sorted(
        (path, read_orchestration_work_order(workspace, path))
        for path in (workspace.root / "runs" / "orchestration" / "work-orders").glob("*.json")
    )]
    assert [order.lifecycle.value for order in orders].count("completed") == 2
    assert all(order.completion_proof is not None for order in orders if order.lifecycle.value == "completed")
    assert all(
        order.completion_proof.event_id
        and order.completion_proof.input_manifest_sha256
        for order in orders
        if order.lifecycle.value == "completed"
    )
    reconciliation = reconcile_orchestration(workspace, allowed_current_action="submit-outline")
    assert reconciliation.blockers == []
    assert reconciliation.recoverable == []


def test_failed_action_is_terminal_and_creates_a_distinct_retry_handoff(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    initial = create_orchestration_work_order(workspace)
    original = read_orchestration_work_order(workspace, initial)

    failed_path = fail_orchestration_action(workspace, "brief", "结构化输入缺少目标读者")

    assert failed_path == initial
    failed = read_orchestration_work_order(workspace, initial)
    assert failed.lifecycle.value == "failed"
    assert failed.completion_proof is not None
    assert failed.completion_proof.reason == "结构化输入缺少目标读者"
    retry_path = create_orchestration_work_order(workspace)
    retry = read_orchestration_work_order(workspace, retry_path)
    assert retry.work_order_id != original.work_order_id
    assert retry.lifecycle.value == "pending"


@pytest.mark.parametrize(
    "stage",
    (
        "delivery_quality_report_invalid",
        "delivery_quality_report_stale",
        "delivery_quality_blocked",
    ),
)
def test_managed_delivery_quality_recovery_work_order_authorizes_revalidation(
    tmp_path: Path, monkeypatch, stage: str
) -> None:
    """A live recovery handoff must not block the only command that can resolve it."""
    from knowledge_product_workflow import runtime

    workspace = Workspace.create(tmp_path, "delivery-recovery")
    initial = next((workspace.root / "runs" / "orchestration" / "work-orders").glob("*.json"))
    initial_order = read_orchestration_work_order(workspace, initial)
    cancel_orchestration_work_order(workspace, initial_order.work_order_id, "测试切换至交付恢复阶段")
    start_run_intent(workspace)
    status = runtime.WorkflowStatus(
        stage=stage,
        recommended_skill="kp-quality-editor",
        next_command="kp validate-delivery",
        macro_phase=MacroPhase.DELIVERY,
    )
    monkeypatch.setattr(runtime, "inspect_workflow", lambda _workspace: status)

    path = create_orchestration_work_order(workspace)
    order = read_orchestration_work_order(workspace, path)

    assert order.lifecycle.value == "pending"
    assert order.allowed_commands == ["validate-delivery"]
    require_delivery_orchestration_ready(workspace, "validate-delivery")
    reconciliation = reconcile_orchestration(workspace, allowed_current_action="validate-delivery")
    assert reconciliation.blockers == []
    assert reconciliation.recoverable == []


def test_cli_requires_explicit_legacy_adoption(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = Workspace.create(tmp_path, "legacy-guide")
    shutil.rmtree(workspace.root / "runs" / "orchestration")
    create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))

    preview = runner.invoke(app, ["orchestration-migration-preview", "--workspace", str(workspace.root), "--json"])
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.output)["requires_confirmation"] is True

    rejected = runner.invoke(app, ["orchestration-plan", "--workspace", str(workspace.root)])
    assert rejected.exit_code == 1
    assert "旧工作区" in rejected.output

    adopted = runner.invoke(
        app,
        ["orchestration-plan", "--workspace", str(workspace.root), "--adopt-legacy", "--json"],
    )
    assert adopted.exit_code == 0, adopted.output
    assert json.loads(adopted.output)["status"]["orchestration_mode"] == "managed"
