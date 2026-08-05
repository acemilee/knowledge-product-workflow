from knowledge_product_workflow.assumption_ledger import (
    AssumptionDisposition,
    AssumptionImpact,
    AssumptionLedgerEntry,
    append_assumption_entry,
    validate_assumptions,
)
from knowledge_product_workflow.workflows.validation import run_validation
from knowledge_product_workflow.workspace import Workspace


def _entry(disposition: AssumptionDisposition) -> AssumptionLedgerEntry:
    return AssumptionLedgerEntry(
        assumption_id="interview-weight",
        statement="系统设计在目标岗位中占较高考核权重。",
        impact=AssumptionImpact.CURRICULUM_WEIGHT,
        disposition=disposition,
        rationale="决定课程章节权重。",
        evidence_claim_ids=["claim-interview-weight"]
        if disposition is AssumptionDisposition.VERIFIED
        else [],
    )


def test_unresolved_assumption_blocks_validation(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    path = workspace.root / "research" / "assumption-ledger.jsonl"
    append_assumption_entry(path, _entry(AssumptionDisposition.EVIDENCE_NEEDED))

    _, report = run_validation(workspace, set())

    assert any(finding.code == "ASSUMPTION_UNRESOLVED" for finding in report.findings)


def test_reframed_assumption_is_not_a_fact_blocker(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    path = workspace.root / "research" / "assumption-ledger.jsonl"
    append_assumption_entry(path, _entry(AssumptionDisposition.EVIDENCE_NEEDED))
    append_assumption_entry(path, _entry(AssumptionDisposition.REFRAMED_AS_CASE))

    assert validate_assumptions(path) == []
