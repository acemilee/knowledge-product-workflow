"""新默认空页脚与署名；旧配置及未提供更新不被自动改写。"""
from datetime import date

import pytest

from knowledge_product_workflow.presentation import (
    PresentationOverrides,
    PublicationUpdateInput,
    _defaults,
    load_or_create_publication_metadata,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_new_defaults_use_publication_year_and_empty_footer():
    data = _defaults("zh-CN", date(2031, 4, 1))
    assert data["footer_label"] == ""
    assert data["copyright_line"] == "© 2031 葬爱西兰花"


def test_empty_footer_update_is_not_omitted_and_whitespace_is_invalid():
    update = PublicationUpdateInput(footer_label="", version_bump="none")
    assert update.model_dump(exclude_none=True)["footer_label"] == ""
    assert PublicationUpdateInput(cover_label="明确封面标签", version_bump="none").footer_label is None
    with pytest.raises(ValueError):
        PublicationUpdateInput(footer_label="  ", version_bump="none")
    with pytest.raises(ValueError):
        PublicationUpdateInput(copyright_line="", version_bump="none")


def test_new_publisher_does_not_implicitly_populate_footer(tmp_path):
    w = Workspace.create(tmp_path, "branding")
    create_product_brief(w, "主题导航")
    meta = load_or_create_publication_metadata(w, title="主题导航", audience_label="目标读者", language="zh-CN",
        overrides=PresentationOverrides(publisher_label="明确品牌"), today=date(2031, 4, 1))
    assert meta.footer_label == ""
    assert "明确品牌" in meta.copyright_line
    stored = load_or_create_publication_metadata(w, title="主题导航", audience_label="目标读者", language="zh-CN")
    assert stored.footer_label == "" and stored.copyright_line == meta.copyright_line


def test_explicit_publication_date_drives_new_copyright_year(tmp_path):
    from knowledge_product_workflow.presentation import load_or_create_publication_profile
    w = Workspace.create(tmp_path, "future")
    profile = load_or_create_publication_profile(w, language="zh-CN", today=date(2026, 1, 1),
        overrides=PresentationOverrides(publication_date=date(2031, 4, 1), cover_summary_zh="用来核验出版日期与默认版权年份的独立样本。"))
    assert profile.copyright_line == "© 2031 葬爱西兰花"
