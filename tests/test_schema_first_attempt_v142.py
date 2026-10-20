"""首轮取得真实类型和命令用法，不用创建书稿试错。"""

import json

from pydantic import ValidationError
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.workflows.curriculum import (
    BlueprintSubmission,
    _template_schema_comments,
    curriculum_outline_schema,
)


def test_outline_schema_exposes_actual_nested_case_value_type():
    schema = curriculum_outline_schema()["submission_schema"]
    assert schema == BlueprintSubmission.model_json_schema()
    cases = schema["$defs"]["SharedContext"]["properties"]["cases"]
    assert cases["type"] == "object"
    assert cases["additionalProperties"]["type"] == "object"
    assert "production_exports" in schema["$defs"]["BlueprintChapter"]["properties"]
    assert "word_target" not in schema["$defs"]["BlueprintChapter"]["properties"]


def test_valid_real_blueprint_accepts_case_object_first_attempt():
    payload = {
        "brief_sha256": "a" * 64, "brief_version": 1, "topic": "资料分类方法",
        "target_audience": "初学者", "audience_level": "beginner", "production_policy_revision": 1,
        "shared_context": {"cases": {"case-main": {"input": "一条资料", "expected": "有效"}}},
        "chapters": [{"chapter_number": 1, "title": "分类方法", "learning_objectives": ["独立分类"],
            "scope": "判定资料是否可用", "weight": 1, "rationale": "先实践后解释", "chapter_archetype": "concept",
            "section_outline": [{"number": "1.1", "title": "首次分类"}],
            "production_dependencies": [], "production_exports": []}],
    }
    result = BlueprintSubmission.model_validate(payload)
    assert result.shared_context.cases["case-main"]["input"] == "一条资料"
    payload["shared_context"]["cases"]["case-main"] = "不是对象"
    try:
        BlueprintSubmission.model_validate(payload)
    except ValidationError as error:
        assert "shared_context.cases.case-main" in str(error)
    else:
        raise AssertionError("实际模型不应接收字符串cases值")
    assert "cases" in _template_schema_comments() and "对象" in _template_schema_comments()


def test_exact_command_metadata_says_observation_template_is_already_json():
    result = CliRunner().invoke(app, ["commands", "--all", "--command", "pdf-observation-template", "--json"])
    assert result.exit_code == 0, result.output
    entries = json.loads(result.output)["commands"]
    assert len(entries) == 1 and entries[0]["output_format"] == "json"
    assert not entries[0]["supports_json_flag"]
    assert not any("--json" in p["flags"] for p in entries[0]["parameters"])


def test_unsupported_json_flag_stops_before_opening_workspace(monkeypatch):
    calls = []
    monkeypatch.setattr("knowledge_product_workflow.cli._open_workspace", lambda *_: calls.append("open"))
    result = CliRunner().invoke(app, ["pdf-observation-template", "--workspace", ".", "--json"])
    assert result.exit_code == 2 and "--json" in result.output
    assert calls == []
