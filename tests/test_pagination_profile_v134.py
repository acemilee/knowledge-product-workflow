from datetime import date

import yaml

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.presentation import (
    LEGACY_PRESENTATION_PROFILE_ID,
    DesignFamily,
    PublicationProfile,
    load_or_create_publication_profile,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def profile_values():
    return dict(subtitle="学习主线", cover_label="教程", footer_label="教程",
                copyright_line="© 2026", edition_label="v1.0.0",
                publication_date=date(2026, 10, 13))


def test_new_technical_manual_records_chapter_header_in_saved_profile(tmp_path):
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "输入与核对", DetailConstraints())
    profile = load_or_create_publication_profile(workspace, language="zh-CN")
    assert profile.reader_layout_revision == 2
    assert profile.running_header_mode == "chapter"
    saved = yaml.safe_load((workspace.root / "planning/publication-profile.yaml").read_text(encoding="utf-8"))
    assert saved["running_header_mode"] == "chapter"


def test_explicit_section_and_historical_missing_mode_are_preserved():
    values = profile_values()
    assert PublicationProfile(**values, reader_layout_revision=2,
                              running_header_mode="section").running_header_mode == "section"
    assert PublicationProfile(**values, reader_layout_revision=1).running_header_mode == "section"
    assert PublicationProfile(**values, schema_version=1,
                              profile_id=LEGACY_PRESENTATION_PROFILE_ID).running_header_mode == "section"
    assert PublicationProfile(**values, reader_layout_revision=1,
                              design_family=DesignFamily.EDITORIAL_GUIDE).running_header_mode == "chapter"


def test_existing_saved_section_profile_remains_byte_identical(tmp_path):
    workspace = Workspace.create(tmp_path, "guide")
    profile = PublicationProfile(**profile_values(), reader_layout_revision=2,
                                 running_header_mode="section")
    path = workspace.root / "planning/publication-profile.yaml"
    path.write_text(yaml.safe_dump(profile.model_dump(mode="json"), allow_unicode=True), encoding="utf-8")
    original = path.read_bytes()
    assert load_or_create_publication_profile(workspace, language="zh-CN").running_header_mode == "section"
    assert path.read_bytes() == original


def test_preprofile_manuscript_keeps_legacy_section_header(tmp_path):
    workspace = Workspace.create(tmp_path, "guide")
    (workspace.root / "chapters/chapter-01.md").write_text("# 旧章\n正文", encoding="utf-8")
    profile = load_or_create_publication_profile(workspace, language="zh-CN")
    assert profile.schema_version == 2
    assert profile.running_header_mode == "section"
