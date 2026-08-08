"""KP-BUG-002 回归：audience_level 中文别名映射与中文报错。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.detail_constraints import (
    DetailConstraints,
    normalize_localized_constraints,
)
from knowledge_product_workflow.workspace import Workspace


def test_audience_level_chinese_aliases_normalize() -> None:
    mapping = {
        "入门": "beginner",
        "初学者": "beginner",
        "初级": "beginner",
        "初中级": "beginner",
        "进阶": "intermediate",
        "中级": "intermediate",
        "中高级": "intermediate",
        "专家": "expert",
        "高级": "expert",
    }
    for alias, expected in mapping.items():
        normalized = normalize_localized_constraints({"audience_level": alias})
        assert normalized["audience_level"] == expected, alias
        assert DetailConstraints.model_validate(normalized).audience_level.value == expected


def test_brief_accepts_new_aliases_and_reports_chinese_hint(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    runner = CliRunner()

    def _brief_input(level: str) -> Path:
        payload = {
            "topic": "别名探针",
            "target_audience": "中文初学者",
            "target_pages": "4",
            "audience_level": level,
            "delivery_format": "markdown",
            "review_policy": "deterministic_only",
        }
        path = workspace.root / "runs" / "brief-input.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    ok = runner.invoke(
        app,
        ["brief", "别名探针", "--workspace", str(workspace.root), "--from", str(_brief_input("初中级")), "--confirm"],
    )
    assert ok.exit_code == 0, ok.output
    assert (workspace.root / "planning" / "product-brief.md").is_file()

    bad = runner.invoke(
        app,
        ["brief", "别名探针", "--workspace", str(workspace.root), "--from", str(_brief_input("老鸟")), "--confirm"],
    )
    assert bad.exit_code == 2
    assert "受众级别不支持该写法" in bad.output
    assert "初中级" in bad.output and "高级" in bad.output, "报错必须列出中文别名"
    assert "Input should be" not in bad.output, "不得再暴露英文 pydantic 枚举错误"
