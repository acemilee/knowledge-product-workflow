"""Deterministic reader-visible Markdown quality checks."""

from __future__ import annotations

import ast
import re
import unicodedata
from collections.abc import Iterable

from knowledge_product_workflow.quality import QualityFinding, Severity

_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://[^\s)>]+")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MARKDOWN_DECORATION = re.compile(r"[`*_~>#|]+")
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_TEMPLATE_PATTERNS = (
    re.compile(r"\{\{[^{}]+\}\}"),
    re.compile(r"\[\[[^\]]+\]\]"),
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bTBD\b", re.IGNORECASE),
    re.compile(r"待补充|待填写"),
)
_TRACE_PATTERNS = (
    re.compile(r"claim-", re.IGNORECASE),
    re.compile(r"source-ledger\.jsonl", re.IGNORECASE),
    re.compile(r"runs/events\.jsonl", re.IGNORECASE),
    re.compile(r"chapter-\d\d-contract\.md", re.IGNORECASE),
)
_INTERNAL_SOURCE_PATTERNS = (
    re.compile(r"来源台账", re.IGNORECASE),
    re.compile(r"\bsource\s*ledger\b", re.IGNORECASE),
    re.compile(r"^\s*(?:来源编号|来源\s*id|source(?:\s*id)?)\s*[:：]\s*\S", re.IGNORECASE),
)
_FORBIDDEN_READER_HEADINGS = frozenset({"正文", "来源", "来源台账", "必需来源", "内部说明"})
_INLINE_HEADING = re.compile(r"\S\s+#{2,6}\s+\S")
_HORIZONTAL_RULE = re.compile(r"^\s*(?:---+|\*\s*\*\s*\*|___+)\s*$")
HIGH_RISK_FACTUAL_PATTERNS = (
    re.compile(r"真实\s*(?:JD|招聘|岗位描述|面试官|公司)", re.IGNORECASE),
    re.compile(r"大多数\s*(?:公司|企业|招聘方|面试官|岗位)", re.IGNORECASE),
    re.compile(r"典型\s*(?:权重|流程|标准|薪资|面试)", re.IGNORECASE),
    re.compile(r"一位\s*(?:面试官|招聘经理|用人经理).{0,24}(?:告诉我|表示|说)", re.IGNORECASE),
)
_CASE_LABEL = re.compile(r"^\s*(?:情境案例|虚构案例|示例|假设)(?:[：:]|\s)", re.IGNORECASE)
_QUANTIFIED_RESUME_EXAMPLE = re.compile(
    r"(?:简历|项目经历|STAR|成果描述).{0,120}?\d+(?:\.\d+)?\s*(?:%|ms|秒|人|天|周|月|倍|万)",
    re.IGNORECASE | re.DOTALL,
)
_RESUME_EVIDENCE_BOUNDARY = re.compile(
    r"(?:(?:真实|本人).{0,16}(?:经历|项目).{0,32}(?:可核验|可验证|如实|不得编造)|"
    r"(?:可核验|可验证).{0,32}(?:真实|本人).{0,16}(?:经历|项目)|"
    r"不得编造.{0,32}(?:指标|数据|结果|经历))",
    re.IGNORECASE | re.DOTALL,
)
_MIN_NORMALIZED_PARAGRAPH_LENGTH = 80
_DUPLICATE_THRESHOLD = 0.90
_INLINE_CODE = re.compile(r"(?<!`)(?P<ticks>`+)(?!`)(?P<expression>.*?)(?<!`)(?P=ticks)(?!`)")
_CODE_FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<suffix>.*)$")
_MATH_TYPESETTING = re.compile(r"[‖√∑∏∫×÷]|\w\s*·\s*\w|[¹²³⁰⁴⁵⁶⁷⁸⁹].*[+=−-]")
_AMBIGUOUS_MATH_EQUATION = re.compile(r"(?<![<>=!:])=(?!=)")
_ASCII_ARITHMETIC = re.compile(r"[\w)]\s*[+*/^−-]\s*[\w(]")
_RELATIVE_FILE_PATH = re.compile(r"^[^\s]+/[^\s]+\.[A-Za-z][A-Za-z0-9]{0,6}$")
_SHELL_OPTION = re.compile(r"^[A-Za-z0-9_.-]+\s+--?[A-Za-z][\w-]*(?:=|\s|$)")
_STRING_LITERAL = re.compile(r'''"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*' ''', re.VERBOSE)
_COMMAND_OR_PATH = re.compile(
    r"^(?:https?://|[A-Za-z]:[\\/]|\.{0,2}/|~/|--?|"
    r"(?:python(?:\d(?:\.\d+)?)?|py|pwsh|powershell|bash|sh|echo|git|kp|curl|pip|uv|npm|node)\s)",
    re.IGNORECASE,
)


def heading_navigation_guidance() -> dict:
    """Shared naming advice, not a linguistic classifier or a new approval."""
    return {
        "title_rule": "章名默认以主题/核心概念短语为入口，加必要限定以区分同级范围；不要整树都写成先/把/让的任务流水。",
        "action_rule": "学习路径仍任务导向；完整动作保留learning_objectives、reader_job和导读，不把任务句直接当title。",
        "exceptions": "必要短问题、操作项与长术语可保留；不能短成基础/实战等空泛词，也不删范围/否定/参数。",
        "review": "原蓝图批准前审完整目录与主题对应；批准后不自行改名，旧书warning不改变已有证明。",
        "examples": [{"task": "从一句中文到下一枚token", "topic": "分词与解码"},
                     {"task": "在相同领域任务上实践QLoRA", "topic": "QLoRA量化微调"}],
        "automatic_semantic_approval": False,
        "display_length_candidate_over": 24,
    }


def heading_navigation_diagnostics(headings: list[dict], *, language: str) -> list[dict]:
    """Linear title-only candidates; never edit, reject, or approve semantics."""
    result = []
    generic = {"基础", "概述", "实战", "入门", "核心概念", "知识点", "机制", "方法"}
    for row in headings:
        title = unicodedata.normalize("NFC", str(row["title"])).strip()
        visible = re.sub(r"^\d+(?:\.\d+)*[.)、]?\s+", "", title).replace("`", "").replace("*", "")
        length = sum(1 if unicodedata.east_asian_width(c) in {"W", "F"} else .5 for c in visible)
        signals = []
        if visible in generic:
            signals.append("generic_topic")
        if language == "zh-CN" and re.match(r"^(?:先|把|让|给|完成一次|用.{1,28}(?:完成|建立|修复|量化)|在.{1,28}实践)", visible):
            signals.append("task_sentence")
        if length > 24:
            signals.append("long_label")
        for signal in signals:
            result.append({"number": str(row["number"]), "location": row.get("location", str(row["number"])),
                           "signal": signal, "severity": "warning", "evidence": title[:120],
                           "message": "核主题是否清楚、同级是否可区分；保留必要条件，不按词性/字数自动判定或改名。"})
    return result


def find_math_code_findings(markdown: str, location: str) -> list[QualityFinding]:
    """Locate high-confidence typeset calculations without editing any payload."""
    findings: list[QualityFinding] = []
    fence_character = ""
    fence_length = 0
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        logical_line = re.sub(r"^ {0,3}(?:> ?)+", "", line)
        logical_line = re.sub(r"^ {0,3}(?:[-+*]|\d+[.)])\s+", "", logical_line)
        fence = _CODE_FENCE.match(logical_line)
        if fence is not None:
            delimiter = fence.group("fence")
            if not fence_character:
                fence_character, fence_length = delimiter[0], len(delimiter)
            elif (delimiter[0] == fence_character and len(delimiter) >= fence_length
                  and not fence.group("suffix").strip()):
                fence_character = ""
            continue
        if fence_character or line.startswith(("    ", "\t")):
            continue
        for match in _INLINE_CODE.finditer(line):
            expression = match.group("expression")
            stripped = expression.strip()
            # Backslashes/dollars denote source-level LaTeX teaching here.
            if (not stripped or "\\" in stripped or "$" in stripped
                    or _COMMAND_OR_PATH.search(stripped) or _RELATIVE_FILE_PATH.fullmatch(stripped)
                    or _SHELL_OPTION.search(stripped)):
                continue
            try:
                ast.parse(stripped, mode="exec")
            except (SyntaxError, ValueError):
                pass
            else:
                continue  # Real Python, including string literals and assignments.
            unquoted = _STRING_LITERAL.sub("", stripped)
            high_confidence = bool(_MATH_TYPESETTING.search(unquoted))
            ambiguous = bool(_AMBIGUOUS_MATH_EQUATION.search(unquoted) and _ASCII_ARITHMETIC.search(unquoted))
            if not high_confidence and not ambiguous:
                continue
            findings.append(QualityFinding(
                code="MATH_EXPRESSION_AS_CODE", severity=Severity.BLOCKER if high_confidence else Severity.WARNING,
                location=f"{location}:第 {line_number} 行，第 {match.start('expression') + 1} 列",
                message=(f"{'手算数学表达' if high_confidence else '疑似数学表达，需编辑人工确认'}被标记为 Code：{expression}；"
                         "请通过正文修订核对数学标记，保持数值、算法和程序数据不变。"),
            ))
    return findings


def _visible_line(line: str) -> str:
    return _URL.sub("", _MARKDOWN_LINK.sub(r"\1", line))


def _normalize(text: str) -> str:
    return _NON_WORD.sub("", _MARKDOWN_DECORATION.sub("", text)).lower()


def _trigrams(text: str) -> set[str]:
    return {text[index : index + 3] for index in range(len(text) - 2)}


def _jaccard(left: str, right: str) -> float:
    left_trigrams = _trigrams(left)
    right_trigrams = _trigrams(right)
    if not left_trigrams or not right_trigrams:
        return 0.0
    return len(left_trigrams & right_trigrams) / len(left_trigrams | right_trigrams)


def _paragraphs(lines: Iterable[tuple[int, str]]) -> list[tuple[int, str]]:
    paragraphs: list[tuple[int, str]] = []
    current: list[str] = []
    start_line = 0

    def flush() -> None:
        nonlocal current, start_line
        if current:
            paragraphs.append((start_line, " ".join(current)))
        current = []
        start_line = 0

    for line_number, line in lines:
        if not line.strip() or _HEADING.match(line):
            flush()
            continue
        if not current:
            start_line = line_number
        current.append(line.strip())
    flush()
    return paragraphs


def high_risk_factual_lines(markdown: str) -> list[tuple[int, str]]:
    """Return visible factual assertions that need a declared, sourced Claim mapping."""
    matches: list[tuple[int, str]] = []
    in_code_fence = False
    for line_number, raw_line in enumerate(markdown.splitlines(), start=1):
        if raw_line.strip().startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue
        line = _visible_line(raw_line)
        segments = [segment.strip().replace("**", "") for segment in re.split(r"[。；;]", line)]
        if any(not _CASE_LABEL.search(segment) and any(pattern.search(segment) for pattern in HIGH_RISK_FACTUAL_PATTERNS) for segment in segments):
            matches.append((line_number, line))
    return matches


def scan_markdown_content(markdown: str, location: str) -> list[QualityFinding]:
    """Return blockers for reader-visible quality, trace leakage, and factual safety rules."""
    findings = find_math_code_findings(markdown, location)
    visible_lines: list[tuple[int, str]] = []
    headings: dict[tuple[str, int, str], int] = {}
    chapter_scope = ""
    navigation_rows = []
    fence_character = ""
    fence_length = 0

    for line_number, raw_line in enumerate(markdown.splitlines(), start=1):
        stripped = raw_line.strip()
        fence = _CODE_FENCE.match(raw_line)
        if fence is not None:
            delimiter = fence.group('fence')
            if not fence_character:
                fence_character, fence_length = delimiter[0], len(delimiter)
            elif (delimiter[0] == fence_character and len(delimiter) >= fence_length
                  and not fence.group('suffix').strip()):
                fence_character = ""
            continue
        if fence_character:
            continue
        line = _visible_line(raw_line)
        visible_lines.append((line_number, line))
        reference_text = re.sub(r'`+[^`\n]*`+', ' ', line)
        references = list(re.finditer(r'图\s*\d+(?:[-－]\d+)?', reference_text))
        manual = any(not re.search(r'(?:原书|原著|外部资料|引用材料)[^。！？；\n图]{0,24}$',
                                  reference_text[max(0, item.start()-30):item.start()]) for item in references)
        if manual and not stripped.startswith(('>', '![')):
            findings.append(QualityFinding(code='MANUAL_FIGURE_REFERENCE',severity=Severity.BLOCKER,
                location=f'{location}:第 {line_number} 行',message='本书图引用使用语义图名，不在正文手写编号；出版层统一编号。外部原著图号与代码示例须明确其引用范围。'))

        if _HORIZONTAL_RULE.match(line):
            findings.append(
                QualityFinding(
                    code="HORIZONTAL_RULE_DETECTED",
                    severity=Severity.BLOCKER,
                    location=f"{location}:第 {line_number} 行",
                    message="检测到读者正文中的水平分隔线；请使用具体标题或自然段组织内容。",
                )
            )

        heading = _HEADING.match(line)
        if heading is not None:
            level = len(heading.group(1))
            heading_text = heading.group(2).strip()
            numbered = re.match(r"(\d+(?:\.\d+)*)[.]?\s+(.+)", heading_text)
            if numbered:
                navigation_rows.append({"number": numbered[1], "level": level, "title": numbered[2],
                                        "location": f"{location}:第 {line_number} 行", "parent_number": None})
            normalized_heading = _normalize(heading_text)
            if level == 1:
                chapter_scope = normalized_heading
            key = (chapter_scope, level, normalized_heading)
            if normalized_heading and key in headings:
                findings.append(
                    QualityFinding(
                        code="DUPLICATE_HEADING_DETECTED",
                        severity=Severity.BLOCKER,
                        location=f"{location}:第 {line_number} 行",
                        message=f"同级标题与第 {headings[key]} 行重复，请合并或重命名其中一节。",
                    )
                )
            else:
                headings[key] = line_number
            if heading_text.casefold() in _FORBIDDEN_READER_HEADINGS:
                findings.append(
                    QualityFinding(
                        code="FORBIDDEN_READER_HEADING",
                        severity=Severity.BLOCKER,
                        location=f"{location}:第 {line_number} 行",
                        message="检测到面向读者的泛化或内部标题，请改为具体的主题标题。",
                    )
                )
        elif _INLINE_HEADING.search(line):
            findings.append(
                QualityFinding(
                    code="MALFORMED_MARKDOWN_STRUCTURE",
                    severity=Severity.BLOCKER,
                    location=f"{location}:第 {line_number} 行",
                    message="检测到行内 Markdown 标题；请将标题另起一行，并使其描述紧随的内容主题。",
                )
            )

        if any(pattern.search(line) for pattern in _TEMPLATE_PATTERNS):
            findings.append(
                QualityFinding(
                    code="UNRESOLVED_TEMPLATE_DETECTED",
                    severity=Severity.BLOCKER,
                    location=f"{location}:第 {line_number} 行",
                    message="检测到未替换的模板或占位符，请补全后再交付。",
                )
            )
        if any(pattern.search(line) for pattern in _TRACE_PATTERNS):
            findings.append(
                QualityFinding(
                    code="INTERNAL_TRACE_LEAKED",
                    severity=Severity.BLOCKER,
                    location=f"{location}:第 {line_number} 行",
                    message="检测到面向读者泄漏的内部追溯标识，请改为常规引用或参考资料。",
                )
            )
        if any(pattern.search(line) for pattern in _INTERNAL_SOURCE_PATTERNS):
            findings.append(
                QualityFinding(
                    code="INTERNAL_SOURCE_LEAKED",
                    severity=Severity.BLOCKER,
                    location=f"{location}:第 {line_number} 行",
                    message="检测到读者正文中的来源台账或内部来源标签，请移出最终书稿。",
                )
            )
    candidates: list[tuple[int, str]] = []
    for line_number, paragraph in _paragraphs(visible_lines):
        normalized = _normalize(paragraph)
        if len(normalized) >= _MIN_NORMALIZED_PARAGRAPH_LENGTH:
            candidates.append((line_number, normalized))
    for current_index, (current_line, current) in enumerate(candidates):
        for earlier_line, earlier in candidates[:current_index]:
            similarity = 1.0 if current == earlier else _jaccard(current, earlier)
            if similarity >= _DUPLICATE_THRESHOLD:
                findings.append(
                    QualityFinding(
                        code="DUPLICATE_CONTENT_DETECTED",
                        severity=Severity.BLOCKER,
                        location=f"{location}:第 {current_line} 行",
                        message=(
                            f"与第 {earlier_line} 行的长段落相似度为 {similarity:.2f}，"
                            "请保留一个版本，并补充新的事实、案例或推理。"
                        ),
                )
            )
    visible_text = "\n".join(line for _, line in visible_lines)
    if _QUANTIFIED_RESUME_EXAMPLE.search(visible_text) and not _RESUME_EVIDENCE_BOUNDARY.search(
        visible_text
    ):
        findings.append(
            QualityFinding(
                code="RESUME_EVIDENCE_BOUNDARY_MISSING",
                severity=Severity.BLOCKER,
                location=location,
                message=(
                    "量化简历或项目经历示例未声明真实、可核验和不得编造的边界；"
                    "请要求读者仅使用本人可验证的指标与经历。"
                ),
            )
        )
    for item in heading_navigation_diagnostics(navigation_rows, language="zh-CN"):
        findings.append(QualityFinding(code="HEADING_NAVIGATION_" + item["signal"].upper(),
            severity=Severity.WARNING, location=item["location"], message=item["message"]))
    return findings
