from pathlib import Path

from knowledge_product_workflow.presentation_validation import validate_presentation_markdown
from knowledge_product_workflow.workspace import Workspace


def test_presentation_assets_must_be_local_to_the_workspace_assets_directory(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    asset = workspace.root / "assets" / "workflow.pdf"
    asset.parent.mkdir(exist_ok=True)
    asset.write_bytes(b"%PDF-fixture")

    findings = validate_presentation_markdown(
        "![工作流](assets/workflow.pdf)\n", "chapters/chapter-01.md", workspace
    )

    assert findings == []


def test_presentation_validation_rejects_remote_or_outside_images_and_unknown_components(
    tmp_path: Path,
) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    markdown = """![远程图](https://example.com/chart.png)
![越界图](../private.png)
![直接 SVG](assets/diagram.svg)

::: {.kp-unknown}
不支持的组件。
:::
"""

    findings = validate_presentation_markdown(markdown, "chapters/chapter-01.md", workspace)

    assert {
        "PRESENTATION_REMOTE_ASSET",
        "PRESENTATION_ASSET_OUTSIDE_WORKSPACE",
        "PRESENTATION_ASSET_TYPE_INVALID",
        "PRESENTATION_COMPONENT_INVALID",
    } <= {finding.code for finding in findings}


def test_source_card_requires_a_reader_visible_hyperlink_and_raw_tex_is_rejected(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    markdown = """::: {.kp-source}
来源：OpenAI 官方文档
:::

\\begin{figure}
危险的原始 TeX。
\\end{figure}
"""

    findings = validate_presentation_markdown(markdown, "delivery/book.md", workspace)

    assert {"PRESENTATION_SOURCE_CARD_INVALID", "PRESENTATION_RAW_TEX"} <= {
        finding.code for finding in findings
    }
