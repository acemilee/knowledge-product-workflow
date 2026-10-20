"""Publication batches keep a fresh public admission and reject changed inputs."""
import json
import os

import pytest

from knowledge_product_workflow import publishing
from knowledge_product_workflow import workspace_transactions as transactions
from knowledge_product_workflow.verification_context import (
    SnapshotChangedError,
    verification_scope,
    write_text,
)
from knowledge_product_workflow.workflows.authoring import create_chapter_draft
from tests.test_batch_chapter_read_view_v140 import record
from tests.test_change_impact_v140 import workspace_fixture


def fixture(path):
    workspace = workspace_fixture(path, count=3)
    create_chapter_draft(workspace, 1)
    return workspace


def scans(monkeypatch):
    calls = []
    original = transactions.pending_workspace_transactions

    def counted(workspace):
        calls.append(workspace.root)
        return original(workspace)

    monkeypatch.setattr(transactions, "pending_workspace_transactions", counted)
    return calls


def test_one_public_release_batch_does_not_reopen_per_chapter_views(tmp_path, monkeypatch):
    workspace = fixture(tmp_path)
    calls = scans(monkeypatch)
    findings = publishing.release_readiness_findings(workspace)
    assert any(f.code == "CHAPTER_COMPLIANCE_MISSING" for f in findings)
    assert sum(f.code == "RELEASE_CHAPTER_MISSING" for f in findings) == 2
    assert len(calls) == 1


def test_new_public_release_inside_parent_view_admits_again(tmp_path, monkeypatch):
    workspace = fixture(tmp_path)
    calls = scans(monkeypatch)
    with verification_scope(workspace), transactions.transaction_read_view(workspace):
        first = publishing.release_readiness_findings(workspace)
        count = len(calls)
        assert count > 0
        assert publishing.release_readiness_findings(workspace) == first
        assert len(calls) > count


def test_standalone_release_rejects_input_changed_after_it_was_checked(tmp_path, monkeypatch):
    workspace = fixture(tmp_path)
    brief = workspace.root / "planning/product-brief.md"
    original = publishing._v_is_file
    changed = False

    def mutate_at_delivery_boundary(path):
        nonlocal changed
        # The Brief must already have been read before injecting an external change.
        if path == workspace.root / "chapters/chapter-01.md" and not changed:
            brief.write_bytes(brief.read_bytes() + b"\nchanged after validation\n")
            changed = True
        return original(path)

    monkeypatch.setattr(publishing, "_v_is_file", mutate_at_delivery_boundary)
    with pytest.raises(SnapshotChangedError):
        publishing.release_readiness_findings(workspace)
    assert changed


def test_controlled_new_transaction_is_seen_by_next_public_release(tmp_path):
    workspace = fixture(tmp_path)
    journal = workspace.root / "runs/orchestration/transactions/tx-batch.json"
    with verification_scope(workspace), transactions.transaction_read_view(workspace):
        first = publishing.release_readiness_findings(workspace)
        assert not any(f.code == "RELEASE_CONTRACT_STALE" for f in first)
        write_text(journal, json.dumps(record(workspace)), encoding="utf8")
        second = publishing.release_readiness_findings(workspace)
        assert any(f.code == "RELEASE_CONTRACT_STALE" for f in second)


def test_external_same_mtime_transaction_change_is_not_hidden_by_parent_view(tmp_path):
    workspace = fixture(tmp_path)
    journal = workspace.root / "runs/orchestration/transactions/tx-batch.json"
    journal.write_text(json.dumps(record(workspace, "completed")), encoding="utf8")
    before = journal.stat()
    with pytest.raises(SnapshotChangedError), verification_scope(workspace), transactions.transaction_read_view(workspace):
        publishing.release_readiness_findings(workspace)
        journal.write_text(json.dumps(record(workspace)), encoding="utf8")
        os.utime(journal, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert any(f.code == "RELEASE_CONTRACT_STALE" for f in publishing.release_readiness_findings(workspace))


def test_another_workspace_cannot_borrow_release_admission(tmp_path):
    first = fixture(tmp_path / "a")
    second = fixture(tmp_path / "b")
    journal = second.root / "runs/orchestration/transactions/tx-batch.json"
    journal.write_text(json.dumps(record(second)), encoding="utf8")
    with verification_scope(first), transactions.transaction_read_view(first):
        assert not any(f.code == "RELEASE_CONTRACT_STALE" for f in publishing.release_readiness_findings(first))
        assert any(f.code == "RELEASE_CONTRACT_STALE" for f in publishing.release_readiness_findings(second))


def test_legacy_missing_brief_still_has_no_release_requirements(tmp_path):
    from knowledge_product_workflow.workspace import Workspace

    workspace = Workspace.create(tmp_path, "legacy")
    assert publishing.release_readiness_findings(workspace) == []
