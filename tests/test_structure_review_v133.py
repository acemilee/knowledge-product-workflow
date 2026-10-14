"""Current-input semantic review, budget and public CLI regressions."""

import json

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.developmental import (
    TEACHING_CHECKS,
    DevelopmentalEditError,
    StructureReviewInput,
    create_structure_review_template,
    reading_units,
    record_developmental_check,
    record_structure_review,
    require_current_developmental_report,
)
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from tests.test_chapter_content_plan import _approved_workspace
from tests.test_developmental_editing import _source, _submit_plan


def fixture_teaching_checks(assessment="passed"):
    return {key: {"assessment": assessment,
                  "note": f"测试夹具依据：{description}，对照当前输入与方案示例进行检查。"}
            for key, description in TEACHING_CHECKS.items()}


def simulated_review(workspace, source, assessment="passed", number=1):
    template = create_structure_review_template(workspace, number, source)
    payload = StructureReviewInput(template_id=template["template_id"], observations=[
        {"unit_id": o["unit_id"], "assessment": assessment,
         "teaching_checks": fixture_teaching_checks(assessment),
         "assertion_checks": [
             {"assertion_id": item['assertion_id'], "quote": item['quote'],
              "assessment": 'supported' if assessment=='passed' else 'unsupported' if assessment=='revise' else 'uncertain',
              "support_refs": ['content_plan'], "missing_premises": [] if assessment=='passed' else ['机器模拟未满足条件'],
              "note": '机器夹具校对：仅验证字段和绑定，不代表模型实际核验了主张。'}
             for item in template.get('assertion_prompts', []) if item['unit_id']==o['unit_id']],
         "note": "测试夹具观察：输入、方案维度与预期产出完整对应，保留此因果解释。"}
        for o in template["observations"]
    ])
    return record_structure_review(workspace, number, source, payload)


def _fixture(tmp_path):
    workspace = _approved_workspace(tmp_path)
    _submit_plan(workspace)
    body = _source(workspace, include_worksheet=True)
    source = workspace.root / "runs/chapter-01-developmental-source.md"
    return workspace, source, body


def test_review_cli_binds_current_final_report_without_token_budget(tmp_path):
    workspace, source, body = _fixture(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["developmental-review-template", "1", "--workspace", str(workspace.root),
                                 "--from", str(source)])
    assert result.exit_code == 0, result.output
    template = json.loads(result.output)
    payload = {"template_id": template["template_id"], "observations": [
        {"unit_id": "1.1", "assessment": "passed", "note": "测试观察：方案取舍的输入、比较表与输出示例对应，解释保留。",
         "teaching_checks": fixture_teaching_checks()}
    ]}
    input_path = workspace.root / "runs/structure-input.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = runner.invoke(app, ["record-developmental-review", "1", "--workspace", str(workspace.root),
                                 "--chapter-source", str(source), "--from", str(input_path)])
    assert result.exit_code == 0, result.output
    _, report = record_developmental_check(workspace, 1, source)
    contract, _ = require_current_chapter_contract(workspace, 1)
    assert report.structure_review_sha256
    assert require_current_developmental_report(workspace, 1, body, contract)
    source.write_text(body + "新的未审解释。\n", encoding="utf-8")
    with pytest.raises(DevelopmentalEditError, match="过期"):
        record_developmental_check(workspace, 1, source)


def test_actual_review_is_required_without_token_budget(tmp_path):
    workspace, source, _ = _fixture(tmp_path)
    with pytest.raises(DevelopmentalEditError, match="缺少实际结构审核"):
        record_developmental_check(workspace, 1, source)
    template = create_structure_review_template(workspace, 1, source)
    payload = StructureReviewInput(template_id=template["template_id"], observations=[
        {"unit_id": "1.1", "assessment": "passed", "note": "测试观察：完整说明了方案取舍和输出依据，保留当前解释。",
         "teaching_checks": fixture_teaching_checks()}
    ])
    record_structure_review(workspace, 1, source, payload)
    assert not (workspace.root / "runs/token-budget.json").exists()


def test_revise_remains_blocking_and_empty_observations_are_rejected(tmp_path):
    workspace, source, _ = _fixture(tmp_path)
    template = create_structure_review_template(workspace, 1, source)
    with pytest.raises(DevelopmentalEditError, match="覆盖"):
        record_structure_review(workspace, 1, source,
                                StructureReviewInput(template_id=template["template_id"], observations=[]))
    simulated_review(workspace, source, "revise")
    with pytest.raises(DevelopmentalEditError, match="待修订"):
        record_developmental_check(workspace, 1, source)


def test_contiguous_prose_is_flagged_and_lists_and_code_are_not_prose():
    paragraphs = "\n\n".join("这里解释一个连续的因果条件。" * 10 for _ in range(4))
    body = "## 1.1 因果\n\n" + paragraphs
    assert any(w["kind"] == "density" for w in reading_units(body)[0]["warnings"])
    structured = "## 1.1 检查\n\n" + "\n".join("- " + "清楚的检查条件。" * 30 for _ in range(4))
    structured += "\n\n```text\n" + "原样保留。" * 100 + "\n```\n"
    assert reading_units(structured)[0]["warnings"] == []
    structured += "\n```markdown\n## 9.9 文件内标题\n原样保存。\n```\n"
    assert [u["unit_id"] for u in reading_units(structured)] == ["1.1"]


def test_short_enumeration_is_flagged_and_causal_explanation_can_be_approved(tmp_path):
    assert reading_units("## 1.1 验收\n\n检查四项：名称、用途、路径、结果。\n")[0]["warnings"]
    workspace, source, body = _fixture(tmp_path)
    source.write_text(body + "\n" + "这是具有共同条件的因果解释。" * 35 + "\n", encoding="utf-8")
    simulated_review(workspace, source)
    _, report = record_developmental_check(workspace, 1, source)
    assert report.findings and all(f.severity.value == "warning" for f in report.findings)
    result = CliRunner().invoke(app, ["developmental-check", "1", "--workspace", str(workspace.root),
                                    "--from", str(source)])
    assert result.exit_code == 0, result.output
    assert "阻断项=0" in result.output
    assert "警告项=" in result.output


def test_policy_adoption_preview_is_readonly_and_confirm_preserves_artifacts(tmp_path):
    from knowledge_product_workflow.runtime import (
        _atomic_json_write,
        _orchestration_state_path,
        _read_orchestration_state,
    )
    workspace, source, _ = _fixture(tmp_path)
    state = _read_orchestration_state(workspace).model_copy(update={"reading_structure_revision": 1,
                                                                 "reading_review_rubric_revision": 1})
    _atomic_json_write(_orchestration_state_path(workspace), state)
    simulated_review(workspace, source)
    saved = {p: p.read_bytes() for p in [source, workspace.root / "planning/product-brief.md",
                                        workspace.root / "planning/publication-profile.yaml"]}
    runner = CliRunner()
    assert runner.invoke(app, ["adopt-reading-policy", "--workspace", str(workspace.root)]).exit_code == 0
    assert _read_orchestration_state(workspace).reading_structure_revision == 1
    result = runner.invoke(app, ["adopt-reading-policy", "--workspace", str(workspace.root), "--confirm"])
    assert result.exit_code == 0, result.output
    assert _read_orchestration_state(workspace).reading_structure_revision == 2
    assert all(p.read_bytes() == contents for p, contents in saved.items())
    with pytest.raises(DevelopmentalEditError, match="过期"):
        record_developmental_check(workspace, 1, source)
    before = (workspace.root / "runs/events.jsonl").read_bytes()
    assert runner.invoke(app, ["adopt-reading-policy", "--workspace", str(workspace.root), "--confirm"]).exit_code == 0
    assert (workspace.root / "runs/events.jsonl").read_bytes() == before


def test_unchanged_review_can_be_rebound_without_new_model_consumption(tmp_path):
    workspace, source, _ = _fixture(tmp_path)
    original = simulated_review(workspace, source).read_bytes()
    template = create_structure_review_template(workspace, 1, source)
    assert template["reusable_unit_ids"] == ["1.1"]
    path = record_structure_review(workspace, 1, source, StructureReviewInput(
        template_id=template["template_id"], observations=[], reuse_unit_ids=["1.1"]))
    assert json.loads(path.read_text(encoding="utf-8"))["reused_unit_ids"] == ["1.1"]
    assert original in [p.read_bytes() for p in path.parent.glob("history/chapter-01/*.json")]
    source.write_text(source.read_text(encoding="utf-8") + "新增解释改变当前单元。\n", encoding="utf-8")
    stale = create_structure_review_template(workspace, 1, source)
    assert stale["reusable_unit_ids"] == []
    with pytest.raises(DevelopmentalEditError, match="覆盖"):
        record_structure_review(workspace, 1, source, StructureReviewInput(
            template_id=stale["template_id"], observations=[], reuse_unit_ids=["1.1"]))


def test_review_context_invalidates_adjacent_units_and_chapter_introduction():
    body = "章导读。\n\n## 1.1 第一单元\n\n先明确条件。\n\n## 1.2 第二单元\n\n比较条件。\n\n## 1.3 第三单元\n\n保存结果。\n"
    original = reading_units(body)
    revised = reading_units(body.replace("先明确条件。", "先明确另一组条件。"))
    assert [a["context_sha256"] == b["context_sha256"] for a, b in zip(original, revised)] == [False, False, True]
    changed_intro = reading_units(body.replace("章导读。", "章导读改为另一种假设。"))
    assert all(a["context_sha256"] != b["context_sha256"] for a, b in zip(original, changed_intro))
    linked = body.replace("保存结果。", "根据1.1节的条件保存结果。")
    linked_original = reading_units(linked)
    linked_revised = reading_units(linked.replace("先明确条件。", "先明确另一组条件。"))
    assert linked_original[2]["context_sha256"] != linked_revised[2]["context_sha256"]


def test_evidence_change_rejects_reuse_and_current_semantic_proof(tmp_path):
    workspace, source, _ = _fixture(tmp_path)
    simulated_review(workspace, source)
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"]
    ledger = workspace.root / "research/source-ledger.jsonl"
    ledger.write_text('{"fixture_source_revision": 2}\n', encoding="utf-8")
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"] == []
    with pytest.raises(DevelopmentalEditError, match="过期"):
        record_developmental_check(workspace, 1, source)


def test_referenced_chapter_change_rejects_review_reuse(tmp_path):
    workspace, source, body = _fixture(tmp_path)
    source.write_text(body + "\n参照第2章的2.1节核对条件。\n", encoding="utf-8")
    other = workspace.root / "chapters/chapter-02.md"
    other.write_text("## 2.1 条件\n\n这是原条件。\n", encoding="utf-8")
    simulated_review(workspace, source)
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"]
    other.write_text("## 2.1 条件\n\n这是新的条件。\n", encoding="utf-8")
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"] == []


def test_unrelated_claim_evidence_does_not_repeat_semantic_review(tmp_path):
    workspace, source, _ = _fixture(tmp_path)
    simulated_review(workspace, source)
    ledger = workspace.root / "research/claim-classifications.jsonl"
    ledger.write_text('{"claim_id":"claim-other-chapter","statement":"new unrelated evidence"}\n', encoding="utf-8")
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"] == ["1.1"]
    record_developmental_check(workspace, 1, source)
    from knowledge_product_workflow.claims import claim_scoped_evidence_digests
    before = claim_scoped_evidence_digests(workspace, {"claim-relevant"})
    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"claim_id": "claim-relevant", "statement": "changed relevant evidence"}) + "\n")
    assert claim_scoped_evidence_digests(workspace, {"claim-relevant"}) != before


def test_malformed_evidence_cannot_preserve_scoped_review(tmp_path):
    workspace, source, _ = _fixture(tmp_path)
    simulated_review(workspace, source)
    (workspace.root / "research/claim-classifications.jsonl").write_text('not-json\n', encoding="utf-8")
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"] == []


def test_legacy_full_ledger_proof_migrates_only_with_exact_context(tmp_path):
    from knowledge_product_workflow.developmental import _review_context_digest
    workspace, source, body = _fixture(tmp_path)
    path = simulated_review(workspace, source)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["review_context_sha256"] = _review_context_digest(workspace, 1, body, scoped=False)
    path.write_text(json.dumps(data), encoding="utf-8")
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"] == ["1.1"]
    record_developmental_check(workspace, 1, source)
    (workspace.root / "research/source-ledger.jsonl").write_text('{"claim_ids":["claim-other"],"revision":2}\n', encoding="utf-8")
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"] == []
    with pytest.raises(DevelopmentalEditError, match="过期"):
        record_developmental_check(workspace, 1, source)
