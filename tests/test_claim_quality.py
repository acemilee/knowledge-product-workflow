from pathlib import Path

from knowledge_product_workflow.claim_quality import validate_undeclared_high_risk_claims
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.authoring import (
    compile_chapter_contracts,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint, submit_test_chapter


def _submitted_chapter(tmp_path: Path, claim_ids: tuple[str, ...]) -> tuple[Workspace, Path, str]:
    workspace = Workspace.create(tmp_path, "claim-guide")
    brief = create_product_brief(workspace, "面试教程", target_audience="工程师")
    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    brief.write_text(
        render_markdown_artifact(transition(metadata, ArtifactStatus.APPROVED, approved_by="user"), body),
        encoding="utf-8",
    )
    blueprint = submit_test_blueprint(
        workspace, brief, titles=("岗位判断",), claim_ids=(claim_ids,)
    )
    metadata, body = parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))
    blueprint.write_text(
        render_markdown_artifact(transition(metadata, ArtifactStatus.APPROVED, approved_by="user"), body),
        encoding="utf-8",
    )
    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    chapter_path = submit_test_chapter(workspace, tmp_path, 1)
    _, chapter_body = parse_markdown_artifact(chapter_path.read_text(encoding="utf-8"))
    return workspace, chapter_path, chapter_body


def test_high_risk_fact_without_a_declared_claim_is_blocked(tmp_path: Path) -> None:
    workspace, chapter_path, chapter_body = _submitted_chapter(tmp_path, ())
    body = chapter_body + "\n大多数公司会将系统设计列为典型面试重点。\n"

    findings = validate_undeclared_high_risk_claims(workspace, chapter_path, body)

    assert [finding.code for finding in findings] == ["UNDECLARED_HIGH_RISK_CLAIM"]


def test_declared_claim_anchor_is_left_for_source_ledger_validation(tmp_path: Path) -> None:
    workspace, chapter_path, chapter_body = _submitted_chapter(tmp_path, ("claim-001",))
    declared = chapter_body + "\n大多数公司会将系统设计列为典型面试重点。\n"
    undeclared = declared + "\n## 1.1 额外结论\n\n真实 JD 显示所有候选人都必须完成该题。\n"

    assert validate_undeclared_high_risk_claims(workspace, chapter_path, declared) == []
    assert [finding.code for finding in validate_undeclared_high_risk_claims(workspace, chapter_path, undeclared)] == [
        "UNDECLARED_HIGH_RISK_CLAIM"
    ]
