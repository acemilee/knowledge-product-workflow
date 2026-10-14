from types import SimpleNamespace

import pytest

from knowledge_product_workflow.developmental import (
    TEACHING_CHECKS,
    DevelopmentalEditError,
    StructureObservation,
    StructureReviewInput,
    _critical_reader_task,
    _validate_teaching_checks,
    adopt_reading_policy,
    create_structure_review_template,
    record_structure_review,
    require_structure_review,
)
from knowledge_product_workflow.runtime import (
    _atomic_json_write,
    _orchestration_state_path,
    _read_orchestration_state,
)
from tests.test_structure_review_v133 import _fixture, simulated_review


def observation(assessment="passed"):
    return StructureObservation(unit_id="1.1", assessment=assessment,
        note="测试样例：读者能够比较方案，并解释各维度对选择的影响。",
        teaching_checks={key: {"assessment": "passed", "note": f"测试依据：{description}，与当前方案例子对应。"}
                         for key, description in TEACHING_CHECKS.items()})


def test_nonprogramming_examples_and_decisions_are_critical_without_keyword_matching():
    for job, blocks in (("解释权重并完成可复算的算例", ["explanation", "example"]),
                        ("制定回合行动顺序并辨析规则取舍", ["decision", "comparison"]),
                        ("理解角色之间的责任与交互", ["diagram", "explanation"])):
        section = SimpleNamespace(reader_job=job, block_sequence=blocks)
        assert _critical_reader_task(section, 2)
        assert not _critical_reader_task(section, 1)


def test_all_checks_and_overall_disposition_are_required():
    current = observation()
    _validate_teaching_checks(current, 2)
    current.teaching_checks["repetition_value"].assessment = "revise"
    with pytest.raises(DevelopmentalEditError, match="总体"):
        _validate_teaching_checks(current, 2)
    current.assessment = "revise"
    _validate_teaching_checks(current, 2)
    current.teaching_checks.pop("actionability")
    with pytest.raises(DevelopmentalEditError, match="六项"):
        _validate_teaching_checks(current, 2)


def test_nonapplicable_requires_reason_and_cannot_hide_increment():
    current = observation()
    current.teaching_checks["learning_increment"].assessment = "not_applicable"
    with pytest.raises(DevelopmentalEditError, match="不适用"):
        _validate_teaching_checks(current, 2)
    with pytest.raises(ValueError, match="具体依据"):
        StructureObservation(unit_id="1.1", assessment="passed", note="测试观察涵盖当前单元的真实上下文。",
            teaching_checks={"learning_increment": {"assessment": "passed", "note": " " * 20}})


def test_rubric_upgrade_invalidates_old_reuse_without_changing_body(tmp_path):
    workspace, source, _ = _fixture(tmp_path)
    state = _read_orchestration_state(workspace).model_copy(update={"reading_review_rubric_revision": 1})
    _atomic_json_write(_orchestration_state_path(workspace), state)
    simulated_review(workspace, source)
    before = source.read_bytes()
    old = create_structure_review_template(workspace, 1, source)
    assert old["reusable_unit_ids"]
    state = _read_orchestration_state(workspace)
    assert state.reading_review_rubric_revision == 1
    result = adopt_reading_policy(workspace)
    assert result["current_rubric_revision"] == 2
    new = create_structure_review_template(workspace, 1, source)
    assert new["template_id"] != old["template_id"]
    assert not new["reusable_unit_ids"]
    assert set(new["teaching_rubric"]) == set(TEACHING_CHECKS)
    assert source.read_bytes() == before


def test_legacy_missing_rubric_marker_defaults_to_one(tmp_path):
    workspace, _, _ = _fixture(tmp_path)
    state = _read_orchestration_state(workspace)
    assert state.reading_review_rubric_revision == 2
    payload = state.model_dump(mode="json")
    payload.pop("reading_review_rubric_revision")
    _atomic_json_write(_orchestration_state_path(workspace), payload)
    assert _read_orchestration_state(workspace).reading_review_rubric_revision == 1


def test_compared_case_change_invalidates_only_its_dependency(tmp_path):
    workspace, source, body = _fixture(tmp_path)
    other = workspace.root / "chapters/chapter-02.md"
    other.write_text("## 2.1 原型规则\n\n每回合玩家先选行动，再更新状态。\n\n## 2.2 其他主题\n\n原补充。\n", encoding="utf-8")
    template = create_structure_review_template(workspace, 1, source)
    assert {u["unit_id"] for u in template["book_reading_index"]} == {"1.1", "2.1", "2.2"}
    current = observation()
    current.compared_unit_ids = ["2.1"]
    record_structure_review(workspace, 1, source, StructureReviewInput(
        template_id=template["template_id"], observations=[current]))
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"] == ["1.1"]
    other.write_text(other.read_text(encoding="utf-8").replace("原补充。", "其他主题增加说明。"), encoding="utf-8")
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"] == ["1.1"]
    other.write_text(other.read_text(encoding="utf-8").replace("玩家先选行动", "敌人先选行动"), encoding="utf-8")
    assert not create_structure_review_template(workspace, 1, source)["reusable_unit_ids"]
    with pytest.raises(DevelopmentalEditError, match="案例单元"):
        require_structure_review(workspace, 1, body)


def test_index_dependency_includes_the_compared_learning_job(tmp_path, monkeypatch):
    import knowledge_product_workflow.developmental as module
    from knowledge_product_workflow.content_plan import ContentPlanSection

    workspace, _source_path, body = _fixture(tmp_path)
    (workspace.root / "chapters/chapter-02.md").write_text("## 2.1 权重\n\n计算采用相同数据。\n", encoding="utf-8")
    section = ContentPlanSection(section_id="2.1", reader_job="解释权重并完成一个可复算的加权计算",
        opening_mode="scenario", block_sequence=["explanation", "example"], artifact="none",
        asset_decision="none", asset_rationale="短公式和算例已足够，不额外画图")
    original = module.require_current_content_plan
    monkeypatch.setattr(module, "require_current_content_plan",
        lambda current, number: SimpleNamespace(sections=[section]) if number == 2 else original(current, number))
    first = {u["unit_id"]: u for u in module.book_reading_index(workspace, 1, body)}["2.1"]
    section.reader_job = "解释评分尺度并说明结论的使用边界"
    second = {u["unit_id"]: u for u in module.book_reading_index(workspace, 1, body)}["2.1"]
    assert first["unit_sha256"] == second["unit_sha256"]
    assert first["comparison_sha256"] != second["comparison_sha256"]
