"""当前正文的前提定位与引用绑定；不模拟真实语义审核通过。"""

import json
from hashlib import sha256

import pytest

from knowledge_product_workflow.developmental import (
    DevelopmentalEditError,
    StructureObservation,
    StructureReviewInput,
    _assertion_support_context,
    _reader_body,
    _validate_assertion_checks,
    create_structure_review_template,
    reading_units,
    record_structure_review,
    require_structure_review,
)
from knowledge_product_workflow.runtime import (
    _atomic_json_write,
    _orchestration_state_path,
    _read_orchestration_state,
)
from tests.test_structure_review_v133 import _fixture, fixture_teaching_checks


def scenario_fixture(tmp_path):
    workspace, source, body = _fixture(tmp_path)
    state = _read_orchestration_state(workspace)
    _atomic_json_write(_orchestration_state_path(workspace),
                       state.model_copy(update={"reading_review_rubric_revision": 3}))
    source.write_text(body + "\n情景前提：q=(1,0)，d=(0,0)。\n\n"
                      "余弦分母为0，因此给定前提下原公式没有定义；先检查长度，再进入回退分支。\n",
                      encoding="utf-8")
    return workspace, source, create_structure_review_template(workspace, 1, source)


def scenario_payload(template, ref="body_unit:1.1"):
    return StructureReviewInput(template_id=template["template_id"], observations=[
        {"unit_id": item["unit_id"], "assessment": "passed",
         "note": "机器回归夹具：仅核当前引用绑定和拒绝边界，不代表独立语义判断。",
         "teaching_checks": fixture_teaching_checks(),
         "assertion_checks": [
             {"assertion_id": prompt["assertion_id"], "quote": prompt["quote"],
              "assessment": "supported", "support_refs": [ref], "missing_premises": [],
              "note": "机器引用夹具：给定q与d为前提，长度乘积为0，不能相除；这不是程序实跑证明。"}
             for prompt in template["assertion_prompts"] if prompt["unit_id"] == item["unit_id"]]}
        for item in template["observations"]])


def test_body_unit_context_has_current_lines_hashes_and_limited_evidence_kind(tmp_path):
    workspace, source, template = scenario_fixture(tmp_path)
    body = _reader_body(source)
    units = reading_units(body)
    contexts = {item["ref"]: item for item in template["assertion_support_context"]}
    assert {"contract", "content_plan", "body_unit:1.1"} <= contexts.keys()
    current = contexts["body_unit:1.1"]
    assert current["kind"] == "author_scenario_and_derivation_context"
    assert current["unit_id"] == "1.1"
    assert current["start_line"] == units[0]["start_line"]
    assert current["end_line"] == len(body.splitlines())
    assert current["unit_sha256"] == units[0]["unit_sha256"]
    assert current["chapter_body_sha256"] == sha256(body.encode()).hexdigest()
    assert "不是外部事实或实际执行证据" in current["evidence_scope"]
    assert "命题原话不能作为自身证明" in current["evidence_scope"]
    assert not {"value", "body", "quote", "text"}.intersection(current)
    assert body not in json.dumps(current, ensure_ascii=False)


def test_body_reference_round_trip_preserves_reviewer_input_and_body(tmp_path):
    workspace, source, template = scenario_fixture(tmp_path)
    payload = scenario_payload(template)
    before = payload.model_dump(mode="json")
    source_before = source.read_bytes()
    output = record_structure_review(workspace, 1, source, payload)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["observations"] == before["observations"]
    assert payload.model_dump(mode="json") == before
    assert source.read_bytes() == source_before
    assert require_structure_review(workspace, 1, _reader_body(source))


@pytest.mark.parametrize("ref", ["body_unit:9.9", "unit:1.1", "body_unit:1.1@old-sha", "example:unknown"])
def test_unknown_and_old_alias_references_still_reject(tmp_path, ref):
    workspace, source, template = scenario_fixture(tmp_path)
    with pytest.raises(DevelopmentalEditError, match="支撑引用"):
        record_structure_review(workspace, 1, source, scenario_payload(template, ref))


def test_changed_body_invalidates_old_template_and_recorded_review(tmp_path):
    workspace, source, template = scenario_fixture(tmp_path)
    payload = scenario_payload(template)
    record_structure_review(workspace, 1, source, payload)
    source.write_text(source.read_text(encoding="utf-8").replace("d=(0,0)", "d=(1,0)"), encoding="utf-8")
    current = create_structure_review_template(workspace, 1, source)
    assert current["template_id"] != template["template_id"]
    old_ref = next(item for item in template["assertion_support_context"] if item["ref"] == "body_unit:1.1")
    new_ref = next(item for item in current["assertion_support_context"] if item["ref"] == "body_unit:1.1")
    assert old_ref["unit_sha256"] != new_ref["unit_sha256"]
    assert old_ref["chapter_body_sha256"] != new_ref["chapter_body_sha256"]
    with pytest.raises(DevelopmentalEditError, match="模板已过期"):
        record_structure_review(workspace, 1, source, payload)
    with pytest.raises(DevelopmentalEditError, match="过期"):
        require_structure_review(workspace, 1, _reader_body(source))
    assert current["reusable_unit_ids"] == []


def test_body_index_bounds_ignore_fenced_headings_and_do_not_repeat_content(tmp_path):
    workspace, source, _ = scenario_fixture(tmp_path)
    body = ("## 1.1 条件\n\n情景的给定前提。\n\n```markdown\n## 9.9 代码标题\n```\n\n"
            "## 1.2 演算\n\n余弦分母为0，因此原公式没有定义。\n")
    contexts = [item for item in _assertion_support_context(workspace, body)
                if item["ref"].startswith("body_unit:")]
    assert [item["ref"] for item in contexts] == ["body_unit:1.1", "body_unit:1.2"]
    assert [(item["start_line"], item["end_line"]) for item in contexts] == [(1, 8), (9, 11)]
    assert all(item["chapter_body_sha256"] == sha256(body.encode()).hexdigest() for item in contexts)


@pytest.mark.parametrize("failure", ["wrong_quote", "missing_premise", "unsupported_passed", "uncertain_passed"])
def test_body_reference_does_not_bypass_existing_assertion_rejections(tmp_path, failure):
    workspace, source, template = scenario_fixture(tmp_path)
    payload = scenario_payload(template)
    check = payload.observations[0].assertion_checks[0]
    if failure == "wrong_quote":
        check.quote = "与当前原话不符的另一命题。"
    elif failure == "missing_premise":
        check.missing_premises = ["当前缺少必要长度前提"]
    else:
        check.assessment = "unsupported" if failure == "unsupported_passed" else "uncertain"
        check.missing_premises = ["当前前提不充分或存在未满足条件"]
    with pytest.raises(DevelopmentalEditError):
        record_structure_review(workspace, 1, source, payload)


@pytest.mark.parametrize("ref", ["contract", "content_plan"])
def test_legacy_support_references_remain_accepted(tmp_path, ref):
    workspace, source, template = scenario_fixture(tmp_path)
    assert record_structure_review(workspace, 1, source, scenario_payload(template, ref))


def test_rubric_two_does_not_upgrade_or_validate_assertions(tmp_path):
    workspace, source, _ = scenario_fixture(tmp_path)
    state = _read_orchestration_state(workspace)
    _atomic_json_write(_orchestration_state_path(workspace), state.model_copy(update={"reading_review_rubric_revision": 2}))
    template = create_structure_review_template(workspace, 1, source)
    assert template["binding"]["rubric_revision"] == 2
    assert template["assertion_prompts"] == template["assertion_support_context"] == []
    observation = StructureObservation(unit_id="1.1", assessment="passed",
        note="机器回归夹具：保持旧Rubric行为，不升级或补签语义审核。",
        teaching_checks=fixture_teaching_checks(), assertion_checks=[])
    before = observation.model_dump(mode="json")
    _validate_assertion_checks(workspace, observation, _reader_body(source), 2)
    assert observation.model_dump(mode="json") == before


def cross_unit_fixture(tmp_path, ref="body_unit:1.3"):
    workspace, source, _ = scenario_fixture(tmp_path)
    source.write_text("## 1.1 判断\n\n余弦分母为0，因此原公式没有定义。\n\n"
                      "## 1.2 相邻解释\n\n先明确处理的前提，再做判断。\n\n"
                      "## 1.3 给定条件\n\n给定q=(1,0)，d=(0,0)。\n\n"
                      "## 1.4 独立内容\n\n记录这项无关的独立操作结果。\n", encoding="utf-8")
    template = create_structure_review_template(workspace, 1, source)
    payload = scenario_payload(template, ref)
    output = record_structure_review(workspace, 1, source, payload)
    return workspace, source, template, payload, output


def test_explicit_referenced_unit_change_rejects_reuse_without_fake_comparison(tmp_path):
    workspace, source, template, payload, output = cross_unit_fixture(tmp_path)
    saved = json.loads(output.read_text(encoding="utf-8"))
    referenced = next(unit for unit in template["units"] if unit["unit_id"] == "1.3")
    assert saved["assertion_body_refs"] == {"1.1": {"body_unit:1.3": referenced["unit_sha256"]}}
    assert saved["comparison_context"] == {}
    assert saved["observations"] == payload.model_dump(mode="json")["observations"]
    assert "1.1" in create_structure_review_template(workspace, 1, source)["reusable_unit_ids"]
    source.write_text(source.read_text(encoding="utf-8").replace("d=(0,0)", "d=(1,0)"), encoding="utf-8")
    current = create_structure_review_template(workspace, 1, source)
    assert "1.1" not in current["reusable_unit_ids"]
    # 当前命题、相邻单元、普通段落依赖未变；失效来自显式正文引用。
    before_unit = next(unit for unit in template["units"] if unit["unit_id"] == "1.1")
    after_unit = next(unit for unit in current["units"] if unit["unit_id"] == "1.1")
    assert before_unit["context_sha256"] == after_unit["context_sha256"]
    assert before_unit["dependency_sha256"] == after_unit["dependency_sha256"]


def test_unreferenced_distant_change_preserves_body_reference_reuse(tmp_path):
    workspace, source, _, _, _ = cross_unit_fixture(tmp_path)
    source.write_text(source.read_text(encoding="utf-8").replace("无关的独立操作结果", "另一项无关的独立操作结果"), encoding="utf-8")
    current = create_structure_review_template(workspace, 1, source)
    assert "1.1" in current["reusable_unit_ids"]


@pytest.mark.parametrize("recorded", [None, {"1.1": {"body_unit:1.3": "f" * 64}}])
def test_missing_or_stale_body_reference_fingerprint_rejects_reuse(tmp_path, recorded):
    workspace, source, _, _, output = cross_unit_fixture(tmp_path)
    saved = json.loads(output.read_text(encoding="utf-8"))
    if recorded is None:
        saved.pop("assertion_body_refs", None)
    else:
        saved["assertion_body_refs"] = recorded
    output.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    assert "1.1" not in create_structure_review_template(workspace, 1, source)["reusable_unit_ids"]
    with pytest.raises(DevelopmentalEditError, match="正文前提引用指纹"):
        require_structure_review(workspace, 1, _reader_body(source))


def test_legacy_report_without_new_refs_keeps_reuse(tmp_path):
    workspace, source, _, _, output = cross_unit_fixture(tmp_path, "content_plan")
    saved = json.loads(output.read_text(encoding="utf-8"))
    saved.pop("assertion_body_refs", None)
    output.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    assert "1.1" in create_structure_review_template(workspace, 1, source)["reusable_unit_ids"]
