from pathlib import Path

import pytest

from knowledge_product_workflow.claim_classification import (
    ClaimClassificationInput,
    append_claim_classification,
    factual_claim_ids,
    validate_nonfactual_claim_boundaries,
)
from knowledge_product_workflow.claim_quality import validate_undeclared_high_risk_claims
from knowledge_product_workflow.claims import current_claim_contract_bindings
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


def test_recommendation_classification_is_contract_bound_without_provenance_label(tmp_path: Path) -> None:
    workspace, _, body = _submitted_chapter(tmp_path, ("claim-001",))
    bindings = current_claim_contract_bindings(workspace)
    statement = "建议先用小样验证路线，并记录失败条件。"
    append_claim_classification(workspace, ClaimClassificationInput(
        claim_id="claim-001",
        kind="author_recommendation",
        statement=statement,
        heading_anchor="1.1 决策框架",
        reason="这是作者的可选做法，而非外部效果事实。",
    ))
    assert factual_claim_ids(workspace, bindings) == set()
    assert [item.code for item in validate_nonfactual_claim_boundaries(
        workspace, 1, body, ["claim-001"], {"claim-001": "1.1 决策框架"}
    )] == ["CLAIM_CLASSIFICATION_BOUNDARY_MISSING"]
    unlabelled = body + f"\n{statement}\n"
    assert validate_nonfactual_claim_boundaries(
        workspace, 1, unlabelled, ["claim-001"], {"claim-001": "1.1 决策框架"}
    ) == []
    marked = body + f"\n作者建议：{statement}\n"
    assert validate_nonfactual_claim_boundaries(
        workspace, 1, marked, ["claim-001"], {"claim-001": "1.1 决策框架"}
    ) == []

    with pytest.raises(ValueError, match="需来源"):
        append_claim_classification(workspace, ClaimClassificationInput(
            claim_id="claim-001",
            kind="author_recommendation",
            statement="建议采用此方案，它会提高通过率 30%。",
            heading_anchor="1.1 决策框架",
            reason="错误地将效果事实写入建议。",
        ))


def test_scenario_needs_actual_content_in_the_bound_section_not_an_identity_label(tmp_path: Path) -> None:
    workspace, _, body = _submitted_chapter(tmp_path, ("claim-001",))
    statement = "两个方案在相同权重下得分为7.6和7.4。"
    append_claim_classification(workspace, ClaimClassificationInput(
        claim_id="claim-001", kind="scenario", statement=statement,
        heading_anchor="1.1 决策框架", reason="后台记录的自设教学数据。",
    ))
    assert validate_nonfactual_claim_boundaries(
        workspace, 1, body + f"\n{statement}\n", ["claim-001"], {"claim-001": "1.1 决策框架"}
    ) == []
    wrong_section = body + f"\n## 1.2 其他内容\n\n{statement}\n"
    assert [item.code for item in validate_nonfactual_claim_boundaries(
        workspace, 1, wrong_section, ["claim-001"], {"claim-001": "1.1 决策框架"}
    )] == ["CLAIM_CLASSIFICATION_BOUNDARY_MISSING"]
    assert [item.code for item in validate_nonfactual_claim_boundaries(
        workspace, 1, body + f"\n{statement}\n", ["claim-001"], {"claim-001": "1.2 其他内容"}
    )] == ["CLAIM_CLASSIFICATION_BOUNDARY_MISSING"]


def test_recommendation_does_not_exempt_a_factual_claim_in_the_same_section(tmp_path: Path) -> None:
    workspace, chapter_path, body = _submitted_chapter(tmp_path, ("claim-001", "claim-002"))
    statement = "建议先用小样验证路线。"
    append_claim_classification(workspace, ClaimClassificationInput(
        claim_id="claim-001", kind="author_recommendation", statement=statement,
        heading_anchor="1.1 决策框架", reason="作者的可选做法。",
    ))
    assert factual_claim_ids(workspace, current_claim_contract_bindings(workspace)) == {"claim-002"}
    marked = body + f"\n作者建议：{statement}\n大多数公司会将系统设计列为面试重点。\n"
    assert validate_nonfactual_claim_boundaries(workspace, 1, marked, ["claim-001", "claim-002"], {"claim-001": "1.1 决策框架", "claim-002": "1.1 决策框架"}) == []
    assert validate_undeclared_high_risk_claims(workspace, chapter_path, marked) == []
