"""Observable contracts for brief semantics and semantic cover line breaks."""

from datetime import date

import pytest

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.presentation import (
    PresentationOverrides,
    PublicationUpdateInput,
    load_or_create_publication_metadata,
    load_or_create_publication_profile,
    preview_publication_update,
)
from knowledge_product_workflow.workflows.product_brief import (
    BriefSemanticFields,
    brief_contract_fingerprints,
    create_product_brief,
    parse_brief_semantics,
    read_request_contract,
    render_product_brief_body,
    write_request_contract,
)
from knowledge_product_workflow.workspace import Workspace


def _semantics():
    return BriefSemanticFields(
        learning_outcomes=["理解Skill的作用", "看懂目录结构", "完成最小Skill的编写与调用"],
        product_positioning="面向使用过Agent的程序员的中文短教程。",
        scope_inclusions=["原理、结构与基础用法"],
        scope_exclusions=["外部工具接入与复杂编排"],
    )


def test_approved_brief_carries_specific_goals_and_provenance(tmp_path):
    workspace = Workspace.create(tmp_path, "intro")
    goals = _semantics()
    constraints = DetailConstraints()
    path = create_product_brief(workspace, "Agent Skills 入门", constraints, "程序员", semantics=goals)
    assert parse_brief_semantics(path.read_text(encoding="utf-8")) == goals
    write_request_contract(workspace, "Agent Skills 入门", "程序员", constraints,
                           {"learning_outcomes": "user_explicit"}, semantics=goals)
    request = read_request_contract(workspace)
    assert request["requirements"]["learning_outcomes"] == {
        "value": goals.learning_outcomes, "source": "user_explicit"
    }
    assert request["requirements"]["scope_exclusions"]["source"] == "model_recommendation"


def test_goal_revision_invalidates_content_but_cover_only_revision_does_not():
    original = render_product_brief_body("Skills", "程序员", DetailConstraints(), None, _semantics())
    changed = _semantics().model_copy(update={"learning_outcomes": ["实现带工具的Skill"]})
    revised = render_product_brief_body("Skills", "程序员", DetailConstraints(), None, changed)
    cover = render_product_brief_body("Skills", "程序员", DetailConstraints(),
                                     PresentationOverrides(subtitle="新副标题"), _semantics())
    before = brief_contract_fingerprints(original)
    assert before["content_contract_sha256"] != brief_contract_fingerprints(revised)["content_contract_sha256"]
    assert before["content_contract_sha256"] == brief_contract_fingerprints(cover)["content_contract_sha256"]


def test_cover_two_defaults_and_title_lines_reach_render_metadata(tmp_path):
    workspace = Workspace.create(tmp_path, "cover")
    title = "Agent Skills 入门：原理、结构与基础用法"
    create_product_brief(workspace, title)
    overrides = PresentationOverrides(cover_layout_revision=2, cover_summary_zh="学习基础结构并完成最小Skill。",
                                      cover_title_lines=["Agent Skills 入门：", "原理、结构与基础用法"])
    profile = load_or_create_publication_profile(workspace, language="zh-CN", overrides=overrides,
                                                today=date(2026, 10, 4))
    assert profile.copyright_line == "© 2026 葬爱西兰花"
    metadata = load_or_create_publication_metadata(workspace, title=title, audience_label="程序员", language="zh-CN")
    assert metadata.cover_title_lines == overrides.cover_title_lines
    assert metadata.title == title


def test_invalid_cover_copyright_is_rejected_before_profile_write(tmp_path):
    workspace = Workspace.create(tmp_path, "invalid")
    with pytest.raises(ValueError, match="版权行"):
        load_or_create_publication_profile(workspace, language="zh-CN", overrides=PresentationOverrides(
            cover_layout_revision=2, cover_summary_zh="学习结构与基础调用。", copyright_line="版权所有 2026 用户"
        ))
    assert not (workspace.root / "planning/publication-profile.yaml").exists()


def test_cover_line_revision_cannot_silently_change_topic(tmp_path):
    workspace = Workspace.create(tmp_path, "lines")
    create_product_brief(workspace, "Agent Skills 入门")
    load_or_create_publication_profile(workspace, language="zh-CN")
    with pytest.raises(ValueError, match="正式主题"):
        preview_publication_update(workspace, PublicationUpdateInput(
            cover_title_lines=["Agent Skills 高级"], version_bump="none"
        ), language="zh-CN")
    assert not (workspace.root / "runs/publication-update-preview.json").exists()


def test_existing_layout_one_keeps_explicit_legacy_copyright(tmp_path):
    workspace = Workspace.create(tmp_path, "legacy")
    profile = load_or_create_publication_profile(workspace, language="zh-CN", overrides=PresentationOverrides(
        cover_layout_revision=1, copyright_line="版权所有 2025 原作者"
    ))
    again = load_or_create_publication_profile(workspace, language="zh-CN")
    assert profile.copyright_line == again.copyright_line == "版权所有 2025 原作者"
