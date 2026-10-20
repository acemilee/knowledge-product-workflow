"""对受控最终PDF测列间字形；默认布局和真实代码单元共同验证。"""

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.presentation import PresentationOverrides
from knowledge_product_workflow.publishing import create_quarto_source, render_pdf
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def native_table_fixture(tmp_path, extra=''):
    workspace = Workspace.create(tmp_path, 'table-gutters')
    create_product_brief(workspace, '表格边界原生检查', DetailConstraints(delivery_format='pdf'),
                         presentation=PresentationOverrides(cover_summary_zh='核验列间留白、程序标识符、数学与合法续页。'))
    body = '# 1. 表格边界\n\n' + extra + r'''

| 核对项目 | 程序调用 | 结果 |
|---|---|---|
| 数量和顺序 | `retrieve(q, docs, top_k=2)` | 取回前两条 |
| 向量长度 | `sqrt(sum(x*x for x in q))` | $\sqrt{1^2+0^2}=1$ |

```{=latex}
\begin{longtable}{@{}p{.32\linewidth}p{.32\linewidth}p{.24\linewidth}@{}}
\hfill LEFTEND & RIGHTSTART & 第三列\\
\hfill LEFTTWO & RIGHTTWO & 中文与数字 20\\
\end{longtable}
```

这是表外正文。`retrieval` 标识符保持原样，$0.8^2+0.6^2=1$ 仍属于数学。
'''
    markdown = workspace.root / 'delivery/book.md'
    markdown.write_text(body, encoding='utf-8')
    qmd = create_quarto_source(workspace, markdown, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / 'delivery', workspace)
    tool = shutil.which('pdftotext')
    assert tool, '缺实际bbox通道，不计成页通过'
    xml = subprocess.run([tool, '-bbox-layout', str(pdf), '-'], capture_output=True, timeout=30, check=True).stdout
    (pdf.parent / 'glyph-bboxes.xhtml').write_bytes(xml)
    return pdf, ET.fromstring(xml)


@pytest.mark.requires_pdf_tools
def test_native_visible_column_gutters_and_payload(tmp_path):
    pdf, tree = native_table_fixture(tmp_path)
    words = {node.text: node.attrib for node in tree.iter() if node.tag.endswith('word')}
    gaps = []
    for left, right in [('LEFTEND', 'RIGHTSTART'), ('LEFTTWO', 'RIGHTTWO')]:
        assert left in words and right in words
        assert abs(float(words[left]['yMin']) - float(words[right]['yMin'])) < .5
        gaps.append(float(words[right]['xMin']) - float(words[left]['xMax']))
    (pdf.parent / 'gutter-measurement.json').write_text(json.dumps({'gaps_pdf_pt': gaps, 'required_minimum': 6}), encoding='utf-8')
    assert min(gaps) >= 6, (pdf, gaps)
    log = (pdf.parent / 'book.log').read_text(encoding='utf-8', errors='replace') if (pdf.parent / 'book.log').exists() else ''
    assert 'Overfull \\hbox' not in log, log[-2000:]


def test_inline_identifiers_prefer_separators_and_allow_long_token_fallback():
    source = (Path(__file__).parents[1] / 'src/knowledge_product_workflow/templates/pdf/v6/knowledge-book-components.lua').read_text(encoding='utf-8')
    assert '\\allowbreak{}' in source
    assert '\\penalty100{}' in source, '无分隔符的超长标识须有非印刷字符的断行退路，分隔符仍优先'
