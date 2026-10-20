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


@pytest.mark.parametrize(
    ("changes", "fields"),
    [
        ({"brief_content_sha256": "a" * 63}, ["brief_content_sha256"]),
        ({"recommended_min_pages": None, "recommended_max_pages": None},
         ["recommended_min_pages", "recommended_max_pages"]),
        ({"rationale": [919991]}, ["rationale.0"]),
    ],
)
def test_length_submission_reports_field_locations_without_input_values(tmp_path, changes, fields):
    import json

    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app
    from knowledge_product_workflow.workflows.curriculum import (
        create_teaching_length_recommendation_template,
    )
    from tests.test_outline_and_length_status import _approved_brief

    workspace = Workspace.create(tmp_path, "guide")
    _approved_brief(workspace)
    template, _ = create_teaching_length_recommendation_template(workspace)
    import yaml

    payload = yaml.safe_load(template.read_text(encoding="utf-8"))
    payload.update(recommended_min_pages=4, recommended_max_pages=8,
                   rationale=["独立合成建议，仅核字段反馈，不代表教学质量通过。"])
    payload.update(changes)
    source = workspace.root / "runs/length-invalid.json"
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = source.read_bytes()
    events = (workspace.root / "runs/events.jsonl").read_bytes()
    result = CliRunner().invoke(app, ["submit-length-recommendation", "--workspace",
                                     str(workspace.root), "--from", str(source)])
    assert result.exit_code == 1, result.output
    for field in fields:
        assert field in result.output
    assert "当前模板" in result.output
    assert "919991" not in result.output
    assert "a" * 63 not in result.output
    assert source.read_bytes() == before
    assert (workspace.root / "runs/events.jsonl").read_bytes() == events
    assert not (workspace.root / "planning/teaching-length-recommendation.yaml").exists()


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
