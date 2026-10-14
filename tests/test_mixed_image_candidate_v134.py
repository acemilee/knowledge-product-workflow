"""受控混合候选用synthetic素材模拟ledger；不证明真实AI来源／本人认可。"""

import base64
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from knowledge_product_workflow.content_plan import submit_content_plan
from knowledge_product_workflow.diagrams import (
    DiagramInput,
    insert_diagram_reference,
    submit_diagram,
)
from knowledge_product_workflow.illustration_budget import (
    budget_path,
    freeze_image_budget,
    load_image_budget,
    record_image_call,
)
from knowledge_product_workflow.illustration_updates import candidate_root
from knowledge_product_workflow.illustrations import (
    preflight_illustration,
    read_asset_style,
    record_asset_style,
    style_review_fingerprint,
)
from knowledge_product_workflow.presentation import publication_profile_path
from tests.test_illustration_policy import approved_workspace, plan, task
from tests.test_illustration_style import review
from tests.test_illustration_v2 import png


def _mixed_workspace(tmp_path, *, caption="隐藏的处理阶段"):
    item = task("material-stages", backend="host-image", diagram_type="stage_flow",
                trigger="hidden_mechanism", relation_kind="handoff", direction="right")
    workspace = approved_workspace(tmp_path, [item])
    profile_path = publication_profile_path(workspace)
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    profile.update(illustration_style_revision=2, illustration_backend_preference="host-image",
                   illustration_category_colors={"输入阶段": "blue"})
    profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    source = workspace.root / "runs/chapter-source.md"
    source.write_text("# 1. 资料边界\n\n## 1.1 输入关系\n\n核对三个隐藏处理阶段与各自职责。\n", encoding="utf-8")
    value = plan(2, [item])
    supplied = workspace.root / "runs/plan.yaml"
    supplied.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    submit_content_plan(workspace, 1, supplied)
    semantic = DiagramInput(
        diagram_id=item["task_id"], task_id=item["task_id"], diagram_type="stage_flow",
        material_composition="stage_flow", direction="right", backend="host-image",
        heading_anchor="1.1 输入关系", caption=caption, reader_conclusion="输入、定位与证据交接按冻结关系工作。",
        nodes=[{"node_id": key, "label": label} for key, label in (("a", "输入"), ("b", "定位"), ("c", "证据"))],
        edges=[{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
        admission={"family": "flow", "information_form": "process_overview",
                   "reader_job": value["sections"][0]["reader_job"], "visual_gain": "同时看到隐藏处理阶段与每个阶段的职责",
                   "evidence_boundary": "本地synthetic素材与模拟ledger，不代表实际AI或本人认可"},
        presentation={"scheme": "cool-structure", "node_colors": {"a": "blue", "b": "lavender", "c": "stone"},
                      "group_colors": {}, "concept_nodes": {"a": "输入阶段"}},
    )
    input_path = workspace.root / "runs/material-input.json"
    input_path.write_text(semantic.model_dump_json(indent=2), encoding="utf-8")
    insert_diagram_reference(workspace, 1, source, semantic.diagram_id, input_path)
    freeze_image_budget(workspace, approved_by="隔离测试预算，不代表真实发送授权")
    return workspace, source, input_path, semantic


def _mock_export(monkeypatch):
    browser = Path(__file__)  # Existing file identity, not a claimed real browser.
    monkeypatch.setattr("knowledge_product_workflow.d2.resolve_diagram_browser",
                        lambda: SimpleNamespace(available=True, path=browser, version="synthetic-exporter"))
    monkeypatch.setattr("knowledge_product_workflow.diagrams.resolve_diagram_browser",
                        lambda: SimpleNamespace(available=True, path=browser, version="synthetic-exporter"))

    def render(_browser, _svg, pdf, image, **_kwargs):
        pdf.write_bytes(b"%PDF-synthetic-exporter")
        image.write_bytes(png((228, 237, 245), ink_height=40))

    monkeypatch.setattr("knowledge_product_workflow.d2.render_svg_with_browser", render)
    monkeypatch.setattr("knowledge_product_workflow.diagrams.render_svg_with_browser", render)


def _completed_material(workspace, semantic, *, origin="synthetic"):
    reservation = preflight_illustration(workspace, 1, semantic, supports_references=True, reference_capacity=3)
    material = workspace.root / "runs/synthetic-material.png"
    material.write_bytes(png((245, 245, 245), ink_height=60))
    record_image_call(workspace, reservation["call_id"], state="dispatched")
    record_image_call(workspace, reservation["call_id"], state="completed", image_path=material,
                      material_origin=origin)
    return material, reservation


def test_completed_material_is_composed_into_a_pending_candidate_without_spending_another_call(tmp_path, monkeypatch):
    _mock_export(monkeypatch)
    workspace, source, input_path, semantic = _mixed_workspace(tmp_path)
    material, reservation = _completed_material(workspace, semantic)
    original = material.read_bytes()
    budget = budget_path(workspace).read_bytes()

    asset, manifest = submit_diagram(workspace, 1, source, input_path,
                                    host_image=material, image_call_id=reservation["call_id"])

    assert manifest.renderer == "host-image+svg"
    assert asset.parent == candidate_root(workspace, reservation["call_id"])
    assert not (workspace.root / "assets/figures/material-stages.png").exists()
    assert manifest.original_image_sha256 == sha256(original).hexdigest()
    assert manifest.material_origin == "synthetic"
    assert material.read_bytes() == original and budget_path(workspace).read_bytes() == budget
    assert len(load_image_budget(workspace).slots[0].calls) == 1
    svg = (asset.parent / "material-stages.svg").read_text(encoding="utf-8")
    assert all(f">{node.label}</text>" in svg for node in semantic.nodes)
    assert "#E4EDF5" in svg  # Approved blue, not the old probe's four hardcoded fills.
    import re
    assert all(base64.b64decode(value) == original for value in re.findall(r"base64,([^\"]+)", svg))


def test_declared_host_origin_without_native_source_evidence_cannot_be_staged(tmp_path, monkeypatch):
    _mock_export(monkeypatch)
    workspace, source, input_path, semantic = _mixed_workspace(tmp_path)
    material, reservation = _completed_material(workspace, semantic, origin="host-image")
    before = budget_path(workspace).read_bytes()

    with pytest.raises(ValueError, match="原工具|来源回执"):
        submit_diagram(workspace, 1, source, input_path, host_image=material, image_call_id=reservation["call_id"])

    assert budget_path(workspace).read_bytes() == before
    assert not (candidate_root(workspace, reservation["call_id"]) / "index.json").exists()


def test_checked_first_mixed_candidate_stays_pending_until_a_current_human_response(tmp_path, monkeypatch):
    from knowledge_product_workflow.illustration_updates import activate_candidate

    _mock_export(monkeypatch)
    workspace, source, input_path, semantic = _mixed_workspace(tmp_path)
    material, reservation = _completed_material(workspace, semantic)
    submit_diagram(workspace, 1, source, input_path, host_image=material, image_call_id=reservation["call_id"])
    record_asset_style(workspace, review(diagram_id=semantic.diagram_id, candidate_call_id=reservation["call_id"],
                                       role_glyph_height_ratios={}, measurement_source="unavailable",
                                       reviewed_by="隔离synthetic模拟观察，不代表真实用户"))

    with pytest.raises(ValueError, match="本人|当前.*响应"):
        activate_candidate(workspace, semantic.diagram_id, reservation["call_id"], chapter_number=1)

    assert not (workspace.root / "assets/figures/material-stages.png").exists()


@pytest.mark.parametrize("state", ["reserved", "dispatched", "failed", "unknown"])
def test_noncompleted_material_never_becomes_a_candidate_or_resets_the_ledger(tmp_path, monkeypatch, state):
    _mock_export(monkeypatch)
    workspace, source, input_path, semantic = _mixed_workspace(tmp_path)
    reservation = preflight_illustration(workspace, 1, semantic, supports_references=True, reference_capacity=3)
    material = workspace.root / "runs/synthetic-unsettled.png"
    material.write_bytes(png((245, 245, 245), ink_height=30))
    if state != "reserved":
        record_image_call(workspace, reservation["call_id"], state="dispatched")
    if state in {"failed", "unknown"}:
        record_image_call(workspace, reservation["call_id"], state=state)
    before = budget_path(workspace).read_bytes()

    with pytest.raises(ValueError, match="已完成"):
        submit_diagram(workspace, 1, source, input_path, host_image=material, image_call_id=reservation["call_id"])

    assert budget_path(workspace).read_bytes() == before
    assert load_image_budget(workspace).slots[0].calls[0].state == state
    assert not (candidate_root(workspace, reservation["call_id"]) / "index.json").exists()


def test_missing_approved_task_is_rejected_before_reserving_a_native_attempt(tmp_path, monkeypatch):
    _mock_export(monkeypatch)
    workspace, _, _, semantic = _mixed_workspace(tmp_path)
    before = budget_path(workspace).read_bytes()
    semantic.task_id = "not-approved"

    with pytest.raises(ValueError, match="任务.*授权"):
        preflight_illustration(workspace, 1, semantic, supports_references=True, reference_capacity=3)

    assert budget_path(workspace).read_bytes() == before


def _checked_candidate(tmp_path, monkeypatch, *, assessment="passed"):
    _mock_export(monkeypatch)
    workspace, source, input_path, semantic = _mixed_workspace(tmp_path)
    material, reservation = _completed_material(workspace, semantic)
    asset, manifest = submit_diagram(workspace, 1, source, input_path, host_image=material, image_call_id=reservation["call_id"])
    payload = review(diagram_id=semantic.diagram_id, candidate_call_id=reservation["call_id"],
                     role_glyph_height_ratios={}, measurement_source="unavailable",
                     reviewed_by="synthetic隔离模拟观察，不代表真实本人")
    payload.observations["typography"].assessment = assessment
    record_asset_style(workspace, payload)
    return workspace, source, input_path, semantic, material, reservation, asset, manifest


def _isolated_approval(workspace, semantic, reservation):
    return {"chapter_number": 1, "confirm_mixed_candidate": True,
            "approved_by": "隔离测试模拟本人", "user_response": "本条仅验证测试门，不代表任何生产样品获得本人认可。",
            "review_input": style_review_fingerprint(workspace, semantic.diagram_id, reservation["call_id"])}


@pytest.mark.parametrize("assessment", ["failed", "uncertain"])
def test_failed_or_uncertain_mixed_candidate_cannot_be_activated_even_with_a_test_response(tmp_path, monkeypatch, assessment):
    from knowledge_product_workflow.illustration_updates import activate_candidate

    workspace, _, _, semantic, _, reservation, _, _ = _checked_candidate(tmp_path, monkeypatch, assessment=assessment)
    before = budget_path(workspace).read_bytes()

    with pytest.raises(ValueError, match="failed|uncertain"):
        activate_candidate(workspace, semantic.diagram_id, reservation["call_id"],
                           **_isolated_approval(workspace, semantic, reservation))

    assert budget_path(workspace).read_bytes() == before
    assert not (workspace.root / "assets/figures/material-stages.png").exists()


def test_a_stale_response_fingerprint_cannot_activate_current_mixed_output(tmp_path, monkeypatch):
    from knowledge_product_workflow.illustration_updates import activate_candidate

    workspace, _, _, semantic, _, reservation, _, _ = _checked_candidate(tmp_path, monkeypatch)
    approval = _isolated_approval(workspace, semantic, reservation) | {"review_input": "0" * 64}

    with pytest.raises(ValueError, match="review-input"):
        activate_candidate(workspace, semantic.diagram_id, reservation["call_id"], **approval)

    assert not (workspace.root / "assets/figures/material-stages.png").exists()


def test_local_synthetic_activation_binds_the_test_response_and_keeps_source_costs(tmp_path, monkeypatch):
    from knowledge_product_workflow.illustration_updates import activate_candidate
    from knowledge_product_workflow.illustrations import style_resources
    from knowledge_product_workflow.pdf_visual import _current_style_anchor

    workspace, source, _, semantic, material, reservation, _, _ = _checked_candidate(tmp_path, monkeypatch)
    before = budget_path(workspace).read_bytes()
    original = material.read_bytes()
    approve = _isolated_approval(workspace, semantic, reservation)

    activate_candidate(workspace, semantic.diagram_id, reservation["call_id"], **approve)

    assert (workspace.root / "assets/figures/material-stages.png").is_file()
    assert material.read_bytes() == original and budget_path(workspace).read_bytes() == before
    assert read_asset_style(workspace, semantic.diagram_id) is not None
    (workspace.root / "delivery/book.qmd").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    assert _current_style_anchor(workspace) == style_resources(2)["anchor_sha256"]
    receipt = json.loads((candidate_root(workspace, reservation["call_id"]) / "human-response.json").read_bytes())
    assert receipt["source_origin"] == "synthetic"
    assert receipt["response_sha256"] == sha256(approve["user_response"].encode()).hexdigest()
    with pytest.raises(ValueError, match="已消费"):
        activate_candidate(workspace, semantic.diagram_id, reservation["call_id"], **approve)


@pytest.mark.parametrize("change", ["font_css", "body", "concept_color"])
def test_composition_inputs_invalidate_checked_candidate_without_consuming_an_image_attempt(tmp_path, monkeypatch, change):
    from knowledge_product_workflow.illustration_updates import activate_candidate

    workspace, source, _, semantic, _, reservation, _, _ = _checked_candidate(tmp_path, monkeypatch)
    approval = _isolated_approval(workspace, semantic, reservation)
    before = budget_path(workspace).read_bytes()
    if change == "font_css":
        from knowledge_product_workflow.d2 import diagram_font_css
        css = diagram_font_css()
        monkeypatch.setattr("knowledge_product_workflow.d2.diagram_font_css", lambda: css + "\n/* changed font input */")
    elif change == "body":
        source.write_text(source.read_text(encoding="utf-8") + "\n改变当前可见字段的解释。\n", encoding="utf-8")
    else:
        profile_path = publication_profile_path(workspace)
        profile = yaml.safe_load(profile_path.read_bytes())
        profile["illustration_category_colors"] = {"输入阶段": "lavender"}
        profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match="过期|变化|未检查"):
        activate_candidate(workspace, semantic.diagram_id, reservation["call_id"], **approval)

    assert budget_path(workspace).read_bytes() == before
    assert not (workspace.root / "assets/figures/material-stages.png").exists()


def test_existing_failed_attempt_and_completed_source_stay_in_the_same_original_task(tmp_path, monkeypatch):
    _mock_export(monkeypatch)
    workspace, source, input_path, semantic = _mixed_workspace(tmp_path)
    failed = preflight_illustration(workspace, 1, semantic, supports_references=True, reference_capacity=3)
    record_image_call(workspace, failed["call_id"], state="dispatched")
    record_image_call(workspace, failed["call_id"], state="failed")
    material, reservation = _completed_material(workspace, semantic)
    freeze_image_budget(workspace, approved_by="隔离预算修订保留既有失败及消费")
    before = budget_path(workspace).read_bytes()

    submit_diagram(workspace, 1, source, input_path, host_image=material, image_call_id=reservation["call_id"])
    cached = preflight_illustration(workspace, 1, semantic, supports_references=True, reference_capacity=3)

    assert cached["cache_hit"] is True and cached["call_id"] == reservation["call_id"]
    assert budget_path(workspace).read_bytes() == before
    slot = load_image_budget(workspace).slots[0]
    assert slot.task_id == "material-stages" and [call.state for call in slot.calls] == ["failed", "completed"]


def test_failed_style_history_survives_recomposition_of_the_same_completed_material(tmp_path, monkeypatch):
    workspace, source, input_path, semantic, material, reservation, asset, _ = _checked_candidate(tmp_path, monkeypatch, assessment="failed")
    failed = (asset.parent / "style-check.json").read_bytes()
    before = budget_path(workspace).read_bytes()

    submit_diagram(workspace, 1, source, input_path, host_image=material, image_call_id=reservation["call_id"])

    assert budget_path(workspace).read_bytes() == before
    assert any(path.read_bytes() == failed for path in (asset.parent / "history").glob("*/style-check.json"))
    assert read_asset_style(workspace, semantic.diagram_id, reservation["call_id"])["input"]["observations"]["typography"]["assessment"] == "failed"


def test_labeled_or_relation_changed_inputs_cannot_reuse_an_old_source_cache(tmp_path, monkeypatch):
    _mock_export(monkeypatch)
    workspace, source, input_path, semantic = _mixed_workspace(tmp_path)
    material, reservation = _completed_material(workspace, semantic)
    changed = semantic.model_dump(mode="json")
    changed["nodes"][0]["label"] = "改变字段"
    input_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    before = budget_path(workspace).read_bytes()

    with pytest.raises(ValueError, match="语义.*变化"):
        submit_diagram(workspace, 1, source, input_path, host_image=material, image_call_id=reservation["call_id"])

    assert budget_path(workspace).read_bytes() == before


def test_receipt_is_bound_but_a_declared_host_origin_is_never_auto_verified(tmp_path, monkeypatch):
    _mock_export(monkeypatch)
    workspace, source, input_path, semantic = _mixed_workspace(tmp_path)
    reservation = preflight_illustration(workspace, 1, semantic, supports_references=True, reference_capacity=3)
    material = workspace.root / "runs/synthetic-receipt-material.png"
    material.write_bytes(png((245, 245, 245), ink_height=40))
    receipt = workspace.root / "runs/synthetic-session-receipt.json"
    receipt.write_text('{"fixture":"synthetic simulated native receipt; not actual AI evidence"}', encoding="utf-8")
    record_image_call(workspace, reservation["call_id"], state="dispatched")
    call = record_image_call(workspace, reservation["call_id"], state="completed", image_path=material,
                             material_origin="host-image", source_evidence=receipt)

    _, manifest = submit_diagram(workspace, 1, source, input_path, host_image=material, image_call_id=call.call_id)

    assert call.source_evidence_sha256 == sha256(receipt.read_bytes()).hexdigest()
    assert manifest.composition_evidence["material_origin_verification"].startswith("declared_only")
    receipt.write_text("changed synthetic fixture", encoding="utf-8")
    with pytest.raises(ValueError, match="来源回执已变化"):
        submit_diagram(workspace, 1, source, input_path, host_image=material, image_call_id=call.call_id)


@pytest.mark.parametrize("change", [{"diagram_type": "flow"}, {"backend": "vector"},
                                     {"direction": "down"}, {"edges": [{"source": "c", "target": "a"}]}])
def test_other_families_and_unsupported_relation_grammars_are_explicitly_rejected(tmp_path, change):
    workspace, _, _, semantic = _mixed_workspace(tmp_path)
    before = budget_path(workspace).read_bytes()

    with pytest.raises(ValueError, match="stage_flow|vector"):
        DiagramInput.model_validate(semantic.model_dump(mode="json") | change)

    assert budget_path(workspace).read_bytes() == before


def test_existing_commands_schema_and_help_publish_the_finite_mixed_interface():
    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app

    runner = CliRunner()
    for command, fields in (("record-illustration-call", {"material_origin", "source_evidence"}),
                            ("activate-illustration-candidate", {"confirm_mixed_candidate", "approved_by", "user_response", "review_input"})):
        result = runner.invoke(app, ["commands", "--all", "--json", "--command", command])
        assert result.exit_code == 0, result.output
        assert fields <= {item["name"] for item in json.loads(result.output)["commands"][0]["parameters"]}
        help_result = runner.invoke(app, [command, "--help"])
        assert help_result.exit_code == 0
        if command.startswith("activate"):
            assert "代签" in help_result.output
    schema = runner.invoke(app, ["diagram-schema"])
    assert schema.exit_code == 0 and "material_composition" in json.loads(schema.output)["json_schema"]["properties"]
    schema = runner.invoke(app, ["illustration-schema"])
    assert schema.exit_code == 0 and "source_evidence_sha256" in json.loads(schema.output)["call_receipt"]["properties"]


def test_pending_material_failure_uses_the_same_second_attempt_and_original_png_as_edit_target(tmp_path, monkeypatch):
    workspace, _, _, semantic, material, reservation, _, _ = _checked_candidate(tmp_path, monkeypatch)
    value = review(diagram_id=semantic.diagram_id, candidate_call_id=reservation["call_id"],
                   measurement_source="unavailable", reviewed_by="synthetic素材失败的模拟审阅")
    value.observations["decorations"].assessment = "failed"
    record_asset_style(workspace, value)
    original = material.read_bytes()

    correction = preflight_illustration(workspace, 1, semantic, supports_references=True,
                                        reference_capacity=3, repair_reason="素材出现声明外装饰，应只在原素材中移除。")

    assert correction["call_id"] != reservation["call_id"]
    assert correction["edit_target"] == str(material)
    assert [item["role"] for item in correction["references"]] == ["style", "palette", "edit_target"]
    record_image_call(workspace, correction["call_id"], state="dispatched")
    record_image_call(workspace, correction["call_id"], state="failed")
    assert [call.state for call in load_image_budget(workspace).slots[0].calls] == ["completed", "failed"]
    assert material.read_bytes() == original
    with pytest.raises(ValueError, match="两次"):
        preflight_illustration(workspace, 1, semantic, supports_references=True,
                              reference_capacity=3, repair_reason="素材明确装饰失败仍未修复，不扩大调用预算。")


def test_deterministic_mixed_text_failure_does_not_dispatch_or_reserve_an_ai_repair(tmp_path, monkeypatch):
    workspace, _, _, semantic, _, _, _, _ = _checked_candidate(tmp_path, monkeypatch, assessment="failed")
    before = budget_path(workspace).read_bytes()

    with pytest.raises(ValueError, match="确定性层"):
        preflight_illustration(workspace, 1, semantic, supports_references=True,
                              reference_capacity=3, repair_reason="图内文字字形不符合当前固定字体要求。")

    assert budget_path(workspace).read_bytes() == before


@pytest.mark.parametrize("invalid", ["fallback_policy", "missing_font_css", "long_label"])
def test_mixed_font_and_capacity_preflight_fail_before_any_reservation_or_render(tmp_path, monkeypatch, invalid):
    _mock_export(monkeypatch)
    workspace, _, _, semantic = _mixed_workspace(tmp_path)
    calls = []

    def unexpected_render(*_args, **_kwargs):
        calls.append("render")
        raise AssertionError("不合法混合输入不应调渲染工具或生成配色参考")

    monkeypatch.setattr("knowledge_product_workflow.d2.render_svg_with_browser", unexpected_render)
    if invalid == "fallback_policy":
        profile_path = publication_profile_path(workspace)
        profile = yaml.safe_load(profile_path.read_bytes())
        profile["font_policy"] = "fallback"
        profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    elif invalid == "missing_font_css":
        monkeypatch.setattr("knowledge_product_workflow.d2.diagram_font_css", lambda: "")
    else:
        semantic.nodes[0].label = "完整阶段名" * 4  # Exactly three lines in the shared layout; mixed allows two.
    before = budget_path(workspace).read_bytes()

    with pytest.raises(ValueError, match="字体|两行"):
        preflight_illustration(workspace, 1, semantic, supports_references=True, reference_capacity=3)

    assert budget_path(workspace).read_bytes() == before
    assert load_image_budget(workspace).slots == [] and calls == []


def test_native_synthetic_material_exports_real_paired_assets_and_remains_pending(tmp_path):
    import xml.etree.ElementTree as ET

    from pypdf import PdfReader

    from knowledge_product_workflow.d2 import resolve_diagram_browser
    from knowledge_product_workflow.illustration_updates import activate_candidate

    if not resolve_diagram_browser().available:
        pytest.skip("需要现有SVG exporter；跳过不是原生混合导出通过")
    workspace, source, input_path, semantic = _mixed_workspace(tmp_path, caption="RAG 隐藏处理阶段")
    material, reservation = _completed_material(workspace, semantic)
    original = material.read_bytes()
    before = budget_path(workspace).read_bytes()

    asset, manifest = submit_diagram(workspace, 1, source, input_path,
                                    host_image=material, image_call_id=reservation["call_id"])

    tree = ET.fromstring((asset.parent / "material-stages.svg").read_text(encoding="utf-8"))
    labels = [node.text for node in tree.iter() if node.tag.endswith("text")]
    assert all(node.label in labels for node in semantic.nodes)
    arrows = [node for node in tree.iter() if node.attrib.get("marker-end") == "url(#arrow)"]
    assert len(arrows) == len(semantic.edges)
    fonts = [str(font.get_object().get("/BaseFont", ""))
             for page in PdfReader(asset).pages for font in page["/Resources"]["/Font"].values()]
    assert any("PingFang" in value for value in fonts), fonts
    assert any("Inter" in value for value in fonts), fonts
    assert not any("Arial" in value or "MicrosoftYaHei" in value for value in fonts), fonts
    text = "".join("".join((page.extract_text() or "").split()) for page in PdfReader(asset).pages)
    assert all(node.label in text for node in semantic.nodes), text
    assert material.read_bytes() == original and budget_path(workspace).read_bytes() == before
    assert manifest.material_origin == "synthetic" and manifest.composition_evidence["production_ready"] is False
    assert not (workspace.root / "assets/figures/material-stages.png").exists()
    with pytest.raises(ValueError, match="未检查|failed"):
        activate_candidate(workspace, semantic.diagram_id, reservation["call_id"], chapter_number=1)
    (workspace.root / "runs/native-mixed-candidate-evidence.json").write_text(json.dumps(
        {"synthetic": True, "actual_ai_dispatched": False, "human_approval": "pending",
         "renderer": manifest.renderer, "candidate": str(asset), "fonts": fonts,
         "labels": labels, "declared_relations": manifest.composition_evidence["relations"],
         "source_png_sha256": sha256(original).hexdigest(), "native_svg_pdf_png_exported": True},
        ensure_ascii=False, indent=2), encoding="utf-8")
