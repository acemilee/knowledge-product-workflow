"""R01: change classification before proof refresh or downstream scheduling."""

import copy
import json

import pytest
import yaml

from knowledge_product_workflow.change_impact import (
    bindings_for_chapter,
    chapter_bindings,
    preview_change_impact,
    semantic_digest,
)
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.authoring import compile_chapter_contracts
from knowledge_product_workflow.workflows.curriculum import (
    amend_curriculum_blueprint,
    require_current_submitted_blueprint,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint
from tests.test_illustration_policy import task


def workspace_fixture(tmp_path, count=2, claim_ids=None):
    workspace = Workspace.create(tmp_path, "precise")
    state = workspace.root / "runs/orchestration/state.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    data["binding_policy_revision"] = 2
    state.write_text(json.dumps(data), encoding="utf-8")
    brief = create_product_brief(workspace, "决策框架", DetailConstraints(target_words=1,
        delivery_format="markdown"), target_audience="工程师")
    approve(brief)
    blueprint = submit_test_blueprint(workspace, brief,
        titles=tuple(f"独立教学单元{n}" for n in range(1, count + 1)), claim_ids=claim_ids)
    approve(blueprint)
    compile_chapter_contracts(workspace)
    return workspace


def approve(path):
    meta, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    path.write_text(render_markdown_artifact(transition(meta, ArtifactStatus.APPROVED,
        approved_by="测试批准"), body), encoding="utf-8")


def chapter(number=21):
    return {"chapter_number": number, "title": "推理策略", "scope": "比较候选拓扑",
        "weight": 1, "rationale": "独立判断条件", "chapter_archetype": "reference",
        "section_outline": [{"number": f"{number}.1", "title": "候选拓扑", "children": []}],
        "illustration_policy_revision": 2,
        "illustration_tasks": [task(section_id=f"{number}.1", diagram_type="comparison",
            relation_kind="comparison")], "production_dependencies": [], "production_exports": []}


def test_graph_change_keeps_content_and_changes_only_its_asset_projection():
    before = chapter()
    after = copy.deepcopy(before)
    after["illustration_tasks"][0].update(relation_kind="dependency", diagram_type="flow")
    old = bindings_for_chapter(before)
    new = bindings_for_chapter(after)
    assert old.content == new.content
    assert old.shared == new.shared
    assert old.review == new.review
    assert old.assets != new.assets


def test_effective_budget_and_shared_contract_are_semantic_inputs():
    raw = chapter()
    a = bindings_for_chapter(raw, shared={"term": "A"}, budget={"word_target": 20})
    assert a.content != bindings_for_chapter(raw, shared={"term": "A"}, budget={"word_target": 21}).content
    assert a.shared != bindings_for_chapter(raw, shared={"term": "B"}, budget={"word_target": 20}).shared


def test_unknown_fields_fail_closed_including_nested_task():
    data = chapter()
    data["future_requirement"] = "不能忽略"
    with pytest.raises(ValueError, match="未知字段"):
        bindings_for_chapter(data)
    data.pop("future_requirement")
    data["illustration_tasks"][0]["future_requirement"] = "不能忽略"
    with pytest.raises(ValueError, match="未知字段"):
        bindings_for_chapter(data)


def test_canonicalization_preserves_order_and_unknown_dependency_distinction():
    assert semantic_digest({"text": "e\u0301\r\n文", "b": 2}) == semantic_digest({"b": 2, "text": "é\n文"})
    assert semantic_digest(["a", "b"]) != semantic_digest(["b", "a"])
    assert semantic_digest(None) != semantic_digest([])


def test_local_chapter_change_preserves_other_twenty_three_contracts(tmp_path):
    workspace = workspace_fixture(tmp_path, 24)
    _, _, submission = require_current_submitted_blueprint(workspace)
    payload = submission.model_dump(mode="json")
    payload["chapters"][20]["scope"] += "，增加一个决策条件"
    proposed = workspace.root / "runs/change.yaml"
    proposed.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    before = {n: (workspace.root / f"chapters/chapter-{n:02d}-contract.md").read_bytes()
              for n in range(1, 25)}
    impact = preview_change_impact(workspace, proposed)
    assert [c.chapter_number for c in impact.chapters if c.disposition != "unaffected"] == [21]
    blueprint = amend_curriculum_blueprint(workspace, proposed)
    approve(blueprint)
    compile_chapter_contracts(workspace)
    for n in range(1, 25):
        current = (workspace.root / f"chapters/chapter-{n:02d}-contract.md").read_bytes()
        assert (current != before[n]) is (n == 21)
    assert chapter_bindings(workspace, 1).schema_version == 2


def test_preview_is_read_only_and_detects_shared_changes(tmp_path):
    workspace = workspace_fixture(tmp_path)
    _, _, submission = require_current_submitted_blueprint(workspace)
    raw = submission.model_dump(mode="json")
    raw["scope_expansion_units"] = ["真实共享条件变化"]
    proposed = workspace.root / "runs/change.yaml"
    proposed.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    before = {p.relative_to(workspace.root): p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    result = preview_change_impact(workspace, proposed)
    assert all(c.disposition != "unaffected" for c in result.chapters)
    assert before == {p.relative_to(workspace.root): p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}


def test_candidate_live_bindings_do_not_retire_unrelated_chapter(tmp_path):
    from knowledge_product_workflow.production_dependencies import (
        binding_digest,
        build_task_context,
    )
    workspace = workspace_fixture(tmp_path, 3)
    context = build_task_context(workspace, 1, "writer")
    assert "planning/curriculum-blueprint.md" in context["snapshot_paths"]
    assert "planning/curriculum-blueprint.md" not in context["live_bindings"]
    _, _, submission = require_current_submitted_blueprint(workspace)
    raw = submission.model_dump(mode="json")
    raw["chapters"][2]["scope"] += "，新增自己的判断"
    proposed = workspace.root / "runs/change.yaml"
    proposed.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    approve(amend_curriculum_blueprint(workspace, proposed))
    compile_chapter_contracts(workspace)
    assert all(binding_digest(workspace, key) == expected
               for key, expected in context["live_bindings"].items())


def test_result_consumer_closure_is_a_recheck_not_an_unrelated_rewrite(tmp_path):
    workspace = workspace_fixture(tmp_path, 4)
    _, _, submission = require_current_submitted_blueprint(workspace)
    raw = submission.model_dump(mode="json")
    raw.update(production_policy_revision=1, shared_context={})
    for c in raw["chapters"]:
        c.update(production_dependencies=[], production_exports=[])
    for n in (1, 2):
        raw["chapters"][n-1]["production_exports"] = [{"export_id": f"export-result-{n}",
            "section_id": f"{n}.1", "kind": "result", "definition": "经验证的结果"}]
        raw["chapters"][n]["production_dependencies"] = [{"producer_chapter": n,
            "section_id": f"{n+1}.1", "kind": "result", "export_id": f"export-result-{n}",
            "purpose": "消费已成立的上游结果"}]
    proposed = workspace.root / "runs/typed.yaml"
    proposed.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    approve(amend_curriculum_blueprint(workspace, proposed))
    compile_chapter_contracts(workspace)
    raw["chapters"][0]["scope"] += "，实际结果有待重核"
    proposed.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    impacts = {i.chapter_number: i for i in preview_change_impact(workspace, proposed).chapters}
    assert impacts[1].disposition == "content_revision"
    assert impacts[2].disposition == impacts[3].disposition == "review_required"
    assert impacts[4].disposition == "unaffected"
