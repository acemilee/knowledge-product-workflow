"""通用前提/运行范围/图义审核协议；不冒充模型语义识错。"""
import json

import pytest

from knowledge_product_workflow.developmental import (
    DevelopmentalEditError,
    StructureReviewInput,
    create_structure_review_template,
    record_structure_review,
)
from knowledge_product_workflow.example_checks import run_python_example
from knowledge_product_workflow.runtime import (
    _atomic_json_write,
    _orchestration_state_path,
    _read_orchestration_state,
)
from tests.test_structure_review_v133 import _fixture, fixture_teaching_checks


def audit_fixture(tmp_path, text="输入 x=2，计算 x*3 得到结果为6。"):
    workspace, source, body = _fixture(tmp_path)
    state = _read_orchestration_state(workspace)
    _atomic_json_write(_orchestration_state_path(workspace), state.model_copy(update={
        "reading_review_rubric_revision": 3, "request_capture_revision": 1}))
    source.write_text(body + "\n" + text + "\n", encoding="utf-8")
    return workspace, source


def audit_payload(template, **changes):
    observations = []
    for observation in template["observations"]:
        checks = []
        for prompt in template["assertion_prompts"]:
            if prompt["unit_id"] != observation["unit_id"]:
                continue
            checks.append({"assertion_id": prompt["assertion_id"], "quote": prompt["quote"],
                "assessment": "supported", "support_refs": ["body_unit:1.1"],
                "missing_premises": [], "note": "回归夹具独立演算：给定2乘3为6，只适用于本次固定输入。",
                "input_status": "sufficient", "inputs": ["x=2"], "operation": "x*3",
                "observed_output": "6", "scope_limit": "固定非概率算例，不推断其他输入",
                "counterexample_status": "none_found", "evidence_kind": "deduction", **changes})
        observations.append({"unit_id": observation["unit_id"], "assessment": "passed",
            "note": "回归协议夹具：仅检验边界字段，不代表真实审稿质量通过。",
            "teaching_checks": fixture_teaching_checks(), "assertion_checks": checks})
    return StructureReviewInput(template_id=template["template_id"], observations=observations)


@pytest.mark.parametrize("text", [
    "未知新的查询输入，却声称得分为0.01。",
    "未给评分权重，却声称加权结果为0.8。",
])
def test_missing_inputs_cannot_sign_supported(tmp_path, text):
    workspace, source = audit_fixture(tmp_path, text)
    template = create_structure_review_template(workspace, 1, source)
    payload = audit_payload(template, input_status="missing", inputs=[])
    with pytest.raises(DevelopmentalEditError, match="前提"):
        record_structure_review(workspace, 1, source, payload)


def test_sufficient_input_and_explicit_scope_round_trip(tmp_path):
    workspace, source = audit_fixture(tmp_path)
    template = create_structure_review_template(workspace, 1, source)
    assert template["assertion_audit_protocol"]["required"] is True
    assert record_structure_review(workspace, 1, source, audit_payload(template))


@pytest.mark.parametrize("changes", [
    {"counterexample_status": "found"}, {"counterexample_status": "uncertain"},
    {"scope_limit": ""}, {"evidence_kind": "actual_execution"},
])
def test_counterexample_scope_and_execution_limits_reject(tmp_path, changes):
    workspace, source = audit_fixture(tmp_path)
    template = create_structure_review_template(workspace, 1, source)
    with pytest.raises(DevelopmentalEditError):
        record_structure_review(workspace, 1, source, audit_payload(template, **changes))


def test_actual_output_must_exist_in_passed_execution(tmp_path):
    workspace, source = audit_fixture(tmp_path, '运行当前脚本，结果为6。\n```python\nprint(6)\n```')
    run_python_example(workspace, source, 1, approved=True)
    template = create_structure_review_template(workspace, 1, source)
    payload = audit_payload(template, evidence_kind="actual_execution", support_refs=["example:0"],
                            observed_output="999")
    with pytest.raises(DevelopmentalEditError, match="输出"):
        record_structure_review(workspace, 1, source, payload)
    payload = audit_payload(template, evidence_kind="actual_execution", support_refs=["example:0"],
                            observed_output="6", operation="print(6)", scope_limit="只实跑打印，不证明完整业务链路")
    assert record_structure_review(workspace, 1, source, payload)


def test_partial_number_is_not_the_actual_output(tmp_path):
    workspace, source = audit_fixture(tmp_path, '运行当前脚本，结果为6。\n```python\nprint(16)\n```')
    run_python_example(workspace, source, 1, approved=True)
    template = create_structure_review_template(workspace, 1, source)
    payload = audit_payload(template, evidence_kind="actual_execution", support_refs=["example:0"], observed_output="6")
    with pytest.raises(DevelopmentalEditError, match="输出"):
        record_structure_review(workspace, 1, source, payload)


@pytest.mark.parametrize("shared_join", [False, True])
def test_two_input_relation_must_exist_in_current_graph(tmp_path, shared_join):
    workspace, source = audit_fixture(tmp_path, '图中的两条流程在共同节点连接。\n![两输入关系](assets/figures/join.pdf)')
    graph = {"diagram_id": "join", "diagram_type": "flow", "caption": "两输入关系",
        "reader_conclusion": "两个输入共同进入结点", "nodes": [
            {"node_id": key, "label": key} for key in ("left", "right", "join")],
        "edges": [{"source": "left", "target": "join"},
                  {"source": "right" if shared_join else "join", "target": "join" if shared_join else "right"}]}
    folder = workspace.root / "runs/diagrams"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "join.input.json").write_text(json.dumps(graph), encoding="utf-8")
    template = create_structure_review_template(workspace, 1, source)
    payload = audit_payload(template, evidence_kind="diagram_semantics", support_refs=["diagram:join"],
        required_relations=[["left", "join"], ["right", "join"]], inputs=["left", "right"],
        operation="读取有向依赖", observed_output="两条输入边", scope_limit="只检查当前图中的边")
    if shared_join:
        assert record_structure_review(workspace, 1, source, payload)
    else:
        with pytest.raises(DevelopmentalEditError, match="关系"):
            record_structure_review(workspace, 1, source, payload)
