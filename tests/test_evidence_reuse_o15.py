"""Public diagnostics expose verified reuse without executing or changing proofs."""

import json

import pytest

from knowledge_product_workflow.chapter_compliance import check_chapter
from knowledge_product_workflow.developmental import record_developmental_check
from knowledge_product_workflow.example_checks import example_template, run_python_example
from knowledge_product_workflow.humanize import record_humanize_check
from knowledge_product_workflow.workspace import Workspace
from tests.test_structure_review_v133 import _fixture, simulated_review


def test_template_reuses_actual_payload_after_prose_change(tmp_path):
    workspace = Workspace.create(tmp_path, "reuse")
    source = workspace.root / "runs/source.md"
    source.write_text("说明。\n```python\nprint(7)\n```\n", encoding="utf-8")
    proof = run_python_example(workspace, source, 1, approved=True)
    before = proof.read_bytes()
    source.write_text("新的说明。\n```python\nprint(7)\n```\n", encoding="utf-8")
    events = (workspace.root / "runs/events.jsonl").read_bytes()
    listing = example_template(workspace, source)
    item = listing["examples"][0]
    assert item["binding_state"] == "current"
    assert item["needs_execution"] is False
    assert item["evidence_path"] == proof.relative_to(workspace.root).as_posix()
    assert item["reuse_reason"] == "verified_code_payload_unchanged"
    assert proof.read_bytes() == before
    assert (workspace.root / "runs/events.jsonl").read_bytes() == events


@pytest.mark.parametrize("target", ["code", "script", "stdout"])
def test_template_never_reuses_changed_or_tampered_execution(tmp_path, target):
    workspace = Workspace.create(tmp_path, "reuse")
    source = workspace.root / "runs/source.md"
    source.write_text("```python\nprint(7)\n```\n", encoding="utf-8")
    proof = run_python_example(workspace, source, 1, approved=True)
    record = json.loads(proof.read_text(encoding="utf-8"))
    if target == "code":
        source.write_text("```python\nprint(8)\n```\n", encoding="utf-8")
    elif target == "script":
        (workspace.root / record["script"]).write_text("print(8)\n", encoding="utf-8")
    else:
        (proof.parent / "stdout.bin").write_bytes(b"8\n")
    item = example_template(workspace, source)["examples"][0]
    assert item["binding_state"] == "missing_or_invalid"
    assert item["needs_execution"] is True
    assert item["evidence_path"] is None


def test_preflight_exposes_current_humanize_proof_without_rewriting(tmp_path):
    workspace, source, _ = _fixture(tmp_path)
    path, _ = record_humanize_check(workspace, 1, source)
    before = path.read_bytes()
    events = (workspace.root / "runs/events.jsonl").read_bytes()
    item = check_chapter(workspace, 1, source)["checks"]["humanize_report"]
    assert item["state"] == "current"
    assert item["reusable"] is True
    assert item["path"] == path.relative_to(workspace.root).as_posix()
    assert path.read_bytes() == before
    assert (workspace.root / "runs/events.jsonl").read_bytes() == events
    source.write_text(source.read_text(encoding="utf-8") + "新增解释。\n", encoding="utf-8")
    stale = check_chapter(workspace, 1, source)["checks"]["humanize_report"]
    assert stale["state"] == "stale"
    assert stale["reusable"] is False


def test_developmental_proof_path_points_to_existing_current_record(tmp_path):
    workspace, source, _ = _fixture(tmp_path)
    simulated_review(workspace, source)
    proof, _ = record_developmental_check(workspace, 1, source)
    item = check_chapter(workspace, 1, source)["checks"]["developmental_report"]
    assert item["reusable"] is True
    assert workspace.root / item["path"] == proof
    assert proof.is_file()
