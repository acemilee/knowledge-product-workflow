"""私有临时JSON不发布权限；最终replace仍核全部输入。"""
import pytest

from knowledge_product_workflow.verification_context import (
    SnapshotChangedError,
    atomic_metadata_text,
    read_bytes,
    verification_scope,
)
from knowledge_product_workflow.workspace import Workspace


def test_changed_input_after_staging_refuses_publication(tmp_path, monkeypatch):
    from knowledge_product_workflow import atomic_files
    w = Workspace.create(tmp_path, "staged")
    source = w.root/"planning/input.txt"
    source.write_bytes(b"before")
    target = w.root/"runs/orchestration/result.json"
    target.write_bytes(b'{"old":true}')
    original = atomic_files.replace_file
    def changed(stage, dest):
        stamp = source.stat()
        source.write_bytes(b"after!")
        import os
        os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        original(stage, dest)
    monkeypatch.setattr(atomic_files, "replace_file", changed)
    with pytest.raises(SnapshotChangedError), verification_scope(w):
        assert read_bytes(source) == b"before"
        atomic_metadata_text(target, '{"new":true}')
    assert target.read_bytes() == b'{"old":true}'
    assert list(target.parent.glob(".result.json.*.tmp"))  # Non-authoritative residue retained.


def test_metadata_publication_has_one_full_gate_and_rejects_business_paths(tmp_path, monkeypatch):
    from knowledge_product_workflow.verification_context import VerificationContext
    w = Workspace.create(tmp_path, "one-gate")
    calls = []
    original = VerificationContext.verify_read_set
    def counted(self):
        calls.append(1)
        return original(self)
    monkeypatch.setattr(VerificationContext, "verify_read_set", counted)
    with verification_scope(w):
        atomic_metadata_text(w.root/"runs/orchestration/result.json", '{"new":true}')
        assert len(calls) == 1  # Exit adds its own mandatory final verification.
        with pytest.raises(ValueError, match="正文"):
            atomic_metadata_text(w.root/"chapters/body.json", '{}')
    assert len(calls) == 2


def test_canonical_snapshot_path_retargeted_after_read_never_reaches_owned_write(tmp_path):
    from knowledge_product_workflow.verification_context import write_text
    from tests.test_verification_context_v140 import _directory_link
    w = Workspace.create(tmp_path, "path-guard")
    folder = w.root/"assets/first"
    folder.mkdir()
    (folder/"value.txt").write_bytes(b"same")
    second = w.root/"assets/second"
    second.mkdir()
    (second/"value.txt").write_bytes(b"same")
    with pytest.raises(SnapshotChangedError), verification_scope(w):
        assert read_bytes(folder/"value.txt") == b"same"
        # Single owned test directory move; both resolved paths are in this fixture.
        archived = w.root/"assets/original"
        assert folder.resolve().is_relative_to(w.root.resolve()) and archived.absolute().is_relative_to(w.root.resolve())
        folder.rename(archived)
        _directory_link(folder, second)
        write_text(w.root/"delivery/result.txt", "不得发布", encoding="utf-8")
    assert not (w.root/"delivery/result.txt").exists()


def test_parent_retarget_during_byte_verification_is_detected_at_end(tmp_path, monkeypatch):
    from pathlib import Path

    from tests.test_verification_context_v140 import _directory_link
    w = Workspace.create(tmp_path, "during-verification")
    first, second = w.root/"assets/first", w.root/"assets/second"
    first.mkdir()
    second.mkdir()
    for folder in (first, second):
        (folder/"a.txt").write_bytes(b"same")
        (folder/"b.txt").write_bytes(b"same")
    original = Path.read_bytes
    armed = [False]
    def changed(path):
        data = original(path)
        if armed[0] and path == first/"b.txt":
            armed[0] = False
            archived = w.root/"assets/original"
            assert first.resolve().is_relative_to(w.root.resolve()) and archived.absolute().is_relative_to(w.root.resolve())
            first.rename(archived)
            _directory_link(first, second)
        return data
    with pytest.raises(SnapshotChangedError), verification_scope(w) as context:
        read_bytes(first/"a.txt")
        read_bytes(first/"b.txt")
        monkeypatch.setattr(Path, "read_bytes", changed)
        armed[0] = True
        context.verify_read_set()
