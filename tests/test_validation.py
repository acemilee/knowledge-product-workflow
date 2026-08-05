from pathlib import Path

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.quality import QualityReportValidity, quality_report_validity
from knowledge_product_workflow.source_ledger import SourceLedgerEntry, append_source_entry
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workflows.validation import run_validation
from knowledge_product_workflow.workspace import Workspace


def test_validation_writes_blocker_report_for_uncovered_claim(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "ai-agent-foundations")
    ledger_path = workspace.root / "research" / "source-ledger.jsonl"
    append_source_entry(
        ledger_path,
        SourceLedgerEntry(
            source_id="official-docs",
            url="https://example.com/docs",
            accessed_at="2026-07-28T00:00:00Z",
            excerpt="A supported fact.",
            claim_ids=["claim-001"],
            license_note="Fixture only",
        ),
    )

    report_path, report = run_validation(workspace, {"claim-001", "claim-002"})

    assert report_path.exists()
    assert report.publishable is False
    assert report.findings[0].code == "MISSING_CLAIM_SOURCE"


def test_validation_archives_a_versioned_report_bound_to_current_inputs(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(workspace, "Guide", DetailConstraints(target_words=1))

    report_path, report = run_validation(workspace)

    assert report.schema_version == 2
    assert report.validation_stage == "quality"
    assert report.report_id
    assert report.inputs
    assert report_path == workspace.root / "delivery" / "qa-report.json"
    assert (workspace.root / "runs" / "quality-reports" / f"{report.report_id}.json").is_file()
    assert quality_report_validity(workspace, report, "quality") is QualityReportValidity.CURRENT

    brief.write_text(brief.read_text(encoding="utf-8") + "\n补充说明。\n", encoding="utf-8")

    assert quality_report_validity(workspace, report, "quality") is QualityReportValidity.STALE
