"""真实出版探针：有限短教学任务保持完整，长解释仍可分页。"""
from pypdf import PdfReader

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.presentation import PresentationOverrides
from knowledge_product_workflow.publishing import create_quarto_source, render_pdf
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_real_short_label_and_final_task_are_not_split(tmp_path):
    w=Workspace.create(tmp_path,'short-task-probe')
    create_product_brief(w,'短教学块分页测试',DetailConstraints(delivery_format='pdf'),
        presentation=PresentationOverrides(cover_summary_zh='隔离机械验收材料，非产品或真实用户批准。'))
    sections=[]
    for n in (9,10,11,12):
        filler=('给定条件与实际输出应当在同一阅读位置相互核对。'*4+'\n\n')*n
        task=(f'**TASKBEGIN{n} 独立练习：** 请取固定材料，保存输入、步骤和预期结果。'
              '按原文分别核查每一个字段，将尚未确认的条件记录为待核。完成后交换记录，'
              f'由另一位读者指出缺少的依据和适用边界。TASKEND{n}')
        sections.append(f'# {n}. 固定分页情境\n\n{filler}\n\n**LABEL{n} 核对条件**\n\n'
                        f'EXPLAIN{n}：此标签的解释属于同一个有限教学任务，不应让标签独占页底。\n\n{task}\n')
    md=w.root/'delivery/book.md';body='\n'.join(sections);md.write_text(body,encoding='utf8')
    qmd=create_quarto_source(w,md)
    pdf=render_pdf(qmd,w.root/'delivery',w)
    pages=[''.join((p.extract_text() or '').split()) for p in PdfReader(pdf).pages]
    for n in (9,10,11,12):
        assert any(f'TASKBEGIN{n}' in page and f'TASKEND{n}' in page for page in pages),str(pdf)
        assert any(f'LABEL{n}' in page and f'EXPLAIN{n}' in page for page in pages),str(pdf)
    assert md.read_text(encoding='utf8')==body
