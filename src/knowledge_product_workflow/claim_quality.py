"""Claim-declaration checks for high-risk reader-facing factual assertions."""

from __future__ import annotations

import re
from pathlib import Path

from knowledge_product_workflow.chapter_compliance import ChapterCompliance, compliance_path
from knowledge_product_workflow.content_quality import high_risk_factual_lines
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from knowledge_product_workflow.workspace import Workspace

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _location(workspace: Workspace, chapter_path: Path, line_number: int) -> str:
    return f"{chapter_path.relative_to(workspace.root).as_posix()}:第 {line_number} 行"


def _heading_contexts(markdown: str) -> dict[int, str | None]:
    """Map each visible line to the closest preceding reader heading."""
    contexts: dict[int, str | None] = {}
    active: str | None = None
    in_code_fence = False
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_code_fence = not in_code_fence
            contexts[line_number] = active
            continue
        if in_code_fence:
            continue
        match = _HEADING.match(line)
        if match is not None:
            active = match.group(2).strip()
        contexts[line_number] = active
    return contexts


def validate_undeclared_high_risk_claims(
    workspace: Workspace, chapter_path: Path, body: str
) -> list[QualityFinding]:
    """Require each high-risk assertion to sit under a current declared Claim anchor."""
    candidates = high_risk_factual_lines(body)
    if not candidates:
        return []
    try:
        chapter_number = int(chapter_path.stem.split("-")[1])
        _, chapter = require_current_chapter_contract(workspace, chapter_number)
    except (IndexError, ValueError):
        return [
            QualityFinding(
                code="UNDECLARED_HIGH_RISK_CLAIM",
                severity=Severity.BLOCKER,
                location=str(chapter_path.relative_to(workspace.root)).replace("\\", "/"),
                message="高风险事实断言无法绑定当前章节合约中的 Claim。",
            )
        ]
    record_path = compliance_path(workspace, chapter_number)
    try:
        record = ChapterCompliance.model_validate_json(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    declared_anchors = set(record.claim_locations.values()) if record is not None else set()
    contexts = _heading_contexts(body)
    findings: list[QualityFinding] = []
    for line_number, _ in candidates:
        if not chapter.claim_ids or contexts.get(line_number) not in declared_anchors:
            findings.append(
                QualityFinding(
                    code="UNDECLARED_HIGH_RISK_CLAIM",
                    severity=Severity.BLOCKER,
                    location=_location(workspace, chapter_path, line_number),
                    message=(
                        "高风险事实断言未在当前章节合约中声明并映射到 Claim 标题；"
                        "请登记 Claim 和来源，或明确改写为情境案例。"
                    ),
                )
            )
    return findings
