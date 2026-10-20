"""Version-bound preparation for human review, separate from publication authority."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from knowledge_product_workflow.quality import (
    QualityFinding,
    QualityInput,
    Severity,
    quality_report_inputs,
)
from knowledge_product_workflow.verification_context import read_text, verified_read, write_text
from knowledge_product_workflow.workspace import Workspace

HUMAN_ONLY_CODES = frozenset({'PDF_DIAGRAM_MANUAL_VISUAL_REVIEW_REQUIRED', 'PDF_FULL_VISUAL_COVERAGE_REQUIRED'})


class DeliveryPreflight(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: int = 1
    rule_set: str = 'delivery-preflight-v1'
    generated_at: datetime
    inputs: list[QualityInput]
    findings: list[QualityFinding]
    pending_human_items: list[QualityFinding] = Field(default_factory=list)
    ready_for_human_review: bool


def _inputs(workspace):
    return [item for item in quality_report_inputs(workspace, 'delivery')
            if item.path not in {'runs/pdf-visual-review.json', 'runs/interaction/pdf.json'}]


@verified_read
def current_delivery_preflight(workspace: Workspace) -> DeliveryPreflight | None:
    path = workspace.root / 'runs/delivery-preflight.json'
    try:
        report = DeliveryPreflight.model_validate_json(read_text(path, encoding='utf8'))
    except (OSError, ValueError):
        return None
    if report.schema_version != 1 or report.rule_set != 'delivery-preflight-v1' or report.inputs != _inputs(workspace):
        return None
    if (any(item.code not in HUMAN_ONLY_CODES for item in report.pending_human_items)
            or report.ready_for_human_review != (not any(f.severity is Severity.BLOCKER for f in report.findings))):
        return None
    return report


@verified_read
def run_delivery_preflight(workspace: Workspace) -> DeliveryPreflight:
    from knowledge_product_workflow.production_intake import consistent_production_read
    from knowledge_product_workflow.workflows.validation import collect_delivery_findings

    with consistent_production_read(workspace):
        all_findings = collect_delivery_findings(workspace)
        pending = [f for f in all_findings if f.code in HUMAN_ONLY_CODES]
        findings = [f for f in all_findings if f.code not in HUMAN_ONLY_CODES]
        report = DeliveryPreflight(generated_at=datetime.now(UTC), inputs=_inputs(workspace),
            findings=findings, pending_human_items=pending,
            ready_for_human_review=not any(f.severity is Severity.BLOCKER for f in findings))
        write_text(workspace.root / 'runs/delivery-preflight.json', report.model_dump_json(indent=2), encoding='utf8')
        return report


def require_delivery_preflight(workspace: Workspace) -> None:
    report = current_delivery_preflight(workspace)
    if report is None or not report.ready_for_human_review:
        raise ValueError('当前交付自动预检未通过或已过期；先运行 kp delivery-preflight，不记录人工通过。')
