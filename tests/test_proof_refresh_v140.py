import json

import pytest
import yaml

from knowledge_product_workflow.change_impact import (
    preview_change_impact,
    proof_contract_digest,
    refresh_chapter_proofs,
)
from knowledge_product_workflow.claim_classification import (
    ClaimClassificationInput,
    append_claim_classification,
    current_claim_classifications,
)
from knowledge_product_workflow.claims import current_claim_contract_bindings
from knowledge_product_workflow.humanize import (
    record_humanize_check,
    require_current_humanize_report,
)
from knowledge_product_workflow.workflows.authoring import compile_chapter_contracts
from knowledge_product_workflow.workflows.curriculum import (
    amend_curriculum_blueprint,
    require_current_submitted_blueprint,
)
from tests.test_change_impact_v140 import approve, workspace_fixture
from tests.test_illustration_policy import task


def graph_workspace(tmp_path):
    w = workspace_fixture(tmp_path, 1, claim_ids=(("claim-example",),))
    _, _, bp = require_current_submitted_blueprint(w)
    raw = bp.model_dump(mode="json")
    raw["illustration_policy_revision"] = 2
    raw["chapters"][0].update(illustration_policy_revision=2,
        illustration_tasks=[task(section_id="1.1", diagram_type="comparison", relation_kind="comparison")])
    apply(w, raw)
    return w


def apply(w, raw):
    p = w.root / "runs/change.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    impact = preview_change_impact(w, p)
    approve(amend_curriculum_blueprint(w, p))
    compile_chapter_contracts(w)
    return impact


def test_graph_change_keeps_factual_definition_but_statement_change_invalidates_it(tmp_path):
    w = graph_workspace(tmp_path)
    value = ClaimClassificationInput(claim_id="claim-example", kind="factual",
        statement="该协议支持固定输入。", heading_anchor="1.1 决策框架", reason="核对真实输入范围")
    append_claim_classification(w, value)
    before = current_claim_contract_bindings(w)
    ledger = (w.root / "research/claim-classifications.jsonl").read_bytes()
    _, _, bp = require_current_submitted_blueprint(w)
    raw = bp.model_dump(mode="json")
    raw["chapters"][0]["illustration_tasks"][0].update(diagram_type="flow", relation_kind="dependency")
    apply(w, raw)
    assert current_claim_contract_bindings(w) == before
    assert (w.root / "research/claim-classifications.jsonl").read_bytes() == ledger
    assert current_claim_classifications(w)["claim-example"].statement == value.statement
    append_claim_classification(w, value.model_copy(update={"statement": "该协议不支持固定输入。"}))
    assert current_claim_contract_bindings(w) != before
    assert current_claim_classifications(w)["claim-example"].statement != value.statement


def test_unchanged_humanize_proof_is_retained_and_forged_impact_cannot_refresh(tmp_path):
    w = graph_workspace(tmp_path)
    source = w.root / "runs/body.md"
    source.write_text("# 1. 教学\n\n## 1.1 决策框架\n\n先确认输入，再比较条件和证据。\n", encoding="utf-8")
    proof, _ = record_humanize_check(w, 1, source)
    before = proof.read_bytes()
    _, _, bp = require_current_submitted_blueprint(w)
    raw = bp.model_dump(mode="json")
    raw["chapters"][0]["illustration_tasks"][0].update(diagram_type="flow", relation_kind="dependency")
    impact = apply(w, raw)
    contract = w.root / "chapters/chapter-01-contract.md"
    require_current_humanize_report(w, 1, source.read_text(encoding="utf-8"), contract)
    receipt = refresh_chapter_proofs(w, 1, impact_id=impact.impact_id)
    assert json.loads(receipt.read_text(encoding="utf-8"))["equivalent_domains"]
    assert proof.read_bytes() == before
    assert refresh_chapter_proofs(w, 1, impact_id=impact.impact_id) == receipt
    with pytest.raises((ValueError, FileNotFoundError)):
        refresh_chapter_proofs(w, 1, impact_id="impact-" + "0" * 64)
    source.write_text(source.read_text(encoding="utf-8") + "新增正文。\n", encoding="utf-8")
    with pytest.raises(ValueError, match="正文"):
        require_current_humanize_report(w, 1, source.read_text(encoding="utf-8"), contract)


def test_diagram_projection_does_not_reuse_a_changed_relationship(tmp_path):
    w = graph_workspace(tmp_path)
    _, _, bp = require_current_submitted_blueprint(w)
    task_id = bp.chapters[0].illustration_tasks[0].task_id
    old = proof_contract_digest(w, 1, "diagram:" + task_id)
    raw = bp.model_dump(mode="json")
    raw["chapters"][0]["illustration_tasks"][0].update(diagram_type="flow", relation_kind="dependency")
    apply(w, raw)
    assert proof_contract_digest(w, 1, "diagram:" + task_id) != old


def test_approved_task_rematerializes_plan_without_reopening_reader_body(tmp_path):
    from knowledge_product_workflow.content_plan import (
        require_current_content_plan,
        submit_content_plan,
    )
    from knowledge_product_workflow.illustration_tasks import approved_illustration_tasks
    from knowledge_product_workflow.presentation import load_or_create_publication_profile
    from knowledge_product_workflow.workflows.authoring import create_chapter_draft
    from tests.test_illustration_policy import plan
    w = graph_workspace(tmp_path)
    load_or_create_publication_profile(w, language="zh-CN")
    create_chapter_draft(w, 1)
    _, _, bp = require_current_submitted_blueprint(w)
    selected = bp.chapters[0].illustration_tasks[0].model_dump(mode="json")
    supplied = w.root / "runs/plan.yaml"
    supplied.write_text(yaml.safe_dump(plan(2, [selected]), allow_unicode=True), encoding="utf-8")
    original = submit_content_plan(w, 1, supplied).read_bytes()
    body = (w.root / "chapters/chapter-01.md").read_bytes()
    native_budget_digest = approved_illustration_tasks(w)[1]
    raw = bp.model_dump(mode="json")
    raw["chapters"][0]["illustration_tasks"][0].update(diagram_type="flow", relation_kind="dependency")
    impact = apply(w, raw)
    refresh_chapter_proofs(w, 1, impact_id=impact.impact_id)
    assert (w.root / "chapters/chapter-01.md").read_bytes() == body
    assert require_current_content_plan(w, 1).sections[0].illustration_tasks[0].diagram_type == "flow"
    assert approved_illustration_tasks(w)[1] == native_budget_digest
    assert any(p.read_bytes() == original for p in (w.root / "runs/content-plan-history").glob("*.yaml"))
