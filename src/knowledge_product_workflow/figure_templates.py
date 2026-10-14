"""Finite, text-exact SVG layouts for knowledge-product diagrams."""

from __future__ import annotations

import re
import unicodedata
from html import escape

from knowledge_product_workflow.presentation import PublicationPalette

SVG_LAYOUT_VERSION = "kp-svg-layout-v2"


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1 for character in text)


def _lines(label: str, limit: int) -> list[str]:
    lines: list[str] = []
    current = ""
    # Keep identifiers/Latin words intact. Their fragments can suggest a different ID.
    for token in re.findall(r"[A-Za-z0-9_]+(?:[-./][A-Za-z0-9_]+)*|[\s\S]", label):
        if token == "\n":
            if current:
                lines.append(current)
                current = ""
            continue
        if _width(token) > limit:
            raise ValueError("图内标识符超过节点可用宽度；请扩大节点或将长标识放到正文，不拆分标识符。")
        if _width(current + token) > limit and current:
            lines.append(current)
            current = ""
        current += token
    if current:
        lines.append(current)
    if len(lines) > 3:
        raise ValueError("图内标签超过三行；请拆分节点或将解释移到正文。")
    return lines


def _card(x: int, y: int, width: int, height: int, label: str, palette: PublicationPalette, *, number: int | None, limit: int) -> str:
    lines = _lines(label, limit)
    start = y + height / 2 - (len(lines) - 1) * 18
    text = "".join(
        f'<text x="{x + width / 2}" y="{start + index * 36:.1f}" text-anchor="middle" class="label">{escape(line)}</text>'
        for index, line in enumerate(lines)
    )
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="18" fill="#{palette.paper}" stroke="#{palette.accent}" stroke-width="2"/>'
        + (f'<circle cx="{x + 31}" cy="{y + 30}" r="17" fill="#{palette.accent}"/>'
        f'<text x="{x + 31}" y="{y + 38}" text-anchor="middle" class="index">{number}</text>' if number is not None else "")
        + text
    )


def preflight_structured_svg(spec, palette: PublicationPalette) -> str:
    """Validate finite layout semantics and prepare SVG without external tools or writes."""
    if spec.presentation is not None and any(node.group for node in spec.nodes):
        raise ValueError("固定SVG图型不支持group分区；选择支持分组的D2 flow")
    kind = str(spec.diagram_type.value)
    nodes = spec.nodes
    def node_palette(node):
        if spec.presentation is None:
            return palette
        from knowledge_product_workflow.illustration_palette import COLORS
        token = spec.presentation.node_colors[node.node_id]
        return palette.model_copy(update={"paper": palette.accent_soft if token == "brand" else COLORS[token], "accent": palette.accent if node.node_id in spec.presentation.highlight_nodes else palette.ink})
    canvas_height = 360 if kind in {"comparison", "stage_flow"} else 720
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{canvas_height}" viewBox="0 0 1200 {canvas_height}">',
        '<style>.label{font:600 28px "Microsoft YaHei","Noto Sans CJK SC",sans-serif;fill:#'
        + palette.ink
        + '}.index{font:700 22px sans-serif;fill:#FFFFFF}.hint{font:500 22px "Microsoft YaHei",sans-serif;fill:#'
        + palette.muted
        + '}</style>',
        f'<rect width="1200" height="{canvas_height}" fill="#{palette.paper}"/>',
        f'<rect x="30" y="30" width="1140" height="{canvas_height - 60}" rx="22" fill="#{palette.accent_soft}"/>',
    ]
    if kind == "layered_architecture":
        if not 2 <= len(nodes) <= 4:
            raise ValueError("层级架构图需要 2–4 个节点，过多层级请拆图。")
        height = 110
        gap = min(130, (620 - len(nodes) * height) // max(1, len(nodes) - 1))
        start_y = (720 - (len(nodes) * height + (len(nodes) - 1) * gap)) // 2
        for index, node in enumerate(nodes):
            y = start_y + index * (height + gap)
            parts.append(_card(145, y, 910, height, node.label, node_palette(node), number=None if spec.presentation else index + 1, limit=44))
        indices = {node.node_id: index for index, node in enumerate(nodes)}
        for edge in spec.edges:
            source, target = indices[edge.source], indices[edge.target]
            if abs(source - target) != 1:
                raise ValueError("层级模板只支持相邻层关系；跨层关系请使用 D2 flow。")
            lower = max(source, target)
            top = start_y + (lower - 1) * (height + gap) + height + 7
            bottom = start_y + lower * (height + gap) - 8
            point = bottom if target > source else top
            base = point - 12 if target > source else point + 12
            parts.append(f'<path d="M600 {top} V{bottom}" stroke="#{palette.accent}" stroke-width="4"/>')
            parts.append(f'<path d="M590 {base} L600 {point} L610 {base}" fill="none" stroke="#{palette.accent}" stroke-width="4"/>')
            if edge.label:
                if _width(edge.label) > 26:
                    raise ValueError("层级连线标签过长；请移到正文或改用 D2 flow。")
                parts.append(f'<text x="630" y="{(top + bottom) / 2 + 8}" class="hint">{escape(edge.label)}</text>')
    elif kind == "comparison":
        if len(nodes) != 2:
            raise ValueError("左右对比图恰好需要两个节点；多项对比请使用卡片矩阵。")
        for index, node in enumerate(nodes):
            x = 75 + index * 550
            parts.append(_card(x, 110, 500, 180, node.label, node_palette(node), number=index + 1, limit=26))
        parts.append('<text x="600" y="75" text-anchor="middle" class="hint">对照 · 适用条件与取舍</text>')
        parts.append('<text x="600" y="210" text-anchor="middle" class="hint">VS</text>')
    elif kind == "nested_structure":
        if not 3 <= len(nodes) <= 5:
            raise ValueError("嵌套结构图需要外层和 2–4 个内层节点。")
        parts.append(f'<rect x="90" y="85" width="1020" height="530" rx="20" fill="#{node_palette(nodes[0]).paper}" stroke="#{node_palette(nodes[0]).accent}" stroke-width="3"/>')
        for index, line in enumerate(_lines(nodes[0].label, 40)):
            parts.append(f'<text x="600" y="{150 + index * 36}" text-anchor="middle" class="label">{escape(line)}</text>')
        width = 440
        for index, node in enumerate(nodes[1:]):
            x = 140 + index % 2 * 480
            y = 270 + index // 2 * 160
            parts.append(_card(x, y, width, 130, node.label, node_palette(node), number=index + 2, limit=23))
    elif kind == "card_matrix":
        if not 3 <= len(nodes) <= 6:
            raise ValueError("卡片矩阵需要 3–6 个节点。")
        columns = 2 if len(nodes) == 4 else 3
        width = 495 if columns == 2 else 330
        gap = 45 if columns == 2 else 30
        for index, node in enumerate(nodes):
            x = 75 + index % columns * (width + gap)
            y = 135 + index // columns * 255
            parts.append(_card(x, y, width, 210, node.label, node_palette(node), number=index + 1, limit=26 if columns == 2 else 17))
    elif kind == "stage_flow":
        if not 2 <= len(nodes) <= 5:
            raise ValueError("阶段流程图需要 2–5 个阶段。")
        gap = 18
        width = (1050 - (len(nodes) - 1) * gap) // len(nodes)
        for index, node in enumerate(nodes):
            x = 75 + index * (width + gap)
            parts.append(_card(x, 110, width, 180, node.label, node_palette(node), number=None if spec.presentation else index + 1, limit=max(9, width // 24)))
        indices = {node.node_id: index for index, node in enumerate(nodes)}
        for edge in spec.edges:
            source, target = indices[edge.source], indices[edge.target]
            if target != source + 1:
                raise ValueError("阶段模板只支持按顺序的相邻阶段；分支/回环请使用 D2 flow。")
            x = 75 + source * (width + gap) + width
            parts.append(f'<path d="M{x + 2} 200 H{x + gap - 4} M{x + gap - 10} 194 L{x + gap - 3} 200 L{x + gap - 10} 206" fill="none" stroke="#{palette.accent}" stroke-width="3"/>')
            if edge.label:
                raise ValueError("阶段模板的关系标签请移入阶段节点或图后结论，避免狭窄间隙小字。")
    else:
        raise ValueError(f"没有受控 SVG 图型：{kind}")
    parts.append("</svg>")
    if spec.presentation is not None:
        parts = [part.replace('"Microsoft YaHei","Noto Sans CJK SC",sans-serif', '"Inter","PingFang SC",sans-serif').replace('"Microsoft YaHei",sans-serif', '"Inter","PingFang SC",sans-serif') for part in parts]
        parts = [part.replace('.hint{font:500 22px', '.hint{font:500 26px') for part in parts]
    return "\n".join(parts) + "\n"


def render_structured_svg(spec, palette: PublicationPalette) -> str:
    """Lay out approved labels as SVG text; no model-generated glyphs or scripts."""
    return preflight_structured_svg(spec, palette)
