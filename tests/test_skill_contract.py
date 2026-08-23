"""SKILL.md / 操作手册 与 CLI 命令注册表的静态契约检查。

宿主按 SKILL.md 提示词执行 `kp` 命令；提示词与控制面漂移（命令改名、
参数拼写错误、选项过时、Skill 元数据版本不一致）只有在人工模拟执行时
才会暴露。本模块把这份"提示词 ↔ 控制面"契约固化为可重复的确定性检查，
零外部依赖，随默认测试套件运行。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml
from typer.main import get_command

from knowledge_product_workflow.cli import app

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = PROJECT_ROOT / "integrations" / "cc-switch" / "skills"
MANIFEST_PATH = PROJECT_ROOT / "integrations" / "cc-switch" / "manifest.json"
CONTRACT_DOCS = [
    PROJECT_ROOT / "docs" / "skills.md",
    PROJECT_ROOT / "docs" / "operator-runbook.md",
    PROJECT_ROOT / "integrations" / "cc-switch" / "references" / "orchestration-contract.md",
    *sorted((PROJECT_ROOT / "integrations" / "cc-switch" / "claude-code" / "commands").glob("*.md")),
]

_COMMAND_RE = re.compile(r"\bkp[ \t]+([a-z][a-z0-9-]*)[ \t]*(?P<rest>[^`\n]*?)(?=[`\n]|$)")
_OPTION_RE = re.compile(r"--([a-z][a-z0-9-]*)")

USER_VISIBLE_COMMANDS = {"init", "brief", "status", "doctor"}


@pytest.fixture(scope="module")
def cli_registry() -> dict[str, dict]:
    """构建 CLI 命令注册表：命令名 -> {hidden, params: {参数名: {required, opts}}}。"""
    command = get_command(app)
    registry: dict[str, dict] = {}
    for name, info in command.commands.items():
        registry[name] = {
            "hidden": bool(info.hidden),
            "params": {
                param.name: {"required": bool(param.required), "opts": list(param.opts)}
                for param in info.params
            },
        }
    return registry


def _skill_files() -> list[Path]:
    return sorted(SKILLS_ROOT.glob("*/SKILL.md"))


def _referenced_commands() -> dict[str, set[str]]:
    """文档中 `kp <command>` 引用 -> 该命令关联的 `--option` 引用集合。"""
    references: dict[str, set[str]] = {}
    for path in [*_skill_files(), *CONTRACT_DOCS]:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for match in _COMMAND_RE.finditer(text):
            command, rest = match.group(1), match.group(2)
            references.setdefault(command, set()).update(_OPTION_RE.findall(rest))
    return references


def _parse_skill_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    _, frontmatter, _ = text.split("---", 2)
    loaded = yaml.safe_load(frontmatter)
    return loaded if isinstance(loaded, dict) else {}


def test_manifest_matches_installed_skill_files() -> None:
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_names = {entry["name"] for entry in manifest["skills"]}
    installed = {path.parent.name for path in _skill_files()}
    assert manifest_names == installed
    assert len(manifest_names) == 13


def test_skill_frontmatter_is_consistent_with_package_metadata() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]
    for path in _skill_files():
        metadata = _parse_skill_frontmatter(path.read_text(encoding="utf-8"))
        assert metadata.get("name") == path.parent.name, path
        assert metadata.get("metadata", {}).get("version") == project_version, path
        # user-invocable 必须显式声明为布尔值；只有根编排 Skill 面向用户调用。
        assert isinstance(metadata.get("user-invocable"), bool), path
        if path.parent.name == "kp-create":
            assert metadata.get("user-invocable") is True, path


def test_every_skill_document_is_loaded() -> None:
    # 防止上面契约文档路径变更后测试静默缩小扫描范围。
    assert len(_skill_files()) == 13
    assert all(path.is_file() for path in CONTRACT_DOCS), CONTRACT_DOCS


def test_all_referenced_kp_commands_exist(cli_registry: dict[str, dict]) -> None:
    references = _referenced_commands()
    missing = sorted(set(references) - set(cli_registry))
    assert missing == [], f"SKILL.md / 手册引用了未注册的 kp 命令：{missing}"


def test_all_referenced_kp_options_exist(cli_registry: dict[str, dict]) -> None:
    bad: list[str] = []
    for command, options in sorted(_referenced_commands().items()):
        if command not in cli_registry:
            continue
        known = {option for param in cli_registry[command]["params"].values() for option in param["opts"]}
        for option in sorted(options):
            if f"--{option}" not in known:
                bad.append(f"kp {command} --{option}")
    assert bad == [], f"SKILL.md / 手册引用了不存在的命令选项：{bad}"


def test_user_visible_commands_remain_visible(cli_registry: dict[str, dict]) -> None:
    hidden = {name for name, info in cli_registry.items() if info["hidden"]}
    assert not (USER_VISIBLE_COMMANDS & hidden), "用户可见命令被意外隐藏"
    assert "route" in hidden and "publish" in hidden, "内部控制命令必须保持隐藏"
