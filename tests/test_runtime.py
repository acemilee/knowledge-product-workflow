import json
from pathlib import Path

from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.review import checkpoint_chapter, create_review_work_order
from knowledge_product_workflow.runtime import (
    WorkflowStatus,
    authorize_command,
    create_work_order,
    inspect_workflow,
    route_workflow,
)
from knowledge_product_workflow.source_ledger import SourceLedgerEntry, append_source_entry
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workflows.validation import write_quality_report
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint, submit_test_chapter


def _approve(path: Path) -> None:
    metadata, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    approved = transition(metadata, ArtifactStatus.APPROVED, approved_by="editor@example.com")
    path.write_text(render_markdown_artifact(approved, body), encoding="utf-8")


def _approved_blueprint(workspace: Workspace) -> None:
    brief = create_product_brief(workspace, "AI Agent development")
    _approve(brief)
    blueprint = submit_test_blueprint(workspace, brief)
    _approve(blueprint)


def _write_passing_review(workspace: Workspace) -> None:
    target = checkpoint_chapter(workspace, 1, "chapter-writer", "author-session-01")
    work_order = create_review_work_order(workspace, 1)
    payload = {
        "review_schema_version": 2,
        "chapter_number": 1,
        "status": "passed",
        "model": "independent-reviewer",
        "model_version": "test",
        "prompt_hash": "a" * 64,
        "reviewed_chapter_sha256": target.chapter_sha256,
        "reviewed_contract_sha256": target.contract_sha256,
        "reviewed_chapter_version": target.chapter_version,
        "author_identity": target.author_identity,
        "author_session_id": target.author_session_id,
        "reviewer_identity": "quality-reviewer",
        "reviewer_session_id": "review-session-01",
        "independence_mode": "host_session_separated",
        "review_work_order_id": work_order.work_order_id,
        "reviewed_at": "2026-07-31T00:00:00Z",
        "scores": {
            "factual_grounding": 4,
            "teaching_progression": 4,
            "interview_actionability": 4,
            "case_specificity": 4,
            "expression_novelty": 4,
        },
        "unit_evidence": [],
        "issues": [],
        "revision_requests": [],
    }
    (workspace.root / "chapters" / "chapter-01-review.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_status_reads_real_artifact_gates_without_writing(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")

    initial = inspect_workflow(workspace)

    assert initial.stage == "brief_missing"
    assert initial.recommended_skill == "kp-product-strategist"
    assert "product-brief.md" in initial.blocked_by[0]
    assert not (workspace.root / "runs" / "work-orders").exists()

    brief = create_product_brief(workspace, "AI Agent development")
    awaiting_approval = inspect_workflow(workspace)

    assert awaiting_approval.stage == "brief_awaiting_approval"
    assert awaiting_approval.recommended_skill == "kp-create"
    assert "kp approve" in awaiting_approval.next_command

    _approve(brief)
    assert inspect_workflow(workspace).stage == "blueprint_missing"

    _approved_blueprint(Workspace.create(tmp_path, "second-guide"))
    approved_status = inspect_workflow(Workspace.open(tmp_path / "second-guide"))
    assert approved_status.stage == "chapter_contracts_incomplete"
    assert approved_status.recommended_skill == "kp-curriculum-architect"


def test_route_writes_scoped_work_order_and_event(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _approved_blueprint(workspace)

    decision = route_workflow(workspace)
    work_order_path = create_work_order(workspace, decision)
    payload = json.loads(work_order_path.read_text(encoding="utf-8"))

    assert decision.status.stage == "chapter_contracts_incomplete"
    assert work_order_path.parent == workspace.root / "runs" / "work-orders"
    assert payload["skill"] == "kp-curriculum-architect"
    assert payload["allowed_write_paths"] == ["planning/curriculum-blueprint.md", "chapters/"]
    assert "kp compile-contracts --all" in payload["acceptance_commands"][0]
    events = (workspace.root / "runs" / "events.jsonl").read_text(encoding="utf-8")
    assert "work_order_created" in events


def test_cli_status_json_is_chinese_and_route_can_create_work_order(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = Workspace.create(tmp_path, "guide")

    status = runner.invoke(app, ["status", "--workspace", str(workspace.root), "--json"])

    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["stage"] == "brief_missing"
    assert payload["recommended_skill"] == "kp-product-strategist"

    route = runner.invoke(
        app,
        ["route", "--workspace", str(workspace.root), "--write-work-order", "--json"],
    )

    assert route.exit_code == 0, route.output
    decision = json.loads(route.output)
    assert decision["work_order_path"].startswith("runs/work-orders/")
    assert "产品 Brief" in route.output

    human_status = runner.invoke(app, ["status", "--workspace", str(workspace.root)])
    assert human_status.exit_code == 0, human_status.output
    assert "当前阶段：缺少产品 Brief" in human_status.output
    assert "brief_missing" not in human_status.output


def test_markdown_only_delivery_does_not_route_to_pdf_rendering(tmp_path: Path, monkeypatch) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(
        workspace,
        "AI Agent development",
        DetailConstraints(target_words=1, delivery_format="markdown"),
    )
    _approve(brief)
    blueprint = submit_test_blueprint(workspace, brief, titles=("基础",))
    _approve(blueprint)
    from knowledge_product_workflow.workflows.authoring import (
        compile_chapter_contracts,
        create_chapter_draft,
    )

    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    submit_test_chapter(workspace, tmp_path, 1)
    (workspace.root / "chapters" / "chapter-01-sample.approved").write_text(
        "approved_by: editor@example.com\n", encoding="utf-8"
    )
    pending_review = inspect_workflow(workspace)
    assert pending_review.stage == "quality_validation_pending"
    assert pending_review.recommended_skill == "kp-quality-editor"
    assert "kp validate" in pending_review.next_command
    write_quality_report(workspace, "quality", [])
    workspace.append_event("validation_completed", {"publishable": True})
    from knowledge_product_workflow.publishing import assemble_book

    assemble_book(workspace)

    status = inspect_workflow(workspace)

    assert status.stage == "delivery_validation_pending"
    assert status.recommended_skill == "kp-quality-editor"
    assert "render-pdf" not in status.next_command


def test_quality_blocker_and_stale_report_both_allow_a_controlled_rerun(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(
        workspace,
        "AI Agent development",
        DetailConstraints(target_words=1, delivery_format="markdown"),
    )
    _approve(brief)
    blueprint = submit_test_blueprint(workspace, brief, titles=("基础",))
    _approve(blueprint)
    from knowledge_product_workflow.workflows.authoring import (
        compile_chapter_contracts,
        create_chapter_draft,
    )

    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    submit_test_chapter(workspace, tmp_path, 1)
    (workspace.root / "chapters" / "chapter-01-sample.approved").write_text(
        "approved_by: editor@example.com\n", encoding="utf-8"
    )
    write_quality_report(
        workspace,
        "quality",
        [
            QualityFinding(
                code="TEST_BLOCKER",
                severity=Severity.BLOCKER,
                location="chapters/chapter-01.md",
                message="测试 blocker",
            )
        ],
    )
    workspace.append_event("validation_completed", {"finding_count": 1, "publishable": False})

    blocked = inspect_workflow(workspace)

    assert blocked.stage == "quality_blocked"
    assert "kp validate" in blocked.next_command
    assert authorize_command(workspace, "validate").stage == "quality_blocked"

    append_source_entry(
        workspace.root / "research" / "source-ledger.jsonl",
        SourceLedgerEntry(
            source_id="new-source",
            url="https://example.com/new-source",
            accessed_at="2026-08-01T00:00:00Z",
            excerpt="用于让输入清单发生变化的有效来源。",
            claim_ids=[],
            license_note="测试来源",
        ),
    )

    stale = inspect_workflow(workspace)

    assert stale.stage == "quality_report_stale"
    assert "kp validate" in stale.next_command
    assert authorize_command(workspace, "validate").stage == "quality_report_stale"


def test_all_quality_recovery_stages_authorize_the_status_recommended_command(
    tmp_path: Path, monkeypatch
) -> None:
    from knowledge_product_workflow import runtime

    cases = (
        ("validate", "quality_validation_pending"),
        ("validate", "quality_report_invalid"),
        ("validate", "quality_report_stale"),
        ("validate", "quality_blocked"),
        ("validate-delivery", "delivery_validation_pending"),
        ("validate-delivery", "delivery_quality_report_invalid"),
        ("validate-delivery", "delivery_quality_report_stale"),
        ("validate-delivery", "delivery_quality_blocked"),
    )
    workspace = Workspace.create(tmp_path, "quality-recovery")
    for command, stage in cases:
        expected = WorkflowStatus(
            stage=stage,
            recommended_skill="kp-quality-editor",
            next_command=f"kp {command}",
        )
        monkeypatch.setattr(runtime, "inspect_workflow", lambda _workspace, status=expected: status)
        assert authorize_command(workspace, command) is expected
