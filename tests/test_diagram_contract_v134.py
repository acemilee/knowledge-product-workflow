"""图族/方向属于批准语义；首次位置解析不要求抄hash或重提计划。"""
import json
import re
import xml.etree.ElementTree as ET

import pytest

from knowledge_product_workflow.content_plan import submit_content_plan
from knowledge_product_workflow.diagrams import insert_diagram_reference
from knowledge_product_workflow.illustration_tasks import IllustrationTask
from tests.test_illustration_policy import approved_workspace, plan, task


def test_linear_handoff_contract_rejects_an_arbitrary_flow_layout():
    with pytest.raises(ValueError, match="stage_flow"):
        IllustrationTask(**task(diagram_type="flow", relation_kind="handoff"))
    chosen = IllustrationTask(**task(diagram_type="stage_flow", relation_kind="handoff", direction="right"))
    assert chosen.direction == "right"


def test_first_semantic_placement_is_idempotent_and_stale_requires_rebind(tmp_path):
    selected = task()
    workspace = approved_workspace(tmp_path, [selected])
    supplied = workspace.root / "runs/content-plan.json"
    supplied.write_text(json.dumps(plan(2, [selected]), ensure_ascii=False), encoding="utf-8")
    submit_content_plan(workspace, 1, supplied)
    source = workspace.root / "runs/source.md"
    original = "# 1. 资料边界\n\n本章区分依赖。\n\n## 1.1 输入关系\n\n下图说明资料与索引如何交接。\n\n读取之后，核对资料更新与索引重建的顺序。\n"
    source.write_text(original, encoding="utf-8")
    semantic = workspace.root / "runs/diagram.json"
    semantic.write_text(json.dumps({"diagram_id": "dependency-map", "task_id": "dependency-map",
        "diagram_type": "layered_architecture", "direction": "down", "backend": "vector",
        "heading_anchor": "1.1 输入关系", "caption": "资料到索引", "reader_conclusion": "资料更新需要重建索引。",
        "nodes": [{"node_id": "docs", "label": "资料"}, {"node_id": "index", "label": "索引"}],
        "edges": [{"source": "docs", "target": "index"}]}, ensure_ascii=False), encoding="utf-8")
    insert_diagram_reference(workspace, 1, source, "dependency-map", semantic)
    once = source.read_text(encoding="utf-8")
    assert once.index("下图说明") < once.index("![资料到索引]") < once.index("读取之后")
    insert_diagram_reference(workspace, 1, source, "dependency-map", semantic)
    assert source.read_text(encoding="utf-8") == once
    source.write_text(once.replace("下图说明资料与索引如何交接。", "下图给出资料更新到索引重建的关系。"), encoding="utf-8")
    with pytest.raises(ValueError, match="锚点|位置"):
        insert_diagram_reference(workspace, 1, source, "dependency-map", semantic)
    insert_diagram_reference(workspace, 1, source, "dependency-map", semantic, rebind_position=True)
    assert source.read_text(encoding="utf-8").count("assets/figures/dependency-map.pdf") == 1


def test_native_d2_group_label_and_inbound_edge_stay_distinct(tmp_path):
    from pypdf import PdfReader

    from knowledge_product_workflow.d2 import resolve_d2, resolve_diagram_browser
    from knowledge_product_workflow.diagrams import submit_diagram
    from tests.test_diagrams import _approved_workspace

    if not resolve_d2().available or not resolve_diagram_browser().available:
        pytest.skip("需要已有D2和浏览器，不安装依赖")
    # Legacy grouped flow uses D2; current v6 layered_architecture uses SVG.
    # Keep the renderer that produced the reported inbound-label collision.
    workspace = _approved_workspace(tmp_path)
    source = workspace.root / "runs/group-label.md"
    source.write_text("# 1. 架构基础\n\n下图展示交接关系。\n", encoding="utf-8")
    semantic = workspace.root / "runs/group-label.json"
    semantic.write_text(json.dumps({"diagram_id": "group-label", "diagram_type": "layered_architecture",
        "direction": "down", "caption": "职责与提问", "reader_conclusion": "输入进入职责分组。",
        "nodes": [{"node_id": "question", "label": "用户提问"},
                  {"node_id": "retrieve", "label": "检索", "group": "职责"},
                  {"node_id": "context", "label": "上下文构造", "group": "职责"},
                  {"node_id": "answer", "label": "回答生成", "group": "职责"}],
        "edges": [{"source": "question", "target": "retrieve", "label": "提问"},
                  {"source": "retrieve", "target": "context", "label": "片段"},
                  {"source": "context", "target": "answer", "label": "上下文"}]}, ensure_ascii=False), encoding="utf-8")
    insert_diagram_reference(workspace, 1, source, "group-label", semantic)
    asset, manifest = submit_diagram(workspace, 1, source, semantic)
    assert manifest.renderer == "d2"
    assert "label.near: top-left" in (workspace.root / "runs/diagrams/group-label.d2").read_text(encoding="utf-8")
    text = "".join(page.extract_text() for page in PdfReader(asset).pages)
    # PDF提取可能在相邻CJK字形间插入水平空白；不合并换行/拉丁/数字空格。
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", text)
    labels = ("职责", "提问", "检索", "上下文构造", "回答生成")
    svg_root = ET.fromstring((workspace.root / "runs/diagrams/group-label.svg").read_text(encoding="utf-8"))
    svg_labels = ["".join(node.itertext()) for node in svg_root.iter() if node.tag.rsplit("}", 1)[-1] == "text"]
    assert all(label in svg_labels for label in labels)
    assert all(label in normalized for label in labels)
    assert (workspace.root / "assets/figures/group-label.png").is_file()
