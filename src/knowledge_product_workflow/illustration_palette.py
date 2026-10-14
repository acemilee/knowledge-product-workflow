"""有限图级配色与书稿内概念记忆；不调用模型。"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from knowledge_product_workflow.presentation import PublicationProfile, _contrast_ratio

ColorToken = Literal["brand", "peach", "sand", "rose", "sage", "blue", "lavender", "stone"]
SchemeId = Literal["warm-focus", "balanced", "cool-structure"]
_PALETTES = json.loads(files("knowledge_product_workflow").joinpath("templates", "illustrations", "palettes-v2.json").read_text(encoding="utf-8"))
COLORS = _PALETTES["colors"]
SCHEMES = {name: set(tokens) for name,tokens in _PALETTES["schemes"].items()}


class DiagramPresentationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheme: SchemeId
    node_colors: dict[str, ColorToken] = Field(description="node_id -> 批准颜色token，须覆盖全部节点")
    group_colors: dict[str, ColorToken] = Field(description="图中group名称 -> 批准颜色token，须覆盖全部区域")
    concept_nodes: dict[str, str] = Field(description="node_id -> 标准概念名；不是反向映射")
    concept_groups: dict[str, str] = Field(default_factory=dict, description="group名称 -> 标准概念名")
    highlight_nodes: list[str] = Field(default_factory=list, max_length=1)


def concept_assignments(presentation: DiagramPresentationInput) -> dict[str, ColorToken]:
    assignments = {}
    for references, colors in ((presentation.concept_nodes, presentation.node_colors),
                               (presentation.concept_groups, presentation.group_colors)):
        for reference, concept in references.items():
            if reference not in colors:
                raise ValueError("概念引用须有颜色映射")
            concept = " ".join(concept.split())
            if not concept:
                raise ValueError("标准概念名不能为空")
            if concept in assignments and assignments[concept] != colors[reference]:
                raise ValueError("概念颜色冲突：同一图内节点和区域须一致")
            assignments[concept] = colors[reference]
    return assignments


def validate_graph_presentation(semantic) -> None:
    p = semantic.presentation
    if p is None:
        return
    nodes = {n.node_id for n in semantic.nodes}
    groups = {n.group for n in semantic.nodes if n.group}
    if set(p.node_colors) != nodes or set(p.group_colors) != groups:
        raise ValueError("展示颜色须准确覆盖当前node/group，不能漏项或引用不存在的对象")
    if not set(p.concept_nodes) <= nodes or not set(p.concept_groups) <= groups:
        raise ValueError("概念引用的node/group不存在")
    if not set(p.highlight_nodes) <= nodes:
        raise ValueError("强调节点不存在")
    concept_assignments(p)


def default_vector_presentation(workspace, profile: PublicationProfile, semantic) -> DiagramPresentationInput:
    """Materialize finite defaults once; explicit host choices are never repaired."""
    known = load_category_colors(workspace)
    def concept(label):
        first = label.splitlines()[0].strip()
        return first if re.match(r"https?://", first) else re.split(r"[:：]", first, maxsplit=1)[0].strip()
    concepts = {node.node_id: concept(node.label) for node in semantic.nodes}
    groups = {node.group: concept(node.group) for node in semantic.nodes if node.group}
    scheme = profile.illustration_palette_scheme
    if profile.illustration_palette_policy != "locked":
        scheme = ("warm-focus" if semantic.diagram_type in {"stage_flow", "lifecycle"}
                  else "cool-structure" if semantic.diagram_type in {"layered_architecture", "nested_structure", "sequence"}
                  else "balanced")
        if any(known[c] not in SCHEMES[scheme] | {"brand"} for c in [*concepts.values(), *groups.values()] if c in known):
            scheme = "balanced"
    pools = {"warm-focus": ["peach", "sand", "rose", "stone"], "cool-structure": ["blue", "lavender", "stone"],
             "balanced": ["peach", "sage", "blue", "stone"]}
    assigned = {}
    for index, name in enumerate(dict.fromkeys([*concepts.values(), *groups.values()])):
        if name in known:
            assigned[name] = known[name]
        else:
            used = {token for token in assigned.values() if token != "brand"}
            pool = sorted(used) if len(used) >= 4 else pools[scheme]
            assigned[name] = "brand" if index == 0 else pool[int(sha256(name.encode()).hexdigest()[:8], 16) % len(pool)]
    presentation = DiagramPresentationInput(scheme=scheme,
        node_colors={key: assigned[value] for key, value in concepts.items()},
        group_colors={key: assigned[value] for key, value in groups.items()},
        concept_nodes=concepts, concept_groups=groups,
        highlight_nodes=[node.node_id for node in semantic.nodes if assigned[concepts[node.node_id]] == "brand"][:1])
    resolve_diagram_palette(profile, presentation, known)
    return presentation


def resolve_diagram_palette(profile: PublicationProfile, presentation: DiagramPresentationInput,
                            category_colors: dict[str, ColorToken]) -> dict[str, str]:
    if profile.illustration_palette_policy == "locked" and presentation.scheme != profile.illustration_palette_scheme:
        raise ValueError("用户锁定色板与本图scheme冲突；先走出版预览确认")
    requested = set(presentation.node_colors.values()) | set(presentation.group_colors.values())
    if len(requested - {"brand"}) > 4:
        raise ValueError("单图最多四种辅助色")
    if not requested <= SCHEMES[presentation.scheme] | {"brand"}:
        raise ValueError("颜色与scheme不兼容；可选balanced或受控修改概念登记")
    for concept, token in concept_assignments(presentation).items():
        if concept in category_colors and category_colors[concept] != token:
            raise ValueError(f"概念颜色冲突：{concept}")
    resolved = {key: (profile.palette.accent_soft if key == "brand" else COLORS[key])
                for key in sorted(requested)}
    for fill in resolved.values():
        if _contrast_ratio(profile.palette.ink, fill) < 4.5:
            raise ValueError("图内正文与所选辅助底色对比度不足")
        if _contrast_ratio(profile.palette.accent, fill) < 3:
            raise ValueError("图内品牌轮廓与所选底色对比度不足；先选择批准的适配色")
    return resolved


def diagram_presentation_digest(profile: PublicationProfile,
                                presentation: DiagramPresentationInput,
                                category_colors: dict[str, ColorToken]) -> str:
    resolved = resolve_diagram_palette(profile, presentation, category_colors)
    values = {"presentation": presentation.model_dump(mode="json"), "colors": resolved,
              "ink": profile.palette.ink, "accent": profile.palette.accent,
              "concepts": concept_assignments(presentation)}
    return sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load_category_colors(workspace) -> dict[str, ColorToken]:
    path = workspace.root / "planning" / "illustration-plan.json"
    from knowledge_product_workflow.illustrations import current_profile
    profile_path = workspace.root / "planning/publication-profile.yaml"
    overrides = current_profile(workspace).illustration_category_colors if profile_path.is_file() else {}
    if not path.is_file():
        return dict(overrides)
    data = json.loads(path.read_text(encoding="utf-8"))
    seals = [json.loads(line) for line in (workspace.root / "runs/events.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    seals = [e for e in seals if e.get("event_type") == "illustration_colors_registered"]
    if not seals or seals[-1]["payload"]["sha256"] != sha256(path.read_bytes()).hexdigest():
        raise ValueError("概念颜色登记封印不匹配；不能手改登记")
    return data["category_colors"] | overrides


def register_category_colors(workspace, profile, presentation) -> None:
    colors = load_category_colors(workspace)
    resolve_diagram_palette(profile, presentation, colors)
    updated = colors | concept_assignments(presentation)
    if updated == colors and (workspace.root / "planning/illustration-plan.json").is_file():
        return
    path = workspace.root / "planning/illustration-plan.json"
    data = {"schema_version": 1, "category_colors": updated}
    from knowledge_product_workflow.presentation import _atomic_write
    old = path.read_bytes() if path.is_file() else None
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    try:
        workspace.append_event("illustration_colors_registered",
                               {"sha256": sha256(path.read_bytes()).hexdigest()})
    except OSError:
        if old is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(old)
        raise


def palette_card_svg(profile, presentation, colors) -> str:
    """仅颜色角色的确定性色板卡，不包含教学图形。"""
    from html import escape
    resolved = resolve_diagram_palette(profile, presentation, colors)
    entries = [("brand-accent (outline)", profile.palette.accent), *(("brand-surface" if key == "brand" else key, value) for key,value in resolved.items())]
    width = 1200
    cell = width / len(entries)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 360">',
             '<rect width="1200" height="360" fill="white"/>']
    for i, (name, color) in enumerate(entries):
        x = i * cell
        parts.extend([f'<rect x="{x + 12}" y="30" width="{cell - 24}" height="220" fill="#{color}"/>',
                      f'<text x="{x + cell / 2}" y="280" text-anchor="middle" font-family="Arial" font-size="18">{escape(name)}</text><text x="{x + cell / 2}" y="315" text-anchor="middle" font-family="Arial" font-size="20">#{color}</text>'])
    return "\n".join(parts) + "\n</svg>"
