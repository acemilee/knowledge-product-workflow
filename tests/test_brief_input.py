import json
from hashlib import sha256
from pathlib import Path

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.presentation import load_or_create_publication_profile
from knowledge_product_workflow.runtime import (
    OrchestrationWorkOrderError,
    inspect_workflow,
    read_orchestration_work_order,
    validate_orchestration_work_order,
)
from knowledge_product_workflow.workflows.product_brief import (
    parse_detail_constraints,
    parse_presentation_overrides,
)
from knowledge_product_workflow.workspace import Workspace


def _brief_path(workspace: Workspace) -> Path:
    return workspace.root / "planning" / "product-brief.md"


def test_interactive_brief_rejects_redirected_stdin_before_consuming_answers(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")

    result = CliRunner().invoke(
        app,
        ["brief", "Guide", "--workspace", str(workspace.root)],
        input="400\n专家\n两者\n",
    )

    assert result.exit_code == 2
    assert "只能在真实终端运行" in result.output
    assert not _brief_path(workspace).exists()
    assert not (workspace.root / "runs" / "brief-session.json").exists()


def test_structured_brief_writes_preview_then_requires_explicit_confirmation(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    runner = CliRunner()
    arguments = [
        "brief",
        "Guide",
        "--workspace",
        str(workspace.root),
        "--no-interactive",
        "--target-audience",
        "准备 AI 产品经理求职的理工科毕业生",
        "--target-words",
        "30000-50000",
        "--audience-level",
        "专家",
        "--delivery-format",
        "两者",
    ]

    preview = runner.invoke(app, arguments)

    assert preview.exit_code == 0, preview.output
    assert "待确认 Brief 预览" in preview.output
    assert not _brief_path(workspace).exists()
    preview_payload = json.loads((workspace.root / "runs" / "brief-preview.json").read_text(encoding="utf-8"))
    assert preview_payload["target_audience"] == "准备 AI 产品经理求职的理工科毕业生"
    assert preview_payload["detail_constraints"]["audience_level"] == "expert"
    assert preview_payload["detail_constraints"]["delivery_format"] == "both"
    assert preview_payload["brand_palette"]["accent"] == "B65E2B"
    assert "设计示意" in preview_payload["design_sample"]

    confirmed = runner.invoke(app, [*arguments, "--confirm"])

    assert confirmed.exit_code == 0, confirmed.output
    _, body = parse_markdown_artifact(_brief_path(workspace).read_text(encoding="utf-8"))
    constraints = parse_detail_constraints(body)
    assert constraints is not None
    assert (constraints.word_min, constraints.word_target, constraints.word_max) == (30000, 40000, 50000)
    assert constraints.audience_level.value == "expert"
    assert constraints.delivery_format.value == "both"
    assert parse_presentation_overrides(body).brand_color_intent == "橙色基调"


def test_explicit_purple_brand_is_bound_in_the_same_brief_confirmation(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    runner = CliRunner()
    args = [
        "brief", "紫色知识指南", "--workspace", str(workspace.root), "--no-interactive",
        "--target-audience", "工程师", "--audience-level", "进阶", "--brand-color", "紫色基调",
    ]
    preview = runner.invoke(app, args)
    assert preview.exit_code == 0, preview.output
    payload = json.loads((workspace.root / "runs" / "brief-preview.json").read_text(encoding="utf-8"))
    assert payload["brand_palette"]["accent"] == "6847A5"
    confirmed = runner.invoke(app, [*args, "--confirm"])
    assert confirmed.exit_code == 0, confirmed.output
    _, body = parse_markdown_artifact(_brief_path(workspace).read_text(encoding="utf-8"))
    overrides = parse_presentation_overrides(body)
    assert overrides is not None and overrides.brand_color_intent == "紫色基调"
    profile = load_or_create_publication_profile(workspace, language="zh-CN", overrides=overrides)
    assert profile.palette.accent == "6847A5"


def test_structured_json_accepts_chinese_values_and_rejects_unknown_fields(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    source = tmp_path / "brief-input.json"
    source.write_text(
        json.dumps(
            {
                "topic": "AI Agent 面试",
                "target_audience": "希望转型 Agent 开发的程序员",
                "target_pages": 120,
                "language": "英文",
                "layout_profile": "A4 标准版式",
                "audience_level": "专家",
                "delivery_format": "Markdown + PDF",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["brief", "--workspace", str(workspace.root), "--from", str(source), "--confirm"],
    )

    assert result.exit_code == 0, result.output
    _, body = parse_markdown_artifact(_brief_path(workspace).read_text(encoding="utf-8"))
    constraints = parse_detail_constraints(body)
    assert constraints is not None
    assert constraints.language == "en-US"
    assert constraints.delivery_format.value == "both"

    invalid = tmp_path / "invalid-brief.json"
    invalid.write_text('{"topic":"x","target_pages":1,"extra":"not allowed"}', encoding="utf-8")
    other_workspace = Workspace.create(tmp_path, "other")
    rejected = CliRunner().invoke(
        app,
        ["brief", "--workspace", str(other_workspace.root), "--from", str(invalid), "--confirm"],
    )
    assert rejected.exit_code == 2
    assert "输入无效" in rejected.output
    assert not _brief_path(other_workspace).exists()


def test_structured_brief_accepts_positional_topic_and_page_range(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    source = workspace.root / "runs" / "brief-input.json"
    source.write_text(
        json.dumps(
            {
                "target_audience": "希望系统准备 AI 岗位面试的开发者",
                "audience_level": "专家",
                "target_pages": "10-20",
                "delivery_format": "Markdown + PDF",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "brief",
            "AI Agent 面试指南",
            "--workspace",
            str(workspace.root),
            "--from",
            str(source.resolve()),
            "--confirm",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "10-20 页" in result.output
    _, body = parse_markdown_artifact(_brief_path(workspace).read_text(encoding="utf-8"))
    constraints = parse_detail_constraints(body)
    assert constraints is not None
    assert (constraints.page_min, constraints.page_target, constraints.page_max) == (10, 15, 20)


def test_structured_brief_rejects_conflicting_command_and_file_topics(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    source = workspace.root / "runs" / "brief-input.json"
    source.write_text(
        json.dumps(
            {
                "topic": "文件主题",
                "target_audience": "目标读者",
                "audience_level": "进阶",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "brief",
            "命令主题",
            "--workspace",
            str(workspace.root),
            "--from",
            str(source.resolve()),
            "--confirm",
        ],
    )

    assert result.exit_code == 2
    assert "topic 不一致" in result.output


def test_interactive_brief_persists_current_field_and_resumes_after_cancel(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    monkeypatch.setattr("knowledge_product_workflow.cli._interactive_stdin_isatty", lambda: True)
    runner = CliRunner()

    interrupted = runner.invoke(
        app,
        ["brief", "Guide", "--workspace", str(workspace.root)],
        input="\n准备求职的应届毕业生\n",
    )

    assert interrupted.exit_code != 0
    assert not _brief_path(workspace).exists()
    session = json.loads((workspace.root / "runs" / "brief-session.json").read_text(encoding="utf-8"))
    assert session["current_field"] == "audience_level"
    assert session["values"]["target_audience"] == "准备求职的应届毕业生"

    resumed = runner.invoke(
        app,
        ["brief", "Guide", "--workspace", str(workspace.root)],
        input="专家\n是\n",
    )

    assert resumed.exit_code == 0, resumed.output
    assert _brief_path(workspace).exists()
    assert not (workspace.root / "runs" / "brief-session.json").exists()


def test_structured_brief_accepts_optional_length_constraints(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    result = CliRunner().invoke(
        app,
        [
            "brief",
            "Guide",
            "--workspace",
            str(workspace.root),
            "--no-interactive",
            "--target-audience",
            "需要系统准备面试的工程师",
            "--target-pages",
            "80",
            "--audience-level",
            "专家",
            "--confirm",
        ],
    )

    assert result.exit_code == 0, result.output
    _, body = parse_markdown_artifact(_brief_path(workspace).read_text(encoding="utf-8"))
    constraints = parse_detail_constraints(body)
    assert constraints is not None
    assert constraints.target_pages == 80
    assert constraints.word_budget is None


def test_structured_brief_requires_only_audience_and_level_not_length(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    runner = CliRunner()
    arguments = [
        "brief",
        "Guide",
        "--workspace",
        str(workspace.root),
        "--no-interactive",
        "--target-audience",
        "准备转型 AI 产品经理的程序员",
        "--audience-level",
        "进阶",
    ]

    result = runner.invoke(app, [*arguments, "--confirm"])

    assert result.exit_code == 0, result.output
    _, body = parse_markdown_artifact(_brief_path(workspace).read_text(encoding="utf-8"))
    constraints = parse_detail_constraints(body)
    assert constraints is not None
    assert constraints.target_pages is None
    assert constraints.word_budget is None
    assert "准备转型 AI 产品经理的程序员" in body


def test_structured_brief_rejects_missing_required_positioning(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["brief", "Guide", "--workspace", str(workspace.root), "--no-interactive"],
    )

    assert result.exit_code == 2
    assert "目标受众" in result.output
    assert not _brief_path(workspace).exists()


def test_confirm_replaces_an_unapproved_brief_draft_without_requiring_a_new_workspace(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    runner = CliRunner()
    base_arguments = [
        "brief",
        "Guide",
        "--workspace",
        str(workspace.root),
        "--no-interactive",
        "--target-audience",
        "准备转型 AI 产品经理的程序员",
        "--audience-level",
        "专家",
        "--delivery-format",
        "两者",
    ]

    first = runner.invoke(app, [*base_arguments, "--target-pages", "10-15", "--confirm"])
    assert first.exit_code == 0, first.output
    initial = inspect_workflow(workspace)
    assert initial.current_work_order is not None
    original_order = read_orchestration_work_order(workspace, initial.current_work_order)
    assert original_order.stage == "brief_awaiting_approval"
    assert original_order.lifecycle.value == "pending"

    corrected = runner.invoke(app, [*base_arguments, "--target-pages", "10+", "--confirm"])
    assert corrected.exit_code == 0, corrected.output
    assert "FileExistsError" not in corrected.output
    assert "已更新待确认产品 Brief" in corrected.output

    metadata, body = parse_markdown_artifact(_brief_path(workspace).read_text(encoding="utf-8"))
    constraints = parse_detail_constraints(body)
    assert metadata.version == 2
    assert constraints is not None
    assert constraints.page_constraint is not None
    assert constraints.page_constraint.mode.value == "minimum"
    assert constraints.page_constraint.minimum == 10
    assert constraints.target_pages is None
    assert len(list((workspace.root / "planning" / "draft-history").glob("product-brief.v1-*.md"))) == 1

    orders = [
        read_orchestration_work_order(workspace, path)
        for path in (workspace.root / "runs" / "orchestration" / "work-orders").glob("*.json")
    ]
    superseded = [
        order
        for order in orders
        if order.stage == "brief_awaiting_approval" and order.lifecycle.value == "superseded"
    ]
    assert original_order.work_order_id in {order.work_order_id for order in superseded}
    for order in superseded:
        assert order.completion_proof is not None
        assert order.completion_proof.transition == "superseded"
        assert order.completion_proof.reason
        assert order.completion_proof.input_manifest_sha256 == order.input_manifest_sha256
    retired = read_orchestration_work_order(workspace, original_order.work_order_id)
    assert retired.input_manifest_sha256 == original_order.input_manifest_sha256
    with pytest.raises(OrchestrationWorkOrderError, match="已过期|已完成|已取消"):
        validate_orchestration_work_order(workspace, original_order.work_order_id, command="approve")
    current = inspect_workflow(workspace)
    assert current.current_work_order is not None
    replacement = read_orchestration_work_order(workspace, current.current_work_order)
    assert replacement.stage == "brief_awaiting_approval"
    assert replacement.lifecycle.value == "pending"
    live = [order for order in orders if order.lifecycle.value in {"pending", "active"}]
    assert [order.work_order_id for order in live] == [replacement.work_order_id]
    bound_inputs = {entry.path: entry.sha256 for entry in replacement.inputs}
    for relative_path in ("planning/product-brief.md", "runs/request-contract.yaml"):
        assert bound_inputs[relative_path] == sha256((workspace.root / relative_path).read_bytes()).hexdigest()
    validated = validate_orchestration_work_order(workspace, replacement.work_order_id, command="approve")
    assert validated.work_order_id == replacement.work_order_id
