import copy
import json

import pytest

from knowledge_product_workflow.chapter_compliance import submit_chapter
from knowledge_product_workflow.humanize import record_humanize_check
from knowledge_product_workflow.production_dependencies import (
    build_dependency_plan,
    deferred_repairs,
)
from knowledge_product_workflow.publishing import (
    assemble_book,
    create_delivery_manifest,
    render_pdf,
)
from knowledge_product_workflow.runtime import inspect_workflow
from knowledge_product_workflow.user_decisions import (
    UserDecisionTarget,
    UserResponseDecision,
    record_user_decision,
    request_user_decision,
)
from knowledge_product_workflow.workflows.authoring import (
    approve_sample_chapter,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
from tests.test_change_impact_v140 import workspace_fixture
from tests.test_chapter_compliance import _body
from tests.test_proof_refresh_v140 import apply


def serial_book(tmp_path, *, with_content_profile=False):
    w = workspace_fixture(tmp_path, 4)
    _, _, blueprint = require_current_submitted_blueprint(w)
    raw = blueprint.model_dump(mode="json")
    raw.update(production_policy_revision=1, shared_context={})
    for n in range(1, 5):
        c = copy.deepcopy(raw["chapters"][0])
        c.update(chapter_number=n, title=f"章节{n}", production_dependencies=[], production_exports=[],
            rationale=["用决策框架建立判断基础", "核验本章产出的正式推理顺序", "以受控结果检验迁移条件", "独立整理不依赖结果的参考约定"][n-1],
            section_outline=[{"number": f"{n}.1", "title": "决策框架", "children": []}])
        raw["chapters"][n-1] = c
    raw["chapters"][1]["production_exports"] = [{"export_id": "export-two", "section_id": "2.1",
        "kind": "result", "definition": "本节经核验的判断顺序"}]
    raw["chapters"][2]["production_dependencies"] = [{"producer_chapter": 2, "section_id": "3.1",
        "kind": "result", "export_id": "export-two", "purpose": "使用已成立的判断结果"}]
    apply(w, raw)
    if with_content_profile:
        from knowledge_product_workflow.presentation import load_or_create_publication_profile
        load_or_create_publication_profile(w, language="zh-CN")
    submit(w, 1)
    request_user_decision(w, UserDecisionTarget.SAMPLE)
    record_user_decision(w, UserDecisionTarget.SAMPLE, UserResponseDecision.APPROVE,
        approved_by="测试用户", response="批准样章并继续")
    approve_sample_chapter(w, "测试用户")
    return w


def submit(w, n):
    create_chapter_draft(w, n)
    source = w.root / f"runs/body-{n}.md"
    source.write_text(_body().replace("1.", f"{n}.").replace("基础判断", f"章节{n}"), encoding="utf-8")
    if (w.root / "planning/publication-profile.yaml").is_file():
        import yaml

        from knowledge_product_workflow.content_plan import submit_content_plan
        from knowledge_product_workflow.developmental import record_developmental_check
        from tests.test_illustration_policy import plan
        from tests.test_structure_review_v133 import simulated_review
        chapter = require_current_submitted_blueprint(w)[2].chapters[n-1]
        payload = plan()
        payload["sections"][0].update(section_id=f"{n}.1",
            production_dependencies=[d.model_dump(mode="json") for d in chapter.production_dependencies or []])
        plan_path = w.root / f"runs/plan-{n}.yaml"
        plan_path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
        submit_content_plan(w, n, plan_path)
        body = source.read_text(encoding="utf-8").replace("\n\n## ", "\n\n::: {.kp-chapter-lead}\n本章从输入和成立条件出发，建立可核验的判断顺序。\n:::\n\n## ", 1)
        source.write_text(body + "\n可查属性表\n\n| 输入 | 核验 |\n| --- | --- |\n| 证据不足 | 记录待核条件 |\n", encoding="utf-8")
        simulated_review(w, source, number=n)
        record_developmental_check(w, n, source)
    mapping = w.root / f"runs/mapping-{n}.json"
    mapping.write_text(json.dumps({"learning_objective_anchors": {"建立可执行的判断框架": f"{n}.1 决策框架"},
        "unit_anchors": {"decision_framework": f"{n}.1 决策框架"}, "claim_locations": {}}, ensure_ascii=False), encoding="utf-8")
    record_humanize_check(w, n, source)
    return submit_chapter(w, n, source, mapping)


def test_serial_submission_creates_current_export_without_native_grant(tmp_path):
    w = serial_book(tmp_path)
    assert 3 not in build_dependency_plan(w).ready_chapters
    submit(w, 2)
    assert 3 in build_dependency_plan(w).ready_chapters
    registry = json.loads((w.root / "runs/production/accepted-exports.json").read_text(encoding="utf-8"))
    assert registry["export-two"]["commit_kind"] == "serial"
    assert not list((w.root / "runs/production").glob("*/run.json"))


def test_stale_producer_blocks_consumer_but_independent_later_chapter_continues(tmp_path):
    w = serial_book(tmp_path)
    submit(w, 2)
    report = w.root / "runs/humanize/chapter-02.json"
    raw = json.loads(report.read_text(encoding="utf-8"))
    raw["findings"] = [{"code": "TEST_FAILURE", "severity": "blocker", "location": "2.1", "message": "待修"}]
    report.write_text(json.dumps(raw), encoding="utf-8")
    assert 3 not in build_dependency_plan(w).ready_chapters
    assert any(i["chapter_number"] == 2 for i in deferred_repairs(w))
    status = inspect_workflow(w)
    assert status.target_chapter_number == 4
    create_chapter_draft(w, 4)
    assert (w.root / "chapters/chapter-04.md").is_file()
    for operation in [lambda: assemble_book(w),
        lambda: render_pdf(w.root / "delivery/book.qmd", w.root / "delivery", w),
        lambda: create_delivery_manifest(w, None, None)]:
        with pytest.raises(ValueError, match="待|依赖|修复|章节"):
            operation()


def test_local_interrupted_submission_does_not_block_independent_later_draft(tmp_path, monkeypatch):
    import knowledge_product_workflow.workspace_transactions as transactions
    w = serial_book(tmp_path)
    def fault(phase):
        if phase == "after_replace:chapters/chapter-02.md":
            raise SystemExit("owned local writer interrupted")
    monkeypatch.setattr(transactions, "_fault", fault)
    with pytest.raises(SystemExit):
        submit(w, 2)
    assert transactions.pending_workspace_transactions(w)
    assert inspect_workflow(w).target_chapter_number == 4
    create_chapter_draft(w, 4)


def test_policy2_private_submission_carries_original_sample_and_result_provenance(tmp_path):
    from pathlib import Path

    from knowledge_product_workflow.production_context import (
        evaluate_candidate,
        freeze_candidate_context,
    )
    from knowledge_product_workflow.production_dependencies import candidate_input_paths
    from knowledge_product_workflow.production_intake import materialize_candidate_submission
    from knowledge_product_workflow.workspace import Workspace
    w = serial_book(tmp_path)
    submit(w, 2)
    source = w.root / "runs/body-3.md"
    source.write_text(_body().replace("1.", "3.").replace("基础判断", "章节3"), encoding="utf-8")
    paths = candidate_input_paths(w, 3, source)
    assert "runs/interaction/sample.json" in paths
    assert "runs/production/accepted-exports.json" in paths
    context = freeze_candidate_context(w, chapter_number=3, candidate_version=1, source=source,
        input_paths=paths, run_id="private-policy2", task_id="writer-three")
    evaluate_candidate(context)
    private = Workspace(Path(context.private_result_root) / "proof-workspace")
    assert 3 in build_dependency_plan(private).ready_chapters
    mapping = private.root / "runs/mapping-three.json"
    mapping.write_text(json.dumps({"learning_objective_anchors": {"建立可执行的判断框架": "3.1 决策框架"},
        "unit_anchors": {"decision_framework": "3.1 决策框架"}, "claim_locations": {}}, ensure_ascii=False), encoding="utf-8")
    materialized = materialize_candidate_submission(private, context, mapping)
    assert (materialized.root / "chapters/chapter-03.md").is_file()
    assert not (w.root / "chapters/chapter-03.md").exists()


def test_proof_only_handoff_repairs_report_without_reopening_submitted_body(tmp_path):
    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app
    from knowledge_product_workflow.runtime import (
        create_orchestration_work_order,
        read_orchestration_work_order,
    )
    w = serial_book(tmp_path)
    for number in (2, 3, 4):
        submit(w, number)
    path = w.root / "runs/humanize/chapter-02.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["contract_sha256"] = "0" * 64
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = (w.root / "chapters/chapter-02.md").read_bytes()
    status = inspect_workflow(w)
    assert status.stage == "chapter_proofs_pending"
    assert [a.command for a in status.next_actions] == ["check-chapter"]
    order = read_orchestration_work_order(w, create_orchestration_work_order(w))
    assert "submit-chapter" not in order.allowed_commands and order.lease is None
    result = CliRunner().invoke(app, ["humanize-check", "2", "--workspace", str(w.root),
        "--from", str(w.root / "runs/body-2.md")])
    assert result.exit_code == 0, result.output
    assert (w.root / "chapters/chapter-02.md").read_bytes() == before
    assert inspect_workflow(w).stage != "chapter_proofs_pending"


def test_changed_accepted_result_invalidates_saved_consumer_and_final_gate(tmp_path):
    from typer.testing import CliRunner

    from knowledge_product_workflow.chapter_compliance import recheck_submitted_chapter
    from knowledge_product_workflow.cli import app
    from knowledge_product_workflow.production_dependencies import require_no_deferred_repairs
    from knowledge_product_workflow.workflows.authoring import amend_chapter_draft
    w = serial_book(tmp_path)
    for n in (2, 3, 4):
        submit(w, n)
    consumer = (w.root / "chapters/chapter-03.md").read_bytes()
    independent = (w.root / "chapters/chapter-04.md").read_bytes()
    assert not deferred_repairs(w)
    amend_chapter_draft(w, 2, "真实结果发生改变")
    source = w.root / "runs/body-2.md"
    source.write_text(source.read_text(encoding="utf-8") + "\n结论变更：证据不足时中止执行并重新收集输入。\n", encoding="utf-8")
    record_humanize_check(w, 2, source)
    submit_chapter(w, 2, source, w.root / "runs/mapping-2.json")
    debts = deferred_repairs(w)
    assert {d["chapter_number"] for d in debts} == {3}
    assert (w.root / "chapters/chapter-03.md").read_bytes() == consumer
    assert (w.root / "chapters/chapter-04.md").read_bytes() == independent
    with pytest.raises(ValueError, match="修复|待"):
        require_no_deferred_repairs(w)
    events = (w.root / "runs/events.jsonl").read_bytes()
    result = CliRunner().invoke(app, ["recheck-chapter", "3", "--workspace", str(w.root),
        "--compliance", str(w.root / "runs/mapping-3.json")])
    assert result.exit_code == 0, result.output
    assert (w.root / "chapters/chapter-03.md").read_bytes() == consumer
    assert not deferred_repairs(w)
    require_no_deferred_repairs(w)
    refreshed_events = (w.root / "runs/events.jsonl").read_bytes()
    assert refreshed_events != events
    recheck_submitted_chapter(w, 3, w.root / "runs/mapping-3.json")
    assert (w.root / "runs/events.jsonl").read_bytes() == refreshed_events
    changed = w.root / "chapters/chapter-03.md"
    changed.write_text(changed.read_text(encoding="utf-8") + "新增正文。\n", encoding="utf-8")
    with pytest.raises(ValueError, match="快照|正文"):
        recheck_submitted_chapter(w, 3, w.root / "runs/mapping-3.json")


def test_result_change_reuses_only_unaffected_review_units(tmp_path):
    import yaml

    from knowledge_product_workflow.content_plan import submit_content_plan
    from knowledge_product_workflow.developmental import create_structure_review_template
    from tests.test_illustration_policy import plan
    from tests.test_structure_review_v133 import simulated_review
    w = serial_book(tmp_path, with_content_profile=True)
    raw = require_current_submitted_blueprint(w)[2].model_dump(mode="json")
    raw["chapters"][2]["section_outline"] = [{"number": f"3.{n}", "title": f"判断步骤{n}", "children": []} for n in (1, 2, 3)]
    raw["chapters"][2]["production_dependencies"][0]["section_id"] = "3.2"
    apply(w, raw)
    submit(w, 2)
    create_chapter_draft(w, 3)
    payload = {"sections": [{**plan()["sections"][0], "section_id": f"3.{n}"} for n in (1, 2, 3)]}
    for section in payload["sections"]:
        section["production_dependencies"] = (raw["chapters"][2]["production_dependencies"]
            if section["section_id"] == "3.2" else [])
    supplied = w.root / "runs/plan-3.yaml"
    supplied.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    submit_content_plan(w, 3, supplied)
    source3 = w.root / "runs/body-three.md"
    source3.write_text("# 3. 章节3\n\n" + "\n".join(f"## 3.{n} 判断步骤{n}\n\n先确认本节的输入、适用条件与任务边界，再按可验证依据完成决策。\n" for n in (1, 2, 3)), encoding="utf-8")
    simulated_review(w, source3, number=3)
    assert create_structure_review_template(w, 3, source3)["reusable_unit_ids"] == ["3.1", "3.2", "3.3"]
    # External upstream modification is unknown, not a newly accepted result.
    # The legal re-acceptance case is covered separately above.
    source2 = w.root / "chapters/chapter-02.md"
    source2.write_text(source2.read_text(encoding="utf-8") + "\n结果条件变更：证据缺失时停止。\n", encoding="utf-8")
    assert create_structure_review_template(w, 3, source3)["reusable_unit_ids"] == ["3.1", "3.3"]
