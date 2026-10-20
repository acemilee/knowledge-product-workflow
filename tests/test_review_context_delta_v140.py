"""Actionable stale-review diagnostics never confer reuse authority."""
import hashlib
import json

import pytest

from knowledge_product_workflow.developmental import (
    DevelopmentalEditError,
    create_structure_review_template,
    record_developmental_check,
)
from tests.test_structure_review_v133 import _fixture, simulated_review


def reviewed_reference(tmp_path):
    workspace, source, body = _fixture(tmp_path)
    source.write_text(body + "\n参照第2章的2.1节核对条件。\n", encoding="utf-8")
    related = workspace.root / "chapters/chapter-02.md"
    related.write_text("## 2.1 条件\n\n原条件。\n\n## 2.2 其他\n\n无关内容。\n", encoding="utf-8")
    proof = simulated_review(workspace, source)
    return workspace, source, related, proof


def test_record_preserves_digest_bound_inputs_and_unchanged_delta(tmp_path):
    workspace, source, _, proof = reviewed_reference(tmp_path)
    data = json.loads(proof.read_text(encoding="utf-8"))
    snapshot = data["review_context_inputs"]
    assert hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest() == data["review_context_sha256"]
    before = proof.read_bytes()
    template = create_structure_review_template(workspace, 1, source)
    assert template["review_context_delta"]["status"] == "unchanged"
    assert template["review_context_delta"]["changes"] == []
    assert template["reusable_unit_ids"] == ["1.1"]
    assert proof.read_bytes() == before


def test_related_section_delta_identifies_old_new_input_and_recheck_unit(tmp_path):
    workspace, source, related, proof = reviewed_reference(tmp_path)
    old = json.loads(proof.read_text(encoding="utf-8"))
    related.write_text(related.read_text(encoding="utf-8").replace("原条件", "新条件"), encoding="utf-8")
    template = create_structure_review_template(workspace, 1, source)
    delta = template["review_context_delta"]
    assert delta["status"] == "changed"
    change = next(c for c in delta["changes"] if c.get("kind") == "section")
    assert (change["chapter"], change["unit"]) == (2, "2.1")
    assert change["old_sha256"] == next(c["sha256"] for c in old["review_context_inputs"]["references"] if c["kind"] == "section")
    assert change["old_sha256"] != change["new_sha256"]
    assert delta["affected_unit_ids"] == ["1.1"]
    assert template["reusable_unit_ids"] == []
    with pytest.raises(DevelopmentalEditError, match=r"chapter=2.*unit=2\.1"):
        record_developmental_check(workspace, 1, source)


def test_unrelated_section_does_not_invent_dependency_changes(tmp_path):
    workspace, source, related, _ = reviewed_reference(tmp_path)
    related.write_text(related.read_text(encoding="utf-8").replace("无关内容", "无关内容更新"), encoding="utf-8")
    template = create_structure_review_template(workspace, 1, source)
    assert template["review_context_delta"]["status"] == "unchanged"
    assert template["reusable_unit_ids"] == ["1.1"]
    record_developmental_check(workspace, 1, source)


@pytest.mark.parametrize("snapshot", [None, {"references": [{"kind": "section", "chapter": 999, "unit": "999.1", "sha256": "forged"}]}])
def test_missing_or_forged_history_never_explains_or_approves_a_change(tmp_path, snapshot):
    workspace, source, related, proof = reviewed_reference(tmp_path)
    data = json.loads(proof.read_text(encoding="utf-8"))
    if snapshot is None:
        data.pop("review_context_inputs", None)
    else:
        data["review_context_inputs"] = snapshot
    proof.write_text(json.dumps(data), encoding="utf-8")
    old = proof.read_bytes()
    related.write_text(related.read_text(encoding="utf-8").replace("原条件", "新条件"), encoding="utf-8")
    delta = create_structure_review_template(workspace, 1, source)["review_context_delta"]
    assert delta["status"] == ("history_unavailable" if snapshot is None else "invalid_history")
    assert delta["changes"] == []
    with pytest.raises(DevelopmentalEditError, match="过期"):
        record_developmental_check(workspace, 1, source)
    assert proof.read_bytes() == old


def test_missing_optional_history_keeps_current_old_review_valid(tmp_path):
    workspace, source, _, proof = reviewed_reference(tmp_path)
    data = json.loads(proof.read_text(encoding="utf-8"))
    data.pop("review_context_inputs", None)
    proof.write_text(json.dumps(data), encoding="utf-8")
    old = proof.read_bytes()
    template = create_structure_review_template(workspace, 1, source)
    assert template["review_context_delta"]["status"] == "history_unavailable"
    assert template["reusable_unit_ids"] == ["1.1"]
    record_developmental_check(workspace, 1, source)
    assert proof.read_bytes() == old


def test_many_changes_are_explicitly_bounded_without_echoing_payload():
    from knowledge_product_workflow.developmental import _review_context_delta

    before = {"references": [{"kind": "section", "chapter": n, "unit": f"{n}.1", "sha256": "0" * 64}
                             for n in range(1, 81)]}
    after = {"references": [{**r, "sha256": "1" * 64} for r in before["references"]]}
    previous = {"dependency_revision": 3, "review_context_inputs": before,
                "review_context_sha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()}
    delta = _review_context_delta(previous, after)
    assert delta["status"] == "changed"
    assert delta["change_count"] == 80
    assert len(delta["changes"]) == 64
    assert delta["changes_truncated"] is True
    assert delta["diagnostic_only"] is True


def test_digest_matching_but_malformed_history_is_not_an_exception(tmp_path):
    workspace, source, related, proof = reviewed_reference(tmp_path)
    data = json.loads(proof.read_text(encoding="utf-8"))
    data["review_context_inputs"] = {"claims": ["DO_NOT_ECHO_RAW_INPUT"]}
    data["review_context_sha256"] = hashlib.sha256(json.dumps(data["review_context_inputs"], sort_keys=True).encode()).hexdigest()
    proof.write_text(json.dumps(data), encoding="utf-8")
    related.write_text(related.read_text(encoding="utf-8").replace("原条件", "新条件"), encoding="utf-8")
    template = create_structure_review_template(workspace, 1, source)
    assert template["review_context_delta"]["status"] == "invalid_history"
    assert "DO_NOT_ECHO_RAW_INPUT" not in json.dumps(template["review_context_delta"])
    with pytest.raises(DevelopmentalEditError, match="过期"):
        record_developmental_check(workspace, 1, source)


def test_current_proof_check_does_not_build_diagnostics(tmp_path, monkeypatch):
    import knowledge_product_workflow.developmental as module

    workspace, source, _, _ = reviewed_reference(tmp_path)
    def unexpected(*args, **kwargs):
        raise AssertionError("Current proof should not build a stale-input explanation")
    monkeypatch.setattr(module, "_review_context_delta", unexpected)
    record_developmental_check(workspace, 1, source)


def test_repeated_templates_parse_same_proof_once_per_action(tmp_path, monkeypatch):
    from knowledge_product_workflow.verification_context import verification_scope

    workspace, source, _, proof = reviewed_reference(tmp_path)
    raw = proof.read_text(encoding="utf-8")
    calls = []
    original = json.loads
    def counted(value, *args, **kwargs):
        if value == raw:
            calls.append(1)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(json, "loads", counted)
    with verification_scope(workspace):
        create_structure_review_template(workspace, 1, source)
        create_structure_review_template(workspace, 1, source)
    assert len(calls) == 1


def test_controlled_proof_change_keeps_delta_separate_from_reuse(tmp_path):
    from knowledge_product_workflow.verification_context import verification_scope, write_text

    workspace, source, _, proof = reviewed_reference(tmp_path)
    with verification_scope(workspace):
        assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"] == ["1.1"]
        data = json.loads(proof.read_text(encoding="utf-8"))
        data["observations"][0]["assessment"] = "revise"
        write_text(proof, json.dumps(data), encoding="utf-8")
        template = create_structure_review_template(workspace, 1, source)
        assert template["review_context_delta"]["status"] == "unchanged"
        assert template["reusable_unit_ids"] == []


@pytest.mark.parametrize("content", ["[]", "false", "{"])
def test_unreadable_proof_has_invalid_history_and_controlled_rejection(tmp_path, content):
    workspace, source, _, proof = reviewed_reference(tmp_path)
    proof.write_text(content, encoding="utf-8")
    template = create_structure_review_template(workspace, 1, source)
    assert template["review_context_delta"]["status"] == "invalid_history"
    assert template["reusable_unit_ids"] == []
    with pytest.raises(DevelopmentalEditError, match="无效"):
        record_developmental_check(workspace, 1, source)


def test_claim_delta_names_ledger_without_echoing_new_source_row(tmp_path):
    workspace, source, _, _ = reviewed_reference(tmp_path)
    ledger = workspace.root / "research/source-ledger.jsonl"
    ledger.write_text('{"fixture_private_row": "DO_NOT_ECHO_SOURCE_TEXT"}\n', encoding="utf-8")
    template = create_structure_review_template(workspace, 1, source)
    delta = template["review_context_delta"]
    assert any(c["domain"] == "claims" and c["input"] == "research/source-ledger.jsonl" for c in delta["changes"])
    assert "DO_NOT_ECHO_SOURCE_TEXT" not in json.dumps(delta)
    assert template["reusable_unit_ids"] == []


def test_compared_case_delta_is_distinct_from_unchanged_reference_context(tmp_path):
    from knowledge_product_workflow.developmental import (
        StructureReviewInput,
        record_structure_review,
    )

    workspace, source, _ = _fixture(tmp_path)
    other = workspace.root / "chapters/chapter-02.md"
    other.write_text("## 2.1 案例\n\n原规则。\n\n## 2.2 其他\n\n原补充。\n", encoding="utf-8")
    previous = json.loads(simulated_review(workspace, source).read_text(encoding="utf-8"))
    template = create_structure_review_template(workspace, 1, source)
    record_structure_review(workspace, 1, source, StructureReviewInput(
        template_id=template["template_id"],
        observations=[{**o, "compared_unit_ids": ["2.1"]} for o in previous["observations"]]))
    other.write_text(other.read_text(encoding="utf-8").replace("原补充", "无关补充变更"), encoding="utf-8")
    template = create_structure_review_template(workspace, 1, source)
    assert template["comparison_context_delta"]["status"] == "unchanged"
    assert template["reusable_unit_ids"] == ["1.1"]
    other.write_text(other.read_text(encoding="utf-8").replace("原规则", "新规则"), encoding="utf-8")
    template = create_structure_review_template(workspace, 1, source)
    assert template["review_context_delta"]["status"] == "unchanged"
    delta = template["comparison_context_delta"]
    assert delta["status"] == "changed"
    assert delta["changes"][0]["unit"] == "2.1"
    assert delta["changes"][0]["old_sha256"] != delta["changes"][0]["new_sha256"]
    assert template["reusable_unit_ids"] == []
    with pytest.raises(DevelopmentalEditError, match=r"unit=2\.1"):
        record_developmental_check(workspace, 1, source)


@pytest.mark.parametrize("domain", ["references", "claims", "declared_results", "approved_diagram_tasks"])
def test_resealed_large_historical_fields_cannot_expand_public_diagnostics(tmp_path, domain):
    workspace, source, _, proof = reviewed_reference(tmp_path)
    data = json.loads(proof.read_text(encoding="utf-8"))
    poison = "DO_NOT_ECHO_" + "X" * 200_000
    snapshot = data["review_context_inputs"]
    if domain == "references":
        snapshot["references"][0]["sha256"] = poison
    elif domain == "approved_diagram_tasks":
        snapshot[domain] = [{"task_id": poison, "section_id": "1.1"}]
    else:
        snapshot[domain] = {"some_input": poison}
    data["review_context_sha256"] = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    proof.write_text(json.dumps(data), encoding="utf-8")
    template = create_structure_review_template(workspace, 1, source)
    delta = template["review_context_delta"]
    assert delta["status"] == "invalid_history"
    assert len(json.dumps(delta)) < 2000
    assert "DO_NOT_ECHO" not in json.dumps(delta)
    assert template["reusable_unit_ids"] == []
    with pytest.raises(DevelopmentalEditError) as caught:
        record_developmental_check(workspace, 1, source)
    assert len(str(caught.value)) < 2000
    assert "DO_NOT_ECHO" not in str(caught.value)


def test_invalid_optional_snapshot_does_not_revoke_a_current_proof(tmp_path):
    workspace, source, _, proof = reviewed_reference(tmp_path)
    data = json.loads(proof.read_text(encoding="utf-8"))
    data["review_context_inputs"]["references"][0]["sha256"] = "broken"
    proof.write_text(json.dumps(data), encoding="utf-8")
    old = proof.read_bytes()
    template = create_structure_review_template(workspace, 1, source)
    assert template["review_context_delta"]["status"] == "invalid_history"
    assert template["reusable_unit_ids"] == ["1.1"]
    record_developmental_check(workspace, 1, source)
    assert proof.read_bytes() == old


def test_invalid_stored_context_digest_is_not_echoed(tmp_path):
    workspace, source, _, proof = reviewed_reference(tmp_path)
    data = json.loads(proof.read_text(encoding="utf-8"))
    data["review_context_sha256"] = "INVALID_CONTEXT_" + "X" * 200_000
    proof.write_text(json.dumps(data), encoding="utf-8")
    delta = create_structure_review_template(workspace, 1, source)["review_context_delta"]
    assert delta["status"] == "invalid_history"
    assert delta["old_context_sha256"] is None
    assert len(json.dumps(delta)) < 2000
