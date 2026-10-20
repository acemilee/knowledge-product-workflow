"""Internal comparison reads may borrow only an active covering production guard."""

import json
import os

import pytest

from knowledge_product_workflow import developmental as editing
from knowledge_product_workflow import production_intake as intake
from knowledge_product_workflow import workspace_transactions as transactions
from knowledge_product_workflow.verification_context import (
    SnapshotChangedError,
    verification_scope,
    write_text,
)
from tests.test_batch_chapter_read_view_v140 import record
from tests.test_structure_review_v133 import _fixture


def count_tokens(monkeypatch):
    calls = []
    original = intake.production_read_token

    def counted(workspace, **kwargs):
        calls.append(kwargs.get("chapters"))
        return original(workspace, **kwargs)

    monkeypatch.setattr(intake, "production_read_token", counted)
    return calls


def read_index(workspace, body, *, public=False):
    selected = (
        editing.book_reading_index
        if public
        else getattr(editing, "book_reading_index_in_view", editing.book_reading_index)
    )
    return selected(workspace, 1, body, chapter_numbers={1})


def test_covering_guard_avoids_repeated_token_snapshots_for_internal_reads(tmp_path, monkeypatch):
    workspace, _, body = _fixture(tmp_path)
    calls = count_tokens(monkeypatch)
    with (
        verification_scope(workspace),
        transactions.transaction_read_view(workspace),
        intake.consistent_production_read(workspace),
    ):
        first = read_index(workspace, body)
        assert first and read_index(workspace, body) == first
        assert read_index(workspace, body) == first
    assert len(calls) == 2


def test_public_index_still_takes_its_own_before_and_after_tokens(tmp_path, monkeypatch):
    workspace, _, body = _fixture(tmp_path)
    calls = count_tokens(monkeypatch)
    with (
        verification_scope(workspace),
        transactions.transaction_read_view(workspace),
        intake.consistent_production_read(workspace),
    ):
        read_index(workspace, body, public=True)
        read_index(workspace, body, public=True)
    assert len(calls) == 6


@pytest.mark.parametrize("guarded", [False, True])
def test_missing_transaction_view_never_borrows_guard(tmp_path, monkeypatch, guarded):
    workspace, _, body = _fixture(tmp_path)
    calls = count_tokens(monkeypatch)
    from contextlib import nullcontext

    with (
        verification_scope(workspace),
        intake.consistent_production_read(workspace) if guarded else nullcontext(),
    ):
        read_index(workspace, body)
    assert len(calls) == (4 if guarded else 2)


def test_narrow_guard_cannot_cover_an_unprotected_chapter(tmp_path, monkeypatch):
    workspace, _, body = _fixture(tmp_path)
    calls = count_tokens(monkeypatch)
    with (
        verification_scope(workspace),
        transactions.transaction_read_view(workspace),
        intake.consistent_production_read(workspace, chapters=set()),
    ):
        read_index(workspace, body)
    assert len(calls) == 4


def test_phase_change_falls_back_to_fresh_public_snapshot(tmp_path, monkeypatch):
    workspace, _, body = _fixture(tmp_path)
    calls = count_tokens(monkeypatch)
    with (
        verification_scope(workspace),
        transactions.transaction_read_view(workspace),
        intake.consistent_production_read(workspace),
    ):
        write_text(workspace.root / "runs/unrelated-owned.txt", "owned update", encoding="utf8")
        read_index(workspace, body)
    assert len(calls) == 4


def test_guard_is_released_after_error(tmp_path, monkeypatch):
    workspace, _, body = _fixture(tmp_path)
    calls = count_tokens(monkeypatch)
    with verification_scope(workspace), transactions.transaction_read_view(workspace):
        with (
            pytest.raises(ValueError, match="owned exception"),
            intake.consistent_production_read(workspace),
        ):
            raise ValueError("owned exception")
        before = len(calls)
        read_index(workspace, body)
        assert len(calls) == before + 2


def test_external_same_mtime_transaction_change_is_rejected_before_result_escapes(tmp_path):
    workspace, _, body = _fixture(tmp_path)
    journal = workspace.root / "runs/orchestration/transactions/tx-batch.json"
    journal.write_text(json.dumps(record(workspace, "completed")), encoding="utf8")
    before = journal.stat()
    output = workspace.root / "runs/unsafe-result.txt"
    with (
        pytest.raises((SnapshotChangedError, intake.ProductionConsistencyError)),
        verification_scope(workspace),
        transactions.transaction_read_view(workspace),
    ):
        with intake.consistent_production_read(workspace):
            read_index(workspace, body)
            journal.write_text(json.dumps(record(workspace)), encoding="utf8")
            os.utime(journal, ns=(before.st_atime_ns, before.st_mtime_ns))
            read_index(workspace, body)
        write_text(output, "must not publish", encoding="utf8")
    assert not output.exists()


def test_copied_context_cannot_extend_a_finished_guard(tmp_path, monkeypatch):
    from contextvars import copy_context

    workspace, _, body = _fixture(tmp_path)
    calls = count_tokens(monkeypatch)
    with verification_scope(workspace), transactions.transaction_read_view(workspace):
        with intake.consistent_production_read(workspace):
            copied = copy_context()
        before = len(calls)
        copied.run(read_index, workspace, body)
        assert len(calls) == before + 2


def test_another_thread_cannot_borrow_the_parent_guard(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    workspace, _, body = _fixture(tmp_path)
    calls = count_tokens(monkeypatch)
    with (
        verification_scope(workspace),
        transactions.transaction_read_view(workspace),
        intake.consistent_production_read(workspace),
    ):
        copied = copy_context()
        with (
            ThreadPoolExecutor(max_workers=1) as pool,
            pytest.raises(intake.ProductionConsistencyError, match="CONTROL_LOCK_BUSY") as blocked,
        ):
            pool.submit(copied.run, read_index, workspace, body).result(timeout=20)
        assert '"state": "active"' in str(blocked.value)
    assert len(calls) == 2


def test_another_async_task_cannot_borrow_the_parent_guard(tmp_path, monkeypatch):
    import asyncio

    workspace, _, body = _fixture(tmp_path)
    calls = count_tokens(monkeypatch)

    async def child():
        return read_index(workspace, body)

    async def parent():
        with (
            verification_scope(workspace),
            transactions.transaction_read_view(workspace),
            intake.consistent_production_read(workspace),
        ):
            assert await asyncio.create_task(child())

    asyncio.run(parent())
    assert len(calls) == 4


def test_covering_finite_guard_supports_current_binding_policy(tmp_path, monkeypatch):
    from knowledge_product_workflow.change_impact import binding_policy_revision
    from knowledge_product_workflow.workspace import Workspace

    original = Workspace.create
    monkeypatch.setattr(
        Workspace,
        "create",
        staticmethod(
            lambda root, book_id, **kwargs: original(
                root, book_id, **{**kwargs, "binding_policy_revision": 2}
            )
        ),
    )
    workspace, _, body = _fixture(tmp_path)
    assert binding_policy_revision(workspace) == 2
    calls = count_tokens(monkeypatch)
    with (
        verification_scope(workspace),
        transactions.transaction_read_view(workspace),
        intake.consistent_production_read(workspace, chapters={1}),
    ):
        assert read_index(workspace, body)
        assert read_index(workspace, body)
    assert len(calls) == 2


def test_another_workspace_cannot_borrow_the_parent_guard(tmp_path, monkeypatch):
    first, _, _ = _fixture(tmp_path / "a")
    second, _, body = _fixture(tmp_path / "b")
    calls = count_tokens(monkeypatch)
    with (
        verification_scope(first),
        transactions.transaction_read_view(first),
        intake.consistent_production_read(first),
    ):
        assert read_index(second, body)
    assert len(calls) == 4


def test_new_unknown_journal_member_prevents_result_publication(tmp_path):
    workspace, _, body = _fixture(tmp_path)
    output = workspace.root / "runs/unsafe-result.txt"
    with (
        pytest.raises((SnapshotChangedError, intake.ProductionConsistencyError)),
        verification_scope(workspace),
        transactions.transaction_read_view(workspace),
    ):
        with intake.consistent_production_read(workspace):
            read_index(workspace, body)
            (workspace.root / "runs/orchestration/transactions/tx-new.json").write_text(
                "{}", encoding="utf8"
            )
            read_index(workspace, body)
        write_text(output, "must not publish", encoding="utf8")
    assert not output.exists()


def test_changed_parent_lock_cannot_be_borrowed_before_cleanup(tmp_path):
    workspace, _, body = _fixture(tmp_path)
    with (
        pytest.raises(ValueError, match="控制锁"),
        verification_scope(workspace),
        transactions.transaction_read_view(workspace),
        intake.consistent_production_read(workspace),
    ):
        lock = workspace.root / "runs/production/control.lock"
        assert lock.is_file()
        raw = lock.read_bytes()
        lock.write_bytes(b"X" + raw[1:])
        with pytest.raises(ValueError, match="控制锁"):
            read_index(workspace, body)


@pytest.mark.parametrize("change", ["bytes", "identity"])
def test_existing_outer_lock_change_blocks_followup_write(tmp_path, change):
    from knowledge_product_workflow.production_tasks import production_lock

    workspace, _, body = _fixture(tmp_path)
    output = workspace.root / "runs/unsafe-result.txt"
    with (
        pytest.raises(ValueError, match="控制锁"),
        production_lock(workspace),
        verification_scope(workspace),
        transactions.transaction_read_view(workspace),
    ):
        with intake.consistent_production_read(workspace):
            lock = workspace.root / "runs/production/control.lock"
            raw = lock.read_bytes()
            if change == "bytes":
                lock.write_bytes(b"X" + raw[1:])
            else:
                replacement = lock.with_name("owned-replacement.lock")
                replacement.write_bytes(raw)
                os.replace(replacement, lock)
            read_index(workspace, body)
        write_text(output, "must not publish", encoding="utf8")
    assert not output.exists()
