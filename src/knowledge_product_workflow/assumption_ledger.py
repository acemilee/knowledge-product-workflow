"""Append-only assumptions that may affect a knowledge product's published claims."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from knowledge_product_workflow.quality import QualityFinding, Severity


class AssumptionImpact(StrEnum):
    """Product decisions an assumption is allowed to influence."""

    AUDIENCE = "audience"
    POSITIONING = "positioning"
    CURRICULUM_WEIGHT = "curriculum_weight"
    SCOPE = "scope"
    CONCLUSION = "conclusion"


class AssumptionDisposition(StrEnum):
    """How an assumption is resolved before reader-visible delivery."""

    EVIDENCE_NEEDED = "evidence_needed"
    VERIFIED = "verified"
    REFRAMED_AS_CASE = "reframed_as_case"


class AssumptionLedgerEntry(BaseModel):
    """One append-only decision about an assumption and its evidence boundary."""

    assumption_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    statement: str = Field(min_length=1)
    impact: AssumptionImpact
    disposition: AssumptionDisposition
    rationale: str = Field(min_length=1)
    evidence_claim_ids: list[str] = Field(default_factory=list)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def append_assumption_entry(path: Path, entry: AssumptionLedgerEntry) -> None:
    """Append a decision revision; the latest row for an ID is authoritative."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry.model_dump_json() + "\n")


def latest_assumptions(path: Path) -> dict[str, AssumptionLedgerEntry]:
    """Read the latest append-only state for each assumption identifier."""
    latest: dict[str, AssumptionLedgerEntry] = {}
    if not path.is_file():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = AssumptionLedgerEntry.model_validate_json(line)
            latest[entry.assumption_id] = entry
    return latest


def validate_assumptions(path: Path) -> list[QualityFinding]:
    """Return blockers for unresolved assumptions or unbacked verified assumptions."""
    try:
        latest = latest_assumptions(path)
    except (OSError, ValidationError, ValueError) as error:
        return [
            QualityFinding(
                code="ASSUMPTION_LEDGER_INVALID",
                severity=Severity.BLOCKER,
                location="research/assumption-ledger.jsonl",
                message=f"假设台账格式无效：{error}",
            )
        ]
    findings: list[QualityFinding] = []
    for entry in latest.values():
        if entry.disposition is AssumptionDisposition.EVIDENCE_NEEDED:
            findings.append(
                QualityFinding(
                    code="ASSUMPTION_UNRESOLVED",
                    severity=Severity.BLOCKER,
                    location=f"research/assumption-ledger.jsonl:{entry.assumption_id}",
                    message=(
                        "会影响受众、定位、范围、章节权重或结论的假设尚未证实或降级为情境案例。"
                    ),
                )
            )
        elif entry.disposition is AssumptionDisposition.VERIFIED and not entry.evidence_claim_ids:
            findings.append(
                QualityFinding(
                    code="ASSUMPTION_EVIDENCE_MISSING",
                    severity=Severity.BLOCKER,
                    location=f"research/assumption-ledger.jsonl:{entry.assumption_id}",
                    message="已验证假设必须关联至少一个事实声明 ID。",
                )
            )
    return findings


def required_assumption_claim_ids(path: Path) -> set[str]:
    """Return claim IDs required by currently verified assumptions."""
    try:
        latest = latest_assumptions(path)
    except (OSError, ValidationError, ValueError):
        return set()
    return {
        claim_id
        for entry in latest.values()
        if entry.disposition is AssumptionDisposition.VERIFIED
        for claim_id in entry.evidence_claim_ids
    }
