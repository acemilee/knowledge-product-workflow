"""Private preparation never publishes stale inputs or borrows existing storage."""

import os
from pathlib import Path

import pytest

from knowledge_product_workflow import workspace_transactions as tx
from knowledge_product_workflow.verification_context import verification_scope
from knowledge_product_workflow.workspace import Workspace


def fixture(tmp_path):
    root = tmp_path / "book"
    (root / "chapters").mkdir(parents=True)
    (root / "runs/orchestration/transactions").mkdir(parents=True)
    outputs = {f"chapters/chapter-{n:02d}.md": f"before {n}".encode() for n in range(1, 6)}
    for name, data in outputs.items():
        (root / name).write_bytes(data)
    return Workspace(root), outputs


def test_private_stage_does_not_recheck_the_whole_read_set_per_blob(tmp_path, monkeypatch):
    workspace, outputs = fixture(tmp_path)
    fact = workspace.root / "fact.txt"
    fact.write_bytes(b"frozen")
    original = Path.read_bytes
    reads = []

    def read(path):
        if path == fact:
            reads.append(path)
        return original(path)

    with verification_scope(workspace) as context:
        context.read_bytes(fact)
        monkeypatch.setattr(Path, "read_bytes", read)
        tx.prepare_workspace_transaction(workspace, outputs, kind="chapter_submission",
                                         scope_chapters=list(range(1, 6)), operation_id="tx-private")
        assert len(reads) <= 3  # Publish metadata still verifies; blob copies do not.


@pytest.mark.parametrize("linked", [False, True])
def test_existing_or_linked_stage_is_refused_without_any_borrowed_write(tmp_path, monkeypatch, linked):
    workspace, outputs = fixture(tmp_path)
    stage = workspace.root / "runs/orchestration/transactions/tx-private"
    foreign = tmp_path / "unrelated"
    foreign.mkdir()
    if linked:
        from tests.test_verification_context_v140 import _directory_link
        _directory_link(stage, foreign)
    else:
        stage.mkdir()
        (stage / "existing.txt").write_bytes(b"preserve")
    original = Path.open
    attempted = []

    def opened(path, mode="r", *args, **kwargs):
        if any(token in mode for token in "wax+") and path.is_relative_to(stage):
            attempted.append(path)
            raise AssertionError("preexisting or aliased stage write must be refused first")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises((ValueError, FileExistsError)):
        tx.prepare_workspace_transaction(workspace, outputs, kind="chapter_submission",
                                         scope_chapters=list(range(1, 6)), operation_id="tx-private")
    assert not attempted
    assert not (stage.parent / "tx-private.json").exists()
    assert not list(foreign.iterdir())
    for name, value in outputs.items():
        assert (workspace.root / name).read_bytes() == value


def test_direct_output_change_during_staging_cannot_publish_a_journal(tmp_path, monkeypatch):
    workspace, outputs = fixture(tmp_path)
    target = workspace.root / "chapters/chapter-01.md"
    stamp = target.stat()

    def fault(phase):
        if phase == "private_staging_complete":
            target.write_bytes(b"external")
            os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    monkeypatch.setattr(tx, "_fault", fault)
    with pytest.raises(ValueError):
        tx.prepare_workspace_transaction(workspace, outputs, kind="chapter_submission",
                                         scope_chapters=list(range(1, 6)), operation_id="tx-private")
    assert target.read_bytes() == b"external"
    assert not (workspace.root / "runs/orchestration/transactions/tx-private.json").exists()
    for name, value in outputs.items():
        if name != "chapters/chapter-01.md":
            assert (workspace.root / name).read_bytes() == value


@pytest.mark.parametrize("change", ["new-journal", "new-member", "stage-bytes", "stage-link"])
def test_private_staging_external_changes_leave_no_recoverable_journal(tmp_path, monkeypatch, change):
    workspace, outputs = fixture(tmp_path)
    parent = workspace.root / "runs/orchestration/transactions"
    stage = parent / "tx-private"
    foreign = tmp_path / "unrelated"
    foreign.mkdir()

    def fault(phase):
        if phase != "private_staging_complete":
            return
        if change == "new-journal":
            (parent / "tx-unknown.json").write_bytes(b"{}")
        elif change == "new-member":
            (stage / "foreign.bin").write_bytes(b"unowned")
        elif change == "stage-bytes":
            path = stage / "0.after"
            stamp = path.stat()
            path.write_bytes(b"tampered")
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        else:
            from tests.test_verification_context_v140 import _directory_link
            archive = parent / "retained-private-stage"
            assert stage.resolve().is_relative_to(tmp_path.resolve())
            assert archive.resolve().is_relative_to(tmp_path.resolve())
            stage.rename(archive)
            _directory_link(stage, foreign)

    monkeypatch.setattr(tx, "_fault", fault)
    with pytest.raises(ValueError):
        tx.prepare_workspace_transaction(workspace, outputs, kind="chapter_submission",
                                         scope_chapters=list(range(1, 6)), operation_id="tx-private")
    assert not (parent / "tx-private.json").exists()
    assert not list(foreign.iterdir())
    for name, value in outputs.items():
        assert (workspace.root / name).read_bytes() == value


def test_interrupted_private_stage_is_retained_and_never_reused(tmp_path, monkeypatch):
    workspace, outputs = fixture(tmp_path)
    stage = workspace.root / "runs/orchestration/transactions/tx-private"

    def fault(phase):
        if phase == "private_staging_complete":
            raise RuntimeError("simulated private preparation interruption")

    monkeypatch.setattr(tx, "_fault", fault)
    with pytest.raises(RuntimeError, match="interruption"):
        tx.prepare_workspace_transaction(workspace, outputs, kind="chapter_submission",
                                         scope_chapters=list(range(1, 6)), operation_id="tx-private")
    before = {p.name: p.read_bytes() for p in stage.iterdir()}
    assert before and not stage.with_suffix(".json").exists()
    with pytest.raises(FileExistsError):
        tx.prepare_workspace_transaction(workspace, outputs, kind="chapter_submission",
                                         scope_chapters=list(range(1, 6)), operation_id="tx-private")
    assert {p.name: p.read_bytes() for p in stage.iterdir()} == before
    for name, value in outputs.items():
        assert (workspace.root / name).read_bytes() == value


def test_valid_preparation_preserves_before_hashes_and_original_recovery(tmp_path):
    from hashlib import sha256

    workspace, before = fixture(tmp_path)
    outputs = {name: b"after " + value for name, value in before.items()}
    record = tx.prepare_workspace_transaction(workspace, outputs, kind="chapter_submission",
                                              scope_chapters=list(range(1, 6)), operation_id="tx-private")
    for name, value in before.items():
        assert record.outputs[name]["before_sha256"] == sha256(value).hexdigest()
        assert (workspace.root / record.outputs[name]["before_path"]).read_bytes() == value
    tx.commit_workspace_transaction(workspace, record)
    for name, value in outputs.items():
        assert (workspace.root / name).read_bytes() == value
    again = tx.prepare_workspace_transaction(workspace, outputs, kind="chapter_submission",
                                             scope_chapters=list(range(1, 6)), operation_id="tx-private")
    assert again.state == "completed"


def test_brief_book_identity_change_is_tracked_before_journal_publication(tmp_path, monkeypatch):
    from knowledge_product_workflow.frontmatter import render_markdown_artifact
    from knowledge_product_workflow.models import ArtifactMetadata, ArtifactStatus

    workspace, outputs = fixture(tmp_path)
    brief = workspace.root / "planning/product-brief.md"
    brief.parent.mkdir()
    brief.write_text(render_markdown_artifact(ArtifactMetadata(
        book_id="original-book", artifact_type="product_brief", status=ArtifactStatus.APPROVED,
        version=1,
    ), "# 合成Brief"), encoding="utf-8")
    stamp = brief.stat()

    def fault(phase):
        if phase == "private_staging_complete":
            brief.write_text(brief.read_text(encoding="utf-8").replace("original-book", "external-book"),
                             encoding="utf-8")
            os.utime(brief, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    monkeypatch.setattr(tx, "_fault", fault)
    with pytest.raises(ValueError):
        tx.prepare_workspace_transaction(workspace, outputs, kind="chapter_submission",
                                         scope_chapters=list(range(1, 6)), operation_id="tx-private")
    assert not (workspace.root / "runs/orchestration/transactions/tx-private.json").exists()
    for name, value in outputs.items():
        assert (workspace.root / name).read_bytes() == value
