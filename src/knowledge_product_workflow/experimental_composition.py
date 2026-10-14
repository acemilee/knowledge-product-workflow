"""最小混合方案探针：素材不改字节，文字/关系确定性；尚非生产后端。"""
from __future__ import annotations

import base64
import hashlib
import struct
from html import escape
from pathlib import Path

MATERIAL_COMPOSITION_VERSION = "kp-material-stage-flow-v1"


def require_material_source_evidence(workspace, call, semantic=None) -> None:
    """Bind the supplied original receipt; presence never proves AI provenance."""
    if call.state != "completed" or call.backend != "host-image":
        raise ValueError("混合素材必须来自当前completed的host-image调用")
    original = (workspace.root / (call.image_path or "")).resolve()
    if (not original.is_relative_to(workspace.root.resolve()) or not original.is_file()
            or hashlib.sha256(original.read_bytes()).hexdigest() != call.image_sha256):
        raise ValueError("completed调用的PNG原件不存在、已变化或越出工作区，不能只提交副本")
    if semantic is not None:
        from knowledge_product_workflow.illustration_budget import (
            _assert_current_plan,
            load_image_budget,
        )
        budget = load_image_budget(workspace)
        _assert_current_plan(workspace, budget)
        slot = next((slot for slot in budget.slots if any(item.call_id == call.call_id for item in slot.calls)), None)
        if (budget.illustration_policy_revision != 2 or slot is None
                or slot.task_id != semantic.task_id or budget.approved_tasks.get(slot.task_id) != slot.chapter_number):
            raise ValueError("混合素材必须绑定当前已批准原图任务及其C+R消费，不能借新task重置来源")
    if call.material_origin == "synthetic":
        return
    if call.source_evidence_path is None or call.source_evidence_sha256 is None:
        raise ValueError("混合素材缺少原工具／会话来源回执；material_origin声明不能证明实际AI来源")
    evidence = (workspace.root / call.source_evidence_path).resolve()
    if (not evidence.is_relative_to(workspace.root.resolve()) or not evidence.is_file()
            or hashlib.sha256(evidence.read_bytes()).hexdigest() != call.source_evidence_sha256):
        raise ValueError("原工具／会话来源回执已变化或越出工作区")


def require_current_material_composition(workspace, manifest, semantic) -> str:
    """Recheck source call, approved task, semantic/color/font and compositor input."""
    from knowledge_product_workflow.d2 import diagram_font_css
    from knowledge_product_workflow.illustration_budget import image_call_for_asset
    from knowledge_product_workflow.illustration_palette import load_category_colors
    from knowledge_product_workflow.illustration_tasks import require_diagram_task
    from knowledge_product_workflow.illustrations import current_profile, image_cache_key
    from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract

    if (manifest.renderer != "host-image+svg" or manifest.renderer_version != MATERIAL_COMPOSITION_VERSION
            or semantic.material_composition != "stage_flow" or not manifest.original_image_path
            or not manifest.image_call_id or manifest.composition_evidence is None):
        raise ValueError("混合候选缺少当前合成版本／原件来源绑定")
    original = (workspace.root / manifest.original_image_path).resolve()
    if not original.is_relative_to(workspace.root.resolve()) or not original.is_file():
        raise ValueError("混合原PNG已丢失或越出当前工作区")
    call = image_call_for_asset(workspace, manifest.image_call_id, manifest.diagram_id, original)
    require_material_source_evidence(workspace, call, semantic)
    if (call.image_sha256 != manifest.original_image_sha256 or call.image_path != manifest.original_image_path
            or call.material_origin != manifest.material_origin
            or call.cache_key != image_cache_key(workspace, semantic, call.model, references=call.input_references)):
        raise ValueError("混合素材来源／图义／批准风格已过期")
    require_diagram_task(workspace, manifest.chapter_number, semantic)
    contract, _ = require_current_chapter_contract(workspace, manifest.chapter_number)
    if hashlib.sha256(contract.read_bytes()).hexdigest() != manifest.contract_sha256:
        raise ValueError("混合候选章节合约已过期")
    digest = composition_input_digest(original, semantic, current_profile(workspace),
                                      load_category_colors(workspace), diagram_font_css())
    if digest != manifest.composition_evidence["input_sha256"]:
        raise ValueError("混合候选的字段／颜色／字体／合成版本已过期，先复用原素材受控重建候选")
    return digest


def validate_material_stage_flow(semantic, *, profile=None, font_css=None) -> dict:
    """Only the declared finite chain is admitted; other families keep vector."""
    chain = list(zip([node.node_id for node in semantic.nodes][:-1],
                     [node.node_id for node in semantic.nodes][1:], strict=True))
    if (semantic.backend != "host-image" or semantic.diagram_type != "stage_flow"
            or semantic.direction != "right" or semantic.presentation is None
            or semantic.admission is None or semantic.admission.information_form != "process_overview"
            or not 3 <= len(semantic.nodes) <= 5
            or [(edge.source, edge.target) for edge in semantic.edges] != chain
            or any(edge.label for edge in semantic.edges) or any(node.group for node in semantic.nodes)):
        raise ValueError("混合素材只支持风格2的三至五阶段stage_flow相邻链；分支、分组或其他图族继续既有vector")
    if profile is not None and (profile.illustration_style_revision != 2
                                or profile.font_policy != "preferred" or not font_css):
        raise ValueError("混合候选须批准风格2及实际固定字体CSS；不能默默回退系统字体")
    from knowledge_product_workflow.figure_templates import _lines
    gap, padding = 64, 48
    card_width = (1672 - padding * 2 - gap * (len(semantic.nodes) - 1)) / len(semantic.nodes)
    title_lines = _lines(semantic.caption, 60)
    conclusion_lines = _lines(semantic.reader_conclusion, 72)
    if len(title_lines) > 1 or len(conclusion_lines) > 2:
        raise ValueError("当前stage_flow样品标题／结论超出有限画布；精炼语义或继续vector，不能缩字")
    node_lines = [_lines(node.label, max(8, int((card_width - 56) / 24))) for node in semantic.nodes]
    if any(len(lines) > 2 for lines in node_lines):
        raise ValueError("阶段标签超出两行；不能靠缩字适配混合画布")
    return {"gap": gap, "padding": padding, "card_width": card_width,
            "conclusion_lines": conclusion_lines, "node_lines": node_lines}


def composition_input_digest(material, semantic, profile, category_colors, font_css) -> str:
    """Bind composition, exact PNG, approved colors, concrete fonts and version."""
    import re
    from urllib.request import url2pathname

    from knowledge_product_workflow.illustration_palette import resolve_diagram_palette

    fonts = {uri: hashlib.sha256(Path(url2pathname(uri.removeprefix("file:"))).read_bytes()).hexdigest()
             for uri in re.findall(r'url\("(file:[^"]+)"\)', font_css or "")}
    values = {"version": MATERIAL_COMPOSITION_VERSION,
              "composer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "material_sha256": hashlib.sha256(material.read_bytes()).hexdigest(),
              "semantic": semantic.model_dump(mode="json"),
              "palette": profile.palette.model_dump(), "font_policy": profile.font_policy,
              "colors": resolve_diagram_palette(profile, semantic.presentation, category_colors),
              "font_css": font_css, "fonts": fonts}
    import json
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def compose_stage_flow_material(material, semantic, profile, category_colors, *, font_css) -> tuple[str, dict]:
    """Candidate compositor; it neither dispatches nor infers source provenance."""
    from knowledge_product_workflow.illustration_palette import resolve_diagram_palette
    from knowledge_product_workflow.illustrations import png_dimensions

    layout = validate_material_stage_flow(semantic, profile=profile, font_css=font_css)
    colors = resolve_diagram_palette(profile, semantic.presentation, category_colors)
    width, height = png_dimensions(material)
    raw = material.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    count = len(semantic.nodes)
    gap, padding, card_width = layout["gap"], layout["padding"], layout["card_width"]
    conclusion_lines = layout["conclusion_lines"]
    palette = profile.palette
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1672" height="941" viewBox="0 0 1672 941">',
             f'<rect width="1672" height="941" fill="#{palette.paper}"/>',
             f'<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="#{palette.accent}"/></marker></defs>',
             f'<text x="836" y="92" text-anchor="middle" font-family="Inter, PingFang SC Semibold, PingFang SC" font-weight="600" font-size="50" fill="#{palette.ink}">{escape(semantic.caption)}</text>']
    for index, node in enumerate(semantic.nodes):
        x = padding + index * (card_width + gap)
        fill = colors[semantic.presentation.node_colors[node.node_id]]
        parts.append(f'<rect x="{x:.2f}" y="166" width="{card_width:.2f}" height="550" rx="18" fill="#{fill}" stroke="#{palette.accent}" stroke-width="3"/>')
        parts.append(f'<svg x="{x + 20:.2f}" y="232" width="{card_width - 40:.2f}" height="290" viewBox="{index * width / count:.2f} 0 {width / count:.2f} {height}"><image width="{width}" height="{height}" href="data:image/png;base64,{encoded}"/></svg>')
        labels = layout["node_lines"][index]
        for line_number, label in enumerate(labels):
            parts.append(f'<text x="{x + card_width / 2:.2f}" y="{624 + line_number * 56}" text-anchor="middle" font-family="Inter, PingFang SC Semibold, PingFang SC" font-weight="600" font-size="48" fill="#{palette.ink}">{escape(label)}</text>')
        if index < count - 1:
            parts.append(f'<path d="M{x + card_width + 5:.2f} 442 H{x + card_width + gap - 12:.2f}" stroke="#{palette.accent}" stroke-width="3" fill="none" marker-end="url(#arrow)"/>')
    parts.append(f'<rect x="48" y="770" width="1576" height="132" rx="14" fill="#{palette.accent_soft}"/>')
    for index, line in enumerate(conclusion_lines):
        parts.append(f'<text x="836" y="{827 + index * 50}" text-anchor="middle" font-family="Inter, PingFang SC" font-size="40" fill="#{palette.ink}">{escape(line)}</text>')
    parts.append("</svg>")
    evidence = {"version": MATERIAL_COMPOSITION_VERSION,
                "input_sha256": composition_input_digest(material, semantic, profile, category_colors, font_css),
                "source_png_sha256": hashlib.sha256(raw).hexdigest(),
                "labels": [node.label for node in semantic.nodes],
                "relations": [[edge.source, edge.target] for edge in semantic.edges],
                "resolved_colors": colors, "presentation": semantic.presentation.model_dump(mode="json"),
                "requested_fonts": ["Inter", "PingFang SC Semibold", "PingFang SC"],
                "material_origin_verification": "declared_only; verify actual tool/session dispatch separately",
                "production_ready": False, "integration": "controlled-candidate; activation requires current human response"}
    return "\n".join(parts) + "\n", evidence


def compose_stage_flow_probe(material: Path, labels: list[str], *, accent: str = "B65E2B") -> tuple[str, dict]:
    """只验证2—6阶段的合成可行性；调用者另行证明AI来源/授权/视觉质量。"""
    if not 2 <= len(labels) <= 6 or any(not text.strip() or len(text) > 14 for text in labels):
        raise ValueError("最小样品只接收2—6个简短阶段名；复杂关系使用已有vector路径")
    if len(accent) != 6 or any(character not in "0123456789abcdefABCDEF" for character in accent):
        raise ValueError("强调色必须来自批准色板的6位HEX")
    raw = material.read_bytes()
    if len(raw) < 33 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("样品素材必须为PNG原件")
    width, height = struct.unpack("!II", raw[16:24])
    if width == 0 or height == 0:
        raise ValueError("素材尺寸无效")
    encoded = base64.b64encode(raw).decode("ascii")
    canvas, gap, padding = 1200, 48, 32
    card = (canvas - padding * 2 - gap * (len(labels)-1)) / len(labels)
    font_size = min(32, (card-24) / max(1, max(len(label) for label in labels)))
    if font_size < 26:
        raise ValueError("标签不能靠缩小文字适配；先精炼名称或改用其他图型")
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 360">',
             '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="#66616A"/></marker></defs>']
    fills = ["F8EBE2", "F3E6D4", "F2E4E9", "E9EDE9"]
    for index, label in enumerate(labels):
        x = padding + index * (card + gap)
        parts.append(f'<rect x="{x:.2f}" y="40" width="{card:.2f}" height="270" rx="18" fill="#{fills[index % len(fills)]}" stroke="#{accent}" stroke-width="2"/>')
        # SVG viewport clipping leaves the original PNG byte-identical. Real
        # material alignment remains a visual question, not an automatic pass.
        parts.append(f'<svg x="{x+card/2-60:.2f}" y="70" width="120" height="120" viewBox="{index*width/len(labels):.2f} 0 {width/len(labels):.2f} {height}"><image width="{width}" height="{height}" href="data:image/png;base64,{encoded}"/></svg>')
        parts.append(f'<text x="{x+card/2:.2f}" y="250" text-anchor="middle" font-family="Inter, PingFang SC Semibold, PingFang SC, sans-serif" font-weight="600" font-size="{font_size:.2f}" fill="#202124">{escape(label)}</text>')
        if index < len(labels)-1:
            parts.append(f'<path d="M{x+card:.2f} 175 H{x+card+gap:.2f}" stroke="#66616A" stroke-width="2" fill="none" marker-end="url(#arrow)"/>')
    parts.append('</svg>')
    evidence = {"renderer": "experimental-svg-material-probe", "source_png_sha256": hashlib.sha256(raw).hexdigest(),
                "requested_fonts": ["Inter", "PingFang SC Semibold"], "labels": labels,
                "ai_origin": "not_inferred; actual dispatch must be verified separately", "production_ready": False,
                "constraints": "does not dispatch AI or reset existing image budgets"}
    return "\n".join(parts), evidence
