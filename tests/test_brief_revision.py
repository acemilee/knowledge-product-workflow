"""Regression tests for recoverable Brief and curriculum revisions."""

import json
from pathlib import Path
from types import SimpleNamespace

import yaml
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.publishing import assemble_book, create_delivery_manifest
from knowledge_product_workflow.runtime import authorize_command, inspect_workflow
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
    require_current_chapter_contract,
)
from knowledge_product_workflow.workflows.curriculum import amend_curriculum_blueprint
from knowledge_product_workflow.workflows.product_brief import (
    brief_contract_fingerprints,
    create_product_brief,
)
from knowledge_product_workflow.workflows.validation import (
    run_delivery_validation,
    run_validation,
    write_quality_report,
)
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint, submit_test_chapter


def _approve(path: Path) -> None:
    metadata, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    path.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="用户"), body
        ),
        encoding="utf-8",
    )


def _approve_brief_amendment_decision(workspace: Workspace) -> None:
    request_user_decision(workspace, UserDecisionTarget.BRIEF_AMENDMENT)
    record_user_decision(
        workspace,
        UserDecisionTarget.BRIEF_AMENDMENT,
        UserResponseDecision.APPROVE,
        approved_by="用户",
        response="批准",
    )


def _approved_v2_blueprint(workspace: Workspace, constraints: DetailConstraints) -> Path:
    brief = create_product_brief(
        workspace,
        "Agent 产品教程",
        constraints,
        target_audience="希望构建 Agent 产品的开发者",
    )
    _approve(brief)
    blueprint = submit_test_blueprint(workspace, brief, titles=("基础",))
    _approve(blueprint)
    return brief


def test_delivery_amendment_preserves_current_blueprint_and_contracts(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = _approved_v2_blueprint(
        workspace, DetailConstraints(delivery_format="markdown", target_words=100)
    )
    contracts = compile_chapter_contracts(workspace)
    contract = contracts[0]
    previous_contract = contract.read_bytes()

    amendment_path = workspace.root / "runs" / "delivery-amendment.yaml"
    amendment_path.write_text("delivery_format: both\nchange_reason: 补充客户要求的 PDF 交付\n", encoding="utf-8")
    runner = CliRunner()
    preview = runner.invoke(app, ["amend-brief", "--workspace", str(workspace.root), "--from", str(amendment_path)])
    assert preview.exit_code == 0, preview.output
    _approve_brief_amendment_decision(workspace)
    approved = runner.invoke(app, ["approve-brief-amendment", "--workspace", str(workspace.root)])
    assert approved.exit_code == 0, approved.output

    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    assert metadata.version == 2
    assert "delivery_format: both" in body
    assert (workspace.root / "planning" / "history" / "product-brief.v1.md").is_file()
    assert inspect_workflow(workspace).stage == "sample_draft_missing"
    assert require_current_chapter_contract(workspace, 1)[0] == contract
    assert contract.read_bytes() == previous_contract

    status = runner.invoke(app, ["status", "--workspace", str(workspace.root), "--json"])
    payload = json.loads(status.output)
    assert payload["effective_constraints"]["delivery_format"] == "both"
    assert payload["effective_constraints"]["fingerprints"]["content_contract_sha256"]


def test_content_amendment_has_a_real_blueprint_recovery_command(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = _approved_v2_blueprint(workspace, DetailConstraints(target_words=100))
    compile_chapter_contracts(workspace)
    source = workspace.root / "runs" / "test-blueprint.yaml"
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)

    amendment_path = workspace.root / "runs" / "content-amendment.yaml"
    amendment_path.write_text("target_audience: 需要上线 Agent 产品的技术负责人\n", encoding="utf-8")
    runner = CliRunner()
    assert runner.invoke(
        app, ["amend-brief", "--workspace", str(workspace.root), "--from", str(amendment_path)]
    ).exit_code == 0
    _approve_brief_amendment_decision(workspace)
    assert runner.invoke(app, ["approve-brief-amendment", "--workspace", str(workspace.root)]).exit_code == 0

    stale = inspect_workflow(workspace)
    assert stale.stage == "blueprint_invalid"
    assert stale.next_actions[0].command == "amend-outline"
    assert stale.next_actions[0].reason_code == "BLUEPRINT_CONTENT_BINDING_STALE"
    assert "kp amend-outline" in stale.next_command

    payload["target_audience"] = "需要上线 Agent 产品的技术负责人"
    source.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    amended = amend_curriculum_blueprint(workspace, source)
    amended_metadata, amended_body = parse_markdown_artifact(amended.read_text(encoding="utf-8"))
    assert amended_metadata.status is ArtifactStatus.READY_FOR_REVIEW
    assert amended_metadata.version == 2
    assert "brief_content_sha256" in amended_body
    assert (workspace.root / "planning" / "history" / "curriculum-blueprint.v1.md").is_file()
    assert not (workspace.root / "chapters" / "chapter-01-contract.md").exists()
    assert (workspace.root / "chapters" / "history" / "chapter-01-contract.blueprint-v1.md").is_file()

    _approve(amended)
    contracts = compile_chapter_contracts(workspace)
    assert [path.name for path in contracts] == ["chapter-01-contract.md"]
    assert require_current_chapter_contract(workspace, 1)[0].is_file()
    _, current_body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    assert brief_contract_fingerprints(current_body)["content_contract_sha256"] in amended_body


def test_both_delivery_with_a_missing_pdf_routes_to_an_authorized_render(tmp_path: Path, monkeypatch) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _approved_v2_blueprint(workspace, DetailConstraints(delivery_format="both", target_words=100))
    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    submit_test_chapter(workspace, tmp_path, 1)
    (workspace.root / "chapters" / "chapter-01-sample.approved").write_text("approved_by: 用户\n", encoding="utf-8")
    write_quality_report(workspace, "quality", [])
    workspace.append_event("validation_completed", {"publishable": True})
    from knowledge_product_workflow.publishing import assemble_book

    assemble_book(workspace)

    from knowledge_product_workflow import runtime

    monkeypatch.setattr(
        runtime,
        "resolve_quarto",
        lambda: SimpleNamespace(available=True),
    )
    status = inspect_workflow(workspace)

    assert status.stage == "pdf_render_pending"
    assert [action.model_dump() for action in status.next_actions] == [
        {
            "command": "render-pdf",
            "argv": ["kp", "render-pdf", "--workspace", str(workspace.root)],
            "reason_code": "PDF_REQUIRED_AND_MISSING",
        }
    ]
    assert authorize_command(workspace, "render-pdf").stage == "pdf_render_pending"


def test_delivery_amendment_rebuilds_stale_input_graph_without_reopening_authoring(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _approved_v2_blueprint(workspace, DetailConstraints(delivery_format="markdown"))
    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    submit_test_chapter(workspace, tmp_path, 1)
    (workspace.root / "chapters" / "chapter-01-sample.approved").write_text(
        "approved_by: 用户\n", encoding="utf-8"
    )
    run_validation(workspace)
    markdown_path = assemble_book(workspace)
    run_delivery_validation(workspace)
    create_delivery_manifest(workspace, markdown_path, None)

    amendment_path = workspace.root / "runs" / "delivery-amendment.yaml"
    amendment_path.write_text("delivery_format: both\n", encoding="utf-8")
    runner = CliRunner()
    assert runner.invoke(
        app, ["amend-brief", "--workspace", str(workspace.root), "--from", str(amendment_path)]
    ).exit_code == 0
    _approve_brief_amendment_decision(workspace)
    assert runner.invoke(app, ["approve-brief-amendment", "--workspace", str(workspace.root)]).exit_code == 0

    stale = inspect_workflow(workspace)
    assert stale.stage == "assembly_pending"
    assert authorize_command(workspace, "assemble").stage == "assembly_pending"

    assemble_book(workspace)
    archive = list((workspace.root / "runs" / "delivery-history").glob("*"))
    assert len(archive) == 1
    assert {"delivery-manifest.yaml", "book-input-manifest.json"} <= {
        path.name for path in archive[0].iterdir()
    }

    from knowledge_product_workflow import runtime

    monkeypatch.setattr(runtime, "resolve_quarto", lambda: SimpleNamespace(available=True))
    pending_pdf = inspect_workflow(workspace)
    assert pending_pdf.stage == "pdf_render_pending"
    assert authorize_command(workspace, "render-pdf").stage == "pdf_render_pending"
