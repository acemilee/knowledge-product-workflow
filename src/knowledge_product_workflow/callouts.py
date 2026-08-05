"""Controlled reader-callout conversion for Markdown and Quarto artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE_START = re.compile(r"^\s*:::\s*\{(?P<attributes>[^}]*)\}\s*$")
_DIV_CLASS = re.compile(r"\.([A-Za-z][A-Za-z0-9_-]*)")
_LEGACY_MARKER = re.compile(r"^\s*\{\.(?P<component>kp-[A-Za-z][A-Za-z0-9_-]*)\}\s*$")
_HEADING = re.compile(r"^#{1,6}\s+")
_INTERNAL_MARKER = re.compile(r"\{\.kp-(?:source|key-takeaway|practice|note)\}")
_FENCE = re.compile(r"^\s*:::")

CALLOUT_LABELS = {
    "kp-note": "提示",
    "kp-key-takeaway": "本章要点",
    "kp-practice": "练习",
    "kp-source": "来源",
}
SUPPORTED_CALLOUTS = frozenset(CALLOUT_LABELS)


class CalloutFormatError(ValueError):
    """Raised when an internal reader component cannot be transformed safely."""


@dataclass(frozen=True)
class Callout:
    component: str
    content: tuple[str, ...]
    line_number: int


def _is_fence(line: str) -> bool:
    return line.strip().startswith("```")


def _component_from_fence(line: str) -> str | None:
    match = _FENCE_START.match(line)
    if match is None:
        return None
    components = [
        value for value in _DIV_CLASS.findall(match.group("attributes")) if value.startswith("kp-")
    ]
    if not components:
        return None
    if len(components) != 1 or components[0] not in SUPPORTED_CALLOUTS:
        raise CalloutFormatError("读者组件必须包含唯一且受支持的 kp-* 类名。")
    return components[0]


def _trim_content(lines: list[str]) -> tuple[str, ...]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise CalloutFormatError("读者组件不能为空。")
    return tuple(lines)


def parse_callout_blocks(markdown: str) -> list[str | Callout]:
    """Parse supported fenced Divs and deterministic legacy marker blocks.

    Legacy markers are accepted only as a standalone line. Their scope ends at
    the next Markdown heading or the next marker, which is the historical KP
    chapter convention. This allows existing frozen chapters to be reassembled
    without mutating chapter text while keeping malformed structures fail-closed.
    """

    lines = markdown.splitlines()
    blocks: list[str | Callout] = []
    ordinary: list[str] = []
    in_code = False
    index = 0

    def flush_ordinary() -> None:
        nonlocal ordinary
        if ordinary:
            blocks.append("\n".join(ordinary))
            ordinary = []

    while index < len(lines):
        line = lines[index]
        if _is_fence(line):
            in_code = not in_code
            ordinary.append(line)
            index += 1
            continue
        if in_code:
            ordinary.append(line)
            index += 1
            continue

        component = _component_from_fence(line)
        if component is not None:
            flush_ordinary()
            start_line = index + 1
            index += 1
            content: list[str] = []
            nested_code = False
            while index < len(lines):
                candidate = lines[index]
                if _is_fence(candidate):
                    nested_code = not nested_code
                    content.append(candidate)
                    index += 1
                    continue
                if not nested_code and candidate.strip() == ":::":
                    index += 1
                    blocks.append(Callout(component, _trim_content(content), start_line))
                    break
                content.append(candidate)
                index += 1
            else:
                raise CalloutFormatError(f"第 {start_line} 行的读者组件缺少结束围栏 :::。")
            continue

        legacy = _LEGACY_MARKER.match(line)
        if legacy is not None:
            component = legacy.group("component")
            if component not in SUPPORTED_CALLOUTS:
                raise CalloutFormatError(f"第 {index + 1} 行使用了不受支持的读者组件：{component}。")
            flush_ordinary()
            start_line = index + 1
            index += 1
            content: list[str] = []
            nested_code = False
            while index < len(lines):
                candidate = lines[index]
                if _is_fence(candidate):
                    nested_code = not nested_code
                    content.append(candidate)
                    index += 1
                    continue
                if not nested_code and (_HEADING.match(candidate) or _LEGACY_MARKER.match(candidate)):
                    break
                content.append(candidate)
                index += 1
            blocks.append(Callout(component, _trim_content(content), start_line))
            continue

        ordinary.append(line)
        index += 1

    flush_ordinary()
    return blocks


def render_reader_markdown(markdown: str) -> str:
    """Create portable reader Markdown without KP styling syntax."""

    rendered: list[str] = []
    for block in parse_callout_blocks(markdown):
        if isinstance(block, str):
            if block.strip():
                rendered.append(block.strip())
            continue
        quote_lines = [f"> **{CALLOUT_LABELS[block.component]}**", ">"]
        quote_lines.extend(">" if not line else f"> {line}" for line in block.content)
        rendered.append("\n".join(quote_lines))
    return "\n\n".join(rendered).strip() + "\n"


def render_qmd_markdown(markdown: str) -> str:
    """Create Quarto Markdown with valid fenced Divs for PDF components."""

    rendered: list[str] = []
    for block in parse_callout_blocks(markdown):
        if isinstance(block, str):
            if block.strip():
                rendered.append(block.strip())
            continue
        rendered.append(
            "\n".join((f"::: {{.{block.component}}}", *block.content, ":::"))
        )
    return "\n\n".join(rendered).strip() + "\n"


def reader_component_leaks(markdown: str, *, allow_fenced_divs: bool) -> list[int]:
    """Return visible-line numbers carrying implementation-only component syntax."""

    leaks: list[int] = []
    in_code = False
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        if _is_fence(line):
            in_code = not in_code
            if not allow_fenced_divs and _FENCE.match(line):
                leaks.append(line_number)
            continue
        if in_code:
            continue
        if allow_fenced_divs and _FENCE_START.match(line):
            try:
                _component_from_fence(line)
            except CalloutFormatError:
                leaks.append(line_number)
            continue
        if _INTERNAL_MARKER.search(line) or (not allow_fenced_divs and _FENCE.match(line)):
            leaks.append(line_number)
    return leaks
