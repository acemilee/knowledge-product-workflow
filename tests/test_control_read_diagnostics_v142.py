import json

import pytest

from knowledge_product_workflow.production_tasks import ControlLockBusy, production_lock
from knowledge_product_workflow.runtime import inspect_workflow, route_workflow
from knowledge_product_workflow.workspace import Workspace


def test_other_reader_lock_is_typed_and_never_removed(tmp_path, monkeypatch):
    from knowledge_product_workflow import production_tasks
    w = Workspace.create(tmp_path, "owner")
    folder = w.root/"runs/production"
    folder.mkdir()
    lock = folder/"control.lock"
    data = json.dumps({"pid": 99, "workspace": str(w.root.resolve()), "process_identity": "same", "created_at": "fixed"}).encode()
    lock.write_bytes(data)
    monkeypatch.setattr(production_tasks, "_process_identity", lambda pid: "same")
    with pytest.raises(ControlLockBusy) as error, production_lock(w):
        pytest.fail("不能取得第二把锁")
    assert error.value.owner["state"] == "active"
    assert error.value.owner["action"] == "unknown"
    assert lock.read_bytes() == data
    assert not list(folder.glob(".lock-owner-*"))
    status = inspect_workflow(w)
    assert status.stage == "production_busy"
    with pytest.raises(ValueError, match="CONTROL_LOCK_BUSY"):
        route_workflow(w)
    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app
    result = CliRunner().invoke(app, ["route", "--workspace", str(w.root), "--write-work-order", "--json"])
    assert result.exit_code == 1 and "CONTROL_LOCK_BUSY" in result.output and "Traceback" not in result.output
    assert not list((w.root/"runs/work-orders").glob("*.json"))
    assert lock.read_bytes() == data


def test_corrupt_or_unknown_owner_is_not_declared_dead(tmp_path, monkeypatch):
    from knowledge_product_workflow import production_tasks
    w = Workspace.create(tmp_path, "unknown")
    folder = w.root/"runs/production"
    folder.mkdir()
    lock = folder/"control.lock"
    lock.write_text("broken", encoding="utf-8")
    monkeypatch.setattr(production_tasks, "_process_identity", lambda pid: None)
    with pytest.raises(ControlLockBusy) as error, production_lock(w):
        pass
    assert error.value.owner["state"] == "invalid_or_unknown"
    assert lock.read_text(encoding="utf-8") == "broken"
