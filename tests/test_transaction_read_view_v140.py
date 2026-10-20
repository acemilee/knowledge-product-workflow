"""Batch internal verification without sharing a later public admission decision."""

from types import SimpleNamespace

import pytest


def test_status_batches_contract_admission_instead_of_scanning_per_helper(tmp_path, monkeypatch):
    from knowledge_product_workflow import workspace_transactions as transactions
    from knowledge_product_workflow.runtime import inspect_workflow
    from tests.performance_support_v140 import make_fixture

    workspace = make_fixture(tmp_path, 4)
    calls = []
    original = transactions.pending_workspace_transactions

    def pending(current):
        calls.append(current.root)
        return original(current)

    monkeypatch.setattr(transactions, "pending_workspace_transactions", pending)
    status = inspect_workflow(workspace)
    assert status.stage == "chapter_drafts_incomplete"
    assert len(calls) <= 20


def test_internal_view_batches_but_public_contract_remains_fresh(tmp_path, monkeypatch):
    from knowledge_product_workflow import workspace_transactions as transactions
    from knowledge_product_workflow.verification_context import verification_scope
    from knowledge_product_workflow.workflows.authoring import (
        require_current_chapter_contract,
        require_current_chapter_contract_in_view,
    )
    from tests.test_structure_review_v133 import _fixture

    workspace, _, _ = _fixture(tmp_path)
    original = transactions.pending_workspace_transactions
    calls = []

    def pending(current):
        calls.append(current.root)
        return original(current)

    monkeypatch.setattr(transactions, "pending_workspace_transactions", pending)
    with verification_scope(workspace), transactions.transaction_read_view(workspace):
        for _ in range(3):
            require_current_chapter_contract_in_view(workspace, 1)
        assert len(calls) == 1
        require_current_chapter_contract(workspace, 1)
        require_current_chapter_contract(workspace, 1)
        assert len(calls) == 3


def test_owned_phase_refreshes_view_and_cannot_read_a_pending_contract(tmp_path):
    import json

    from knowledge_product_workflow.verification_context import verification_scope, write_text
    from knowledge_product_workflow.workflows.authoring import (
        require_current_chapter_contract_in_view,
    )
    from knowledge_product_workflow.workspace_transactions import transaction_read_view
    from tests.test_structure_review_v133 import _fixture

    workspace, _, _ = _fixture(tmp_path)
    journal = workspace.root / "runs/orchestration/transactions/tx-next.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    with verification_scope(workspace), transaction_read_view(workspace):
        require_current_chapter_contract_in_view(workspace, 1)
        write_text(journal, json.dumps({"operation_id": "tx-next", "book_id": workspace.root.name,
                                       "kind": "chapter_submission", "scope_chapters": [1],
                                       "outputs": {"chapters/chapter-01-contract.md": {}},
                                       "state": "prepared"}), encoding="utf-8")
        with pytest.raises(ValueError, match="事务待恢复"):
            require_current_chapter_contract_in_view(workspace, 1)


def test_a_new_public_action_refreshes_even_inside_an_existing_context(tmp_path):
    import json

    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        verification_scope,
    )
    from knowledge_product_workflow.workspace_transactions import (
        require_transaction_consistency_in_view,
        transaction_read_view,
    )

    workspace = SimpleNamespace(root=tmp_path)
    folder = tmp_path / "runs/orchestration/transactions"
    folder.mkdir(parents=True)
    path = folder / "tx-local.json"
    record = {"operation_id": "tx-local", "book_id": "local", "kind": "chapter_submission",
              "scope_chapters": [1], "outputs": {}, "state": "completed"}
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        with transaction_read_view(workspace):
            require_transaction_consistency_in_view(workspace, chapters={1})
        record["state"] = "prepared"
        path.write_text(json.dumps(record), encoding="utf-8")
        with transaction_read_view(workspace), pytest.raises(ValueError, match="事务待恢复"):
            require_transaction_consistency_in_view(workspace, chapters={1})


def test_view_cannot_escape_action_or_verification_context(tmp_path):
    from knowledge_product_workflow.verification_context import verification_scope
    from knowledge_product_workflow.workspace_transactions import transaction_read_view

    workspace = SimpleNamespace(root=tmp_path)
    with verification_scope(workspace):
        with transaction_read_view(workspace) as outer:
            assert outer.current() == []
            with transaction_read_view(workspace) as inner:
                with pytest.raises(ValueError, match="当前动作"):
                    outer.current()
                assert inner.current() == []
            assert outer.current() == []
        with pytest.raises(ValueError, match="当前动作"):
            outer.current()
    with pytest.raises(ValueError, match="结束"):
        outer.current()


def test_unknown_journal_directory_is_never_silently_ignored(tmp_path):
    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        verification_scope,
        write_text,
    )
    from knowledge_product_workflow.workspace_transactions import (
        pending_workspace_transactions,
        require_transaction_consistency_in_view,
        transaction_read_view,
    )

    workspace = SimpleNamespace(root=tmp_path)
    folder = tmp_path / "runs/orchestration/transactions"
    folder.mkdir(parents=True)
    unknown = folder / "tx-unknown.json"
    with pytest.raises(SnapshotChangedError), verification_scope(workspace), transaction_read_view(workspace):
        require_transaction_consistency_in_view(workspace)
        unknown.mkdir()
        write_text(tmp_path / "unsafe.md", "unsafe", encoding="utf-8")
    assert not (tmp_path / "unsafe.md").exists()
    assert pending_workspace_transactions(workspace)[0]["state"] == "unknown"


def test_view_does_not_cross_workspaces_or_cache_aliased_journals(tmp_path):
    import json

    from knowledge_product_workflow.verification_context import verification_scope
    from knowledge_product_workflow.workspace_transactions import (
        require_transaction_consistency_in_view,
        transaction_read_view,
    )
    from tests.test_verification_context_v140 import _directory_link

    a, b, outside = tmp_path / "a", tmp_path / "b", tmp_path / "outside"
    a.mkdir()
    b.mkdir()
    outside.mkdir()
    parent = b / "runs/orchestration"
    parent.mkdir(parents=True)
    _directory_link(parent / "transactions", outside)
    record = {"operation_id": "tx-alias", "book_id": "b", "kind": "chapter_submission",
              "scope_chapters": [1], "outputs": {}, "state": "completed"}
    path = outside / "tx-alias.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with verification_scope(SimpleNamespace(root=a)), transaction_read_view(SimpleNamespace(root=a)):
        require_transaction_consistency_in_view(SimpleNamespace(root=a))
        with verification_scope(SimpleNamespace(root=b)), transaction_read_view(SimpleNamespace(root=b)):
            require_transaction_consistency_in_view(SimpleNamespace(root=b))
            record["state"] = "prepared"
            path.write_text(json.dumps(record), encoding="utf-8")
            with pytest.raises(ValueError, match="事务待恢复"):
                require_transaction_consistency_in_view(SimpleNamespace(root=b))
        with verification_scope(SimpleNamespace(root=b)), pytest.raises(ValueError, match="事务待恢复"):
            require_transaction_consistency_in_view(SimpleNamespace(root=b))


def test_view_rows_cannot_be_mutated_into_a_different_admission(tmp_path):
    import json

    from knowledge_product_workflow.verification_context import verification_scope
    from knowledge_product_workflow.workspace_transactions import (
        require_transaction_consistency_in_view,
        transaction_read_view,
    )

    workspace = SimpleNamespace(root=tmp_path)
    folder = tmp_path / "runs/orchestration/transactions"
    folder.mkdir(parents=True)
    (folder / "tx-pending.json").write_text(json.dumps({
        "operation_id": "tx-pending", "book_id": "local", "kind": "chapter_submission",
        "scope_chapters": [2], "outputs": {}, "state": "prepared",
    }), encoding="utf-8")
    with verification_scope(workspace), transaction_read_view(workspace) as view:
        rows = view.current()
        rows[0]["scope_chapters"] = [1]
        require_transaction_consistency_in_view(workspace, chapters={1})
        with pytest.raises(ValueError, match="事务待恢复"):
            require_transaction_consistency_in_view(workspace, chapters={2})
