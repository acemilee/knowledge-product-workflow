"""公开发现、模板恢复及无重复外部导出的操作回归。"""

import json

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.content_plan import create_content_plan_template
from tests.test_chapter_content_plan import _approved_workspace


def test_filled_content_plan_template_is_reused_only_for_current_contract(tmp_path):
    workspace = _approved_workspace(tmp_path)
    template, context = create_content_plan_template(workspace, 1)
    template.write_text("# 已填写的工作稿，必须保留\nsections: []\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (template, context)}
    result = CliRunner().invoke(app, ["content-plan-template", "1", "--workspace", str(workspace.root)])
    assert result.exit_code == 0, result.output
    assert all(path.read_bytes() == data for path, data in before.items())
    contract = workspace.root / "chapters/chapter-01-contract.md"
    contract.write_text(contract.read_text(encoding="utf-8") + "\n合同变化。\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["content-plan-template", "1", "--workspace", str(workspace.root)])
    assert result.exit_code == 1
    assert "已过期" in result.output and str(template) in result.output
    assert "Traceback" not in result.output
    assert all(path.read_bytes() == data for path, data in before.items())


def test_command_discovery_includes_required_argv_choices_and_role():
    runner = CliRunner()
    result = runner.invoke(app, ["commands", "--all", "--json", "--command", "example-check-template"])
    assert result.exit_code == 0
    entry = json.loads(result.output)["commands"][0]
    assert entry["argv"] == ["kp", "example-check-template", "--workspace", "<workspace>", "--from", "<source_path>"]
    assert entry["required_role"] == "kp-chapter-writer"
    result = runner.invoke(app, ["commands", "--all", "--json", "--command", "record-user-decision"])
    entry = json.loads(result.output)["commands"][0]
    decision = next(p for p in entry["parameters"] if p["name"] == "decision")
    assert decision["choices"] == ["approve", "revise", "clarify"]
    result = runner.invoke(app, ["commands", "--all", "--json", "--command", "submit-content-plan"])
    entry = json.loads(result.output)["commands"][0]
    assert entry["input_templates"][0]["command"] == "content-plan-template"


@pytest.mark.requires_poppler
def test_pdf_observation_discovery_is_readonly_and_never_prefills_passed(tmp_path):
    from knowledge_product_workflow.visual_audit import audit_pdf_visual
    from tests.test_visual_audit import _workspace

    workspace = _workspace(tmp_path)
    audit_pdf_visual(workspace)
    before = {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    runner = CliRunner()
    result = runner.invoke(app, ["pdf-observation-schema"])
    assert result.exit_code == 0, result.output
    schema = json.loads(result.output)
    assert schema["type"] == "array" and "page_number" in schema["items"]["properties"]
    result = runner.invoke(app, ["pdf-observation-template", "--workspace", str(workspace.root)])
    assert result.exit_code == 0, result.output
    template = json.loads(result.output)
    assert template["observable_pages"] == [1, 2, 3]
    assert [r["page_number"] for r in template["observations"]] == [1, 2, 3]
    assert all(r["assessment"] == "uncertain" and not r["note"] for r in template["observations"])
    assert before == {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    with (workspace.root / "delivery/book.pdf").open("ab") as stream:
        stream.write(b"\n% version changed\n")
    assert runner.invoke(app, ["pdf-observation-template", "--workspace", str(workspace.root)]).exit_code == 1


def _vector_input(workspace, *, kind="comparison", label="资料边界", relation=None):
    from knowledge_product_workflow.diagrams import insert_diagram_reference
    source = workspace.root / "runs/vector-source.md"
    source.write_text("# 1. 架构基础\n\n比较两个方案。\n", encoding="utf-8")
    semantic = workspace.root / "runs/vector-input.json"
    semantic.write_text(json.dumps({"diagram_id": "operator-vector", "diagram_type": kind,
        "direction": "right" if kind == "stage_flow" else "down", "caption": "方案关系",
        "reader_conclusion": "按条件核对资料与索引。",
        "nodes": [{"node_id": "docs", "label": label}, {"node_id": "index", "label": "索引"}],
        "edges": [{"source": "docs", "target": "index", "label": relation}]}, ensure_ascii=False), encoding="utf-8")
    insert_diagram_reference(workspace, 1, source, "operator-vector", semantic)
    return source, semantic


@pytest.mark.parametrize("kind,label,relation", [("comparison", "甲\n乙\n丙\n丁", None), ("stage_flow", "资料", "交接")])
def test_svg_semantic_errors_are_rejected_before_external_tool_probe(tmp_path, monkeypatch, kind, label, relation):
    from knowledge_product_workflow.diagrams import DiagramSubmissionError, submit_diagram
    from tests.test_diagrams import _approved_workspace as diagram_workspace
    workspace = diagram_workspace(tmp_path, v6=True)
    source, semantic = _vector_input(workspace, kind=kind, label=label, relation=relation)
    probes = []
    monkeypatch.setattr("knowledge_product_workflow.diagrams.resolve_diagram_browser", lambda: probes.append("browser"))
    with pytest.raises(DiagramSubmissionError) as error:
        submit_diagram(workspace, 1, source, semantic)
    assert error.value.code == "DIAGRAM_INPUT_INVALID"
    assert probes == []
    assert not (workspace.root / "assets/figures/operator-vector.pdf").exists()


def test_valid_vector_assets_are_rebound_without_export_but_changed_semantics_redraw(tmp_path, monkeypatch):
    from knowledge_product_workflow.d2 import BrowserResolution
    from knowledge_product_workflow.diagrams import insert_diagram_reference, submit_diagram
    from tests.test_diagrams import _approved_workspace as diagram_workspace
    workspace = diagram_workspace(tmp_path, v6=True)
    source, semantic = _vector_input(workspace)
    browser = workspace.root / "runs/browser.exe"
    browser.write_bytes(b"known tool identity fixture")
    browser_version = ["fixture-1"]
    monkeypatch.setattr("knowledge_product_workflow.diagrams.resolve_diagram_browser",
        lambda: BrowserResolution(True, browser, "test", browser_version[0], ()))
    exports = []
    def export(_browser, svg, pdf, png, **kwargs):
        exports.append(svg.read_bytes())
        pdf.write_bytes(b"%PDF-fixture:" + svg.read_bytes())
        png.write_bytes(b"PNG-fixture:" + svg.read_bytes())
    monkeypatch.setattr("knowledge_product_workflow.diagrams.render_svg_with_browser", export)
    asset, first = submit_diagram(workspace, 1, source, semantic)
    original = asset.read_bytes()
    source.write_text(source.read_text(encoding="utf-8") + "\n仅正文新增核对步骤。\n", encoding="utf-8")
    semantic.write_text(json.dumps(json.loads(semantic.read_text(encoding="utf-8")), ensure_ascii=False, indent=4), encoding="utf-8")
    asset, rebound = submit_diagram(workspace, 1, source, semantic)
    assert len(exports) == 1 and asset.read_bytes() == original
    assert rebound.chapter_body_sha256 != first.chapter_body_sha256
    assert rebound.pdf_sha256 == first.pdf_sha256
    payload = json.loads(semantic.read_text(encoding="utf-8"))
    payload["nodes"][0]["label"] = "新的资料"
    semantic.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    insert_diagram_reference(workspace, 1, source, "operator-vector", semantic)
    submit_diagram(workspace, 1, source, semantic)
    assert len(exports) == 2
    # Corrupted/missing assets are not a successful cache hit.
    asset.write_bytes(b"damaged")
    submit_diagram(workspace, 1, source, semantic)
    assert len(exports) == 3
    browser.write_bytes(b"same reported version, changed executable bytes")
    submit_diagram(workspace, 1, source, semantic)
    assert len(exports) == 4
    browser_version[0] = None
    submit_diagram(workspace, 1, source, semantic)
    submit_diagram(workspace, 1, source, semantic)
    assert len(exports) == 6
