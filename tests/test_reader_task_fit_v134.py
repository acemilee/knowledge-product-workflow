"""Explicit reader promises remain binding; prose is a valid representation."""
from types import SimpleNamespace
import json

import pytest

from knowledge_product_workflow.content_plan import (
    ContentPlanError, ContentPlanInput, ContentPlanSection, _requires_reader_artifact,
    _validate_plan_sections, submit_content_plan,
)
from knowledge_product_workflow.developmental import _critical_reader_task, _findings_for_current_plan
from knowledge_product_workflow.developmental import (
    DevelopmentalEditError, create_structure_review_template, require_structure_review,
    structure_review_path,
)
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from tests.test_chapter_content_plan import _approved_workspace


def section(kind=None):
    return ContentPlanSection(section_id="1.1", reader_job="理解检索结果与回答依据的区别和适用条件。",
        opening_mode="claim", block_sequence=["explanation"], artifact="none" if kind is None else "可复用的取舍过程",
        artifact_kind=kind, asset_decision="none", asset_rationale="连续文字能直接表达条件和推理，不需要额外图表。")


def save_plan(workspace, value):
    path = workspace.root / "runs/reader-task-plan.json"
    path.write_text(ContentPlanInput(sections=[value]).model_dump_json(), encoding="utf-8")
    submit_content_plan(workspace, 1, path)


@pytest.mark.parametrize("units,wanted", [(["concept"], False), (["decision_framework"], False),
    (["scenario", "evidence_boundary"], False), (["reference_entries"], False),
    (["input_output_artifact"], True), (["template"], True)])
def test_artifact_requirement_uses_explicit_units_not_archetype(units, wanted):
    assert _requires_reader_artifact(SimpleNamespace(required_units=units, chapter_archetype=SimpleNamespace(value="concept"))) is wanted


def test_natural_explanation_plan_can_submit_but_explicit_io_still_requires_artifact(tmp_path):
    workspace = _approved_workspace(tmp_path)
    save_plan(workspace, section())
    _, findings = _findings_for_current_plan(workspace, 1, "## 1.1 决策框架\n\n检索结果来自排序，回答依据还需要核对内容是否支持结论。\n")
    assert not any(f.severity.value == "blocker" for f in findings)
    _, chapter = require_current_chapter_contract(workspace, 1)
    chapter = chapter.model_copy(update={"required_units": ["input_output_artifact"]})
    with pytest.raises(ContentPlanError, match="读者可复用 artifact"):
        _validate_plan_sections(ContentPlanInput(sections=[section()]), chapter)


def test_illustration_cannot_replace_explicit_io_artifact(tmp_path):
    workspace = _approved_workspace(tmp_path)
    _, chapter = require_current_chapter_contract(workspace, 1)
    diagram = SimpleNamespace(production="create", section_id="1.1", task_id="process",
                              blueprint_binding=lambda: {"purpose": "解释过程"})
    chapter = chapter.model_copy(update={"required_units": ["input_output_artifact"],
        "illustration_policy_revision": 2, "illustration_tasks": [diagram]})
    explanation = section().model_copy(update={"illustration_policy_revision": 2,
        "illustration_tasks": [diagram], "block_sequence": ["explanation", "diagram"]})
    plan = ContentPlanInput(sections=[section()]).model_copy(update={"illustration_policy_revision": 2, "sections": [explanation]})
    with pytest.raises(ContentPlanError, match="读者可复用 artifact"):
        _validate_plan_sections(plan, chapter)


@pytest.mark.parametrize("kind,prose", [
    ("decision", "如果记录较少且无需模糊匹配，直接检索可减少依赖；当词汇差异导致遗漏时，应比较召回方案，但还要评估误匹配与维护成本。因此先选直接检索，并在出现稳定遗漏证据时复验另一方案。"),
    ("derivation", "先从当前检索结果中找到与问题对应的段落，再检查该段落的适用条件。只有条件一致时，才能将段落作为回答依据；因此排序靠前并不直接推出结论可信，还需要完成第二次条件核对。"),
])
def test_narrative_artifact_is_candidate_and_requires_actual_review(tmp_path, kind, prose):
    workspace = _approved_workspace(tmp_path)
    promise = section(kind)
    save_plan(workspace, promise)
    assert _critical_reader_task(promise, 2)
    _, good = _findings_for_current_plan(workspace, 1, "## 1.1 决策框架\n\n" + prose + "\n")
    assert not any(f.code == "DEVELOPMENTAL_ARTIFACT_MISSING" for f in good)
    for empty in ("", "待补充", "下文会给出具体的取舍过程。"):
        _, missing = _findings_for_current_plan(workspace, 1, "## 1.1 决策框架\n\n" + empty + "\n")
        assert any(f.code == "DEVELOPMENTAL_ARTIFACT_MISSING" for f in missing)


@pytest.mark.parametrize("language,delimiter", [("cpp", "```"), ("javascript", "~~~"), ("c", "````")])
def test_non_python_closed_nonempty_code_exists_without_execution_claim(tmp_path, language, delimiter):
    workspace = _approved_workspace(tmp_path)
    code = section().model_copy(update={"artifact": "程序示例", "artifact_kind": "code", "block_sequence": ["example"]})
    save_plan(workspace, code)
    _, present = _findings_for_current_plan(workspace, 1, f"## 1.1 决策框架\n\n{delimiter}{language}\nint value = 1;\n{delimiter}\n")
    assert not any(f.code == "DEVELOPMENTAL_ARTIFACT_MISSING" for f in present)
    for payload in (f"{delimiter}{language}\n\n{delimiter}\n", f"{delimiter}{language}\nint value = 1;\n"):
        _, missing = _findings_for_current_plan(workspace, 1, "## 1.1 决策框架\n\n" + payload)
        assert any(f.code == "DEVELOPMENTAL_ARTIFACT_MISSING" for f in missing)
    assert not list((workspace.root / "runs").rglob("stdout.bin"))


def test_empty_quote_code_is_missing_and_nested_list_code_exists(tmp_path):
    workspace = _approved_workspace(tmp_path)
    code = section().model_copy(update={"artifact": "程序示例", "artifact_kind": "code", "block_sequence": ["example"]})
    save_plan(workspace, code)
    for payload, wanted in (("> ~~~cpp\n>\n> ~~~\n", False),
                            ("> ~~~cpp\n> int value = 1;\n> ~~~\n", True),
                            ("- 执行：\n\n    ~~~cpp\n    int value = 1;\n    ~~~\n", True)):
        _, findings = _findings_for_current_plan(workspace, 1, "## 1.1 决策框架\n\n" + payload)
        assert (not any(f.code == "DEVELOPMENTAL_ARTIFACT_MISSING" for f in findings)) is wanted


def test_old_required_flags_cannot_skip_newly_mandatory_promised_artifact(tmp_path):
    from knowledge_product_workflow.content_plan import _flatten_outline
    from tests.test_structure_review_v133 import simulated_review
    workspace = _approved_workspace(tmp_path, titles=("基础判断", "案例拆解"))
    _, chapter = require_current_chapter_contract(workspace, 2)
    outlines = _flatten_outline(chapter.section_outline)
    sections = [section("decision" if index == 0 else None).model_copy(update={"section_id": number})
                for index, (number, _) in enumerate(outlines)]
    plan_path = workspace.root / "runs/plan-02.json"
    plan_path.write_text(ContentPlanInput(sections=sections).model_dump_json(), encoding="utf-8")
    submit_content_plan(workspace, 2, plan_path)
    source = workspace.root / "runs/chapter-02.md"
    body = "# 2. 案例拆解\n\n" + "\n".join(f"## {number} {title}\n\n条件明确时选择方案甲；条件不同则比较方案乙的代价，再确定适用范围。\n" for number, title in outlines)
    source.write_text(body, encoding="utf-8")
    simulated_review(workspace, source, number=2)
    path = structure_review_path(workspace, 2)
    data = json.loads(path.read_text(encoding="utf-8"))
    for unit in data["units"]:
        unit["required"] = False
    data["observations"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DevelopmentalEditError, match="缺项|过期"):
        require_structure_review(workspace, 2, body)
    template = create_structure_review_template(workspace, 2, source)
    assert [item["unit_id"] for item in template["observations"]] == [outlines[0][0]]
    assert not template["reusable_unit_ids"]
