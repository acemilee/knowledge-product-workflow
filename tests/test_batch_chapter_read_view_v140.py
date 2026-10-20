"""Internal batches share snapshots; public calls still admit afresh."""
import copy
import json
import os

import pytest

from knowledge_product_workflow import chapter_compliance as chapters
from knowledge_product_workflow import workspace_transactions as transactions
from knowledge_product_workflow.verification_context import (
    SnapshotChangedError,
    verification_scope,
    write_text,
)
from tests.test_structure_review_v133 import _fixture as _raw_fixture


def _fixture(path):
    from knowledge_product_workflow.workflows.authoring import create_chapter_draft

    workspace, source, body = _raw_fixture(path)
    create_chapter_draft(workspace, 1)
    return workspace, source, body


def invoke(name, workspace, source, *, public=False):
    function = getattr(chapters, name if public else name + "_in_view")
    return (function(workspace, 1, source) if name == "check_chapter" else
            function(workspace, workspace.root / "chapters/chapter-01.md"))


def baseline(name, workspace, source):
    result = invoke(name, workspace, source)
    if name == "check_chapter":
        assert result["checks"]["contract"]["state"] == "current"
    else:
        assert [item.code for item in result] == ["CHAPTER_COMPLIANCE_MISSING"]
    return result


def count_scans(monkeypatch):
    calls = []
    original = transactions.pending_workspace_transactions

    def pending(workspace):
        calls.append(workspace.root)
        return original(workspace)

    monkeypatch.setattr(transactions, "pending_workspace_transactions", pending)
    return calls


def record(workspace, state="prepared"):
    return {"operation_id": "tx-batch", "book_id": workspace.root.name,
            "kind": "chapter_submission", "scope_chapters": [1],
            "outputs": {"chapters/chapter-01-contract.md": {}}, "state": state}


def blocked(name, workspace, source, *, public=False):
    if name == "check_chapter":
        with pytest.raises(ValueError, match="事务待恢复"):
            invoke(name, workspace, source, public=public)
    else:
        findings = invoke(name, workspace, source, public=public)
        assert findings and any(item.severity.value == "blocker" for item in findings)
        assert any(item.code == "CHAPTER_COMPLIANCE_STALE" for item in findings)


@pytest.mark.parametrize("name", ["check_chapter", "validate_current_chapter_compliance"])
def test_internal_batch_reuses_only_current_view_and_public_call_is_fresh(tmp_path, monkeypatch, name):
    workspace, source, _ = _fixture(tmp_path)
    calls = count_scans(monkeypatch)
    with verification_scope(workspace), transactions.transaction_read_view(workspace):
        first = baseline(name, workspace, source)
        assert invoke(name, workspace, source) == first
        assert invoke(name, workspace, source) == first
        internal_scans = len(calls)
        assert internal_scans > 0
        assert invoke(name, workspace, source, public=True) == first
        invoke(name, workspace, source, public=True)
        invoke(name, workspace, source, public=True)
        public_scans = len(calls) - internal_scans
        assert internal_scans < public_scans
        if name == "validate_current_chapter_compliance":
            assert internal_scans == 1


@pytest.mark.parametrize("name", ["check_chapter", "validate_current_chapter_compliance"])
def test_internal_entry_without_view_falls_back_to_public_admission(tmp_path, monkeypatch, name):
    workspace, source, _ = _fixture(tmp_path)
    calls = count_scans(monkeypatch)
    with verification_scope(workspace):
        baseline(name, workspace, source)
        first_scans = len(calls)
        invoke(name, workspace, source)
        assert len(calls) > first_scans > 0


@pytest.mark.parametrize("name", ["check_chapter", "validate_current_chapter_compliance"])
def test_controlled_new_pending_transaction_refreshes_internal_batch(tmp_path, name):
    workspace, source, _ = _fixture(tmp_path)
    journal = workspace.root / "runs/orchestration/transactions/tx-batch.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    with verification_scope(workspace), transactions.transaction_read_view(workspace):
        baseline(name, workspace, source)
        write_text(journal, json.dumps(record(workspace)), encoding="utf-8")
        blocked(name, workspace, source)


@pytest.mark.parametrize("name", ["check_chapter", "validate_current_chapter_compliance"])
def test_public_admission_sees_external_same_mtime_change_inside_batch(tmp_path, name):
    workspace, source, _ = _fixture(tmp_path)
    journal = workspace.root / "runs/orchestration/transactions/tx-batch.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps(record(workspace, "completed")), encoding="utf-8")
    before = journal.stat()
    with pytest.raises(SnapshotChangedError), verification_scope(workspace), transactions.transaction_read_view(workspace):
        baseline(name, workspace, source)
        journal.write_text(json.dumps(record(workspace)), encoding="utf-8")
        os.utime(journal, ns=(before.st_atime_ns, before.st_mtime_ns))
        blocked(name, workspace, source, public=True)


@pytest.mark.parametrize("name", ["check_chapter", "validate_current_chapter_compliance"])
def test_external_new_unknown_member_rejects_before_any_write(tmp_path, name):
    workspace, source, _ = _fixture(tmp_path)
    folder = workspace.root / "runs/orchestration/transactions"
    folder.mkdir(parents=True, exist_ok=True)
    output = workspace.root / "runs/unsafe.txt"
    with pytest.raises(SnapshotChangedError), verification_scope(workspace), transactions.transaction_read_view(workspace):
        baseline(name, workspace, source)
        (folder / "tx-unknown.json").mkdir()
        with pytest.raises(SnapshotChangedError):
            write_text(output, "must not publish", encoding="utf-8")
        assert not output.exists()


@pytest.mark.parametrize("name", ["check_chapter", "validate_current_chapter_compliance"])
def test_internal_results_cannot_poison_a_later_read(tmp_path, name):
    workspace, source, _ = _fixture(tmp_path)
    with verification_scope(workspace), transactions.transaction_read_view(workspace):
        first = baseline(name, workspace, source)
        expected = copy.deepcopy(first)
        if name == "check_chapter":
            first["checks"]["contract"]["state"] = "forged"
        else:
            assert first
            first[0].message = "forged"
        assert invoke(name, workspace, source) == expected


@pytest.mark.parametrize("name", ["check_chapter", "validate_current_chapter_compliance"])
def test_aliased_journal_never_caches_pending_admission(tmp_path, name):
    workspace, source, _ = _fixture(tmp_path)
    outside = tmp_path / "aliased-record.json"
    outside.write_text(json.dumps(record(workspace, "completed")), encoding="utf-8")
    journal = workspace.root / "runs/orchestration/transactions/tx-batch.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.symlink_to(outside)
    with verification_scope(workspace), transactions.transaction_read_view(workspace):
        baseline(name, workspace, source)
        outside.write_text(json.dumps(record(workspace)), encoding="utf-8")
        blocked(name, workspace, source)


@pytest.mark.parametrize("name", ["check_chapter", "validate_current_chapter_compliance"])
def test_another_workspace_cannot_use_current_batch_view(tmp_path, name):
    a, source_a, _ = _fixture(tmp_path / "a")
    b, source_b, _ = _fixture(tmp_path / "b")
    journal = b.root / "runs/orchestration/transactions/tx-batch.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps(record(b)), encoding="utf-8")
    with verification_scope(a), transactions.transaction_read_view(a):
        baseline(name, a, source_a)
        blocked(name, b, source_b)
        invoke(name, a, source_a)


def test_view_eligibility_requires_same_lexical_root_and_live_scope(tmp_path):
    from knowledge_product_workflow.workspace import Workspace
    from tests.test_verification_context_v140 import _directory_link

    workspace, _, _ = _fixture(tmp_path)
    alias = tmp_path / "alias-root"
    _directory_link(alias, workspace.root)
    alias_workspace = Workspace(root=alias)
    assert not transactions.has_transaction_read_view(workspace)
    with verification_scope(workspace), transactions.transaction_read_view(workspace):
        assert transactions.has_transaction_read_view(workspace)
        assert not transactions.has_transaction_read_view(alias_workspace)
    assert not transactions.has_transaction_read_view(workspace)


def test_controlled_source_change_is_visible_to_next_internal_preflight(tmp_path):
    workspace, source, _ = _fixture(tmp_path)
    with verification_scope(workspace), transactions.transaction_read_view(workspace):
        first = chapters.check_chapter_in_view(workspace, 1, source)
        write_text(source, source.read_text(encoding="utf-8") + "\n补充当前输入条件。\n", encoding="utf-8")
        second = chapters.check_chapter_in_view(workspace, 1, source)
        assert second["source_sha256"] != first["source_sha256"]
