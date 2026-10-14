from datetime import date

import pytest
from pydantic import ValidationError

from knowledge_product_workflow.illustration_palette import (
    COLORS,
    SCHEMES,
    DiagramPresentationInput,
    diagram_presentation_digest,
    resolve_diagram_palette,
)
from knowledge_product_workflow.presentation import PublicationProfile


def profile():
    return PublicationProfile(subtitle="测试", cover_label="测试", footer_label="测试",
        copyright_line="内部测试", edition_label="v1.0.0", publication_date=date(2026, 10, 2))


def presentation(scheme="balanced", token="peach"):
    return DiagramPresentationInput(scheme=scheme, node_colors={"a": token},
                                    group_colors={}, concept_nodes={"a": "原始资料"})


def test_concept_conflict_and_unused_concept_cache():
    with pytest.raises(ValueError, match="概念颜色冲突"):
        resolve_diagram_palette(profile(), presentation(), {"原始资料": "blue"})
    a = diagram_presentation_digest(profile(), presentation(), {"原始资料": "peach"})
    assert a == diagram_presentation_digest(profile(), presentation(),
        {"原始资料": "peach", "无关": "sage"})
    changed = profile().model_copy(update={"revision": 9, "copyright_line": "新版署名"})
    assert a == diagram_presentation_digest(changed, presentation(), {})


@pytest.mark.parametrize("scheme", list(SCHEMES))
@pytest.mark.parametrize("brand", ["橙色基调", "紫色基调", "#176B55"])
@pytest.mark.parametrize("kind", ["flow", "layered_architecture", "comparison", "sequence", "lifecycle", "nested_structure", "card_matrix", "stage_flow"])
def test_72_palette_combinations(scheme, brand, kind):
    p = profile().model_copy(update={"brand_color_intent": brand})
    from knowledge_product_workflow.presentation import _brand_palette
    p.palette = _brand_palette(brand)
    token = min(SCHEMES[scheme])
    from knowledge_product_workflow.diagrams import DiagramInput
    graph = DiagramInput(diagram_id="matrix", diagram_type=kind, caption="测试", reader_conclusion="说明关系", nodes=[{"node_id":"a","label":"甲"},{"node_id":"b","label":"乙"}], presentation=DiagramPresentationInput(scheme=scheme,node_colors={"a":token,"b":token},group_colors={},concept_nodes={}))
    assert graph.diagram_type.value == kind
    assert resolve_diagram_palette(p, presentation(scheme, token), {})[token] == COLORS[token]


def test_illegal_tokens_and_scheme_conflict():
    with pytest.raises(ValidationError):
        presentation(token="#FFFFFF")
    with pytest.raises(ValueError, match="scheme"):
        resolve_diagram_palette(profile(), presentation("warm-focus", "blue"), {})
    with pytest.raises(ValidationError):
        DiagramPresentationInput(scheme="balanced", node_colors={}, group_colors={},
                                  concept_nodes={}, highlight_nodes=["a", "b"])
