from __future__ import annotations

from knowledge_product_workflow.callouts import render_qmd_markdown, render_reader_markdown
from knowledge_product_workflow.presentation_validation import validate_presentation_markdown
from knowledge_product_workflow.workspace import Workspace


def test_new_reader_components_render_to_portable_markdown_and_qmd() -> None:
    source = """::: {.kp-decision}
判断：优先验证影响最大的未知条件。
:::

::: {.kp-procedure}
输入：约束。

1. 记录条件。
2. 输出结论。
:::

::: {.kp-worksheet}
使用说明：比较可选方案。

输入：目标与约束。

| 方案 | 后果 |
| --- | --- |
| A | 可验证 |

输出示例：选择 A。

复盘提示：记录放弃项。
:::
"""

    reader = render_reader_markdown(source)
    qmd = render_qmd_markdown(source)

    assert "**决策要点**" in reader
    assert "**操作步骤**" in reader
    assert "**工作表**" in reader
    assert ".kp-" not in reader
    assert all(component in qmd for component in (".kp-decision", ".kp-procedure", ".kp-worksheet"))


def test_worksheet_requires_usage_input_output_and_reflection(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    markdown = """::: {.kp-worksheet}
使用说明：填写一个方案。

输入：目标。
:::
"""

    findings = validate_presentation_markdown(markdown, "chapters/chapter-01.md", workspace)

    assert {finding.code for finding in findings} == {"PRESENTATION_WORKSHEET_INVALID"}
