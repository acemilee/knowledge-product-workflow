"""Version-bound, page-level measurements and review samples for v6 PDFs."""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import struct
import subprocess
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pypdf import PdfReader

from knowledge_product_workflow.pdf_preview import (
    current_pdf_preview,
    iter_png_rgb_rows,
    png_has_visible_ink,
    resolve_pdftoppm,
)
from knowledge_product_workflow.presentation import (
    PRESENTATION_PROFILE_ID,
    PUBLICATION_PROFILE_FILENAME,
    PublicationProfile,
)
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.verification_context import current_verification
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import read_bytes as _v_read_bytes
from knowledge_product_workflow.verification_context import read_text as _v_read_text
from knowledge_product_workflow.verification_context import verified_read as _verification_read
from knowledge_product_workflow.verification_context import write_bytes as _v_write_bytes
from knowledge_product_workflow.verification_context import write_text as _v_write_text
from knowledge_product_workflow.workspace import Workspace

VISUAL_RULE_SET = "kp-pdf-v6-visual-6"
_BODY_BLOCK_PT = 10.0
_BODY_WARN_PT = 11.5
_TOC_BLOCK_PT = 9.0
_TOC_WARN_PT = 10.0


class HorizontalTextCandidate(BaseModel):
    """Approximate single-run location; never a verdict on painted glyphs."""

    text: str = Field(max_length=180)
    x_pt: float
    y_pt: float
    estimated_right_pt: float
    font_role: str


class VisualPageMetric(BaseModel):
    page_number: int = Field(gt=0)
    page_type: str
    width_pt: float
    height_pt: float
    text_characters: int
    body_font_median_pt: float | None = None
    minimum_content_font_pt: float | None = None
    line_baseline_median_pt: float | None = None
    max_line_equivalent_characters: float | None = None
    body_height_fraction: float | None = None
    image_count: int = 0
    scaled_text_fragments: int = 0
    font_role_medians_pt: dict[str, float] = Field(default_factory=dict)
    body_role_base_pt: dict[str, float] = Field(default_factory=dict)
    body_role_sample_characters: dict[str, int] = Field(default_factory=dict)
    expected_body_font_pt: float | None = None
    high_resolution_image: str | None = None
    high_resolution_image_sha256: str | None = None
    high_resolution_dpi: int | None = None
    page_context_sha256: str | None = None
    bottom_gap_fraction: float | None = None
    layout_context_required_pages: list[int] = Field(default_factory=list)
    horizontal_text_candidates: list[HorizontalTextCandidate] = Field(default_factory=list)
    horizontal_text_candidate_count: int = Field(default=0, ge=0)


class VisualObservation(BaseModel):
    page_number: int = Field(gt=0)
    assessment: Literal["passed", "failed", "uncertain"]
    note: str = Field(min_length=1)
    responsible_skill: str = Field(default="kp-visual-reviewer", min_length=1)
    scope: Literal['page', 'context'] = 'context'
    reuse_origin: dict[str, str] | None = None
    observation_input_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    page_image_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    view_kind: Literal['clear_page', 'overview', 'machine'] | None = None
    evidence_anchor: str | None = Field(default=None, max_length=1200)
    checked_dimensions: list[Literal['legibility', 'clipping', 'layout']] = Field(default_factory=list)
    context_page_sha256: dict[int, str] = Field(default_factory=dict)
    flow_cause: Literal['object_pushback', 'chapter_end', 'other', 'unknown'] | None = None


class VisualAuditFinding(QualityFinding):
    page_number: int | None = None
    page_type: str | None = None
    measurement: dict[str, float | str | None] = Field(default_factory=dict)
    responsible_skill: str = "kp-publisher"
    repair_action: str = "调整受控版式后重新渲染与复核"
    screenshot_path: str | None = None
    screenshot_sha256: str | None = None


class VisualAuditReport(BaseModel):
    schema_version: int = 1
    evidence_revision: Literal[0, 1] = 0
    typography_measurement_revision: Literal[0, 1, 2] = 0
    rule_set: str = VISUAL_RULE_SET
    created_at: datetime
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    figure_sources_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(gt=0)
    pages: list[VisualPageMetric]
    sampled_pages: list[int]
    sample_limit: int = 12
    findings: list[VisualAuditFinding]
    observations: list[VisualObservation] = Field(default_factory=list)
    observation_state: Literal["manual_required", "observed"] = "manual_required"
    table_column_counts: list[int] = Field(default_factory=list)


def audit_path(workspace: Workspace) -> Path:
    return workspace.root / "runs" / "visual-audit-report.json"


def _digest(path: Path) -> str:
    return sha256(_v_read_bytes(path)).hexdigest()


def _profile_path(workspace: Workspace) -> Path:
    return workspace.root / "planning" / PUBLICATION_PROFILE_FILENAME


def _figure_sources_digest(workspace: Workspace) -> str:
    qmd = workspace.root / "delivery" / "book.qmd"
    ids = set(re.findall(r"assets/figures/([a-z0-9-]+)\.pdf", _v_read_text(qmd, encoding="utf-8"))) if _v_is_file(qmd) else set()
    parts = []
    for figure_id in sorted(ids):
        for suffix in (".input.json", ".spec.json", ".svg", ".json"):
            path = workspace.root / "runs" / "diagrams" / f"{figure_id}{suffix}"
            parts.append(f"{figure_id}{suffix}:{_digest(path) if _v_is_file(path) else 'missing'}")
    from knowledge_product_workflow.illustrations import current_profile, page_style_digest
    try:
        if current_profile(workspace).illustration_style_revision:
            parts.append("page-style:" + page_style_digest(workspace, _v_read_text(qmd, encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return sha256("\n".join(parts).encode()).hexdigest()


def _bindings(workspace: Workspace) -> tuple[Path, Path, Path] | None:
    pdf = workspace.root / "delivery" / "book.pdf"
    profile = _profile_path(workspace)
    if not _v_is_file(pdf) or not _v_is_file(profile):
        return None
    try:
        if PublicationProfile.model_validate(yaml.safe_load(_v_read_text(profile, encoding="utf-8"))).profile_id != PRESENTATION_PROFILE_ID:
            return None
    except (OSError, ValueError, TypeError):
        return None
    current = current_pdf_preview(pdf, workspace.root / "runs" / "pdf-preview")
    if current is None:
        return None
    return pdf, current[0], profile


def _clear_image_valid(workspace: Workspace, page: VisualPageMetric, dpi: int = 144) -> bool:
    context = current_verification(workspace)
    if context is not None:
        key = ('clear-png.v1', page.high_resolution_image, page.high_resolution_image_sha256,
               page.high_resolution_dpi, page.width_pt, page.height_pt, dpi)
        return context.memo(key, lambda: _clear_image_valid_uncached(workspace, page, dpi))
    return _clear_image_valid_uncached(workspace, page, dpi)


def _clear_image_valid_uncached(workspace: Workspace, page: VisualPageMetric, dpi: int) -> bool:
    if not page.high_resolution_image or (page.high_resolution_dpi or 0) < dpi:
        return False
    image = workspace.root / page.high_resolution_image
    if not image.resolve().is_relative_to(workspace.root.resolve()):
        return False
    try:
        if not image.is_file() or _digest(image) != page.high_resolution_image_sha256:
            return False
        header = _v_read_bytes(image)[:24]
        if not header.startswith(b"\x89PNG\r\n\x1a\n") or len(header) != 24:
            return False
        width, height = struct.unpack("!II", header[16:24])
        rows = iter_png_rgb_rows(image, include_alpha=True)
        try:
            next(rows)
        finally:
            rows.close()
        return width >= page.width_pt * page.high_resolution_dpi / 72 - 2 and height >= page.height_pt * page.high_resolution_dpi / 72 - 2
    except (OSError, ValueError, StopIteration):
        return False


def _bound_visual_report(workspace: Workspace) -> VisualAuditReport | None:
    bindings = _bindings(workspace)
    if bindings is None:
        return None
    pdf, preview, profile = bindings
    try:
        raw = json.loads(_v_read_text(audit_path(workspace), encoding="utf-8"))
        if not raw.get("rule_set"):
            return None
        report = VisualAuditReport.model_validate(raw)
    except (OSError, ValueError):
        return None
    if (
        report.pdf_sha256 != _digest(pdf)
        or report.preview_manifest_sha256 != _digest(preview)
        or report.profile_sha256 != _digest(profile)
        or report.figure_sources_sha256 != _figure_sources_digest(workspace)
        or report.page_count != len(report.pages)
        or report.page_count != len(PdfReader(pdf).pages)
        or [page.page_number for page in report.pages] != list(range(1, report.page_count + 1))
        or report.sample_limit < 1
        or report.sampled_pages != sorted(set(report.sampled_pages))
        or not set(report.sampled_pages).issubset(range(1, report.page_count + 1))
    ):
        return None
    try:
        events = [json.loads(line) for line in _v_read_text(workspace.root / "runs" / "events.jsonl", encoding="utf-8").splitlines() if line.strip()]
        audit_events = [event for event in events if event.get("event_type") in {"pdf_visual_audited", "pdf_visual_observations_recorded"}]
        if not audit_events or audit_events[-1].get("payload", {}).get("report_sha256") != _digest(audit_path(workspace)):
            return None
    except (OSError, ValueError):
        return None
    return report


def _observation_pages(report: VisualAuditReport) -> list[QualityFinding]:
    return [QualityFinding(code="PDF_OBSERVATION_REVIEW_REQUIRED", severity=Severity.WARNING,
                           location=f"第 {item.page_number} 页", message=item.note)
            for item in report.observations if item.assessment != "passed"]


@_verification_read
def current_visual_audit(workspace: Workspace) -> VisualAuditReport | None:
    report = _bound_visual_report(workspace)
    if report is None:
        return None
    from knowledge_product_workflow.developmental import review_evidence_revision
    if report.evidence_revision != review_evidence_revision(workspace):
        return None
    design = PublicationProfile.model_validate(yaml.safe_load(_v_read_text(_profile_path(workspace), encoding="utf-8")))
    if report.rule_set != VISUAL_RULE_SET or report.typography_measurement_revision != (2 if design.reader_layout_revision == 2 else 0):
        return None
    limit = max(report.sample_limit, report.page_count) if design.schema_version >= 4 and design.reader_layout_revision == 2 and report.page_count <= 20 else report.sample_limit
    required = set(_sample_pages(report.pages, [*report.findings, *_observation_pages(report)], limit))
    if not required.issubset(report.sampled_pages):
        return None
    if any((page.high_resolution_image or page.high_resolution_image_sha256 or page.high_resolution_dpi)
           and not _clear_image_valid(workspace, page) for page in report.pages):
        return None
    if resolve_pdftoppm() is not None and any(not _clear_image_valid(workspace, report.pages[n-1]) for n in report.sampled_pages):
        return None
    return report


def _historical_visual_candidate(workspace, profile, typography_revision):
    """Read a stale candidate for comparison only; never return it as current."""
    try:
        raw = audit_path(workspace).read_bytes()
        report = VisualAuditReport.model_validate_json(raw)
        events = (workspace.root / 'runs/events.jsonl').read_text(encoding='utf8').splitlines()
        recorded = next((event.get('payload',{}).get('report_sha256')
            for line in reversed(events) if line.strip()
            for event in [json.loads(line)] if event.get('event_type') in
            {'pdf_visual_audited','pdf_visual_observations_recorded'}), None)
    except (OSError, ValueError):
        return None
    from knowledge_product_workflow.developmental import review_evidence_revision
    if (recorded != sha256(raw).hexdigest() or report.schema_version != 1 or report.rule_set != VISUAL_RULE_SET
            or report.evidence_revision != review_evidence_revision(workspace)
            or report.typography_measurement_revision != typography_revision
            or report.profile_sha256 != _digest(profile)
            or report.figure_sources_sha256 != _figure_sources_digest(workspace)
            or [p.page_number for p in report.pages] != list(range(1,report.page_count+1))):
        return None
    return report, sha256(raw).hexdigest()


def full_visual_observation_coverage(report: VisualAuditReport) -> bool:
    expected = set(range(1, report.page_count + 1))
    return (set(report.sampled_pages) == expected and
            {item.page_number for item in report.observations if item.assessment == "passed"
             and (not report.evidence_revision or _evidence_issues(report, item) is None)} == expected and
            all(page.high_resolution_image and (page.high_resolution_dpi or 0) >= 144 for page in report.pages))


def visual_observation_worklist(report: VisualAuditReport) -> dict[str, object]:
    """Partition a caller-verified current report without writing or creating proof."""
    observable = {page.page_number for page in report.pages if page.page_number in report.sampled_pages
                  and page.high_resolution_image and page.high_resolution_image_sha256
                  and (page.high_resolution_dpi or 0) >= 144}
    observations = {item.page_number: item.assessment for item in report.observations
                    if not report.evidence_revision or _evidence_issues(report, item) is None}
    pending = sorted(observable - observations.keys())
    unavailable = sorted(set(report.sampled_pages) - observable)
    return {
        "observable_pages": sorted(observable),
        "reusable_pages": sorted(page for page in observable if observations.get(page) == "passed"),
        "inherited_pages": sorted(item.page_number for item in report.observations if item.reuse_origin),
        "newly_observed_pages": sorted(item.page_number for item in report.observations if not item.reuse_origin),
        "pending_pages": pending,
        "failed_pages": sorted(page for page in observable if observations.get(page) == "failed"),
        "uncertain_pages": sorted(page for page in observable if observations.get(page) == "uncertain"),
        "unavailable_pages": unavailable,
        "needs_submission": bool(pending),
        "needs_clear_images": bool(unavailable),
        "full_coverage_ready": full_visual_observation_coverage(report),
        "evidence_revision": report.evidence_revision,
        "evidence_schema": VisualObservation.model_json_schema(),
    }


def _evidence_issues(report: VisualAuditReport, item: VisualObservation) -> str | None:
    """Validate declared scope, not whether a model truly understood the pixels."""
    if item.assessment == 'passed' and item.view_kind != 'clear_page':
        return '概览或机器量测不能代替当前清晰页细查；未看清保留uncertain'
    if item.assessment == 'passed' and (not (item.evidence_anchor or '').strip()
            or set(item.checked_dimensions) != {'legibility', 'clipping', 'layout'}):
        return '通过观察需要具体可见内容依据及legibility/clipping/layout三项已查维度'
    page = report.pages[item.page_number-1]
    if item.scope == 'page' and item.context_page_sha256:
        return '跨页结论须用context范围，不得声明为page'
    if item.scope == 'context' and item.assessment == 'passed':
        numbers = set(item.context_page_sha256)
        if item.page_number not in numbers or len(numbers) < 2:
            return 'context跨页观察必须绑定当前页及相关相邻页'
        if any(n < 1 or n > report.page_count or abs(n-item.page_number) > 1
               or not report.pages[n-1].high_resolution_image_sha256
               or digest != report.pages[n-1].high_resolution_image_sha256
               for n, digest in item.context_page_sha256.items()):
            return '跨页清晰PNG身份过期或不是相关相邻页'
    if page.layout_context_required_pages and item.assessment == 'passed':
        if (item.scope != 'context' or not set(page.layout_context_required_pages) <= set(item.context_page_sha256)):
            return '图表挤页候选必须核对相邻页，不能只签单页passed'
        if item.flow_cause not in {'other', 'chapter_end'}:
            return '图表挤页造成大白不可通过；未核清原因保留uncertain，确认object_pushback则修复'
    return None


def _bottom_gap(pdf: Path, number: int, tool: Path) -> float:
    """Cheap grayscale geometry probe; clear PNGs remain the review evidence."""
    result = subprocess.run([str(tool), '-f', str(number), '-l', str(number), '-scale-to', '320',
        '-gray', '-singlefile', str(pdf)], capture_output=True, timeout=30, check=False)
    match = re.match(rb'P5\s+(\d+)\s+(\d+)\s+255\s', result.stdout)
    if result.returncode or match is None:
        raise RuntimeError('低分辨率留白量测失败；不默认通过，保留待核')
    width, height = int(match[1]), int(match[2])
    pixels = result.stdout[match.end():]
    if len(pixels) != width*height:
        raise RuntimeError('留白量测像素不完整，保留待核')
    first, end = int(height*.08), int(height*.92)
    last = first
    for y in range(first, end):
        row = pixels[y*width:(y+1)*width]
        if sum(row[x] < 210 for x in range(int(width*.06), int(width*.94))) >= 2:
            last = y
    return round((end-last)/max(1,end-first), 3)


def _object_flow_findings(metrics: list[VisualPageMetric], texts: list[str], qmd: str) -> list[QualityFinding]:
    headers = [re.sub(r'\s+', '', line) for line in qmd.splitlines()
               if line.strip().startswith('|') and not re.fullmatch(r'[\s|:\-]+', line)]
    result = []
    for index, (page, following) in enumerate(pairwise(metrics)):
        if page.page_type in {'cover','toc'} or (page.bottom_gap_fraction or 0) < .35:
            continue
        current_chapter = re.match(r'^\s*(\d+)\.\s', texts[index])
        next_chapter = re.match(r'^\s*(\d+)\.\s', texts[index+1])
        if current_chapter and next_chapter and current_chapter[1] != next_chapter[1]:
            continue
        next_text = re.sub(r'\s+', '', texts[index+1])
        figure = re.search(r'图\s*\d+\s*[:：]', texts[index+1])
        table = any(len(cells := [c for c in line.split('|') if c]) >= 2
                    and all(cell in next_text for cell in cells[:2]) for line in headers)
        if not (figure or table):
            continue
        page.layout_context_required_pages = [page.page_number, following.page_number]
        result.append(QualityFinding(code='PDF_OBJECT_FLOW_REVIEW_REQUIRED', severity=Severity.WARNING,
            location=f'delivery/book.pdf:第 {page.page_number} 页',
            message=f'正文区末端留白约{page.bottom_gap_fraction:.0%}，后页存在图表；核对这两页与源对象。'
                '章末偶发空白可容忍，图表挤页大白须修复；仅凭密度不能确定原因。'))
    return result


def _page_type(page_number: int, text: str, image_count: int) -> str:
    if page_number == 1:
        return "cover"
    first_lines = [line.strip() for line in text.splitlines()[:3]]
    if "目录" in first_lines or "Table of Contents" in first_lines:
        return "toc"
    if "工作表" in text:
        return "worksheet"
    if image_count:
        return "figure"
    if "图 " in text or "图：" in text:
        return "figure"
    if "```" in text or "def " in text or "function " in text:
        return "code"
    return "body"


def _measure_page(page, page_number: int, expected_body_font_pt: float | None = None) -> VisualPageMetric:
    sizes: list[float] = []
    minimum_sizes: list[float] = []
    y_positions: list[float] = []
    line_characters: dict[float, float] = {}
    segments: dict[float, list[tuple[float, float, float]]] = {}
    role_sizes: dict[str, list[float]] = {}
    scaled_text_fragments = 0
    typography_runs: list[tuple[str, float, float, int]] = []
    marked_code: list[bool] = []
    horizontal_candidates: list[HorizontalTextCandidate] = []
    horizontal_candidate_count = 0

    def collect_role(operator, arguments, _cm, _tm) -> None:
        if operator in {b"BMC", b"BDC"}:
            inherited = bool(marked_code and marked_code[-1])
            marked_code.append(inherited or bool(arguments and str(arguments[0]) == "/KPCode"))
        elif operator == b"EMC" and marked_code:
            marked_code.pop()

    def collect(text: str, cm, tm, _font, size: float) -> None:
        nonlocal scaled_text_fragments, horizontal_candidate_count
        if not text.strip():
            return
        # pypdf emits aggregate text callbacks after nested Form XObjects,
        # carrying a surrounding font state rather than the painted glyph's
        # state. Do not turn those callbacks into deterministic font blockers.
        if abs(math.hypot(cm[0], cm[1]) - 1) > 0.01 or abs(math.hypot(tm[0], tm[1]) - 1) > 0.01:
            scaled_text_fragments += 1
            return
        effective_size = size * math.hypot(cm[0], cm[1]) * math.hypot(tm[0], tm[1])
        font_name = str((_font or {}).get("/BaseFont", "")).lower()
        role = "monospace" if (marked_code and marked_code[-1]) or any(name in font_name for name in ("mono", "consolas", "courier")) else "math" if any(name in font_name for name in ("math", "cmmi", "cmsy", "symbol")) else "body"
        x = cm[0] * tm[4] + cm[2] * tm[5] + cm[4]
        y = cm[1] * tm[4] + cm[3] * tm[5] + cm[5]
        height = float(page.mediabox.height)
        # A PDF visitor's text is not a glyph bounding box. Only locate simple,
        # positioned, unrotated runs near the physical page edge; nested forms,
        # multiline aggregates and origin callbacks cannot support this estimate.
        positioned = tm[4] != 0 or tm[5] != 0
        unrotated = all(abs(value) < .001 for value in (cm[1], cm[2], tm[1], tm[2])) and cm[0] > 0 and tm[0] > 0
        lines = [line for line in text.splitlines() if line.strip()]
        if positioned and unrotated and len(lines) == 1 and height * .08 <= y <= height * .92:
            approximate_width = sum(1 if ord(c) > 255 else .6 if role == "monospace" else .5 for c in lines[0]) * effective_size
            estimated_right = x + approximate_width
            if x < float(page.mediabox.left) - 2 or estimated_right > float(page.mediabox.right) + 2:
                horizontal_candidate_count += 1
                if len(horizontal_candidates) < 20:
                    horizontal_candidates.append(HorizontalTextCandidate(
                        text=lines[0][:180], x_pt=round(x, 2), y_pt=round(y, 2),
                        estimated_right_pt=round(estimated_right, 2), font_role=role,
                    ))
        if height * 0.08 <= y <= height * 0.92:
            if expected_body_font_pt is not None and .75 * expected_body_font_pt <= effective_size <= 1.5 * expected_body_font_pt:
                family = None if role == "monospace" else "cjk" if any(name in font_name for name in ("pingfang", "simhei", "yahei")) else "math" if role == "math" else "latin" if any(name in font_name for name in ("inter-", "latinmodernsans", "helvetica")) else None
                if family in {"cjk", "latin"} and "bold" in font_name and effective_size > expected_body_font_pt + .15:
                    family = None  # Larger heading faces are not body-size evidence.
                # Font switches can yield aggregate callbacks with an identity
                # text matrix at the page CTM origin (including footer text).
                # Their glyph position is unknown, so they are not role evidence.
                if family and (tm[4] != 0 or tm[5] != 0) and len(text.strip().splitlines()) == 1:
                    typography_runs.append((family, round(effective_size, 4), y, min(len(text.strip()), 100)))
            if 7 <= effective_size <= 18:
                weights = [round(effective_size, 2)] * min(len(text.strip()), 100)
                role_sizes.setdefault(role, []).extend(weights)
                if role == "body":
                    sizes.extend(weights)
            if len(text.strip()) >= 5 and effective_size >= 4:
                minimum_sizes.append(effective_size)
            y_positions.append(y)
            if 10 <= effective_size <= 14:
                line_key = round(y, 1)
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                equivalents = [sum(1 if ord(character) > 255 else 0.5 for character in line) for line in lines]
                if len(lines) == 1:
                    segments.setdefault(line_key, []).append((x, x+equivalents[0]*effective_size, equivalents[0]))
                elif equivalents:
                    # An aggregate multiline callback is not one 146-character physical line.
                    line_characters[line_key] = max(line_characters.get(line_key, 0), max(equivalents))

    text = page.extract_text(visitor_text=collect, visitor_operand_before=collect_role) or ""
    for baseline, pieces in segments.items():
        run_chars, max_chars, end = 0.0, 0.0, None
        for start, stop, count in sorted(pieces):
            if end is not None and start-end > 28:
                max_chars = max(max_chars, run_chars)
                run_chars = 0.0
            run_chars += count
            end = max(end or stop, stop)
        line_characters[baseline] = max(line_characters.get(baseline, 0), run_chars, max_chars)
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    try:
        image_count = len(page.images)
    except (OSError, ValueError, ImportError):
        image_count = 0
    kind = _page_type(page_number, text, image_count)
    if scaled_text_fragments and kind == "body":
        kind = "figure"
    body_y = [y for y in y_positions if height * 0.08 <= y <= height * 0.92]
    occupancy = (max(body_y) - min(body_y)) / height if len(body_y) >= 2 else None
    baselines = sorted(line_characters)
    gaps = [second - first for first, second in pairwise(baselines) if 12 <= second - first <= 32]
    # Inline math shares a baseline with prose. Display equations and lowered
    # scripts are not evidence for the ordinary inline base size.
    role_counts: dict[str, Counter] = {}
    prose = [run for run in typography_runs if run[0] in {"cjk", "latin"}]
    for family, size, y, count in typography_runs:
        if family == "math" and not any(abs(y-other[2]) < 1 for other in prose):
            continue
        role_counts.setdefault(family, Counter())[size] += count
    bases = {family: counter.most_common(1)[0] for family, counter in role_counts.items()}
    return VisualPageMetric(
        page_number=page_number,
        page_type=kind,
        width_pt=round(width, 2),
        height_pt=round(height, 2),
        text_characters=len(text.strip()),
        body_font_median_pt=round(statistics.median(sizes), 2) if sizes else None,
        minimum_content_font_pt=round(min(minimum_sizes), 2) if minimum_sizes else None,
        line_baseline_median_pt=round(statistics.median(gaps), 2) if gaps else None,
        max_line_equivalent_characters=round(max(line_characters.values()), 1) if line_characters else None,
        body_height_fraction=round(occupancy, 3) if occupancy is not None else None,
        image_count=image_count,
        scaled_text_fragments=scaled_text_fragments,
        font_role_medians_pt={role: round(statistics.median(values), 2) for role, values in role_sizes.items() if values},
        body_role_base_pt={family: size for family, (size, count) in bases.items()},
        body_role_sample_characters={family: count for family, (size, count) in bases.items()},
        expected_body_font_pt=expected_body_font_pt,
        horizontal_text_candidates=horizontal_candidates,
        horizontal_text_candidate_count=horizontal_candidate_count,
    )


def _page_findings(metric: VisualPageMetric) -> list[QualityFinding]:
    location = f"delivery/book.pdf:第 {metric.page_number} 页"
    findings: list[QualityFinding] = []
    if metric.horizontal_text_candidate_count:
        examples = "; ".join(
            f"({c.x_pt:g}, {c.y_pt:g})≈{c.estimated_right_pt:g}pt {c.text!r}"
            for c in metric.horizontal_text_candidates[:3]
        )
        findings.append(QualityFinding(
            code="PDF_HORIZONTAL_TEXT_CANDIDATE", severity=Severity.WARNING, location=location,
            message=f"发现 {metric.horizontal_text_candidate_count} 处横向文字越界候选（字符宽度估算，非字形边界）：{examples}。"
                    "须查看当前清晰页及相邻列核实；无候选也不证明无裁切，不以此自动签通过或修改正文。",
        ))
    if metric.page_type == "body" and metric.expected_body_font_pt is not None:
        tolerance = max(.15, .01 * metric.expected_body_font_pt)
        for family, observed in metric.body_role_base_pt.items():
            if metric.body_role_sample_characters.get(family, 0) < (3 if family == "math" else 12):
                continue
            if abs(observed-metric.expected_body_font_pt) > tolerance:
                findings.append(QualityFinding(
                    code="PDF_BODY_ROLE_SIZE_DRIFT", severity=Severity.BLOCKER, location=location,
                    message=f"正文{family}基础字级约{observed:.3f} PDF pt，与声明目标{metric.expected_body_font_pt:.3f}偏离；不能由整页中位数或人工通过覆盖。",
                ))
    if metric.scaled_text_fragments:
        findings.append(QualityFinding(
            code="PDF_SCALED_TEXT_MANUAL_REQUIRED", severity=Severity.WARNING, location=location,
            message="页面含缩放的矢量/嵌套文本，字号回调不可靠；必须在当前清晰页图中核验图字，不能以原始字号冒充成页字号。",
        ))
    if abs(metric.width_pt - 595.28) > 2 or abs(metric.height_pt - 841.89) > 2:
        findings.append(QualityFinding(code="PDF_PAGE_SIZE_INVALID", severity=Severity.BLOCKER, location=location, message="v6 出版页必须为 A4。"))
    size = metric.body_font_median_pt
    if size is not None and metric.page_type in {"body", "figure", "code", "worksheet", "toc"}:
        if metric.page_type == "toc":
            block, warn = _TOC_BLOCK_PT, _TOC_WARN_PT
        elif metric.page_type in {"figure", "code", "worksheet"}:
            block, warn = 8.0, 9.0
        else:
            block, warn = _BODY_BLOCK_PT, _BODY_WARN_PT
        if size < warn:
            findings.append(QualityFinding(
                code="PDF_FONT_BELOW_MINIMUM" if size < block else "PDF_FONT_SMALL_WARNING",
                severity=Severity.BLOCKER if size < block else Severity.WARNING,
                location=location,
                message=f"{metric.page_type} 页可提取文字中位字号 {size:.1f} pt；出版下限 {block:.1f} pt，预警线 {warn:.1f} pt。",
            ))
    if metric.page_type != "cover" and metric.minimum_content_font_pt is not None and metric.minimum_content_font_pt < 8.0:
        findings.append(QualityFinding(
            code="PDF_SMALL_TEXT_BLOCKER", severity=Severity.BLOCKER, location=location,
            message=f"可提取正文/图中文字最小约 {metric.minimum_content_font_pt:.1f} pt，低于 8 pt 出版下限；请拆图/拆表或调整版式。",
        ))
    if metric.page_type == "body" and metric.body_height_fraction is not None:
        if metric.body_height_fraction < 0.30:
            findings.append(QualityFinding(code="PDF_BODY_SPARSE_WARNING", severity=Severity.WARNING, location=location, message=f"正文占高约 {metric.body_height_fraction:.0%}；先核章节边界，章末偶发留白可保留，不按填充率加字；图表挤页原因另查。"))
        elif metric.body_height_fraction > 0.91:
            findings.append(QualityFinding(code="PDF_BODY_DENSE_WARNING", severity=Severity.WARNING, location=location, message=f"正文占高约 {metric.body_height_fraction:.0%}；请检查连续密集页面。"))
    if metric.page_type == "body" and metric.max_line_equivalent_characters is not None and metric.max_line_equivalent_characters > 55:
        findings.append(QualityFinding(code="PDF_LONG_LINE_WARNING", severity=Severity.WARNING, location=location, message=f"单行约 {metric.max_line_equivalent_characters:g} 个中文等效字符；请检查扫读负担。"))
    return findings


def _sample_pages(metrics: list[VisualPageMetric], findings: list[QualityFinding], cap: int) -> list[int]:
    if len(metrics) <= cap:
        return list(range(1, len(metrics) + 1))
    selected = {1, len(metrics)}
    for finding in findings:
        if match := re.search(r"第 (\d+) 页", finding.location):
            number = int(match.group(1))
            if 1 <= number <= len(metrics):
                selected.add(number)
    def evenly(numbers, count):
        if count <= 0:
            return []
        if len(numbers) <= count:
            return numbers
        if count == 1:
            return [numbers[len(numbers)//2]]
        return [numbers[round(i*(len(numbers)-1)/(count-1))] for i in range(count)]
    remaining = max(0, cap-len(selected))
    special = [page.page_number for page in metrics if page.page_type in {"toc", "figure", "code", "worksheet"} and page.page_number not in selected]
    selected.update(evenly(special, (remaining+1)//2))
    distributed = [number for number in range(1, len(metrics)+1) if number not in selected]
    selected.update(evenly(distributed, max(0, cap-len(selected))))
    return sorted(selected)


def _closure_findings(metrics: list[VisualPageMetric]) -> list[QualityFinding]:
    """仅定位短教程疑似孤尾；有教学用途的留白仍由实际视觉审核判断。"""
    if len(metrics) <= 3:
        return []
    return [QualityFinding(code='PDF_POSSIBLE_ORPHAN_CLOSURE', severity=Severity.WARNING,
            location=f'delivery/book.pdf:第 {page.page_number} 页',
            message='本页内容很少；章末偶发留白可容忍，不因短收束独占页就强制改正文或填字。图表挤页致前页大白需另核相邻页并修排版，不以填充率代替原因判断。')
            for page in metrics if page.page_type == 'body' and page.body_height_fraction is not None
            and page.body_height_fraction < .20 and page.text_characters < 220]


def audit_pdf_visual(workspace: Workspace, *, sample_limit: int = 12, dpi: int = 160) -> VisualAuditReport:
    if type(sample_limit) is not int or sample_limit < 1 or not 144 <= dpi <= 180:
        raise ValueError("视觉抽样页数须为正整数，清晰页图 DPI 须为 144–180。")
    bindings = _bindings(workspace)
    if bindings is None:
        raise ValueError("请先生成当前 v6 PDF、出版档案与完整页面预览，再执行视觉量测。")
    pdf, preview, profile = bindings
    pages = PdfReader(pdf).pages
    design = PublicationProfile.model_validate(yaml.safe_load(profile.read_text(encoding="utf-8")))
    sample_limit = min(sample_limit, len(pages))
    if design.schema_version >= 4 and design.reader_layout_revision == 2 and len(pages) <= 20:
        sample_limit = len(pages)
    cached = current_visual_audit(workspace)
    if cached is not None:
        required = _sample_pages(cached.pages, [*cached.findings, *_observation_pages(cached)], sample_limit)
        if set(required).issubset(cached.sampled_pages) and (
            all(_clear_image_valid(workspace, cached.pages[n-1], dpi) for n in required)
            or resolve_pdftoppm() is None and all(not cached.pages[n-1].high_resolution_image for n in required)
        ):
            return cached
    from knowledge_product_workflow.developmental import review_evidence_revision
    evidence_revision = review_evidence_revision(workspace)
    previous = _bound_visual_report(workspace)
    if previous is not None and previous.evidence_revision != evidence_revision:
        previous = None
    expected = design.body_font_size * 72 / 72.27 if design.reader_layout_revision == 2 else None
    historical = None if previous else _historical_visual_candidate(workspace, profile, 2 if expected is not None else 0)
    metrics = [_measure_page(page, index, expected) for index, page in enumerate(pages, start=1)]
    texts = [page.extract_text() or '' for page in pages]
    for index, metric in enumerate(metrics):
        metric.page_context_sha256 = sha256(json.dumps(
            [texts[max(0,index-1):index+2],metric.width_pt,metric.height_pt],ensure_ascii=False).encode()).hexdigest()
    findings = [finding for metric in metrics for finding in _page_findings(metric)] + _closure_findings(metrics)
    qmd = workspace.root / "delivery" / "book.qmd"
    table_columns = []
    if qmd.is_file():
        from knowledge_product_workflow.illustrations import validate_style_assets
        findings.extend(validate_style_assets(workspace, qmd.read_text(encoding="utf-8"), final=True))
        for line_number, line in enumerate(qmd.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip().startswith("|"):
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                    table_columns.append(len(cells))
                    if len(cells) >= 5:
                        findings.append(QualityFinding(code="PDF_MULTI_COLUMN_TABLE_REVIEW_REQUIRED", severity=Severity.WARNING, location=f"delivery/book.qmd:第 {line_number} 行", message=f"表格有 {len(cells)} 列；需在成页检查最窄列、表头与行高，拥挤时拆表而非缩字。"))
    if previous:
        findings.extend(QualityFinding(code=f.code, severity=f.severity, location=f.location, message=f.message)
                        for f in previous.findings if f.code == "PDF_CLEAR_PREVIEW_BLANK_PAGE")
    comparison = previous or (historical[0] if historical else None)
    selected = _sample_pages(metrics, [*findings, *(_observation_pages(comparison) if comparison else [])], sample_limit)
    old_pages = {page.page_number: page for page in previous.pages} if previous else {}
    for number in selected:
        old_page = old_pages.get(number)
        if old_page and _clear_image_valid(workspace, old_page, dpi):
            for field in ("high_resolution_image", "high_resolution_image_sha256", "high_resolution_dpi"):
                setattr(metrics[number-1], field, getattr(old_page, field))
    report = VisualAuditReport(
        evidence_revision=evidence_revision,
        typography_measurement_revision=2 if expected is not None else 0,
        created_at=datetime.now(UTC), pdf_sha256=_digest(pdf),
        preview_manifest_sha256=_digest(preview), profile_sha256=_digest(profile),
        observation_input_sha256=sha256((_digest(pdf) + _digest(preview) + _digest(profile) + _figure_sources_digest(workspace) + VISUAL_RULE_SET + ("|typography=1" if expected is not None else "") + ("|evidence=1" if evidence_revision else "")).encode()).hexdigest(),
        figure_sources_sha256=_figure_sources_digest(workspace), page_count=len(metrics),
        pages=metrics, sampled_pages=selected, sample_limit=sample_limit,
        findings=[], table_column_counts=table_columns,
    )
    def persist():
        report.findings = []
        for finding in findings:
            match = re.search(r"第 (\d+) 页", finding.location)
            number = int(match.group(1)) if match else 0
            metric = metrics[number-1] if 1 <= number <= len(metrics) else None
            report.findings.append(VisualAuditFinding(
                **finding.model_dump(), page_number=metric.page_number if metric else None,
                page_type=metric.page_type if metric else None,
                measurement={"font_median_pt": metric.body_font_median_pt, "minimum_font_pt": metric.minimum_content_font_pt, "body_height_fraction": metric.body_height_fraction} if metric else {},
                responsible_skill="kp-chapter-writer" if "SPARSE" in finding.code or finding.code == "ILLUSTRATION_CONTENT_FAILED" else "kp-publisher",
                screenshot_path=metric.high_resolution_image if metric else None,
                screenshot_sha256=metric.high_resolution_image_sha256 if metric else None,
            ))
        if previous:
            report.observations = [item for item in previous.observations
                if item.page_number in selected and item.page_number in old_pages
                and metrics[item.page_number-1].high_resolution_image_sha256 is not None
                and old_pages[item.page_number].high_resolution_image_sha256 == metrics[item.page_number-1].high_resolution_image_sha256
                and old_pages[item.page_number].high_resolution_dpi == metrics[item.page_number-1].high_resolution_dpi]
        elif historical and historical[0].page_count == report.page_count:
            old_report, old_digest = historical
            old_by_number = {p.page_number:p for p in old_report.pages}
            reused = []
            for item in old_report.observations:
                if item.scope != 'page' or item.assessment != 'passed' or item.page_number not in selected:
                    continue
                old = old_by_number.get(item.page_number)
                new = metrics[item.page_number-1]
                if (old is not None and old.page_context_sha256 is not None
                        and old.page_context_sha256 == new.page_context_sha256
                        and old.high_resolution_image_sha256 == new.high_resolution_image_sha256
                        and old.high_resolution_dpi == new.high_resolution_dpi
                        and _clear_image_valid(workspace, old, dpi) and _clear_image_valid(workspace,new,dpi)):
                    reused.append(item.model_copy(update={'reuse_origin':{
                        'report_sha256':old_digest,'previous_pdf_sha256':old_report.pdf_sha256,
                        'previous_image_sha256':old.high_resolution_image_sha256,
                        'current_image_sha256':new.high_resolution_image_sha256,
                        'scope':'page_visible_only'}}))
            report.observations = reused
        report.observation_state = "observed" if report.observations else "manual_required"
        path = audit_path(workspace)
        if path.is_file():
            history = path.parent / "history/visual-audit"
            history.mkdir(parents=True, exist_ok=True)
            archived = history / f"audit-{_digest(path)}.json"
            if not archived.exists():
                archived.write_bytes(path.read_bytes())
        temporary = path.with_suffix(".tmp")
        temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        workspace.append_event("pdf_visual_audited", {"page_count": len(metrics), "sampled_pages": selected, "rule_set": VISUAL_RULE_SET, "report_sha256": _digest(path)})
    pdftoppm = resolve_pdftoppm()
    from uuid import uuid4
    image_root = workspace.root / "runs" / "visual-audit-pages" / uuid4().hex[:12]
    for number in selected:
        if _clear_image_valid(workspace, metrics[number-1], dpi) or pdftoppm is None:
            continue
        image_root.mkdir(parents=True, exist_ok=True)
        prefix = image_root / f"page-{number:03d}"
        image = prefix.with_suffix(".png")
        try:
            if os.name == "nt" and max(len(str(pdf)), len(str(image))) >= 260:
                from .pdf_preview import _render_png_via_pipe
                _render_png_via_pipe(pdftoppm, pdf, image, number, dpi)
            else:
                result = subprocess.run(
                    [str(pdftoppm), "-f", str(number), "-l", str(number), "-r", str(dpi), "-singlefile", "-png", str(pdf), str(prefix)],
                    capture_output=True, text=True, timeout=30, check=False,
                )
                if result.returncode != 0 or not image.is_file() or not image.stat().st_size:
                    raise RuntimeError(f"第 {number} 页高分辨率预览失败：{result.stderr.strip() or result.returncode}")
            metric = metrics[number-1]
            metric.high_resolution_image = image.relative_to(workspace.root).as_posix()
            metric.high_resolution_image_sha256, metric.high_resolution_dpi = _digest(image), dpi
            if not _clear_image_valid(workspace, metric, dpi):
                metric.high_resolution_image = metric.high_resolution_image_sha256 = metric.high_resolution_dpi = None
                raise RuntimeError(f"第 {number} 页清晰PNG无效，保留故障工件。")
            if not png_has_visible_ink(image) and not any(f.code == "PDF_CLEAR_PREVIEW_BLANK_PAGE" and f.location == f"delivery/book.pdf:第 {number} 页" for f in findings):
                findings.append(QualityFinding(code="PDF_CLEAR_PREVIEW_BLANK_PAGE", severity=Severity.BLOCKER,
                    location=f"delivery/book.pdf:第 {number} 页", message="清晰PNG有效，但实际页面没有可见内容；保留截图并修订空白页，不作为渲染器故障。"))
        except (subprocess.TimeoutExpired, OSError, RuntimeError) as error:
            persist()
            if isinstance(error, subprocess.TimeoutExpired):
                raise RuntimeError(f"第 {number} 页清晰预览超过 30 秒；已保留完成页与缺项，先诊断再补页。") from error
            raise
    if evidence_revision:
        for metric in metrics:
            if metric.high_resolution_image and pdftoppm is not None:
                metric.bottom_gap_fraction = _bottom_gap(pdf, metric.page_number, pdftoppm)
        findings.extend(_object_flow_findings(metrics, texts, qmd.read_text(encoding='utf8') if qmd.is_file() else ''))
    persist()
    return report


@_verification_read
def record_visual_observations(workspace: Workspace, observations: list[VisualObservation]) -> VisualAuditReport:
    if any(item.reuse_origin is not None for item in observations):
        raise ValueError('观察派生来源只能由当前控制流程计算，不能手填复用回执')
    report = current_visual_audit(workspace)
    if report is None:
        raise ValueError("视觉量测报告不存在或已过期；请先运行 kp audit-pdf-visual。")
    if not observations:
        raise ValueError("视觉观察至少填写一个已查看页面，无法用空记录宣称已审阅。")
    invalid = sorted({item.page_number for item in observations} - set(report.sampled_pages))
    if invalid:
        raise ValueError(f"观察记录只能引用本次清晰预览页：{invalid}")
    for item in observations:
        page = report.pages[item.page_number-1]
        if report.evidence_revision and (issue := _evidence_issues(report, item)):
            raise ValueError(issue)
        if ((item.observation_input_sha256 is not None and item.observation_input_sha256 != report.observation_input_sha256)
                or (item.page_image_sha256 is not None and item.page_image_sha256 != page.high_resolution_image_sha256)):
            raise ValueError('观察输入或PNG身份已过期；读取当前pdf-observation-template，不移绑旧观察')
    if any(not report.pages[item.page_number-1].high_resolution_image for item in observations):
        raise ValueError("缺少当前清晰页图；不能提交模型视觉观察，保留人工待核")
    if any(not item.observation_input_sha256 or not item.page_image_sha256 for item in observations):
        raise ValueError('新观察须携当前输入与PNG身份；旧记录只读，使用当前pdf-observation-template')
    merged = {item.page_number: item for item in report.observations}
    merged.update({item.page_number: item for item in observations})
    updated = report.model_copy(update={"observations": [merged[number] for number in sorted(merged)], "observation_state": "observed"})
    history = audit_path(workspace).parent / "history/visual-audit"
    _v_mkdir(history, parents=True, exist_ok=True)
    archived = history / f"audit-{_digest(audit_path(workspace))}.json"
    if not archived.exists():
        _v_write_bytes(archived, _v_read_bytes(audit_path(workspace)))
    _v_write_text(audit_path(workspace), updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
    workspace.append_event("pdf_visual_observations_recorded", {"count": len(observations), "report_sha256": _digest(audit_path(workspace))})
    return updated
