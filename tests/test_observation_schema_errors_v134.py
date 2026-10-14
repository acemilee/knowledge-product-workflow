"""观察到的填写错误：公开约束与当前单元冲突诊断，不代替实际审核。"""

import json

import pytest
import yaml
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.developmental import (
    DevelopmentalEditError,
    StructureObservation,
    StructureReviewInput,
    _validate_teaching_checks,
    create_structure_review_template,
    record_structure_review,
    structure_review_path,
)
from knowledge_product_workflow.illustration_tasks import RELATION_FAMILIES, IllustrationTask
from knowledge_product_workflow.workflows.curriculum import (
    create_curriculum_blueprint_template,
    curriculum_outline_schema,
)
from tests.test_illustration_policy import approved_workspace, task
from tests.test_structure_review_v133 import _fixture, fixture_teaching_checks


def test_public_relation_constraints_match_all_validator_combinations():
    result = CliRunner().invoke(app, ["outline-schema", "--json"])
    assert result.exit_code == 0, result.output
    schema = json.loads(result.output)
    rules = schema["illustration_tasks"]["relation_constraints"]
    assert rules["applies_when"] == {"production": "create", "relation_kind": "declared"}
    assert set(rules["families"]) == set(RELATION_FAMILIES)
    diagram_types = schema["illustration_tasks"]["item_schema"]["properties"]["diagram_type"]["anyOf"][0]["enum"]
    for relation, (family, recommendation) in RELATION_FAMILIES.items():
        public = rules["families"][relation]
        assert public["diagram_type"] == family
        assert public["recommended_direction"] == recommendation
        assert public["allowed_directions"] == ([None, "right"] if relation == "handoff" else [None, "right", "down"])
        for diagram in diagram_types:
            for direction in (None, "right", "down"):
                supplied = task(relation_kind=relation, diagram_type=diagram, direction=direction)
                valid = diagram == family and direction in public["allowed_directions"]
                if valid:
                    parsed = IllustrationTask.model_validate(supplied)
                    assert parsed.direction == direction
                else:
                    with pytest.raises(ValueError):
                        IllustrationTask.model_validate(supplied)


def test_generated_blueprint_template_publishes_same_mapping_and_direction_scope(tmp_path):
    workspace = approved_workspace(tmp_path, [task()])
    path = create_curriculum_blueprint_template(workspace, workspace.root / "planning/product-brief.md")
    text = path.read_text(encoding="utf-8")
    assert "production=create 且声明 relation_kind" in text
    assert "只有 handoff 的 direction 硬限为未填或 right" in text
    assert "其他关系的方向只是建议" in text
    for relation, (family, direction) in RELATION_FAMILIES.items():
        assert f"{relation} -> diagram_type: {family}；建议 direction: {direction}" in text
    # 说明是 YAML 注释；旧字段与模板输入结构保持。
    value = yaml.safe_load(text)
    assert value["chapters"][0]["illustration_tasks"] == [
        {"section_id": "<章节号.1>", "decision": "none",
         "alternative_rationale": "<说明本节为何不需要图以及采用的表达形式>"}
    ]


def test_relation_mapping_does_not_restrict_legacy_reference_or_omit_tasks():
    none = IllustrationTask.model_validate(curriculum_outline_schema()["illustration_tasks"]["none_example"])
    assert none.production == "omit" and none.relation_kind is None
    for production, extra in (("reference", {"reference_task_id": "existing-map"}), ("omit", {})):
        parsed = IllustrationTask.model_validate(task(
            decision="useful", production=production, relation_kind="handoff",
            diagram_type="flow", direction="down", **extra))
        assert (parsed.production, parsed.diagram_type, parsed.direction) == (production, "flow", "down")
    for diagram in ("flow", "layered_architecture", "comparison", "sequence", "lifecycle",
                    "nested_structure", "card_matrix", "stage_flow"):
        for direction in (None, "right", "down"):
            parsed = IllustrationTask.model_validate(task(diagram_type=diagram, direction=direction))
            assert parsed.relation_kind is None and parsed.direction == direction
    with pytest.raises(ValueError, match="不画不声明"):
        IllustrationTask.model_validate({"section_id": "1.1", "decision": "none",
                                        "alternative_rationale": "有序文字步骤足以表达当前操作",
                                        "relation_kind": "handoff"})


@pytest.mark.parametrize("assessment,changes,expected", [
    ("passed", {"repetition_value": "revise", "actionability": "uncertain"}, "revise"),
    ("passed", {"representation_fit": "uncertain"}, "uncertain"),
    ("uncertain", {"reading_hierarchy": "revise"}, "revise"),
    ("revise", {"complementarity": "not_applicable"}, "passed"),
])
def test_summary_conflict_identifies_unit_actual_expected_and_negative_fields(tmp_path, assessment, changes, expected):
    workspace, source, _ = _fixture(tmp_path)
    template = create_structure_review_template(workspace, 1, source)
    checks = fixture_teaching_checks()
    for key, status in changes.items():
        checks[key]["assessment"] = status
    payload = StructureReviewInput(template_id=template["template_id"], observations=[
        {"unit_id": "1.1", "assessment": assessment, "teaching_checks": checks,
         "note": "机器诊断夹具：仅验证当前字段冲突定位，不代表实际教学质量判断。"}
    ])
    before = payload.model_dump(mode="json")
    source_before = source.read_bytes()
    with pytest.raises(DevelopmentalEditError) as caught:
        record_structure_review(workspace, 1, source, payload)
    message = str(caught.value)
    assert "总体判断必须与六项教学检查一致" in message
    assert "unit_id=1.1" in message
    assert f"提交assessment={assessment}" in message
    assert f"期望assessment={expected}" in message
    negatives = {key: status for key, status in changes.items() if status in {"revise", "uncertain"}}
    assert json.loads(message.split("负项名称/状态=", 1)[1]) == negatives
    assert payload.model_dump(mode="json") == before
    assert source.read_bytes() == source_before
    assert not structure_review_path(workspace, 1).exists()


@pytest.mark.parametrize("assessment,changes", [
    ("passed", {"complementarity": "not_applicable"}),
    ("revise", {"repetition_value": "revise", "actionability": "uncertain"}),
    ("uncertain", {"representation_fit": "uncertain"}),
])
def test_consistent_summary_and_legacy_rubric_keep_author_input(assessment, changes):
    checks = fixture_teaching_checks()
    for key, status in changes.items():
        checks[key]["assessment"] = status
    observation = StructureObservation(unit_id="3.2", assessment=assessment,
                                       teaching_checks=checks,
                                       note="机器诊断夹具：核对规则和输入不变，不作实际审核结论。")
    before = observation.model_dump(mode="json")
    _validate_teaching_checks(observation, 2)
    assert observation.model_dump(mode="json") == before
    observation.assessment = "passed" if assessment != "passed" else "revise"
    legacy_before = observation.model_dump(mode="json")
    _validate_teaching_checks(observation, 1)
    assert observation.model_dump(mode="json") == legacy_before
