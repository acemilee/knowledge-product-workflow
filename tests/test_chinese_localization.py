import json
import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.authoring import (
    create_chapter_contract,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.product_brief import (
    create_product_brief,
    parse_detail_constraints,
)
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint


def _piped_kp(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    """Launch the CLI through raw pipes, the affected Windows-host boundary."""
    root = Path(__file__).parents[1]
    environment = os.environ.copy()
    source_path = str(root / "src")
    environment["PYTHONPATH"] = source_path + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from knowledge_product_workflow.cli import app; app()",
            *arguments,
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        check=False,
    )


def test_constraints_normalize_chinese_and_english_display_languages() -> None:
    assert DetailConstraints(target_words=1, language="中文").language == "zh-CN"
    assert DetailConstraints(target_words=1, language="英文").language == "en-US"


def test_new_brief_parses_chinese_and_legacy_english_constraint_headings(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief_path = create_product_brief(workspace, "中文教程", DetailConstraints(target_words=1))
    _, body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))

    assert "## 交付约束" in body
    assert parse_detail_constraints(body).language == "zh-CN"
    legacy = body.replace("## 交付约束", "## Delivery constraints")
    assert parse_detail_constraints(legacy).language == "zh-CN"


def test_english_brief_and_blueprint_use_english_fixed_structure(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief_path = create_product_brief(
        workspace, "Agent Interview Guide", DetailConstraints(target_words=1, language="英文")
    )
    metadata, body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    brief_path.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="editor"), body
        ),
        encoding="utf-8",
    )

    blueprint_path = submit_test_blueprint(workspace, brief_path, titles=("Foundations", "Cases"))
    blueprint = blueprint_path.read_text(encoding="utf-8")

    assert "## Target readers" in body
    assert "## Delivery constraints" in body
    assert "## Learning path" in blueprint
    assert "## Detail budgets" in blueprint

    blueprint_metadata, blueprint_body = parse_markdown_artifact(blueprint_path.read_text(encoding="utf-8"))
    blueprint_path.write_text(
        render_markdown_artifact(
            transition(blueprint_metadata, ArtifactStatus.APPROVED, approved_by="editor"), blueprint_body
        ),
        encoding="utf-8",
    )
    contract = create_chapter_contract(workspace, 1, "Foundations")
    draft = create_chapter_draft(workspace, 1)
    assert "## Learning objectives" in contract.read_text(encoding="utf-8")
    assert "## Exercises" not in draft.read_text(encoding="utf-8")


def test_new_artifacts_and_help_are_chinese(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    brief_path = create_product_brief(workspace, "中文教程", DetailConstraints(target_words=7))
    metadata, body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    brief_path.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="editor"), body
        ),
        encoding="utf-8",
    )
    blueprint_path = submit_test_blueprint(workspace, brief_path, titles=("基础", "案例"))
    blueprint_metadata, blueprint_body = parse_markdown_artifact(blueprint_path.read_text(encoding="utf-8"))
    blueprint_path.write_text(
        render_markdown_artifact(
            transition(blueprint_metadata, ArtifactStatus.APPROVED, approved_by="editor"), blueprint_body
        ),
        encoding="utf-8",
    )
    contract_path = create_chapter_contract(workspace, 1, "基础")
    draft_path = create_chapter_draft(workspace, 1)
    _, contract_body = parse_markdown_artifact(contract_path.read_text(encoding="utf-8"))
    _, draft_body = parse_markdown_artifact(draft_path.read_text(encoding="utf-8"))

    assert "目标读者" in body
    assert "学习路径" in blueprint_body
    assert "章节预算" in contract_body
    assert draft_body == "# 1. 基础\n"
    help_output = CliRunner().invoke(app, ["--help"]).output
    assert "用法" in help_output
    assert "选项" in help_output
    assert "命令" in help_output
    assert "Show this message" not in help_output


def test_piped_cli_output_and_errors_are_utf8_not_gbk() -> None:
    doctor = _piped_kp("doctor", "--json")

    assert doctor.returncode == 0
    doctor_text = doctor.stdout.decode("utf-8")
    assert "\ufffd" not in doctor_text
    assert isinstance(json.loads(doctor_text), dict)

    help_result = _piped_kp("humanize-check", "--help")
    assert help_result.returncode == 0
    assert "运行中文终稿编辑规则" in help_result.stdout.decode("utf-8")

    error = _piped_kp("humanize-check")
    assert error.returncode != 0
    error_text = error.stderr.decode("utf-8")
    assert "缺少必填参数" in error_text
    assert "错误：" in error_text
    assert "Missing argument" not in error_text
