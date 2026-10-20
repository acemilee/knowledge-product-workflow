import json
import subprocess
import sys
from pathlib import Path

import pytest

from knowledge_product_workflow.production_dependencies import file_digest
from knowledge_product_workflow.production_intake import recover_production_commit
from knowledge_product_workflow.production_tasks import (
    ProductionPolicy,
    ProductionRun,
    next_dispatches,
    recover_production_lock,
)
from knowledge_product_workflow.workspace import Workspace


def journal(workspace, *, bindings=None):
    folder=workspace.root/"runs/production/commits"
    folder.mkdir(parents=True,exist_ok=True)
    source=workspace.root/"runs/production/staged.md"
    source.write_text("待接受正文",encoding="utf-8")
    (folder/"commit-test.json").write_text(json.dumps({"state":"prepared","diagnostic_only":False,
        "chapter_number":2,"input_bindings":bindings or {},
        "outputs":{"chapters/chapter-02.md":{"staged":"runs/production/staged.md","sha256":file_digest(source)}}}),encoding="utf-8")


def test_zero_capacity_pauses_without_releasing_existing_threads():
    run=ProductionRun.diagnostic_run(21,ProductionPolicy(requested_parallelism=4))
    current=next_dispatches(run)
    run.resize(0)
    assert run.effective_parallelism==0 and next_dispatches(run)==[] and run.active_count==4
    run.complete_edit(current[0].task_id,"夹具",input_manifest_sha256=current[0].input_manifest_sha256)
    assert next_dispatches(run)==[]


def test_real_local_check_schedules_semantics_only_after_snapshot_preparation():
    run=ProductionRun.diagnostic_run(1,ProductionPolicy())
    run.synthetic_local_checks=False
    writer=next_dispatches(run)[0]
    run.complete_edit(writer.task_id,"夹具",input_manifest_sha256=writer.input_manifest_sha256)
    assert writer.state=="local_check"
    semantic=next(t for t in run.tasks if t.role=="semantic")
    assert semantic.state=="waiting_snapshot" and next_dispatches(run)==[]


def test_recovery_rejects_changed_dependency_before_any_output(tmp_path):
    workspace=Workspace.create(tmp_path,"drift")
    evidence=workspace.root/"research/source-ledger.jsonl"
    evidence.write_text("来源A",encoding="utf-8")
    journal(workspace,bindings={"research/source-ledger.jsonl":file_digest(evidence)})
    evidence.write_text("来源B",encoding="utf-8")
    with pytest.raises(ValueError,match="实际依赖"):
        recover_production_commit(workspace,"commit-test")
    assert not (workspace.root/"chapters/chapter-02.md").exists()


def test_event_tail_is_archived_and_recovery_is_idempotent(tmp_path):
    workspace=Workspace.create(tmp_path,"tail")
    journal(workspace)
    events=workspace.root/"runs/events.jsonl"
    original=events.read_bytes()+b'{"event_id":"partial"'
    events.write_bytes(original)
    recover_production_commit(workspace,"commit-test")
    archived=list((workspace.root/"runs/production/event-recovery").glob("*.original.bin"))
    assert len(archived)==1 and archived[0].read_bytes()==original
    assert all(isinstance(json.loads(line),dict) for line in events.read_text(encoding="utf-8").splitlines())
    before=events.read_bytes()
    recover_production_commit(workspace,"commit-test")
    assert events.read_bytes()==before


def test_index_rejects_commit_created_after_initial_guard(tmp_path,monkeypatch):
    import knowledge_product_workflow.developmental as module
    from knowledge_product_workflow.frontmatter import render_markdown_artifact
    from knowledge_product_workflow.models import ArtifactMetadata, ArtifactStatus
    workspace=Workspace.create(tmp_path,"reader")
    chapter=workspace.root/"chapters/chapter-02.md"
    chapter.write_text(render_markdown_artifact(ArtifactMetadata(book_id="reader",artifact_type="chapter",status=ArtifactStatus.IN_VALIDATION,version=1),"## 2.1 A\nA"),encoding="utf-8")
    original=module._reader_body
    def inject(path):
        journal(workspace)
        return original(path)
    monkeypatch.setattr(module,"_reader_body",inject)
    with pytest.raises(ValueError,match="半提交"):
        module.book_reading_index(workspace,1,"## 1.1 标题\n输入")


def test_hard_crash_owner_is_queryable_and_only_dead_lock_is_released(tmp_path):
    import knowledge_product_workflow
    workspace=Workspace.create(tmp_path,"crash")
    site=Path(knowledge_product_workflow.__file__).resolve().parents[1]
    dependencies=next(p for p in sys.path if p.replace("\\","/").endswith(".venv/Lib/site-packages"))
    code=f"import sys,os;sys.path[:0]=[{str(site)!r},{dependencies!r}];from pathlib import Path;from knowledge_product_workflow.workspace import Workspace;from knowledge_product_workflow.production_tasks import production_lock\nwith production_lock(Workspace(root=Path({str(workspace.root)!r}))): os._exit(77)"
    result=subprocess.run([sys.executable,"-B","-S","-c",code],capture_output=True,text=True,timeout=30,check=False)
    assert result.returncode==77,result.stderr
    owner=json.loads((workspace.root/"runs/production/control.lock").read_text(encoding="utf-8"))
    assert owner["pid"] and owner["process_identity"]
    assert recover_production_lock(workspace)["state"]=="released_dead_owner"
    journal(workspace)
    assert recover_production_commit(workspace,"commit-test")["state"]=="completed"


def test_shared_source_request_consumes_actual_domain_row_and_never_appends_again(tmp_path):
    from knowledge_product_workflow.production_intake import (
        complete_shared_request,
        shared_request_handoffs,
    )
    from knowledge_product_workflow.production_tasks import save_run
    from tests.test_research_orchestration_o10 import _prepared_workspace, _research
    workspace=_prepared_workspace(tmp_path)
    run=ProductionRun.diagnostic_run(1,ProductionPolicy())
    payload={"source_id":"group-one","url":"https://example.com/evidence","excerpt":"真实 CLI 测试来源摘录。","license_note":"测试夹具","claim_ids":["claim-one"]}
    request=run.queue_shared_request(run.tasks[0].task_id,"source",payload)
    save_run(workspace,run)
    assert shared_request_handoffs(run)[0]["domain_command"]=="research"
    with pytest.raises(ValueError):
        complete_shared_request(workspace,run.run_id,request["request_id"])
    assert _research(workspace,"claim-one",source="group-one").returncode==0
    before=(workspace.root/"research/source-ledger.jsonl").read_bytes()
    result=complete_shared_request(workspace,run.run_id,request["request_id"])
    assert result["state"]=="completed"
    assert complete_shared_request(workspace,run.run_id,request["request_id"])==result
    assert (workspace.root/"research/source-ledger.jsonl").read_bytes()==before


def test_evaluate_cli_persists_semantic_ready_even_when_developmental_review_missing(tmp_path):
    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app
    from knowledge_product_workflow.production_context import freeze_candidate_context
    from knowledge_product_workflow.production_tasks import resume_production_run, save_run
    from tests.test_structure_review_v133 import _fixture
    workspace,source,body=_fixture(tmp_path)
    body=body.replace("\n\n## ","\n\n::: {.kp-chapter-lead}\n本章从输入、使用条件与可检查产出展开。\n:::\n\n## ",1)
    source.write_text(body,encoding="utf-8")
    paths=[p.relative_to(workspace.root).as_posix() for p in workspace.root.rglob("*") if p.is_file()]
    run=ProductionRun.diagnostic_run(1,ProductionPolicy())
    run.synthetic_local_checks=False
    writer=next_dispatches(run)[0]
    run.complete_edit(writer.task_id,body,input_manifest_sha256=writer.input_manifest_sha256)
    save_run(workspace,run)
    context=freeze_candidate_context(workspace,chapter_number=1,candidate_version=1,source=source,
        input_paths=paths,run_id=run.run_id,task_id=writer.task_id)
    context_path=Path(context.snapshot_root).parent/"context.json"
    result=CliRunner().invoke(app,["production-evaluate","--workspace",str(workspace.root),"--context",str(context_path)])
    assert result.exit_code==0,result.output
    assert json.loads(result.output)["required_missing"]
    restored=resume_production_run(workspace,run.run_id)
    assert next(t for t in restored.tasks if t.role=="semantic").state=="review_ready"
    assert next_dispatches(restored)[0].role=="semantic"
