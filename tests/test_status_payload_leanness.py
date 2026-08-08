"""KP-BUG-005 回归：status --json 载荷不含 workspace 绝对路径，事件已截断。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.workspace import Workspace


def test_status_json_has_no_absolute_workspace_path(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    runner = CliRunner()

    result = runner.invoke(app, ["status", "--json", "--workspace", str(workspace.root)])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    rendered = result.output
    assert str(workspace.root) not in rendered, "status --json 泄漏 workspace 绝对路径"
    assert "<workspace>" in rendered, "绝对路径应替换为 <workspace> 占位符"
    assert len(payload["recent_events"]) <= 5, "recent_events 必须截断"


def test_orchestration_plan_json_has_no_absolute_workspace_path(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    runner = CliRunner()

    result = runner.invoke(
        app, ["orchestration-plan", "--json", "--workspace", str(workspace.root)]
    )
    assert result.exit_code == 0, result.output
    assert str(workspace.root) not in result.output
    assert "<workspace>" in result.output


def test_status_human_view_stays_clean(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    runner = CliRunner()

    result = runner.invoke(app, ["status", "--workspace", str(workspace.root)])
    assert result.exit_code == 0, result.output
    assert str(workspace.root) not in result.output, "用户可见视图不得出现绝对路径"
