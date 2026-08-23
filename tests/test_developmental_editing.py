from __future__ import annotations

import pytest
import yaml

from knowledge_product_workflow.content_plan import submit_content_plan
from knowledge_product_workflow.developmental import (
    DevelopmentalEditError,
    record_developmental_check,
    require_current_developmental_report,
)
from knowledge_product_workflow.workflows.authoring import (
    require_current_chapter_contract,
)
from knowledge_product_workflow.workspace import Workspace
from tests.test_chapter_content_plan import _approved_workspace, _valid_plan


def _source(workspace: Workspace, *, include_worksheet: bool) -> str:
    body = """# 1. 基础判断

## 1.1 决策框架

先把方案选择写成可检查的条件，再明确哪些信息是事实、哪些仍是假设。读者需要将每项选择与可观察的后果对应，避免只记住抽象原则而无法行动。
"""
    if include_worksheet:
        body += """
::: {.kp-worksheet}
使用说明：比较两个可选方案。

输入：目标、约束与可接受代价。

| 方案 | 条件 | 预期后果 |
| --- | --- | --- |
| A | 填写条件 | 填写后果 |

输出示例：选择 A，因为其约束可验证。

复盘提示：记录被放弃方案的风险。
:::
"""
    source = workspace.root / "runs" / "chapter-01-developmental-source.md"
    source.write_text(body.strip() + "\n", encoding="utf-8")
    return body.strip() + "\n"


def _submit_plan(workspace: Workspace) -> None:
    path = workspace.root / "runs" / "content-plan.yaml"
    path.write_text(yaml.safe_dump(_valid_plan(workspace), allow_unicode=True), encoding="utf-8")
    submit_content_plan(workspace, 1, path)


def test_developmental_report_requires_the_planned_worksheet(tmp_path) -> None:
    workspace = _approved_workspace(tmp_path)
    _submit_plan(workspace)
    _source(workspace, include_worksheet=False)

    _, report = record_developmental_check(
        workspace, 1, workspace.root / "runs" / "chapter-01-developmental-source.md"
    )

    assert report is not None
    assert {finding.code for finding in report.findings} == {"DEVELOPMENTAL_ARTIFACT_MISSING"}


def test_developmental_report_binds_body_contract_and_content_plan(tmp_path) -> None:
    workspace = _approved_workspace(tmp_path)
    _submit_plan(workspace)
    body = _source(workspace, include_worksheet=True)
    source = workspace.root / "runs" / "chapter-01-developmental-source.md"
    _, report = record_developmental_check(workspace, 1, source)
    contract_path, _ = require_current_chapter_contract(workspace, 1)

    assert report is not None
    assert report.findings == []
    assert require_current_developmental_report(workspace, 1, body, contract_path) is not None

    with pytest.raises(DevelopmentalEditError, match="已过期"):
        require_current_developmental_report(workspace, 1, body + "补充。\n", contract_path)
