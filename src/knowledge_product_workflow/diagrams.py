"""D2-backed, current-version figure assets for reader-visible knowledge products."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from knowledge_product_workflow.d2 import (
    d2_subprocess_environment,
    render_svg_with_browser,
    resolve_d2,
    resolve_diagram_browser,
)
from knowledge_product_workflow.figure_templates import SVG_LAYOUT_VERSION, preflight_structured_svg
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.illustration_palette import (
    DiagramPresentationInput,
    validate_graph_presentation,
)
from knowledge_product_workflow.illustrations import IllustrationAdmission
from knowledge_product_workflow.presentation import (
    PRESENTATION_PROFILE_ID,
    PublicationPalette,
    PublicationProfile,
    publication_profile_path,
)
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from knowledge_product_workflow.workspace import Workspace

from .workspace_scratch import workspace_scratch

_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<target>assets/figures/(?P<id>[a-z0-9][a-z0-9-]*)\.pdf)\)")
_FIGURE_REFERENCE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^)]+)\)"
)
_MARKDOWN_CODE_FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<suffix>.*)$")
_MARKDOWN_INLINE_CODE = re.compile(r"(?<!`)(?P<ticks>`+)(?!`).*?(?<!`)(?P=ticks)(?!`)")
_CAPTION_NUMBER_PREFIX = re.compile(r"^\s*(?:图|figure)\s*\d+\s*[:：.、-]?\s*", re.IGNORECASE)
_PDF_NODE_FONT_SIZE = 28
_PDF_EDGE_FONT_SIZE = 22


class DiagramType(StrEnum):
    FLOW = "flow"
    LAYERED_ARCHITECTURE = "layered_architecture"
    COMPARISON = "comparison"
    SEQUENCE = "sequence"
    LIFECYCLE = "lifecycle"
    NESTED_STRUCTURE = "nested_structure"
    CARD_MATRIX = "card_matrix"
    STAGE_FLOW = "stage_flow"


_STRUCTURED_SVG_TYPES = frozenset({
    DiagramType.LAYERED_ARCHITECTURE, DiagramType.COMPARISON,
    DiagramType.NESTED_STRUCTURE, DiagramType.CARD_MATRIX, DiagramType.STAGE_FLOW,
})


def _preflight_vector_input(semantic, palette, *, template_renderer: bool) -> str | None:
    """Share the submit-time semantic/layout rules with pre-insertion validation."""
    if any(len(node.label.splitlines()) > 3 for node in semantic.nodes):
        raise ValueError("图内标签超过三行；请将解释移到正文。")
    return preflight_structured_svg(semantic, palette) if template_renderer else None


def _concept_color_conflict_context(presentation, registered_colors) -> str:
    """Describe conflicting references without changing the palette acceptance rules."""
    previous = {}
    details = []
    for kind, references, colors in (
        ("node", presentation.concept_nodes, presentation.node_colors),
        ("group", presentation.concept_groups, presentation.group_colors),
    ):
        for reference, name in references.items():
            concept = " ".join(name.split())
            token = colors.get(reference)
            if token is None:
                continue
            current = f"本次{kind}引用 {reference}={token}"
            if concept in registered_colors and registered_colors[concept] != token:
                details.append(f"概念{concept}：已登记/冻结概念引用={registered_colors[concept]}；{current}")
            if concept in previous and previous[concept][0] != token:
                details.append(f"概念{concept}：已有{previous[concept][1]}；{current}")
            previous[concept] = (token, f"{kind}引用 {reference}={token}")
    return "；".join(details)


class DiagramNode(BaseModel):
    """A labelled, stable node in a D2 diagram specification."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    label: str = Field(min_length=1, max_length=180)
    group: str | None = Field(default=None, max_length=80)


class DiagramEdge(BaseModel):
    """One directed connection between declared nodes."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    target: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    label: str | None = Field(default=None, max_length=120)


class DiagramInput(BaseModel):
    """The semantic diagram description a host may submit to KP."""

    model_config = ConfigDict(extra="forbid")

    diagram_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    task_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]*$")
    diagram_type: DiagramType
    direction: str = Field(default="down", pattern=r"^(down|right)$")
    caption: str = Field(min_length=1, max_length=180)
    reader_conclusion: str = Field(min_length=1, max_length=400)
    heading_anchor: str | None = Field(default=None, max_length=180)
    nodes: list[DiagramNode] = Field(min_length=2)
    edges: list[DiagramEdge] = Field(default_factory=list)
    backend: Literal["vector", "host-image"] = "vector"
    admission: IllustrationAdmission | None = None
    presentation: DiagramPresentationInput | None = None
    material_composition: Literal["stage_flow"] | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        value = handler(self)
        if self.task_id is None:
            value.pop("task_id", None)
        if self.material_composition is None:
            value.pop("material_composition", None)
        return value

    @model_validator(mode="after")
    def validate_graph(self) -> DiagramSpec:
        validate_graph_presentation(self)
        if self.material_composition is not None:
            from knowledge_product_workflow.experimental_composition import (
                validate_material_stage_flow,
            )
            validate_material_stage_flow(self)
        if _CAPTION_NUMBER_PREFIX.match(self.caption):
            raise ValueError("图注不得自行包含“图 1”或“Figure 1”编号；出版层会统一添加编号")
        if len(self.nodes) > 14:
            raise ValueError("单张图表最多 14 个节点；请拆分为多张教学图或改用分组图")
        if any(len(node.label.strip()) > 72 for node in self.nodes):
            raise ValueError("图表节点文字不能超过 72 个字符；请将解释移到正文或图后结论")
        if any(edge.label is not None and len(edge.label.strip()) > 56 for edge in self.edges):
            raise ValueError("图表连线文字不能超过 56 个字符；请简化关系标签")
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("图表节点 ID 必须唯一")
        unknown = {
            item
            for edge in self.edges
            for item in (edge.source, edge.target)
            if item not in set(node_ids)
        }
        if unknown:
            raise ValueError(f"图表边引用了不存在的节点：{', '.join(sorted(unknown))}")
        return self


class DiagramSpec(DiagramInput):
    """Internal proof that binds a semantic diagram to one current chapter version."""

    schema_version: int = Field(default=2, ge=1)
    chapter_number: int = Field(gt=0)
    chapter_body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    theme: str = Field(default="knowledge-product-pdf-v4", pattern=r"^knowledge-product-pdf-v[456]$")


class DiagramManifest(BaseModel):
    """Internal version proof for a D2 source and its rendered reader assets."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    diagram_id: str
    task_id: str | None = None
    chapter_number: int
    chapter_body_sha256: str
    contract_sha256: str
    spec_sha256: str
    d2_sha256: str | None = None
    svg_sha256: str
    pdf_sha256: str
    png_sha256: str
    d2_version: str | None = None
    renderer: str = "d2"
    renderer_version: str | None = None
    theme_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    semantic_input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    export_input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    caption: str = Field(min_length=1, max_length=180)
    node_count: int = Field(ge=2)
    max_label_characters: int = Field(ge=1)
    minimum_font_pt: float | None = Field(default=None, ge=8)
    image_call_id: str | None = None
    style_binding: str | None = None
    original_image_sha256: str | None = None
    original_image_path: str | None = None
    material_origin: Literal["host-image", "synthetic", "unknown"] | None = None
    composition_evidence: dict | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        value = handler(self)
        if self.task_id is None:
            value.pop("task_id", None)
        for name in ("original_image_path", "material_origin", "composition_evidence"):
            if getattr(self, name) is None:
                value.pop(name, None)
        return value


class DiagramSubmissionError(ValueError):
    """Raised when a diagram cannot be rendered or attached to the current reader chapter."""

    def __init__(self, code: str, message: str, diagnostic_path: Path | None = None) -> None:
        self.code = code
        self.diagnostic_path = diagnostic_path
        suffix = f"；诊断：{diagnostic_path}" if diagnostic_path is not None else ""
        super().__init__(f"{code}: {message}{suffix}")


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _diagram_theme(workspace: Workspace) -> tuple[str, PublicationPalette, str | None]:
    path = publication_profile_path(workspace)
    if not path.is_file():
        return "knowledge-product-pdf-v4", PublicationPalette(), None
    profile = PublicationProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    theme_bytes = json.dumps(
        {"profile_id": profile.profile_id, "palette": profile.palette.model_dump()},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return profile.profile_id, profile.palette, sha256(theme_bytes).hexdigest()


def _quote(label: str) -> str:
    return json.dumps(label, ensure_ascii=False)


def render_d2_source(spec: DiagramSpec, palette: PublicationPalette | None = None) -> str:
    """Render deterministic D2 source with the KP visual defaults, without model-selected styling."""
    colors = palette or PublicationPalette()
    from knowledge_product_workflow.illustration_palette import COLORS
    presentation = spec.presentation
    def fill(token):
        return colors.accent_soft if token == "brand" else COLORS[token]
    def node_fill(node):
        return fill(presentation.node_colors[node.node_id]) if presentation else colors.paper
    edge_color = colors.ink if presentation else colors.accent
    def node_stroke(node):
        return colors.accent if presentation and node.node_id in presentation.highlight_nodes else edge_color
    compact_right_flow = (
        spec.diagram_type is DiagramType.FLOW and spec.direction == "right" and len(spec.nodes) >= 4 and spec.presentation is None
    )
    grouped_grid = (presentation is not None and spec.diagram_type is DiagramType.FLOW
                    and spec.direction == "right" and len({node.group for node in spec.nodes if node.group}) in {3,4})
    lines = [
        "direction: down" if spec.direction == "down" else "direction: right",
        *(["grid-rows: 2"] if compact_right_flow else []),
        *(["grid-rows: 2", "horizontal-gap: 280", "vertical-gap: 180"] if grouped_grid else []),
        "vars: {",
        "  d2-config: { pad: 18 }",
        "}",
    ]
    if spec.diagram_type is DiagramType.SEQUENCE:
        # D2 only preserves message order and draws lifelines in this native shape.
        lines.insert(0, "shape: sequence_diagram")
    groups: dict[str, list[DiagramNode]] = {}
    ungrouped: list[DiagramNode] = []
    for node in spec.nodes:
        (groups.setdefault(node.group, []).append(node) if node.group else ungrouped.append(node))
    group_aliases = {group: f"group_{index}" for index, group in enumerate(groups, start=1)}
    node_paths: dict[str, str] = {node.node_id: node.node_id for node in ungrouped}
    for group, nodes in groups.items():
        alias = group_aliases[group]
        lines.append(f"{alias}: {{")
        lines.append(f"  label: {_quote(group)}")
        # Centered container labels compete with inbound edge labels above the
        # same boundary. Use D2's supported alignment for every container.
        lines.append("  label.near: top-left")
        lines.append(f'  style: {{ fill: "#{fill(presentation.group_colors[group]) if presentation else colors.accent_soft}"; stroke: "#{colors.accent}"; font-color: "#{colors.ink}" }}')
        for node in nodes:
            lines.append(
                f'  {node.node_id}: {_quote(node.label)} {{ style: {{ fill: "#{node_fill(node)}"; stroke: "#{node_stroke(node)}"; font-color: "#{colors.ink}"; font-size: {_PDF_NODE_FONT_SIZE}; border-radius: 6 }} }}'
            )
            node_paths[node.node_id] = f"{alias}.{node.node_id}"
        lines.append("}")
    for node in ungrouped:
        lines.append(
            f'{node.node_id}: {_quote(node.label)} {{ style: {{ fill: "#{node_fill(node)}"; stroke: "#{node_stroke(node)}"; font-color: "#{colors.ink}"; font-size: {_PDF_NODE_FONT_SIZE}; border-radius: 6 }} }}'
        )
    for edge in spec.edges:
        edge_line = f"{node_paths[edge.source]} -> {node_paths[edge.target]}"
        if edge.label and not compact_right_flow:
            edge_line += f": {_quote(edge.label)}"
        lines.append(
            f'{edge_line} {{ style: {{ stroke: "#{edge_color}"; font-size: {_PDF_EDGE_FONT_SIZE}; animated: false }} }}'
        )
    return "\n".join(lines) + "\n"


def _reader_body(source_path: Path) -> str:
    text = source_path.read_text(encoding="utf-8")
    try:
        _, body = parse_markdown_artifact(text)
    except ValueError:
        body = text
    return body.strip() + "\n"


_INTERNAL_SPEC_FIELDS = frozenset(
    {"schema_version", "chapter_number", "chapter_body_sha256", "contract_sha256", "theme"}
)


def _relative_path(workspace: Workspace, path: Path) -> str:
    return str(path.resolve().relative_to(workspace.root)).replace("\\", "/")


def _require_workspace_file(workspace: Workspace, path: Path, *, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"找不到{label}：{path}")
    try:
        resolved.relative_to(workspace.root)
    except ValueError as error:
        raise ValueError(f"{label}必须位于当前书稿工作区内") from error
    return resolved


def _write_diagnostic(
    workspace: Workspace,
    *,
    chapter_number: int,
    code: str,
    message: str,
    artifacts: list[Path] | None = None,
    next_command: str | None = None,
) -> Path:
    output = workspace.root / "runs" / "diagnostics" / f"submit-diagram-chapter-{chapter_number:02d}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "operation": "submit-diagram",
        "error_code": code,
        "message": message,
        "artifacts": [_relative_path(workspace, path) for path in artifacts or [] if path.exists()],
    }
    if next_command:
        payload["next_command"] = next_command
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def _diagram_error(
    workspace: Workspace,
    *,
    chapter_number: int,
    code: str,
    message: str,
    artifacts: list[Path] | None = None,
    next_command: str | None = None,
) -> DiagramSubmissionError:
    diagnostic = _write_diagnostic(
        workspace,
        chapter_number=chapter_number,
        code=code,
        message=message,
        artifacts=artifacts,
        next_command=next_command,
    )
    return DiagramSubmissionError(code, message, diagnostic.relative_to(workspace.root))


def _load_diagram_input(workspace: Workspace, chapter_number: int, path: Path) -> DiagramInput:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_INPUT_INVALID_JSON",
            message="图表输入必须是有效 UTF-8 JSON。",
            artifacts=[path],
            next_command=f"kp diagram-template {chapter_number} --workspace <工作区> --chapter-source <章节源稿> --diagram-id <图表ID>",
        ) from error
    if not isinstance(raw, dict):
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_INPUT_INVALID",
            message="图表输入必须是 JSON 对象。",
            artifacts=[path],
        )
    leaked = sorted(_INTERNAL_SPEC_FIELDS & set(raw))
    if leaked:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_LEGACY_INPUT",
            message=f"检测到旧版内部字段：{', '.join(leaked)}。请重新生成语义图表模板，不要填写 SHA-256 或内部版本字段。",
            artifacts=[path],
            next_command=f"kp diagram-template {chapter_number} --workspace <工作区> --chapter-source <章节源稿> --diagram-id <图表ID>",
        )
    try:
        semantic = DiagramInput.model_validate(raw)
        from knowledge_product_workflow.presentation import _read_profile, publication_profile_path
        profile_path = publication_profile_path(workspace)
        if semantic.backend == "vector" and semantic.presentation is None and profile_path.is_file():
            profile = _read_profile(profile_path)
            if profile.schema_version >= 4 and profile.illustration_style_revision == 2:
                from knowledge_product_workflow.illustration_palette import (
                    default_vector_presentation,
                )
                semantic = semantic.model_copy(update={"presentation": default_vector_presentation(workspace, profile, semantic)})
        return semantic
    except ValueError as error:
        color_context = ""
        if raw.get("presentation") is not None:
            try:
                color_context = _concept_color_conflict_context(
                    DiagramPresentationInput.model_validate(raw["presentation"]), {})
            except ValueError:
                pass
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_INPUT_INVALID",
            message=f"图表语义输入无效：{error}；{color_context + '；' if color_context else ''}请使用当前模板填写图表类型、图注、节点和边。",
            artifacts=[path],
            next_command="kp diagram-schema；kp diagram-template <章节号> --workspace <工作区> --chapter-source <章节源稿> --diagram-id <图表ID>",
        ) from error


def diagram_schema() -> dict[str, object]:
    """Describe the public semantic diagram contract accepted by ``submit-diagram``."""
    return {
        "schema_version": 1,
        "input_type": "DiagramInput",
        "json_schema": DiagramInput.model_json_schema(),
        "fields": {
            "diagram_id": "小写字母、数字和连字符组成的图表 ID",
            "backend": "vector（默认，确定性图）或 host-image（受控宿主生图）",
            "admission": "host-image 必填；完整准入字段见 kp illustration-schema",
            "presentation": "风格2须填写scheme/node_colors/group_colors/concept_nodes；完整结构见json_schema",
            "material_composition": "可选stage_flow；只在已批准host-image三至五阶段链中派生混合候选，原素材须completed与来源回执；本人当前认可前不激活",
            "diagram_type": [member.value for member in DiagramType],
            "direction": ["down", "right"],
            "caption": "读者可见图注",
            "reader_conclusion": "图后的一句读者结论",
            "heading_anchor": "可选的精确正文标题；首次插入引用时放在该小节末尾",
            "nodes": "至少两个节点，每个节点含 node_id、label，可选 group",
            "edges": "可选连接，source 和 target 必须引用 nodes 中的 node_id",
        },
        "forbidden_fields": sorted(_INTERNAL_SPEC_FIELDS),
        "renderers": "v6矢量：flow/sequence/lifecycle 使用 D2；layered_architecture/comparison/nested_structure/card_matrix/stage_flow 使用 SVG 模板。host-image 先预约、记录实际调用和资产，再用 submit-illustration 提交；material_composition=stage_flow派生host-image+svg候选，仍消费原task的C+R，须当前本人认可后activate。CLI不调用生图服务；旧版图维持原渲染语义。",
        "migration": "旧版完整 DiagramSpec JSON 不可提交。先运行 kp diagram-template，填写语义输入，再用 kp insert-diagram-reference --diagram-input 写入受控引用。",
    }


def _diagram_reference_matches(text: str, diagram_id: str) -> list[re.Match[str]]:
    return [
        match
        for match in _FIGURE_REFERENCE.finditer(text)
        if Path(match.group("target").replace("\\", "/").split("?", 1)[0].split("#", 1)[0]).stem == diagram_id
    ]


def _validate_diagram_id(diagram_id: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", diagram_id):
        raise ValueError("图表 ID 只能使用小写字母、数字和连字符")
    return diagram_id


def _require_single_controlled_reference(text: str, diagram_id: str) -> re.Match[str]:
    expected = f"assets/figures/{diagram_id}.pdf"
    related = _diagram_reference_matches(text, diagram_id)
    if any(match.group("target") != expected for match in related):
        raise ValueError("图表引用只能使用 assets/figures/<diagram-id>.pdf，不能使用绝对路径、SVG 或 PNG。")
    exact = [match for match in related if match.group("target") == expected]
    if len(exact) != 1:
        raise ValueError("章节源稿必须恰好包含一次当前图表的受控 PDF 引用。")
    return exact[0]


def insert_diagram_reference(
    workspace: Workspace,
    chapter_number: int,
    source_path: Path,
    diagram_id: str,
    diagram_input_path: Path | None = None,
    *, rebind_position: bool = False,
) -> Path:
    """Idempotently insert or update one controlled PDF reference in a workspace chapter source."""
    _validate_diagram_id(diagram_id)
    try:
        source = _require_workspace_file(workspace, source_path, label="章节源稿")
        require_current_chapter_contract(workspace, chapter_number)
        semantic = (
            _load_diagram_input(
                workspace,
                chapter_number,
                _require_workspace_file(workspace, diagram_input_path, label="图表语义输入文件"),
            )
            if diagram_input_path is not None
            else None
        )
    except (FileNotFoundError, ValueError) as error:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_REFERENCE_SOURCE_INVALID",
            message=str(error),
            next_command=f"kp draft {chapter_number} --workspace <工作区>",
        ) from error
    if semantic is not None and semantic.diagram_id != diagram_id:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_REFERENCE_ID_MISMATCH",
            message="图表语义输入中的 diagram_id 与引用命令不一致。",
            artifacts=[source, diagram_input_path] if diagram_input_path is not None else [source],
        )
    caption = semantic.caption if semantic is not None else "图表"
    from knowledge_product_workflow.illustration_tasks import require_diagram_task
    task_binding = require_diagram_task(workspace, chapter_number, semantic)
    if (task_binding is not None and semantic.backend == "vector"
            and semantic.diagram_type in _STRUCTURED_SVG_TYPES):
        theme, palette, _theme_sha256 = _diagram_theme(workspace)
        if theme == PRESENTATION_PROFILE_ID:
            try:
                from knowledge_product_workflow.presentation import _read_profile, publication_profile_path
                from knowledge_product_workflow.illustration_palette import (
                    load_category_colors, resolve_diagram_palette,
                )
                profile = _read_profile(publication_profile_path(workspace))
                if profile.illustration_style_revision == 2 and semantic.presentation is None:
                    raise ValueError("风格2图须包含完整presentation")
                if semantic.presentation is not None and profile.schema_version >= 4:
                    registered_colors = load_category_colors(workspace)
                    try:
                        resolve_diagram_palette(profile, semantic.presentation, registered_colors)
                    except ValueError as error:
                        context = _concept_color_conflict_context(semantic.presentation, registered_colors)
                        raise ValueError(f"{error}；{context}" if context else str(error)) from error
                _preflight_vector_input(semantic, palette, template_renderer=True)
            except ValueError as error:
                raise _diagram_error(workspace, chapter_number=chapter_number, code="DIAGRAM_INPUT_INVALID",
                    message=f"图语义/布局预检失败：{error}", artifacts=[source, diagram_input_path],
                    next_command="kp diagram-schema；按当前批准图任务修正语义输入并重新受控插入引用。") from error
    text = source.read_text(encoding="utf-8")
    expected = f"assets/figures/{diagram_id}.pdf"
    related = _diagram_reference_matches(text, diagram_id)
    if any(match.group("target") != expected for match in related):
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_REFERENCE_INVALID",
            message="同 ID 图表存在非受控引用；只允许 assets/figures/<diagram-id>.pdf。",
            artifacts=[source],
        )
    if len(related) > 1:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_REFERENCE_DUPLICATED",
            message="同一图表 ID 只能在当前章节源稿中引用一次。",
            artifacts=[source],
        )
    reference = f"![{caption.replace('\\', '\\\\').replace(']', '\\]')}]({expected})"
    if task_binding is not None:
        _section, task = task_binding
        from knowledge_product_workflow.illustration_tasks import resolve_task_anchor
        without_current = text
        if related:
            match = related[0]
            without_current = text[:match.start()] + text[match.end():]
        position_path = workspace.root / "runs/diagrams" / f"{diagram_id}.position.json"
        saved = json.loads(position_path.read_text(encoding="utf-8")) if position_path.is_file() else None
        try:
            anchor = resolve_task_anchor(task, without_current, saved, rebind=rebind_position)
        except ValueError as error:
            raise _diagram_error(workspace, chapter_number=chapter_number,
                                 code="DIAGRAM_POSITION_ANCHOR_INVALID",
                                 message=str(error), artifacts=[source]) from error
        place_after = anchor["after"]
        position = anchor["end"] if place_after else anchor["start"]
        # A group at the same anchor follows the frozen task order, even when
        # commands arrive out of order. Narrative bridge paragraphs form new anchors.
        from knowledge_product_workflow.content_plan import require_current_content_plan
        plan = require_current_content_plan(workspace, chapter_number)
        order = {item.task_id: index for section in plan.sections
                 for index, item in enumerate(section.illustration_tasks) if item.task_id}
        prefix, suffix = without_current[:position], without_current[position:]
        group = [(order[task.task_id], reference)]
        if place_after:
            while True:
                image_match = re.match(r"\s*(!\[[^\]]*\]\(assets/figures/([a-z0-9-]+)\.pdf\))", suffix)
                if image_match is None:
                    break
                saved = workspace.root / "runs/diagrams" / f"{image_match.group(2)}.input.json"
                if not saved.is_file():
                    break
                peer = DiagramInput.model_validate_json(saved.read_bytes())
                peer_task = next((item for section in plan.sections for item in section.illustration_tasks
                                  if item.task_id == peer.task_id), None)
                if peer_task is None or peer_task.after_anchor != task.after_anchor:
                    break
                group.append((order[peer.task_id], image_match.group(1)))
                suffix = suffix[image_match.end():]
        else:
            while True:
                image_match = re.search(r"\s*(!\[[^\]]*\]\(assets/figures/([a-z0-9-]+)\.pdf\))\s*$", prefix)
                if image_match is None:
                    break
                saved = workspace.root / "runs/diagrams" / f"{image_match.group(2)}.input.json"
                if not saved.is_file():
                    break
                peer = DiagramInput.model_validate_json(saved.read_bytes())
                peer_task = next((item for section in plan.sections for item in section.illustration_tasks
                                  if item.task_id == peer.task_id), None)
                if peer_task is None or peer_task.before_anchor != task.before_anchor:
                    break
                group.append((order[peer.task_id], image_match.group(1)))
                prefix = prefix[:image_match.start()]
        composed = prefix.rstrip() + "\n\n" + "\n\n".join(item[1] for item in sorted(group)) + "\n\n" + suffix.lstrip("\n")
        semantic_path = workspace.root / "runs" / "diagrams" / f"{diagram_id}.input.json"
        semantic_path.parent.mkdir(parents=True, exist_ok=True)
        with workspace_scratch(prefix="reference-update-", root=semantic_path.parent) as temporary_name:
            temporary = Path(temporary_name)
            prepared_source = temporary / "chapter-source.md"
            prepared_input = temporary / "diagram.input.json"
            prepared_position = temporary / "diagram.position.json"
            prepared_source.write_text(composed, encoding="utf-8")
            prepared_input.write_text(semantic.model_dump_json(indent=2) + "\n", encoding="utf-8")
            prepared_position.write_text(json.dumps({"task_id": task.task_id, "section_id": task.section_id,
                "anchor": anchor["anchor"], "after": place_after, "selection": "explicit" if task.after_anchor or task.before_anchor else "semantic"}, ensure_ascii=False), encoding="utf-8")
            _atomic_replace_many([(prepared_source, source), (prepared_input, semantic_path),
                                  (prepared_position, position_path)], temporary)
        from knowledge_product_workflow.presentation import _read_profile, publication_profile_path
        profile_path = publication_profile_path(workspace)
        if semantic.presentation is not None and profile_path.is_file():
            profile = _read_profile(profile_path)
            if profile.schema_version >= 4:
                from knowledge_product_workflow.illustration_palette import register_category_colors
                register_category_colors(workspace, profile, semantic.presentation)
    elif related:
        match = related[0]
        if match.group(0) != reference:
            source.write_text(text[: match.start()] + reference + text[match.end() :], encoding="utf-8")
    else:
        suffix = "" if text.endswith("\n") else "\n"
        if semantic is not None and semantic.heading_anchor:
            headings = list(re.finditer(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$", text, re.MULTILINE))
            matching = [(index, heading) for index, heading in enumerate(headings) if heading.group("title") == semantic.heading_anchor]
            if len(matching) != 1:
                raise _diagram_error(workspace, chapter_number=chapter_number, code="DIAGRAM_HEADING_ANCHOR_INVALID", message="图位置必须对应唯一的当前正文精确标题。", artifacts=[source])
            index, heading = matching[0]
            end = len(text)
            for later in headings[index + 1:]:
                if len(later.group("marks")) <= len(heading.group("marks")):
                    end = later.start()
                    break
            text = text[:end].rstrip() + f"\n\n{reference}\n\n" + text[end:]
        else:
            text = f"{text}{suffix}\n{reference}\n"
        source.write_text(text, encoding="utf-8")
    workspace.append_event(
        "diagram_reference_inserted",
        {"chapter_number": chapter_number, "diagram_id": diagram_id, "source": _relative_path(workspace, source)},
    )
    if semantic is not None and task_binding is None:
        semantic_path = workspace.root / "runs" / "diagrams" / f"{diagram_id}.input.json"
        semantic_path.parent.mkdir(parents=True, exist_ok=True)
        semantic_path.write_text(semantic.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return source


def create_diagram_template(
    workspace: Workspace, chapter_number: int, source_path: Path, diagram_id: str,
    task_id: str | None = None,
) -> tuple[Path, Path]:
    """Write a semantic-only diagram template for a current workspace chapter source."""
    _validate_diagram_id(diagram_id)
    try:
        source = _require_workspace_file(workspace, source_path, label="章节源稿")
        _contract, chapter = require_current_chapter_contract(workspace, chapter_number)
    except (FileNotFoundError, ValueError) as error:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_TEMPLATE_SOURCE_INVALID",
            message=str(error),
            next_command=f"kp diagram-template {chapter_number} --workspace <工作区> --chapter-source <工作区内章节源稿> --diagram-id {diagram_id}",
        ) from error
    template = {
        "diagram_id": diagram_id,
        "diagram_type": "flow",
        "direction": "down",
        "caption": "",
        "reader_conclusion": "",
        "nodes": [
            {"node_id": "start", "label": "", "group": None},
            {"node_id": "end", "label": "", "group": None},
        ],
        "edges": [{"source": "start", "target": "end", "label": None}],
    }
    extra = {}
    if chapter.illustration_policy_revision == 2:
        from knowledge_product_workflow.content_plan import _flatten_outline
        from knowledge_product_workflow.illustration_tasks import paragraph_anchors
        tasks = [task for task in chapter.illustration_tasks
                 if task.task_id == (task_id or diagram_id) and task.production == "create"]
        if len(tasks) != 1:
            raise ValueError("政策2模板须选择批准task_id（默认与diagram-id相同）")
        task = tasks[0]
        titles = dict(_flatten_outline(chapter.section_outline))
        template.update(task_id=task.task_id, diagram_type=task.diagram_type,
                        direction=task.direction or ("right" if task.diagram_type in {"stage_flow", "sequence"} else "down"),
                        backend=task.backend, heading_anchor=f"{task.section_id} {titles[task.section_id]}")
        from knowledge_product_workflow.illustration_tasks import RELATION_FAMILIES
        from knowledge_product_workflow.illustration_palette import load_category_colors
        extra = {"task": task.model_dump(mode="json"),
                 "diagram_type_direction_rules": {
                     "approved_task": {"diagram_type": template["diagram_type"], "direction": template["direction"]},
                     "relation_families": {relation: {"diagram_type": family, "direction": direction}
                                           for relation, (family, direction) in RELATION_FAMILIES.items()},
                     "constraints": ["本图diagram_type/backend/direction须与当前批准任务一致；更改须先受控修订蓝图。",
                                     "方向优先使用批准task.direction；未声明时stage_flow/sequence为right，其余为down。",
                                     "relation_families列出关系图族的默认方向；当前已批准任务方向以approved_task为准。",
                                     "stage_flow仅支持顺序相邻阶段，边不填label；分支或回环须批准为flow。",
                                     "结构SVG标签排版最多三行；显式换行与节点宽度产生的换行都参与预检。"]},
                 "registered_concept_colors": load_category_colors(workspace),
                 "paragraph_anchors": [{k: v for k, v in item.items() if k not in {"start", "end"}}
                                       for item in paragraph_anchors(source.read_text(encoding="utf-8"))
                                       if item["section_id"] == task.section_id]}
    directory = workspace.root / "runs" / "templates"
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"chapter-{chapter_number:02d}-{diagram_id}.diagram.template.json"
    context = directory / f"chapter-{chapter_number:02d}-{diagram_id}.diagram.context.json"
    output.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    context.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": chapter_number,
                "diagram_id": diagram_id,
                "source": _relative_path(workspace, source),
                "controlled_reference": f"assets/figures/{diagram_id}.pdf",
                "allowed_diagram_types": [item.value for item in DiagramType],
                **extra,
                "submission_constraints": [
                    "diagram_id 在同一章节内必须唯一；insert-diagram-reference 会拒绝重复引用。",
                    "图注（caption）只填写标题，不自行填写“图 1”或“Figure 1”；出版层会统一添加编号，并与图表资产版本绑定。",
                    "不允许引用工作区外资产或绝对路径；图型决定受控 D2 或 SVG 模板，中文标签由结构化输入精确排版。",
                    "不使用 ASCII 字符画或无教学目的的装饰图；正文图表引用必须在终稿编辑前完成。",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    workspace.append_event(
        "diagram_template_created",
        {"chapter_number": chapter_number, "diagram_id": diagram_id, "template": _relative_path(workspace, output)},
    )
    return output, context


def diagram_manifest_path(workspace: Workspace, diagram_id: str) -> Path:
    return workspace.root / "runs" / "diagrams" / f"{diagram_id}.json"


def _diagram_internal_paths(workspace: Workspace, diagram_id: str) -> tuple[Path, Path, Path, Path]:
    root = workspace.root / "runs" / "diagrams"
    return (
        root / f"{diagram_id}.spec.json",
        root / f"{diagram_id}.d2",
        root / f"{diagram_id}.svg",
        root / f"{diagram_id}.json",
    )


def _atomic_replace_many(pairs: list[tuple[Path, Path]], temporary_root: Path) -> None:
    """Replace a set of prepared artifacts, rolling back previously existing files on failure."""
    backups: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for _prepared, destination in pairs:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                backup = temporary_root / f"backup-{len(backups)}-{destination.name}"
                os.replace(destination, backup)
                backups[destination] = backup
        for prepared, destination in pairs:
            os.replace(prepared, destination)
            replaced.append(destination)
    except OSError:
        for destination in replaced:
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
        for destination, backup in backups.items():
            if backup.exists():
                os.replace(backup, destination)
        raise


def _vector_export_binding(semantic: DiagramInput, *, theme: str, theme_sha256: str | None,
                           template_renderer: bool, resolution, browser, font_css: str | None) -> str | None:
    """Reuse requires known tool identities and the same concrete render inputs."""
    if not getattr(browser, "version", None) or (not template_renderer and not getattr(resolution, "version", None)):
        return None
    if not browser.path.is_file() or (not template_renderer and not resolution.path.is_file()):
        return None
    fonts = {}
    if font_css:
        from urllib.request import url2pathname
        for uri in re.findall(r'url\("(file:[^"]+)"\)', font_css):
            fonts[uri] = _sha256_file(Path(url2pathname(uri.removeprefix("file:"))))
    payload = {
        "semantic": semantic.model_dump(mode="json"), "theme": theme,
        "theme_sha256": theme_sha256, "font_css": font_css, "fonts": fonts,
        "renderer": "svg-template" if template_renderer else "d2",
        "renderer_sources": {name: _sha256_file(Path(__file__).with_name(name))
                             for name in ("diagrams.py", "figure_templates.py", "d2.py")},
        "browser": {"path": str(browser.path), "version": browser.version, "sha256": _sha256_file(browser.path)},
        "d2": None if template_renderer else {"path": str(resolution.path), "version": resolution.version,
                                             "sha256": _sha256_file(resolution.path)},
    }
    return _sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _reusable_vector_manifest(workspace: Workspace, spec: DiagramSpec, binding: str | None) -> DiagramManifest | None:
    if binding is None:
        return None
    stored, d2, svg, path = _diagram_internal_paths(workspace, spec.diagram_id)
    try:
        diagnostic = workspace.root / "runs/diagnostics" / f"submit-diagram-chapter-{spec.chapter_number:02d}.json"
        if (diagnostic.is_file() and path.is_file()
                and diagnostic.stat().st_mtime_ns >= path.stat().st_mtime_ns
                and json.loads(diagnostic.read_text(encoding="utf-8")).get("error_code") == "D2_RENDER_FAILED"):
            return None
        manifest = DiagramManifest.model_validate_json(path.read_text(encoding="utf-8"))
        if (manifest.export_input_sha256 != binding or manifest.chapter_number != spec.chapter_number
                or manifest.renderer not in {"svg-template", "d2"}):
            return None
        checks = {stored: manifest.spec_sha256, svg: manifest.svg_sha256,
                  workspace.root / "assets/figures" / f"{spec.diagram_id}.pdf": manifest.pdf_sha256,
                  workspace.root / "assets/figures" / f"{spec.diagram_id}.png": manifest.png_sha256}
        if manifest.renderer == "d2":
            checks[d2] = manifest.d2_sha256
        if any(not file.is_file() or not file.stat().st_size or _sha256_file(file) != digest
               for file, digest in checks.items()):
            return None
        return manifest
    except (OSError, ValueError, TypeError):
        return None


def submit_diagram(
    workspace: Workspace, chapter_number: int, source_path: Path, diagram_input_path: Path,
    *, host_image: Path | None = None, image_call_id: str | None = None,
) -> tuple[Path, DiagramManifest]:
    """Render a semantic diagram input and freeze every generated asset to current source and contract."""
    try:
        source = _require_workspace_file(workspace, source_path, label="包含图表引用的章节源稿")
        input_path = _require_workspace_file(workspace, diagram_input_path, label="图表语义输入文件")
        contract_path, _ = require_current_chapter_contract(workspace, chapter_number)
    except (FileNotFoundError, ValueError) as error:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_INPUT_OUTSIDE_WORKSPACE",
            message=str(error),
            next_command=f"kp insert-diagram-reference {chapter_number} <图表ID> --workspace <工作区> --from <工作区内章节源稿>",
        ) from error
    semantic = _load_diagram_input(workspace, chapter_number, input_path)
    from knowledge_product_workflow.illustration_tasks import require_diagram_task
    try:
        require_diagram_task(workspace, chapter_number, semantic)
    except ValueError as error:
        raise _diagram_error(workspace, chapter_number=chapter_number, code="DIAGRAM_INPUT_INVALID",
            message=f"批准图任务预检失败：{error}", artifacts=[input_path]) from error
    if host_image is not None and semantic.backend != "host-image":
        raise ValueError("submit-illustration只接受host-image语义；矢量图使用submit-diagram")
    host_call = None
    if semantic.backend == "host-image":
        from knowledge_product_workflow.illustration_budget import image_call_for_asset
        from knowledge_product_workflow.illustrations import (
            image_cache_key,
            png_dimensions,
            style_binding,
        )
        from knowledge_product_workflow.pdf_preview import png_has_visible_ink
        if host_image is None or image_call_id is None:
            raise ValueError("宿主生图不能作为矢量渲染；使用submit-illustration提供已预约的实际PNG")
        host_image = _require_workspace_file(workspace, host_image, label="宿主返回PNG")
        png_dimensions(host_image)
        if not png_has_visible_ink(host_image):
            raise ValueError("宿主返回PNG没有可见内容")
        host_call = image_call_for_asset(workspace, image_call_id, semantic.diagram_id, host_image)
        if semantic.material_composition is not None:
            from knowledge_product_workflow.experimental_composition import (
                require_material_source_evidence,
            )
            require_material_source_evidence(workspace, host_call, semantic)
        if host_call.cache_key != image_cache_key(workspace, semantic, host_call.model,
                                                references=host_call.input_references):
            raise ValueError("图片调用绑定的语义/风格已变化；不能复用过期图片")
    try:
        _require_single_controlled_reference(source.read_text(encoding="utf-8"), semantic.diagram_id)
    except ValueError as error:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_REFERENCE_REQUIRED",
            message=str(error),
            artifacts=[source, input_path],
            next_command=f"kp insert-diagram-reference {chapter_number} {semantic.diagram_id} --workspace <工作区> --from <章节源稿>",
        ) from error
    body = _reader_body(source)
    theme, palette, theme_sha256 = _diagram_theme(workspace)
    from knowledge_product_workflow.illustrations import current_profile
    if theme == PRESENTATION_PROFILE_ID and current_profile(workspace).illustration_style_revision == 2:
        from knowledge_product_workflow.illustration_palette import (
            load_category_colors,
            resolve_diagram_palette,
        )
        if semantic.presentation is None:
            raise ValueError("风格2图须包含完整presentation")
        resolve_diagram_palette(current_profile(workspace), semantic.presentation, load_category_colors(workspace))
    semantic_path = workspace.root / "runs" / "diagrams" / f"{semantic.diagram_id}.input.json"
    if theme == PRESENTATION_PROFILE_ID and (
        not semantic_path.is_file() or (_load_diagram_input(workspace, chapter_number, semantic_path) != semantic and not __import__("knowledge_product_workflow.illustration_updates",fromlist=["is_pending_presentation"]).is_pending_presentation(workspace, semantic))
    ):
        raise _diagram_error(
            workspace, chapter_number=chapter_number, code="DIAGRAM_SEMANTICS_CHANGED",
            message="图语义与写入引用时的输入不同；请重新受控写入引用、定稿并刷新两份报告。",
            artifacts=[input_path],
        )
    if theme != PRESENTATION_PROFILE_ID and semantic.diagram_type in {
        DiagramType.NESTED_STRUCTURE, DiagramType.CARD_MATRIX, DiagramType.STAGE_FLOW,
    }:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_THEME_REQUIRED",
            message="新图型需要已确认的 v6 出版设计档案；旧版书稿不会自动迁移。",
            artifacts=[source, input_path],
        )
    template_renderer = theme == PRESENTATION_PROFILE_ID and semantic.diagram_type in _STRUCTURED_SVG_TYPES
    spec = DiagramSpec(
        schema_version=2 if theme == PRESENTATION_PROFILE_ID else 1,
        chapter_number=chapter_number,
        chapter_body_sha256=_sha256_text(body),
        contract_sha256=_sha256_file(contract_path),
        theme=theme,
        **semantic.model_dump(),
    )
    prepared_svg = None
    if host_call is None:
        try:
            prepared_svg = _preflight_vector_input(spec, palette, template_renderer=template_renderer)
        except ValueError as error:
            raise _diagram_error(workspace, chapter_number=chapter_number, code="DIAGRAM_INPUT_INVALID",
                message=f"图语义/布局预检失败：{error}", artifacts=[source, input_path],
                next_command="kp diagram-schema；按当前批准图任务修正语义输入并重新受控插入引用。") from error
    resolution = resolve_d2() if not template_renderer and host_call is None else None
    if not template_renderer and host_call is None and (resolution is None or not resolution.available or resolution.path is None):
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="D2_UNAVAILABLE",
            message="未检测到可运行的 D2；请运行 kp doctor 查看已检查的位置，禁止回退为 ASCII 字符画或 PNG。",
            artifacts=[source, input_path],
            next_command="kp doctor",
        )
    browser = resolve_diagram_browser()
    if not browser.available or browser.path is None:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_BROWSER_UNAVAILABLE",
            message="未检测到可离线运行的 Chrome、Edge 或 Chromium；D2 SVG 无法转换为受控 PDF/PNG 资产。",
            artifacts=[source, input_path],
            next_command="kp doctor",
        )
    internal = workspace.root / "runs" / "diagrams"
    internal.mkdir(parents=True, exist_ok=True)
    stored_spec_path, d2_path, svg_path, manifest_output = _diagram_internal_paths(workspace, spec.diagram_id)
    assets = workspace.root / "assets" / "figures"
    pdf_path = assets / f"{spec.diagram_id}.pdf"
    png_path = assets / f"{spec.diagram_id}.png"
    font_css = None
    if (spec.presentation is not None and (host_call is None or semantic.material_composition is not None)
            and current_profile(workspace).font_policy == "preferred"):
        from knowledge_product_workflow.d2 import diagram_font_css
        font_css = diagram_font_css()
    export_binding = None if host_call is not None else _vector_export_binding(
        semantic, theme=theme, theme_sha256=theme_sha256, template_renderer=template_renderer,
        resolution=resolution, browser=browser, font_css=font_css)
    reusable = _reusable_vector_manifest(workspace, spec, export_binding)
    from knowledge_product_workflow.illustration_updates import is_pending_presentation
    if is_pending_presentation(workspace, semantic):
        reusable = None
    composition_evidence = None
    try:
        with workspace_scratch(prefix=f"{spec.diagram_id}-", root=internal) as temporary_name:
            temporary = Path(temporary_name)
            temp_spec = temporary / stored_spec_path.name
            temp_d2 = temporary / d2_path.name
            temp_svg = temporary / svg_path.name
            temp_pdf = temporary / pdf_path.name
            temp_png = temporary / png_path.name
            temp_manifest = temporary / manifest_output.name
            temp_spec.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
            if reusable is not None:
                manifest = reusable.model_copy(update={"chapter_body_sha256": spec.chapter_body_sha256,
                    "contract_sha256": spec.contract_sha256, "spec_sha256": _sha256_file(temp_spec),
                    "semantic_input_sha256": _sha256_file(semantic_path) if theme == PRESENTATION_PROFILE_ID else None})
                temp_manifest.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
                _atomic_replace_many([(temp_spec, stored_spec_path), (temp_manifest, manifest_output)], temporary)
                workspace.append_event("diagram_assets_rebound", {"diagram_id": spec.diagram_id,
                    "chapter_number": chapter_number, "pdf_sha256": manifest.pdf_sha256,
                    "png_sha256": manifest.png_sha256})
                return pdf_path, manifest
            if host_call is not None:
                assert host_image is not None
                if semantic.material_composition is not None:
                    from knowledge_product_workflow.experimental_composition import (
                        compose_stage_flow_material,
                    )
                    prepared_svg, composition_evidence = compose_stage_flow_material(
                        host_image, semantic, current_profile(workspace), load_category_colors(workspace), font_css=font_css)
                    temp_svg.write_text(prepared_svg, encoding="utf-8")
                else:
                    width, height = png_dimensions(host_image)
                    encoded = base64.b64encode(host_image.read_bytes()).decode("ascii")
                    temp_svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}"><image width="{width}" height="{height}" href="data:image/png;base64,{encoded}"/></svg>', encoding="utf-8")
            elif template_renderer:
                temp_svg.write_text(prepared_svg, encoding="utf-8")
            else:
                assert resolution is not None and resolution.path is not None
                temp_d2.write_text(render_d2_source(spec, palette), encoding="utf-8")
                subprocess.run(
                    [str(resolution.path), str(temp_d2), str(temp_svg)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=d2_subprocess_environment(),
                )
            if font_css is not None:
                render_svg_with_browser(browser.path, temp_svg, temp_pdf, temp_png, font_css=font_css)
            else:
                render_svg_with_browser(browser.path, temp_svg, temp_pdf, temp_png)
            if host_call is not None and composition_evidence is None:
                shutil.copyfile(host_image, temp_png)
            if any(not path.is_file() or path.stat().st_size == 0 for path in (temp_svg, temp_pdf, temp_png)):
                raise OSError("D2 未生成完整的 SVG、PDF 和 PNG 输出")
            manifest = DiagramManifest(
                schema_version=2 if theme == PRESENTATION_PROFILE_ID else 1,
                diagram_id=spec.diagram_id,
                task_id=spec.task_id,
                chapter_number=chapter_number,
                chapter_body_sha256=spec.chapter_body_sha256,
                contract_sha256=spec.contract_sha256,
                spec_sha256=_sha256_file(temp_spec),
                d2_sha256=_sha256_file(temp_d2) if not template_renderer and host_call is None else None,
                svg_sha256=_sha256_file(temp_svg),
                pdf_sha256=_sha256_file(temp_pdf),
                png_sha256=_sha256_file(temp_png),
                d2_version=resolution.version if resolution is not None else None,
                renderer="host-image+svg" if composition_evidence is not None else "host-image" if host_call is not None else "svg-template" if template_renderer else "d2",
                renderer_version=composition_evidence["version"] if composition_evidence is not None else SVG_LAYOUT_VERSION if template_renderer and host_call is None else None,
                theme_sha256=theme_sha256,
                semantic_input_sha256=_sha256_file(input_path if semantic.presentation is not None and __import__("knowledge_product_workflow.illustration_updates",fromlist=["is_pending_presentation"]).is_pending_presentation(workspace,semantic) else semantic_path) if theme == PRESENTATION_PROFILE_ID else None,
                export_input_sha256=export_binding,
                caption=spec.caption,
                node_count=len(spec.nodes),
                max_label_characters=max(len(node.label.strip()) for node in spec.nodes),
                minimum_font_pt=12 if composition_evidence is not None or host_call is None else None,
                image_call_id=host_call.call_id if host_call is not None else None,
                style_binding=(_sha256_text(style_binding(workspace, semantic.admission.family, semantic=semantic) + composition_evidence["input_sha256"])
                               if composition_evidence is not None else style_binding(workspace, semantic.admission.family, semantic=semantic) if host_call is not None else None),
                original_image_sha256=host_call.image_sha256 if host_call is not None else None,
                original_image_path=host_call.image_path if composition_evidence is not None else None,
                material_origin=host_call.material_origin if composition_evidence is not None else None,
                composition_evidence=composition_evidence,
            )
            temp_manifest.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
            prepared = [(temp_spec, stored_spec_path)]
            if not template_renderer and host_call is None:
                prepared.append((temp_d2, d2_path))
            prepared.extend([
                (temp_svg, svg_path),
                (temp_pdf, pdf_path),
                (temp_png, png_path),
                (temp_manifest, manifest_output),
            ])
            from knowledge_product_workflow.illustration_updates import (
                candidate_root,
                is_pending_presentation,
                stage_candidate,
            )
            if host_call is not None and (composition_evidence is not None or is_pending_presentation(workspace, semantic)):
                stage_candidate(workspace, semantic, host_call.call_id, prepared,
                                chapter_source=source if composition_evidence is not None else None)
                return candidate_root(workspace, host_call.call_id) / pdf_path.name, manifest
            if host_call is None and is_pending_presentation(workspace, semantic):
                updated_input = temporary / "updated.input.json"
                updated_input.write_bytes(input_path.read_bytes())
                prepared.append((updated_input, semantic_path))
            _atomic_replace_many(prepared, temporary)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="D2_RENDER_FAILED",
            message=f"图表渲染失败：{error}；请检查图型标签、浏览器、字体和输出。",
            artifacts=[source, input_path],
            next_command="kp doctor",
        ) from error
    if semantic.presentation is not None:
        from knowledge_product_workflow.illustration_palette import register_category_colors
        register_category_colors(workspace, current_profile(workspace), semantic.presentation)
    workspace.append_event(
        "diagram_rendered",
        {
            "diagram_id": spec.diagram_id,
            "chapter_number": chapter_number,
            "pdf": _relative_path(workspace, pdf_path),
            "renderer": manifest.renderer,
            "d2_version": resolution.version if resolution is not None else None,
        },
    )
    return pdf_path, manifest


def refresh_diagram_theme(workspace: Workspace) -> list[str]:
    """Re-render only figures whose approved palette changed, preserving chapter text."""
    _, _, current_theme_sha256 = _diagram_theme(workspace)
    root = workspace.root / "runs" / "diagrams"
    if not root.is_dir():
        return []
    refreshed: list[str] = []
    for manifest_path in sorted(root.glob("*.json")):
        if manifest_path.name.endswith((".spec.json", ".input.json", ".presentation-candidate.json")):
            continue
        manifest = DiagramManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        from knowledge_product_workflow.illustrations import (
            current_profile,
            review_binding,
            review_semantic,
        )
        if current_profile(workspace).illustration_style_revision == 2:
            semantic = review_semantic(workspace,manifest.diagram_id)
            from knowledge_product_workflow.illustration_palette import (
                load_category_colors,
                resolve_diagram_palette,
            )
            try:
                resolve_diagram_palette(current_profile(workspace),semantic.presentation,load_category_colors(workspace))
            except ValueError as error:
                raise ValueError(f"图{manifest.diagram_id}概念色已更新；运行kp update-diagram-presentation {manifest.chapter_number} {manifest.diagram_id} --chapter-source <当前章节>，再完成当前候选检查与切换") from error
            if manifest.renderer in {"host-image", "host-image+svg"} and manifest.style_binding != review_binding(workspace,manifest.diagram_id):
                raise ValueError(f"图{manifest.diagram_id}风格绑定过期；沿原slot受控生图后更新，禁止自动调用")
        if manifest.renderer in {"host-image", "host-image+svg"}:
            if manifest.theme_sha256 != current_theme_sha256:
                raise ValueError(f"图{manifest.diagram_id}须在新品牌下受控生图；沿用同槽位预算，不自动重绘或混入旧色图片。运行illustration-preflight与submit-illustration后重试出版更新")
            continue
        if manifest.theme_sha256 == current_theme_sha256 and (manifest.renderer != "svg-template" or manifest.renderer_version == SVG_LAYOUT_VERSION):
            continue
        spec_path = root / f"{manifest.diagram_id}.spec.json"
        spec = DiagramSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
        source = workspace.root / "chapters" / f"chapter-{spec.chapter_number:02d}.md"
        if not source.is_file():
            raise DiagramSubmissionError("DIAGRAM_SOURCE_MISSING", f"图 {spec.diagram_id} 缺少已提交章节，无法仅换色。")
        semantic = DiagramInput.model_validate(spec.model_dump(exclude={
            "schema_version", "chapter_number", "chapter_body_sha256", "contract_sha256", "theme",
        }))
        with workspace_scratch(prefix="theme-refresh-", root=root) as temporary_name:
            semantic_path = Path(temporary_name) / f"{spec.diagram_id}.input.json"
            semantic_path.write_text(semantic.model_dump_json(), encoding="utf-8")
            submit_diagram(workspace, spec.chapter_number, source, semantic_path)
        refreshed.append(spec.diagram_id)
    return refreshed


def diagram_semantics_digest(workspace: Workspace, body: str) -> str | None:
    """Bind final editorial proofs to exact v6 figure labels and relationships."""
    if _diagram_theme(workspace)[0] != PRESENTATION_PROFILE_ID:
        return None
    inputs = []
    for match in _IMAGE.finditer(body):
        path = workspace.root / "runs" / "diagrams" / f"{match.group('id')}.input.json"
        if not path.is_file():
            raise ValueError("图语义输入缺失；请先使用带 --diagram-input 的 insert-diagram-reference。")
        semantic = DiagramInput.model_validate_json(path.read_text(encoding="utf-8"))
        if semantic.presentation is not None:
            from knowledge_product_workflow.illustrations import semantic_digest
            inputs.append(semantic_digest(semantic))
        else:
            inputs.append(_sha256_file(path))
    return _sha256_text("\n".join(inputs)) if inputs else None


def _replace_visible_image_references(markdown: str, pattern: re.Pattern[str], replace) -> str:
    """Derive image syntax while preserving code examples verbatim."""
    result: list[str] = []
    fence_character = ""
    fence_length = 0
    for line in markdown.splitlines(keepends=True):
        logical_line = re.sub(r"^ {0,3}(?:> ?)+", "", line)
        logical_line = re.sub(r"^ {0,3}(?:[-+*]|\d+[.)])\s+", "", logical_line)
        fence = _MARKDOWN_CODE_FENCE.match(logical_line)
        if fence is not None:
            delimiter = fence.group("fence")
            if not fence_character:
                fence_character, fence_length = delimiter[0], len(delimiter)
            elif (delimiter[0] == fence_character and len(delimiter) >= fence_length
                  and not fence.group("suffix").strip()):
                fence_character = ""
            result.append(line)
            continue
        if fence_character or line.startswith(("    ", "\t")):
            result.append(line)
            continue
        code_spans = [(match.start(), match.end()) for match in _MARKDOWN_INLINE_CODE.finditer(line)]

        def visible_replace(match: re.Match[str], protected_spans=code_spans) -> str:
            if any(start <= match.start() < end for start, end in protected_spans):
                return match.group(0)
            return replace(match)

        result.append(pattern.sub(visible_replace, line))
    return "".join(result)


def reader_png_references(workspace: Workspace, markdown: str) -> str:
    """Derive portable Markdown PNG links only for verified v6 paired assets."""
    if _diagram_theme(workspace)[0] != PRESENTATION_PROFILE_ID:
        return markdown

    def replace(match: re.Match[str]) -> str:
        diagram_id = match.group("id")
        manifest_path = diagram_manifest_path(workspace, diagram_id)
        manifest = DiagramManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        pdf = workspace.root / match.group("target")
        png = workspace.root / "assets" / "figures" / f"{diagram_id}.png"
        if not png.is_file() or not pdf.is_file() or _sha256_file(png) != manifest.png_sha256 or _sha256_file(pdf) != manifest.pdf_sha256:
            raise DiagramSubmissionError("DIAGRAM_ASSET_UNBOUND", f"{diagram_id} 的双格式资产未绑定当前清单。")
        return match.group(0).replace(match.group("target"), f"../assets/figures/{diagram_id}.png")

    return _replace_visible_image_references(markdown, _IMAGE, replace)


def qmd_figure_references(workspace: Workspace, markdown: str) -> str:
    """Use renderer-specific page widths without changing frozen source references."""
    if _diagram_theme(workspace)[0] != PRESENTATION_PROFILE_ID:
        return markdown

    def replace(match: re.Match[str]) -> str:
        manifest = DiagramManifest.model_validate_json(diagram_manifest_path(workspace, match.group("id")).read_text(encoding="utf-8"))
        from knowledge_product_workflow.illustrations import current_profile
        profile = current_profile(workspace)
        width = "100%" if manifest.renderer in {"svg-template", "host-image", "host-image+svg"} or profile.illustration_style_revision == 2 else "72%"
        position = ' fig-pos="H"' if profile.reader_layout_revision == 2 else ""
        return match.group(0) + f"{{width={width}{position}}}"

    return _replace_visible_image_references(markdown, _IMAGE, replace)


def validate_current_diagram_assets(workspace: Workspace, chapter_path: Path, body: str) -> list[QualityFinding]:
    """Verify every KP figure reference is bound to current text and a non-tampered asset manifest."""
    findings: list[QualityFinding] = []
    chapter_number = int(chapter_path.stem.split("-")[1])
    try:
        contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
    except ValueError as error:
        return [
            QualityFinding(
                code="DIAGRAM_ASSET_UNBOUND",
                severity=Severity.BLOCKER,
                location=str(chapter_path.relative_to(workspace.root)).replace("\\", "/"),
                message=str(error),
            )
        ]
    if chapter.illustration_policy_revision == 2:
        from knowledge_product_workflow.illustration_tasks import task_reference_findings
        try:
            findings.extend(task_reference_findings(workspace, chapter_number, body))
        except ValueError as error:
            findings.append(QualityFinding(code="ILLUSTRATION_TASK_PLAN_INVALID", severity=Severity.BLOCKER,
                                           location=str(chapter_path), message=str(error)))
    for match in _IMAGE.finditer(body):
        diagram_id = match.group("id")
        manifest_path = diagram_manifest_path(workspace, diagram_id)
        pdf_path = workspace.root / match.group("target")
        if not manifest_path.is_file() or not pdf_path.is_file():
            findings.append(
                QualityFinding(
                    code="DIAGRAM_ASSET_UNBOUND",
                    severity=Severity.BLOCKER,
                    location=str(chapter_path.relative_to(workspace.root)).replace("\\", "/"),
                    message="图表引用缺少当前 DiagramSpec/D2 渲染清单或 PDF 资产。",
                )
            )
            continue
        try:
            manifest = DiagramManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            findings.append(
                QualityFinding(
                    code="DIAGRAM_ASSET_UNBOUND",
                    severity=Severity.BLOCKER,
                    location=str(manifest_path.relative_to(workspace.root)).replace("\\", "/"),
                    message="图表渲染清单无效。",
                )
            )
            continue
        stored_spec_path, d2_path, svg_path, _ = _diagram_internal_paths(workspace, diagram_id)
        png_path = workspace.root / "assets" / "figures" / f"{diagram_id}.png"
        internal_paths = (stored_spec_path, svg_path, png_path)
        if manifest.renderer == "d2":
            internal_paths = (*internal_paths, d2_path)
        if manifest.semantic_input_sha256 is not None:
            internal_paths = (*internal_paths, workspace.root / "runs" / "diagrams" / f"{diagram_id}.input.json")
        if any(not path.is_file() for path in internal_paths):
            findings.append(
                QualityFinding(
                    code="DIAGRAM_ASSET_UNBOUND",
                    severity=Severity.BLOCKER,
                    location=str(manifest_path.relative_to(workspace.root)).replace("\\", "/"),
                    message="图表缺少当前语义源、渲染源、SVG 中间产物或 PNG 预览资产。",
                )
            )
            continue
        if (
            manifest.chapter_number != chapter_number
            or manifest.chapter_body_sha256 != _sha256_text(body)
            or manifest.contract_sha256 != _sha256_file(contract_path)
            or manifest.spec_sha256 != _sha256_file(stored_spec_path)
            or (manifest.renderer == "d2" and manifest.d2_sha256 != _sha256_file(d2_path))
            or manifest.svg_sha256 != _sha256_file(svg_path)
            or manifest.pdf_sha256 != _sha256_file(pdf_path)
            or manifest.png_sha256 != _sha256_file(png_path)
            or (manifest.theme_sha256 is not None and manifest.theme_sha256 != _diagram_theme(workspace)[2])
            or (manifest.semantic_input_sha256 is not None and manifest.semantic_input_sha256 != _sha256_file(workspace.root / "runs" / "diagrams" / f"{diagram_id}.input.json"))
        ):
            findings.append(
                QualityFinding(
                    code="DIAGRAM_ASSET_UNBOUND",
                    severity=Severity.BLOCKER,
                    location=str(pdf_path.relative_to(workspace.root)).replace("\\", "/"),
                    message="图表资产、当前正文或当前章节合约的版本绑定不一致。",
                )
            )
            continue
        if _CAPTION_NUMBER_PREFIX.match(manifest.caption) or (manifest.renderer != "host-image" and (manifest.minimum_font_pt is None or manifest.minimum_font_pt < 12)):
            findings.append(
                QualityFinding(
                    code="DIAGRAM_VISUAL_CONTRACT_INVALID",
                    severity=Severity.BLOCKER,
                    location=str(manifest_path.relative_to(workspace.root)).replace("\\", "/"),
                    message="图表编号或最小字体不符合 PDF 视觉合同；请重新生成当前图表资产。",
                )
            )
    from knowledge_product_workflow.illustrations import validate_style_assets
    findings.extend(validate_style_assets(workspace, body))
    return findings
