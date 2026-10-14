"""Temporary tool files inside a caller-owned workspace, including restricted Windows tokens."""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


def create_workspace_scratch(root: Path, *, prefix: str = ".kp-scratch-") -> Path:
    """Inherit Windows workspace capabilities; retain private POSIX permissions."""
    if not prefix or prefix in (".", "..") or any(c in prefix for c in "/\\:"):
        raise ValueError("scratch prefix must be one relative filename component")
    parent = Path(root).resolve(strict=True)
    if not parent.is_dir():
        raise NotADirectoryError(parent)
    if os.name != "nt":
        return Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    # CPython 3.13's mode 0700 replaces the inherited capability DACL. Do not
    # change the parent ACL or use the global TEMP directory to work around it.
    for _ in range(3):
        # 64 random bits plus exclusive creation; keep paths short for native
        # browser tools which still have stricter path limits than Python.
        directory = parent / (prefix + uuid.uuid4().hex[:16])
        try:
            directory.mkdir(mode=0o777)
            return directory
        except FileExistsError:
            continue
    raise FileExistsError("workspace scratch collision; nothing overwritten")


@contextmanager
def workspace_scratch(root: Path, *, prefix: str = ".kp-scratch-"):
    directory = create_workspace_scratch(root, prefix=prefix)
    parent = directory.parent
    try:
        yield str(directory)
    finally:
        if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()):
            raise OSError("scratch directory replaced by a link; refuse cleanup")
        if directory.parent != parent:
            raise OSError("scratch directory escaped its owner")
        shutil.rmtree(directory)
