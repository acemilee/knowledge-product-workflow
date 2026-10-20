"""Invocation-local immutable inputs; never a persistent authority or global cache."""

from __future__ import annotations

import copy
import hashlib
import io
import os
import stat
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

_active: ContextVar[VerificationContext | None] = ContextVar("kp_verification", default=None)


class SnapshotChangedError(ValueError):
    """A read input changed before a controlled result could be used."""


class VerificationContext:
    def __init__(self, workspace, *, view_id: str = "canonical"):
        self._root_input = Path(
            workspace if isinstance(workspace, (str, Path)) else workspace.root
        ).absolute()
        self.root = self._root_input.resolve()
        # Every public/nested entry contributes its original spelling. Reusing
        # one physical read view must not discard another junction's binding.
        self._root_inputs = {self._root_input}
        try:
            root_info = self.root.stat()
        except FileNotFoundError:
            self._root_identity = None
        else:
            self._root_identity = (root_info.st_dev, root_info.st_ino)
        self.view_id = view_id
        self._bytes: dict[Path, bytes] = {}
        self._read_set: dict[Path, bytes | None] = {}
        self._existence: dict[Path, bool] = {}
        self._path_existence: dict[Path, bool] = {}
        self._parsed: dict[tuple, object] = {}
        self._memos: dict[object, tuple[object, set[Path], dict[Path, bytes | None], set[tuple[Path, str]]]] = {}
        self._latest_live: dict[Path, bytes | None] = {}
        self._captures: list[set[Path]] = []
        self._directory_captures: list[set[tuple[Path, str]]] = []
        self._directories: dict[tuple[Path, str], frozenset[Path]] = {}
        self._input_aliases: dict[Path, Path] = {}
        self._untracked_reads = 0
        self.closed = False
        self.phase = 0
        self.parent: VerificationContext | None = None

    def owns(self, path: Path) -> bool:
        return path.resolve().is_relative_to(self.root)

    def _ensure_open(self) -> None:
        if self.closed:
            raise ValueError("验证上下文已结束，不能跨命令复用")

    def _verify_root(self):
        try:
            info = self.root.stat()
            identity = (info.st_dev, info.st_ino)
        except FileNotFoundError:
            identity = None
        except OSError:
            raise SnapshotChangedError("验证期间工作区根路径归属发生变化；核对原操作") from None
        if identity != self._root_identity or any(
            original.resolve() != self.root for original in self._root_inputs
        ):
            raise SnapshotChangedError("验证期间工作区根路径归属发生变化；核对原操作")

    def _resolve_input(self, path: Path) -> Path:
        resolved = path.resolve()
        if path.absolute() != resolved:
            # Do not memoize alias reads, even inside root. Retain the first
            # target as well: unchanged old bytes do not prove the alias still
            # names that target at a write/exit boundary.
            self._untracked_reads += 1
            self._input_aliases.setdefault(path.absolute(), resolved)
        return resolved

    @staticmethod
    def volatile(path: Path) -> bool:
        # Admission, leases, native liveness and append-only history stay fresh.
        fact_ledgers = {
            "source-ledger.jsonl",
            "assumption-ledger.jsonl",
            "claim-classifications.jsonl",
        }
        return (
            (path.suffix == ".jsonl" and path.name not in fact_ledgers)
            or "orchestration" in path.parts
            or "production" in path.parts
            or "leases" in path.parts
        )

    @staticmethod
    def live_metadata(path: Path) -> bool:
        """Read admission metadata afresh; its changing clock/state is not a frozen proof."""
        return (
            path.name == "events.jsonl"
            or "work-orders" in path.parts
            or "leases" in path.parts
            or "locks" in path.parts
            or ("orchestration" in path.parts and path.name in {"state.json", "run-intent.json"})
            or (
                "production" in path.parts
                and path.name in {"run.json", "native-state.json", "ledger.json"}
            )
        )

    def read_bytes(self, path: Path) -> bytes:
        self._ensure_open()
        path = self._resolve_input(path)
        if not path.is_relative_to(self.root):
            self._untracked_reads += 1
            return path.read_bytes()
        for capture in self._captures:
            capture.add(path)
        if not self.volatile(path) and path in self._bytes:
            return self._bytes[path]
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            if not self.live_metadata(path):
                self._read_set.setdefault(path, None)
            raise
        if not self.live_metadata(path):
            self._read_set.setdefault(path, data)
        else:
            self._latest_live[path] = data
        if not self.volatile(path):
            self._bytes[path] = data
        return data

    def is_file(self, path: Path) -> bool:
        self._ensure_open()
        path = self._resolve_input(path)
        present = path.is_file()
        if path.is_relative_to(self.root):
            for capture in self._captures:
                capture.add(path)
            if not self.live_metadata(path):
                self._existence.setdefault(path, present)
            elif not present:
                self._latest_live[path] = None
        else:
            self._untracked_reads += 1
        return present

    def exists(self, path: Path) -> bool:
        self._ensure_open()
        path = self._resolve_input(path)
        present = path.exists()
        if path.is_relative_to(self.root):
            for capture in self._captures:
                capture.add(path)
            if not self.live_metadata(path):
                self._path_existence.setdefault(path, present)
            elif not present:
                self._latest_live[path] = None
        else:
            self._untracked_reads += 1
        return present

    def parsed(self, path: Path, parser_key: str, loader):
        data = self.read_bytes(path)
        return self.parsed_payload(parser_key, data, loader)

    def parsed_payload(self, parser_key: str, data: bytes, loader):
        self._ensure_open()
        key = (parser_key, hashlib.sha256(data).digest())
        if key not in self._parsed:
            self._parsed[key] = copy.deepcopy(loader(data))
        return copy.deepcopy(self._parsed[key])

    def memo(self, key, compute):
        """Only deterministic, fully declared reads; never use for admission checks."""
        self._ensure_open()
        if key in self._memos:
            value, dependencies, live, directories = self._memos[key]
            if all(
                (p.read_bytes() if p.is_file() else None) == expected
                for p, expected in live.items()
            ):
                for capture in self._captures:
                    capture.update(dependencies)
                for capture in self._directory_captures:
                    capture.update(directories)
                return copy.deepcopy(value)
            self._memos.pop(key)
        dependencies: set[Path] = set()
        directories: set[tuple[Path, str]] = set()
        untracked_before = self._untracked_reads
        self._captures.append(dependencies)
        self._directory_captures.append(directories)
        try:
            value = compute()
        finally:
            self._captures.pop()
            self._directory_captures.pop()
        live = {p: self._latest_live.get(p) for p in dependencies if self.live_metadata(p)}
        if self._untracked_reads == untracked_before:
            self._memos[key] = (copy.deepcopy(value), dependencies, live, directories)
        return copy.deepcopy(value)

    def next_phase(self, changed_paths=()) -> None:
        self._ensure_open()
        self._verify_root()
        changed = {Path(p).resolve() for p in changed_paths}
        changed_directories = set()
        for path in changed:
            self._bytes.pop(path, None)
            self._read_set.pop(path, None)
            self._existence.pop(path, None)
            self._path_existence.pop(path, None)
            for (directory, pattern), names in tuple(self._directories.items()):
                if path == directory or directory.is_relative_to(path):
                    changed_directories.add((directory, pattern))
                    self._directories[(directory, pattern)] = frozenset(
                        p.resolve() for p in directory.glob(pattern)
                    )
                elif path.is_relative_to(directory) and path.relative_to(directory).full_match(
                    pattern
                ):
                    changed_directories.add((directory, pattern))
                    updated = set(names)
                    if path.exists():
                        updated.add(path)
                    else:
                        updated.discard(path)
                    self._directories[(directory, pattern)] = frozenset(updated)
        self._memos = {k: v for k, v in self._memos.items()
                       if not (v[1] & changed or v[3] & changed_directories)}
        self.phase += 1

    def watch_directory(self, directory: Path, pattern: str, *, include_non_files=False):
        """Track file membership; known controlled writes update only their own path."""
        self._ensure_open()
        directory = self._resolve_input(directory)
        if not directory.is_relative_to(self.root):
            self._untracked_reads += 1
            return [p for p in directory.glob(pattern) if include_non_files or p.is_file()]
        names = frozenset(self._resolve_input(p) for p in directory.glob(pattern))
        key = (directory, pattern)
        for capture in self._directory_captures:
            capture.add(key)
        if key in self._directories and self._directories[key] != names:
            raise SnapshotChangedError("验证期间目录输入发生变化；核对原操作")
        self._directories[key] = names
        return sorted(p for p in names if include_non_files or self.is_file(p))

    def verify_read_set(self) -> None:
        self._ensure_open()
        self._verify_root()
        for original, expected in self._input_aliases.items():
            try:
                current = original.resolve()
            except (OSError, RuntimeError):
                raise SnapshotChangedError("验证期间输入路径别名归属发生变化；核对原操作") from None
            if current != expected:
                raise SnapshotChangedError("验证期间输入路径别名归属发生变化；核对原操作")
        for path, expected in tuple(self._existence.items()):
            if path.is_file() != expected:
                raise SnapshotChangedError(
                    f"验证期间输入存在状态变化：{path.relative_to(self.root)}；核对原操作"
                )
        for path, expected in tuple(self._path_existence.items()):
            if path.exists() != expected:
                raise SnapshotChangedError(
                    f"验证期间输入存在状态变化：{path.relative_to(self.root)}；核对原操作"
                )
        for path, expected in tuple(self._read_set.items()):
            actual = path.read_bytes() if path.is_file() else None
            if actual != expected:
                raise SnapshotChangedError(
                    f"验证期间输入发生变化：{path.relative_to(self.root)}；保留原动作并核对"
                )
        for (directory, pattern), expected in tuple(self._directories.items()):
            actual = frozenset(p.resolve() for p in directory.glob(pattern))
            if actual != expected:
                raise SnapshotChangedError("验证期间目录输入发生变化；核对原操作")


@contextmanager
def verification_scope(workspace, *, context=None, view_id="canonical"):
    original = Path(workspace if isinstance(workspace, (str, Path)) else workspace.root)
    parent = _active.get()
    if parent is not None:
        parent._verify_root()
    root = original.resolve()
    if context is not None:
        context._ensure_open()
        if context is not parent:
            context._verify_root()
        if context.root != root or context.view_id != view_id:
            raise ValueError("验证上下文不属于当前工作区／视图")
    selected = context or (
        parent if parent is not None and parent.root == root and parent.view_id == view_id else None
    )
    owner = selected is None or selected is not parent
    selected = selected or VerificationContext(workspace, view_id=view_id)
    selected._root_inputs.add(original.absolute())
    if owner:
        selected.parent = parent
    token = _active.set(selected)
    try:
        yield selected
        if owner:
            selected.verify_read_set()
    finally:
        _active.reset(token)
        if owner:
            selected.closed = True
            selected._bytes.clear()
            selected._parsed.clear()
            selected._memos.clear()
            selected._directories.clear()
            selected._read_set.clear()
            selected._existence.clear()
            selected._path_existence.clear()
            selected._latest_live.clear()
            selected._input_aliases.clear()


def verified_read(function=None, *, transaction_view=False):
    """Compatible public entry point with an optional explicit verification keyword."""
    if function is None:
        return lambda selected: verified_read(selected, transaction_view=transaction_view)

    @wraps(function)
    def invoke(workspace, *args, verification=None, **kwargs):
        with verification_scope(workspace, context=verification):
            if transaction_view:
                from knowledge_product_workflow.workspace_transactions import transaction_read_view
                with transaction_read_view(workspace):
                    return function(workspace, *args, **kwargs)
            return function(workspace, *args, **kwargs)

    return invoke


def memoized_read(function):
    """Reuse deterministic validation only inside an existing read snapshot.

    Readers must declare every dependency through this module. Live admission
    checks stay outside these functions; writes and scope exit still recheck the
    full byte/existence/directory read set. Returned models are independent copies.
    """
    @wraps(function)
    def invoke(workspace, *args, **kwargs):
        context = current_verification(workspace)
        if context is None:
            return function(workspace, *args, **kwargs)
        return context.memo((function, args, tuple(sorted(kwargs.items()))),
                            lambda: function(workspace, *args, **kwargs))

    return invoke


def read_bytes(path: Path) -> bytes:
    context = _active.get()
    return (
        context.read_bytes(path)
        if context is not None and isinstance(path, Path)
        else path.read_bytes()
    )


def read_text(path: Path, *args, **kwargs) -> str:
    context = _active.get()
    if context is None or not isinstance(path, Path):
        return path.read_text(*args, **kwargs)
    with io.TextIOWrapper(io.BytesIO(context.read_bytes(path)), *args, **kwargs) as stream:
        return stream.read()


def _write_contexts(path: Path):
    """Internal path/root guard only; callers publishing content must still verify reads."""
    context = _active.get()
    while context is not None:
        # Rebinding can make owns(path) false; reject before that filter.
        context._verify_root()
        if isinstance(path, Path):
            resolved = path.resolve()
            original = path.absolute()
            inside = original.is_relative_to(context.root) or any(
                original.is_relative_to(root_input) for root_input in context._root_inputs
            )
            if inside and not resolved.is_relative_to(context.root):
                raise SnapshotChangedError("工作区内写入路径已链接到外部；禁止发布")
            if resolved.is_relative_to(context.root):
                yield context
        context = context.parent


def before_write(path: Path) -> None:
    for context in _write_contexts(path):
        context.verify_read_set()


def after_write(path: Path) -> None:
    context = _active.get()
    while context is not None:
        if isinstance(path, Path) and context.owns(path):
            context.next_phase([path])
        context = context.parent


def write_text(path: Path, *args, **kwargs):
    before_write(path)
    result = path.write_text(*args, **kwargs)
    after_write(path)
    return result


def write_bytes(path: Path, data):
    before_write(path)
    result = path.write_bytes(data)
    after_write(path)
    return result


class _PrivateStaging:
    """Exclusive unpublished files, never an alternative canonical write path."""

    def __init__(self, workspace, directory):
        self.workspace = workspace
        self.root = workspace.root.absolute()
        self.directory = directory.absolute()
        name = self.directory.name
        if (self.directory.parent != self.root / "runs/orchestration/transactions"
                or not name.startswith("tx-") or not name[3:].replace("-", "").isalnum()):
            raise ValueError("私有stage仅限事务目录内本次operation子目录")
        self.context = current_verification(workspace)
        if self.context is None:
            raise ValueError("私有stage必须绑定当前验证上下文")
        self._check_path()
        if self.directory.exists():
            raise FileExistsError("私有stage已存在；保留原残留，禁止借用或覆盖")
        mkdir(self.directory.parent, parents=True, exist_ok=True)
        self._check_path()
        self.directory.mkdir()  # Exclusive private allocation, without published authority.
        info = self.directory.stat(follow_symlinks=False)
        self.identity = (info.st_dev, info.st_ino)
        self.created = [self.directory]
        self.contents = {}
        self.identities = {}
        self.closed = False

    def _check_path(self):
        if self.root.resolve() != self.root or not self.directory.is_relative_to(self.root):
            raise ValueError("私有stage越界或根目录为链接")
        for path in (self.directory, *self.directory.parents):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError("私有stage及祖先不得使用链接／junction")
            if path == self.root:
                break

    def _check_owner(self):
        self.context._ensure_open()
        if current_verification(self.workspace) is not self.context:
            raise ValueError("私有stage不属于当前验证上下文")
        self._check_path()
        info = self.directory.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self.identity:
            raise ValueError("私有stage归属已变化；禁止发布")

    def write_bytes(self, path, data):
        self._check_owner()
        path = path.absolute()
        if self.closed or path.parent != self.directory:
            raise ValueError("私有写仅限本次stage直属新文件")
        with path.open("xb") as handle:
            self.created.append(path)
            handle.write(data)
        info = path.stat(follow_symlinks=False)
        self.contents[path] = hashlib.sha256(data).digest()
        self.identities[path] = (info.st_dev, info.st_ino)

    def finish(self):
        if self.closed:
            return
        self._check_owner()
        self.closed = True
        context = self.context
        while context is not None:
            context.next_phase([path for path in self.created if context.owns(path)])
            context = context.parent

    def verify(self):
        self._check_owner()
        if not self.closed:
            raise ValueError("私有stage尚未封口")
        if set(glob_files(self.directory, "*", include_non_files=True)) != set(self.contents):
            raise ValueError("私有stage成员已变化；禁止发布")
        for path, expected in self.contents.items():
            info = path.stat(follow_symlinks=False)
            if (path.is_symlink() or getattr(path, "is_junction", lambda: False)()
                    or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (info.st_dev, info.st_ino) != self.identities[path]
                    or hashlib.sha256(read_bytes(path)).digest() != expected):
                raise ValueError("私有stage文件归属／内容已变化；禁止发布")
        self._check_owner()


@contextmanager
def private_staging(workspace, directory):
    staging = _PrivateStaging(workspace, directory)
    try:
        yield staging
    finally:
        staging.finish()  # Preserve failures as non-authoritative, never-reused residue.


def mkdir(path: Path, mode=0o777, parents=False, exist_ok=False):
    if exist_ok and path.is_dir():
        return path.mkdir(mode, parents, exist_ok)  # No new write phase for an existing directory.
    # Record which parent directories this owned operation actually creates.
    created = [p for p in (path, *path.parents) if not p.exists()]
    before_write(path)
    # A missing root stays missing for reads. Only this exclusive owned mkdir
    # may bind its new identity; exist_ok must not adopt an externally created root.
    pending = list(_write_contexts(path))
    roots = sorted({context.root for context in pending
                    if context._root_identity is None
                    and (path.resolve() == context.root or parents)},
                   key=lambda root: len(root.parts))
    for root in roots:
        root.mkdir(mode=mode if path.resolve() == root else 0o777,
                   parents=parents, exist_ok=False)
        info = root.stat()
        for context in pending:
            if context.root == root:
                context._root_identity = (info.st_dev, info.st_ino)
    result = None if path.resolve() in roots else path.mkdir(mode, parents, exist_ok)
    for directory in reversed(created):
        if directory.is_dir():
            after_write(directory)
    after_write(path)
    return result


def copyfile(source: Path, target: Path):
    import shutil

    before_write(target)
    result = shutil.copyfile(source, target)
    after_write(target)
    return result


def unlink(path: Path, *args, **kwargs):
    before_write(path)
    result = path.unlink(*args, **kwargs)
    after_write(path)
    return result


def atomic_replace(source, target):
    source_path, target_path = Path(source), Path(target)
    before_write(target_path)
    os.replace(source, target)
    after_write(source_path)
    after_write(target_path)


@contextmanager
def write_open(path: Path, *args, **kwargs):
    before_write(path)
    with path.open(*args, **kwargs) as handle:
        try:
            yield handle
        finally:
            handle.flush()
    after_write(path)


def glob_files(directory: Path, pattern: str, *, include_non_files=False):
    context = _active.get()
    if context is not None:
        return context.watch_directory(directory, pattern, include_non_files=include_non_files)
    return [p for p in directory.glob(pattern) if include_non_files or p.is_file()]


def exists(path):
    context = _active.get()
    return (
        context.exists(path)
        if context is not None and isinstance(path, Path)
        else path.exists()
    )


def current_verification(workspace):
    context = _active.get()
    return context if context is not None and context.root == workspace.root.resolve() else None


def is_file(path):
    context = _active.get()
    return (
        context.is_file(path)
        if context is not None and isinstance(path, Path)
        else path.is_file()
    )


def load_yaml(value):
    """Use the existing safe C loader when available; cache pure parsed values."""
    import yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    context = _active.get()
    if context is None or not isinstance(value, (str, bytes)):
        return yaml.load(value, Loader=loader)
    data = value.encode("utf-8") if isinstance(value, str) else value
    return context.parsed_payload("yaml.safe.v1", data, lambda raw: yaml.load(raw, Loader=loader))
