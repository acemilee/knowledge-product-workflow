"""O11：结构图在插入引用前拒绝不合法语义，保留原输入与颜色规则。"""

import json
import pytest
import yaml

from knowledge_product_workflow.content_plan import submit_content_plan
from knowledge_product_workflow.diagrams import (
    DiagramInput,
    DiagramSubmissionError,
    create_diagram_template,
    insert_diagram_reference,
    submit_diagram,
)
from knowledge_product_workflow.illustration_palette import (
    register_category_colors,
)
from knowledge_product_workflow.presentation import _read_profile, publication_profile_path
from tests.test_illustration_policy import approved_workspace, plan, task


def _fixture(tmp_path, *, diagram_type="stage_flow", backend="vector", style=2):
    selected = task(diagram_type=diagram_type, backend=backend,
                    direction="right" if diagram_type == "stage_flow" else "down")
    workspace = approved_workspace(tmp_path, [selected])
    profile_path = publication_profile_path(workspace)
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    profile.update(illustration_style_revision=style)
    profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    supplied = workspace.root / "runs/content-plan.json"
    supplied.write_text(json.dumps(plan(2, [selected]), ensure_ascii=False), encoding="utf-8")
    submit_content_plan(workspace, 1, supplied)
    source = workspace.root / "runs/source.md"
    source.write_text("# 1. 资料边界\n\n## 1.1 输入关系\n\n下图说明资料与索引的交接。\n\n随后检查索引是否仍对应当前资料。\n", encoding="utf-8")
    data = {"diagram_id": "dependency-map", "task_id": "dependency-map",
            "diagram_type": diagram_type, "direction": selected["direction"],
            "backend": backend, "heading_anchor": "1.1 输入关系", "caption": "资料到索引",
            "reader_conclusion": "资料更新需要重建索引。",
            "nodes": [{"node_id": "docs", "label": "资料"}, {"node_id": "index", "label": "索引"}],
            "edges": [{"source": "docs", "target": "index"}],
            "presentation": {"scheme": "warm-focus", "node_colors": {"docs": "peach", "index": "sand"},
                             "group_colors": {}, "concept_nodes": {}}}
    semantic = workspace.root / "runs/diagram.json"
    _write(semantic, data)
    return workspace, source, semantic, data


def _write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _state(workspace):
    return {str(path.relative_to(workspace.root)): path.read_bytes()
            for path in workspace.root.rglob("*") if path.is_file()
            and "diagnostics" not in path.parts}


def _reject_without_writes(workspace, source, semantic, message):
    before = _state(workspace)
    caught = None
    try:
        insert_diagram_reference(workspace, 1, source, "dependency-map", semantic)
    except ValueError as error:
        caught = error
    after = _state(workspace)
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    print(json.dumps({"error": str(caught) if caught else None, "changed_paths": changed}, ensure_ascii=False))
    assert caught is not None, "非法输入已插入正文而未被拒绝"
    assert message in str(caught)
    assert after == before, "非法输入修改了章稿、position、input、登记或event"
    return caught


@pytest.mark.parametrize("shape,message", [
    ("stage_edge_label", "关系标签"),
    ("explicit_four_lines", "三行"),
    ("wrapped_four_lines", "三行"),
    ("stage_reverse", "相邻阶段"),
    ("stage_capacity", "2–5"),
])
@pytest.mark.parametrize("existing", [False, True])
def test_invalid_structured_input_has_zero_reference_writes(tmp_path, shape, message, existing):
    workspace, source, semantic, data = _fixture(tmp_path)
    if existing:
        insert_diagram_reference(workspace, 1, source, "dependency-map", semantic)
    if shape == "stage_edge_label":
        data["edges"][0]["label"] = "交接资料"
    elif shape == "explicit_four_lines":
        data["nodes"][0]["label"] = "输入\n核验\n建库\n输出"
    elif shape == "wrapped_four_lines":
        data["nodes"][0]["label"] = "资料" * 18
        # Five nodes make 195px cards; the same text then needs eight visual lines.
        data["nodes"] += [{"node_id": name, "label": name} for name in ("a", "b", "c")]
        data["presentation"]["node_colors"].update(a="sand", b="peach", c="sand")
    elif shape == "stage_reverse":
        data["edges"] = [{"source": "index", "target": "docs"}]
    else:
        data["nodes"] += [{"node_id": name, "label": name} for name in ("a", "b", "c", "d")]
        data["presentation"]["node_colors"].update(a="sand", b="peach", c="sand", d="peach")
    _write(semantic, data)
    _reject_without_writes(workspace, source, semantic, message)


def test_caption_number_is_rejected_before_any_reference_write(tmp_path):
    workspace, source, semantic, data = _fixture(tmp_path)
    data["caption"] = "图 1 资料到索引"
    _write(semantic, data)
    _reject_without_writes(workspace, source, semantic, "统一添加编号")


@pytest.mark.parametrize("family,count,message", [
    ("layered_architecture", 5, "2–4"),
    ("comparison", 3, "两个节点"),
    ("nested_structure", 2, "2–4"),
    ("card_matrix", 2, "3–6"),
])
def test_other_structured_families_use_existing_capacity_rules_before_writes(tmp_path, family, count, message):
    workspace, source, semantic, data = _fixture(tmp_path, diagram_type=family)
    for index in range(count - 2):
        node_id = f"extra{index}"
        data["nodes"].append({"node_id": node_id, "label": "组件"})
        data["presentation"]["node_colors"][node_id] = "peach"
    _write(semantic, data)
    _reject_without_writes(workspace, source, semantic, message)


@pytest.mark.parametrize("shape,message", [("group", "group分区"), ("identifier", "标识符")])
def test_structured_geometry_rejects_groups_and_unsplittable_identifiers(tmp_path, shape, message):
    workspace, source, semantic, data = _fixture(tmp_path)
    if shape == "group":
        data["nodes"][0]["group"] = "输入职责"
        data["presentation"]["group_colors"] = {"输入职责": "peach"}
    else:
        data["nodes"][0]["label"] = "unchanged_identifier_that_must_not_be_split"
    _write(semantic, data)
    _reject_without_writes(workspace, source, semantic, message)


def test_valid_three_lines_insert_once_without_external_tools_or_semantic_rewrite(tmp_path, monkeypatch):
    import knowledge_product_workflow.diagrams as diagrams

    workspace, source, semantic, data = _fixture(tmp_path)
    data["nodes"][0]["label"] = "资料\n出处\n日期"
    _write(semantic, data)
    original_input = semantic.read_bytes()
    def unavailable(*_args, **_kwargs):
        pytest.fail("插入引用不允许调用D2、浏览器或外部导出")
    for symbol in ("resolve_d2", "resolve_diagram_browser", "render_svg_with_browser"):
        monkeypatch.setattr(diagrams, symbol, unavailable)
    insert_diagram_reference(workspace, 1, source, "dependency-map", semantic)
    first = source.read_bytes()
    stored = (workspace.root / "runs/diagrams/dependency-map.input.json").read_bytes()
    position = (workspace.root / "runs/diagrams/dependency-map.position.json").read_bytes()
    insert_diagram_reference(workspace, 1, source, "dependency-map", semantic)
    assert source.read_bytes() == first
    assert semantic.read_bytes() == original_input
    assert (workspace.root / "runs/diagrams/dependency-map.input.json").read_bytes() == stored
    assert (workspace.root / "runs/diagrams/dependency-map.position.json").read_bytes() == position
    assert source.read_text(encoding="utf-8").count("assets/figures/dependency-map.pdf") == 1
    assert DiagramInput.model_validate_json(stored) == DiagramInput.model_validate(data)


def test_later_real_semantic_change_still_requires_reference_rebinding(tmp_path):
    workspace, source, semantic, data = _fixture(tmp_path)
    insert_diagram_reference(workspace, 1, source, "dependency-map", semantic)
    data["nodes"][0]["label"] = "更新资料"
    _write(semantic, data)
    before = _state(workspace)
    with pytest.raises(DiagramSubmissionError) as raised:
        submit_diagram(workspace, 1, source, semantic)
    assert raised.value.code == "DIAGRAM_SEMANTICS_CHANGED"
    assert _state(workspace) == before
    insert_diagram_reference(workspace, 1, source, "dependency-map", semantic)
    assert DiagramInput.model_validate_json((workspace.root / "runs/diagrams/dependency-map.input.json").read_bytes()) == DiagramInput.model_validate(data)


def test_legacy_reference_without_semantic_input_remains_idempotent(tmp_path):
    from tests.test_diagrams import _approved_workspace

    workspace = _approved_workspace(tmp_path)
    source = workspace.root / "runs/source.md"
    source.write_text("# 1. 架构基础\n", encoding="utf-8")
    insert_diagram_reference(workspace, 1, source, "legacy")
    first = source.read_bytes()
    insert_diagram_reference(workspace, 1, source, "legacy")
    assert source.read_bytes() == first


@pytest.mark.parametrize("scope", ["legacy", "non_structured", "host_image"])
def test_early_structured_rules_do_not_expand_legacy_or_other_family_scope(tmp_path, scope):
    if scope == "legacy":
        from tests.test_diagrams import _approved_workspace

        workspace = _approved_workspace(tmp_path, v6=True)
        source = workspace.root / "runs/source.md"
        source.write_text("# 1. 架构基础\n\n## 1.1 输入关系\n\n资料。\n", encoding="utf-8")
        semantic = workspace.root / "runs/diagram.json"
        data = {"diagram_id": "dependency-map", "diagram_type": "stage_flow", "direction": "right",
                "caption": "资料到索引", "reader_conclusion": "保留出处。",
                "nodes": [{"node_id": "docs", "label": "资料"}, {"node_id": "index", "label": "索引"}],
                "edges": [{"source": "docs", "target": "index", "label": "资料"}]}
    else:
        workspace, source, semantic, data = _fixture(tmp_path,
            diagram_type="flow" if scope == "non_structured" else "stage_flow",
            backend="host-image" if scope == "host_image" else "vector")
        data["nodes"][0]["label"] = "一\n二\n三\n四"
        data["edges"][0]["label"] = "资料"
    _write(semantic, data)
    insert_diagram_reference(workspace, 1, source, "dependency-map", semantic)
    assert "dependency-map.pdf" in source.read_text(encoding="utf-8")


def test_existing_concept_color_conflict_reports_both_references_without_writes(tmp_path):
    workspace, source, semantic, data = _fixture(tmp_path)
    data["presentation"]["concept_nodes"] = {"docs": "原始资料"}
    _write(semantic, data)
    insert_diagram_reference(workspace, 1, source, "dependency-map", semantic)
    data["presentation"]["node_colors"]["docs"] = "sand"
    _write(semantic, data)
    error = _reject_without_writes(workspace, source, semantic, "概念颜色冲突")
    assert all(value in str(error) for value in ("原始资料", "已登记", "peach", "本次", "docs", "sand"))


def test_intra_diagram_concept_color_conflict_reports_previous_and_current_references(tmp_path):
    workspace, source, semantic, data = _fixture(tmp_path)
    data["presentation"]["concept_nodes"] = {"docs": "原始资料", "index": "原始资料"}
    _write(semantic, data)
    error = _reject_without_writes(workspace, source, semantic, "概念颜色冲突")
    assert all(value in str(error) for value in ("原始资料", "已有", "docs", "peach", "本次", "index", "sand"))


@pytest.mark.parametrize("violation", ["locked_scheme", "incompatible_color"])
def test_current_palette_policy_rejection_keeps_source_and_saved_inputs(tmp_path, violation):
    workspace, source, semantic, data = _fixture(tmp_path)
    if violation == "locked_scheme":
        profile_path = publication_profile_path(workspace)
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        profile.update(illustration_palette_policy="locked", illustration_palette_scheme="cool-structure")
        profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
        message = "锁定色板"
    else:
        data["presentation"]["node_colors"]["docs"] = "blue"
        _write(semantic, data)
        message = "scheme不兼容"
    _reject_without_writes(workspace, source, semantic, message)


def test_template_context_exposes_caption_family_direction_and_current_concept_colors(tmp_path):
    workspace, source, semantic, data = _fixture(tmp_path)
    data["presentation"]["concept_nodes"] = {"docs": "原始资料"}
    register_category_colors(workspace, _read_profile(publication_profile_path(workspace)), DiagramInput.model_validate(data).presentation)
    _template, context_path = create_diagram_template(workspace, 1, source, "dependency-map")
    context = json.loads(context_path.read_bytes())
    assert context["registered_concept_colors"] == {"原始资料": "peach"}
    assert context["diagram_type_direction_rules"]["approved_task"] == {"diagram_type": "stage_flow", "direction": "right"}
    assert context["diagram_type_direction_rules"]["relation_families"]["handoff"] == {"diagram_type": "stage_flow", "direction": "right"}
    assert any("caption" in constraint and "统一添加编号" in constraint for constraint in context["submission_constraints"])
