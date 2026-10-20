"""Current-version chapter submission and deterministic teaching-structure checks."""

from __future__ import annotations

import json
import re
import unicodedata
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_product_workflow.change_impact import proof_contract_digest, proof_contract_matches
from knowledge_product_workflow.claim_classification import validate_nonfactual_claim_boundaries
from knowledge_product_workflow.code_quality import validate_code_blocks
from knowledge_product_workflow.content_plan import ContentPlanError, require_current_content_plan
from knowledge_product_workflow.content_quality import scan_markdown_content
from knowledge_product_workflow.developmental import (
    DevelopmentalEditError,
    _FinalStructureReviewPending,
    developmental_report_path,
    require_current_developmental_report,
)
from knowledge_product_workflow.diagrams import validate_current_diagram_assets
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.humanize import (
    HumanizeReportError,
    humanize_report_path,
    require_current_humanize_report,
)
from knowledge_product_workflow.models import ArtifactMetadata, ArtifactStatus
from knowledge_product_workflow.presentation_validation import validate_presentation_markdown
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.user_decisions import UserDecisionTarget
from knowledge_product_workflow.verification_context import (
    current_verification as _v_current_context,
)
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import glob_files as _v_glob_files
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import memoized_read as _memoized_read
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    read_bytes as _v_read_bytes,
)
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.verification_context import (
    verified_read as _verification_read,
)
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workflows.authoring import (
    ChapterContractRequiredError,
)
from knowledge_product_workflow.workflows.authoring import (
    require_current_chapter_contract_in_view as require_current_chapter_contract,
)
from knowledge_product_workflow.workflows.curriculum import (
    BlueprintChapter,
    ProductMode,
    SectionOutlineItem,
    require_current_submitted_blueprint,
)
from knowledge_product_workflow.workflows.product_brief import parse_detail_constraints
from knowledge_product_workflow.workspace import Workspace

_HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")
_NUMBERED_HEADING = re.compile(r"^(?P<number>\d+(?:\.\d+)*)(?:\.)?\s+(?P<title>\S.*)$")
_DIV_START = re.compile(r"^\s*:::\s*\{(?P<attributes>[^}]*)\}\s*$")
_DIV_CLASS = re.compile(r"\.([A-Za-z][A-Za-z0-9_-]*)")
_BARE_COMPONENT = re.compile(r"(?<![{:])\.(kp-[A-Za-z][A-Za-z0-9_-]*)\b")
_ASCII_DIAGRAM = re.compile(r"[┌┐└┘│─]{3,}")
_COMPLETE_ANSWER = re.compile(
    r"(?:完整(?:示范)?回答|完整回答示范|标准回答(?:要点)?|深度答案|complete answer(?: demonstration)?)",
    re.IGNORECASE,
)
_SUPPORTED_COMPONENTS = frozenset(
    {"kp-source", "kp-key-takeaway", "kp-practice", "kp-note", "kp-decision", "kp-procedure", "kp-worksheet", "kp-percentage-distribution", "kp-chapter-lead"}
)
_VISIBLE_TEXT = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")


class ChapterComplianceMappingInput(BaseModel):
    """The only semantic mapping a host may supply for a chapter submission."""

    model_config = ConfigDict(extra="forbid")

    learning_objective_anchors: dict[str, str] = Field(default_factory=dict)
    unit_anchors: dict[str, str] = Field(default_factory=dict)
    claim_locations: dict[str, str] = Field(default_factory=dict)

    @field_validator("learning_objective_anchors", "unit_anchors", "claim_locations")
    @classmethod
    def validate_anchor_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not anchor.strip() for key, anchor in value.items()):
            raise ValueError("合规映射的键和值均不能为空")
        return value


class ChapterComplianceSubmission(ChapterComplianceMappingInput):
    """Internal persisted proof that binds semantic mappings to one chapter version."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    chapter_number: int = Field(gt=0)
    chapter_body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
class ChapterCompliance(ChapterComplianceSubmission):
    """Persisted internal proof that binds the submitted body to its current contract."""

    chapter_version: int = Field(ge=1)


class ChapterSubmissionError(ValueError):
    """Raised when a chapter cannot enter the controlled workflow."""

    def __init__(self, code: str, message: str, diagnostic_path: Path | None = None) -> None:
        self.code = code
        self.diagnostic_path = diagnostic_path
        suffix = f"；诊断：{diagnostic_path}" if diagnostic_path is not None else ""
        super().__init__(f"{code}: {message}{suffix}")


def _sha256_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return sha256(_v_read_bytes(path)).hexdigest()


def _location(path: Path, workspace: Workspace, line_number: int | None = None) -> str:
    base = str(path.relative_to(workspace.root)).replace("\\", "/")
    return base if line_number is None else f"{base}:第 {line_number} 行"


def _finding(code: str, path: Path, workspace: Workspace, message: str, line_number: int | None = None) -> QualityFinding:
    return QualityFinding(
        code=code,
        severity=Severity.BLOCKER,
        location=_location(path, workspace, line_number),
        message=message,
    )


def _visible_lines(body: str) -> list[tuple[int, str]]:
    """Strip fenced-code lines before structural checks; code examples may contain Markdown syntax."""
    visible: list[tuple[int, str]] = []
    in_fence = False
    for line_number, line in enumerate(body.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            visible.append((line_number, line))
    return visible


def _headings(body: str) -> list[tuple[int, int, str]]:
    return [
        (line_number, len(match.group("marks")), match.group("text").strip())
        for line_number, line in _visible_lines(body)
        if (match := _HEADING.match(line)) is not None
    ]


def _reader_body_from_source(path: Path) -> str:
    text = _v_read_text(path, encoding="utf-8")
    try:
        _, body = parse_markdown_artifact(text)
    except ValueError:
        return text
    return body


def _requires_chinese_humanize(workspace: Workspace) -> bool:
    brief_path = workspace.root / "planning" / "product-brief.md"
    if not _v_is_file(brief_path):
        return True
    try:
        _, brief_body = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
        constraints = parse_detail_constraints(brief_body)
    except (OSError, ValueError):
        return True
    return constraints is None or constraints.language == "zh-CN"


_INTERNAL_SUBMISSION_FIELDS = frozenset(
    {"schema_version", "chapter_number", "chapter_body_sha256", "contract_sha256", "chapter_version"}
)


def _relative_path(workspace: Workspace, path: Path) -> str:
    return str(path.resolve().relative_to(workspace.root)).replace("\\", "/")


def _require_workspace_file(workspace: Workspace, path: Path, *, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not _v_is_file(resolved):
        raise FileNotFoundError(f"找不到{label}：{path}")
    try:
        resolved.relative_to(workspace.root)
    except ValueError as error:
        raise ValueError(f"{label}必须位于当前书稿工作区内") from error
    return resolved


def _write_diagnostic(
    workspace: Workspace,
    *,
    operation: str,
    identifier: str,
    code: str,
    message: str,
    artifacts: list[Path] | None = None,
    next_command: str | None = None,
    findings: list[QualityFinding] | None = None,
) -> Path:
    """Persist a compact, host-readable recovery record without leaking reader content."""
    output = workspace.root / "runs" / "diagnostics" / f"{operation}-{identifier}.json"
    _v_mkdir(output.parent,parents=True,exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "operation": operation,
        "error_code": code,
        "message": message,
        "artifacts": [_relative_path(workspace, path) for path in artifacts or [] if _v_exists(path)],
    }
    if next_command:
        payload["next_command"] = next_command
    if findings:
        payload["findings"] = [
            {
                "code": finding.code,
                "location": finding.location,
                "message": finding.message,
            }
            for finding in findings
        ]
    _v_write_text(output, json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def _submission_error(
    workspace: Workspace,
    *,
    chapter_number: int,
    code: str,
    message: str,
    artifacts: list[Path] | None = None,
    next_command: str | None = None,
    findings: list[QualityFinding] | None = None,
) -> ChapterSubmissionError:
    diagnostic = _write_diagnostic(
        workspace,
        operation="submit-chapter",
        identifier=f"chapter-{chapter_number:02d}",
        code=code,
        message=message,
        artifacts=artifacts,
        next_command=next_command,
        findings=findings,
    )
    return ChapterSubmissionError(code, message, diagnostic.relative_to(workspace.root))


def _load_mapping(workspace: Workspace, chapter_number: int, path: Path) -> ChapterComplianceMappingInput:
    try:
        raw = json.loads(_v_read_text(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code="CHAPTER_COMPLIANCE_INPUT_INVALID_JSON",
            message="章节合规映射必须是有效 UTF-8 JSON。",
            artifacts=[path],
            next_command=f"kp chapter-compliance-template {chapter_number} --workspace <工作区> --from <章节源稿>",
        ) from error
    if not isinstance(raw, dict):
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code="CHAPTER_COMPLIANCE_INPUT_INVALID",
            message="章节合规映射必须是 JSON 对象。",
            artifacts=[path],
            next_command=f"kp chapter-compliance-template {chapter_number} --workspace <工作区> --from <章节源稿>",
        )
    leaked = sorted(_INTERNAL_SUBMISSION_FIELDS & set(raw))
    if leaked:
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code="CHAPTER_COMPLIANCE_LEGACY_INPUT",
            message=f"检测到旧版内部字段：{', '.join(leaked)}。请重新生成当前章节的语义映射模板，不要填写版本或 SHA-256。",
            artifacts=[path],
            next_command=f"kp chapter-compliance-template {chapter_number} --workspace <工作区> --from <章节源稿>",
        )
    try:
        return ChapterComplianceMappingInput.model_validate(raw)
    except ValueError as error:
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code="CHAPTER_COMPLIANCE_INPUT_INVALID",
            message="章节合规映射只允许学习目标、教学单元和 Claim 的标题锚点字段。",
            artifacts=[path],
            next_command=f"kp chapter-compliance-schema；kp chapter-compliance-template {chapter_number} --workspace <工作区> --from <章节源稿>",
        ) from error


def chapter_compliance_schema() -> dict[str, object]:
    """Describe the stable public semantic input accepted by ``submit-chapter``."""
    return {
        "schema_version": 1,
        "input_type": "ChapterComplianceMappingInput",
        "fields": {
            "learning_objective_anchors": "object：当前章节学习目标 -> 当前正文中的标题文本",
            "unit_anchors": "object：当前章节必需教学单元 -> 当前正文中的标题文本",
            "claim_locations": "object：当前章节 Claim ID -> 当前正文中的标题文本",
        },
        "forbidden_fields": sorted(_INTERNAL_SUBMISSION_FIELDS),
        "migration": "旧版完整 ChapterComplianceSubmission JSON 不可提交。运行 kp chapter-compliance-template 生成当前章节模板。",
    }


def _heading_candidates(body: str) -> list[str]:
    return [heading for _, _, heading in _headings(body)]


def _section_outline_payload(items: list[SectionOutlineItem]) -> list[dict[str, object]]:
    """Expose the approved reader structure without exposing control-plane fields."""
    return [
        {
            "number": item.number,
            "title": item.title,
            "children": _section_outline_payload(item.children),
        }
        for item in items
    ]


def create_chapter_compliance_template(
    workspace: Workspace, chapter_number: int, source_path: Path
) -> tuple[Path, Path]:
    """Create a semantic-only mapping template and a separate current-source context file."""
    source = _require_workspace_file(workspace, source_path, label="章节源稿")
    _, chapter = require_current_chapter_contract(workspace, chapter_number)
    if not chapter.section_outline:
        raise ChapterContractRequiredError(
            "当前章节合约来自缺少 section_outline 的旧蓝图；请先使用 "
            "kp amend-outline --workspace <工作区> --from <蓝图输入> 补齐、批准并重新编译合约。"
        )
    try:
        require_current_content_plan(workspace, chapter_number)
    except ContentPlanError as error:
        raise ContentPlanError(
            f"{error}；请先运行 kp content-plan-template {chapter_number} 并提交当前内容计划。"
        ) from error
    body = _reader_body_from_source(source).strip() + "\n"
    template = {
        "learning_objective_anchors": {objective: "" for objective in chapter.learning_objectives},
        "unit_anchors": {unit: "" for unit in chapter.required_units},
        "claim_locations": {claim: "" for claim in chapter.claim_ids},
    }
    directory = workspace.root / "runs" / "templates"
    _v_mkdir(directory,parents=True,exist_ok=True)
    output = directory / f"chapter-{chapter_number:02d}-compliance.template.json"
    context = directory / f"chapter-{chapter_number:02d}-compliance.context.json"
    _v_write_text(output, json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _v_write_text(context,
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": chapter_number,
                "source": _relative_path(workspace, source),
                "current_heading_candidates": _heading_candidates(body),
                "expected_section_outline": _section_outline_payload(chapter.section_outline),
                "required_learning_objectives": chapter.learning_objectives,
                "required_units": chapter.required_units,
                "required_claim_ids": chapter.claim_ids,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    workspace.append_event(
        "chapter_compliance_template_created",
        {"chapter_number": chapter_number, "template": _relative_path(workspace, output)},
    )
    return output, context


def _validate_heading_tree(
    body: str, chapter: BlueprintChapter, path: Path, workspace: Workspace
) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    headings = _headings(body)
    if not headings:
        return [_finding("CHAPTER_SUBSTANTIVE_CONTENT_MISSING", path, workspace, "章节至少需要一个一级标题和实质正文。")]
    first_line, first_level, first_heading = headings[0]
    expected_title = f"{chapter.chapter_number}. {chapter.title}"
    if first_level != 1 or _normalized_heading_title(first_heading) != _normalized_heading_title(expected_title):
        findings.append(
            _finding(
                "HEADING_NUMBER_OUT_OF_ORDER",
                path,
                workspace,
                f"章节一级标题必须为“{expected_title}”，并与当前合约一致。",
                first_line,
            )
        )

    seen_numbers: set[tuple[int, ...]] = {(chapter.chapter_number,)}
    last_child: dict[tuple[int, ...], int] = {}
    actual_items: list[tuple[int, tuple[int, ...], str]] = []
    for line_number, level, heading in headings:
        match = _NUMBERED_HEADING.match(heading)
        if match is None:
            continue
        parts = tuple(int(part) for part in match.group("number").split("."))
        if len(parts) == 1:
            continue
        if len(parts) != level:
            findings.append(
                _finding(
                    "HEADING_NUMBER_OUT_OF_ORDER",
                    path,
                    workspace,
                    "编号标题层级必须与 Markdown 标题层级一致。",
                    line_number,
                )
            )
            continue
        if parts[0] != chapter.chapter_number:
            findings.append(
                _finding(
                    "HEADING_NUMBER_OUT_OF_ORDER",
                    path,
                    workspace,
                    f"章节编号必须以 {chapter.chapter_number} 开头。",
                    line_number,
                )
            )
            continue
        parent = parts[:-1]
        if parent and parent not in seen_numbers:
            findings.append(
                _finding(
                    "HEADING_NUMBER_OUT_OF_ORDER",
                    path,
                    workspace,
                    "编号标题的父级必须先在正文中出现。",
                    line_number,
                )
            )
        previous = last_child.get(parent)
        expected_child = (previous + 1) if previous is not None else 1
        if parts[-1] < expected_child:
            findings.append(
                _finding(
                    "HEADING_NUMBER_OUT_OF_ORDER",
                    path,
                    workspace,
                    f"同一父级下的编号乱序或重复：前一项为 {previous}，当前为 {parts[-1]}。",
                    line_number,
                )
            )
        elif parts[-1] > expected_child:
            parent_text = ".".join(map(str, parent))
            expected_text = ".".join(map(str, parent + (expected_child,)))
            findings.append(
                _finding(
                    "HEADING_NUMBER_GAP",
                    path,
                    workspace,
                    f"编号不连续：父级 {parent_text} 下缺少 {expected_text}，当前为 {match.group('number')}。",
                    line_number,
                )
            )
        last_child[parent] = parts[-1]
        seen_numbers.add(parts)
        actual_items.append((line_number, parts, match.group("title")))

    if not chapter.section_outline:
        return findings

    expected_items = _flatten_section_outline(chapter.section_outline)
    expected_by_number = {parts: title for parts, title in expected_items}
    actual_by_number: dict[tuple[int, ...], tuple[int, str]] = {}
    for line_number, parts, title in actual_items:
        actual_by_number.setdefault(parts, (line_number, title))

    for parts, expected_heading in expected_items:
        expected_number = ".".join(map(str, parts))
        actual = actual_by_number.get(parts)
        if actual is None:
            findings.append(
                _finding(
                    "HEADING_OUTLINE_ITEM_MISSING",
                    path,
                    workspace,
                    f"正文缺少已批准蓝图标题：{expected_number} {expected_heading}。",
                )
            )
            continue
        line_number, actual_heading = actual
        if _normalized_heading_title(actual_heading) != _normalized_heading_title(expected_heading):
            findings.append(
                _finding(
                    "HEADING_OUTLINE_TITLE_MISMATCH",
                    path,
                    workspace,
                    f"标题 {expected_number} 与蓝图不一致：期望“{expected_heading}”，实际“{actual_heading}”。",
                    line_number,
                )
            )

    for line_number, parts, actual_heading in actual_items:
        if parts not in expected_by_number:
            actual_number = ".".join(map(str, parts))
            findings.append(
                _finding(
                    "HEADING_OUTLINE_ITEM_UNDECLARED",
                    path,
                    workspace,
                    f"正文包含蓝图未声明的编号标题：{actual_number} {actual_heading}。",
                    line_number,
                )
            )

    expected_order = [parts for parts, _ in expected_items]
    actual_order = [parts for _, parts, _ in actual_items]
    if len(actual_order) == len(expected_order) and set(actual_order) == set(expected_order) and actual_order != expected_order:
        first_difference = next(
            index for index, (actual, expected) in enumerate(zip(actual_order, expected_order, strict=True)) if actual != expected
        )
        actual_number = ".".join(map(str, actual_order[first_difference]))
        expected_number = ".".join(map(str, expected_order[first_difference]))
        line_number = actual_items[first_difference][0]
        findings.append(
            _finding(
                "HEADING_OUTLINE_ORDER_MISMATCH",
                path,
                workspace,
                f"正文标题顺序与蓝图不一致：此处期望 {expected_number}，实际 {actual_number}。",
                line_number,
            )
        )
    return findings


def _normalized_heading_title(title: str) -> str:
    return unicodedata.normalize("NFC", title).strip()


def _flatten_section_outline(items: list[SectionOutlineItem]) -> list[tuple[tuple[int, ...], str]]:
    flattened: list[tuple[tuple[int, ...], str]] = []
    for item in items:
        parts = tuple(int(part) for part in item.number.split("."))
        flattened.append((parts, item.title))
        flattened.extend(_flatten_section_outline(item.children))
    return flattened


def _validate_components(body: str, path: Path, workspace: Workspace) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    stack: list[tuple[int, set[str]]] = []
    for line_number, line in _visible_lines(body):
        stripped = line.strip()
        start = _DIV_START.match(line)
        if start is not None:
            classes = set(_DIV_CLASS.findall(start.group("attributes")))
            components = {item for item in classes if item.startswith("kp-")}
            unknown = components - _SUPPORTED_COMPONENTS
            if not components or unknown or stack:
                detail = "读者组件必须使用受支持的单层 fenced div 语法。"
                findings.append(
                    _finding("INVALID_READER_COMPONENT", path, workspace, detail, line_number)
                )
            stack.append((line_number, components))
            continue
        if stripped == ":::":
            if not stack:
                findings.append(
                    _finding("INVALID_READER_COMPONENT", path, workspace, "读者组件存在无对应开始标记的结束围栏。", line_number)
                )
            else:
                stack.pop()
            continue
        if _BARE_COMPONENT.search(line):
            findings.append(
                _finding("INVALID_READER_COMPONENT", path, workspace, "读者组件只能使用 fenced div，不能裸露类名。", line_number)
            )
    for line_number, _ in stack:
        findings.append(
            _finding("INVALID_READER_COMPONENT", path, workspace, "读者组件缺少结束围栏 :::。", line_number)
        )
    return findings


def validate_chapter_reader_content(workspace: Workspace, path: Path, body: str) -> list[QualityFinding]:
    """Use the same local reader checks before submission and at full-book validation."""
    location = _relative_path(workspace, path)
    return [
        *scan_markdown_content(body, location),
        *validate_code_blocks(body, location, workspace=workspace),
        *validate_presentation_markdown(body, location, workspace),
    ]


def _validate_compliance_mapping(
    body: str,
    chapter: BlueprintChapter,
    submission: ChapterComplianceSubmission,
    path: Path,
    workspace: Workspace,
) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    heading_texts = {heading for _, _, heading in _headings(body)}
    if set(submission.learning_objective_anchors) != set(chapter.learning_objectives):
        findings.append(
            _finding(
                "CHAPTER_COMPLIANCE_MAPPING_INVALID",
                path,
                workspace,
                "学习目标映射必须与当前章节合约中的学习目标一一对应。",
            )
        )
    required_units = set(chapter.required_units)
    if set(submission.unit_anchors) != required_units:
        findings.append(
            _finding(
                "CHAPTER_COMPLIANCE_MAPPING_INVALID",
                path,
                workspace,
                "教学单元映射必须与当前章节合约中的必需教学单元一一对应。",
            )
        )
    expected_claims = set(chapter.claim_ids)
    if set(submission.claim_locations) != expected_claims:
        findings.append(
            _finding(
                "CHAPTER_COMPLIANCE_MAPPING_INVALID",
                path,
                workspace,
                "事实声明映射必须与当前章节合约的 Claim 清单一致。",
            )
        )
    for anchor in (
        *submission.learning_objective_anchors.values(),
        *submission.unit_anchors.values(),
        *submission.claim_locations.values(),
    ):
        if anchor not in heading_texts:
            findings.append(
                _finding(
                    "CHAPTER_COMPLIANCE_MAPPING_INVALID",
                    path,
                    workspace,
                    f"合规映射锚点不存在于读者正文标题中：{anchor}",
                )
            )
    mapped_headings = set(submission.learning_objective_anchors.values()) | set(
        submission.unit_anchors.values()
    ) | set(submission.claim_locations.values())
    for line_number, level, heading in _headings(body):
        if level > 1 and _NUMBERED_HEADING.match(heading) is None and heading not in mapped_headings:
            findings.append(
                _finding(
                    "CHAPTER_COMPLIANCE_MAPPING_INVALID",
                    path,
                    workspace,
                    "非编号读者标题必须是合规映射中已声明的教学单元或学习目标锚点。",
                    line_number,
                )
            )
    return findings


def _complete_answer_headings(body: str) -> list[str]:
    return [heading for _, _, heading in _headings(body) if _COMPLETE_ANSWER.search(heading)]


def _existing_complete_answer_count(workspace: Workspace, except_chapter: int) -> int:
    directory = workspace.root / "runs" / "chapter-compliance"
    total = 0
    for path in _v_glob_files(directory, "chapter-[0-9][0-9].json"):
        try:
            record = ChapterCompliance.model_validate_json(_v_read_text(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.chapter_number == except_chapter:
            continue
        chapter_path = workspace.root / "chapters" / f"chapter-{record.chapter_number:02d}.md"
        if _v_is_file(chapter_path):
            try:
                _, current_body = parse_markdown_artifact(_v_read_text(chapter_path, encoding="utf-8"))
            except ValueError:
                continue
            if _sha256_text(current_body) == record.chapter_body_sha256:
                total += len(_complete_answer_headings(current_body))
    return total


def _validate_interview_policy(
    body: str, chapter: BlueprintChapter, path: Path, workspace: Workspace
) -> list[QualityFinding]:
    _, _, blueprint = require_current_submitted_blueprint(workspace)
    headings = _complete_answer_headings(body)
    if not headings:
        return []
    if blueprint.product_mode is not ProductMode.INTERVIEW or blueprint.interview_answer_policy is None:
        return [
            _finding(
                "INTERVIEW_ANSWER_QUOTA_EXCEEDED",
                path,
                workspace,
                "非面试产品不得加入完整示范回答或标准话术标题。",
            )
        ]
    allowed_for_chapter = blueprint.interview_answer_policy.chapter_complete_answer_limits.get(
        chapter.chapter_number, 0
    )
    if "complete_answer_demo" not in chapter.required_units + chapter.optional_units or len(headings) > allowed_for_chapter:
        return [
            _finding(
                "INTERVIEW_ANSWER_QUOTA_EXCEEDED",
                path,
                workspace,
                "完整示范回答超出当前章节体裁或蓝图声明的配额。",
            )
        ]
    total = _existing_complete_answer_count(workspace, chapter.chapter_number) + len(headings)
    if total > blueprint.interview_answer_policy.total_complete_answer_limit:
        return [
            _finding(
                "INTERVIEW_ANSWER_QUOTA_EXCEEDED",
                path,
                workspace,
                "完整示范回答超出全书蓝图声明的配额。",
            )
        ]
    return []


def validate_chapter_submission(
    workspace: Workspace,
    chapter_number: int,
    body: str,
    submission: ChapterComplianceSubmission,
    *,
    path: Path | None = None,
    include_reader_content: bool = True,
) -> list[QualityFinding]:
    """Validate a body and mapping against the one current, approved chapter contract."""
    destination = path or workspace.root / "chapters" / f"chapter-{chapter_number:02d}.md"
    try:
        _contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
    except ChapterContractRequiredError as error:
        return [_finding("CHAPTER_CONTRACT_STALE", destination, workspace, str(error))]
    findings: list[QualityFinding] = []
    if submission.chapter_number != chapter_number:
        findings.append(
            _finding("CHAPTER_COMPLIANCE_MAPPING_INVALID", destination, workspace, "合规映射章节号与提交章节不一致。")
        )
    if submission.chapter_body_sha256 != _sha256_text(body):
        findings.append(
            _finding("CHAPTER_COMPLIANCE_STALE", destination, workspace, "合规映射绑定的正文 SHA-256 与提交正文不一致。")
        )
    if not proof_contract_matches(workspace, chapter_number, submission.contract_sha256, "compliance", compliance_path(workspace, chapter_number)):
        findings.append(
            _finding("CHAPTER_COMPLIANCE_STALE", destination, workspace, "合规映射绑定的章节合约 SHA-256 已过期。")
        )
    visible_text = "\n".join(line for _, line in _visible_lines(body))
    minimum = max(80, len(chapter.required_units) * 80)
    if len(_VISIBLE_TEXT.findall(visible_text)) < minimum:
        findings.append(
            _finding(
                "CHAPTER_SUBSTANTIVE_CONTENT_MISSING",
                destination,
                workspace,
                f"章节实质文本不足；当前体裁至少需要 {minimum} 个可读字符。",
            )
        )
    findings.extend(_validate_heading_tree(body, chapter, destination, workspace))
    findings.extend(_validate_components(body, destination, workspace))
    if include_reader_content:
        findings.extend(validate_chapter_reader_content(workspace, destination, body))
    findings.extend(_validate_compliance_mapping(body, chapter, submission, destination, workspace))
    findings.extend(
        validate_nonfactual_claim_boundaries(
            workspace, chapter_number, body, chapter.claim_ids, submission.claim_locations
        )
    )
    findings.extend(_validate_interview_policy(body, chapter, destination, workspace))
    findings.extend(validate_current_diagram_assets(workspace, destination, body))
    for line_number, line in _visible_lines(body):
        if _ASCII_DIAGRAM.search(line):
            findings.append(
                _finding(
                    "ASCII_DIAGRAM_DETECTED",
                    destination,
                    workspace,
                    "检测到读者正文中的 ASCII 字符画；请使用受控图表模板和图表资产链路替换。",
                    line_number,
                )
            )
    return findings


def compliance_path(workspace: Workspace, chapter_number: int) -> Path:
    return workspace.root / "runs" / "chapter-compliance" / f"chapter-{chapter_number:02d}.json"


@_verification_read(transaction_view=True)
def check_chapter(workspace: Workspace, chapter_number: int, source_path: Path) -> dict[str, object]:
    """Read-only preflight for a candidate chapter and its bound proof chain."""
    return _check_chapter(workspace, chapter_number, source_path)


def check_chapter_in_view(workspace: Workspace, chapter_number: int, source_path: Path):
    """Internal batch read; an absent or different action retains public admission."""
    from knowledge_product_workflow.workspace_transactions import has_transaction_read_view

    if not has_transaction_read_view(workspace):
        return check_chapter(workspace, chapter_number, source_path)
    return _check_chapter(workspace, chapter_number, source_path)


@_verification_read(transaction_view=True)
def check_chapters(workspace: Workspace, chapter_numbers: list[int]) -> dict:
    """Canonical chapters only, admitted once and checked in one guarded snapshot."""
    from knowledge_product_workflow.production_intake import consistent_production_read
    if (not chapter_numbers or any(type(n) is not int or n < 1 for n in chapter_numbers)
            or len(set(chapter_numbers)) != len(chapter_numbers)):
        raise ValueError("章节批次须为非空、不重复的正整数")
    reports = []
    with consistent_production_read(workspace, chapters=set(chapter_numbers)):
        for number in chapter_numbers:
            path = workspace.root / f"chapters/chapter-{number:02d}.md"
            if not _v_is_file(path):
                reports.append({"chapter_number": number, "state": "missing", "checks": {},
                                "refresh_actions": ["先完成此章节；不在只读检查中生成"]})
            else:
                reports.append(check_chapter_in_view(workspace, number, path))
    from knowledge_product_workflow.verification_context import current_verification
    current_verification(workspace).verify_read_set()  # Reject mixed bytes before CLI can emit a coherent result.
    return {"coherent_snapshot": True, "reports": reports,
            "all_current": all(bool(r.get("checks")) and not r.get("refresh_actions")
                               and all(v.get("state") in {"current", "not_required"} for v in r["checks"].values()) for r in reports),
            "scope": "canonical_chapter_proofs_only; not final QA or publication approval"}


def _check_chapter(workspace: Workspace, chapter_number: int, source_path: Path):
    from knowledge_product_workflow.workspace_transactions import (
        require_transaction_consistency_in_view as require_transaction_consistency,
    )
    require_transaction_consistency(workspace, chapters={chapter_number})
    return _check_chapter_payload(workspace, chapter_number, source_path)


@_memoized_read
def _check_chapter_payload(workspace: Workspace, chapter_number: int, source_path: Path):
    source = _require_workspace_file(workspace, source_path, label="章节源稿")
    body = _reader_body_from_source(source).strip() + "\n"
    report: dict[str, object] = {
        "chapter_number": chapter_number,
        "source": _relative_path(workspace, source),
        "source_sha256": _sha256_text(body),
        "checks": {},
        "refresh_actions": [],
    }
    checks: dict[str, object] = report["checks"]  # type: ignore[assignment]
    actions: list[str] = report["refresh_actions"]  # type: ignore[assignment]
    try:
        contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
        checks["contract"] = {"state": "current", "path": _relative_path(workspace, contract_path)}
    except ChapterContractRequiredError as error:
        checks["contract"] = {"state": "blocked", "reason": str(error)}
        actions.append("修复并批准当前章节合约")
        return report
    try:
        plan = require_current_content_plan(workspace, chapter_number)
        checks["content_plan"] = {"state": "current" if plan is not None else "not_required"}
    except ContentPlanError as error:
        state = "stale" if "过期" in str(error) else "missing" if "缺少" in str(error) else "blocked"
        checks["content_plan"] = {"state": state, "reason": str(error)}
        actions.append(f"kp content-plan-template {chapter_number} --workspace <工作区>")
    findings = [
        *validate_chapter_reader_content(workspace, source, body),
        *_validate_heading_tree(body, chapter, source, workspace),
        *_validate_components(body, source, workspace),
    ]
    pending_assets = [item.model_dump(mode="json") for item in findings if item.code == "PRESENTATION_ASSET_MISSING"]
    findings = [item for item in findings if item.code != "PRESENTATION_ASSET_MISSING"]
    checks["source_structure"] = {
        "state": "blocked" if any(item.severity is Severity.BLOCKER for item in findings) else "current",
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "pending_assets": pending_assets,
        "expected_heading": f"{chapter_number}. {chapter.title}",
        "actual_headings": _heading_candidates(body),
    }
    if any(item.severity is Severity.BLOCKER for item in findings):
        actions.append("先修正章节源稿结构与读者组件")
    for name, required, command in (
        ("developmental_report", require_current_developmental_report, "developmental-check"),
        ("humanize_report", require_current_humanize_report, "humanize-check"),
    ):
        if name == "humanize_report" and not _requires_chinese_humanize(workspace):
            checks[name] = {"state": "not_required"}
            continue
        try:
            proof = required(workspace, chapter_number, body, contract_path)
            proof_path = (humanize_report_path if name == "humanize_report" else developmental_report_path)(workspace, chapter_number)
            checks[name] = {"state": "current" if proof is not None else "not_required",
                            "reusable": proof is not None,
                            "path": _relative_path(workspace, proof_path) if proof is not None else None,
                            "reuse_reason": "current_body_contract_and_diagram_semantics" if proof is not None else None}
        except (DevelopmentalEditError, HumanizeReportError) as error:
            state = "stale" if "过期" in str(error) else "missing" if "缺少" in str(error) else "blocked"
            checks[name] = {"state": state, "reason": str(error), "reusable": False}
            actions.append(f"kp {command} {chapter_number} --workspace <工作区> --from <章节源稿>")
    diagram_findings = validate_current_diagram_assets(
        workspace, workspace.root / "chapters" / f"chapter-{chapter_number:02d}.md", body
    )
    checks["diagram_assets"] = {
        "state": "blocked" if any(item.severity is Severity.BLOCKER for item in diagram_findings) else "current",
        "visual_review_required": any(item.severity is Severity.WARNING for item in diagram_findings),
        "findings": [finding.model_dump(mode="json") for finding in diagram_findings],
    }
    if any(item.severity is Severity.BLOCKER for item in diagram_findings):
        actions.append("按当前源稿重建受控图表资产")
    record_path = compliance_path(workspace, chapter_number)
    if not _v_is_file(record_path):
        checks["compliance_mapping"] = {"state": "missing"}
    else:
        try:
            record = ChapterCompliance.model_validate_json(_v_read_text(record_path, encoding="utf-8"))
            current = record.chapter_body_sha256 == _sha256_text(body) and proof_contract_matches(workspace, chapter_number, record.contract_sha256, "compliance", record_path)
            checks["compliance_mapping"] = {"state": "current" if current else "stale"}
        except (OSError, ValueError):
            checks["compliance_mapping"] = {"state": "blocked", "reason": "合规映射无法读取"}
    if checks["compliance_mapping"]["state"] != "current":
        actions.append(f"kp chapter-compliance-template {chapter_number} --workspace <工作区> --from <章节源稿>")
        from knowledge_product_workflow.change_impact import binding_policy_revision
        saved = workspace.root / f"chapters/chapter-{chapter_number:02d}.md"
        if (binding_policy_revision(workspace) == 2 and _v_is_file(saved)
                and parse_markdown_artifact(_v_read_text(saved, encoding="utf-8"))[0].status
                    in {ArtifactStatus.APPROVED, ArtifactStatus.IN_VALIDATION}):
            actions.append(f"kp recheck-chapter {chapter_number} --workspace <工作区> --compliance <合规映射>")
        else:
            actions.append(f"kp submit-chapter {chapter_number} --workspace <工作区> --from <章节源稿> --compliance <合规映射>")
    return report


@_verification_read(transaction_view=True)
def submit_chapter(
    workspace: Workspace, chapter_number: int, source_path: Path, compliance_input: Path
) -> tuple[Path, Path]:
    """Freeze a reader chapter and its current-version compliance mapping together."""
    from knowledge_product_workflow.runtime import authorize_workflow_transition

    authorize_workflow_transition(workspace, "submit-chapter")
    from knowledge_product_workflow.change_impact import binding_policy_revision
    if binding_policy_revision(workspace) == 2 and chapter_number != 1:
        from knowledge_product_workflow.production_dependencies import build_dependency_plan
        dependency_plan = build_dependency_plan(workspace)
        if not dependency_plan.legacy and chapter_number not in dependency_plan.ready_chapters:
            raise ValueError("本章实际依赖尚未就绪；保留稿件，先处理依赖")
    destination = workspace.root / "chapters" / f"chapter-{chapter_number:02d}.md"
    try:
        source = _require_workspace_file(workspace, source_path, label="章节提交源稿")
        mapping_file = _require_workspace_file(workspace, compliance_input, label="章节合规映射文件")
    except (FileNotFoundError, ValueError) as error:
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code="CHAPTER_COMPLIANCE_INPUT_OUTSIDE_WORKSPACE",
            message=str(error),
            next_command=f"kp chapter-compliance-template {chapter_number} --workspace <工作区> --from <工作区内章节源稿>",
        ) from error
    if not _v_is_file(destination):
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code="CHAPTER_DRAFT_REQUIRED",
            message="提交正文前必须先通过 kp draft 创建当前章节输入。",
            next_command=f"kp draft {chapter_number} --workspace <工作区>",
        )
    try:
        draft_metadata, _ = parse_markdown_artifact(_v_read_text(destination, encoding="utf-8"))
    except ValueError as error:
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code="CHAPTER_DRAFT_INVALID",
            message="章节输入缺少受控 frontmatter；请重新运行 kp draft。",
            artifacts=[destination],
            next_command=f"kp draft {chapter_number} --workspace <工作区>",
        ) from error
    if draft_metadata.status is not ArtifactStatus.IN_PRODUCTION:
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code="CHAPTER_DRAFT_NOT_SUBMITTABLE",
            message="章节输入不是可提交的 in_production 草稿；请从当前阶段重新创建或修订。",
            artifacts=[destination],
        )
    mapping = _load_mapping(workspace, chapter_number, mapping_file)
    body = _reader_body_from_source(source).strip() + "\n"
    try:
        contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
    except ChapterContractRequiredError as error:
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code="CHAPTER_CONTRACT_STALE",
            message=str(error),
            artifacts=[destination],
        ) from error
    if not chapter.section_outline:
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code="SECTION_OUTLINE_MIGRATION_REQUIRED",
            message=(
                "当前章节合约缺少已批准的 section_outline；历史书稿可纯派生重渲染，"
                "但重新提交正文前必须修订蓝图。"
            ),
            artifacts=[source, destination],
            next_command="kp amend-outline --workspace <工作区> --from <补齐 section_outline 的蓝图输入>",
        )
    try:
        require_current_content_plan(workspace, chapter_number)
    except ContentPlanError as error:
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code="CONTENT_PLAN_REQUIRED",
            message=str(error),
            artifacts=[source, destination],
            next_command=f"kp content-plan-template {chapter_number} --workspace <工作区>",
        ) from error
    submission = ChapterComplianceSubmission(
        schema_version=1,
        chapter_number=chapter_number,
        chapter_body_sha256=_sha256_text(body),
        contract_sha256=proof_contract_digest(workspace, chapter_number, "compliance"),
        **mapping.model_dump(),
    )
    findings = validate_chapter_submission(workspace, chapter_number, body, submission, path=destination)
    blockers = [item for item in findings if item.severity is Severity.BLOCKER]
    if blockers:
        primary = blockers[0]
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code=primary.code,
            message="；".join(finding.message for finding in blockers[:3]),
            artifacts=[source, mapping_file, destination],
            next_command=f"kp chapter-compliance-template {chapter_number} --workspace <工作区> --from <工作区内章节源稿>",
            findings=findings,
        )
    stale_reports: list[str] = []
    refresh_commands: list[str] = []
    try:
        require_current_developmental_report(workspace, chapter_number, body, contract_path)
    except DevelopmentalEditError as error:
        stale_reports.append(f"发展性报告：{error}")
        refresh_commands.append(f"kp developmental-check {chapter_number} --workspace <工作区> --from <章节源稿>")
    if _requires_chinese_humanize(workspace):
        try:
            require_current_humanize_report(workspace, chapter_number, body, contract_path)
        except HumanizeReportError as error:
            stale_reports.append(f"中文终稿报告：{error}")
            refresh_commands.append(f"kp humanize-check {chapter_number} --workspace <工作区> --from <章节源稿>")
    if stale_reports:
        raise _submission_error(
            workspace,
            chapter_number=chapter_number,
            code="CHAPTER_FINAL_REPORTS_STALE",
            message="；".join(stale_reports),
            artifacts=[source],
            next_command="；".join(refresh_commands),
        )
    sample_marker = workspace.root / "chapters/chapter-01-sample.approved"
    sample_body_changed = False
    if chapter_number == 1 and _v_is_file(sample_marker):
        from knowledge_product_workflow.content_checkpoint import checkpoint_path
        for prior_proof in (compliance_path(workspace, 1), checkpoint_path(workspace, 1)):
            if _v_is_file(prior_proof):
                previous = json.loads(_v_read_text(prior_proof, encoding="utf-8"))
                sample_body_changed = previous.get("chapter_body_sha256") != submission.chapter_body_sha256
                break
    from knowledge_product_workflow.change_impact import binding_policy_revision
    from knowledge_product_workflow.user_decisions import sample_approval_is_current
    if (chapter_number == 1 and _v_is_file(sample_marker) and binding_policy_revision(workspace) == 2
            and not sample_approval_is_current(workspace, body_override=body)):
        sample_body_changed = True
    prior_version = draft_metadata.version
    metadata = ArtifactMetadata(
        book_id=workspace.root.name,
        artifact_type="chapter",
        status=ArtifactStatus.IN_VALIDATION,
        version=prior_version + 1,
        depends_on=[str(contract_path.relative_to(workspace.root))],
    )
    from knowledge_product_workflow.user_decisions import prepare_decision_invalidation
    from knowledge_product_workflow.workspace_transactions import (
        commit_workspace_transaction,
        prepare_workspace_transaction,
    )
    record = ChapterCompliance(**submission.model_dump(), chapter_version=metadata.version)
    output = compliance_path(workspace, chapter_number)
    relative = destination.relative_to(workspace.root).as_posix()
    proof_relative = output.relative_to(workspace.root).as_posix()
    outputs = {relative: render_markdown_artifact(metadata, body).encode("utf-8"),
               proof_relative: record.model_dump_json(indent=2).encode("utf-8")}
    shared = []
    events = []
    if sample_body_changed:
        archive = f"runs/interaction/history/sample-before-body-change-v{prior_version}.approved"
        if not _v_exists(workspace.root / archive):
            outputs[archive] = _v_read_bytes(sample_marker)
            shared.append(archive)
        outputs[sample_marker.relative_to(workspace.root).as_posix()] = None
        shared.append(sample_marker.relative_to(workspace.root).as_posix())
    if chapter_number == 1 and (sample_body_changed or not _v_is_file(sample_marker)):
        decisions, decision_events = prepare_decision_invalidation(workspace, UserDecisionTarget.SAMPLE,
            reason="第一章样章正文已重新提交", include_consumed=sample_body_changed)
        outputs.update(decisions)
        shared.extend(decisions)
        events.extend(decision_events)
    events.append(("chapter_submitted", {"chapter_number": chapter_number,
        "chapter_body_sha256": record.chapter_body_sha256, "contract_sha256": record.contract_sha256,
        "compliance": proof_relative}))
    transaction = prepare_workspace_transaction(workspace, outputs, kind="chapter_submission",
        scope_chapters=[chapter_number], shared_paths=shared, events=events,
        input_bindings={contract_path.relative_to(workspace.root).as_posix(): _sha256_file(contract_path)})
    commit_workspace_transaction(workspace, transaction)
    return destination, output


@_verification_read(transaction_view=True)
def recheck_submitted_chapter(workspace: Workspace, chapter_number: int, mapping_path: Path) -> Path:
    """Refresh reviewed obligations without changing a committed reader version."""
    from knowledge_product_workflow.change_impact import binding_policy_revision
    from knowledge_product_workflow.runtime import authorize_workflow_transition
    from knowledge_product_workflow.workspace_transactions import (
        commit_workspace_transaction,
        prepare_workspace_transaction,
        require_transaction_consistency,
    )
    authorize_workflow_transition(workspace, "recheck-chapter")
    if binding_policy_revision(workspace) != 2:
        raise ValueError("旧书先显式迁移绑定政策；不能静默解释旧证明")
    require_transaction_consistency(workspace, chapters={chapter_number})
    destination = workspace.root / f"chapters/chapter-{chapter_number:02d}.md"
    metadata, body = parse_markdown_artifact(_v_read_text(destination, encoding="utf-8"))
    if metadata.status not in {ArtifactStatus.IN_VALIDATION, ArtifactStatus.APPROVED}:
        raise ValueError("只复核已提交正文；编辑中的章节仍走原提交门")
    body = body.strip() + "\n"
    relative = destination.relative_to(workspace.root).as_posix()
    committed = False
    for path in [*workspace.root.glob("runs/orchestration/transactions/*.json"),
                 *workspace.root.glob("runs/production/commits/*.json")]:
        raw = json.loads(_v_read_text(path, encoding="utf-8"))
        if path.parent.name == "transactions" and raw.get("kind") not in {"chapter_submission", "production"}:
            continue
        item = raw.get("outputs", {}).get(relative)
        if raw.get("state") != "completed" or not item or not item.get("staged"):
            continue
        staged = (workspace.root / item["staged"]).resolve()
        if (not staged.is_relative_to(workspace.root / "runs") or not _v_is_file(staged)
                or _sha256_file(staged) != item.get("sha256")):
            continue
        saved, saved_body = parse_markdown_artifact(_v_read_text(staged, encoding="utf-8"))
        if (saved.book_id == metadata.book_id and saved.version == metadata.version
                and saved_body.strip() + "\n" == body):
            committed = True
            break
    if not committed:
        raise ValueError("正文／版本不等于原始已提交快照；须走正文修订，不能伪作证明更新")
    contract, _ = require_current_chapter_contract(workspace, chapter_number)
    mapping_path = _require_workspace_file(workspace, mapping_path, label="复核合规映射")
    mapping = _load_mapping(workspace, chapter_number, mapping_path)
    record = ChapterCompliance(chapter_number=chapter_number, chapter_version=metadata.version,
        chapter_body_sha256=_sha256_text(body), contract_sha256=proof_contract_digest(workspace, chapter_number, "compliance"),
        **mapping.model_dump())
    findings = validate_chapter_submission(workspace, chapter_number, body, record, path=destination)
    blockers = [f.message for f in findings if f.severity is Severity.BLOCKER]
    if blockers:
        raise ValueError("复核仍有内容阻断：" + "；".join(blockers))
    require_current_content_plan(workspace, chapter_number)
    require_current_developmental_report(workspace, chapter_number, body, contract)
    if _requires_chinese_humanize(workspace):
        require_current_humanize_report(workspace, chapter_number, body, contract)
    output = compliance_path(workspace, chapter_number)
    if _v_is_file(output) and ChapterCompliance.model_validate_json(_v_read_text(output, encoding="utf-8")) == record:
        return output
    outputs = {output.relative_to(workspace.root).as_posix(): record.model_dump_json(indent=2).encode("utf-8")}
    if _v_is_file(output):
        outputs[f"runs/chapter-compliance/history/chapter-{chapter_number:02d}-{_sha256_file(output)}.json"] = _v_read_bytes(output)
    transaction = prepare_workspace_transaction(workspace, outputs, kind="chapter_proof_recheck",
        scope_chapters=[chapter_number], shared_paths=list(outputs),
        input_bindings={relative: _sha256_file(destination), contract.relative_to(workspace.root).as_posix(): _sha256_file(contract),
                        mapping_path.relative_to(workspace.root).as_posix(): _sha256_file(mapping_path)},
        events=[("chapter_proofs_rechecked", {"chapter_number": chapter_number, "chapter_version": metadata.version})])
    commit_workspace_transaction(workspace, transaction)
    return output


@_verification_read(transaction_view=True)
def validate_current_chapter_compliance(workspace: Workspace, chapter_path: Path, *, final_review: bool = False) -> list[QualityFinding]:
    """Admit each public read; reuse checks only in the invocation's input snapshot."""
    return _current_chapter_compliance_in_view(workspace, chapter_path, final_review=final_review)


def validate_current_chapter_compliance_in_view(workspace: Workspace, chapter_path: Path, *, final_review: bool = False):
    """Internal batch read; public callers always create their own admission view."""
    from knowledge_product_workflow.workspace_transactions import has_transaction_read_view

    if not has_transaction_read_view(workspace):
        return validate_current_chapter_compliance(workspace, chapter_path, final_review=final_review)
    return _current_chapter_compliance_in_view(workspace, chapter_path, final_review=final_review)


def _current_chapter_compliance_in_view(workspace: Workspace, chapter_path: Path, *, final_review: bool = False):
    try:
        chapter_number = int(chapter_path.stem.split("-")[1])
        from knowledge_product_workflow.workspace_transactions import (
            require_transaction_consistency_in_view as require_transaction_consistency,
        )
        require_transaction_consistency(workspace, chapters={chapter_number})
    except (IndexError, OSError, ValueError):
        return [_finding("CHAPTER_COMPLIANCE_STALE", chapter_path, workspace, "章节正文无法读取或缺少受控 frontmatter。")]
    context = _v_current_context(workspace)
    compute = lambda: _validated_current_chapter_compliance(workspace, chapter_path, chapter_number)
    base, final = context.memo(("chapter-compliance.validation.v3", chapter_path), compute) if context else compute()
    return [*base, *final] if final_review else base


def _validated_current_chapter_compliance(workspace: Workspace, chapter_path: Path,
                                        chapter_number: int) -> tuple[list[QualityFinding], list[QualityFinding]]:
    """Validate once; derive ordinary and final outcomes from the same complete proof."""
    try:
        _, body = parse_markdown_artifact(_v_read_text(chapter_path, encoding="utf-8"))
    except (IndexError, OSError, ValueError):
        return [_finding("CHAPTER_COMPLIANCE_STALE", chapter_path, workspace, "章节正文无法读取或缺少受控 frontmatter。")], []
    record_path = compliance_path(workspace, chapter_number)
    if not _v_is_file(record_path):
        return [_finding("CHAPTER_COMPLIANCE_MISSING", chapter_path, workspace, "章节缺少当前版本的合规映射。")], []
    try:
        record = ChapterCompliance.model_validate_json(_v_read_text(record_path, encoding="utf-8"))
    except (OSError, ValueError):
        return [_finding("CHAPTER_COMPLIANCE_STALE", chapter_path, workspace, "章节合规映射 JSON 无效。")], []
    if record.chapter_version != parse_markdown_artifact(_v_read_text(chapter_path, encoding="utf-8"))[0].version:
        return [_finding("CHAPTER_COMPLIANCE_STALE", chapter_path, workspace, "章节合规映射绑定的正文版本已过期。")], []
    findings = validate_chapter_submission(
        workspace, chapter_number, body, record, path=chapter_path, include_reader_content=False
    )
    final = []
    try:
        contract_path, _ = require_current_chapter_contract(workspace, chapter_number)
        require_current_content_plan(workspace, chapter_number)
        require_current_developmental_report(workspace, chapter_number, body, contract_path, final_review=True)
    except _FinalStructureReviewPending as error:
        final.append(_finding("DEVELOPMENTAL_REPORT_REQUIRED", chapter_path, workspace, str(error)))
    except (ChapterContractRequiredError, ContentPlanError, DevelopmentalEditError) as error:
        findings.append(
            _finding(
                "DEVELOPMENTAL_REPORT_REQUIRED",
                chapter_path,
                workspace,
                str(error),
            )
        )
    return findings, final
