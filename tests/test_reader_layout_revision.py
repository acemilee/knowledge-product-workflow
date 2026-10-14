import yaml

from knowledge_product_workflow.callouts import render_qmd_markdown, render_reader_markdown
from knowledge_product_workflow.presentation import (
    PublicationProfile,
    load_or_create_publication_profile,
)
from knowledge_product_workflow.presentation_validation import validate_chapter_lead
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_legacy_profile_defaults_to_layout_one_new_profile_uses_two(tmp_path):
    workspace = Workspace.create(tmp_path, "reader-book")
    create_product_brief(workspace, "阅读布局")
    profile = load_or_create_publication_profile(workspace, language="zh-CN")
    assert profile.reader_layout_revision == 2
    legacy = profile.model_dump(mode="json", exclude={"reader_layout_revision", "illustration_style_revision"})
    legacy["schema_version"] = 3
    assert PublicationProfile.model_validate(legacy).reader_layout_revision == 1


def test_lead_is_structural_component_and_portable_markdown_keeps_its_meaning(tmp_path):
    workspace = Workspace.create(tmp_path, "reader-book")
    create_product_brief(workspace, "阅读布局")
    load_or_create_publication_profile(workspace, language="zh-CN")
    source = "# 1. 校验边界\n\n::: {.kp-chapter-lead}\n本章先识别原始资料与检查结果，再确定交接责任与适用边界。\n:::\n\n## 1.1 当前输入\n\n正文。\n"
    assert not validate_chapter_lead(source, "chapters/chapter-01.md", workspace)
    portable = render_reader_markdown(source)
    assert "本章先识别" in portable and "kp-chapter-lead" not in portable
    assert not validate_chapter_lead(portable, "delivery/book.md", workspace, required=True)
    assert "kp-chapter-lead" in render_qmd_markdown(source)
    misplaced = source.replace("# 1. 校验边界", "# 1. 校验边界\n\n## 1.0 错误位置")
    assert any(item.code == "CHAPTER_LEAD_POSITION_INVALID" for item in validate_chapter_lead(misplaced, "chapters/chapter-01.md", workspace))
    duplicate = source.replace("## 1.1 当前输入", "::: {.kp-chapter-lead}\n重复导读。\n:::\n\n## 1.1 当前输入")
    assert any(item.code == "CHAPTER_LEAD_DUPLICATED" for item in validate_chapter_lead(duplicate, "chapters/chapter-01.md", workspace))


def test_old_layout_does_not_require_lead_but_new_layout_cannot_omit_title_to_bypass(tmp_path):
    workspace = Workspace.create(tmp_path, "reader-book")
    create_product_brief(workspace, "阅读布局")
    profile = load_or_create_publication_profile(workspace, language="zh-CN")
    body = "## 1.1 当前输入\n\n正文。"
    assert any(item.code == "CHAPTER_LEAD_REQUIRED" for item in validate_chapter_lead(body, "chapters/chapter-01.md", workspace))
    path = workspace.root / "planning" / "publication-profile.yaml"
    path.write_text(yaml.safe_dump(profile.model_copy(update={"reader_layout_revision": 1}).model_dump(mode="json")), encoding="utf-8")
    assert not validate_chapter_lead(body, "chapters/chapter-01.md", workspace)
