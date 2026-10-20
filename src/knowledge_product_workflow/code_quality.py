"""Safe syntax-only checks for reader-visible code examples."""

from __future__ import annotations

import ast

from knowledge_product_workflow.quality import QualityFinding, Severity


def validate_code_blocks(markdown: str, location: str, *, workspace=None) -> list[QualityFinding]:
    """Validate Python syntax only; never execute examples, imports, or network calls."""
    findings: list[QualityFinding] = []
    excerpt_spans = set()
    from knowledge_product_workflow.reader_artifacts import reader_fences, resolve_reader_resources
    if workspace is not None:
        try:
            for resource in resolve_reader_resources(workspace, markdown):
                excerpt_spans.update(resource["excerpt_spans"])
        except (OSError, ValueError) as error:
            findings.append(QualityFinding(code="READER_RESOURCE_INVALID", severity=Severity.BLOCKER,
                location=location, message=str(error)))
    for fence in reader_fences(markdown):
        if not fence.closed or fence.language not in {"python", "py"}:
            continue
        if (fence.start, fence.end) in excerpt_spans:
            continue
        try:
            ast.parse(fence.code, filename=location, mode="exec")
        except SyntaxError as error:
            findings.append(QualityFinding(code="PYTHON_CODE_SYNTAX_INVALID", severity=Severity.BLOCKER,
                location=f"{location}:第 {fence.line + max(0, (error.lineno or 1) - 1)} 行",
                message=f"Python 示例存在语法错误：{error.msg}。片段须精确绑定完整资源，检查不会执行代码。"))
    return findings
