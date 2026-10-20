"""Append-only source ledger and claim coverage validation."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field, HttpUrl, field_validator

from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.verification_context import (
    write_open as _v_write_open,
)


class SourceLedgerEntry(BaseModel):
    """Evidence retained for one or more publishable claims."""

    source_id: str
    url: HttpUrl
    accessed_at: str
    excerpt: str
    claim_ids: list[str]
    claim_contract_sha256: dict[str, str] = Field(default_factory=dict)
    license_note: str

    @field_validator("claim_contract_sha256")
    @classmethod
    def validate_contract_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            not claim_id.strip() or not re.fullmatch(r"[0-9a-f]{64}", contract_sha256)
            for claim_id, contract_sha256 in value.items()
        ):
            raise ValueError("Claim 合约绑定必须使用非空 Claim ID 和 SHA-256")
        return {claim_id.casefold(): contract_sha256 for claim_id, contract_sha256 in value.items()}


def append_source_entry(path: Path, entry: SourceLedgerEntry) -> None:
    """Append a source record without rewriting existing evidence."""
    _v_mkdir(path.parent,parents=True,exist_ok=True)
    with _v_write_open(path, "a", encoding="utf-8") as handle:
        handle.write(entry.model_dump_json() + "\n")


def validate_claims(
    claim_ids: set[str], ledger_path: Path, expected_contract_sha256: dict[str, str] | None = None
) -> list[QualityFinding]:
    """Return blockers for uncovered Claims or evidence bound to obsolete contracts."""
    covered_claims: set[str] = set()
    stale_claims: set[str] = set()
    if _v_exists(ledger_path):
        for line in _v_read_text(ledger_path, encoding="utf-8").splitlines():
            if line.strip():
                entry = SourceLedgerEntry.model_validate_json(line)
                for claim_id in entry.claim_ids:
                    normalized = claim_id.casefold()
                    expected = (expected_contract_sha256 or {}).get(normalized)
                    if expected is None or entry.claim_contract_sha256.get(normalized) == expected:
                        covered_claims.add(normalized)
                    else:
                        stale_claims.add(normalized)
    normalized_claims = {claim_id.casefold() for claim_id in claim_ids}
    findings = [
        QualityFinding(
            code="MISSING_CLAIM_SOURCE",
            severity=Severity.BLOCKER,
            location=claim_id,
            message="事实声明没有已登记的来源台账条目",
        )
        for claim_id in sorted(normalized_claims - covered_claims - stale_claims)
    ]
    findings.extend(
        QualityFinding(
            code="CLAIM_SOURCE_CONTRACT_MISMATCH",
            severity=Severity.BLOCKER,
            location=claim_id,
            message="来源台账记录绑定的是旧章节合约；请重新核验并登记当前版本来源。",
        )
        for claim_id in sorted((stale_claims - covered_claims) & normalized_claims)
    )
    return findings
