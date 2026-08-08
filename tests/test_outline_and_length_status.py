"""KP-BUG-007/008 回归：outline 报错提示修订路径；篇幅评估提交后状态可见。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.runtime import inspect_workflow
from knowledge_product_workflow.workflows.product_brief import brief_contract_fingerprints
from knowledge_product_workflow.workspace import Workspace


def _approved_brief(workspace: Workspace) -> None:
    """init -> brief(4 页) -> 用户批准。"""
    brief_input = {
        "topic": "阶段四探针",
        "target_audience": "中文初学者",
        "target_pages": "4",
        "language": "中文",
        "layout_profile": "A4 标准版式",
        "audience_level": "入门",
        "delivery_format": "Markdown",
        "review_policy": "deterministic_only",
    }
    input_path = workspace.root / "runs" / "brief-input.json"
    input_path.write_text(json.dumps(brief_input, ensure_ascii=False), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app, ["brief", "阶段四探针", "--workspace", str(workspace.root), "--from", str(input_path), "--confirm"]
    )
    assert result.exit_code == 0, result.output
    for command in (
        ["request-user-decision", "brief", "--workspace", str(workspace.root)],
        ["record-user-decision", "brief", "--workspace", str(workspace.root), "--decision", "approve", "--response", "可以", "--by", "用户"],
        ["approve", str(workspace.root / "planning" / "product-brief.md"), "--workspace", str(workspace.root)],
    ):
        result = runner.invoke(app, command)
        assert result.exit_code == 0, (command, result.output)


def test_outline_duplicate_error_suggests_amend(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _approved_brief(workspace)
    runner = CliRunner()

    first = runner.invoke(app, ["outline", "--workspace", str(workspace.root)])
    assert first.exit_code == 0, first.output
    second = runner.invoke(app, ["outline", "--workspace", str(workspace.root)])
    assert second.exit_code != 0
    assert "amend-outline" in second.output, "重复 outline 的报错必须提示修订路径"
    assert "--from" in second.output


def test_blueprint_missing_reflects_submitted_length_recommendation(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _approved_brief(workspace)

    before = inspect_workflow(workspace)
    assert before.stage == "blueprint_missing"
    assert "缺少课程蓝图" in before.blocked_by[0]

    # 提交篇幅评估后，blocked_by 必须反映该前置条件已满足。
    brief = workspace.root / "planning" / "product-brief.md"
    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    recommendation = {
        "schema_version": 1,
        "brief_content_sha256": brief_contract_fingerprints(body)["content_contract_sha256"],
        "brief_version": metadata.version,
        "recommended_min_pages": 4,
        "recommended_max_pages": 4,
        "confidence": "high",
        "rationale": ["探针"],
        "scope_expansion_units": [],
    }
    rec_path = workspace.root / "runs" / "length-recommendation.yaml"
    rec_path.write_text(yaml.safe_dump(recommendation, allow_unicode=True, sort_keys=False), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app, ["submit-length-recommendation", "--workspace", str(workspace.root), "--from", str(rec_path)]
    )
    assert result.exit_code == 0, result.output

    after = inspect_workflow(workspace)
    assert after.stage == "blueprint_missing"
    assert "篇幅评估已提交" in after.blocked_by[0], after.blocked_by
    assert "缺少课程蓝图" not in after.blocked_by[0]
