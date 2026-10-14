from __future__ import annotations

from datetime import date

import pytest

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.presentation import (
    LEGACY_PRESENTATION_PROFILE_ID,
    PRESENTATION_PROFILE_ID,
    DesignFamily,
    PublicationProfile,
    PublicationUpdateInput,
    apply_publication_update,
    load_or_create_publication_profile,
    preview_publication_update,
)
from knowledge_product_workflow.publishing import create_quarto_source
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_new_workspaces_seed_a_v6_orange_publication_design_profile(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")

    create_product_brief(workspace, "输入与结果核对", DetailConstraints())
    profile = load_or_create_publication_profile(
        workspace,
        language="zh-CN",
        today=date(2026, 8, 23),
    )

    assert profile.schema_version == 4
    assert profile.profile_id == PRESENTATION_PROFILE_ID
    assert profile.design_family is DesignFamily.TECHNICAL_MANUAL
    assert profile.content_contract_enabled is True
    assert profile.running_header_mode == "chapter"
    assert profile.palette.accent == "B65E2B"
    assert profile.body_font_size == 12
    assert profile.illustration_backend_preference == "vector"


def test_legacy_v4_profile_loads_without_silent_migration(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    profile_path = workspace.root / "planning" / "publication-profile.yaml"
    profile_path.write_text(
        "schema_version: 1\n"
        f"profile_id: {LEGACY_PRESENTATION_PROFILE_ID}\n"
        "revision: 3\n"
        "subtitle: 旧副标题\n"
        "cover_label: 旧品牌\n"
        "footer_label: 旧页脚\n"
        "copyright_line: 版权所有 2026 旧品牌\n"
        "edition_label: v1.0.0\n"
        "publication_date: 2026-08-01\n"
        "body_font_size: 12\n",
        encoding="utf-8",
    )

    profile = load_or_create_publication_profile(workspace, language="zh-CN")

    assert profile.schema_version == 1
    assert profile.profile_id == LEGACY_PRESENTATION_PROFILE_ID
    assert profile.revision == 3
    assert profile.paragraph_spacing == "compact"


def test_design_update_uses_whitelisted_tokens_and_rejects_tex_like_colours(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "输入与结果核对", DetailConstraints())
    load_or_create_publication_profile(workspace, language="zh-CN", today=date(2026, 8, 23))

    _, candidate = preview_publication_update(
        workspace,
        PublicationUpdateInput(
            design_family=DesignFamily.EDITORIAL_GUIDE,
            reading_context="夜间屏幕阅读",
            version_bump="none",
        ),
        language="zh-CN",
    )

    assert candidate.profile.design_family is DesignFamily.EDITORIAL_GUIDE
    assert candidate.profile.palette.accent == "B65E2B"
    assert candidate.profile.reading_context == "夜间屏幕阅读"
    assert candidate.profile.content_contract_enabled is True

    with pytest.raises(ValueError, match="HEX"):
        PublicationProfile(
            subtitle="专业知识教程",
            cover_label="知识产品工作流",
            footer_label="知识产品工作流",
            copyright_line="版权所有 2026 知识产品工作流",
            edition_label="v1.0.0",
            publication_date=date(2026, 8, 23),
            palette={"accent": "\\input{evil}"},
        )


def test_v6_design_tokens_are_mapped_to_fixed_pdf_assets(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(
        workspace,
        "决策树分类器",
        DetailConstraints(target_words=1, delivery_format="pdf"),
    )
    load_or_create_publication_profile(workspace, language="zh-CN", today=date(2026, 8, 23))
    preview_publication_update(
        workspace,
        PublicationUpdateInput(design_family=DesignFamily.EDITORIAL_GUIDE, version_bump="none"),
        language="zh-CN",
    )
    apply_publication_update(workspace, "用户")
    markdown_path = workspace.root / "delivery" / "book.md"
    markdown_path.write_text("# 决策树分类器\n\n正文。\n", encoding="utf-8")

    qmd_path = create_quarto_source(workspace, markdown_path)
    style = (workspace.root / "delivery" / "knowledge-book-style.tex").read_text(encoding="utf-8")

    assert "\\providecommand{\\KnowledgeProductInkHex}{202124}" in style
    assert "\\providecommand{\\KnowledgeProductAccentHex}{B65E2B}" in style
    assert "\\providecommand{\\KnowledgeProductChapterSize}{24}" in style
    assert "left=13mm,right=13mm" in qmd_path.read_text(encoding="utf-8")
