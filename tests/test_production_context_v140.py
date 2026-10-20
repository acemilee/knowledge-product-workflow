from pathlib import Path

import pytest

from knowledge_product_workflow.production_context import (
    evaluate_candidate,
    freeze_candidate_context,
)
from knowledge_product_workflow.workspace import Workspace


def test_private_evaluation_accepts_its_owned_directory_inside_canonical_scope(tmp_path):
    from knowledge_product_workflow.verification_context import verification_scope
    from tests.test_structure_review_v133 import _fixture
    w, source, body = _fixture(tmp_path)
    source.write_text(body, encoding='utf-8')
    inputs = [p.relative_to(w.root).as_posix() for p in w.root.rglob('*') if p.is_file()]
    context = freeze_candidate_context(w, chapter_number=1, candidate_version=1,
        source=source, input_paths=inputs, run_id='run-owned', task_id='writer-owned')
    with verification_scope(w):
        proof = evaluate_candidate(context)
    assert proof.task_id == 'writer-owned'
    assert Path(context.private_result_root, 'proofs.json').is_file()


def test_frozen_context_does_not_fall_back_to_changed_canonical_file(tmp_path):
    workspace = Workspace.create(tmp_path, "context")
    source = workspace.root / "runs/source.md"
    source.write_text("# 1. 原稿\n\n## 1.1 输入\n\n固定输入。", encoding="utf-8")
    context = freeze_candidate_context(
        workspace, chapter_number=1, candidate_version=1, source=source,
        input_paths=["runs/source.md"], run_id="run-one", task_id="task-one",
    )
    source.write_text("canonical 已修改", encoding="utf-8")
    assert "固定输入" in context.read_input("runs/source.md")
    with pytest.raises(ValueError, match="清单"):
        context.read_input("research/missing.jsonl")
    with pytest.raises(ValueError):
        context.read_input("../outside.md")
    assert not (workspace.root / "runs/editorial/book-reading-index.json").exists()


def test_snapshot_tampering_and_cross_task_reuse_are_rejected(tmp_path):
    workspace = Workspace.create(tmp_path, "tamper")
    source = workspace.root / "runs/source.md"
    source.write_text("正文", encoding="utf-8")
    context = freeze_candidate_context(workspace, chapter_number=1, candidate_version=1,
        source=source, input_paths=["runs/source.md"], run_id="run-one", task_id="task-one")
    Path(context.snapshot_root, "runs/source.md").write_text("篡改", encoding="utf-8")
    with pytest.raises(ValueError, match="篡改"):
        context.verify()


def test_actual_core_checks_write_only_private_reports_and_refuse_old_canonical_proof(tmp_path):
    from knowledge_product_workflow.developmental import record_developmental_check
    from knowledge_product_workflow.humanize import record_humanize_check
    from tests.test_structure_review_v133 import _fixture, simulated_review

    workspace, source, body = _fixture(tmp_path)
    body = body.replace("\n\n## ", "\n\n::: {.kp-chapter-lead}\n本章从具体方案与成立条件出发，帮助读者完成可检查的取舍。\n:::\n\n## ", 1)
    source.write_text(body, encoding="utf-8")
    simulated_review(workspace, source)
    record_developmental_check(workspace, 1, source)
    record_humanize_check(workspace, 1, source)
    source.write_text(body + "\n新的候选需要重新核验其使用条件。\n", encoding="utf-8")
    before = {p.relative_to(workspace.root).as_posix(): p.read_bytes()
              for p in workspace.root.rglob("*") if p.is_file()}
    context = freeze_candidate_context(workspace, chapter_number=1, candidate_version=1,
        source=source, input_paths=list(before), run_id="core-one", task_id="writer-one")
    proof = evaluate_candidate(context)
    assert proof.required_missing
    assert all(Path(p).is_relative_to(Path(context.private_result_root)) for p in proof.reports.values())
    assert before == {key: (workspace.root/key).read_bytes() for key in before}
    assert Path(context.private_result_root, "structure-template.json").is_file()


def test_incomplete_context_cannot_silently_use_default_policy(tmp_path):
    workspace = Workspace.create(tmp_path, "no-policy", current_policy=True)
    source = workspace.root / "runs/source.md"
    source.write_text("正文", encoding="utf-8")
    context = freeze_candidate_context(workspace, chapter_number=1, candidate_version=1,
        source=source, input_paths=[], run_id="run-one", task_id="task-one")
    with pytest.raises(ValueError, match="必需批准输入"):
        evaluate_candidate(context)


def test_relative_manifest_and_cross_task_context_rejected_before_copy(tmp_path):
    workspace = Workspace.create(tmp_path, "paths")
    source = workspace.root / "runs/source.md"
    source.write_text("原稿", encoding="utf-8")
    with pytest.raises(ValueError, match="相对路径"):
        freeze_candidate_context(workspace, chapter_number=1, candidate_version=1,
            source=source, input_paths=[str(source)], run_id="run-one", task_id="task-one")
    assert source.read_text(encoding="utf-8") == "原稿"
    context = freeze_candidate_context(workspace, chapter_number=1, candidate_version=1,
        source=source, input_paths=[], run_id="run-one", task_id="task-one")
    with pytest.raises(ValueError, match="跨任务"):
        context.model_copy(update={"task_id": "other-task"}).verify()


def test_repeated_evaluation_reuses_current_proofs_and_rejects_report_tampering(tmp_path):
    from tests.test_structure_review_v133 import _fixture
    workspace, source, body = _fixture(tmp_path)
    body = body.replace("\n\n## ", "\n\n::: {.kp-chapter-lead}\n本章说明输入、使用条件及可检查的产出。\n:::\n\n## ", 1)
    source.write_text(body, encoding="utf-8")
    inputs = [p.relative_to(workspace.root).as_posix() for p in workspace.root.rglob("*") if p.is_file()]
    context = freeze_candidate_context(workspace, chapter_number=1, candidate_version=1,
        source=source, input_paths=inputs, run_id="run-one", task_id="task-one")
    first = evaluate_candidate(context)
    assert evaluate_candidate(context) == first
    report = Path(first.reports["humanize"])
    report.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="证明报告"):
        evaluate_candidate(context)


def test_cached_proof_refuses_private_content_plan_drift(tmp_path):
    from tests.test_structure_review_v133 import _fixture
    workspace,source,body=_fixture(tmp_path)
    body=body.replace("\n\n## ","\n\n::: {.kp-chapter-lead}\n本章从具体输入、使用条件与可检查结果展开。\n:::\n\n## ",1)
    source.write_text(body,encoding="utf-8")
    inputs=[p.relative_to(workspace.root).as_posix() for p in workspace.root.rglob("*") if p.is_file()]
    context=freeze_candidate_context(workspace,chapter_number=1,candidate_version=1,source=source,
        input_paths=inputs,run_id="cache-plan",task_id="task-plan")
    evaluate_candidate(context)
    Path(context.private_result_root,"proof-workspace/chapters/chapter-01-content-plan.yaml").write_text("corrupted_plan: true",encoding="utf-8")
    with pytest.raises(ValueError,match="私有校验输入"):
        evaluate_candidate(context)


def test_native_windows_snapshot_keys_are_posix_and_required_blueprint_is_found(tmp_path):
    from tests.test_structure_review_v133 import _fixture
    workspace,source,body=_fixture(tmp_path)
    body=body.replace("\n\n## ","\n\n::: {.kp-chapter-lead}\n本章从实际输入、使用条件和可检查结果展开。\n:::\n\n## ",1)
    source.write_text(body,encoding="utf-8")
    inputs=[str(p.relative_to(workspace.root)) for p in workspace.root.rglob("*") if p.is_file()]
    context=freeze_candidate_context(workspace,chapter_number=1,candidate_version=1,source=source,
        input_paths=inputs,run_id="native-windows",task_id="writer-windows")
    assert "planning/curriculum-blueprint.md" in context.input_bindings
    assert all("\\" not in p for p in context.input_bindings)
    proof=evaluate_candidate(context)
    assert proof.task_id=="writer-windows"


def test_existing_capability_preflight_is_frozen_without_granting_native_permission(tmp_path):
    from knowledge_product_workflow.production_dependencies import build_task_context
    from knowledge_product_workflow.runtime import preflight_independent_review
    from tests.test_chapter_content_plan import _approved_workspace
    workspace=_approved_workspace(tmp_path)
    snapshot=preflight_independent_review(workspace,platform="diagnostic-fixture",supports_real_subtasks=False,
        production_verified=False,max_parallelism=0)
    assert not snapshot.capabilities.production_verified
    context=build_task_context(workspace,1,"writer")
    key="runs/orchestration/capability-snapshot.json"
    assert key in context["snapshot_paths"] and key in context["live_bindings"]
    assert not snapshot.capabilities.supports_real_subtasks
