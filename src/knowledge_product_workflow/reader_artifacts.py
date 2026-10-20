"""Resolve declared reader resources and exact excerpts; never execute or approve them."""
from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from knowledge_product_workflow.content_plan import require_current_content_plan, teaching_focus
from knowledge_product_workflow.humanize import fenced_code_regions
from knowledge_product_workflow.verification_context import is_file, read_bytes
from knowledge_product_workflow.workspace import Workspace


class ReaderResourceError(ValueError):
    """A declared resource or excerpt is missing, unsafe, or inconsistent."""


def reader_presentation_guidance(workspace: Workspace) -> dict:
    """One bounded instruction shared by the existing author and editor, not a new role."""
    focus = teaching_focus(workspace)
    if focus is None:
        return {}
    primary = {
        "method_practice": "正文保留完整输入、提示、输出/失败对照和判断，完整运行器按code_delivery交配套资源；读者理解方法不以先读懂文件I/O、CLI或校验框架为前提。",
        "implementation": "读者当前任务就是实现时，可保留必要完整实现；先交代运行目标，再分段解释关键机制与核对点，不为追求短代码删掉必要逻辑。",
        "mixed": "按本节读者任务和code_delivery选择正文实现或资源片段；方法说明与实现查阅分层，每种保留都说明新增教学价值。",
    }
    return {"teaching_focus": focus, "primary_guidance": primary[focus],
        "reading_rhythm": "按信息关系选择连贯解释、比较表、步骤、图和练习；核心问题/重点/例外/下一步易定位。提示与相关输入输出相邻，难点前给读者任务，练习后有参考与迁移。不强制每节相同卡片、图表或段落配额。",
        "program_review_threshold_lines": 40,
        "threshold_policy": "超过40程序逻辑行只定位复核；须说明保留或外置的具体教学价值。不将提示/JSON数据一概计为程序，不靠拆围栏、藏附录或截图代码达标。",
        "completion": "完整交付不等于正文全文内嵌。程序外置后仍保留必要解释、完整方法示例与练习；资源缺失、片段错绑或未取得所声称的执行证据不得通过。"}


@dataclass(frozen=True)
class ReaderFence:
    start: int
    end: int
    line: int
    language: str
    code: str
    closed: bool


def reader_fences(body: str) -> list[ReaderFence]:
    result = []
    for start, end, raw, closed in fenced_code_regions(body):
        lines = raw.splitlines()
        prefix = re.match(r"^ {0,3}(?:> ?)+", lines[0])
        quote = prefix.group() if prefix else ""
        logical = [line[len(quote):] if quote and line.startswith(quote) else line for line in lines]
        opening = re.match(r"^\s*(?:`{3,}|~{3,})([^\s]*)", logical[0])
        language = opening[1].casefold() if opening else ""
        code = "\n".join(logical[1:-1] if closed else logical[1:]) + "\n"
        result.append(ReaderFence(start, end, body.count("\n", 0, start) + 2, language, code, closed))
    return result


def reader_presentation_metrics(body: str) -> dict:
    """Describe visible payload, not learning quality; splitting fences keeps totals."""
    programs = {"python", "py", "javascript", "js", "typescript", "ts", "c", "cpp", "c++",
                "java", "go", "rust", "ruby", "sql"}
    counts = {kind: 0 for kind in ("program", "prompt_or_text", "data", "command", "unclassified")}
    lines = dict.fromkeys(counts, 0)
    program_items = []
    for index, fence in enumerate(reader_fences(body), 1):
        language = fence.language
        kind = ("program" if language in programs else "data" if language in {"json", "yaml", "yml", "csv", "xml"}
                else "command" if language in {"bash", "sh", "shell", "powershell", "ps1", "console", "cmd"}
                else "prompt_or_text" if language in {"text", "txt", "prompt", "markdown", "md"} else "unclassified")
        displayed = sum(bool(line.strip()) for line in fence.code.splitlines())
        counts[kind] += 1
        lines[kind] += displayed
        if kind == "program":
            logical = None
            if language in {"python", "py"}:
                try:
                    logical = sum(token.type == tokenize.NEWLINE for token in tokenize.generate_tokens(io.StringIO(fence.code).readline))
                except (tokenize.TokenError, IndentationError, SyntaxError):
                    pass
            program_items.append({"fence_index": index, "source_line": fence.line, "language": language,
                                  "display_nonblank_lines": displayed, "python_logical_lines": logical,
                                  "closed": fence.closed})
    return {"fence_counts": counts, "display_nonblank_lines": lines, "program_fences": program_items,
            "aggregate_program_display_over_40": lines["program"] > 40,
            "scope": "全稿聚合，拆围栏不降低程序总量；按语言标签分类，未标注不猜测。Python逻辑行来自词法NEWLINE，其他语言只量展示行。",
            "interpretation": "仅定位阅读负担，非硬上限或教学质量结论；完整提示、输入输出和练习不能为降低数字而删除。实际连续成页和相邻列仍须看当前PDF。"}


def _asset(workspace: Workspace, relative: str) -> tuple[Path, bytes]:
    assets = (workspace.root / "assets").resolve()
    path = (workspace.root / relative).resolve()
    if (not assets.is_relative_to(workspace.root.resolve()) or not path.is_relative_to(assets)
            or not is_file(path)):
        raise ReaderResourceError(f"读者资源缺失或越出当前assets：{relative}")
    return path, read_bytes(path)


def resolve_reader_resources(workspace: Workspace, body: str, *, chapter_number: int | None = None,
                             partial_scope: bool = False) -> list[dict]:
    """Bind a chapter or assembled reader book; returned hashes are observations, not proof."""
    if teaching_focus(workspace) is None:
        return []
    fences = reader_fences(body)
    masked = body
    for fence in reversed(fences):
        raw = masked[fence.start:fence.end]
        masked = masked[:fence.start] + re.sub(r"[^\n]", " ", raw) + masked[fence.end:]
    headings = list(re.finditer(r"^#{2,6}\s+(\d+(?:\.\d+)+)\s+.+$", masked, re.MULTILINE))
    numbers = {chapter_number} if chapter_number is not None else {int(h[1].split(".")[0]) for h in headings}
    result = []
    for number in sorted(numbers):
        plan = require_current_content_plan(workspace, number)
        if plan is None:
            continue
        for section in plan.sections:
            delivery = section.code_delivery
            if delivery is None or delivery.mode == "inline_complete":
                continue
            matches = [h for h in headings if h[1] == section.section_id]
            if not matches and partial_scope:
                continue
            if len(matches) != 1:
                raise ReaderResourceError(f"资源小节缺失或重复：{section.section_id}")
            heading = matches[0]
            level = len(heading[0].split()[0])
            end = next((h.start() for h in headings if h.start() > heading.start()
                        and len(h[0].split()[0]) <= level), len(body))
            if chapter_number is None:
                chapter_end = re.search(r"^#\s+\d+\.\s", masked[heading.end():], re.MULTILINE)
                if chapter_end:
                    end = min(end, heading.end() + chapter_end.start())
            scope = body[heading.end():end]
            assert delivery.resource_path is not None  # Validated by CodeDeliveryPlan.
            if delivery.resource_path not in scope:
                raise ReaderResourceError(f"小节{section.section_id}缺少读者资源路径：{delivery.resource_path}")
            path, raw = _asset(workspace, delivery.resource_path)
            try:
                source = raw.decode("utf-8-sig")
                if path.suffix.casefold() == ".py":
                    ast.parse(source, filename=delivery.resource_path)
            except (UnicodeError, SyntaxError) as error:
                raise ReaderResourceError(f"完整程序资源编码或Python语法无效：{delivery.resource_path}") from error
            inputs = {delivery.resource_path: sha256(raw).hexdigest()}
            for relative in delivery.input_files:
                _, data = _asset(workspace, relative)
                inputs[relative] = sha256(data).hexdigest()
            local_fences = [f for f in fences if heading.end() <= f.start < end]
            spans = []
            source_lines = source.splitlines()
            for excerpt in delivery.excerpts:
                if excerpt.body_fence_index > len(local_fences) or excerpt.end_line > len(source_lines):
                    raise ReaderResourceError(f"小节{section.section_id}片段围栏或源行范围不存在")
                fence = local_fences[excerpt.body_fence_index - 1]
                expected = "\n".join(source_lines[excerpt.start_line - 1:excerpt.end_line]) + "\n"
                languages = {".py": {"python", "py"}, ".js": {"javascript", "js"},
                    ".ts": {"typescript", "ts"}, ".cpp": {"cpp", "c++"}, ".c": {"c"},
                    ".java": {"java"}, ".ps1": {"powershell"}, ".sh": {"bash", "sh"},
                    ".sql": {"sql"}}
                if (not fence.closed or fence.language not in languages.get(path.suffix.casefold(), set())
                        or fence.code != expected):
                    raise ReaderResourceError(f"小节{section.section_id}正文片段与当前完整资源不一致")
                spans.append((fence.start, fence.end))
            result.append({"chapter_number": number, "section_id": section.section_id,
                "mode": delivery.mode, "resource_path": delivery.resource_path,
                "input_files": list(delivery.input_files), "inputs": inputs, "excerpt_spans": spans,
                "syntax_check": "python_ast" if path.suffix.casefold() == ".py" else "not_verified",
                "execution_verified": False})
    return result
