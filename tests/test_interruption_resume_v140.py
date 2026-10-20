import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import knowledge_product_workflow
from knowledge_product_workflow.runtime import (
    pause_run_intent,
    recovery_view,
    start_run_intent,
)
from knowledge_product_workflow.workspace import Workspace
from knowledge_product_workflow.workspace_transactions import recover_workspace_transaction


def workspace(tmp_path):
    w = Workspace.create(tmp_path, "resume")
    path = w.root / "runs/orchestration/state.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["binding_policy_revision"] = 2
    path.write_text(json.dumps(raw), encoding="utf-8")
    return w


def test_cooperative_pause_and_resume_preserve_the_same_intent(tmp_path):
    w = workspace(tmp_path)
    original = start_run_intent(w)
    stopped = pause_run_intent(w)
    assert stopped.state.value == "paused"
    view = recovery_view(w)
    assert view["can_resume"] and view["host_pause_verified"] is False
    before = {p.relative_to(w.root): p.read_bytes() for p in w.root.rglob("*") if p.is_file()}
    assert recovery_view(w) == view
    assert before == {p.relative_to(w.root): p.read_bytes() for p in w.root.rglob("*") if p.is_file()}
    assert start_run_intent(w).intent_id == original.intent_id


def test_corrupt_intent_is_not_treated_as_a_new_run(tmp_path):
    w = workspace(tmp_path)
    start_run_intent(w)
    path = w.root / "runs/orchestration/run-intent.json"
    path.write_bytes(b'{"broken":')
    assert recovery_view(w)["can_resume"] is False
    with pytest.raises(ValueError, match="恢复|损坏"):
        start_run_intent(w)
    assert path.read_bytes() == b'{"broken":'


def test_unknown_host_task_is_preserved_and_never_redispatched_by_resume(tmp_path):
    w = workspace(tmp_path)
    start_run_intent(w)
    p = w.root / "runs/production/owned/run.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"tasks": [{"task_id": "owned-unknown", "state": "unknown",
        "host_task_id": "native-child", "release_confirmed": False}]}), encoding="utf-8")
    assert pause_run_intent(w).state.value == "stop_requested"
    assert recovery_view(w)["unknown_tasks"] == ["owned-unknown"]
    with pytest.raises(ValueError):
        start_run_intent(w)
    assert json.loads(p.read_text(encoding="utf-8"))["tasks"][0]["release_confirmed"] is False


def test_process_exit_without_finally_recovers_its_actual_file_set(tmp_path):
    w = workspace(tmp_path)
    original = start_run_intent(w)
    site = str(Path(knowledge_product_workflow.__file__).resolve().parent.parent)
    script = f'''import sys, os
sys.path.insert(0, {site!r})
from pathlib import Path
from knowledge_product_workflow.workspace import Workspace
import knowledge_product_workflow.workspace_transactions as tx
w=Workspace(Path({str(w.root)!r}))
t=tx.prepare_workspace_transaction(w, {{"chapters/chapter-02.md": b"saved-body"}}, kind="chapter_submission", scope_chapters=[2], operation_id="tx-kill")
def stop(phase):
    if phase == "after_replace:chapters/chapter-02.md": os._exit(23)
tx._fault=stop
tx.commit_workspace_transaction(w,t)
'''
    result = subprocess.run([sys.executable, "-B", "-c", script], timeout=30, check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([site, str(Path(__import__('pydantic').__file__).resolve().parent.parent)])},
        capture_output=True)
    assert result.returncode == 23, result.stderr.decode(errors="replace")
    assert recovery_view(w)["pending_operations"] == ["tx-kill"]
    from knowledge_product_workflow.production_tasks import recover_production_lock
    recover_production_lock(w)
    recover_workspace_transaction(w, "tx-kill")
    assert (w.root / "chapters/chapter-02.md").read_bytes() == b"saved-body"
    assert start_run_intent(w).intent_id == original.intent_id


def test_paused_auxiliary_cli_and_library_writes_refuse_without_any_new_files(tmp_path):
    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app
    from knowledge_product_workflow.humanize import record_humanize_check
    from tests.test_deferred_repairs_v140 import serial_book, submit
    w = serial_book(tmp_path)
    submit(w, 2)
    source = w.root / "runs/body-2.md"
    source.write_text(source.read_text(encoding="utf-8") + "\n暂停前保存的候选改动。\n", encoding="utf-8")
    original = start_run_intent(w)
    assert pause_run_intent(w).state.value == "paused"
    before = {p.relative_to(w.root).as_posix(): p.read_bytes() for p in w.root.rglob("*") if p.is_file()}
    runner = CliRunner()
    for arguments in (["humanize-check", "2", "--from", str(source)],
                      ["content-plan-template", "2"], ["orchestration-plan"]):
        result = runner.invoke(app, [*arguments, "--workspace", str(w.root)])
        assert result.exit_code == 1 and "暂停" in result.output, result.output
    with pytest.raises(ValueError, match="暂停"):
        record_humanize_check(w, 2, source)
    for arguments in (["status", "--json"], ["recovery-view", "--json"],
                      ["check-chapter", "2", "--from", str(source), "--json"]):
        result = runner.invoke(app, [*arguments, "--workspace", str(w.root)])
        assert result.exit_code == 0, result.output
    assert before == {p.relative_to(w.root).as_posix(): p.read_bytes() for p in w.root.rglob("*") if p.is_file()}
    result = runner.invoke(app, ["start-run-intent", "--workspace", str(w.root)])
    assert result.exit_code == 0, result.output
    assert start_run_intent(w).intent_id == original.intent_id
