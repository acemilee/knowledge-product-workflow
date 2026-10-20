"""本书引用在源稿期校验；外部资料/代码的编号不是本书图号。"""
import pytest

from knowledge_product_workflow.content_quality import scan_markdown_content


@pytest.mark.parametrize('text',['图 1-1 把两个环并排放在一起。','请参见图 2。','图7-1 用状态迁移表达恢复。',
    '图1-1是输入与输出的关系。','图1-1表示输入与输出。','本书图2-1中的箭头表示结果。',
    '原著图1说明旧例。本书图2给出新例。'])
def test_manual_number_is_rejected_before_pdf(text):
    assert any(f.code=='MANUAL_FIGURE_REFERENCE' for f in scan_markdown_content(text,'chapter.md'))


@pytest.mark.parametrize('text',['双环分工图把两个环并排放在一起。','原著图 2-1 展示原论文的试验。',
    '```text\n图 1-1 把两个环并排放在一起。\n```','~~~text\n图 1-1 把两个环并排放在一起。\n~~~',
    '````text\n```\n图 1-1 把两个环并排放在一起。\n````','引用格式示例：`见图 2`。','> 如图 3 所示，是原始引用。'])
def test_semantic_names_and_explicit_external_examples_are_preserved(text):
    assert not any(f.code=='MANUAL_FIGURE_REFERENCE' for f in scan_markdown_content(text,'chapter.md'))


def test_sixteen_chapter_nine_figure_projection_never_rewrites_sources(tmp_path):
    """Synthetic assembly only; not a semantic-review or rendered-PDF claim."""
    import base64
    import json
    from hashlib import sha256

    from pypdf import PdfWriter

    from knowledge_product_workflow.diagrams import DiagramManifest
    from knowledge_product_workflow.presentation import (
        PresentationOverrides,
        load_or_create_publication_profile,
    )
    from knowledge_product_workflow.publishing import assemble_book
    from knowledge_product_workflow.workspace import Workspace
    w=Workspace.create(tmp_path,'sixteen-projection')
    load_or_create_publication_profile(w,language='zh-CN',
        overrides=PresentationOverrides(cover_summary_zh='合成十六章九图汇编投影夹具，不代表语义或真实出版验收。'))
    figures=w.root/'assets/figures';figures.mkdir(parents=True)
    manifests=w.root/'runs/diagrams';manifests.mkdir()
    before={}
    for n in range(1,17):
        body=f'# {n}. 输入情境{n}\n\n本章围绕独立的输入情境说明核对任务。\n'
        if n<=9:
            identifier=f'case-{n}'
            body+=f'\n输入关系示意帮助读者核对来源与结果。\n\n![输入关系示意{n}](assets/figures/{identifier}.pdf)\n'
            pdf=figures/f'{identifier}.pdf';writer=PdfWriter();writer.add_blank_page(595,842);writer.write(pdf)
            png=figures/f'{identifier}.png';png.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=='))
            manifest=DiagramManifest(diagram_id=identifier,chapter_number=n,chapter_body_sha256=sha256(body.encode()).hexdigest(),
                contract_sha256='0'*64,spec_sha256='0'*64,svg_sha256='0'*64,pdf_sha256=sha256(pdf.read_bytes()).hexdigest(),
                png_sha256=sha256(png.read_bytes()).hexdigest(),caption=f'输入关系示意{n}',node_count=2,max_label_characters=4)
            (manifests/f'{identifier}.json').write_text(manifest.model_dump_json(),encoding='utf8')
        path=w.root/f'chapters/chapter-{n:02d}.md'
        path.write_text('---\nbook_id: sixteen-projection\nartifact_type: chapter\nstatus: in_production\nversion: 1\ndepends_on: []\n---\n'+body,encoding='utf8')
        before[path]=path.read_bytes()
        assert not any(f.code=='MANUAL_FIGURE_REFERENCE' for f in scan_markdown_content(body,path.name))
    assemble_book(w)
    for name in ('book.md','book.qmd'):
        projection=(w.root/'delivery'/name).read_text(encoding='utf8')
        assert projection.count('![输入关系示意')==9
        assert all(f'# {n}. 输入情境{n}' in projection for n in range(1,17))
    assert all(path.read_bytes()==body for path,body in before.items())
    events=[json.loads(line)['event_type'] for line in (w.root/'runs/events.jsonl').read_text(encoding='utf8').splitlines()]
    assert events.count('book_assembled')==1 and not any('amend' in event for event in events)
