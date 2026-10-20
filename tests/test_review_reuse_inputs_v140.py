"""Review evidence must be tracked before deterministic reuse is allowed."""
import os

import pytest

from knowledge_product_workflow.example_checks import example_evidence_for_body, run_python_example
from knowledge_product_workflow.verification_context import SnapshotChangedError, verification_scope
from knowledge_product_workflow.workflows.product_brief import (
    read_request_contract,
    request_contract_path,
)
from knowledge_product_workflow.workspace import Workspace


def test_fresh_review_does_not_revalidate_unused_historical_observations(tmp_path, monkeypatch):
    import json

    from knowledge_product_workflow import developmental as dev
    from tests.test_structure_review_v133 import _fixture, simulated_review

    workspace, source, _ = _fixture(tmp_path)
    original_path = simulated_review(workspace, source)
    historical = json.loads(original_path.read_text(encoding="utf-8"))
    template = dev.create_structure_review_template(workspace, 1, source)
    assert template["reusable_unit_ids"] == ["1.1"]
    payload = dev.StructureReviewInput(
        template_id=template["template_id"], observations=historical["observations"]
    )
    calls = []
    original = dev._reusable_observations

    def observed(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(dev, "_reusable_observations", observed)
    output = dev.record_structure_review(workspace, 1, source, payload)
    assert len(calls) == 1  # The current template still validates historical reuse eligibility.
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["reused_unit_ids"] == []
    assert saved["reuse_evidence_sha256"] is None
    assert saved["observations"] == [item.model_dump() for item in payload.observations]


def test_explicit_observation_reuse_keeps_second_current_validation(tmp_path, monkeypatch):
    import json

    from knowledge_product_workflow import developmental as dev
    from tests.test_structure_review_v133 import _fixture, simulated_review

    workspace, source, _ = _fixture(tmp_path)
    simulated_review(workspace, source)
    template = dev.create_structure_review_template(workspace, 1, source)
    calls = []
    original = dev._reusable_observations

    def observed(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(dev, "_reusable_observations", observed)
    output = dev.record_structure_review(workspace, 1, source, dev.StructureReviewInput(
        template_id=template["template_id"], observations=[], reuse_unit_ids=["1.1"]
    ))
    assert len(calls) == 2
    assert json.loads(output.read_text(encoding="utf-8"))["reused_unit_ids"] == ["1.1"]


def executed_example(tmp_path):
    workspace = Workspace.create(tmp_path, "review-inputs")
    source = workspace.root / "runs/source.md"
    body = "## 1.1 固定例子\n\n```python\nprint(1)\n```\n"
    source.write_text(body, encoding="utf-8")
    evidence = run_python_example(workspace, source, 1, approved=True)
    return workspace, body, evidence


@pytest.mark.parametrize("name", ["result.json", "example.py", "stdout.bin", "stderr.bin"])
def test_example_evidence_external_same_mtime_edit_rejects_snapshot(tmp_path, name):
    workspace, body, evidence = executed_example(tmp_path)
    target = evidence.parent / name
    before = target.stat()
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        assert len(example_evidence_for_body(workspace, body)) == 1
        target.write_bytes(target.read_bytes() + b" ")
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))


def test_example_evidence_external_new_result_rejects_snapshot(tmp_path):
    workspace, body, evidence = executed_example(tmp_path)
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        assert len(example_evidence_for_body(workspace, body)) == 1
        other = evidence.parent.parent / "new-record"
        other.mkdir()
        (other / "result.json").write_text("{}", encoding="utf-8")


def test_request_contract_external_same_mtime_edit_rejects_snapshot(tmp_path):
    workspace = Workspace.create(tmp_path, "request-inputs")
    target = request_contract_path(workspace)
    target.write_text("schema_version: 1\nrequirements: {}\n", encoding="utf-8")
    before = target.stat()
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        assert read_request_contract(workspace)["schema_version"] == 1
        target.write_text("schema_version: 2\nrequirements: {}\n", encoding="utf-8")
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))


@pytest.mark.parametrize("related", [False, True])
def test_example_memo_tracks_controlled_output_and_keeps_unrelated_writes(tmp_path, related):
    from knowledge_product_workflow.verification_context import write_bytes

    workspace, body, evidence = executed_example(tmp_path)
    calls = []

    def query():
        calls.append(1)
        return example_evidence_for_body(workspace, body)

    with verification_scope(workspace) as context:
        assert len(context.memo("example-probe", query)) == 1
        target = evidence.parent / "stdout.bin" if related else workspace.root / "runs/unrelated.txt"
        write_bytes(target, b"changed")
        assert len(context.memo("example-probe", query)) == (0 if related else 1)
        assert len(calls) == (2 if related else 1)


def test_request_memo_tracks_controlled_contract_changes(tmp_path):
    from knowledge_product_workflow.verification_context import write_text

    workspace = Workspace.create(tmp_path, "request-memo")
    target = request_contract_path(workspace)
    target.write_text("schema_version: 1\n", encoding="utf-8")
    with verification_scope(workspace) as context:
        query = lambda: read_request_contract(workspace)
        assert context.memo("request", query)["schema_version"] == 1
        write_text(target, "schema_version: 2\n", encoding="utf-8")
        assert context.memo("request", query)["schema_version"] == 2


def test_review_template_rejects_evidence_change_at_index_write_boundary(tmp_path, monkeypatch):
    from knowledge_product_workflow import developmental
    from tests.test_structure_review_v133 import _fixture

    workspace, source, body = _fixture(tmp_path)
    source.write_text(body + "\n```python\nprint(1)\n```\n", encoding="utf-8")
    evidence = run_python_example(workspace, source, 1, approved=True)
    target = evidence.parent / "stdout.bin"
    original_write = developmental._v_write_text
    changed = []

    def external_change(path, *args, **kwargs):
        if path.name == "book-reading-index.json" and not changed:
            changed.append(True)
            before = target.stat()
            target.write_bytes(b"2\r\n")
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(developmental, "_v_write_text", external_change)
    with pytest.raises(SnapshotChangedError):
        developmental.create_structure_review_template(workspace, 1, source)
    assert changed


def test_record_builds_assertion_support_context_once_per_action(tmp_path, monkeypatch):
    import knowledge_product_workflow.workflows.product_brief as brief
    from knowledge_product_workflow.developmental import record_structure_review
    from tests.test_review_assertions_v134 import assertion_fixture, payload_for

    workspace, source, template = assertion_fixture(tmp_path)
    original_read = brief.read_request_contract
    calls = []

    def read(workspace):
        calls.append(1)
        return original_read(workspace)

    monkeypatch.setattr(brief, "read_request_contract", read)
    output = record_structure_review(workspace, 1, source, payload_for(template))
    assert output.is_file()
    assert len(calls) == 1


def test_empty_assertion_set_does_not_load_support_context(monkeypatch):
    from knowledge_product_workflow import developmental

    def unexpected(*args):
        pytest.fail("没有命题的单元不应扫描支撑记录")

    observation = developmental.StructureObservation(
        unit_id="1.1", assessment="passed", note="测试单元提供文字说明和读者操作边界，无结果主张。")
    monkeypatch.setattr(developmental, "_assertion_support_context", unexpected)
    developmental._validate_assertion_checks(None, observation, "## 1.1 阅读\n解释概念与边界。\n", 3)


def test_support_context_tracks_request_changes_and_returns_isolated_values(tmp_path, monkeypatch):
    import knowledge_product_workflow.workflows.product_brief as brief
    from knowledge_product_workflow.developmental import _assertion_support_context
    from knowledge_product_workflow.verification_context import write_text

    workspace = Workspace.create(tmp_path, "support-memo")
    target = request_contract_path(workspace)
    old = "requirements:\n  fixed_inputs:\n    value: [old]\n"
    new = old.replace("old", "new")
    target.write_text(old, encoding="utf-8")
    calls = []
    original = brief.read_request_contract

    def read(workspace):
        calls.append(1)
        return original(workspace)

    monkeypatch.setattr(brief, "read_request_contract", read)
    with verification_scope(workspace):
        result = _assertion_support_context(workspace, "## 1.1 说明\n已有输入。\n")
        fixed = next(item for item in result if item["ref"] == "reader_request.fixed_inputs")
        fixed["value"].append("caller-mutation")
        write_text(workspace.root / "runs/unrelated.txt", "unrelated", encoding="utf-8")
        again = _assertion_support_context(workspace, "## 1.1 说明\n已有输入。\n")
        assert next(item["value"] for item in again if item["ref"] == "reader_request.fixed_inputs") == ["old"]
        assert len(calls) == 1
        write_text(target, new, encoding="utf-8")
        current = _assertion_support_context(workspace, "## 1.1 说明\n已有输入。\n")
        assert next(item["value"] for item in current if item["ref"] == "reader_request.fixed_inputs") == ["new"]
        assert len(calls) == 2


def test_support_context_rechecks_changed_execution_output(tmp_path):
    from knowledge_product_workflow.developmental import _assertion_support_context
    from knowledge_product_workflow.verification_context import write_bytes

    workspace, body, evidence = executed_example(tmp_path)
    with verification_scope(workspace):
        before = _assertion_support_context(workspace, body)
        assert any(item["ref"] == "example:0" for item in before)
        write_bytes(evidence.parent / "stdout.bin", b"different output")
        after = _assertion_support_context(workspace, body)
        assert not any(item["ref"].startswith("example:") for item in after)


def test_support_context_does_not_lose_nested_directory_dependencies(tmp_path, monkeypatch):
    from knowledge_product_workflow import developmental
    from knowledge_product_workflow.verification_context import mkdir, write_text

    workspace, body, evidence = executed_example(tmp_path)
    new = evidence.parent.parent / "new-record" / "result.json"
    calls = []
    original = developmental._build_assertion_support_context

    def build(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(developmental, "_build_assertion_support_context", build)
    with verification_scope(workspace) as context:
        query = lambda: developmental._assertion_support_context(workspace, body)
        assert any(item["ref"] == "example:0" for item in query())
        assert context.memo("outer", query)
        mkdir(new.parent)
        write_text(new, "{}", encoding="utf-8")
        # Even an invalid new candidate is part of the observed directory set.
        assert context.memo("outer", query)
        assert len(calls) == 2


def test_cached_support_still_rejects_external_output_change(tmp_path):
    from knowledge_product_workflow.developmental import _assertion_support_context

    workspace, body, evidence = executed_example(tmp_path)
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        assert any(item["ref"] == "example:0" for item in _assertion_support_context(workspace, body))
        target = evidence.parent / "stdout.bin"
        before = target.stat()
        target.write_bytes(b"mutated externally")
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        # A cache hit remains only a snapshot; the write/exit guard rejects it.
        _assertion_support_context(workspace, body)


def test_retargeted_request_alias_rejects_before_publishing_old_support(tmp_path):
    import json

    from knowledge_product_workflow.developmental import _assertion_support_context
    from knowledge_product_workflow.verification_context import write_text

    workspace = Workspace.create(tmp_path, "request-alias")
    old, new = (workspace.root / "runs" / f"{name}.yaml" for name in ("old", "new"))
    for path, value in ((old, "old"), (new, "new")):
        path.write_text(f"requirements:\n  fixed_inputs:\n    value: [{value}]\n", encoding="utf-8")
    alias = request_contract_path(workspace)
    alias.symlink_to(old)
    output = workspace.root / "runs/support-result.json"
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        support = _assertion_support_context(workspace, "## 1.1 输入\n给定条件。\n")
        alias.unlink()
        alias.symlink_to(new)
        with pytest.raises(SnapshotChangedError):
            write_text(output, json.dumps(support), encoding="utf-8")
        assert not output.exists()
