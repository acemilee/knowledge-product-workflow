from datetime import date
from pathlib import Path

import pytest

from knowledge_product_workflow.presentation import (
    PresentationOverrides,
    load_or_create_publication_metadata,
)
from knowledge_product_workflow.workflows.product_brief import (
    create_product_brief,
    parse_presentation_overrides,
)
from knowledge_product_workflow.workspace import Workspace


def test_publication_profile_keeps_defaults_while_metadata_refreshes_from_current_book(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")

    created = load_or_create_publication_metadata(
        workspace,
        title="AI Agent 工程实践",
        audience_label="面向进阶级中文读者",
        language="zh-CN",
        today=date(2026, 8, 1),
    )
    reloaded = load_or_create_publication_metadata(
        workspace,
        title="更新后的标题",
        audience_label="更新后的受众",
        language="zh-CN",
        overrides=PresentationOverrides(subtitle="不应覆盖的副标题"),
        today=date(2026, 8, 2),
    )

    assert created.title == "AI Agent 工程实践"
    assert created.subtitle == "专业知识教程"
    assert created.edition == "v1.0.0"
    assert created.publication_date == date(2026, 8, 1)
    assert created.cover_label == "知识产品工作流"
    assert created.footer_label == "知识产品工作流"
    assert created.copyright_line == "版权所有 2026 知识产品工作流"
    assert reloaded.title == "更新后的标题"
    assert reloaded.audience_label == "更新后的受众"
    assert reloaded.subtitle == "专业知识教程"
    metadata_path = workspace.root / "delivery" / "publication-metadata.yaml"
    assert metadata_path.is_file()
    assert (workspace.root / "planning" / "publication-profile.yaml").is_file()
    assert "publication_profile_created" in (
        workspace.root / "runs" / "events.jsonl"
    ).read_text(encoding="utf-8")


def test_publication_metadata_applies_optional_overrides_without_making_them_required(
    tmp_path: Path,
) -> None:
    workspace = Workspace.create(tmp_path, "guide")

    metadata = load_or_create_publication_metadata(
        workspace,
        title="Knowledge Product Workflow",
        audience_label="For English-speaking engineers",
        language="en-US",
        overrides=PresentationOverrides(
            subtitle="A practical production guide",
            edition="Second edition",
            publication_date=date(2026, 7, 31),
            publisher_label="Example Press",
            copyright_line="Copyright 2026 Example Press",
        ),
    )

    assert metadata.subtitle == "A practical production guide"
    assert metadata.edition == "Second edition"
    assert metadata.publication_date == date(2026, 7, 31)
    assert metadata.publisher_label == "Example Press"
    assert metadata.footer_label == "Example Press"
    assert metadata.copyright_line == "Copyright 2026 Example Press"


def test_presentation_overrides_reject_blank_explicit_values() -> None:
    with pytest.raises(ValueError, match="subtitle"):
        PresentationOverrides(subtitle="   ")


def test_missing_v102_profile_seeds_reader_labels_from_legacy_metadata(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    (workspace.root / "delivery" / "publication-metadata.yaml").write_text(
        "profile_id: knowledge-product-pdf-v3\n"
        "title: 旧书\n"
        "subtitle: 旧副标题\n"
        "audience_label: 旧读者\n"
        "edition: 第二版\n"
        "publication_date: 2025-01-02\n"
        "publisher_label: 旧品牌\n"
        "copyright_line: 版权所有 2025 旧品牌\n",
        encoding="utf-8",
    )

    metadata = load_or_create_publication_metadata(
        workspace,
        title="旧书",
        audience_label="旧读者",
        language="zh-CN",
        today=date(2026, 8, 2),
    )

    assert metadata.cover_label == "旧品牌"
    assert metadata.footer_label == "旧品牌"
    assert metadata.edition_label == "第二版"
    assert metadata.publication_date == date(2025, 1, 2)


def test_product_brief_persists_optional_presentation_overrides(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief_path = create_product_brief(
        workspace,
        "AI Agent 工程实践",
        presentation=PresentationOverrides(subtitle="从原型到稳定交付"),
    )

    body = brief_path.read_text(encoding="utf-8")
    parsed = parse_presentation_overrides(body)

    assert parsed is not None
    assert parsed.subtitle == "从原型到稳定交付"
    assert "## 出版信息" in body
