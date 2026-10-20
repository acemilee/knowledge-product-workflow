from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge_product_workflow.claims import current_claim_contract_bindings
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.publishing import (
    assemble_book,
    create_delivery_manifest,
    delivery_manifest_issues,
)
from knowledge_product_workflow.review_batches import (
    HostCapabilities,
    ReviewBatch,
    ReviewResultInput,
    ReviewTask,
    TaskReceipt,
    submit_review_batch,
    submit_review_result,
)
from knowledge_product_workflow.source_ledger import SourceLedgerEntry, append_source_entry
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.user_decisions import (
    UserDecisionTarget,
    UserResponseDecision,
    record_user_decision,
    request_user_decision,
)
from knowledge_product_workflow.workflows.authoring import (
    approve_sample_chapter,
    compile_chapter_contracts,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workflows.validation import run_delivery_validation, run_validation
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint, submit_test_chapter


def _approve(path: Path) -> None:
    metadata, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    approved = transition(metadata, ArtifactStatus.APPROVED, approved_by="editor@example.com")
    path.write_text(render_markdown_artifact(approved, body), encoding="utf-8")


def _approve_sample_decision(workspace: Workspace) -> None:
    request_user_decision(workspace, UserDecisionTarget.SAMPLE)
    record_user_decision(
        workspace,
        UserDecisionTarget.SAMPLE,
        UserResponseDecision.APPROVE,
        approved_by="editor@example.com",
        response="批准",
    )


class _VerifiedTestDispatcher:
    """Only an in-memory integration double; production discovery never returns it."""

    kind = "verified-test"

    def probe(self) -> HostCapabilities:
        return HostCapabilities(
            platform="integration-test",
            version="test",
            supports_real_subtasks=True,
            production_verified=True,
            max_parallelism=8,
            reason="受控集成测试。",
        )

    def dispatch(self, batch: ReviewBatch, task: ReviewTask) -> TaskReceipt:
        return TaskReceipt(
            task_id=f"test-task-{task.chapter_number}",
            parent_task_id="test-parent",
            dispatched_at=datetime.now(UTC),
            task_input_manifest_sha256=task.task_input_manifest_sha256,
        )

    def cancel(self, task_id: str) -> None:
        return None


def _write_passing_reviews(workspace: Workspace) -> None:
    batch = submit_review_batch(workspace, dispatcher=_VerifiedTestDispatcher())
    assert batch is not None
    for task in batch.tasks:
        submit_review_result(
            workspace,
            batch.batch_id,
            task.task_token,
            ReviewResultInput.model_validate(
                {
                    "status": "passed",
                    "model": "independent-reviewer",
                    "model_version": "test",
                    "prompt_hash": "a" * 64,
                    "reviewer_identity": f"quality-reviewer-{task.chapter_number}",
                    "reviewer_session_id": f"review-session-{task.chapter_number}",
                    "scores": {
                        "factual_grounding": 4,
                        "teaching_progression": 4,
                        "interview_actionability": 4,
                        "case_specificity": 4,
                        "expression_novelty": 4,
                    },
                    "unit_evidence": [],
                }
            ),
        )


def test_ai_agent_tutorial_reaches_an_auditable_delivery(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "ai-agent-foundations")

    brief_path = create_product_brief(workspace, "AI Agent development")
    _approve(brief_path)
    curriculum_path = submit_test_blueprint(
        workspace,
        brief_path,
        titles=("AI Agent Foundations", "RAG for Agents"),
        claim_ids=(("claim-001",), ("claim-002",)),
    )
    _approve(curriculum_path)

    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    submit_test_chapter(workspace, tmp_path, 1)
    _approve_sample_decision(workspace)
    approve_sample_chapter(workspace, "editor@example.com")
    create_chapter_draft(workspace, 2)
    submit_test_chapter(workspace, tmp_path, 2)
    _write_passing_reviews(workspace)

    ledger_path = workspace.root / "research" / "source-ledger.jsonl"
    claim_bindings = current_claim_contract_bindings(workspace)
    append_source_entry(
        ledger_path,
        SourceLedgerEntry(
            source_id="official-agent-docs",
            url="https://example.com/agent-docs",
            accessed_at="2026-07-28T00:00:00Z",
            excerpt="An AI agent combines a model, tools, and instructions.",
            claim_ids=["claim-001"],
            claim_contract_sha256={"claim-001": claim_bindings["claim-001"]},
            license_note="Fixture source only",
        ),
    )
    append_source_entry(
        ledger_path,
        SourceLedgerEntry(
            source_id="official-rag-docs",
            url="https://example.com/rag-docs",
            accessed_at="2026-07-28T00:00:00Z",
            excerpt="Retrieved context can ground an answer.",
            claim_ids=["claim-002"],
            claim_contract_sha256={"claim-002": claim_bindings["claim-002"]},
            license_note="Fixture source only",
        ),
    )

    report_path, report = run_validation(workspace, {"claim-001", "claim-002"})
    assert report_path.exists()
    assert report.publishable is True

    markdown_path = assemble_book(workspace)
    pdf_path = workspace.root / "delivery" / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfixture\n")
    _, delivery_report = run_delivery_validation(workspace)
    assert delivery_report.publishable, delivery_report.findings
    manifest_path = create_delivery_manifest(workspace, markdown_path, pdf_path)

    assert manifest_path.exists()
    manifest = manifest_path.read_text(encoding="utf-8")
    assert "sha256" in manifest
    assert "review_proofs" in manifest
    assert "schema_version: 2" in manifest
    assert "book-input-manifest.json" in manifest
    assert "deterministic_only" in manifest
    assert manifest.count("parent_task_id: test-parent") == 2
    (workspace.root / "delivery" / "book.md").write_text("# 被修改的书稿\n", encoding="utf-8")
    assert delivery_manifest_issues(workspace) == ["交付清单输入已过期：delivery/book.md"]
    events = (workspace.root / "runs" / "events.jsonl").read_text(encoding="utf-8")
    assert "product_brief_created" in events
    assert "validation_completed" in events
    assert "delivery_manifest_created" in events


def test_repeated_content_or_trace_leak_blocks_publish(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    repeated = "面试者需要说明业务目标、性能约束、工具调用和失败降级路径，并用监控指标验证系统设计是否可靠。" * 3
    chapter_path = workspace.root / "chapters" / "chapter-01.md"
    chapter_path.write_text(
        "---\nbook_id: guide\nartifact_type: chapter\nstatus: in_production\nversion: 1\ndepends_on: []\napproved_by: null\n---\n"
        f"# 第一章\n\n{repeated}\n\n{repeated}\n\n结论（claim-ch01-0001）",
        encoding="utf-8",
    )
    _, report = run_validation(workspace, set())
    markdown_path = assemble_book(workspace)
    pdf_path = workspace.root / "delivery" / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfixture\n")

    _, delivery_report = run_delivery_validation(workspace)
    assert not delivery_report.publishable

    assert {"DUPLICATE_CONTENT_DETECTED", "INTERNAL_TRACE_LEAKED"} <= {
        finding.code for finding in report.findings
    }
    with pytest.raises(ValueError, match="质量报告未通过"):
        create_delivery_manifest(workspace, markdown_path, pdf_path)
