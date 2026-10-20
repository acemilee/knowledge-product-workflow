"""v1.1.1 user-decision control-plane regression tests."""

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
from knowledge_product_workflow.runtime import ensure_orchestration, inspect_workflow
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.user_decisions import (
    UserDecisionStaleError,
    UserDecisionState,
    UserDecisionTarget,
    UserResponseDecision,
    record_user_decision,
    request_user_decision,
    require_approved_user_decision,
    user_decision_requirement,
)
from knowledge_product_workflow.workflows.authoring import (
    SampleApprovalRequiredError,
    approve_sample_chapter,
    compile_chapter_contracts,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint, submit_test_chapter


def _approve(path: Path) -> None:
    metadata, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    path.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="用户"),
            body,
        ),
        encoding="utf-8",
    )


def test_managed_brief_requires_a_current_presented_user_decision(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    ensure_orchestration(workspace)
    brief = create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))
    runner = CliRunner()

    blocked = runner.invoke(app, ["approve", str(brief), "--workspace", str(workspace.root)])
    assert blocked.exit_code == 1
    assert "尚未收到当前版本的用户确认" in blocked.output

    requested = runner.invoke(app, ["request-user-decision", "brief", "--workspace", str(workspace.root)])
    assert requested.exit_code == 0, requested.output
    recorded = runner.invoke(
        app,
        [
            "record-user-decision",
            "brief",
            "--workspace",
            str(workspace.root),
            "--decision",
            "approve",
            "--response",
            "继续课程设计",
        ],
    )
    assert recorded.exit_code == 0, recorded.output

    approved = runner.invoke(app, ["approve", str(brief), "--workspace", str(workspace.root)])
    assert approved.exit_code == 0, approved.output
    metadata, _ = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    assert metadata.status is ArtifactStatus.APPROVED
    decision = json.loads((workspace.root / "runs" / "interaction" / "brief.json").read_text(encoding="utf-8"))
    assert decision["state"] == "consumed"
    assert "继续课程设计" not in (workspace.root / "runs" / "interaction" / "brief.json").read_text(
        encoding="utf-8"
    )


def test_changed_artifact_invalidates_its_user_decision(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))
    request_user_decision(workspace, UserDecisionTarget.BRIEF)
    record_user_decision(
        workspace,
        UserDecisionTarget.BRIEF,
        UserResponseDecision.APPROVE,
        approved_by="用户",
        response="批准",
    )

    brief.write_text(brief.read_text(encoding="utf-8") + "\n补充边界说明。\n", encoding="utf-8")

    requirement = user_decision_requirement(workspace, UserDecisionTarget.BRIEF)
    assert requirement.state == "stale"
    with pytest.raises(UserDecisionStaleError, match="内容已更新"):
        require_approved_user_decision(workspace, UserDecisionTarget.BRIEF)


def test_brief_decision_also_freezes_v6_publication_design(tmp_path: Path) -> None:
    from knowledge_product_workflow.presentation import (
        PublicationUpdateInput,
        apply_publication_update,
        load_or_create_publication_profile,
        preview_publication_update,
    )

    workspace = Workspace.create(tmp_path, "design-decision")
    brief = create_product_brief(workspace, "主题", DetailConstraints(target_words=1000))
    original_brief = brief.read_bytes()
    load_or_create_publication_profile(workspace, language="zh-CN")
    request_user_decision(workspace, UserDecisionTarget.BRIEF)
    record_user_decision(workspace, UserDecisionTarget.BRIEF, UserResponseDecision.APPROVE,
                         approved_by="测试用户", response="批准当前橙色设计")
    preview_publication_update(workspace, PublicationUpdateInput(brand_color_intent="紫色基调", version_bump="none"), language="zh-CN")
    apply_publication_update(workspace, approved_by="测试用户")
    assert brief.read_bytes() == original_brief
    assert user_decision_requirement(workspace, UserDecisionTarget.BRIEF).state == "stale"


def test_managed_blueprint_requires_its_own_current_user_decision(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))
    _approve(brief)
    blueprint = submit_test_blueprint(workspace, brief, titles=("基础", "交付"))
    ensure_orchestration(workspace)
    runner = CliRunner()

    blocked = runner.invoke(app, ["approve", str(blueprint), "--workspace", str(workspace.root)])
    assert blocked.exit_code == 1
    assert "尚未收到当前版本的用户确认" in blocked.output

    request_user_decision(workspace, UserDecisionTarget.BLUEPRINT)
    record_user_decision(
        workspace,
        UserDecisionTarget.BLUEPRINT,
        UserResponseDecision.APPROVE,
        approved_by="用户",
        response="继续撰写样章",
    )
    approved = runner.invoke(app, ["approve", str(blueprint), "--workspace", str(workspace.root)])
    assert approved.exit_code == 0, approved.output
    assert user_decision_requirement(workspace, UserDecisionTarget.BLUEPRINT).state == "consumed"


def test_managed_sample_gate_requires_a_bound_continue_decision(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))
    _approve(brief)
    blueprint = submit_test_blueprint(workspace, brief, titles=("AI Agent", "生产交付"))
    _approve(blueprint)
    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    submit_test_chapter(workspace, tmp_path, 1)
    ensure_orchestration(workspace)

    with pytest.raises(SampleApprovalRequiredError, match="尚未收到当前版本的用户确认"):
        approve_sample_chapter(workspace, approved_by="用户")

    request_user_decision(workspace, UserDecisionTarget.SAMPLE)
    decision = record_user_decision(
        workspace,
        UserDecisionTarget.SAMPLE,
        UserResponseDecision.APPROVE,
        approved_by="用户",
        response="继续生成全书",
    )
    assert decision.state is UserDecisionState.APPROVED
    approve_sample_chapter(workspace, approved_by="用户")

    assert (workspace.root / "chapters" / "chapter-01-sample.approved").is_file()
    assert user_decision_requirement(workspace, UserDecisionTarget.SAMPLE).state == "consumed"


def test_default_status_hides_control_plane_but_json_preserves_it(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    ensure_orchestration(workspace)
    create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))
    runner = CliRunner()

    plain = runner.invoke(app, ["status", "--workspace", str(workspace.root)])
    assert plain.exit_code == 0, plain.output
    assert "当前进度：产品定位" in plain.output
    assert "请确认产品定位" in plain.output
    assert "当前步骤需要处理后才能继续" not in plain.output
    for forbidden in ("kp ", "approve", "runs/", "work_order", "SHA", "推荐 Skill"):
        assert forbidden not in plain.output

    structured = runner.invoke(app, ["status", "--workspace", str(workspace.root), "--json"])
    assert structured.exit_code == 0, structured.output
    payload = json.loads(structured.output)
    assert payload["user_decision"]["target"] == "brief"
    assert payload["user_decision"]["state"] == "request_required"
    assert payload["user_decision"]["artifact_path"] == "planning/product-brief.md"
    assert inspect_workflow(workspace).required_human_decision is not None


def test_default_help_hides_control_plane_commands_but_keeps_them_callable() -> None:
    runner = CliRunner()

    help_output = runner.invoke(app, ["--help"])
    assert help_output.exit_code == 0, help_output.output
    for user_command in ("init", "brief", "status", "doctor"):
        assert user_command in help_output.output
    for internal_command in (
        "request-user-decision",
        "record-user-decision",
        "orchestration-plan",
        "complete-orchestration-work-order",
        "approve-sample",
        "submit-review-batch",
    ):
        assert internal_command not in help_output.output

    internal_help = runner.invoke(app, ["request-user-decision", "--help"])
    assert internal_help.exit_code == 0, internal_help.output
    assert "内部：登记当前版本已展示" in internal_help.output


def test_legacy_adoption_imports_existing_approval_without_reprompting(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "legacy-guide")
    shutil.rmtree(workspace.root / "runs" / "orchestration")
    brief = create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))
    _approve(brief)

    ensure_orchestration(workspace)

    record = json.loads((workspace.root / "runs" / "interaction" / "brief.json").read_text(encoding="utf-8"))
    assert record["provenance"] == "legacy_import"
    assert record["state"] == "consumed"
    assert record["approved_by"] == "用户"


def test_brief_amendment_requires_a_separate_version_bound_user_decision(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(workspace, "AI Agent", DetailConstraints(target_words=1000))
    _approve(brief)
    ensure_orchestration(workspace)
    amendment = workspace.root / "runs" / "brief-amendment.json"
    amendment.write_text(
        json.dumps({"target_words": 1200, "change_reason": "增加练习深度"}),
        encoding="utf-8",
    )
    runner = CliRunner()

    preview = runner.invoke(app, ["amend-brief", "--workspace", str(workspace.root), "--from", str(amendment)])
    assert preview.exit_code == 0, preview.output
    blocked = runner.invoke(app, ["approve-brief-amendment", "--workspace", str(workspace.root)])
    assert blocked.exit_code == 1
    assert "尚未收到当前版本的用户确认" in blocked.output

    request_user_decision(workspace, UserDecisionTarget.BRIEF_AMENDMENT)
    record_user_decision(
        workspace,
        UserDecisionTarget.BRIEF_AMENDMENT,
        UserResponseDecision.APPROVE,
        approved_by="用户",
        response="按这个修改继续",
    )
    approved = runner.invoke(app, ["approve-brief-amendment", "--workspace", str(workspace.root)])
    assert approved.exit_code == 0, approved.output
    metadata, _ = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    assert metadata.version == 2
    assert metadata.status is ArtifactStatus.APPROVED
    decision = json.loads(
        (workspace.root / "runs" / "interaction" / "brief_amendment.json").read_text(encoding="utf-8")
    )
    assert decision["state"] == "consumed"
