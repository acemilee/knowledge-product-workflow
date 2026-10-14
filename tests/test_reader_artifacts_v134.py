"""语义产物不强加图表，但必须在当前正文中真实存在。"""
import pytest

from knowledge_product_workflow.content_plan import (
    ContentPlanInput,
    ContentPlanSection,
    submit_content_plan,
)
from knowledge_product_workflow.developmental import _findings_for_current_plan
from tests.test_chapter_content_plan import _approved_workspace


def code_section():
    return {"section_id": "1.1", "reader_job": "运行给定检索程序并核对两条排序结果。",
            "opening_mode": "procedure", "block_sequence": ["procedure", "example"],
            "artifact": "可运行的最小检索程序", "artifact_kind": "code", "asset_decision": "none",
            "asset_rationale": "代码和输出直接支持运行核对，不需要额外图表。"}


def test_code_reader_artifact_can_be_declared_without_a_diagram(tmp_path):
    workspace = _approved_workspace(tmp_path)
    payload = ContentPlanInput(sections=[code_section()])
    input_path = workspace.root / "runs/code-plan.json"
    input_path.write_text(payload.model_dump_json(), encoding="utf-8")
    submit_content_plan(workspace, 1, input_path)
    _, findings = _findings_for_current_plan(workspace, 1, "## 1.1 决策框架\n\n```python\nprint(1)\n```\n")
    assert not any(f.severity.value == "blocker" for f in findings)
    _, missing = _findings_for_current_plan(workspace, 1, "## 1.1 决策框架\n\n只有散文，没有可运行的程序。\n")
    assert any(f.code == "DEVELOPMENTAL_ARTIFACT_MISSING" for f in missing)


def test_plain_prose_cannot_claim_a_nonrendered_artifact():
    with pytest.raises(ValueError):
        ContentPlanSection(**(code_section() | {"artifact_kind": None}))


def test_legacy_content_plan_serialization_does_not_gain_empty_fields():
    old = code_section()
    old.pop("artifact_kind")
    old.update(asset_decision="worksheet", block_sequence=["worksheet"])
    assert "artifact_kind" not in ContentPlanSection(**old).model_dump()
