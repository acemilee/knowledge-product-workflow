"""读者任务与代码呈现分离，旧书不因新字段缺失改变。"""
import pytest
from pydantic import ValidationError

from knowledge_product_workflow.content_plan import (
    CodeDeliveryPlan,
    ContentPlanError,
    ContentPlanInput,
    ContentPlanSection,
    _validate_code_delivery_policy,
)
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.workflows.product_brief import (
    BriefSemanticFields,
    brief_contract_fingerprints,
    parse_brief_semantics,
    render_product_brief_body,
)


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_teaching_focus_survives_reviewable_brief_roundtrip_and_changes_content_binding(language):
    constraints = DetailConstraints(language=language)
    body = render_product_brief_body("方法实践", "初学者", constraints, None,
                                    BriefSemanticFields(teaching_focus="method_practice"))
    assert parse_brief_semantics(body).teaching_focus == "method_practice"
    other = body.replace("method_practice", "implementation")
    assert brief_contract_fingerprints(body)["content_contract_sha256"] != brief_contract_fingerprints(other)["content_contract_sha256"]
    assert brief_contract_fingerprints(body)["delivery_contract_sha256"] == brief_contract_fingerprints(other)["delivery_contract_sha256"]


def test_legacy_brief_and_plan_do_not_gain_new_fields():
    assert "teaching_focus" not in BriefSemanticFields().model_dump()
    section = ContentPlanSection(section_id="1.1", reader_job="理解证据支持与格式正确的区别。",
        opening_mode="claim", block_sequence=["explanation"], artifact="none",
        asset_decision="none", asset_rationale="用短对照说明。")
    assert "code_delivery" not in section.model_dump()


def test_prompt_artifact_does_not_require_a_program():
    section = ContentPlanSection(section_id="1.1", reader_job="写出完整提示并逐项核对回答依据。",
        opening_mode="procedure", block_sequence=["example"], artifact="可迁移的提示",
        artifact_kind="prompt", asset_decision="none", asset_rationale="直接给输入与示范。")
    assert section.code_delivery is None


@pytest.mark.parametrize("path", ["../main.py", "assets/../main.py", "C:/main.py",
    "assets\\main.py", "/assets/main.py", "assets/./main.py", "assets//main.py"])
def test_resource_paths_have_one_canonical_workspace_asset_spelling(path):
    with pytest.raises(ValidationError):
        CodeDeliveryPlan(mode="resource_only", resource_path=path, reason="完整运行器供选择阅读。")


def test_resource_mode_requires_real_mapping_and_ordered_integer_ranges():
    valid = {"mode": "excerpt_with_resource", "resource_path": "assets/examples/main.py",
             "input_files": ["assets/examples/input.json"], "reason": "正文只讲证据过滤这一步。",
             "excerpts": [{"body_fence_index": 1, "start_line": 3, "end_line": 6}]}
    assert CodeDeliveryPlan(**valid).excerpts[0].end_line == 6
    for bad in [{"excerpts": []}, {"input_files": ["assets/examples/input.json"] * 2},
                {"excerpts": [{"body_fence_index": True, "start_line": 3, "end_line": 6}]},
                {"excerpts": [{"body_fence_index": 1, "start_line": 6, "end_line": 3}]}]:
        with pytest.raises(ValidationError):
            CodeDeliveryPlan(**(valid | bad))
    with pytest.raises(ValidationError):
        CodeDeliveryPlan(mode="resource_only", reason="没有资源不能只写下载说明。")
    with pytest.raises(ValidationError):
        CodeDeliveryPlan(mode="inline_complete", resource_path="assets/main.py", reason="内嵌不隐式重复资源。")


def test_legacy_book_requires_explicit_adoption_and_new_book_requires_code_placement(tmp_path):
    from knowledge_product_workflow.workflows.product_brief import create_product_brief
    from knowledge_product_workflow.workspace import Workspace
    workspace = Workspace.create(tmp_path, "guide")
    section = {"section_id": "1.1", "reader_job": "运行过滤并说明未知字段保留的原因。",
        "opening_mode": "procedure", "block_sequence": ["example"],
        "artifact": "过滤程序", "artifact_kind": "code", "asset_decision": "none",
        "asset_rationale": "只看关键过滤关系。"}
    legacy = ContentPlanInput(sections=[section])
    _validate_code_delivery_policy(workspace, legacy)
    placed = ContentPlanInput(sections=[section | {"code_delivery": {
        "mode": "inline_complete", "reason": "本节教学就是完整实现这个函数。"}}])
    with pytest.raises(ContentPlanError, match="显式确认"):
        _validate_code_delivery_policy(workspace, placed)
    create_product_brief(workspace, "方法课", DetailConstraints(), target_audience="初学者",
                         semantics=BriefSemanticFields(teaching_focus="method_practice"))
    with pytest.raises(ContentPlanError, match="须说明"):
        _validate_code_delivery_policy(workspace, legacy)
    _validate_code_delivery_policy(workspace, placed)
