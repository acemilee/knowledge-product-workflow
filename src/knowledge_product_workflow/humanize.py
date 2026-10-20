"""Deterministic guardrails for a Chinese professional-copy editing pass."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from knowledge_product_workflow.change_impact import proof_contract_digest, proof_contract_matches
from knowledge_product_workflow.content_quality import _CODE_FENCE, _INLINE_CODE
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    read_bytes as _v_read_bytes,
)
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.verification_context import verified_read as _verification_read
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workflows.authoring import (
    require_current_chapter_contract_in_view as require_current_chapter_contract,
)
from knowledge_product_workflow.workspace import Workspace

_BOLD = re.compile(r"\*\*[^*\n]+\*\*")
_DECORATIVE_MIDDOT = re.compile(r"\*\*\s*[·•]\s*\*\*")
_SINGLE_EM_DASH = re.compile(r"(?<!—)—(?!—)")
_CORNER_QUOTE = re.compile(r"[「」]")
_FORMULAIC_CONTRAST = re.compile(r"(?:不是|并非).{0,28}(?:而是|而在于)")
_TEMPLATE_LABEL = re.compile(r"(?:规则|标准回答要点|深度答案|可能的追问及应对|本质问题)\s*[:：]")
_LINK = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?:%|ms|秒|分钟|小时|天|周|月|倍|元)?")
_EDITOR_INLINE_CODE = re.compile(_INLINE_CODE.pattern, _INLINE_CODE.flags | re.DOTALL)
_URL = re.compile(r"https?://[^\s<>()，。；！？、：”’]+")
_PATH = re.compile(r"(?<![\w/<])(?:[A-Za-z]:[\\/][^\s<>，。；！？、”’]+|(?:\.{1,2}/|~/)[\w./\\-]+|(?:[\w.-]+/)+[\w.-]+\.[A-Za-z][A-Za-z0-9]{0,6}|/(?:[\w.-]+/)*[\w.-]+)")
HUMANIZE_RULE_SET = "kp-chinese-humanize-editor-v2"


class HumanizeReport(BaseModel):
    """Internal proof that a current Chinese chapter passed the agreed copy-style contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    chapter_number: int = Field(gt=0)
    chapter_body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_fragments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_set: str = "kp-chinese-humanize-editor-v1"
    findings: list[QualityFinding] = Field(default_factory=list)
    diagram_semantics_sha256: str | None = None


class HumanizeReportError(ValueError):
    """Raised when a chapter has not passed a current humanize pass."""


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def fenced_code_regions(markdown: str) -> list[tuple[int, int, str, bool]]:
    """Reuse KP delimiter rules; offsets include language and closing markers."""
    regions = []
    start = offset = 0
    character = ""
    length = 0
    list_indent = 0
    for line in markdown.splitlines(keepends=True):
        logical = re.sub(r"^ {0,3}(?:> ?)+", "", line.rstrip("\r\n"))
        indent = len(logical) - len(logical.lstrip(" "))
        listing = re.match(r"^ *(?:[-+*]|\d+[.)]) +", logical) if not character else None
        if listing:
            list_indent = listing.end()
            logical = logical[list_indent:]
        elif list_indent:
            if logical.strip() and indent < list_indent:
                list_indent = 0
            else:
                logical = logical[list_indent:]
        fence = _CODE_FENCE.match(logical)
        if fence:
            delimiter, suffix = fence.group("fence", "suffix")
            if not character and not (delimiter[0] == "`" and "`" in suffix):
                start, character, length = offset, delimiter[0], len(delimiter)
            elif character == delimiter[0] and len(delimiter) >= length and not suffix.strip():
                regions.append((start, offset + len(line), markdown[start:offset + len(line)], True))
                character = ""
        offset += len(line)
    if character:
        regions.append((start, len(markdown), markdown[start:], False))
    return regions


def _without_fenced_code(markdown: str) -> str:
    for start, end, _, _ in reversed(fenced_code_regions(markdown)):
        markdown = markdown[:start] + "\n" * markdown[start:end].count("\n") + markdown[end:]
    return markdown


def _indented_code_regions(text: str) -> list[tuple[int, int, str]]:
    regions = []
    offset = start = end = 0
    active = False
    previous_blank = True
    list_indent = 0
    for line in text.splitlines(keepends=True):
        blank = not line.strip()
        expanded = line.expandtabs(4)
        indent = len(expanded) - len(expanded.lstrip(" "))
        listing = re.match(r"^ {0,3}(?:[-+*]|\d+[.)]) +", expanded)
        if listing:
            list_indent = listing.end()
        elif not blank and indent < list_indent:
            list_indent = 0
        code = not blank and indent >= list_indent + 4 and (previous_blank or active)
        if code:
            if not active:
                start = offset
            active, end = True, offset + len(line)
        elif not blank and active:
            regions.append((start, end, text[start:end].rstrip("\n")))
            active = False
        previous_blank = blank
        offset += len(line)
    if active:
        regions.append((start, end, text[start:end].rstrip("\n")))
    return regions


def _without_code(markdown: str) -> str:
    text = _without_fenced_code(markdown)
    for start, end, _ in reversed(_indented_code_regions(text)):
        text = text[:start] + "\n" * text[start:end].count("\n") + text[end:]
    return text


def _visible_lines(markdown: str) -> list[str]:
    text = _EDITOR_INLINE_CODE.sub("", _without_code(markdown))
    return [line for line in text.splitlines() if not line.startswith(("    ", "\t"))]


def _link_targets(text: str) -> list[str]:
    targets = []
    for match in re.finditer(r"\]\(", text):
        depth, index = 1, match.end()
        while index < len(text) and text[index] != "\n":
            if text[index] == "\\":
                index += 2
                continue
            depth += (text[index] == "(") - (text[index] == ")")
            if depth == 0:
                targets.append(text[match.end():index])
                break
            index += 1
    targets.extend(re.findall(r"^ {0,3}\[[^\]]+\]:\s*(\S+)", text, re.M))
    return targets


def protected_parts(markdown: str) -> dict[str, list[str]]:
    """Adapt protected_parts from Humanizer-zh f4518a8 (MIT, Copyright 2026 歸藏).

    Original: tests/check_structure.py. Preserve technical payloads, not entire
    table/step wording. This bounded Markdown guard does not prove semantics.
    """
    frontmatter = re.match(r"\A---\n.*?\n---(?:\n|$)", markdown, re.S)
    text = markdown[frontmatter.end():] if frontmatter else markdown
    regions = fenced_code_regions(text)
    indented = _indented_code_regions(_without_fenced_code(text))
    outside = _without_code(text)
    inline = [match.group(0) for match in _EDITOR_INLINE_CODE.finditer(outside)]
    prose = _EDITOR_INLINE_CODE.sub("", outside)
    return {
        "frontmatter": [frontmatter.group(0)] if frontmatter else [],
        "fenced_code": [region[2].rstrip("\n") for region in regions],
        "indented_code": [region[2] for region in indented],
        "inline_code": inline,
        "headings": re.findall(r"^ {0,3}#{1,6} .+$", outside, re.M),
        "link_targets": _link_targets(prose),
        "table_numbers": ["|".join(_NUMBER.findall(line)) for line in prose.splitlines() if _TABLE_ROW.match(line)],
        "ordered_steps": re.findall(r"^([ \t]*\d+[.)])[ \t]+", prose, re.M),
        "explicit_ids": re.findall(r'<[^>]+\bid=[\"\'][^\"\']+[\"\'][^>]*>', prose),
        "component_boundaries": re.findall(r"^[ \t]*:{3,}(?:[ \t]*\{[^\n]*\})?[ \t]*$", prose, re.M),
        "math": re.findall(r"(?<!\\)\$\$[\s\S]*?(?<!\\)\$\$|(?<![\\$])\$(?!\$)[^\n$]+(?<!\\)\$", prose),
        "urls": [value.rstrip(".,;!?") for value in _URL.findall(prose)],
        "paths": _PATH.findall(_URL.sub("", prose)),
    }


def compare_protected_parts(before: str, after: str, location: str) -> list[QualityFinding]:
    original, edited = protected_parts(before), protected_parts(after)
    return [QualityFinding(code="HUMANIZE_PROTECTED_" + key.upper(), severity=Severity.BLOCKER,
                           location=location, message=f"中文编辑改变了受保护内容：{key}；请交回作者修订，不以自然化改变技术载荷。")
            for key in original if original[key] != edited[key]]


def protected_fragments_sha256(markdown: str) -> str:
    """Hash code, links, table numerics, URLs, commands and other no-rewrite fragments."""
    return _sha256_text(json.dumps(protected_parts(markdown), ensure_ascii=False, sort_keys=True))


def scan_chinese_style(markdown: str, location: str) -> list[QualityFinding]:
    """Return concrete editorial findings, never a model-likeness or AI-probability score."""
    findings: list[QualityFinding] = []
    visible = _visible_lines(markdown)
    text = "\n".join(visible)
    line_count = max(1, len([line for line in visible if line.strip()]))

    def add(code: str, message: str) -> None:
        findings.append(QualityFinding(code=code, severity=Severity.WARNING, location=location, message=message))

    if any(not closed for _, _, _, closed in fenced_code_regions(markdown)):
        findings.append(QualityFinding(code="HUMANIZE_UNCLOSED_CODE", severity=Severity.BLOCKER,
                                       location=location, message="代码围栏未闭合；保留原稿并交回作者修复。"))
    depth = 0
    invalid_div = False
    for line in _without_code(markdown).splitlines():
        if re.match(r"^\s*:{3,}\s*\{[^\n]*\}\s*$", line):
            depth += 1
        elif re.match(r"^\s*:{3,}\s*$", line):
            depth -= 1
            invalid_div |= depth < 0
    if depth or invalid_div:
        findings.append(QualityFinding(code="HUMANIZE_COMPONENT_BOUNDARY", severity=Severity.BLOCKER,
                                       location=location, message="KP组件边界不完整；不能将组件改写成裸标记。"))

    if _CORNER_QUOTE.search(text):
        add("CHINESE_STYLE_CORNER_QUOTES", "中文专业教程默认使用“”与《》；直角引号只可保留在明确的原始引文或日文材料中。")
    if _DECORATIVE_MIDDOT.search(text):
        add("CHINESE_STYLE_DECORATIVE_MIDDOT", "检测到装饰性加粗中间点；请删除该伪分隔符。")
    if _SINGLE_EM_DASH.search(text):
        add("CHINESE_STYLE_SINGLE_DASH", "检测到单破折号；请拆句、改用逗号/句号，确有插入语时统一使用——。")
    if len(_BOLD.findall(text)) > max(12, line_count // 8):
        add("CHINESE_STYLE_EXCESSIVE_BOLD", "粗体密度过高；只保留真正需要读者扫读的少量关键词。")
    dash_count = text.count("——")
    if dash_count > max(12, line_count // 8):
        add("CHINESE_STYLE_EXCESSIVE_DASH", "破折号密度过高；将解释并入主句或拆成自然段。")
    if len(_FORMULAIC_CONTRAST.findall(text)) > 3:
        add("CHINESE_STYLE_FORMULAIC_CONTRAST", "“不是……而是……”式对偶过多；请改为直接、具体的说明。")
    if len(_TEMPLATE_LABEL.findall(text)) > 5:
        add("CHINESE_STYLE_TEMPLATE_LABEL_OVERUSE", "规则/标准回答/深度答案等模板标签过多；请保留必要的教学结构，删除量产式前缀。")
    return findings


def humanize_report_path(workspace: Workspace, chapter_number: int) -> Path:
    return workspace.root / "runs" / "humanize" / f"chapter-{chapter_number:02d}.json"


def _workspace_file(workspace: Workspace, path: Path) -> Path:
    path = path.expanduser().absolute()
    root = workspace.root.resolve()
    if not path.resolve().is_relative_to(root):
        raise HumanizeReportError("编辑前稿／后稿必须位于当前书稿工作区内。")
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise HumanizeReportError("编辑前稿／后稿不得通过链接跳转。")
        if part == root:
            break
    if not _v_is_file(path):
        raise HumanizeReportError(f"找不到待编辑章节文件：{path}")
    return path


@_verification_read(transaction_view=True)
def record_humanize_check(workspace: Workspace, chapter_number: int, source_path: Path,
                          *, before_path: Path | None = None) -> tuple[Path, HumanizeReport]:
    """Record the final editor check for a source body before controlled chapter submission."""
    from knowledge_product_workflow.runtime import require_workflow_mutation_allowed
    require_workflow_mutation_allowed(workspace, "humanize-check")
    source_path = _workspace_file(workspace, source_path)
    contract_path, _ = require_current_chapter_contract(workspace, chapter_number)
    text = _v_read_text(source_path, encoding="utf-8")
    preservation = compare_protected_parts(_v_read_text(_workspace_file(workspace, before_path), encoding="utf-8"),
                                           text, str(source_path)) if before_path is not None else []
    try:
        _, body = parse_markdown_artifact(text)
    except ValueError:
        body = text
    body = body.strip() + "\n"
    from knowledge_product_workflow.diagrams import diagram_semantics_digest

    output = humanize_report_path(workspace, chapter_number)
    if not preservation:
        try:
            return output, require_current_humanize_report(workspace, chapter_number, body, contract_path)
        except HumanizeReportError:
            pass

    report = HumanizeReport(
        chapter_number=chapter_number,
        chapter_body_sha256=_sha256_text(body),
        contract_sha256=proof_contract_digest(workspace, chapter_number, "humanize"),
        protected_fragments_sha256=protected_fragments_sha256(body),
        rule_set=HUMANIZE_RULE_SET,
        findings=[*scan_chinese_style(body, str(source_path)), *preservation],
        diagram_semantics_sha256=diagram_semantics_digest(workspace, body),
    )
    _v_mkdir(output.parent,parents=True,exist_ok=True)
    _v_write_text(output, report.model_dump_json(indent=2), encoding="utf-8")
    workspace.append_event(
        "chinese_humanize_checked",
        {
            "chapter_number": chapter_number,
            "chapter_body_sha256": report.chapter_body_sha256,
            "contract_audit_sha256": sha256(_v_read_bytes(contract_path)).hexdigest(),
            "finding_count": len(report.findings),
        },
    )
    from knowledge_product_workflow.production_dependencies import finalize_serial_exports
    finalize_serial_exports(workspace, chapter_number=chapter_number)
    return output, report


def require_current_humanize_report(
    workspace: Workspace, chapter_number: int, body: str, contract_path: Path
) -> HumanizeReport:
    """Reject a Chinese submission when its final editor report is missing, stale or still blocked."""
    path = humanize_report_path(workspace, chapter_number)
    if not _v_is_file(path):
        raise HumanizeReportError("缺少 kp-chinese-humanize-editor 的当前编辑报告。")
    try:
        raw = json.loads(_v_read_text(path, encoding="utf-8"))
        report = HumanizeReport.model_validate(raw)
    except (OSError, ValueError) as error:
        raise HumanizeReportError("中文终稿编辑报告无效。") from error
    if raw.get("rule_set") != HUMANIZE_RULE_SET or report.protected_fragments_sha256 != protected_fragments_sha256(body):
        raise HumanizeReportError("中文终稿编辑报告已过期：规则或当前技术保护摘要不匹配。")
    if report.chapter_number != chapter_number:
        raise HumanizeReportError("中文终稿编辑报告的章节号不一致。")
    if report.chapter_body_sha256 != _sha256_text(body):
        raise HumanizeReportError("中文终稿编辑报告已过期：它不再绑定当前正文。")
    if not proof_contract_matches(workspace, chapter_number, report.contract_sha256, "humanize", path):
        raise HumanizeReportError("中文终稿编辑报告已过期：它不再绑定当前章节合约。")
    from knowledge_product_workflow.diagrams import diagram_semantics_digest

    if report.diagram_semantics_sha256 != diagram_semantics_digest(workspace, body):
        raise HumanizeReportError("中文终稿编辑报告已过期：图内标签或关系已变化。")
    if any(finding.severity is Severity.BLOCKER for finding in report.findings):
        raise HumanizeReportError("中文终稿编辑报告仍包含阻断项。")
    return report
