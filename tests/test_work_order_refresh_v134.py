"""受控提交衔接真实后继工单，未知变化不静默吞掉。"""

import json

import pytest
import yaml
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.runtime import (
    WorkflowStatus,
    WorkOrderInputChangedError,
    capture_orchestration_action,
    capture_orchestration_update,
    complete_orchestration_action,
    create_orchestration_work_order,
    read_orchestration_work_order,
    refresh_orchestration_after_controlled_update,
    validate_orchestration_work_order,
)
from knowledge_product_workflow.workflows.product_brief import (
    BriefSemanticFields,
    create_product_brief,
)
from knowledge_product_workflow.workspace import Workspace
from tests.test_chapter_content_plan import _approved_workspace, _valid_plan


def test_unknown_external_input_change_requires_explicit_refresh(tmp_path):
    workspace = Workspace.create(tmp_path, "unknown-change")
    brief = create_product_brief(workspace, "外部变化工单核验", semantics=BriefSemanticFields(reader_request="请生成外部变化工单核验教程。"))
    path = create_orchestration_work_order(workspace)
    original = path.read_bytes()
    brief.write_text(brief.read_text(encoding="utf-8") + "\n外部直接追加正文。\n", encoding="utf-8")
    with pytest.raises(WorkOrderInputChangedError, match="refresh"):
        capture_orchestration_action(workspace, "approve")
    assert path.read_bytes() == original


def test_same_stage_completion_cannot_hide_an_unrelated_input_change(tmp_path):
    workspace = Workspace.create(tmp_path, "unrelated-change")
    brief = create_product_brief(workspace, "完成证明边界", semantics=BriefSemanticFields(reader_request="请生成完成证明边界教程。"))
    path = create_orchestration_work_order(workspace)
    order = read_orchestration_work_order(workspace, path)
    brief.write_text(brief.read_text(encoding="utf-8") + "\n未授权外部正文变化。\n", encoding="utf-8")
    output = workspace.root / "runs/unrelated-output.json"
    output.write_text("{}", encoding="utf-8")
    with pytest.raises(WorkOrderInputChangedError):
        complete_orchestration_action(workspace, "approve", proof="测试夹具输出不能证明输入变化",
                                      output_paths=[output], work_order_id=order.work_order_id)


def test_controlled_update_does_not_refresh_unknown_changes_before_or_during_write(tmp_path):
    workspace = Workspace.create(tmp_path, "bounded-update", current_policy=True)
    brief = create_product_brief(workspace, "更新边界", semantics=BriefSemanticFields(reader_request="给进阶工程师写更新边界教程。"))
    order_path = create_orchestration_work_order(workspace)
    snapshot = capture_orchestration_update(workspace)
    request = workspace.root / "runs/request-contract.yaml"
    request.write_text(request.read_text(encoding="utf-8") + "\ncontrolled_fixture_output: true\n", encoding="utf-8")
    brief.write_text(brief.read_text(encoding="utf-8") + "\n更新期间的外部突改。\n", encoding="utf-8")
    with pytest.raises(WorkOrderInputChangedError, match="非输出"):
        refresh_orchestration_after_controlled_update(workspace, "brief-preview", snapshot=snapshot, output_paths=[request])
    assert read_orchestration_work_order(workspace, order_path).lifecycle.value == "pending"
    with pytest.raises(WorkOrderInputChangedError):
        capture_orchestration_update(workspace)


def test_successful_content_plan_submission_refreshes_its_actual_successor(tmp_path, monkeypatch):
    workspace = _approved_workspace(tmp_path)
    target = workspace.root / "chapters/chapter-01-content-plan.yaml"
    # A general-stage handoff consumes the plan. The actual domain submission is
    # real; only routing is held constant to expose the same-stage stale boundary.
    def status(_workspace):
        return WorkflowStatus(stage="quality_validation_pending", recommended_skill="kp-quality-editor",
            next_command="kp validate", artifacts={"chapters/chapter-01-content-plan.yaml": "present" if target.is_file() else "missing"})
    monkeypatch.setattr("knowledge_product_workflow.runtime.inspect_workflow", status)
    original = create_orchestration_work_order(workspace)
    supplied = workspace.root / "runs/plan-input.yaml"
    supplied.write_text(yaml.safe_dump(_valid_plan(workspace), allow_unicode=True), encoding="utf-8")
    result = CliRunner().invoke(app, ["submit-content-plan", "1", "--workspace", str(workspace.root), "--from", str(supplied)])
    assert result.exit_code == 0, result.output
    assert read_orchestration_work_order(workspace, original).lifecycle.value == "superseded"
    successor = create_orchestration_work_order(workspace)
    validate_orchestration_work_order(workspace, read_orchestration_work_order(workspace, successor).work_order_id, command="validate")


def test_work_order_exposes_actions_and_keeps_request_contract_in_snapshot(tmp_path):
    workspace = _approved_workspace(tmp_path)
    request = workspace.root / "runs/request-contract.yaml"
    request.write_text("reader_request: 测试夹具的原始需求\n", encoding="utf-8")
    path = create_orchestration_work_order(workspace)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["actions"] and data["actions"][0]["argv"]
    assert data["command_discovery"] == ["kp", "commands", "--all", "--json"]
    assert any(item["path"] == "runs/request-contract.yaml" for item in data["inputs"])
    request.write_text("reader_request: 未知外部修改的原始需求\n", encoding="utf-8")
    with pytest.raises(WorkOrderInputChangedError):
        capture_orchestration_action(workspace, "draft", chapter_number=1)


def test_only_explicit_comparison_with_33_invalidates_42_after_label_change(tmp_path):
    from knowledge_product_workflow.content_plan import submit_content_plan
    from knowledge_product_workflow.developmental import (
        StructureReviewInput,
        create_structure_review_template,
        record_structure_review,
    )
    from knowledge_product_workflow.runtime import (
        _atomic_json_write,
        _orchestration_state_path,
        _read_orchestration_state,
    )
    from knowledge_product_workflow.workflows.authoring import compile_chapter_contracts
    from tests.blueprint_support import submit_test_blueprint
    from tests.test_orchestration import _approve
    from tests.test_structure_review_v133 import fixture_teaching_checks

    workspace = Workspace.create(tmp_path, "comparison-binding")
    # The reported production case used rubric 2; keep that exact version while
    # testing its local comparison proof, independently of new rubric 3 policy.
    state = _read_orchestration_state(workspace).model_copy(update={"reading_review_rubric_revision": 2})
    _atomic_json_write(_orchestration_state_path(workspace), state)
    brief = create_product_brief(workspace, "局部比较绑定回归", semantics=BriefSemanticFields(reader_request="请生成局部比较绑定回归教程。"))
    _approve(brief)
    outlines = tuple([{ "number": f"{n}.{i}", "title": f"判断{i}", "children": []}
                      for i in range(1, 4)] for n in range(1, 5))
    blueprint = submit_test_blueprint(workspace, brief, titles=("基础", "场景", "标签", "比较"), section_outlines=outlines)
    _approve(blueprint)
    compile_chapter_contracts(workspace)
    from knowledge_product_workflow.presentation import load_or_create_publication_profile
    load_or_create_publication_profile(workspace, language="zh-CN")
    for number in (3, 4):
        supplied = workspace.root / f"runs/plan-{number}.yaml"
        supplied.write_text(yaml.safe_dump({"sections": [
            {"section_id": f"{number}.{i}", "reader_job": f"根据第{i}类条件写出可执行的取舍判断。",
             "opening_mode": "decision", "block_sequence": ["decision", "comparison"],
             "artifact": f"第{i}类判断记录", "artifact_kind": "decision", "asset_decision": "none",
             "asset_rationale": "此判断可直接按当前条件核对，无需额外图。"} for i in range(1, 4)
        ]}, allow_unicode=True), encoding="utf-8")
        submit_content_plan(workspace, number, supplied)
        chapter = workspace.root / f"chapters/chapter-{number:02d}.md"
        chapter.write_text(f"# {number}. 当前章\n\n章导读。\n\n" + "\n\n".join(
            f"## {number}.{i} 判断{i}\n\n按条件{i}保留结果，并写出可检查的依据。" for i in range(1, 4)) + "\n", encoding="utf-8")
    source = workspace.root / "chapters/chapter-04.md"
    template = create_structure_review_template(workspace, 4, source)
    record_structure_review(workspace, 4, source, StructureReviewInput(template_id=template["template_id"], observations=[
        {"unit_id": f"4.{i}", "assessment": "passed", "note": "测试夹具：对照当前条件与输出，判断过程和可执行依据完整。",
         "teaching_checks": fixture_teaching_checks(), "compared_unit_ids": ["3.3"] if i == 2 else []}
        for i in range(1, 4)]))
    assert create_structure_review_template(workspace, 4, source)["reusable_unit_ids"] == ["4.1", "4.2", "4.3"]
    chapter3 = workspace.root / "chapters/chapter-03.md"
    chapter3.write_text(chapter3.read_text(encoding="utf-8").replace("## 3.3 判断3", "## 3.3 新标签"), encoding="utf-8")
    template = create_structure_review_template(workspace, 4, source)
    assert template["reusable_unit_ids"] == ["4.1", "4.3"]
    with pytest.raises(ValueError, match="覆盖"):
        record_structure_review(workspace, 4, source, StructureReviewInput(template_id=template["template_id"],
            observations=[], reuse_unit_ids=["4.1", "4.2", "4.3"]))
