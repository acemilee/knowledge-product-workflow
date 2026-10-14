from __future__ import annotations

import pytest
import yaml

from knowledge_product_workflow.content_plan import (
    ContentPlanError,
    ContentPlanInput,
    content_plan_schema,
    create_content_plan_template,
    require_current_content_plan,
    submit_content_plan,
)
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.presentation import (
    load_or_create_publication_profile,
)
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.authoring import (
    amend_chapter_draft,
    compile_chapter_contracts,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint


def _approved_workspace(tmp_path, *, titles=("基础判断",)) -> Workspace:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(
        workspace,
        "决策框架",
        DetailConstraints(target_words=1, delivery_format="markdown"),
        target_audience="需要完成决策说明的工程师",
    )
    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    brief.write_text(
        render_markdown_artifact(transition(metadata, ArtifactStatus.APPROVED, approved_by="用户"), body),
        encoding="utf-8",
    )
    blueprint = submit_test_blueprint(workspace, brief, titles=titles)
    metadata, body = parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))
    blueprint.write_text(
        render_markdown_artifact(transition(metadata, ArtifactStatus.APPROVED, approved_by="用户"), body),
        encoding="utf-8",
    )
    compile_chapter_contracts(workspace)
    load_or_create_publication_profile(workspace, language="zh-CN")
    return workspace


def _valid_plan(workspace: Workspace) -> dict[str, object]:
    return {
        "sections": [
            {
                "section_id": "1.1",
                "reader_job": "建立可比较方案的决策框架并写出取舍条件。",
                "opening_mode": "decision",
                "block_sequence": ["decision", "worksheet"],
                "artifact": "方案取舍工作表",
                "asset_decision": "worksheet",
                "asset_rationale": "用可填写工作表显式记录方案、条件与后果。",
            }
        ]
    }


def test_content_plan_template_exposes_only_semantic_section_fields(tmp_path) -> None:
    workspace = _approved_workspace(tmp_path)

    template, context = create_content_plan_template(workspace, 1)

    payload = yaml.safe_load(template.read_text(encoding="utf-8"))
    context_payload = __import__("json").loads(context.read_text(encoding="utf-8"))
    assert set(payload) == {"sections"}
    assert payload["sections"][0]["section_id"] == "1.1"
    assert context_payload["expected_sections"] == [{"section_id": "1.1", "title": "决策框架"}]
    assert "contract_sha256" not in template.read_text(encoding="utf-8")


def test_content_plan_requires_contract_coverage_without_forcing_strategy_artifact(tmp_path) -> None:
    workspace = _approved_workspace(tmp_path)
    source = workspace.root / "runs" / "content-plan.yaml"
    source.write_text(yaml.safe_dump({"sections": []}, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ContentPlanError, match="章节内容计划字段无效"):
        submit_content_plan(workspace, 1, source)

    source.write_text(
        yaml.safe_dump(
            {
                "sections": [
                    {
                        **_valid_plan(workspace)["sections"][0],
                        "artifact": "none",
                        "asset_decision": "none",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    assert submit_content_plan(workspace, 1, source).is_file()


def test_current_content_plan_becomes_stale_when_contract_changes(tmp_path) -> None:
    workspace = _approved_workspace(tmp_path)
    source = workspace.root / "runs" / "content-plan.yaml"
    source.write_text(yaml.safe_dump(_valid_plan(workspace), allow_unicode=True), encoding="utf-8")
    output = submit_content_plan(workspace, 1, source)

    assert output.is_file()
    assert require_current_content_plan(workspace, 1) is not None
    payload = yaml.safe_load(output.read_text(encoding="utf-8"))
    payload["contract_sha256"] = "0" * 64
    output.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ContentPlanError, match="已过期"):
        require_current_content_plan(workspace, 1)


def test_public_schema_example_is_valid_and_unfinished_template_is_explicit(tmp_path) -> None:
    schema = content_plan_schema()
    assert schema["input_schema"] == ContentPlanInput.model_json_schema()
    assert ContentPlanInput.model_validate(schema["example"])
    workspace = _approved_workspace(tmp_path)
    template, _ = create_content_plan_template(workspace, 1)
    with pytest.raises(ContentPlanError, match="模板未完成"):
        submit_content_plan(workspace, 1, template)
    assert not (workspace.root / "chapters" / "chapter-01-content-plan.yaml").exists()


def test_controlled_amendment_can_replace_exact_archived_plan_only_once(tmp_path):
    workspace = _approved_workspace(tmp_path)
    create_chapter_draft(workspace, 1)
    source = workspace.root / "runs" / "content-plan.yaml"
    source.write_text(yaml.safe_dump(_valid_plan(workspace), allow_unicode=True), encoding="utf-8")
    output = submit_content_plan(workspace, 1, source)
    original = output.read_bytes()
    with pytest.raises(FileExistsError, match="受控章节修订"):
        submit_content_plan(workspace, 1, source)

    amend_chapter_draft(workspace, 1, reason="调整当前章读者任务")
    payload = _valid_plan(workspace)
    payload["sections"][0]["reader_job"] = "按同一组约束比较方案并记录取舍理由。"
    source.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    submit_content_plan(workspace, 1, source)
    assert output.read_bytes() != original
    with pytest.raises(FileExistsError, match="受控章节修订"):
        submit_content_plan(workspace, 1, source)


def test_draft_plan_revision_archives_old_plan_without_amending_chapter(tmp_path):
    workspace = _approved_workspace(tmp_path)
    chapter=create_chapter_draft(workspace,1)
    original_chapter=chapter.read_bytes()
    source=workspace.root/'runs/content-plan.yaml'
    payload=_valid_plan(workspace)
    source.write_text(yaml.safe_dump(payload,allow_unicode=True),encoding='utf-8')
    output=submit_content_plan(workspace,1,source);original=output.read_bytes()
    payload['sections'][0]['reader_job']='按同一组约束比较方案并记录取舍理由。'
    source.write_text(yaml.safe_dump(payload,allow_unicode=True),encoding='utf-8')
    submit_content_plan(workspace,1,source,replace_draft=True)
    assert chapter.read_bytes()==original_chapter
    assert any(path.read_bytes()==original for path in (workspace.root/'runs/content-plan-history').glob('*.yaml'))
    assert require_current_content_plan(workspace,1).sections[0].reader_job==payload['sections'][0]['reader_job']


def test_draft_plan_revision_rejects_approved_chapter(tmp_path):
    workspace = _approved_workspace(tmp_path)
    chapter=create_chapter_draft(workspace,1)
    source=workspace.root/'runs/content-plan.yaml'
    source.write_text(yaml.safe_dump(_valid_plan(workspace),allow_unicode=True),encoding='utf-8')
    output=submit_content_plan(workspace,1,source);original=output.read_bytes()
    metadata,body=parse_markdown_artifact(chapter.read_text(encoding='utf-8'))
    metadata.status=ArtifactStatus.APPROVED
    chapter.write_text(render_markdown_artifact(metadata,body),encoding='utf-8')
    with pytest.raises(ContentPlanError,match='amend-chapter'):
        submit_content_plan(workspace,1,source,replace_draft=True)
    assert output.read_bytes()==original
