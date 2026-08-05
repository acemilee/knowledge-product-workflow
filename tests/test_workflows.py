from pathlib import Path

import pytest

from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.curriculum import (
    CurriculumPrerequisiteError,
    create_curriculum_blueprint_template,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_product_brief_is_created_ready_for_review(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "ai-agent-foundations")

    brief_path = create_product_brief(workspace, "AI Agent development")

    metadata, body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    assert metadata.artifact_type == "product_brief"
    assert metadata.status.value == "ready_for_review"
    assert "AI Agent development" in body


def test_curriculum_requires_an_approved_product_brief(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "ai-agent-foundations")
    brief_path = create_product_brief(workspace, "AI Agent development")

    with pytest.raises(CurriculumPrerequisiteError):
        create_curriculum_blueprint_template(workspace, brief_path)

    metadata, body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    approved = transition(metadata, target=ArtifactStatus.APPROVED, approved_by="editor@example.com")
    brief_path.write_text(render_markdown_artifact(approved, body), encoding="utf-8")

    template_path = create_curriculum_blueprint_template(workspace, brief_path)

    template = template_path.read_text(encoding="utf-8")
    assert template_path.name == "curriculum-blueprint.template.yaml"
    assert "chapters:" in template
    assert "<主题化章节标题>" in template
