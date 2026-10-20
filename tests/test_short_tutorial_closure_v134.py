"""稀疏只是定位线索；有限闭合与长代码续页在实际PDF验证。"""

from pathlib import Path

import pytest
from pypdf import PdfReader

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.presentation import PresentationOverrides
from knowledge_product_workflow.publishing import create_quarto_source, render_pdf
from knowledge_product_workflow.visual_audit import VisualPageMetric
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_possible_orphan_closure_requires_specific_review_without_fill_quota():
    from knowledge_product_workflow.visual_audit import _closure_findings
    pages = [VisualPageMetric(page_number=i, page_type='body', width_pt=595.28, height_pt=841.89,
        text_characters=1800, body_height_fraction=.7) for i in range(1,5)]
    pages[-1].body_height_fraction = .03
    pages[-1].text_characters = 80
    findings = _closure_findings(pages)
    assert [item.code for item in findings] == ['PDF_POSSIBLE_ORPHAN_CLOSURE']
    assert findings[0].severity.value == 'warning', '稀疏不能直接代替教学目的判断'
    pages[-1].text_characters = 600
    assert not _closure_findings(pages), '不能用固定填充率裁决所有留白'


@pytest.mark.requires_pdf_tools
def test_long_code_uses_legal_continuation_marker_and_keeps_payload(tmp_path):
    workspace = Workspace.create(tmp_path, 'code-continuation')
    create_product_brief(workspace, '长代码续页', DetailConstraints(delivery_format='pdf'),
        presentation=PresentationOverrides(cover_summary_zh='核验长代码合法续页及短总结的连续阅读。'))
    lines = [f'print("CODELINE{i:03d}")' for i in range(95)]
    body = '# 1. 操作\n\n保持整个文件可复制，运行下列代码：\n\n```python\n' + '\n'.join(lines) + '\n```\n\n'
    body += '最后核对 LASTRESULT：第一行是 CODELINE000，末行是 CODELINE094。\n'
    markdown = workspace.root / 'delivery/book.md'
    markdown.write_text(body, encoding='utf-8')
    qmd = create_quarto_source(workspace, markdown, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / 'delivery', workspace)
    pages = [page.extract_text() or '' for page in PdfReader(pdf).pages]
    joined = '\n'.join(pages)
    assert '代码（续）' in joined.replace(' ', ''), pdf
    assert all(f'CODELINE{i:03d}' in joined for i in range(95))
    assert '\n'.join(lines) in qmd.read_text(encoding='utf-8')
    last = next(i for i,text in enumerate(pages) if 'LASTRESULT' in text.replace(' ', ''))
    assert 'print("CODELINE094")' in pages[last].replace(' ', ''), '短结果与最后代码不应被拆成孤尾'


def test_only_breakable_reservations_use_a_partial_page_budget():
    root = Path(__file__).parents[1] / 'src/knowledge_product_workflow/templates/pdf/v6'
    source = (root / 'knowledge-book-components.tex').read_text(encoding='utf-8')
    assert 'KpReserveBreakable' in source
    assert r'\KpMeasuredHeight<\KpReserveLimit' in source
