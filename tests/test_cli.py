import json

from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.user_decisions import (
    UserDecisionTarget,
    UserResponseDecision,
    record_user_decision,
    request_user_decision,
)
from knowledge_product_workflow.workflows.authoring import (
    compile_chapter_contracts,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.product_brief import (
    create_product_brief,
    parse_presentation_overrides,
)
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint


def _approve_brief_decision(workspace: Workspace, identity: str = "用户") -> None:
    request_user_decision(workspace, UserDecisionTarget.BRIEF)
    record_user_decision(
        workspace,
        UserDecisionTarget.BRIEF,
        UserResponseDecision.APPROVE,
        approved_by=identity,
        response="批准",
    )


def test_help_exposes_the_kp_command() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "知识产品工作流" in result.stdout


def test_init_creates_a_workspace(tmp_path) -> None:
    result = CliRunner().invoke(app, ["init", "ai-agent-foundations", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert (tmp_path / "ai-agent-foundations" / "runs" / "events.jsonl").exists()
    assert "environment_diagnosed" in (
        tmp_path / "ai-agent-foundations" / "runs" / "events.jsonl"
    ).read_text(encoding="utf-8")


def test_brief_approval_and_outline_template_run_through_the_cli(tmp_path) -> None:
    runner = CliRunner()
    workspace_path = tmp_path / "ai-agent-foundations"

    assert runner.invoke(app, ["init", "ai-agent-foundations", "--root", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(
            app,
            [
                "brief",
                "AI Agent development",
                "--workspace",
                str(workspace_path),
                "--no-interactive",
                "--target-audience",
                "准备从事 AI Agent 开发的工程师",
                "--audience-level",
                "进阶",
                "--target-words",
                "1",
                "--confirm",
            ],
        ).exit_code
        == 0
    )

    brief_path = workspace_path / "planning" / "product-brief.md"
    _approve_brief_decision(Workspace.open(workspace_path))
    approval = runner.invoke(app, ["approve", str(brief_path), "--workspace", str(workspace_path)])
    assert approval.exit_code == 0
    metadata, _ = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    assert metadata.approved_by == "用户"

    outline = runner.invoke(app, ["outline", "--workspace", str(workspace_path)])
    assert outline.exit_code == 0
    template = workspace_path / "planning" / "curriculum-blueprint.template.yaml"
    assert template.is_file()
    assert "<主题化章节标题>" in template.read_text(encoding="utf-8")
    assert not (workspace_path / "planning" / "curriculum-blueprint.md").exists()


def test_explicit_approval_identity_overrides_the_workspace_default(tmp_path) -> None:
    runner = CliRunner()
    workspace_path = tmp_path / "guide"
    assert runner.invoke(app, ["init", "guide", "--root", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(
            app,
            [
                "brief",
                "Guide",
                "--workspace",
                str(workspace_path),
                "--no-interactive",
                "--target-audience",
                "需要系统学习知识产品工作流的工程师",
                "--audience-level",
                "进阶",
                "--target-words",
                "1",
                "--confirm",
            ],
        ).exit_code
        == 0
    )

    brief_path = workspace_path / "planning" / "product-brief.md"
    _approve_brief_decision(Workspace.open(workspace_path), "内容负责人")
    result = runner.invoke(
        app,
        ["approve", str(brief_path), "--workspace", str(workspace_path), "--by", "内容负责人"],
    )

    assert result.exit_code == 0, result.output
    metadata, _ = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    assert metadata.approved_by == "内容负责人"
    assert Workspace.open(workspace_path).approval_identity() == "用户"


def test_brief_accepts_optional_publication_metadata_without_expanding_required_inputs(tmp_path) -> None:
    runner = CliRunner()
    workspace_path = tmp_path / "guide"
    assert runner.invoke(app, ["init", "guide", "--root", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "brief",
            "AI Agent 工程实践",
            "--workspace",
            str(workspace_path),
            "--no-interactive",
            "--target-audience",
            "希望转型 AI Agent 开发的程序员",
            "--audience-level",
            "进阶",
            "--subtitle",
            "从原型到稳定交付",
            "--edition",
            "第二版",
            "--publication-date",
            "2026-08-01",
            "--publisher-label",
            "示例出版社",
            "--copyright-line",
            "版权所有 2026 示例出版社",
            "--confirm",
        ],
    )

    assert result.exit_code == 0, result.output
    _, body = parse_markdown_artifact(
        (workspace_path / "planning" / "product-brief.md").read_text(encoding="utf-8")
    )
    presentation = parse_presentation_overrides(body)
    assert presentation is not None
    assert presentation.subtitle == "从原型到稳定交付"
    assert presentation.edition == "第二版"
    assert presentation.publication_date.isoformat() == "2026-08-01"


def test_research_registers_claim_evidence_through_the_cli(tmp_path) -> None:
    runner = CliRunner()
    workspace = Workspace.create(tmp_path, "ai-agent-foundations")
    brief = create_product_brief(workspace, "Agent 教程", target_audience="工程师")
    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    brief.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="reviewer"), body
        ),
        encoding="utf-8",
    )
    blueprint = submit_test_blueprint(
        workspace, brief, titles=("基础",), claim_ids=(("claim-001",),)
    )
    metadata, body = parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))
    blueprint.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="reviewer"), body
        ),
        encoding="utf-8",
    )
    compile_chapter_contracts(workspace)
    workspace_path = workspace.root

    result = runner.invoke(
        app,
        [
            "research",
            "official-agent-docs",
            "https://example.com/agent-docs",
            "--workspace",
            str(workspace_path),
            "--claim",
            "claim-001",
            "--excerpt",
            "An agent combines a model, instructions, and tools.",
            "--license-note",
            "Fixture source only",
        ],
    )

    assert result.exit_code == 0
    assert "official-agent-docs" in (workspace_path / "research" / "source-ledger.jsonl").read_text(
        encoding="utf-8"
    )


def test_doctor_reports_local_pdf_prerequisites_as_json() -> None:
    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert {"kp", "quarto", "xelatex", "microsoft_yahei"} <= payload.keys()
    assert all("available" in check for check in payload.values())

    human_result = CliRunner().invoke(app, ["doctor"])
    assert human_result.exit_code == 0, human_result.output
    assert "D2 图表工具" in human_result.output


def test_public_contract_commands_expose_semantic_templates_without_hashes(tmp_path) -> None:
    runner = CliRunner()
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(workspace, "Agent 教程", target_audience="工程师")
    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    brief.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="reviewer"), body
        ),
        encoding="utf-8",
    )
    blueprint = submit_test_blueprint(workspace, brief, titles=("基础",))
    metadata, body = parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))
    blueprint.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="reviewer"), body
        ),
        encoding="utf-8",
    )
    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    source = workspace.root / "runs" / "chapter.md"
    source.write_text("# 1. 基础\n\n## 决策框架\n\n这是足够长的章节正文，用于生成当前版本的模板。\n", encoding="utf-8")

    chapter_schema = runner.invoke(app, ["chapter-compliance-schema"])
    diagram_schema = runner.invoke(app, ["diagram-schema"])
    inserted = runner.invoke(
        app,
        ["insert-diagram-reference", "1", "agent-layers", "--workspace", str(workspace.root), "--from", str(source)],
    )
    diagram_template = runner.invoke(
        app,
        [
            "diagram-template",
            "1",
            "--workspace",
            str(workspace.root),
            "--chapter-source",
            str(source),
            "--diagram-id",
            "agent-layers",
        ],
    )
    compliance_template = runner.invoke(
        app,
        ["chapter-compliance-template", "1", "--workspace", str(workspace.root), "--from", str(source)],
    )

    assert chapter_schema.exit_code == diagram_schema.exit_code == inserted.exit_code == 0
    assert diagram_template.exit_code == compliance_template.exit_code == 0
    assert "chapter_body_sha256" not in json.loads(chapter_schema.output)["fields"]
    assert "chapter_body_sha256" not in json.loads(diagram_schema.output)["fields"]
    assert (workspace.root / "runs" / "templates" / "chapter-01-compliance.template.json").is_file()
    assert (workspace.root / "runs" / "templates" / "chapter-01-agent-layers.diagram.template.json").is_file()


def test_network_timeout_event_is_redacted_to_category_and_duration(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    result = CliRunner().invoke(
        app,
        [
            "record-network-timeout",
            "--workspace",
            str(workspace.root),
            "--endpoint-category",
            "official-documentation",
            "--timeout-seconds",
            "8",
        ],
    )

    assert result.exit_code == 0, result.output
    events = [
        json.loads(line)
        for line in (workspace.root / "runs" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    timeout_event = events[-1]
    assert timeout_event["event_type"] == "network_timeout"
    assert timeout_event["payload"] == {
        "endpoint_category": "official-documentation",
        "timeout_seconds": 8,
    }


def test_assumption_command_writes_an_append_only_internal_ledger(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    result = CliRunner().invoke(
        app,
        [
            "assumption",
            "interview-weight",
            "--workspace",
            str(workspace.root),
            "--statement",
            "系统设计在目标岗位中占较高考核权重。",
            "--impact",
            "curriculum_weight",
            "--disposition",
            "evidence_needed",
            "--rationale",
            "决定章节权重。",
        ],
    )

    assert result.exit_code == 0, result.output
    ledger = (workspace.root / "research" / "assumption-ledger.jsonl").read_text(encoding="utf-8")
    assert "interview-weight" in ledger
    assert "assumption_recorded" in (workspace.root / "runs" / "events.jsonl").read_text(
        encoding="utf-8"
    )
