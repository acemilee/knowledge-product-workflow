"""无参数真实入口只签default范围，不能从成功stdout推断全部配置。"""
import json

import pytest

from knowledge_product_workflow.example_checks import example_template, run_python_resource
from tests.test_reader_resource_binding_v142 import resource_case as _resource_case

resource_case = _resource_case


def test_default_run_and_template_expose_only_actual_entrypoint_scope(resource_case):
    w, _program, body = resource_case
    source = w.root / "runs/scope-candidate.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(body, encoding="utf-8")
    result = run_python_resource(w, source, "assets/examples/main.py", approved=True)
    raw = json.loads(result.read_text(encoding="utf-8"))
    assert raw["execution_scope"] == {"entrypoint":"default","argv":[],"coverage":"default_entrypoint_only"}
    template = example_template(w, source)
    assert template["resources"][0]["execution_scope"]["coverage"] == "default_entrypoint_only"
    assert template["branch_review_guidance"]["actual_model_training_inferred"] is False


def test_legacy_scope_is_derived_but_current_record_tampering_is_rejected(resource_case):
    import hashlib
    w, _, body = resource_case
    source = w.root/"runs/legacy.md"
    source.write_text(body, encoding="utf-8")
    result = run_python_resource(w, source, "assets/examples/main.py", approved=True)
    data = json.loads(result.read_text(encoding="utf-8"))
    data.pop("execution_scope")
    result.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    # Simulated historical record and original matching event, not a production migration.
    events = w.root/"runs/events.jsonl"
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row.get("event_type") == "example_executed" and row["payload"].get("evidence") == result.relative_to(w.root).as_posix():
            row["payload"]["result_sha256"] = hashlib.sha256(result.read_bytes()).hexdigest()
    events.write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in rows), encoding="utf-8")
    template = example_template(w, source)
    assert template["resources"][0]["execution_scope"]["coverage"] == "legacy_default_entrypoint_only"
    data["execution_scope"] = {"entrypoint":"all", "argv":["--train"], "coverage":"all_modes"}
    result.write_text(json.dumps(data), encoding="utf-8")
    template = example_template(w, source)
    assert template["resources"][0]["needs_execution"]


def test_default_entrypoint_success_never_verifies_unknown_modes(resource_case):
    w, _, body = resource_case
    source = w.root/"runs/not-run.md"
    source.write_text(body, encoding="utf-8")
    template = example_template(w, source)
    assert template["resources"][0]["needs_execution"]
    assert template["resources"][0]["execution_scope"]["coverage"] == "not_executed"


@pytest.mark.parametrize("also_program_input", [False, True])
def test_document_bytes_only_invalidate_execution_when_declared_program_input(resource_case, also_program_input):
    from knowledge_product_workflow import reader_artifacts
    w, _, body = resource_case
    plan = reader_artifacts.require_current_content_plan(w, 1)
    document = "assets/template.json"
    plan.sections[0].reader_files = [document]
    if also_program_input:
        plan.sections[0].code_delivery.input_files = [document]
    (w.root/document).write_text('{"input":"before"}', encoding="utf-8")
    source = w.root/"runs/document-scope.md"
    source.write_text(body, encoding="utf-8")
    run_python_resource(w, source, "assets/examples/main.py", approved=True)
    assert not example_template(w, source)["resources"][0]["needs_execution"]
    (w.root/document).write_text('{"input":"after"}', encoding="utf-8")
    assert example_template(w, source)["resources"][0]["needs_execution"] is also_program_input


def test_existing_editor_template_displays_scope_without_changing_proof_binding(tmp_path):
    from knowledge_product_workflow.developmental import create_structure_review_template
    from tests.test_reader_delivery_integration_v142 import _resource_book
    w, _, _ = _resource_book(tmp_path)
    before = (w.root/"runs/editorial/chapter-01-structure-review.json").read_bytes()
    template = create_structure_review_template(w, 1, w.root/"chapters/chapter-01.md")
    assert template["resource_execution_scopes"][0]["execution_scope"]["coverage"] == "default_entrypoint_only"
    assert template["resource_execution_scopes"][0]["display_only"]
    assert (w.root/"runs/editorial/chapter-01-structure-review.json").read_bytes() == before
