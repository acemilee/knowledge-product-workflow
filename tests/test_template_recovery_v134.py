"""新运行包的排版资源变化须回到受控汇编，不改读者配置。"""

import hashlib

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.publishing import create_quarto_source, pdf_template_input_drift
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_template_probe_is_read_only_and_detects_current_runtime_changes(tmp_path, monkeypatch):
    from knowledge_product_workflow import publishing

    workspace = Workspace.create(tmp_path, "template-recovery")
    create_product_brief(workspace, "模板恢复核验", DetailConstraints(delivery_format="pdf"))
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text("# 1. 模板恢复\n\n正文未改。\n", encoding="utf-8")
    create_quarto_source(workspace, markdown)
    assert pdf_template_input_drift(workspace) == []
    paths = [path for path in workspace.root.rglob("*") if path.is_file()]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    original = publishing._template_text

    def updated(name, *arguments):
        text = original(name, *arguments)
        return text + "\n-- new runtime template\n" if name.endswith(".lua") else text

    monkeypatch.setattr(publishing, "_template_text", updated)
    assert pdf_template_input_drift(workspace) == ["knowledge-book-components.lua"]
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
