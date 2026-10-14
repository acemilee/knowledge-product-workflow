"""Validation for reader-facing PDF components and local visual assets."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import unquote

from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.workspace import Workspace

_IMAGE = re.compile(r"!\[[^\]]*\]\((?P<target>[^)\s]+)(?:\s+[^)]*)?\)")
_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(https?://[^)\s]+\)", re.IGNORECASE)
_DIV_START = re.compile(r"^\s*:::\s*\{(?P<attributes>[^}]*)\}\s*$")
_DIV_CLASS = re.compile(r"\.([A-Za-z][A-Za-z0-9_-]*)")
_RAW_TEX = re.compile(r"\\(?:begin|end|input|includegraphics|newcommand|usepackage)\b")
_RAW_HTML = re.compile(r"<\s*/?\s*(?:div|span|script|style|table|img)\b", re.IGNORECASE)
_SUPPORTED_ASSET_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg"})
_SUPPORTED_COMPONENTS = frozenset(
    {"kp-source", "kp-key-takeaway", "kp-practice", "kp-note", "kp-decision", "kp-procedure", "kp-worksheet", "kp-percentage-distribution", "kp-chapter-lead"}
)
_WORKSHEET_LABELS = ("使用说明", "输入", "输出示例", "复盘")
_EXACT_PERCENT = re.compile(r"^(?P<number>\d+(?:\.\d+)?)%$")


def _finding(code: str, location: str, line_number: int, message: str) -> QualityFinding:
    return QualityFinding(
        code=code,
        severity=Severity.BLOCKER,
        location=f"{location}:第 {line_number} 行",
        message=message,
    )


def _validate_percentage_distribution(content: list[str], location: str, line_number: int) -> list[QualityFinding]:
    """Check only the declared share column of one explicit common-population table."""
    if not any(line.strip().startswith("总体：") and line.strip()[3:].strip() for line in content):
        return [_finding("PERCENTAGE_DISTRIBUTION_INVALID", location, line_number, "百分比分配表必须先写明“总体：”及其具体范围。")]
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in content if line.strip().startswith("|")]
    if len(rows) < 3 or "占比" not in rows[0]:
        return [_finding("PERCENTAGE_DISTRIBUTION_INVALID", location, line_number, "百分比分配表必须含表头、分隔行、数据行及“占比”列。")]
    column = rows[0].index("占比")
    if len(rows[1]) != len(rows[0]) or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]):
        return [_finding("PERCENTAGE_DISTRIBUTION_INVALID", location, line_number, "百分比分配表的 Markdown 分隔行无效。")]
    values: list[Decimal] = []
    tolerance = Decimal(0)
    for row in rows[2:]:
        if len(row) != len(rows[0]) or not (match := _EXACT_PERCENT.fullmatch(row[column])):
            return [_finding("PERCENTAGE_DISTRIBUTION_INVALID", location, line_number, "“占比”列只能填写确定百分比，如 40%；范围、阈值和容差应写在普通指标表。")]
        try:
            value = Decimal(match.group("number"))
        except InvalidOperation:
            return [_finding("PERCENTAGE_DISTRIBUTION_INVALID", location, line_number, "“占比”列包含无效数字。")]
        if value < 0 or value > 100:
            return [_finding("PERCENTAGE_DISTRIBUTION_INVALID", location, line_number, "单项占比必须介于 0% 与 100% 之间。")]
        values.append(value)
        tolerance += Decimal("0.5") * Decimal(1).scaleb(value.as_tuple().exponent)
    total = sum(values)
    if abs(total - Decimal(100)) > tolerance:
        return [_finding("PERCENTAGE_TOTAL_INVALID", location, line_number, f"同一总体占比合计 {total}%；按各项显示精度的允许误差为 ±{tolerance}%，应为 100%。")]
    return []


def _validate_image(target: str, location: str, line_number: int, workspace: Workspace) -> list[QualityFinding]:
    cleaned = unquote(target.strip().strip("<>"))
    lowered = cleaned.casefold()
    if lowered.startswith(("http://", "https://", "data:")):
        return [
            _finding(
                "PRESENTATION_REMOTE_ASSET",
                location,
                line_number,
                "读者插图必须是当前工作区 assets/ 中的本地文件，不能在渲染时联网下载。",
            )
        ]
    if lowered.startswith("file:"):
        return [
            _finding(
                "PRESENTATION_ASSET_OUTSIDE_WORKSPACE",
                location,
                line_number,
                "读者插图不能使用 file: URI 或工作区外绝对路径。",
            )
        ]
    candidate = Path(cleaned)
    asset_root = (workspace.root / "assets").resolve()
    base = workspace.root
    if location == "delivery/book.md" and cleaned.startswith("../assets/"):
        base = workspace.root / "delivery"
    parts = cleaned.split("/")
    invalid_parent = ".." in parts and not (
        base == workspace.root / "delivery" and parts[0] == ".." and ".." not in parts[1:]
    )
    resolved = (base / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if (invalid_parent or not resolved.is_relative_to(asset_root)
            or not resolved.is_relative_to(workspace.root.resolve())):
        return [
            _finding(
                "PRESENTATION_ASSET_OUTSIDE_WORKSPACE",
                location,
                line_number,
                "读者插图只能引用当前书稿工作区 assets/ 目录中的文件。",
            )
        ]
    if resolved.suffix.casefold() not in _SUPPORTED_ASSET_EXTENSIONS:
        return [
            _finding(
                "PRESENTATION_ASSET_TYPE_INVALID",
                location,
                line_number,
                "插图格式仅支持 PDF、PNG、JPG 或 JPEG；SVG 必须先受控转换。",
            )
        ]
    if not resolved.is_file():
        return [
            _finding(
                "PRESENTATION_ASSET_MISSING",
                location,
                line_number,
                "插图文件不存在；请在当前书稿 assets/ 目录创建受控静态文件。",
            )
        ]
    return []


def validate_presentation_markdown(
    markdown: str, location: str, workspace: Workspace
) -> list[QualityFinding]:
    """Return deterministic blockers for unsafe or unsupported reader-facing components."""
    findings: list[QualityFinding] = []
    in_code_fence = False
    open_divs: list[dict[str, object]] = []

    for line_number, raw_line in enumerate(markdown.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue
        if stripped == ":::":
            if open_divs:
                frame = open_divs.pop()
                if "kp-source" in frame["classes"] and not frame["has_link"]:
                    findings.append(
                        _finding(
                            "PRESENTATION_SOURCE_CARD_INVALID",
                            location,
                            int(frame["line"]),
                            "来源卡片必须包含读者可见的 HTTPS 超链接标题。",
                        )
                    )
                if "kp-worksheet" in frame["classes"]:
                    contents = "\n".join(frame["content"])
                    missing = [label for label in _WORKSHEET_LABELS if label not in contents]
                    if missing:
                        findings.append(
                            _finding(
                                "PRESENTATION_WORKSHEET_INVALID",
                                location,
                                int(frame["line"]),
                                f"工作表缺少必要字段：{', '.join(missing)}。",
                            )
                        )
                if "kp-percentage-distribution" in frame["classes"]:
                    findings.extend(_validate_percentage_distribution(frame["content"], location, int(frame["line"])))
            continue
        div_start = _DIV_START.match(raw_line)
        if div_start is not None:
            classes = set(_DIV_CLASS.findall(div_start.group("attributes")))
            unknown = sorted(
                class_name
                for class_name in classes
                if class_name.startswith("kp-") and class_name not in _SUPPORTED_COMPONENTS
            )
            if unknown:
                findings.append(
                    _finding(
                        "PRESENTATION_COMPONENT_INVALID",
                        location,
                        line_number,
                        f"未定义的读者组件：{', '.join(unknown)}。",
                    )
                )
            open_divs.append({"line": line_number, "classes": classes, "has_link": False, "content": []})
            continue

        for frame in open_divs:
            frame["content"].append(raw_line)

        if _RAW_TEX.search(raw_line):
            findings.append(
                _finding(
                    "PRESENTATION_RAW_TEX",
                    location,
                    line_number,
                    "读者书稿不能注入原始 TeX；请使用受支持的 Markdown 组件。",
                )
            )
        if _RAW_HTML.search(raw_line):
            findings.append(
                _finding(
                    "PRESENTATION_RAW_HTML",
                    location,
                    line_number,
                    "读者书稿不能注入原始 HTML；请使用受支持的 Markdown 组件。",
                )
            )
        for image in _IMAGE.finditer(raw_line):
            findings.extend(_validate_image(image.group("target"), location, line_number, workspace))
        if _LINK.search(raw_line):
            for frame in open_divs:
                if "kp-source" in frame["classes"]:
                    frame["has_link"] = True

    for frame in open_divs:
        findings.append(
            _finding(
                "PRESENTATION_COMPONENT_INVALID",
                location,
                int(frame["line"]),
                "读者组件缺少结束标记 :::。",
            )
        )
    findings.extend(validate_chapter_lead(markdown, location, workspace))
    return findings


def validate_chapter_lead(markdown: str, location: str, workspace: Workspace,
                          *, required: bool | None = None) -> list[QualityFinding]:
    """One shared chapter-level structure gate; code examples are not components."""
    from knowledge_product_workflow.callouts import (
        Callout,
        CalloutFormatError,
        parse_callout_blocks,
    )
    from knowledge_product_workflow.illustrations import current_profile
    from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract

    try:
        blocks = parse_callout_blocks(markdown)
    except CalloutFormatError as error:
        return [_finding("CHAPTER_LEAD_STRUCTURE_INVALID", location, 1, str(error))]
    visible, in_code = [], False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        elif not in_code:
            visible.append(line)
    headings = [line for line in visible if re.match(r"^#\s+", line)]
    if len(headings) > 1:
        # A whole-book QMD is validated as its real chapter segments, never as one giant chapter.
        segments = re.split(r"(?m)(?=^#\s+\d+[.、 ]\s*)", markdown)
        return [finding for segment in segments if re.match(r"^#\s+\d+", segment)
                for finding in validate_chapter_lead(segment, location, workspace, required=required)]
    leads = [(index, block) for index, block in enumerate(blocks)
             if isinstance(block, Callout) and block.component == "kp-chapter-lead"]
    match = re.search(r"(?m)^#\s+(\d+)[.、 ]", "\n".join(visible))
    chapter_match = match or re.search(r"chapter-(\d+)", location)
    ordinary = True
    if chapter_match:
        try:
            _, chapter = require_current_chapter_contract(workspace, int(chapter_match.group(1)))
            ordinary = chapter.chapter_archetype.value not in {"reference", "appendix"}
        except ValueError:
            pass
    if not ordinary:
        required = False
    elif required is None:
        try:
            required = bool(chapter_match) and current_profile(workspace).reader_layout_revision == 2
        except (OSError, ValueError):
            required = False
    findings = []
    if len(leads) > 1:
        findings.append(_finding("CHAPTER_LEAD_DUPLICATED", location, 1, "每章最多一个导读，包括参考/附录"))
    if required and not leads:
        portable_lead = location == "delivery/book.md" and re.search(r"(?m)^> \*\*本章导读\*\*", markdown)
        if not portable_lead:
            findings.append(_finding("CHAPTER_LEAD_REQUIRED", location, 1, "布局2普通教学章须在H1后、首个H2前填写自然章导读"))
    for index, lead in leads:
        prefix = "\n".join(block for block in blocks[:index] if isinstance(block, str))
        if not re.search(r"(?m)^#\s+", prefix) or re.search(r"(?m)^#{2,6}\s+", prefix):
            findings.append(_finding("CHAPTER_LEAD_POSITION_INVALID", location, lead.line_number,
                                     "章导读只允许在章标题之后、首个小节之前"))
        content = "\n".join(lead.content)
        if re.search(r"(?m)^\s*(?:#{1,6}\s|```|:::|\||[-*+]\s|\d+[.)]\s)", content) or "\n\n" in content:
            findings.append(_finding("CHAPTER_LEAD_STRUCTURE_INVALID", location, lead.line_number,
                                     "导读应为一段自然理解框架，不含新标题、表格、代码或列表"))
        if len(content) > 180:
            findings.append(QualityFinding(code="CHAPTER_LEAD_LENGTH_REVIEW", severity=Severity.WARNING,
                                           location=location, message="导读超过建议长度；检查重复与合页可读性，不以字数证明质量"))
    return findings
