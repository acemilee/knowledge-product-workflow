"""本机冻结输入重复渲染校准，不冒充真实第二宿主验收。"""

import hashlib
import json
import subprocess

import pytest
from pypdf import PdfReader

from knowledge_product_workflow.d2 import render_svg_with_browser, resolve_diagram_browser
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.pdf_fonts import probe_preferred_fonts
from knowledge_product_workflow.pdf_preview import resolve_pdftoppm
from knowledge_product_workflow.presentation import PresentationOverrides
from knowledge_product_workflow.publishing import create_quarto_source, render_pdf
from knowledge_product_workflow.quarto import resolve_quarto
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_frozen_native_inputs_repeat_with_identical_page_pixels(tmp_path):
    rasterizer = resolve_pdftoppm()
    browser = resolve_diagram_browser()
    if not browser.available or not rasterizer or not resolve_quarto().available or not all(
        value["available"] for value in probe_preferred_fonts(tmp_path).values()
    ):
        pytest.skip("原生排版或指定字体缺失，未执行不等于通过")
    workspace = Workspace.create(tmp_path, "frozen-native")
    create_product_brief(workspace, "冻结输入格式基准", DetailConstraints(delivery_format="pdf"),
        presentation=PresentationOverrides(cover_summary_zh="核对相同内容与出版档案在本机的重复成页结果。"))
    body = r"""# 1. 格式基准

正文 Agent 与数字 10，以及行内标识 `task-notes` 用于核对混排。

## 1.1 数学与代码

行内计算 $2.4+3.0+1.8=7.2$；下面分别给出公式与代码。

$$
\operatorname{cos}(q,d)=\frac{q\cdot d}{\lVert q\rVert\lVert d\rVert}
$$

```python
print("doc-1", 1.0)
```

| 条件 | 结果 |
| --- | --- |
| 输入相同 | 核对页面 |
| 来源不足 | 标明未知 |

![结构基准](../assets/figures/frozen.pdf)
"""
    figure = workspace.root / "assets/figures/frozen.svg"
    figure.parent.mkdir(parents=True, exist_ok=True)
    figure.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="360" height="80"><rect x="10" y="10" width="120" height="50" rx="6" fill="#F8EBE2" stroke="#B65E2B"/><path d="M135 35H220" stroke="#202124"/><rect x="225" y="10" width="120" height="50" rx="6" fill="#E9EFF5" stroke="#202124"/></svg>', encoding="utf-8")
    render_svg_with_browser(browser.path, figure, figure.with_suffix(".pdf"), figure.with_suffix(".png"))
    markdown = workspace.root / "delivery/book.md"
    markdown.write_text(body, encoding="utf-8")
    qmd = create_quarto_source(workspace, markdown, qmd_markdown=body)
    rounds = []
    for number in (1, 2):
        pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
        directory = workspace.root / f"runs/repeat-{number}"
        directory.mkdir()
        (directory / "book.pdf").write_bytes(pdf.read_bytes())
        subprocess.run([str(rasterizer), "-r", "96", "-png", str(pdf), str(directory / "page")],
                       check=True, capture_output=True, timeout=30)
        rounds.append({"pages": len(PdfReader(pdf).pages), "png_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(directory.glob("page-*.png"))}})
    record = {"scope": "local_repeat_only", "rounds": rounds, "actual_second_host": False,
              "geometry_tolerance_pt": 1, "pixel_equal": rounds[0] == rounds[1]}
    (workspace.root / "runs/repeat-calibration.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    assert rounds[0]["pages"] >= 2
    assert rounds[0] == rounds[1], record
