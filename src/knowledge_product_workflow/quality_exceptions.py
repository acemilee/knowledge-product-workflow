"""Version-bound, narrowly scoped waivers for confirmed quality-gate defects."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_product_workflow.chapter_compliance import ChapterCompliance, compliance_path
from knowledge_product_workflow.claim_quality import validate_undeclared_high_risk_claims
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.source_ledger import validate_claims
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from knowledge_product_workflow.workspace import Workspace

CONTROLLED_EXCEPTIONS_FILENAME = "controlled-exceptions.json"
CONTROLLED_EXCEPTIONS_SCHEMA_VERSION = 1
_SUPPORTED_RULE_IDS = frozenset({"UNDECLARED_HIGH_RISK_CLAIM"})


class ControlledExceptionInput(BaseModel):
    """Internal version-bound exception record before approval metadata is added."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    chapter_body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_paths: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=500)

    @field_validator("rule_id", "location", "claim_id", "rationale")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("受控例外文本字段不能为空")
        return cleaned

    @field_validator("evidence_paths")
    @classmethod
    def require_unique_evidence_paths(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().replace("\\", "/") for item in value if item.strip()]
        if not cleaned:
            raise ValueError("受控例外至少需要一条证据工件路径")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("受控例外中的证据工件路径不能重复")
        return cleaned


class ControlledExceptionRequest(BaseModel):
    """The semantic-only exception input a host is allowed to submit."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    evidence_paths: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=500)

    @field_validator("rule_id", "location", "claim_id", "rationale")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("受控例外文本字段不能为空")
        return cleaned

    @field_validator("evidence_paths")
    @classmethod
    def require_unique_evidence_paths(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().replace("\\", "/") for item in value if item.strip()]
        if not cleaned:
            raise ValueError("受控例外至少需要一条证据工件路径")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("受控例外中的证据工件路径不能重复")
        return cleaned

class ControlledException(ControlledExceptionInput):
    """Persisted waiver proof bound to one exact reader-visible finding."""

    approved_by: str = Field(min_length=1, max_length=120)
    approved_at: datetime

    @field_validator("approved_by")
    @classmethod
    def normalize_approver(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("受控例外必须记录审批身份")
        return cleaned


class ControlledExceptions(BaseModel):
    """The complete, atomically written exception inventory for one workspace."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = CONTROLLED_EXCEPTIONS_SCHEMA_VERSION
    exceptions: list[ControlledException] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_rule_locations(self) -> ControlledExceptions:
        keys = [(item.rule_id, item.location) for item in self.exceptions]
        if len(set(keys)) != len(keys):
            raise ValueError("同一规则和命中位置只能登记一条受控例外")
        return self


def controlled_exceptions_path(workspace: Workspace) -> Path:
    """Return the only CLI-managed controlled-exception record."""
    return workspace.root / "runs" / CONTROLLED_EXCEPTIONS_FILENAME


def controlled_exception_schema() -> dict[str, object]:
    """Expose a semantic-only host-fillable schema."""
    return {
        "schema_version": CONTROLLED_EXCEPTIONS_SCHEMA_VERSION,
        "input_type": "ControlledExceptionRequest",
        "supported_rule_ids": sorted(_SUPPORTED_RULE_IDS),
        "fields": {
            "rule_id": "确认存在检查器缺陷的规则 ID",
            "location": "当前质量报告的精确 location",
            "claim_id": "当前章节合约中已声明的 Claim ID",
            "evidence_paths": "当前工作区内、可读取的证据工件路径数组",
            "rationale": "经人工确认的误判理由",
        },
        "cli_managed_fields": ["chapter_body_sha256", "contract_sha256", "approved_by", "approved_at"],
        "guarantee": "例外只对规则、位置、正文、合约和证据均匹配的当前命中项生效。",
    }


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _workspace_path(workspace: Workspace, stored_path: str) -> Path:
    relative = PurePosixPath(stored_path)
    if (
        relative.is_absolute()
        or relative.as_posix() != stored_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("受控例外证据路径必须是工作区内的规范相对路径")
    candidate = workspace.root.resolve().joinpath(*relative.parts).resolve()
    if not candidate.is_relative_to(workspace.root.resolve()):
        raise ValueError("受控例外证据路径越出当前工作区")
    return candidate


def _load(path: Path) -> ControlledExceptions:
    try:
        return ControlledExceptions.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"受控例外记录格式无效：{path}") from error


def load_controlled_exceptions(workspace: Workspace) -> ControlledExceptions | None:
    """Read the current record when one exists; absence is a normal no-exception state."""
    path = controlled_exceptions_path(workspace)
    return _load(path) if path.is_file() else None


def controlled_exception_input_paths(workspace: Workspace) -> list[Path]:
    """Return exception evidence that must invalidate a bound quality report when changed."""
    record_path = controlled_exceptions_path(workspace)
    if not record_path.is_file():
        return []
    paths = [record_path]
    try:
        record = _load(record_path)
        for exception in record.exceptions:
            for stored_path in exception.evidence_paths:
                candidate = _workspace_path(workspace, stored_path)
                if candidate.is_file() and candidate not in paths:
                    paths.append(candidate)
    except ValueError:
        # The record itself remains a report input so a corrected replacement invalidates the report.
        pass
    return paths


def _bind_exception_request(
    workspace: Workspace, request: ControlledExceptionRequest
) -> ControlledExceptionInput:
    chapter_number = _chapter_number(request.location)
    if chapter_number is None:
        raise ValueError("受控例外位置必须指向 chapters/chapter-XX.md。")
    if not request.location.startswith(f"chapters/chapter-{chapter_number:02d}.md:第 "):
        raise ValueError("受控例外位置必须与当前章节路径和行号完全一致。")
    try:
        contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
        compliance = ChapterCompliance.model_validate_json(
            compliance_path(workspace, chapter_number).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"无法读取当前章节合约或合规记录：{error}") from error
    normalized_claim = request.claim_id.casefold()
    if normalized_claim not in {item.casefold() for item in chapter.claim_ids}:
        raise ValueError("受控例外引用的 Claim 未在当前章节合约中声明。")
    if normalized_claim not in {item.casefold() for item in compliance.claim_locations}:
        raise ValueError("受控例外引用的 Claim 未映射到当前章节正文。")
    return ControlledExceptionInput(
        **request.model_dump(),
        chapter_body_sha256=compliance.chapter_body_sha256,
        contract_sha256=sha256(contract_path.read_bytes()).hexdigest(),
    )


def submit_controlled_exceptions(
    workspace: Workspace, inputs: list[ControlledExceptionRequest], approved_by: str
) -> Path:
    """Atomically replace the inventory and bind semantic requests to current inputs."""
    if not inputs:
        raise ValueError("至少需要一条受控例外才能提交")
    approved = approved_by.strip()
    if not approved:
        raise ValueError("提交受控例外需要审批身份")
    records = [_bind_exception_request(workspace, item) for item in inputs]
    record = ControlledExceptions(
        exceptions=[
            ControlledException(
                **item.model_dump(), approved_by=approved, approved_at=datetime.now(UTC)
            )
            for item in records
        ]
    )
    errors = validate_controlled_exceptions(workspace, record)
    if errors:
        raise ValueError(errors[0].message)
    output = controlled_exceptions_path(workspace)
    _atomic_write(output, record.model_dump_json(indent=2) + "\n")
    workspace.append_event(
        "controlled_exceptions_submitted",
        {"exception_count": len(record.exceptions), "approved_by": approved},
    )
    return output


def _chapter_number(location: str) -> int | None:
    prefix = location.split(":", 1)[0]
    name = Path(prefix).name
    if not name.startswith("chapter-") or not name.endswith(".md"):
        return None
    try:
        return int(name.removeprefix("chapter-").removesuffix(".md"))
    except ValueError:
        return None


def _exception_error(exception: ControlledException, message: str) -> QualityFinding:
    return QualityFinding(
        code="CONTROLLED_EXCEPTION_INVALID",
        severity=Severity.BLOCKER,
        location=exception.location,
        message=message,
    )


def validate_controlled_exceptions(
    workspace: Workspace, record: ControlledExceptions | None = None
) -> list[QualityFinding]:
    """Verify every exception against current contracts, source evidence, and text version."""
    if record is None:
        try:
            record = load_controlled_exceptions(workspace)
        except ValueError as error:
            return [
                QualityFinding(
                    code="CONTROLLED_EXCEPTION_INVALID",
                    severity=Severity.BLOCKER,
                    location="runs/controlled-exceptions.json",
                    message=str(error),
                )
            ]
    if record is None:
        return []
    findings: list[QualityFinding] = []
    for exception in record.exceptions:
        if exception.rule_id not in _SUPPORTED_RULE_IDS:
            findings.append(_exception_error(exception, "该规则不支持受控例外。"))
            continue
        chapter_number = _chapter_number(exception.location)
        if chapter_number is None:
            findings.append(_exception_error(exception, "受控例外位置必须指向 chapters/chapter-XX.md。"))
            continue
        chapter_path = workspace.root / "chapters" / f"chapter-{chapter_number:02d}.md"
        if not exception.location.startswith(f"chapters/chapter-{chapter_number:02d}.md:第 "):
            findings.append(_exception_error(exception, "受控例外位置必须与当前章节路径和行号完全一致。"))
            continue
        try:
            contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
            compliance = ChapterCompliance.model_validate_json(
                compliance_path(workspace, chapter_number).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            findings.append(_exception_error(exception, f"无法读取当前章节合约或合规记录：{error}"))
            continue
        current_contract_sha = sha256(contract_path.read_bytes()).hexdigest()
        if compliance.chapter_body_sha256 != exception.chapter_body_sha256:
            findings.append(_exception_error(exception, "受控例外绑定的正文版本已过期。"))
            continue
        if current_contract_sha != exception.contract_sha256 or compliance.contract_sha256 != exception.contract_sha256:
            findings.append(_exception_error(exception, "受控例外绑定的章节合约版本已过期。"))
            continue
        if not chapter_path.is_file():
            findings.append(_exception_error(exception, "受控例外对应的当前章节正文不存在。"))
            continue
        try:
            _, body = parse_markdown_artifact(chapter_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            findings.append(_exception_error(exception, f"无法读取当前章节正文：{error}"))
            continue
        current_body_sha = sha256((body.strip() + "\n").encode("utf-8")).hexdigest()
        if current_body_sha != exception.chapter_body_sha256:
            findings.append(_exception_error(exception, "当前章节正文已变化，受控例外已过期。"))
            continue
        claim_id = exception.claim_id.casefold()
        if claim_id not in {item.casefold() for item in chapter.claim_ids}:
            findings.append(_exception_error(exception, "受控例外引用的 Claim 未在当前章节合约中声明。"))
            continue
        if claim_id not in {item.casefold() for item in compliance.claim_locations}:
            findings.append(_exception_error(exception, "受控例外引用的 Claim 未映射到当前章节正文。"))
            continue
        source_findings = validate_claims(
            {claim_id},
            workspace.root / "research" / "source-ledger.jsonl",
            {claim_id: exception.contract_sha256},
        )
        if source_findings:
            findings.append(_exception_error(exception, "受控例外关联 Claim 的来源台账无效或已过期。"))
            continue
        try:
            evidence = [_workspace_path(workspace, path) for path in exception.evidence_paths]
        except ValueError as error:
            findings.append(_exception_error(exception, str(error)))
            continue
        if any(not path.is_file() for path in evidence):
            findings.append(_exception_error(exception, "受控例外引用的证据工件不存在。"))
            continue
        current_findings = validate_undeclared_high_risk_claims(workspace, chapter_path, body)
        if (exception.rule_id, exception.location) not in {
            (finding.code, finding.location) for finding in current_findings
        }:
            findings.append(_exception_error(exception, "受控例外不再对应当前质量报告中的精确命中项。"))
    return findings


def apply_controlled_exceptions(
    workspace: Workspace, findings: list[QualityFinding]
) -> list[QualityFinding]:
    """Replace only fully proven, exact matching blockers with auditable info findings."""
    try:
        record = load_controlled_exceptions(workspace)
    except ValueError:
        record = None
    invalid = validate_controlled_exceptions(workspace, record)
    if record is None or invalid:
        return findings + invalid
    active = {(item.rule_id, item.location): item for item in record.exceptions}
    remaining: list[QualityFinding] = []
    applied: list[QualityFinding] = []
    for finding in findings:
        exception = active.get((finding.code, finding.location))
        if exception is None:
            remaining.append(finding)
            continue
        applied.append(
            QualityFinding(
                code="CONTROLLED_EXCEPTION_APPLIED",
                severity=Severity.INFO,
                location=finding.location,
                message=(
                    f"已应用受控例外：{exception.rule_id}；Claim={exception.claim_id}；"
                    f"审批身份={exception.approved_by}。"
                ),
            )
        )
    return remaining + applied


def controlled_exception_proofs(workspace: Workspace) -> list[dict[str, object]]:
    """Return manifest-safe disclosure only for exceptions that remain fully valid."""
    try:
        record = load_controlled_exceptions(workspace)
    except ValueError:
        return []
    if record is None or validate_controlled_exceptions(workspace, record):
        return []
    return [
        {
            "rule_id": item.rule_id,
            "location": item.location,
            "claim_id": item.claim_id,
            "chapter_body_sha256": item.chapter_body_sha256,
            "contract_sha256": item.contract_sha256,
            "evidence_paths": item.evidence_paths,
            "rationale": item.rationale,
            "approved_by": item.approved_by,
            "approved_at": item.approved_at.isoformat(),
        }
        for item in record.exceptions
    ]
