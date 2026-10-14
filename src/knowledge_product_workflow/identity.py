"""只读运行身份：文件与包元数据可验证，宿主/模型声明不可冒充实证。"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import subprocess
import sys
import tomllib
from pathlib import Path


def runtime_identity(host: str | None = None, model: str | None = None) -> dict:
    package = Path(__file__).resolve().parent
    project = package.parent.parent
    editable = (project / "pyproject.toml").is_file() and (project / "src" / package.name) == package
    try:
        distribution = importlib.metadata.version("knowledge-product-workflow")
    except importlib.metadata.PackageNotFoundError:
        distribution = None
    version = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"] if editable else distribution
    resources = {}
    for path in sorted((package / "templates").rglob("*")):
        if path.is_file():
            resources[path.relative_to(package).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    resources_digest = hashlib.sha256(json.dumps(resources, sort_keys=True).encode()).hexdigest()
    manifest = project / "integrations/cc-switch/manifest.json"
    skill = json.loads(manifest.read_text(encoding="utf-8")) if editable and manifest.is_file() else None
    vcs = {"commit": None, "dirty": None, "status": "unavailable"}
    if editable and (project / ".git").exists():
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, check=True, timeout=10).stdout.decode().strip()
            dirty = subprocess.run(["git", "status", "--porcelain"], cwd=project, capture_output=True, check=True, timeout=10).stdout
            vcs = {"commit": commit, "dirty": bool(dirty), "status": "verified"}
        except (OSError, subprocess.SubprocessError):
            pass
    def declaration(value):
        return {"value": value, "status": "declared" if value else "unknown", "evidence": "caller" if value else None}
    return {
        "effective_source": str(package), "source_version": version,
        "distribution_version": distribution, "executable": sys.executable,
        "python_version": sys.version.split()[0], "mode": "editable_source" if editable else "installed_package",
        "version_consistent": version == distribution and (skill is None or skill["version"] == version),
        "skill_version": skill["version"] if skill else None,
        "skill_loading": {"status": "unknown", "manifest_file_verified": skill is not None,
                          "note": "文件身份不能证明宿主已加载上下文"},
        "skill_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest() if skill else None,
        "resources_sha256": resources_digest, "resources": resources, "git": vcs,
        "host": declaration(host), "model": declaration(model),
    }
