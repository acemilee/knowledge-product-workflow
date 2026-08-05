"""Deterministic local Quarto discovery shared by diagnostics and rendering."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QuartoResolution:
    """One locally verified Quarto candidate and its discovery provenance."""

    path: Path | None
    source: str | None
    version: str | None
    failed_candidates: tuple[str, ...]

    @property
    def available(self) -> bool:
        return self.path is not None


def _is_windows() -> bool:
    return os.name == "nt"


def _registry_candidates() -> Iterable[Path]:
    """Read standard Windows installer locations without scanning the disk."""
    if not _is_windows():
        return ()
    try:
        import winreg
    except ImportError:
        return ()
    candidates: list[Path] = []
    locations = (
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\\Quarto"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\Quarto"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\WOW6432Node\\Quarto"),
    )
    for hive, key_path in locations:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
        except OSError:
            continue
        if isinstance(install_dir, str) and install_dir:
            candidates.append(Path(install_dir) / "bin" / "quarto.exe")
    return tuple(candidates)


def _persistent_user_path_candidates() -> Iterable[Path]:
    """Read the user's persisted PATH when a long-lived host has a stale environment."""
    if not _is_windows():
        return ()
    try:
        import winreg
    except ImportError:
        return ()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            user_path, _ = winreg.QueryValueEx(key, "Path")
    except OSError:
        return ()
    if not isinstance(user_path, str) or not user_path.strip():
        return ()

    candidates: list[Path] = []
    for entry in user_path.split(";"):
        expanded = os.path.expandvars(entry.strip().strip('"'))
        if not expanded:
            continue
        path = Path(expanded)
        candidates.append(path if path.name.casefold() == "quarto.exe" else path / "quarto.exe")
    return tuple(candidates)


def _candidate_paths() -> Iterable[tuple[Path, str]]:
    configured = os.environ.get("KP_QUARTO_PATH", "").strip()
    if configured:
        yield Path(configured), "KP_QUARTO_PATH"
    located = shutil.which("quarto")
    if located:
        yield Path(located), "PATH"
    for candidate in _persistent_user_path_candidates():
        yield candidate, "用户 PATH（持久化）"
    if _is_windows():
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            yield Path(local_app_data) / "Programs" / "Quarto" / "bin" / "quarto.exe", "当前用户默认安装路径"
    for candidate in _registry_candidates():
        yield candidate, "Windows 注册表"
    yield Path(r"C:\Program Files\Quarto\bin\quarto.exe"), "默认安装路径"
    yield Path(r"C:\Program Files (x86)\Quarto\bin\quarto.exe"), "默认安装路径（x86）"


def resolve_quarto() -> QuartoResolution:
    """Resolve and locally health-check Quarto with an eight-second hard timeout."""
    failed: list[str] = []
    seen: set[Path] = set()
    for candidate, source in _candidate_paths():
        normalized = candidate.expanduser()
        if normalized in seen:
            continue
        seen.add(normalized)
        if source == "KP_QUARTO_PATH" and not normalized.is_absolute():
            failed.append(f"{source}：必须是绝对路径（{normalized}）")
            continue
        if not normalized.is_file():
            failed.append(f"{source}：不存在（{normalized}）")
            continue
        try:
            completed = subprocess.run(
                [str(normalized), "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            failed.append(f"{source}：不可运行（{normalized}；{type(error).__name__}）")
            continue
        version = (completed.stdout or completed.stderr).strip().splitlines()
        return QuartoResolution(
            path=normalized.resolve(),
            source=source,
            version=version[0] if version else "版本未知",
            failed_candidates=tuple(failed),
        )
    return QuartoResolution(path=None, source=None, version=None, failed_candidates=tuple(failed))
