import os
from pathlib import Path

import pytest

from knowledge_product_workflow.workspace_scratch import create_workspace_scratch, workspace_scratch


def test_nested_write_and_cleanup(tmp_path):
    with workspace_scratch(tmp_path) as name:
        root = Path(name)
        assert root.parent == tmp_path.resolve()
        (root / "nested").mkdir()
        (root / "nested/probe.txt").write_text("actual writable")
        assert (root / "nested/probe.txt").read_text() == "actual writable"
    assert not root.exists()


def test_cleanup_on_failure(tmp_path):
    with pytest.raises(RuntimeError, match="controlled failure"), workspace_scratch(tmp_path) as name:
        (Path(name) / "data").write_text("temporary")
        raise RuntimeError("controlled failure")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("prefix", ["../escape", "..\\escape", "C:escape", "", "..", "."])
def test_prefix_cannot_escape(tmp_path, prefix):
    with pytest.raises(ValueError):
        create_workspace_scratch(tmp_path, prefix=prefix)
    assert not list(tmp_path.iterdir())


def test_native_permission_mode(tmp_path, monkeypatch):
    modes = []
    original = Path.mkdir
    def observed(path, mode=0o777, **kwargs):
        modes.append(mode)
        return original(path, mode=mode, **kwargs)
    monkeypatch.setattr(Path, "mkdir", observed)
    with workspace_scratch(tmp_path) as name:
        pass
    if os.name == "nt":
        assert modes == [0o777]
        assert len(Path(name).name) <= len(".kp-scratch-") + 16
    else:
        # tempfile uses os.mkdir, not Path.mkdir, and retains its private mode.
        assert modes == []


def test_existing_directory_never_overwritten(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows UUID collision branch")
    from types import SimpleNamespace
    monkeypatch.setattr("knowledge_product_workflow.workspace_scratch.uuid.uuid4", lambda: SimpleNamespace(hex="fixed"))
    existing = tmp_path / ".kp-scratch-fixed"
    existing.mkdir()
    (existing / "keep").write_text("kept")
    with pytest.raises(FileExistsError):
        create_workspace_scratch(tmp_path)
    assert (existing / "keep").read_text() == "kept"
