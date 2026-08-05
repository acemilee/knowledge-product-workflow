"""Regression tests for v1.0.3 PDF delivery P0 fixes."""

from __future__ import annotations

import pytest

from knowledge_product_workflow.callouts import (
    reader_component_leaks,
    render_qmd_markdown,
    render_reader_markdown,
)
from knowledge_product_workflow.presentation import PublicationProfile, PublicationUpdateInput


def test_legacy_callout_markers_become_safe_markdown_and_valid_qmd() -> None:
    source = """# GraphRAG 实战

{.kp-note}
长上下文能放下材料，但不能自动提供可遍历的关系证据。

## 下一节

{.kp-key-takeaway}
**本章要点**

- 先验证问题是否真的需要图。
"""

    reader_markdown = render_reader_markdown(source)
    qmd_markdown = render_qmd_markdown(source)

    assert ".kp-" not in reader_markdown
    assert ":::" not in reader_markdown
    assert "> **提示**" in reader_markdown
    assert "> **本章要点**" in reader_markdown
    assert reader_component_leaks(reader_markdown, allow_fenced_divs=False) == []
    assert "::: {.kp-note}" in qmd_markdown
    assert "::: {.kp-key-takeaway}" in qmd_markdown
    assert reader_component_leaks(qmd_markdown, allow_fenced_divs=True) == []


def test_delivery_scanner_rejects_a_bare_internal_component_marker() -> None:
    assert reader_component_leaks("{.kp-note}\n读者不应看到它。\n", allow_fenced_divs=False) == [1]
    assert reader_component_leaks("{.kp-note}\n读者不应看到它。\n", allow_fenced_divs=True) == [1]


def test_publication_profile_rejects_an_unsupported_font_size() -> None:
    values = {
        "subtitle": "专业知识教程",
        "cover_label": "知识产品工作流",
        "footer_label": "知识产品工作流",
        "copyright_line": "版权所有 2026 知识产品工作流",
        "edition_label": "v1.0.0",
        "publication_date": "2026-08-03",
    }
    with pytest.raises(ValueError, match="12 或 14"):
        PublicationProfile(**values, body_font_size=13)
    with pytest.raises(ValueError, match="12 或 14"):
        PublicationUpdateInput(body_font_size=13, version_bump="none")
