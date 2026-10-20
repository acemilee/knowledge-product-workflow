from typer.testing import CliRunner

from knowledge_product_workflow import cli
from knowledge_product_workflow.verification_context import SnapshotChangedError
from knowledge_product_workflow.workspace import Workspace


def test_amend_control_completion_failure_does_not_print_edit_permission(tmp_path, monkeypatch):
    w = Workspace.create(tmp_path, "amend")
    monkeypatch.setattr("knowledge_product_workflow.runtime.require_workflow_mutation_allowed", lambda *_: None)
    calls = []
    monkeypatch.setattr(cli, "amend_chapter_draft", lambda *_: calls.append("opened") or w.root/"chapters/chapter-01.md")
    monkeypatch.setattr(cli, "supersede_orchestration_work_orders", lambda *_: calls.append("superseded"))
    def changed(*_):
        raise SnapshotChangedError("输入已变；原归档保留，不重发重开")
    monkeypatch.setattr(cli, "create_orchestration_work_order", changed)
    result = CliRunner().invoke(cli.app, ["amend-chapter", "1", "--workspace", str(w.root), "--reason", "修订实际内容"])
    assert result.exit_code == 1 and calls == ["opened", "superseded"]
    assert "输入已变" in result.output and "可开始编辑" not in result.output
    assert "Traceback" not in result.output
