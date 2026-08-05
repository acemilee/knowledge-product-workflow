from pathlib import Path

import pytest

from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactMetadata, ArtifactStatus
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.user_decisions import (
    UserDecisionTarget,
    UserResponseDecision,
    record_user_decision,
    request_user_decision,
)
from knowledge_product_workflow.workflows.authoring import (
    CurriculumApprovalRequiredError,
    SampleApprovalRequiredError,
    amend_chapter_draft,
    approve_sample_chapter,
    compile_chapter_contracts,
    create_chapter_contract,
    create_chapter_draft,
)
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint, submit_test_chapter


def _approve_sample_decision(workspace: Workspace) -> None:
    request_user_decision(workspace, UserDecisionTarget.SAMPLE)
    record_user_decision(
        workspace,
        UserDecisionTarget.SAMPLE,
        UserResponseDecision.APPROVE,
        approved_by="editor@example.com",
        response="批准",
    )


def test_chapter_contract_requires_an_approved_curriculum(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "ai-agent-foundations")

    with pytest.raises(CurriculumApprovalRequiredError):
        create_chapter_contract(workspace, chapter_number=1, title="AI Agent Foundations")


def test_non_sample_chapter_requires_approved_sample(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "ai-agent-foundations")
    brief = workspace.root / "planning" / "product-brief.md"
    brief.write_text(
        render_markdown_artifact(
            transition(
                ArtifactMetadata(
                    book_id=workspace.root.name,
                    artifact_type="product_brief",
                    status=ArtifactStatus.READY_FOR_REVIEW,
                    version=1,
                ),
                ArtifactStatus.APPROVED,
                approved_by="editor@example.com",
            ),
            "# AI Agent Foundations\n\n## 目标读者\n\n工程师\n",
        ),
        encoding="utf-8",
    )
    blueprint = submit_test_blueprint(
        workspace, brief, titles=("AI Agent Foundations", "Planning Agents")
    )
    metadata, body = parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))
    blueprint.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="editor@example.com"), body
        ),
        encoding="utf-8",
    )
    contract_path = compile_chapter_contracts(workspace)[0]
    contract, contract_body = parse_markdown_artifact(contract_path.read_text(encoding="utf-8"))
    assert contract.artifact_type == "chapter_contract"
    assert "必需事实声明" in contract_body

    chapter_one = create_chapter_draft(workspace, chapter_number=1)
    assert "## 正文" not in chapter_one.read_text(encoding="utf-8")
    with pytest.raises(SampleApprovalRequiredError):
        create_chapter_draft(workspace, chapter_number=2)

    submit_test_chapter(workspace, tmp_path, 1)
    _approve_sample_decision(workspace)
    approve_sample_chapter(workspace, approved_by="editor@example.com")
    chapter_two = create_chapter_draft(workspace, chapter_number=2)

    assert chapter_two.exists()


def test_amendment_archives_a_submitted_sample_and_reopens_a_new_draft_version(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "amendment-guide")
    brief = workspace.root / "planning" / "product-brief.md"
    brief.write_text(
        render_markdown_artifact(
            transition(
                ArtifactMetadata(book_id=workspace.root.name, artifact_type="product_brief", status=ArtifactStatus.READY_FOR_REVIEW, version=1),
                ArtifactStatus.APPROVED,
                approved_by="editor@example.com",
            ),
            "# 修订测试\n\n## 目标读者\n\n工程师\n",
        ),
        encoding="utf-8",
    )
    blueprint = submit_test_blueprint(workspace, brief, titles=("基础判断",))
    metadata, body = parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))
    blueprint.write_text(render_markdown_artifact(transition(metadata, ArtifactStatus.APPROVED, approved_by="editor@example.com"), body), encoding="utf-8")
    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    submit_test_chapter(workspace, tmp_path, 1)
    _approve_sample_decision(workspace)
    approve_sample_chapter(workspace, approved_by="editor@example.com")

    reopened = amend_chapter_draft(workspace, 1, "补充并发原子领取语义")

    revised, _ = parse_markdown_artifact(reopened.read_text(encoding="utf-8"))
    assert revised.status is ArtifactStatus.IN_PRODUCTION
    assert (workspace.root / "runs" / "chapter-revisions" / "chapter-01" / "v2" / "chapter-01.md").is_file()
    assert not (workspace.root / "chapters" / "chapter-01-sample.approved").exists()
