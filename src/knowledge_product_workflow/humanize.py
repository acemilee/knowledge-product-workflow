"""Deterministic guardrails for a Chinese professional-copy editing pass."""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from knowledge_product_workflow.workspace import Workspace

_BOLD = re.compile(r"\*\*[^*\n]+\*\*")
_DECORATIVE_MIDDOT = re.compile(r"\*\*\s*[·•]\s*\*\*")
_SINGLE_EM_DASH = re.compile(r"(?<!—)—(?!—)")
_CORNER_QUOTE = re.compile(r"[「」]")
_FORMULAIC_CONTRAST = re.compile(r"(?:不是|并非).{0,28}(?:而是|而在于)")
_TEMPLATE_LABEL = re.compile(r"(?:规则|标准回答要点|深度答案|可能的追问及应对|本质问题)\s*[:：]")
_FENCE = re.compile(r"^\s*```")
_LINK = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_NUMBER = re.compile(r"\d+(?:\.\d+)?(?:%|ms|秒|分钟|小时|天|周|月|倍|元)?")


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


class HumanizeReportError(ValueError):
    """Raised when a chapter has not passed a current humanize pass."""


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _visible_lines(markdown: str) -> list[str]:
    lines: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def protected_fragments_sha256(markdown: str) -> str:
    """Hash code, links, table numerics, URLs, commands and other no-rewrite fragments."""
    fragments: list[str] = []
    in_fence = False
    code_lines: list[str] = []
    for line in markdown.splitlines():
        if _FENCE.match(line):
            if in_fence:
                fragments.append("\n".join(code_lines))
                code_lines = []
            in_fence = not in_fence
            continue
        if in_fence:
            code_lines.append(line)
            continue
        fragments.extend(_LINK.findall(line))
        if _TABLE_ROW.match(line):
            fragments.extend(_NUMBER.findall(line))
        fragments.extend(re.findall(r"https?://[^\s)>]+", line))
        fragments.extend(re.findall(r"(?:[A-Za-z]:\\|/)[\w./\\-]+", line))
    return _sha256_text("\n".join(fragments))


def scan_chinese_style(markdown: str, location: str) -> list[QualityFinding]:
    """Return concrete editorial findings, never a model-likeness or AI-probability score."""
    findings: list[QualityFinding] = []
    visible = _visible_lines(markdown)
    text = "\n".join(visible)
    line_count = max(1, len([line for line in visible if line.strip()]))

    def add(code: str, message: str) -> None:
        findings.append(QualityFinding(code=code, severity=Severity.BLOCKER, location=location, message=message))

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


def record_humanize_check(workspace: Workspace, chapter_number: int, source_path: Path) -> tuple[Path, HumanizeReport]:
    """Record the final editor check for a source body before controlled chapter submission."""
    if not source_path.is_file():
        raise HumanizeReportError(f"找不到待编辑章节文件：{source_path}")
    contract_path, _ = require_current_chapter_contract(workspace, chapter_number)
    text = source_path.read_text(encoding="utf-8")
    try:
        _, body = parse_markdown_artifact(text)
    except ValueError:
        body = text
    body = body.strip() + "\n"
    report = HumanizeReport(
        chapter_number=chapter_number,
        chapter_body_sha256=_sha256_text(body),
        contract_sha256=sha256(contract_path.read_bytes()).hexdigest(),
        protected_fragments_sha256=protected_fragments_sha256(body),
        findings=scan_chinese_style(body, str(source_path)),
    )
    output = humanize_report_path(workspace, chapter_number)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    workspace.append_event(
        "chinese_humanize_checked",
        {
            "chapter_number": chapter_number,
            "chapter_body_sha256": report.chapter_body_sha256,
            "finding_count": len(report.findings),
        },
    )
    return output, report


def require_current_humanize_report(
    workspace: Workspace, chapter_number: int, body: str, contract_path: Path
) -> HumanizeReport:
    """Reject a Chinese submission when its final editor report is missing, stale or still blocked."""
    path = humanize_report_path(workspace, chapter_number)
    if not path.is_file():
        raise HumanizeReportError("缺少 kp-chinese-humanize-editor 的当前编辑报告。")
    try:
        report = HumanizeReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HumanizeReportError("中文终稿编辑报告无效。") from error
    if report.chapter_number != chapter_number:
        raise HumanizeReportError("中文终稿编辑报告的章节号不一致。")
    if report.chapter_body_sha256 != _sha256_text(body):
        raise HumanizeReportError("中文终稿编辑报告已过期：它不再绑定当前正文。")
    if report.contract_sha256 != sha256(contract_path.read_bytes()).hexdigest():
        raise HumanizeReportError("中文终稿编辑报告已过期：它不再绑定当前章节合约。")
    if any(finding.severity is Severity.BLOCKER for finding in report.findings):
        raise HumanizeReportError("中文终稿编辑报告仍包含阻断项。")
    return report
