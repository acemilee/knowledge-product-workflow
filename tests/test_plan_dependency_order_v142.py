"""依赖身份不取决于排列；新提交归属严格，历史存放容器兼容。"""
import copy

import pytest

from knowledge_product_workflow.content_plan import (
    ContentPlanError,
    ContentPlanInput,
    _validate_plan_sections,
)
from knowledge_product_workflow.workflows.curriculum import BlueprintChapter


def case():
    edges = [
        {"producer_chapter": 1, "section_id": "2.2", "kind": "navigation", "purpose": "回查前章已有的机制说明"},
        {"producer_chapter": 1, "section_id": "2.1", "kind": "navigation", "purpose": "复用输入定义与适用条件"},
    ]
    chapter = BlueprintChapter(chapter_number=2, title="依赖规则", scope="分别核对输入与输出的已有约定",
        weight=1, rationale="提供本章独立的依赖核对任务", chapter_archetype="concept",
        learning_objectives=["核对依赖消费者"], production_dependencies=edges,
        section_outline=[{"number": "2.1", "title": "输入约定"}, {"number": "2.2", "title": "输出约定"}])
    sections = [{"section_id": sid, "reader_job": "核对本节声明的输入和已有定义",
                 "opening_mode": "claim", "block_sequence": ["explanation"], "artifact": "none",
                 "asset_decision": "none", "asset_rationale": "文字即可完整说明当前关系",
                 "production_dependencies": [e for e in edges if e["section_id"] == sid]}
                for sid in ("2.1", "2.2")]
    return chapter, ContentPlanInput(sections=sections)


def test_dependency_order_does_not_change_approved_edges():
    chapter, plan = case()
    _validate_plan_sections(plan, chapter)


@pytest.mark.parametrize("mutation", ["missing", "extra", "purpose", "consumer"])
def test_dependency_multiset_preserves_all_attributes_and_multiplicity(mutation):
    chapter, plan = case()
    raw = plan.model_dump(mode="json")
    deps = raw["sections"][0]["production_dependencies"]
    if mutation == "missing":
        deps.clear()
    elif mutation == "extra":
        deps.append(copy.deepcopy(deps[0]))
    elif mutation == "purpose":
        deps[0]["purpose"] = "另一项未经批准的教学用途"
    else:
        deps[0]["section_id"] = "2.2"
    with pytest.raises(ContentPlanError):
        _validate_plan_sections(ContentPlanInput.model_validate(raw), chapter)


def test_new_submission_rejects_legacy_wrong_container_but_read_keeps_edges():
    chapter, plan = case()
    raw = plan.model_dump(mode="json")
    raw["sections"][0]["production_dependencies"] = [e.model_dump(mode="json") for e in chapter.production_dependencies]
    raw["sections"][1]["production_dependencies"] = []
    legacy = ContentPlanInput.model_validate(raw)
    _validate_plan_sections(legacy, chapter)
    with pytest.raises(ContentPlanError, match="归属|小节"):
        _validate_plan_sections(legacy, chapter, submission=True)


def test_existing_filled_template_gets_new_context_without_losing_input(tmp_path):
    import json

    from knowledge_product_workflow.content_plan import create_content_plan_template
    from tests.test_chapter_content_plan import _approved_workspace
    w = _approved_workspace(tmp_path)
    template, context = create_content_plan_template(w, 1)
    template.write_text(template.read_text(encoding="utf-8")+"\n# 作者尚未提交的填写\n", encoding="utf-8")
    before = template.read_bytes()
    data = json.loads(context.read_text(encoding="utf-8"))
    data.pop("heading_navigation")
    context.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    create_content_plan_template(w, 1)
    assert template.read_bytes() == before
    fresh = json.loads(context.read_text(encoding="utf-8"))
    assert "heading_navigation" in fresh and "boundary_repetition_candidates" in fresh
    stamp = context.stat().st_mtime_ns
    create_content_plan_template(w, 1)
    assert context.stat().st_mtime_ns == stamp


def test_new_submission_distinguishes_unknown_from_explicit_no_dependencies():
    chapter, plan = case()
    chapter.production_dependencies = []
    for section in plan.sections:
        section.production_dependencies = []
    _validate_plan_sections(plan, chapter, submission=True)
    plan.sections[0].production_dependencies = None
    _validate_plan_sections(plan, chapter)  # Historical frozen inputs retain original reading compatibility.
    with pytest.raises(ContentPlanError, match="None"):
        _validate_plan_sections(plan, chapter, submission=True)
