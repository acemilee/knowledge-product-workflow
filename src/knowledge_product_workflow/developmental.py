"""Deterministic developmental editing checks for structured reader tasks."""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from knowledge_product_workflow.content_plan import (
    ContentPlanError,
    content_plan_path,
    require_current_content_plan,
)
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.humanize import protected_fragments_sha256
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from knowledge_product_workflow.workspace import Workspace

_HEADING = re.compile(r"^#{2,6}\s+(?P<title>.+?)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_IMAGE = re.compile(r"!\[[^\]]*\]\(assets/")
_FENCE = re.compile(r"^\s*```")
_COMPONENT = re.compile(r"^\s*:::\s*\{\.(?P<name>kp-[a-z-]+)")


class DevelopmentalEditError(ValueError):
    """Raised when a v5 chapter lacks a current developmental editing report."""


class DevelopmentalEditReport(BaseModel):
    """A version-bound structural editing proof; it never claims authorship quality."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    rule_set: str = "kp-developmental-editor-v1"
    chapter_number: int = Field(gt=0)
    chapter_body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_fragments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    findings: list[QualityFinding] = Field(default_factory=list)


def developmental_report_path(workspace: Workspace, chapter_number: int) -> Path:
    return workspace.root / "runs" / "editorial" / f"chapter-{chapter_number:02d}-developmental-report.json"


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _reader_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    try:
        _, body = parse_markdown_artifact(text)
    except ValueError:
        body = text
    return body.strip() + "\n"


def _paragraphs(body: str) -> list[tuple[int, str]]:
    paragraphs: list[tuple[int, str]] = []
    current: list[str] = []
    start_line = 0
    in_fence = False
    for line_number, line in enumerate(body.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            if current:
                paragraphs.append((start_line, " ".join(current)))
                current, start_line = [], 0
            continue
        if in_fence or _HEADING.match(line) or _COMPONENT.match(line) or line.strip() == ":::":
            if current:
                paragraphs.append((start_line, " ".join(current)))
                current, start_line = [], 0
            continue
        if not line.strip():
            if current:
                paragraphs.append((start_line, " ".join(current)))
                current, start_line = [], 0
            continue
        if not current:
            start_line = line_number
        current.append(line.strip())
    if current:
        paragraphs.append((start_line, " ".join(current)))
    return paragraphs


def _body_capabilities(body: str) -> set[str]:
    capabilities: set[str] = set()
    if "::: {.kp-worksheet}" in body:
        capabilities.add("worksheet")
    if "::: {.kp-procedure}" in body:
        capabilities.add("procedure")
    if "::: {.kp-decision}" in body:
        capabilities.add("decision")
    if any(_TABLE_ROW.match(line) for line in body.splitlines()):
        capabilities.add("table")
    if _IMAGE.search(body):
        capabilities.add("diagram")
    if "```" in body:
        capabilities.add("code")
    return capabilities


def _findings_for_current_plan(workspace: Workspace, chapter_number: int, body: str) -> tuple[str, list[QualityFinding]]:
    plan = require_current_content_plan(workspace, chapter_number)
    if plan is None:
        return "", []
    plan_sha = _sha256_file(content_plan_path(workspace, chapter_number))
    findings: list[QualityFinding] = []
    capabilities = _body_capabilities(body)
    for section in plan.sections:
        if section.asset_decision != "none" and section.asset_decision not in capabilities:
            findings.append(
                QualityFinding(
                    code="DEVELOPMENTAL_ARTIFACT_MISSING",
                    severity=Severity.BLOCKER,
                    location=f"chapters/chapter-{chapter_number:02d}.md:{section.section_id}",
                    message=f"内容计划要求的 {section.asset_decision} 资产未出现在当前正文中：{section.artifact}。",
                )
            )
    paragraphs = _paragraphs(body)
    long_paragraphs = [line for line, paragraph in paragraphs if len(paragraph) >= 260]
    if len(long_paragraphs) >= 3:
        findings.append(
            QualityFinding(
                code="DEVELOPMENTAL_EXPLANATION_DENSITY_WARNING",
                severity=Severity.WARNING,
                location=f"chapters/chapter-{chapter_number:02d}.md:第 {long_paragraphs[0]} 行",
                message="连续长解释段过多；请在关键位置补充判断、对比、步骤、案例或读者工件。",
            )
        )
    return plan_sha, findings


def record_developmental_check(
    workspace: Workspace, chapter_number: int, source_path: Path
) -> tuple[Path | None, DevelopmentalEditReport | None]:
    """Record a current report for v5 workspaces; legacy v4 workspaces remain unchanged."""
    try:
        plan = require_current_content_plan(workspace, chapter_number)
    except ContentPlanError as error:
        raise DevelopmentalEditError(str(error)) from error
    if plan is None:
        return None, None
    resolved = source_path.expanduser().resolve()
    if not resolved.is_file() or not resolved.is_relative_to(workspace.root.resolve()):
        raise DevelopmentalEditError("发展性编辑源稿必须位于当前书稿工作区内")
    contract_path, _ = require_current_chapter_contract(workspace, chapter_number)
    body = _reader_body(resolved)
    plan_sha, findings = _findings_for_current_plan(workspace, chapter_number, body)
    report = DevelopmentalEditReport(
        chapter_number=chapter_number,
        chapter_body_sha256=_sha256_text(body),
        contract_sha256=_sha256_file(contract_path),
        content_plan_sha256=plan_sha,
        protected_fragments_sha256=protected_fragments_sha256(body),
        findings=findings,
    )
    output = developmental_report_path(workspace, chapter_number)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    workspace.append_event(
        "developmental_edit_checked",
        {"chapter_number": chapter_number, "finding_count": len(findings)},
    )
    return output, report


def require_current_developmental_report(
    workspace: Workspace, chapter_number: int, body: str, contract_path: Path
) -> DevelopmentalEditReport | None:
    """Fail closed only for v5 workspaces that explicitly require content planning."""
    try:
        plan = require_current_content_plan(workspace, chapter_number)
    except ContentPlanError as error:
        raise DevelopmentalEditError(str(error)) from error
    if plan is None:
        return None
    path = developmental_report_path(workspace, chapter_number)
    if not path.is_file():
        raise DevelopmentalEditError("缺少当前发展性编辑报告；请先完成章节结构与读者工件检查。")
    try:
        report = DevelopmentalEditReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DevelopmentalEditError("发展性编辑报告无效") from error
    if report.chapter_number != chapter_number:
        raise DevelopmentalEditError("发展性编辑报告的章节号不一致")
    if report.chapter_body_sha256 != _sha256_text(body):
        raise DevelopmentalEditError("发展性编辑报告已过期：它不再绑定当前正文。")
    if report.contract_sha256 != _sha256_file(contract_path):
        raise DevelopmentalEditError("发展性编辑报告已过期：它不再绑定当前章节合约。")
    if report.content_plan_sha256 != _sha256_file(content_plan_path(workspace, chapter_number)):
        raise DevelopmentalEditError("发展性编辑报告已过期：它不再绑定当前内容计划。")
    if any(finding.severity is Severity.BLOCKER for finding in report.findings):
        raise DevelopmentalEditError("发展性编辑报告仍包含阻断项。")
    return report
