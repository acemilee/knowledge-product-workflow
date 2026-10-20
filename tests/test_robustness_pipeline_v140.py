"""人工24章、零模型；不读取生产稿，不证明真实宿主容量或费用。"""
import copy
import json
import time

import pytest

from knowledge_product_workflow.production_dependencies import (
    build_dependency_plan,
    deferred_repairs,
)
from knowledge_product_workflow.production_tasks import (
    ProductionPolicy,
    ProductionRun,
    next_dispatches,
)
from knowledge_product_workflow.publishing import assemble_book
from knowledge_product_workflow.runtime import inspect_workflow, recovery_view
from knowledge_product_workflow.user_decisions import (
    UserDecisionTarget,
    UserResponseDecision,
    record_user_decision,
    request_user_decision,
    sample_approval_is_current,
)
from knowledge_product_workflow.workflows.authoring import approve_sample_chapter
from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
from tests.test_change_impact_v140 import workspace_fixture
from tests.test_illustration_policy import task
from tests.test_proof_refresh_v140 import apply


def book(tmp_path):
    w = workspace_fixture(tmp_path, 24)
    _, _, blueprint = require_current_submitted_blueprint(w)
    raw = blueprint.model_dump(mode="json")
    raw.update(production_policy_revision=1, shared_context={}, illustration_policy_revision=2)
    base = copy.deepcopy(raw["chapters"][0])
    for n in range(1, 25):
        raw["chapters"][n-1] = {**copy.deepcopy(base), "chapter_number": n,
            "title": f"章节{n}", "rationale": f"独立核验第{n}个教学任务的决策条件",
            "production_dependencies": [], "production_exports": [],
            "illustration_policy_revision": 2,
            "illustration_tasks": [{"section_id": f"{n}.1", "decision": "none",
                "alternative_rationale": "本节以文字判断顺序表达，无须图示关系"}],
            "section_outline": [{"number": f"{n}.1", "title": "决策框架", "children": []}]}
    raw["chapters"][20]["production_exports"] = [{"export_id": "export-result-21", "section_id": "21.1",
        "kind": "result", "definition": "本节已核验的推理顺序"}]
    raw["chapters"][22]["production_dependencies"] = [{"producer_chapter": 21, "section_id": "23.1",
        "kind": "result", "export_id": "export-result-21", "purpose": "应用已验证的推理结果"}]
    apply(w, raw)
    planned_submit(w, 1)
    request_user_decision(w, UserDecisionTarget.SAMPLE)
    record_user_decision(w, UserDecisionTarget.SAMPLE, UserResponseDecision.APPROVE,
                         approved_by="测试用户", response="批准样章继续")
    approve_sample_chapter(w, "测试用户")
    return w


def planned_submit(w, n):
    import yaml

    from knowledge_product_workflow.chapter_compliance import submit_chapter
    from knowledge_product_workflow.content_plan import submit_content_plan
    from knowledge_product_workflow.developmental import record_developmental_check
    from knowledge_product_workflow.humanize import record_humanize_check
    from knowledge_product_workflow.workflows.authoring import create_chapter_draft
    from tests.test_chapter_compliance import _body
    from tests.test_illustration_policy import plan
    from tests.test_structure_review_v133 import simulated_review
    create_chapter_draft(w, n)
    raw = plan(2, [{"section_id": f"{n}.1", "decision": "none",
        "alternative_rationale": "本节以文字判断顺序表达，无须图示关系"}])
    raw["sections"][0]["section_id"] = f"{n}.1"
    path = w.root / f"runs/plan-{n}.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    submit_content_plan(w, n, path)
    source = w.root / f"runs/body-{n}.md"
    source.write_text(_body().replace("1.", f"{n}.").replace("基础判断", f"章节{n}") +
        "\n可查属性表\n\n| 输入 | 核验 |\n| --- | --- |\n| 证据不足 | 记录待核条件 |\n", encoding="utf-8")
    mapping = w.root / f"runs/mapping-{n}.json"
    mapping.write_text(json.dumps({"learning_objective_anchors": {"建立可执行的判断框架": f"{n}.1 决策框架"},
        "unit_anchors": {"decision_framework": f"{n}.1 决策框架"}, "claim_locations": {}}, ensure_ascii=False), encoding="utf-8")
    simulated_review(w, source, number=n)
    record_developmental_check(w, n, source)
    record_humanize_check(w, n, source)
    return submit_chapter(w, n, source, mapping)


def test_twenty_four_chapter_serial_lifecycle_and_local_graph_debt(tmp_path):
    started = time.perf_counter()
    w = book(tmp_path)
    for n in range(2, 22):
        planned_submit(w, n)
    assert 23 in build_dependency_plan(w).ready_chapters
    before = {p.relative_to(w.root).as_posix(): p.read_bytes()
        for n in range(1, 21) for p in [w.root / f"chapters/chapter-{n:02d}.md",
                                       w.root / f"chapters/chapter-{n:02d}-contract.md"]}
    marker = (w.root / "chapters/chapter-01-sample.approved").read_bytes()
    _, _, bp = require_current_submitted_blueprint(w)
    raw = bp.model_dump(mode="json")
    # Introduce a genuine new required diagram on submitted 21; the body is saved,
    # but that missing asset is deliberately a debt, never an invented green proof.
    raw["chapters"][20]["illustration_tasks"] = [task(section_id="21.1", relation_kind="comparison", diagram_type="comparison")]
    apply(w, raw)
    raw = require_current_submitted_blueprint(w)[2].model_dump(mode="json")
    raw["chapters"][20]["illustration_tasks"][0].update(relation_kind="dependency", diagram_type="flow")
    impact = apply(w, raw)
    assert {c.chapter_number for c in impact.chapters if c.disposition != "unaffected"} == {21, 23}
    assert all((w.root / p).read_bytes() == data for p, data in before.items())
    assert (w.root / "chapters/chapter-01-sample.approved").read_bytes() == marker
    assert sample_approval_is_current(w)
    plan = build_dependency_plan(w)
    assert {22, 24} <= set(plan.ready_chapters) and 23 not in plan.ready_chapters
    assert inspect_workflow(w).target_chapter_number == 22
    planned_submit(w, 22)
    assert inspect_workflow(w).target_chapter_number == 24
    planned_submit(w, 24)
    assert any(d["chapter_number"] == 21 for d in deferred_repairs(w))
    with pytest.raises(ValueError, match="待|依赖|修复|章节"):
        assemble_book(w)
    assert recovery_view(w)["unknown_tasks"] == []
    evidence = {"elapsed_seconds": time.perf_counter()-started, "saved_chapters": 23,
        "unrelated_body_or_contract_rewrites": 0, "model_dispatches": 0,
        "protected_reader_and_contract_reads": len(before), "real_capacity_verified": False}
    (w.root / "runs/offline-metrics.json").write_text(json.dumps(evidence), encoding="utf-8")


@pytest.mark.parametrize("limit", [4, 5, 6])
def test_offline_twenty_four_chapter_all_roles_share_capacity(limit):
    run = ProductionRun.diagnostic_run(24, ProductionPolicy(requested_parallelism=limit))
    turns = 0
    while True:
        dispatched = next_dispatches(run)
        if not dispatched:
            break
        assert run.active_count <= limit
        for t in dispatched:
            if t.role == "writer":
                run.complete_edit(t.task_id, f"人工短稿{t.chapter_number}", input_manifest_sha256=t.input_manifest_sha256)
            else:
                run.complete_review(t.task_id, passed=True, input_manifest_sha256=t.input_manifest_sha256)
        turns += 1
        assert turns < 100
    assert len([t for t in run.tasks if t.role == "reviewer" and t.state == "acceptance_ready"]) == 24
    assert run.active_count == 0 and run.diagnostic_only
