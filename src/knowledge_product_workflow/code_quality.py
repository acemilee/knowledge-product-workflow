"""Safe syntax-only checks for reader-visible code examples."""

from __future__ import annotations

import ast
import re

from knowledge_product_workflow.quality import QualityFinding, Severity

_FENCE = re.compile(r"^```(?P<language>[A-Za-z0-9_+-]*)\s*$")


def validate_code_blocks(markdown: str, location: str) -> list[QualityFinding]:
    """Validate Python syntax only; never execute examples, imports, or network calls."""
    findings: list[QualityFinding] = []
    language: str | None = None
    code: list[str] = []
    start_line = 0
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        match = _FENCE.match(line)
        if match is not None:
            if language is None:
                language = match.group("language").casefold()
                code = []
                start_line = line_number + 1
                continue
            if language in {"python", "py"}:
                try:
                    ast.parse("\n".join(code), filename=location, mode="exec")
                except SyntaxError as error:
                    findings.append(
                        QualityFinding(
                            code="PYTHON_CODE_SYNTAX_INVALID",
                            severity=Severity.BLOCKER,
                            location=f"{location}:第 {start_line + max(0, (error.lineno or 1) - 1)} 行",
                            message=f"Python 示例存在语法错误：{error.msg}。代码检查不会执行示例、导入或网络请求。",
                        )
                    )
            language = None
            code = []
            continue
        if language is not None:
            code.append(line)
    return findings
