"""教学取向派生既有作者/编辑指引，不增加角色或给所有主题相同配额。"""
import pytest

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.reader_artifacts import reader_presentation_guidance
from knowledge_product_workflow.workflows.product_brief import (
    BriefSemanticFields,
    create_product_brief,
)
from knowledge_product_workflow.workspace import Workspace


@pytest.mark.parametrize("focus", ["method_practice", "implementation", "mixed"])
def test_author_and_editor_receive_one_active_presentation_rule(tmp_path, focus):
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "教学主题", DetailConstraints(), target_audience="目标读者",
                         semantics=BriefSemanticFields(teaching_focus=focus))
    guidance = reader_presentation_guidance(workspace)
    assert guidance["teaching_focus"] == focus
    assert guidance["primary_guidance"]
    assert guidance["program_review_threshold_lines"] == 40
    assert "不是硬截断" in guidance["threshold_policy"] or "只定位" in guidance["threshold_policy"]
    assert "提示与相关输入输出相邻" in guidance["reading_rhythm"]
    assert "不强制" in guidance["reading_rhythm"]


def test_old_book_does_not_gain_presentation_policy_by_software_upgrade(tmp_path):
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "旧书", DetailConstraints(), target_audience="读者")
    assert reader_presentation_guidance(workspace) == {}


def test_program_totals_cannot_be_hidden_by_splitting_fences():
    from knowledge_product_workflow.reader_artifacts import reader_presentation_metrics
    payload = "\n".join(f"value_{n} = {n}" for n in range(50)) + "\n"
    one = reader_presentation_metrics(f"```python\n{payload}```\n")
    two = reader_presentation_metrics("```python\n" + "\n".join(payload.splitlines()[:25])
        + "\n```\n\n```python\n" + "\n".join(payload.splitlines()[25:]) + "\n```\n")
    assert one["display_nonblank_lines"]["program"] == two["display_nonblank_lines"]["program"] == 50
    assert one["aggregate_program_display_over_40"] and two["aggregate_program_display_over_40"]
    assert sum(f["python_logical_lines"] for f in two["program_fences"]) == 50


def test_prompts_data_and_commands_are_visible_but_not_treated_as_programs():
    from knowledge_product_workflow.reader_artifacts import reader_presentation_metrics
    body = ('```prompt\n把以下资料分类，返回JSON。\n```\n\n```json\n{"status":"current"}\n```\n'
            '\n```powershell\npython examples/main.py\n```\n\n```python\nvalues = [\n    1,\n    2,\n]\n```\n')
    result = reader_presentation_metrics(body)
    assert result["fence_counts"] == {"program": 1, "prompt_or_text": 1, "data": 1, "command": 1, "unclassified": 0}
    assert result["display_nonblank_lines"]["program"] == 4
    assert result["program_fences"][0]["python_logical_lines"] == 1
    assert not result["aggregate_program_display_over_40"]


def test_unknown_or_unclosed_payloads_do_not_claim_valid_program_syntax():
    from knowledge_product_workflow.reader_artifacts import reader_presentation_metrics
    unknown = reader_presentation_metrics("```\npython-like content\n```\n")
    assert unknown["fence_counts"]["unclassified"] == 1
    broken = reader_presentation_metrics("```python\nvalue = [\n")
    assert broken["program_fences"][0]["python_logical_lines"] is None
    assert not broken["program_fences"][0]["closed"]
