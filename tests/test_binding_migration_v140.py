import json

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.change_impact import adopt_binding_policy, binding_policy_revision
from knowledge_product_workflow.cli import app
from knowledge_product_workflow.workspace import Workspace
from tests.test_sample_approval_basis_v140 import sample


def snapshot(workspace):
    return {p.relative_to(workspace.root).as_posix(): p.read_bytes()
            for p in workspace.root.rglob("*") if p.is_file()}


def legacy(tmp_path):
    workspace = sample(tmp_path)
    state = workspace.root / "runs/orchestration/state.json"
    raw = json.loads(state.read_text(encoding="utf-8"))
    raw.pop("binding_policy_revision")
    state.write_text(json.dumps(raw), encoding="utf-8")
    return workspace


def test_migration_preview_is_readonly_confirm_archives_and_is_idempotent(tmp_path):
    w = legacy(tmp_path)
    before = snapshot(w)
    assert adopt_binding_policy(w)["requires_confirmation"]
    assert snapshot(w) == before
    result = adopt_binding_policy(w, confirm=True)
    assert result["adopted"] and binding_policy_revision(w) == 2
    assert not (w.root / "chapters/chapter-01-sample.approved").exists()
    assert (w.root / "chapters/chapter-01.md").read_bytes() == before["chapters/chapter-01.md"]
    assert list(w.root.glob("runs/orchestration/binding-migration/*/chapters/chapter-01-sample.approved"))
    after = snapshot(w)
    assert adopt_binding_policy(w, confirm=True)["already_adopted"]
    assert snapshot(w) == after


def test_revoked_sample_never_restored_and_unknown_task_refuses_migration(tmp_path):
    w = legacy(tmp_path)
    (w.root / "chapters/chapter-01-sample.approved").unlink()
    p = w.root / "runs/production/pending/run.json"
    p.parent.mkdir(parents=True)
    p.write_text('{"tasks":[{"task_id":"unknown","host_task_id":"h","release_confirmed":false}]}')
    before = snapshot(w)
    with pytest.raises(ValueError, match="未知"):
        adopt_binding_policy(w, confirm=True)
    assert snapshot(w) == before
    p.unlink()
    adopt_binding_policy(w, confirm=True)
    assert not (w.root / "chapters/chapter-01-sample.approved").exists()


def test_cli_new_books_policy2_sdk_default1_and_readonly_commands(tmp_path, monkeypatch):
    from knowledge_product_workflow import cli
    monkeypatch.setattr(cli, "_doctor_checks", lambda: {"quarto": {"available": False}, "xelatex": {"available": False}})
    runner = CliRunner()
    assert runner.invoke(app, ["init", "new", "--root", str(tmp_path)]).exit_code == 0
    w = Workspace.open(tmp_path / "new")
    assert binding_policy_revision(w) == 2
    assert binding_policy_revision(Workspace.create(tmp_path, "sdk", current_policy=True)) == 1
    before = snapshot(w)
    result = runner.invoke(app, ["recovery-view", "--workspace", str(w.root), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["host_pause_verified"] is False
    assert snapshot(w) == before
    result = runner.invoke(app, ["commands", "--json"])
    names = {row["command"] for row in json.loads(result.output)["commands"]}
    assert {"preview-change-impact", "refresh-chapter-proofs", "recovery-view", "adopt-binding-policy"} <= names
