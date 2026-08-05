from pathlib import Path

import pytest

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.curriculum import BlueprintSubmissionError
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint


def _approve(path: Path) -> None:
    metadata, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    path.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="reviewer"), body
        ),
        encoding="utf-8",
    )


def test_excessive_hard_page_target_requires_declared_scope_expansion(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(
        workspace,
        "一个可在短文讲清的主题",
        DetailConstraints(target_pages=100),
        target_audience="初学工程师",
    )
    _approve(brief)

    with pytest.raises(BlueprintSubmissionError, match="范围扩展"):
        submit_test_blueprint(
            workspace,
            brief,
            titles=("核心概念",),
            recommendation_max_pages=10,
        )
