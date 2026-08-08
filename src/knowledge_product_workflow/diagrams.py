"""D2-backed, current-version figure assets for reader-visible knowledge products."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from knowledge_product_workflow.d2 import (
    d2_subprocess_environment,
    render_svg_with_browser,
    resolve_d2,
    resolve_diagram_browser,
)
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from knowledge_product_workflow.workspace import Workspace

_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<target>assets/figures/(?P<id>[a-z0-9][a-z0-9-]*)\.pdf)\)")
_FIGURE_REFERENCE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^)]+)\)"
)
_CAPTION_NUMBER_PREFIX = re.compile(r"^\s*(?:图|figure)\s*\d+\s*[:：.、-]?\s*", re.IGNORECASE)
_PDF_NODE_FONT_SIZE = 28
_PDF_EDGE_FONT_SIZE = 22


class DiagramType(StrEnum):
    FLOW = "flow"
    LAYERED_ARCHITECTURE = "layered_architecture"
    COMPARISON = "comparison"
    SEQUENCE = "sequence"
    LIFECYCLE = "lifecycle"


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
    diagram_type: DiagramType
    direction: str = Field(default="down", pattern=r"^(down|right)$")
    caption: str = Field(min_length=1, max_length=180)
    reader_conclusion: str = Field(min_length=1, max_length=400)
    nodes: list[DiagramNode] = Field(min_length=2)
    edges: list[DiagramEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> DiagramSpec:
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
    theme: str = Field(default="knowledge-product-pdf-v4", pattern=r"^knowledge-product-pdf-v4$")


class DiagramManifest(BaseModel):
    """Internal version proof for a D2 source and its rendered reader assets."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    diagram_id: str
    chapter_number: int
    chapter_body_sha256: str
    contract_sha256: str
    spec_sha256: str
    d2_sha256: str
    svg_sha256: str
    pdf_sha256: str
    png_sha256: str
    d2_version: str | None = None
    caption: str = Field(min_length=1, max_length=180)
    node_count: int = Field(ge=2)
    max_label_characters: int = Field(ge=1)
    minimum_font_pt: int = Field(ge=12)


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


def _quote(label: str) -> str:
    return json.dumps(label, ensure_ascii=False)


def render_d2_source(spec: DiagramSpec) -> str:
    """Render deterministic D2 source with the KP visual defaults, without model-selected styling."""
    compact_right_flow = (
        spec.diagram_type is DiagramType.FLOW and spec.direction == "right" and len(spec.nodes) >= 4
    )
    lines = [
        "direction: down" if spec.direction == "down" else "direction: right",
        *(["grid-rows: 2"] if compact_right_flow else []),
        "vars: {",
        "  d2-config: { pad: 18 }",
        "}",
    ]
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
        lines.append('  style: { fill: "#F6EAE6"; stroke: "#C75D45"; font-color: "#202124" }')
        for node in nodes:
            lines.append(
                f'  {node.node_id}: {_quote(node.label)} {{ style: {{ fill: "#FFFCFA"; stroke: "#C75D45"; font-color: "#202124"; font-size: {_PDF_NODE_FONT_SIZE}; border-radius: 6 }} }}'
            )
            node_paths[node.node_id] = f"{alias}.{node.node_id}"
        lines.append("}")
    for node in ungrouped:
        lines.append(
            f'{node.node_id}: {_quote(node.label)} {{ style: {{ fill: "#FFFCFA"; stroke: "#C75D45"; font-color: "#202124"; font-size: {_PDF_NODE_FONT_SIZE}; border-radius: 6 }} }}'
        )
    for edge in spec.edges:
        edge_line = f"{node_paths[edge.source]} -> {node_paths[edge.target]}"
        if edge.label and not compact_right_flow:
            edge_line += f": {_quote(edge.label)}"
        lines.append(
            f'{edge_line} {{ style: {{ stroke: "#C75D45"; font-size: {_PDF_EDGE_FONT_SIZE}; animated: false }} }}'
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
        return DiagramInput.model_validate(raw)
    except ValueError as error:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="DIAGRAM_INPUT_INVALID",
            message="图表输入只允许图表语义字段；请使用当前模板填写图表类型、图注、节点和边。",
            artifacts=[path],
            next_command="kp diagram-schema；kp diagram-template <章节号> --workspace <工作区> --chapter-source <章节源稿> --diagram-id <图表ID>",
        ) from error


def diagram_schema() -> dict[str, object]:
    """Describe the public semantic diagram contract accepted by ``submit-diagram``."""
    return {
        "schema_version": 1,
        "input_type": "DiagramInput",
        "fields": {
            "diagram_id": "小写字母、数字和连字符组成的图表 ID",
            "diagram_type": [member.value for member in DiagramType],
            "direction": ["down", "right"],
            "caption": "读者可见图注",
            "reader_conclusion": "图后的一句读者结论",
            "nodes": "至少两个节点，每个节点含 node_id、label，可选 group",
            "edges": "可选连接，source 和 target 必须引用 nodes 中的 node_id",
        },
        "forbidden_fields": sorted(_INTERNAL_SPEC_FIELDS),
        "migration": "旧版完整 DiagramSpec JSON 不可提交。先运行 kp diagram-template，填写语义输入，再用 kp insert-diagram-reference --diagram-input 写入受控引用。",
    }


def _diagram_reference_matches(text: str, diagram_id: str) -> list[re.Match[str]]:
    return [
        match
        for match in _FIGURE_REFERENCE.finditer(text)
        if diagram_id in match.group("target")
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
    if related:
        match = related[0]
        if match.group(0) != reference:
            source.write_text(text[: match.start()] + reference + text[match.end() :], encoding="utf-8")
    else:
        suffix = "" if text.endswith("\n") else "\n"
        text = f"{text}{suffix}\n{reference}\n"
        source.write_text(text, encoding="utf-8")
    workspace.append_event(
        "diagram_reference_inserted",
        {"chapter_number": chapter_number, "diagram_id": diagram_id, "source": _relative_path(workspace, source)},
    )
    return source


def create_diagram_template(
    workspace: Workspace, chapter_number: int, source_path: Path, diagram_id: str
) -> tuple[Path, Path]:
    """Write a semantic-only diagram template for a current workspace chapter source."""
    _validate_diagram_id(diagram_id)
    try:
        source = _require_workspace_file(workspace, source_path, label="章节源稿")
        require_current_chapter_contract(workspace, chapter_number)
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
                "submission_constraints": [
                    "diagram_id 在同一章节内必须唯一；insert-diagram-reference 会拒绝重复引用。",
                    "图注（caption）将进入中文终稿检查，并与图表资产版本绑定。",
                    "不允许引用工作区外资产或绝对路径；D2 是唯一图表结构与 SVG 生成器。",
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


def submit_diagram(
    workspace: Workspace, chapter_number: int, source_path: Path, diagram_input_path: Path
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
    spec = DiagramSpec(
        schema_version=1,
        chapter_number=chapter_number,
        chapter_body_sha256=_sha256_text(body),
        contract_sha256=_sha256_file(contract_path),
        theme="knowledge-product-pdf-v4",
        **semantic.model_dump(),
    )
    resolution = resolve_d2()
    if not resolution.available or resolution.path is None:
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
    try:
        with tempfile.TemporaryDirectory(prefix=f"{spec.diagram_id}-", dir=internal) as temporary_name:
            temporary = Path(temporary_name)
            temp_spec = temporary / stored_spec_path.name
            temp_d2 = temporary / d2_path.name
            temp_svg = temporary / svg_path.name
            temp_pdf = temporary / pdf_path.name
            temp_png = temporary / png_path.name
            temp_manifest = temporary / manifest_output.name
            temp_spec.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
            temp_d2.write_text(render_d2_source(spec), encoding="utf-8")
            subprocess.run(
                [str(resolution.path), str(temp_d2), str(temp_svg)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
                env=d2_subprocess_environment(),
            )
            render_svg_with_browser(browser.path, temp_svg, temp_pdf, temp_png)
            if any(not path.is_file() or path.stat().st_size == 0 for path in (temp_svg, temp_pdf, temp_png)):
                raise OSError("D2 未生成完整的 SVG、PDF 和 PNG 输出")
            manifest = DiagramManifest(
                diagram_id=spec.diagram_id,
                chapter_number=chapter_number,
                chapter_body_sha256=spec.chapter_body_sha256,
                contract_sha256=spec.contract_sha256,
                spec_sha256=_sha256_file(temp_spec),
                d2_sha256=_sha256_file(temp_d2),
                svg_sha256=_sha256_file(temp_svg),
                pdf_sha256=_sha256_file(temp_pdf),
                png_sha256=_sha256_file(temp_png),
                d2_version=resolution.version,
                caption=spec.caption,
                node_count=len(spec.nodes),
                max_label_characters=max(len(node.label.strip()) for node in spec.nodes),
                minimum_font_pt=12,
            )
            temp_manifest.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
            _atomic_replace_many(
                [
                    (temp_spec, stored_spec_path),
                    (temp_d2, d2_path),
                    (temp_svg, svg_path),
                    (temp_pdf, pdf_path),
                    (temp_png, png_path),
                    (temp_manifest, manifest_output),
                ],
                temporary,
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise _diagram_error(
            workspace,
            chapter_number=chapter_number,
            code="D2_RENDER_FAILED",
            message="D2 图表渲染失败；请检查本机 D2、字体和输出格式支持。",
            artifacts=[source, input_path],
            next_command="kp doctor",
        ) from error
    workspace.append_event(
        "diagram_rendered",
        {
            "diagram_id": spec.diagram_id,
            "chapter_number": chapter_number,
            "pdf": _relative_path(workspace, pdf_path),
            "d2_version": resolution.version,
        },
    )
    return pdf_path, manifest


def validate_current_diagram_assets(workspace: Workspace, chapter_path: Path, body: str) -> list[QualityFinding]:
    """Verify every KP figure reference is bound to current text and a non-tampered asset manifest."""
    findings: list[QualityFinding] = []
    chapter_number = int(chapter_path.stem.split("-")[1])
    try:
        contract_path, _ = require_current_chapter_contract(workspace, chapter_number)
    except ValueError as error:
        return [
            QualityFinding(
                code="DIAGRAM_ASSET_UNBOUND",
                severity=Severity.BLOCKER,
                location=str(chapter_path.relative_to(workspace.root)).replace("\\", "/"),
                message=str(error),
            )
        ]
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
        internal_paths = (stored_spec_path, d2_path, svg_path, png_path)
        if any(not path.is_file() for path in internal_paths):
            findings.append(
                QualityFinding(
                    code="DIAGRAM_ASSET_UNBOUND",
                    severity=Severity.BLOCKER,
                    location=str(manifest_path.relative_to(workspace.root)).replace("\\", "/"),
                    message="图表缺少当前 DiagramSpec、D2 源、SVG 中间产物或 PNG 预览资产。",
                )
            )
            continue
        if (
            manifest.chapter_number != chapter_number
            or manifest.chapter_body_sha256 != _sha256_text(body)
            or manifest.contract_sha256 != _sha256_file(contract_path)
            or manifest.spec_sha256 != _sha256_file(stored_spec_path)
            or manifest.d2_sha256 != _sha256_file(d2_path)
            or manifest.svg_sha256 != _sha256_file(svg_path)
            or manifest.pdf_sha256 != _sha256_file(pdf_path)
            or manifest.png_sha256 != _sha256_file(png_path)
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
        if _CAPTION_NUMBER_PREFIX.match(manifest.caption) or manifest.minimum_font_pt < 12:
            findings.append(
                QualityFinding(
                    code="DIAGRAM_VISUAL_CONTRACT_INVALID",
                    severity=Severity.BLOCKER,
                    location=str(manifest_path.relative_to(workspace.root)).replace("\\", "/"),
                    message="图表编号或最小字体不符合 PDF 视觉合同；请重新生成当前图表资产。",
                )
            )
    return findings
