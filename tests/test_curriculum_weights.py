from pathlib import Path

import pytest

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.authoring import create_chapter_contract
from knowledge_product_workflow.workflows.curriculum import (
    parse_chapter_budgets,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint


def _approve(path: Path) -> None:
    metadata, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    path.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="editor@example.com"),
            body,
        ),
        encoding="utf-8",
    )


def test_curriculum_budget_must_match_the_approved_book_range() -> None:
    body = """## 章节预算

```yaml
chapter_budgets:
  - chapter_number: 1
    weight: 1
    word_min: 100
    word_target: 200
    word_max: 300
    rationale: 基础
    required_units: [concept_explanation]
```
"""
    constraints = DetailConstraints(word_min=200, word_target=400, word_max=600)

    with pytest.raises(ValueError, match="章节预算"):
        parse_chapter_budgets(body, constraints)


def test_contract_inherits_approved_weight_reason_and_teaching_units(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(
        workspace,
        "AI Agent 面试",
        DetailConstraints(word_min=30000, word_target=40000, word_max=50000),
    )
    _approve(brief)
    blueprint = submit_test_blueprint(workspace, brief, titles=("岗位判断", "核心能力"))
    _approve(blueprint)

    _, blueprint_body = parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))
    budgets = parse_chapter_budgets(
        blueprint_body,
        DetailConstraints(word_min=30000, word_target=40000, word_max=50000),
    )
    contract = create_chapter_contract(workspace, 2, "核心能力")
    _, contract_body = parse_markdown_artifact(contract.read_text(encoding="utf-8"))

    assert len({budget.weight for budget in budgets.values()}) > 1
    assert sum(budget.word_target or 0 for budget in budgets.values()) == 40000
    assert "weight:" in contract_body
    assert "rationale:" in contract_body
    assert "required_units:" in contract_body
    assert "## 必需教学单元" in contract_body
