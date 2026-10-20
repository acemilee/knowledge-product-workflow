"""失败留在低成本阶段，不能建立回执或调用授权/渲染。"""
from typer.testing import CliRunner

from knowledge_product_workflow import cli, publishing
from knowledge_product_workflow.workspace import Workspace


def test_preflight_failure_precedes_authorization_receipt_and_render(tmp_path, monkeypatch):
    w = Workspace.create(tmp_path, "cheap")
    def fail(*args, **kwargs):
        raise AssertionError("昂贵或有副作用阶段不得调用")
    monkeypatch.setattr(publishing, "resource_preflight_for_chapters", lambda *_: {"ready": False,
        "findings": [{"path": "assets/README.md"}, {"path": "assets/THIRD_PARTY_NOTICES.md"}]})
    monkeypatch.setattr(cli, "_prepare_delivery_action", fail)
    monkeypatch.setattr(cli, "authorize_command", fail)
    monkeypatch.setattr(cli, "render_pdf", fail)
    for command in ("assemble", "render-pdf", "reader-resource-preflight"):
        result = CliRunner().invoke(cli.app, [command, "--workspace", str(w.root)])
        assert result.exit_code == 1, result.output
        assert "README" in result.output and "NOTICES" in result.output
    assert not (w.root/"runs/orchestration/operations").exists()
    assert not (w.root/"delivery/book.md").exists()


def test_resource_preflight_is_available_when_paused(tmp_path, monkeypatch):
    w = Workspace.create(tmp_path, "paused")
    monkeypatch.setattr(publishing, "resource_preflight_for_chapters", lambda *_: {"ready": True, "findings": []})
    monkeypatch.setattr("knowledge_product_workflow.runtime.require_workflow_mutation_allowed", lambda *_: (_ for _ in ()).throw(AssertionError("非写入")))
    result = CliRunner().invoke(cli.app, ["reader-resource-preflight", "--workspace", str(w.root), "--json"])
    assert result.exit_code == 0, result.output


def test_explicit_existing_unknown_operation_is_handled_before_resource_preflight(tmp_path, monkeypatch):
    import os

    from knowledge_product_workflow.operation_receipts import OperationReceipt, receipt_path
    w = Workspace.create(tmp_path, "original-operation")
    receipt = OperationReceipt(operation_id="op-original", workspace=str(w.root.resolve()),
        book_id=w.root.name, action="assemble", chapter_number=0, owner_pid=os.getpid(), owner_nonce="synthetic", phase="unknown")
    path = receipt_path(w, receipt.operation_id)
    path.parent.mkdir(parents=True)
    path.write_text(receipt.model_dump_json(), encoding="utf-8")
    before = path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("必须先处理原未知回执")
    monkeypatch.setattr(publishing, "resource_preflight_for_chapters", forbidden)
    monkeypatch.setattr(cli, "_prepare_delivery_action", forbidden)
    result = CliRunner().invoke(cli.app, ["assemble", "--workspace", str(w.root), "--operation-id", "op-original"])
    assert result.exit_code == 1 and "op-original" in result.output and "unknown" in result.output
    assert path.read_bytes() == before and len(list(path.parent.glob("*.json"))) == 1
