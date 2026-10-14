"""Finite illustration admission, host prompts and version-bound style evidence."""

from __future__ import annotations

import json
import re
import struct
from datetime import UTC, datetime
from hashlib import sha256
from importlib.resources import files
from itertools import pairwise
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_product_workflow.illustration_budget import reserve_image
from knowledge_product_workflow.presentation import PublicationProfile, publication_profile_path
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.workspace import Workspace

STYLE_CHECKS = frozenset({"labels", "relations", "typography", "role_scale", "palette",
                          "density", "decorations", "geometry", "reference_leakage"})


class IllustrationAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: Literal["architecture", "hierarchy", "flow"]
    reader_job: str = Field(min_length=8, max_length=240)
    visual_gain: str = Field(min_length=8, max_length=240)
    information_form: Literal["spatial_relationship", "hierarchy", "branching_flow", "process_overview"]
    evidence_boundary: str = Field(min_length=8, max_length=240)
    required: bool = True
    fallback: Literal["table", "list"] = "list"
    planned_width_mm: float = Field(default=184, ge=120, le=184)
    minimum_glyph_height_ratio: float = Field(default=0.034, gt=0, le=0.12)

    @field_validator("reader_job", "visual_gain", "evidence_boundary")
    @classmethod
    def meaningful_text(cls, value):
        if len(value.strip()) < 8:
            raise ValueError("图任务、增益与证据边界须具体填写，不能用空白绕过")
        return value.strip()

    @model_validator(mode="after")
    def consistent_family(self):
        expected = {"architecture": "spatial_relationship", "hierarchy": "hierarchy", "flow": "branching_flow"}
        if self.information_form != expected[self.family] and not (
            self.family == "flow" and self.information_form == "process_overview"
        ):
            raise ValueError("图族与信息形式不一致；先确定教学关系再选后端")
        return self


class StyleObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment: Literal["passed", "failed", "uncertain"]
    note: str = Field(min_length=8)


class AssetStyleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagram_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    observations: dict[str, StyleObservation]
    role_glyph_height_ratios: dict[str, float] = Field(default_factory=dict)
    measurement_source: Literal["pixel_measurement", "vector", "visual_estimate", "unavailable"]
    reviewed_by: str = Field(min_length=1)
    text_regions: list[TextRegion] = Field(default_factory=list)
    candidate_call_id: str | None = Field(default=None, pattern=r"^image-call-[a-z0-9]+$")
    color_regions: list[ColorRegion] = Field(default_factory=list)


class TextRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["title", "region", "node", "description", "conclusion"]
    box: list[int] = Field(min_length=4, max_length=4)
    script: Literal["cjk", "latin"] = "cjk"


class ColorRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_id: str = Field(min_length=1)
    reference_kind: Literal["node", "group"] = "node"
    box: list[int] = Field(min_length=4,max_length=4)
    kind: Literal["flat", "mixed"] = "flat"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def style_resources(revision: int = 1) -> dict:
    resource = files("knowledge_product_workflow").joinpath("templates", "illustrations")
    spec = json.loads(resource.joinpath("style-v2.json" if revision == 2 else "style.json").read_text(encoding="utf-8"))
    if sha256(resource.joinpath(spec.get("anchor_file", "style-anchor.png")).read_bytes()).hexdigest() != spec["anchor_sha256"]:
        raise ValueError("正式风格母版资源hash不匹配；请修复规范安装包")
    return spec


def current_profile(workspace: Workspace) -> PublicationProfile:
    return PublicationProfile.model_validate(yaml.safe_load(publication_profile_path(workspace).read_text(encoding="utf-8")))


def layout_resources() -> dict:
    return json.loads(files("knowledge_product_workflow").joinpath("templates", "illustrations", "layouts.json").read_text(encoding="utf-8"))


def style_binding(workspace: Workspace, family: str | None = None, *, semantic=None, diagram_id: str | None = None) -> str:
    profile = current_profile(workspace)
    spec = style_resources(profile.illustration_style_revision)
    values = {"style": spec, "style_revision": profile.illustration_style_revision,
              "palette": profile.palette.model_dump()}
    if profile.illustration_style_revision == 2:
        from knowledge_product_workflow.illustration_palette import (
            diagram_presentation_digest,
            load_category_colors,
        )
        if semantic is None and diagram_id:
            from knowledge_product_workflow.diagrams import DiagramInput
            semantic = DiagramInput.model_validate_json((workspace.root / "runs/diagrams" / f"{diagram_id}.input.json").read_text(encoding="utf-8"))
        if semantic is not None and semantic.presentation is not None:
            values["presentation"] = diagram_presentation_digest(profile, semantic.presentation, load_category_colors(workspace))
            values["backend"] = semantic.backend
    if semantic is not None and semantic.task_id is not None:
        values["style"] = {key: value for key, value in spec.items() if key != "approval_state"}
    if semantic is not None and semantic.admission is not None and semantic.admission.information_form == "process_overview":
        values["layout"] = overview_layout()
    elif family in layout_resources():
        values["layout"] = layout_resources()[family]
    return sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def semantic_digest(semantic) -> str:
    values = semantic.model_dump(mode="json", exclude={"heading_anchor", "diagram_id", "task_id", "presentation"})
    if semantic.presentation is not None:
        values.pop("backend", None)
        if values.get("admission"):
            values["admission"] = {k: v for k, v in values["admission"].items() if k not in {"planned_width_mm", "minimum_glyph_height_ratio", "fallback", "required"}}
    return sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def overview_layout() -> dict:
    return json.loads(files("knowledge_product_workflow").joinpath(
        "templates", "illustrations", "process-overview-v2.json").read_text(encoding="utf-8"))


def require_verified_sample(workspace: Workspace, task_id: str | None) -> None:
    from knowledge_product_workflow.illustration_budget import budget_path, load_image_budget
    if task_id is None or not budget_path(workspace).is_file():
        return
    budget = load_image_budget(workspace)
    active = [slot for slot in budget.slots if slot.task_id in budget.approved_tasks
              and any(call.state not in {"reserved", "cancelled"} for call in slot.calls)]
    if not active:
        return
    sample = min(active, key=lambda slot: slot.calls[0].reserved_at)
    if sample.task_id == task_id:
        return
    for diagram_id in sample.diagram_ids:
        proof = read_asset_style(workspace, diagram_id)
        if proof and not proof["numeric_blockers"] and not proof["numeric_reviews"] and all(
            item["assessment"] == "passed" for item in proof["input"]["observations"].values()
        ):
            return
    raise ValueError("首张样图尚未合格；先在原任务有限纠正/处理待决，两次失败停止批量AI，不继续赌下一张")


def image_cache_key(workspace: Workspace, semantic, model: str | None, *, references=None) -> str:
    parameters = {"backend": "host-image", "model": model, "size": "1672x941", "quality": "high"}
    if references:
        parameters["references"] = [{"role": item["role"], "sha256": item["sha256"]} for item in references]
    return sha256((semantic_digest(semantic) + style_binding(workspace, semantic.admission.family if semantic.admission else None, semantic=semantic) +
                  json.dumps(parameters, sort_keys=True)).encode()).hexdigest()


def png_dimensions(path: Path, *, enforce_host: bool = True) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("宿主返回必须是有效PNG位图，不能提交SVG或伪造扩展名")
    width, height = struct.unpack("!II", data[16:24])
    if enforce_host and (not (1200 <= width <= 8192 and 600 <= height <= 8192) or abs(width / height - 16 / 9) > 0.06):
        raise ValueError("插图须为清晰16:9横向PNG，宽至少1200像素；不得自由改变画布")
    return width, height


def preflight_illustration(workspace: Workspace, chapter_number: int, semantic,
                          *, supports_references: bool, requested_model: str | None = None,
                          actual_model: str | None = None, repair_reason: str | None = None,
                          reference_capacity: int | None = None) -> dict:
    from knowledge_product_workflow.content_plan import (
        _flatten_outline,
        require_current_content_plan,
    )
    from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract

    require_current_chapter_contract(workspace, chapter_number)
    profile = current_profile(workspace)
    if profile.illustration_style_revision not in {1, 2} or semantic.backend != "host-image" or semantic.admission is None:
        raise ValueError("生图须选择host-image、填写准入，并在出版预览确认插图风格revision 1或2")
    if profile.illustration_style_revision == 2 and profile.illustration_backend_preference != "host-image":
        raise ValueError("尚未在Brief出版预览选择host-image；停止生图预约，先确认后端方案")
    if not supports_references:
        raise ValueError("当前正式宿主生图入口须支持style参考；无此能力请选择已支持的矢量形式")
    if requested_model is not None and requested_model != actual_model:
        raise ValueError("宿主未证明实际模型符合指定型号；停止，不能宣称锁定image-2.5")
    from knowledge_product_workflow.illustration_tasks import require_diagram_task
    task_binding = require_diagram_task(workspace, chapter_number, semantic)
    require_verified_sample(workspace, semantic.task_id)
    if semantic.admission.information_form == "process_overview":
        if profile.illustration_style_revision != 2 or task_binding is None:
            raise ValueError("机制总览须政策2及风格2，不能改变旧分支图校验")
        task = task_binding[1]
        if task.trigger not in {"hidden_mechanism", "synthesis"} or semantic.diagram_type not in {"flow", "stage_flow"}:
            raise ValueError("机制总览须解释隐藏阶段/整体综合，普通步骤列表不准入")
        chain = list(zip([node.node_id for node in semantic.nodes][:-1],
                         [node.node_id for node in semantic.nodes][1:], strict=True))
        if not 3 <= len(semantic.nodes) <= 6 or [(edge.source, edge.target) for edge in semantic.edges] != chain:
            raise ValueError("机制总览只接受冻结的三至六阶段链，不伪造分支/回路")
    if profile.illustration_style_revision == 2:
        from knowledge_product_workflow.illustration_palette import (
            load_category_colors,
            resolve_diagram_palette,
        )
        if semantic.presentation is None:
            raise ValueError("风格2须冻结图级presentation")
        if reference_capacity is None or reference_capacity < 3:
            raise ValueError("风格2须证明可接收首次两参考及纠正三输入")
        resolve_diagram_palette(profile, semantic.presentation, load_category_colors(workspace))
    admission = semantic.admission
    _, contract = require_current_chapter_contract(workspace, chapter_number)
    plan = require_current_content_plan(workspace, chapter_number)
    if plan is None or not any((task_binding is not None or section.asset_decision == "diagram") and
                               section.reader_job == admission.reader_job for section in plan.sections):
        raise ValueError("图reader_job必须来自当前已提交内容计划的diagram小节")
    if not contract.section_outline or not semantic.heading_anchor:
        raise ValueError("插图须绑定当前小节位置，不能只为填版面生图")
    anchors = {f"{number} {title}" for number, title in _flatten_outline(contract.section_outline)}
    if semantic.heading_anchor not in anchors or not any(
        section.section_id == semantic.heading_anchor.split()[0] and section.reader_job == admission.reader_job
        and (task_binding is not None or section.asset_decision == "diagram") for section in plan.sections):
        raise ValueError("插图标题位置与批准小节/读者任务不一致")
    layout = overview_layout() if admission.information_form == "process_overview" else layout_resources().get(admission.family, {})
    if admission.family == "hierarchy" and semantic.direction != "down":
        raise ValueError("stacked-layers-1固定纵向层级；direction须为down，不能发送冲突布局指令")
    if len(semantic.nodes) > layout.get("maximum_nodes", 14) or len({node.group for node in semantic.nodes if node.group}) > layout.get("maximum_regions", 4):
        raise ValueError("节点/分区超出有限图族容量；先拆解教学范围")
    height_pt = admission.planned_width_mm * 72 / 25.4 * 9 / 16
    if height_pt * admission.minimum_glyph_height_ratio < 9:
        raise ValueError("计划图幅下图字不足9pt可读字高；缩小范围或增加图幅，不能靠缩字")
    if admission.information_form == "branching_flow":
        outgoing = [edge.source for edge in semantic.edges]
        incoming = [edge.target for edge in semantic.edges]
        branches = len(outgoing) != len(set(outgoing)) or len(incoming) != len(set(incoming))
        if not branches and not any(edge.target == semantic.nodes[0].node_id for edge in semantic.edges):
            raise ValueError("单纯线性操作默认使用有序列表；流程插图须有分支、并行、汇合或反馈增益")
    if semantic.material_composition is not None:
        from knowledge_product_workflow.d2 import diagram_font_css
        from knowledge_product_workflow.experimental_composition import validate_material_stage_flow
        validate_material_stage_flow(semantic, profile=profile, font_css=diagram_font_css())
    root = workspace.root / "runs" / "illustrations"
    root.mkdir(parents=True, exist_ok=True)
    reference = root / "style-anchor.png"
    spec = style_resources(profile.illustration_style_revision)
    resource = files("knowledge_product_workflow").joinpath("templates", "illustrations", spec.get("anchor_file", "style-anchor.png"))
    reference.write_bytes(resource.read_bytes())
    spec = style_resources(profile.illustration_style_revision)
    visible_spec = {key: semantic.model_dump(mode="json")[key] for key in
                    ("caption", "reader_conclusion", "nodes", "edges", "direction")}
    labels = {node.node_id: node.label.split("\n")[0] for node in semantic.nodes}
    visible_spec["nodes"] = [{"label": node.label, "group": node.group} for node in semantic.nodes]
    visible_spec["edges"] = [{"source_label": labels[edge.source], "target_label": labels[edge.target],
                               "label": edge.label} for edge in semantic.edges]
    visible_spec["only_allowed_arrows"] = [f"{labels[edge.source]} → {labels[edge.target]}"
                                           + (f"（{edge.label}）" if edge.label else "") for edge in semantic.edges]
    prompt = ("生成一张原创中文专业教程插图。参考图片仅用于style，不复制其标签、题材或组件。\n"
              + spec["visual_language"] + "\n"
              + "文字笔画粗细和字形匹配style母版，不用粗黑系统标题；短标题不放大填满横幅。\n"
              + str(layout.get("grammar", "架构图使用母版分组卡片语法；分区数由冻结group决定。")) + "\n"
              + f"固定16:9横幅，目标1672×941，高质量；母版角色字高比例{spec['role_glyph_height_ratios']}。\n"
              + f"当前批准色板{profile.palette.model_dump()}；主品牌强调边线/编号/结论框呼应主色。\n"
              + "只用以下冻结语义；标题无额外横线，所有标签准确，箭头只表达声明关系。\n"
              + "图内只显示caption、nodes的label/group、edges的label及reader_conclusion；不显示JSON字段名、ID、reader_job或后台说明。\n"
              + json.dumps(visible_spec, ensure_ascii=False, indent=2))
    if semantic.material_composition is not None:
        prompt = ("生成原创无文字阶段素材，供既有受控SVG合成，不生成最终教学图。\n"
                  "固定16:9画布，三个至五个阶段的非文字图形横向排列；不画任何标签、数字、箭头、标题或结论。\n"
                  "不要复制参考图片的文字、题材或组件；参考仅用于风格及批准配色。\n"
                  + spec["visual_language"] + "\n"
                  + json.dumps({"stage_meanings": [node.label for node in semantic.nodes],
                                "frozen_relations_for_later_svg": visible_spec["only_allowed_arrows"],
                                "canvas_target": "1672x941; actual capability unknown unless host reports it"}, ensure_ascii=False))
    references = [{"path": str(reference), "role": "style", "sha256": _digest(reference)}]
    if profile.illustration_style_revision == 2:
        from knowledge_product_workflow.d2 import render_svg_with_browser, resolve_diagram_browser
        from knowledge_product_workflow.illustration_palette import (
            load_category_colors,
            palette_card_svg,
            resolve_diagram_palette,
        )
        colors = load_category_colors(workspace)
        card = root / f"{semantic.diagram_id}.palette.svg"
        card.write_text(palette_card_svg(profile, semantic.presentation, colors), encoding="utf-8")
        browser = resolve_diagram_browser()
        if not browser.available:
            raise ValueError("配色卡需要当前离线浏览器")
        card_png = card.with_suffix(".png")
        render_svg_with_browser(browser.path, card, card.with_suffix(".pdf"), card_png)
        references.append({"path": str(card_png), "role": "palette", "sha256": _digest(card_png)})
        prompt += "\n角色字高指单行中文字黑色笔画的可见高度，不是标称字体字号；按母版可见笔画高度匹配，不能直接用比例乘画布得到的数值当字体字号。结论保留母版conclusion字高；过长时在底部框内自然分成两行，不为塞入一行缩小字。\n第二张是冻结配色卡；各节点/区域必须严格采用如下颜色，允许低强度质感但不自行换色。\n" + json.dumps({"presentation":semantic.presentation.model_dump(mode="json"),"colors":resolve_diagram_palette(profile,semantic.presentation,colors)},ensure_ascii=False)
    edit_target = None
    proof = read_asset_style(workspace, semantic.diagram_id)
    pending_material = None
    if semantic.material_composition is not None and proof is None:
        from knowledge_product_workflow.illustration_budget import load_image_budget
        budget = load_image_budget(workspace)
        completed = [call for slot in budget.slots if slot.task_id == semantic.task_id
                     for call in slot.calls if call.state == "completed" and call.image_path]
        for call in reversed(completed):
            candidate_proof = read_asset_style(workspace, semantic.diagram_id, call.call_id)
            if candidate_proof is not None:
                proof = candidate_proof
                pending_material = workspace.root / call.image_path
                break
    failed = proof is not None and (proof["numeric_blockers"] or any(
        item["assessment"] == "failed" for item in proof["input"]["observations"].values()))
    if failed and not repair_reason:
        raise ValueError("当前候选明确失败；在原槽提供repair_reason纠正，不能复用为合格缓存")
    if repair_reason:
        if not failed or len(repair_reason.strip()) < 8:
            raise ValueError("纠正调用只用于当前明确失败的资产；不能为轻微审美差异重复生图")
        if semantic.material_composition is not None:
            material_failures = {key for key, value in proof["input"]["observations"].items()
                                 if value["assessment"] == "failed"}
            if not material_failures & {"palette", "geometry", "decorations", "reference_leakage", "density"}:
                raise ValueError("混合文字／关系／字体为确定性层；先本地修复，不消费AI纠正")
            if pending_material is None:
                manifest = _manifest(workspace, semantic.diagram_id)
                pending_material = workspace.root / manifest.original_image_path
        edit_target = pending_material or workspace.root / "assets" / "figures" / f"{semantic.diagram_id}.png"
        references.append({"path": str(edit_target), "role": "edit_target", "sha256": _digest(edit_target)})
        prompt = ("修正候选图。参考输入以references有序角色清单为真源；只纠正edit_target的明确缺陷。\n"
                  + repair_reason + f"\n编辑目标hash：{_digest(edit_target)}。\n" + prompt)
    elif failed:
        raise ValueError("当前缓存图有明确失败；填写repair-reason在原槽位纠正，不能复用失败图")
    prompt_root = root / "prompts"
    prompt_root.mkdir(exist_ok=True)
    prompt_path = prompt_root / f"{sha256(prompt.encode()).hexdigest()}.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    key = image_cache_key(workspace, semantic, actual_model,
                          references=references if profile.illustration_style_revision == 2 else None)
    call, hit = reserve_image(workspace, diagram_id=semantic.diagram_id, chapter_number=chapter_number,
                              semantic_sha256=semantic_digest(semantic), cache_key=key,
                              prompt_sha256=_digest(prompt_path), model=actual_model, repair=bool(repair_reason),
                              input_references=references if profile.illustration_style_revision == 2 else None,
                              task_id=semantic.task_id)
    return {"call_id": call.call_id, "cache_hit": hit, "prompt": str(prompt_path),
            "reference": str(reference), "reference_role": "style", "model": actual_model,
            "parameter_capability": "宿主未暴露size/quality时此值仅是提示词目标；记录实际像素",
            "image": call.image_path, "edit_target": str(edit_target) if edit_target else None,
            "admission": admission.model_dump(), "references": references,
            "cache_kind": "approved_asset" if hit and proof and not failed and not proof["numeric_reviews"] and not any(item["assessment"]=="uncertain" for item in proof["input"]["observations"].values()) else "call_result" if hit else "new_call"}


def asset_check_path(workspace: Workspace, diagram_id: str, candidate_call_id=None) -> Path:
    if candidate_call_id:
        from knowledge_product_workflow.illustration_updates import candidate_root
        return candidate_root(workspace,candidate_call_id) / "style-check.json"
    return workspace.root / "runs" / "illustrations" / "asset-checks" / f"{diagram_id}.json"


def _manifest(workspace: Workspace, diagram_id: str, candidate_call_id=None):
    from knowledge_product_workflow.diagrams import DiagramManifest
    if candidate_call_id:
        from knowledge_product_workflow.illustration_updates import candidate_root
        return DiagramManifest.model_validate_json((candidate_root(workspace,candidate_call_id) / f"{diagram_id}.json").read_text(encoding="utf-8"))
    return DiagramManifest.model_validate_json((workspace.root / "runs" / "diagrams" / f"{diagram_id}.json").read_text(encoding="utf-8"))


def _asset_family(workspace: Workspace, diagram_id: str) -> str | None:
    path = workspace.root / "runs" / "diagrams" / f"{diagram_id}.input.json"
    if not path.is_file():
        return None
    return (json.loads(path.read_text(encoding="utf-8")).get("admission") or {}).get("family")


def review_semantic(workspace, diagram_id, candidate_call_id=None):
    from knowledge_product_workflow.diagrams import DiagramInput
    if candidate_call_id:
        from knowledge_product_workflow.illustration_updates import candidate_root
        path = candidate_root(workspace,candidate_call_id) / "input.json"
    else:
        path = workspace.root / "runs/diagrams" / f"{diagram_id}.input.json"
    return DiagramInput.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None


def review_binding(workspace, diagram_id, candidate_call_id=None):
    semantic = review_semantic(workspace, diagram_id, candidate_call_id)
    binding = style_binding(workspace, semantic.admission.family if semantic and semantic.admission else None,
                            semantic=semantic, diagram_id=None if semantic else diagram_id)
    manifest = _manifest(workspace, diagram_id, candidate_call_id)
    if manifest.renderer == "host-image+svg":
        from knowledge_product_workflow.experimental_composition import (
            require_current_material_composition,
        )
        binding = sha256((binding + require_current_material_composition(workspace, manifest, semantic)).encode()).hexdigest()
    return binding


def review_image(workspace, diagram_id, candidate_call_id=None):
    if candidate_call_id:
        from knowledge_product_workflow.illustration_updates import candidate_root
        return candidate_root(workspace,candidate_call_id) / f"{diagram_id}.png"
    return workspace.root / "assets/figures" / f"{diagram_id}.png"


def style_review_fingerprint(workspace: Workspace, diagram_id: str, candidate_call_id=None) -> str:
    manifest = _manifest(workspace,diagram_id,candidate_call_id)
    return sha256((manifest.png_sha256 + str(manifest.semantic_input_sha256) + review_binding(workspace,diagram_id,candidate_call_id)).encode()).hexdigest()


def _iter_visible_png_rgb_rows(path: Path):
    """Strictly decode without rewriting the asset; measure RGBA as visible on white paper."""
    from knowledge_product_workflow.pdf_preview import iter_png_rgb_rows

    width, _ = png_dimensions(path, enforce_host=False)
    for row in iter_png_rgb_rows(path, include_alpha=True):
        if len(row) == width * 3:
            yield row
            continue
        visible = bytearray(width * 3)
        for x in range(width):
            alpha = row[x * 4 + 3]
            for channel in range(3):
                visible[x * 3 + channel] = (
                    row[x * 4 + channel] * alpha + 255 * (255 - alpha) + 127
                ) // 255
        yield visible


def create_style_review_template(workspace: Workspace, diagram_id: str, candidate_call_id=None) -> dict:
    manifest = _manifest(workspace, diagram_id, candidate_call_id)
    semantic = review_semantic(workspace, diagram_id, candidate_call_id)
    spec = style_resources(current_profile(workspace).illustration_style_revision)
    host_canvas = manifest.renderer in {"host-image", "host-image+svg"}
    admission = semantic.admission if semantic is not None else None
    measurement_policy = {
        "renderer": manifest.renderer,
        "region_box": "[x, y, width, height]，单位像素；非右下角坐标，须完整位于当前图片内",
        "png_measurement": {
            "encodings": ["8-bit RGB", "8-bit RGBA"], "read_only": True,
            "rgba_background_rgb": [255, 255, 255],
            "rgba_channel_formula": "(channel * alpha + 255 * (255 - alpha) + 127) // 255",
            "validation": "严格CRC、完整块/像素、压缩与滤波校验；不支持隔行或其它编码",
        },
        "host_canvas_gate": {
            "applicable": host_canvas,
            "rule": "仅host-image/host-image+svg须宽1200至8192、高600至8192，宽高比与16:9相差不超过0.06",
        },
        "role_ratio_gate": {
            "applicable": not (spec["revision"] == 2 and not host_canvas),
            "review_deviation": spec["review_deviation"], "block_deviation": spec["block_deviation"],
            "rule": "风格2非宿主vector不走母版角色比例偏差门；visual_estimate标量是人工估测，不冒机器量测",
        },
        "planned_legibility_gate": {
            "applicable": spec["revision"] == 2 and admission is not None,
            "minimum_glyph_pt": 9, "planned_width_mm": admission.planned_width_mm if admission else None,
            "rule": "风格2有admission时，可靠单行CJK暗像素字高按计划宽度/实际PNG宽度折算，低于9pt阻断；vector同样适用",
        },
        "text_observation": "必须实际观察图中最小文字；量测区域只框单行字形，排除图标/边框，不能只测大标题代替最小文字",
        "unreliable_measurements": "无暗像素或非CJK文字、风格2多行/分离文字、混合/纹理颜色区域仍待决；不能可靠判断的观察保留uncertain，不自动passed",
    }
    layout = overview_layout() if semantic is not None and semantic.admission is not None and semantic.admission.information_form == "process_overview" else layout_resources().get(_asset_family(workspace, diagram_id))
    root = workspace.root / "runs" / "templates"
    root.mkdir(exist_ok=True)
    path = root / f"{diagram_id}.style-review.template.json"
    template = {"candidate_call_id": candidate_call_id, "diagram_id": diagram_id, "reviewed_by": "", "measurement_source": "unavailable",
                "role_glyph_height_ratios": {}, "observations": {
                    key: {"assessment": "uncertain", "note": ""} for key in sorted(STYLE_CHECKS)}}
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"template": str(path), "image": str(review_image(workspace,diagram_id,candidate_call_id)),
            "review_input": style_review_fingerprint(workspace, diagram_id,candidate_call_id),
            "renderer": manifest.renderer, "style": spec, "layout": layout,
            "measurement_policy": measurement_policy}


def record_asset_style(workspace: Workspace, review: AssetStyleInput) -> Path:
    if set(review.observations) != STYLE_CHECKS:
        raise ValueError(f"风格观察必须逐项覆盖：{', '.join(sorted(STYLE_CHECKS))}")
    manifest = _manifest(workspace, review.diagram_id,review.candidate_call_id)
    image = review_image(workspace,review.diagram_id,review.candidate_call_id)
    if _digest(image) != manifest.png_sha256:
        raise ValueError("图片已变化，不能为旧清单记录风格通过")
    spec = style_resources(current_profile(workspace).illustration_style_revision)
    semantic = review_semantic(workspace,review.diagram_id,review.candidate_call_id)
    numeric_blockers, numeric_reviews = [], []
    measurements = {}
    if review.text_regions:
        width, height = png_dimensions(image, enforce_host=manifest.renderer in {"host-image", "host-image+svg"})
        rows = list(_iter_visible_png_rgb_rows(image))
        for region in review.text_regions:
            x, y, w, h = region.box
            if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
                raise ValueError("文字量测框须位于当前图片内；不修剪或修改图片")
            def dark(pixel):
                return (sum(c * weight for c,weight in zip(pixel,(0.2126,0.7152,0.0722))) < 150
                        if spec["revision"] == 2 else max(pixel) < 150)
            occupied = [row_index for row_index in range(y, y + h)
                        if any(dark(rows[row_index][column * 3:column * 3 + 3])
                               for column in range(x, x + w))]
            if not occupied or region.script != "cjk":
                numeric_reviews.append(region.role)
                continue
            if spec["revision"] == 2 and any(b-a > 3 for a,b in pairwise(occupied)):
                numeric_reviews.append(region.role)
                measurements[region.role] = {"reliable":False,"reason":"多行/分离区域不能走单行字高硬门"}
                continue
            ratio = (max(occupied) - min(occupied) + 1) / height
            measurements[region.role] = {"glyph_height_ratio": ratio, "box": region.box,
                                         "method": "dark-pixel-bounds-in-reviewer-annotated-region"}
            if spec["revision"] == 2 and semantic is not None and semantic.admission is not None:
                glyph_pt = (max(occupied)-min(occupied)+1) * semantic.admission.planned_width_mm * 72 / 25.4 / width
                measurements[region.role]["planned_glyph_pt"] = glyph_pt
                if glyph_pt < 9:
                    numeric_blockers.append(f"{region.role}:planned-legibility")
            deviation = abs(ratio / spec["role_glyph_height_ratios"][region.role] - 1)
            if deviation >= spec["block_deviation"] and not (spec["revision"] == 2 and manifest.renderer not in {"host-image", "host-image+svg"}):
                numeric_blockers.append(region.role)
            elif deviation >= spec["review_deviation"] and not (spec["revision"] == 2 and manifest.renderer not in {"host-image", "host-image+svg"}):
                numeric_reviews.append(region.role)
    for role, ratio in review.role_glyph_height_ratios.items():
        if role not in spec["role_glyph_height_ratios"] or not 0 < ratio < 1:
            raise ValueError("字高角色或数值无效；只填写当前图需要的批准角色")
        deviation = abs(ratio / spec["role_glyph_height_ratios"][role] - 1)
        if not (spec["revision"] == 2 and manifest.renderer not in {"host-image", "host-image+svg"}) and deviation >= spec["block_deviation"] and review.measurement_source in {"vector", "pixel_measurement"}:
            numeric_blockers.append(role)
        elif not (spec["revision"] == 2 and manifest.renderer not in {"host-image", "host-image+svg"}) and deviation >= spec["review_deviation"]:
            numeric_reviews.append(role)
    color_measurements = {}
    if review.color_regions:
        from statistics import median

        from knowledge_product_workflow.illustration_palette import (
            load_category_colors,
            resolve_diagram_palette,
        )
        semantic = review_semantic(workspace,review.diagram_id,review.candidate_call_id)
        if semantic is None or semantic.presentation is None:
            raise ValueError("颜色量测须绑定风格2的展示输入")
        resolved = resolve_diagram_palette(current_profile(workspace),semantic.presentation,load_category_colors(workspace))
        width,height = png_dimensions(image,enforce_host=manifest.renderer in {"host-image", "host-image+svg"})
        rows = list(_iter_visible_png_rgb_rows(image))
        for region in review.color_regions:
            key = f"color:{region.reference_kind}:{region.object_id}"
            references = semantic.presentation.node_colors if region.reference_kind == "node" else semantic.presentation.group_colors
            if region.object_id not in references:
                raise ValueError("颜色区域引用不存在")
            x,y,w,h=region.box
            if min(x,y)<0 or min(w,h)<=0 or x+w>width or y+h>height:
                raise ValueError("颜色量测框须位于当前图片内")
            pixels=[tuple(row[j*3:j*3+3]) for row in rows[y:y+h] for j in range(x,x+w)]
            if region.kind == "mixed" or any(max(p[c] for p in pixels)-min(p[c] for p in pixels)>30 for c in range(3)):
                numeric_reviews.append(key)
                color_measurements[key]={"reliable":False,"reason":"混合/文字/纹理区域不能走硬门"}
                continue
            actual=[int(median(p[c] for p in pixels)) for c in range(3)]
            expected=resolved[references[region.object_id]]
            rgb=[int(expected[j:j+2],16) for j in (0,2,4)]
            deviation=max(abs(a-b) for a,b in zip(actual,rgb))
            color_measurements[key]={"reliable":True,"median_rgb":actual,"expected":expected,"deviation":deviation}
            if deviation>30:
                numeric_blockers.append(key)
            elif deviation>18:
                numeric_reviews.append(key)
    proof = {"schema_version": 1, "created_at": datetime.now(UTC).isoformat(),
             "measurement_revision": 2 if spec["revision"] == 2 else 1,
             "input": review.model_dump(mode="json"), "style_binding": review_binding(workspace,review.diagram_id,review.candidate_call_id),
             "png_sha256": manifest.png_sha256, "semantic_sha256": manifest.semantic_input_sha256,
             "numeric_blockers": numeric_blockers, "numeric_reviews": numeric_reviews,
             "pixel_measurements": measurements, "color_measurements": color_measurements,
             "rule_set": spec["rule_set"]}
    path = asset_check_path(workspace, review.diagram_id,review.candidate_call_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    workspace.append_event("illustration_style_checked", {"diagram_id": review.diagram_id,"candidate_call_id": review.candidate_call_id,
                                                           "sha256": _digest(path)})
    return path


def read_asset_style(workspace: Workspace, diagram_id: str, candidate_call_id=None) -> dict | None:
    try:
        path = asset_check_path(workspace, diagram_id,candidate_call_id)
        proof = json.loads(path.read_text(encoding="utf-8"))
        manifest = _manifest(workspace, diagram_id,candidate_call_id)
        events = [json.loads(line) for line in (workspace.root / "runs" / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        seals = [event for event in events if event.get("event_type") == "illustration_style_checked"
                 and event["payload"]["diagram_id"] == diagram_id and event["payload"].get("candidate_call_id")==candidate_call_id]
        if (not seals or seals[-1]["payload"]["sha256"] != _digest(path)
                or proof["style_binding"] != review_binding(workspace,diagram_id,candidate_call_id)
                or proof["png_sha256"] != manifest.png_sha256
                or _digest(review_image(workspace,diagram_id,candidate_call_id)) != manifest.png_sha256
                or proof["semantic_sha256"] != manifest.semantic_input_sha256
                or proof["rule_set"] != style_resources(current_profile(workspace).illustration_style_revision)["rule_set"]):
            return None
        if current_profile(workspace).illustration_style_revision == 2 and proof.get("measurement_revision") != 2:
            return None
        AssetStyleInput.model_validate(proof["input"])
        return proof
    except (OSError, ValueError, KeyError, TypeError):
        return None


def validate_style_assets(workspace: Workspace, body: str, *, final: bool = False) -> list[QualityFinding]:
    try:
        profile = current_profile(workspace)
        if profile.illustration_style_revision == 0:
            return []
    except (OSError, ValueError):
        return []
    findings = []
    diagram_ids = sorted(set(re.findall(r"assets/figures/([a-z0-9-]+)\.(?:pdf|png)", body)))
    schemes = []
    for diagram_id in diagram_ids:
        semantic = review_semantic(workspace,diagram_id)
        if semantic is not None and semantic.presentation is not None:
            schemes.append(semantic.presentation.scheme)
        proof = read_asset_style(workspace, diagram_id)
        if proof is None:
            findings.append(QualityFinding(code="ILLUSTRATION_STYLE_PROOF_REQUIRED", severity=Severity.BLOCKER,
                                           location=f"图:{diagram_id}", message="资产风格证明缺失/过期；运行record-illustration-style-check后再提交"))
            continue
        review = AssetStyleInput.model_validate(proof["input"])
        failures = [key for key, observation in review.observations.items() if observation.assessment == "failed"]
        if proof["numeric_blockers"] or failures:
            semantic = any(key in {"labels", "relations", "reference_leakage"} for key in failures)
            findings.append(QualityFinding(code="ILLUSTRATION_CONTENT_FAILED" if semantic else "ILLUSTRATION_STYLE_FAILED",
                                           severity=Severity.BLOCKER, location=f"图:{diagram_id}",
                                           message=f"图资产有明确失败：{failures or proof['numeric_blockers']}；按内容/展示类型受控修复"))
        pending = proof["numeric_reviews"] or any(item.assessment == "uncertain" for item in review.observations.values())
        if pending:
            findings.append(QualityFinding(code="ILLUSTRATION_STYLE_MANUAL_REQUIRED", severity=Severity.WARNING,
                                           location=f"图:{diagram_id}", message="主观/量测待决项须在最终人工成页门绑定解决；此处允许取得页面证据，非风格通过"))
    if profile.illustration_style_revision == 2 and profile.illustration_palette_policy != "locked" and len(schemes) >= 2 and len(set(schemes)) == 1:
        findings.append(QualityFinding(code="ILLUSTRATION_PALETTE_REPETITION_REVIEW",severity=Severity.INFO,
            location="跨图配色",message="多图使用同一scheme；结合教学任务审阅阅读节奏，不自动失败或追加生图。"))
    return findings


def page_style_digest(workspace: Workspace, body: str) -> str:
    paths = [asset_check_path(workspace, item) for item in sorted(set(re.findall(r"assets/figures/([a-z0-9-]+)\.(?:pdf|png)", body)))]
    return sha256("\n".join(_digest(path) if path.is_file() else "missing" for path in paths).encode()).hexdigest()
