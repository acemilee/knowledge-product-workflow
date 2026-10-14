import json
import re
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parents[1] / "integrations" / "cc-switch"


def _manifest() -> dict[str, object]:
    return json.loads((PACKAGE_ROOT / "manifest.json").read_text(encoding="utf-8"))


def _canonical_resource_bytes(path: Path) -> bytes:
    """Hash text resources independently of the checkout's newline policy."""
    return path.read_bytes() if path.suffix == ".png" else path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def test_cc_switch_package_has_one_root_and_thirteen_specialists() -> None:
    manifest = _manifest()
    skills = manifest["skills"]
    assert isinstance(skills, list)
    names = [entry["name"] for entry in skills]
    assert names == [
        "kp-create",
        "kp-product-strategist",
        "kp-publication-designer",
        "kp-curriculum-architect",
        "kp-research-verifier",
        "kp-chapter-writer",
        "kp-developmental-editor",
        "kp-chinese-humanize-editor",
        "kp-diagram-designer",
        "kp-practice-designer",
        "kp-quality-editor",
        "kp-independent-reviewer",
        "kp-publisher",
        "kp-visual-reviewer",
    ]


def test_every_skill_declares_a_complete_operational_contract() -> None:
    for entry in _manifest()["skills"]:
        name = entry["name"]
        content = (PACKAGE_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert f"name: {name}" in content
        for heading in (
            "用途与触发条件",
            "前置工件及状态",
            "允许读取与写入",
            "禁止事项",
            "确定性验收命令",
            "下游交接条件",
        ):
            assert heading in content


def test_package_manifest_hash_binds_shared_resources_and_skill_references() -> None:
    manifest = _manifest()
    resources = manifest["shared_resources"]
    assert isinstance(resources, list)
    assert resources
    for resource in resources:
        path = PACKAGE_ROOT / resource["path"]
        assert path.is_file()
        assert resource["sha256"] == sha256(_canonical_resource_bytes(path)).hexdigest()

    reference_pattern = re.compile(r"`(?P<path>(?:\.\./)*references/[^`]+)`")
    for entry in manifest["skills"]:
        skill_path = PACKAGE_ROOT / "skills" / entry["name"] / "SKILL.md"
        for match in reference_pattern.finditer(skill_path.read_text(encoding="utf-8")):
            assert (skill_path.parent / match.group("path")).resolve().is_file()
        for resource in entry["resources"]:
            mirror = skill_path.parent / resource["path"]
            assert sha256(_canonical_resource_bytes(mirror)).hexdigest() == resource["sha256"]
            shared = next(item for item in resources if item["path"] == resource["path"])
            assert resource["sha256"] == shared["sha256"]


def test_each_skill_can_be_copied_as_a_self_contained_directory(tmp_path: Path) -> None:
    for entry in _manifest()["skills"]:
        destination = tmp_path / entry["name"]
        shutil.copytree(PACKAGE_ROOT / "skills" / entry["name"], destination)
        content = (destination / "SKILL.md").read_text(encoding="utf-8")
        assert "../../references/" not in content
        assert (destination / "references" / "orchestration-contract.md").is_file()


def test_humanizer_reference_license_and_upstream_cases_are_pinned() -> None:
    expected = {
        "humanizer-zh.md": "95627fd4437d886f98032ccc62d79136e6310f1fc04e354bb86431795ff206df",
        "humanizer-zh.LICENSE": "aa00e74769e1b9d8e7fa7094dbfcca9b129a0ded6dce1cf4da050b99146d2fa7",
    }
    editor = PACKAGE_ROOT / "skills/kp-chinese-humanize-editor/references"
    for name, digest in expected.items():
        original = (PACKAGE_ROOT / "references" / name).read_bytes()
        assert sha256(original).hexdigest() == digest
        assert (editor / name).read_bytes() == original
    fixture = Path(__file__).parent / "fixtures/humanize/upstream_cases.json"
    assert sha256(fixture.read_bytes()).hexdigest() == "6a62586ee93dd9695976cd45271036f2a92be4f74e1b3b9b6c21a411c8497c0e"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    assert len(cases) == 18


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="需要 PowerShell 验证 CC Switch 安装器")
def test_install_package_includes_the_shared_orchestration_contract(tmp_path: Path) -> None:
    contract = PACKAGE_ROOT / "references" / "orchestration-contract.md"
    destination = tmp_path / "cc-switch" / "skills"
    installer = PACKAGE_ROOT / "scripts" / "install-cc-switch.ps1"
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer),
            "-PackageRoot",
            str(PACKAGE_ROOT),
            "-DestinationRoot",
            str(destination),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert contract.is_file()
    assert (destination.parent / "references" / "orchestration-contract.md").read_bytes() == contract.read_bytes()
    for entry in _manifest()["skills"]:
        assert (destination / entry["name"] / "SKILL.md").is_file()
        assert (destination / entry["name"] / "references" / "orchestration-contract.md").read_bytes() == contract.read_bytes()


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="需要 PowerShell 验证 Skill 包完整性")
def test_package_verifier_rejects_a_tampered_shared_resource(tmp_path: Path) -> None:
    package = tmp_path / "package"
    shutil.copytree(PACKAGE_ROOT, package)
    resource = package / "references" / "orchestration-contract.md"
    resource.write_text("tampered\n", encoding="utf-8")
    verifier = package / "scripts" / "verify-cc-switch-package.ps1"

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(verifier),
            "-PackageRoot",
            str(package),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert completed.returncode != 0
    assert "hash mismatch" in completed.stderr.lower()


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="需要 PowerShell 验证安装回滚")
def test_installer_rolls_back_already_replaced_skills_after_a_move_failure(tmp_path: Path) -> None:
    destination = tmp_path / "installation" / "skills"
    destination.mkdir(parents=True)
    for entry in _manifest()["skills"]:
        target = destination / entry["name"]
        target.mkdir()
        (target / "SKILL.md").write_text(f"旧包 {entry['name']}\n", encoding="utf-8")
    before = {path.relative_to(destination).as_posix(): path.read_bytes() for path in destination.glob("*/SKILL.md")}
    installer = PACKAGE_ROOT / "scripts" / "install-cc-switch.ps1"

    def quote(path: Path) -> str:
        return "'" + str(path).replace("'", "''") + "'"

    script = r"""function Move-Item {
param([string]$LiteralPath,[string]$Destination)
if ($LiteralPath -like '*.kp-stage-*' -and $LiteralPath -like '*kp-curriculum-architect') { throw 'Injected move failure' }
Microsoft.PowerShell.Management\Move-Item -LiteralPath $LiteralPath -Destination $Destination
}
""" + f"& {quote(installer)} -PackageRoot {quote(PACKAGE_ROOT)} -DestinationRoot {quote(destination)}"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode != 0
    assert "Injected move failure" in result.stderr
    assert {path.relative_to(destination).as_posix(): path.read_bytes() for path in destination.glob("*/SKILL.md")} == before


def test_every_skill_metadata_version_matches_the_package_version() -> None:
    manifest = _manifest()
    expected_version = manifest["version"]
    for entry in manifest["skills"]:
        name = entry["name"]
        content = (PACKAGE_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert f'version: "{expected_version}"' in content


def test_root_skill_embeds_non_variable_operational_constraints() -> None:
    content = (PACKAGE_ROOT / "skills" / "kp-create" / "SKILL.md").read_text(encoding="utf-8")
    for phrase in ("全程使用中文", "仅操作当前工作目录", "最长 8 秒", "用户审批", "kp render-pdf"):
        assert phrase in content
    assert "30000-50000" not in content
    assert "submit-review-batch" not in content
    assert "用户最小输入只有主题、目标受众和受众级别" in content
    assert "绝不追问目标页数、目标字数、语言、版式或交付格式" in content
    assert "默认审稿策略是 `deterministic_only`" in content
    assert "必须获得真实宿主父子任务回执" in content
    assert "--from brief-input.json" not in content
    assert "<workspace>/runs/brief-input.json" in content
    assert "不得查询 `uv tool`" in content


def test_recovery_skills_preserve_machine_readable_constraint_protocol() -> None:
    root = (PACKAGE_ROOT / "skills" / "kp-create" / "SKILL.md").read_text(encoding="utf-8")
    curriculum = (PACKAGE_ROOT / "skills" / "kp-curriculum-architect" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    publisher = (PACKAGE_ROOT / "skills" / "kp-publisher" / "SKILL.md").read_text(encoding="utf-8")

    assert "runs/request-contract.yaml" in root
    assert "effective_constraints" in root
    assert "next_actions" in root
    assert "kp amend-brief" in root
    assert "kp amend-outline" in root
    assert "BLUEPRINT_CONTENT_BINDING_STALE" in curriculum
    assert "不得删除蓝图" in curriculum
    assert "PDF_REQUIRED_AND_MISSING" in publisher
    assert "delivery_format: both" in publisher


def test_publishing_skills_keep_reader_components_inside_the_fixed_pdf_contract() -> None:
    writer = (PACKAGE_ROOT / "skills" / "kp-chapter-writer" / "SKILL.md").read_text(encoding="utf-8")
    publisher = (PACKAGE_ROOT / "skills" / "kp-publisher" / "SKILL.md").read_text(encoding="utf-8")

    assert "kp-source" in writer
    assert "原始 TeX" in writer
    assert "assets/" in writer
    assert "publication-metadata.yaml" in publisher
    assert "publication-profile.yaml" in publisher
    assert "kp update-publication" in publisher
    assert "SVG" in publisher
    assert "kp render-pdf" in publisher


def test_reviewer_skill_requires_snapshot_bound_host_skill_review() -> None:
    content = (PACKAGE_ROOT / "skills" / "kp-independent-reviewer" / "SKILL.md").read_text(encoding="utf-8")
    assert "user-invocable: false" in content
    assert "mode: host-skill" in content
    assert "submit-review-result" in content
    assert "不得填写哈希" in content


def test_host_adapters_keep_commands_thin() -> None:
    command_root = PACKAGE_ROOT / "claude-code" / "commands"
    assert {path.name for path in command_root.glob("*.md")} == {
        "kp-create.md",
        "kp-continue.md",
        "kp-deliver.md",
    }
    assert "固定规则" in (command_root / "kp-create.md").read_text(encoding="utf-8")
