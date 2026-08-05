from pathlib import Path

from knowledge_product_workflow.source_ledger import (
    SourceLedgerEntry,
    append_source_entry,
    validate_claims,
)


def test_validate_claims_reports_a_missing_source_mapping(tmp_path: Path) -> None:
    ledger_path = tmp_path / "source-ledger.jsonl"
    append_source_entry(
        ledger_path,
        SourceLedgerEntry(
            source_id="official-docs",
            url="https://example.com/docs",
            accessed_at="2026-07-28T00:00:00Z",
            excerpt="A supported fact.",
            claim_ids=["claim-001"],
            license_note="Official documentation",
        ),
    )

    findings = validate_claims({"claim-001", "claim-002"}, ledger_path)

    assert [finding.code for finding in findings] == ["MISSING_CLAIM_SOURCE"]
    assert findings[0].location == "claim-002"


def test_validate_claims_accepts_complete_source_coverage(tmp_path: Path) -> None:
    ledger_path = tmp_path / "source-ledger.jsonl"
    append_source_entry(
        ledger_path,
        SourceLedgerEntry(
            source_id="official-docs",
            url="https://example.com/docs",
            accessed_at="2026-07-28T00:00:00Z",
            excerpt="A supported fact.",
            claim_ids=["claim-001", "claim-002"],
            license_note="Official documentation",
        ),
    )

    assert validate_claims({"claim-001", "claim-002"}, ledger_path) == []


def test_validate_claims_rejects_evidence_bound_to_an_obsolete_contract(tmp_path: Path) -> None:
    ledger_path = tmp_path / "source-ledger.jsonl"
    append_source_entry(
        ledger_path,
        SourceLedgerEntry(
            source_id="official-docs",
            url="https://example.com/docs",
            accessed_at="2026-07-28T00:00:00Z",
            excerpt="A supported fact.",
            claim_ids=["claim-001"],
            claim_contract_sha256={"claim-001": "a" * 64},
            license_note="Official documentation",
        ),
    )

    findings = validate_claims(
        {"claim-001"}, ledger_path, expected_contract_sha256={"claim-001": "b" * 64}
    )

    assert [finding.code for finding in findings] == ["CLAIM_SOURCE_CONTRACT_MISMATCH"]
