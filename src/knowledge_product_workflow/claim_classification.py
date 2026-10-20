"""Append-only, contract-bound distinctions between facts and teaching constructs."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_product_workflow.claims import current_claim_contract_bindings
from knowledge_product_workflow.content_quality import high_risk_factual_lines
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.verification_context import (
    write_open as _v_write_open,
)
from knowledge_product_workflow.workspace import Workspace


class ClaimKind(StrEnum):
    FACTUAL = "factual"
    AUTHOR_RECOMMENDATION = "author_recommendation"
    SCENARIO = "scenario"


class ClaimClassificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^claim-[a-z0-9][a-z0-9-]*$")
    kind: ClaimKind
    statement: str = Field(min_length=1)
    heading_anchor: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @field_validator("statement", "heading_anchor", "reason")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        cleaned = unicodedata.normalize("NFC", value).strip()
        if not cleaned:
            raise ValueError("Claim 分类文本不能为空")
        return cleaned


class ClaimClassification(ClaimClassificationInput):
    schema_version: int = 1
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    statement_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime


def classification_path(workspace: Workspace) -> Path:
    return workspace.root / "research" / "claim-classifications.jsonl"


def classification_schema() -> dict[str, object]:
    return {
        "schema_version": 1,
        "input_schema": ClaimClassificationInput.model_json_schema(),
        "kinds": {
            "factual": "外部事实、规则、统计或效果；必须有来源。",
            "author_recommendation": "作者提出的可选做法，须说明适用条件与验证方式。",
            "scenario": "后台登记的教学情境；正文无需展示分类身份标签。",
        },
    }


def _digest(value: str) -> str:
    return sha256(unicodedata.normalize("NFC", value).strip().encode("utf-8")).hexdigest()


def append_claim_classification(workspace: Workspace, value: ClaimClassificationInput) -> ClaimClassification:
    bindings = current_claim_contract_bindings(workspace, statements={value.claim_id: {
        k: value.model_dump(mode="json")[k] for k in ("kind", "statement", "heading_anchor")}})
    contract = bindings.get(value.claim_id)
    if contract is None:
        raise ValueError(f"Claim ID 未在当前章节合约中声明：{value.claim_id}")
    if value.kind is ClaimKind.AUTHOR_RECOMMENDATION and (
        high_risk_factual_lines(value.statement)
        or re.search(r"(?:提升|降低|增长|减少|达到|提高).{0,24}\d+(?:\.\d+)?%", value.statement)
    ):
        raise ValueError("建议声明包含外部事实或量化效果；请拆分为需来源的 factual Claim。")
    record = ClaimClassification(
        **value.model_dump(),
        contract_sha256=contract,
        statement_sha256=_digest(value.statement),
        recorded_at=datetime.now(UTC),
    )
    path = classification_path(workspace)
    _v_mkdir(path.parent,parents=True,exist_ok=True)
    with _v_write_open(path, "a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")
    workspace.append_event("claim_classified", {"claim_id": value.claim_id, "kind": value.kind.value})
    return record


def current_claim_classifications(workspace: Workspace) -> dict[str, ClaimClassification]:
    path = classification_path(workspace)
    if not _v_is_file(path):
        return {}
    bindings = current_claim_contract_bindings(workspace)
    latest: dict[str, ClaimClassification] = {}
    for line in _v_read_text(path, encoding="utf-8").splitlines():
        if line.strip():
            record = ClaimClassification.model_validate_json(line)
            latest[record.claim_id] = record
    return {
        claim_id: record
        for claim_id, record in latest.items()
        if bindings.get(claim_id) == record.contract_sha256
        and _digest(record.statement) == record.statement_sha256
    }


def factual_claim_ids(workspace: Workspace, bindings: dict[str, str]) -> set[str]:
    try:
        classifications = current_claim_classifications(workspace)
    except (OSError, ValueError):
        return set(bindings)
    return {
        claim_id for claim_id in bindings
        if classifications.get(claim_id) is None or classifications[claim_id].kind is ClaimKind.FACTUAL
    }


def validate_classification_ledger(workspace: Workspace) -> list[QualityFinding]:
    try:
        current_claim_classifications(workspace)
    except (OSError, ValueError) as error:
        return [QualityFinding(
            code="CLAIM_CLASSIFICATION_LEDGER_INVALID",
            severity=Severity.BLOCKER,
            location="research/claim-classifications.jsonl",
            message=f"Claim 分类台账格式无效：{error}",
        )]
    return []


def validate_nonfactual_claim_boundaries(
    workspace: Workspace, chapter_number: int, body: str, claim_ids: list[str], locations: dict[str, str]
) -> list[QualityFinding]:
    """Bind registered teaching content to its section, without provenance labels."""
    try:
        classifications = current_claim_classifications(workspace)
    except (OSError, ValueError) as error:
        return [QualityFinding(
            code="CLAIM_CLASSIFICATION_LEDGER_INVALID",
            severity=Severity.BLOCKER,
            location="research/claim-classifications.jsonl",
            message=f"Claim 分类台账格式无效：{error}",
        )]
    findings: list[QualityFinding] = []
    headings = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$", re.MULTILINE)
    matches = list(headings.finditer(body))
    sections = {
        match.group("title").strip(): body[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(body)]
        for index, match in enumerate(matches)
    }
    for claim_id in claim_ids:
        record = classifications.get(claim_id)
        if record is None or record.kind is ClaimKind.FACTUAL:
            continue
        anchor = locations.get(claim_id, record.heading_anchor)
        section = sections.get(anchor, "")
        if anchor != record.heading_anchor or record.statement not in section:
            findings.append(QualityFinding(
                code="CLAIM_CLASSIFICATION_BOUNDARY_MISSING",
                severity=Severity.BLOCKER,
                location=f"chapters/chapter-{chapter_number:02d}.md:{anchor}",
                message=f"{claim_id} 的后台声明与绑定标题下的正文内容不一致。",
            ))
    return findings
