"""单轮真实故障的定向回归；实际PDF字形与CLI流分别核实。"""
import json
import unicodedata

import pytest
import typer
from pypdf import PdfReader
from typer.testing import CliRunner

from knowledge_product_workflow import cli
from knowledge_product_workflow.callouts import render_reader_markdown
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.figure_templates import _lines
from knowledge_product_workflow.pdf_fonts import probe_preferred_fonts
from knowledge_product_workflow.presentation import PresentationOverrides
from knowledge_product_workflow.publishing import (
    create_quarto_source,
    missing_pdf_source_contracts,
    render_pdf,
)
from knowledge_product_workflow.quarto import resolve_quarto
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def test_command_discovery_can_target_one_controlled_command():
    result=CliRunner().invoke(cli.app,['commands','--all','--command','record-developmental-review','--json'])
    assert result.exit_code==0,result.output
    entries=json.loads(result.stdout)['commands']
    assert [e['command'] for e in entries]==['record-developmental-review']
    assert any('--from' in p['flags'] and p['required'] for p in entries[0]['parameters'])


def test_command_discovery_rejects_unknown_and_hidden_without_all():
    for args in (['commands','--command','does-not-exist'],['commands','--command','record-developmental-review']):
        result=CliRunner().invoke(cli.app,args)
        assert result.exit_code==2
        assert result.stderr


def test_successful_estimate_notice_never_uses_error_stream(tmp_path):
    from tests.test_page_word_budget_estimation import _run_brief_to_blueprint
    workspace=Workspace.create(tmp_path,'success-stream')
    _run_brief_to_blueprint(CliRunner(),workspace)
    command=typer.Typer()
    @command.command()
    def probe():
        cli._echo_estimated_budget_warning(workspace)
    result=CliRunner().invoke(command,[])
    assert result.exit_code==0
    assert '字下限' in result.stdout
    assert result.stderr==''


@pytest.mark.parametrize('label,limit,token',[('来源: doc-1, v1',9,'doc-1'),('片段：chunk_id',10,'chunk_id'),('路径 a/b.py',8,'a/b.py')])
def test_svg_wrap_keeps_identifiers_intact(label,limit,token):
    lines=_lines(label,limit)
    assert any(token in line for line in lines)
    assert ''.join(lines)==label


def test_svg_wrap_rejects_overwide_token_and_preserves_explicit_breaks():
    with pytest.raises(ValueError):
        _lines('too_long_identifier',5)
    assert _lines('中文\ndoc-1',9)==['中文','doc-1']


def test_actual_svg_pdf_does_not_break_source_identifier(tmp_path):
    from knowledge_product_workflow.diagrams import (
        insert_diagram_reference,
        resolve_diagram_browser,
        submit_diagram,
    )
    from tests.test_diagrams import _approved_workspace, _source_and_input
    if not resolve_diagram_browser().available:
        pytest.skip('无原生图表浏览器；skip不是图内标识通过')
    workspace=_approved_workspace(tmp_path,v6=True)
    source,inputs=_source_and_input(workspace)
    data=json.loads(inputs.read_text(encoding='utf-8'))
    data.update(diagram_type='stage_flow',nodes=[{'node_id':f'n{i}','label':label} for i,label in enumerate(
        ['向量排序记录','片段标识: chunk-1','原文: 负责人是平台组','来源: doc-1, v1'])],
        edges=[{'source':f'n{i}','target':f'n{i+1}'} for i in range(3)])
    inputs.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    insert_diagram_reference(workspace,1,source,'agent-layers',inputs)
    pdf,manifest=submit_diagram(workspace,1,source,inputs)
    assert manifest.renderer=='svg-template'
    text=''.join(page.extract_text() for page in PdfReader(pdf).pages)
    assert 'doc-1' in text and 'chunk-1' in text,text
    svg=workspace.root/'runs/diagrams/agent-layers.svg'
    import xml.etree.ElementTree as ET
    lines=[''.join(e.itertext()) for e in ET.fromstring(svg.read_text(encoding='utf-8')).iter() if e.tag.endswith('}text')]
    assert any('doc-1' in line for line in lines),lines


def test_old_math_role_template_is_stale(tmp_path):
    workspace=Workspace.create(tmp_path,'math-role-stale')
    create_product_brief(workspace,'字体角色',DetailConstraints(delivery_format='pdf'))
    md=workspace.root/'delivery/book.md';md.write_text('# 字体角色\n\n正文。\n',encoding='utf-8')
    qmd=create_quarto_source(workspace,md)
    style=qmd.parent/'knowledge-book-style.tex'
    style.write_text(style.read_text(encoding='utf-8').replace(r'\setoperatorfont{\symup}',''),encoding='utf-8')
    assert 'PDF_DEFAULT_FONT_CONTRACT_STALE' in missing_pdf_source_contracts(qmd)


def test_actual_pdf_math_alphabets_keep_body_font_separate(tmp_path):
    if not resolve_quarto().available or not all(x['available'] for x in probe_preferred_fonts(tmp_path).values()):
        pytest.skip('缺实际出版工具或默认字体；skip不是字形通过')
    workspace=Workspace.create(tmp_path,'math-role-native')
    create_product_brief(workspace,'数学字母表核验',DetailConstraints(delivery_format='pdf'),
        presentation=PresentationOverrides(cover_layout_revision=2,cover_summary_zh='核对算子、数学直立与粗体；正文英文保持原角色。'))
    body=r'''# 字体角色

正文 English 与数学保持各自的字体。

$$\cos(q,d)=\operatorname{sim}(q,d)+\mathrm{ROLE}+\mathbf{BOLD}+\mathit{ital}$$

图内标识符 doc-1 保持整体，数学数字为 $0.8$。
'''
    md=workspace.root/'delivery/book.md';md.write_text(render_reader_markdown(body),encoding='utf-8')
    qmd=create_quarto_source(workspace,md,qmd_markdown=body)
    pdf=render_pdf(qmd,workspace.root/'delivery',workspace)
    runs=[]
    for page in PdfReader(pdf).pages[2:]:
        def visit(text,cm,tm,font,size):
            if text.strip():runs.append({'text':unicodedata.normalize('NFKC',text.strip()),'font':str((font or {}).get('/BaseFont',''))})
        page.extract_text(visitor_text=visit)
    (workspace.root/'runs/math-role-measurements.json').write_text(json.dumps(runs,ensure_ascii=False,indent=2),encoding='utf-8')
    for token in ('cos','sim','ROLE','BOLD','ital'):
        found=[r for r in runs if token in r['text']]
        assert found,(token,runs)
        assert all('LatinModernMath' in r['font'] for r in found),(token,found)
    assert any('English' in r['text'] and 'Inter' in r['font'] for r in runs)
