"""Validation for reader-facing PDF components and local visual assets."""

from __future__ import annotations

import re
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
_SUPPORTED_COMPONENTS = frozenset({"kp-source", "kp-key-takeaway", "kp-practice", "kp-note"})


def _finding(code: str, location: str, line_number: int, message: str) -> QualityFinding:
    return QualityFinding(
        code=code,
        severity=Severity.BLOCKER,
        location=f"{location}:第 {line_number} 行",
        message=message,
    )


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
    resolved = (workspace.root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if not resolved.is_relative_to(asset_root):
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
            open_divs.append({"line": line_number, "classes": classes, "has_link": False})
            continue

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
    return findings
