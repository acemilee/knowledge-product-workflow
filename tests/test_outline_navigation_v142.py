"""目录意图独立于措辞；结构合法不冒充语义已通过。"""
import copy
import hashlib
import json

import pytest
from pydantic import ValidationError

from knowledge_product_workflow.workflows.curriculum import (
    BlueprintSubmission,
    curriculum_outline_schema,
)


def legacy_payload():
    return {
        "schema_version": 2, "brief_sha256": "a" * 64, "brief_version": 1,
        "topic": "专业教程", "target_audience": "工程师", "audience_level": "intermediate",
        "chapters": [{"chapter_number": 1, "title": "评测设计", "scope": "评测集与评分器。",
            "weight": 1, "rationale": "建立可复核的评测方法。", "chapter_archetype": "strategy",
            "learning_objectives": ["设计评测集及评分器。"],
            "section_outline": [{"number": "1.1", "title": "评分器", "children": []}]}],
    }


def modern_payload():
    value = legacy_payload()
    value.update(navigation_policy_revision=1, title_style={"mode": "topic", "vocabulary": [], "examples": []})
    value["chapters"][0]["navigation"] = {
        "focus": "评测设计", "topics": ["评测集", "评分器"], "reader_job": "设计评测集和评分器并解释判定口径。"}
    value["chapters"][0]["section_outline"][0]["navigation"] = {
        "focus": "评分器", "topics": ["评分口径", "结果判定"], "reader_job": "为固定答案建立可复核的评分规则。"}
    return value


def test_new_policy_requires_style_and_every_heading_intent():
    for mutation in ("style", "chapter", "section"):
        value = modern_payload()
        if mutation == "style":
            value.pop("title_style")
        elif mutation == "chapter":
            value["chapters"][0].pop("navigation")
        else:
            value["chapters"][0]["section_outline"][0].pop("navigation")
        with pytest.raises(ValidationError, match="导航|标题|风格|意图"):
            BlueprintSubmission.model_validate(value)


@pytest.mark.parametrize("field,value", [("focus", " "), ("topics", []), ("reader_job", " "),
                                        ("topics", ["评分口径", "评分口径"])])
def test_incomplete_or_duplicate_intent_never_passes(field, value):
    payload = modern_payload()
    payload["chapters"][0]["navigation"][field] = value
    with pytest.raises(ValidationError):
        BlueprintSubmission.model_validate(payload)


def test_policy_cannot_be_removed_while_new_fields_remain():
    payload = modern_payload()
    payload.pop("navigation_policy_revision")
    with pytest.raises(ValidationError, match="政策|导航"):
        BlueprintSubmission.model_validate(payload)


def test_legacy_serialization_adds_no_new_fields():
    value = BlueprintSubmission.model_validate(legacy_payload()).model_dump(mode="json")
    assert "navigation_policy_revision" not in value and "title_style" not in value
    assert "navigation" not in value["chapters"][0]
    assert "navigation" not in value["chapters"][0]["section_outline"][0]
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert digest == LEGACY_DIGEST


def test_real_schema_exposes_style_intent_and_semantic_boundary():
    schema = curriculum_outline_schema()
    properties = schema["submission_schema"]["properties"]
    assert "title_style" in properties and "navigation_policy_revision" in properties
    assert schema["heading_navigation"]["automatic_semantic_approval"] is False
    assert "topics" in schema["submission_schema"]["$defs"]["HeadingIntent"]["properties"]


def test_necessary_comparison_and_long_term_are_legal():
    payload = modern_payload()
    payload["chapters"][0]["title"] = "Retrieval-Augmented Generation 与领域微调的适用条件比较"
    assert BlueprintSubmission.model_validate(payload).chapters[0].title == payload["chapters"][0]["title"]


def test_new_data_is_preserved_without_inventing_a_semantic_pass():
    payload = modern_payload()
    result = BlueprintSubmission.model_validate(copy.deepcopy(payload)).model_dump(mode="json")
    assert result["title_style"] == payload["title_style"]
    assert result["chapters"][0]["navigation"] == payload["chapters"][0]["navigation"]
    payload["title_style"]["automatic_semantic_approval"] = True
    with pytest.raises(ValidationError):
        BlueprintSubmission.model_validate(payload)


LEGACY_DIGEST = "df90456367c575139e41338982b823637e7bc6c2a39c82e0c8a86c113db6df42"
