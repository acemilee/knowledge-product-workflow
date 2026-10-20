"""合成PDF和自有PNG桩核验观察派生；不调用真实截图或TeX工具。"""
import json
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from knowledge_product_workflow import visual_audit as audit
from knowledge_product_workflow.publishing import create_quarto_source
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace
from tests.visual_support import bind_fixture_observations


def _png(value):
    def chunk(tag, data):
        return struct.pack('!I',len(data))+tag+data+struct.pack('!I',zlib.crc32(tag+data)&0xffffffff)
    header = struct.pack('!IIBBBBB',1350,1900,8,2,0,0,0)
    pixels = (b'\0'+bytes([value])*1350*3)*1900
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',header)+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')


def fixture(tmp_path, monkeypatch):
    w=Workspace.create(tmp_path,'page-reuse')
    create_product_brief(w,'观察复用测试')
    md=w.root/'delivery/book.md';md.write_text('# 观察复用\n',encoding='utf8')
    create_quarto_source(w,md)
    pdf=w.root/'delivery/book.pdf'
    writer=PdfWriter()
    for _ in range(45):
        writer.add_blank_page(595.28,841.89)
    writer.write(pdf)
    preview=w.root/'runs/owned-preview.json';preview.write_text('{}',encoding='utf8')
    profile=w.root/'planning/publication-profile.yaml'
    monkeypatch.setattr(audit,'_bindings',lambda _: (pdf,preview,profile))
    monkeypatch.setattr(audit,'resolve_pdftoppm',lambda: Path('owned-rasterizer'))
    images={0:_png(0),1:_png(60)}
    changed=set()
    def image(path, number):
        path.write_bytes(images[int(number in changed)])
    def run(argv,**kw):
        image(Path(argv[-1]).with_suffix('.png'),int(argv[argv.index('-f')+1]))
        return SimpleNamespace(returncode=0,stderr='')
    monkeypatch.setattr(audit.subprocess,'run',run)
    monkeypatch.setattr('knowledge_product_workflow.pdf_preview._render_png_via_pipe',
        lambda _tool,_pdf,path,number,_dpi: image(path,number))
    return w,pdf,changed


def test_new_pdf_reuses_six_identical_page_only_observations_but_no_human_approval(tmp_path,monkeypatch):
    w,pdf,changed=fixture(tmp_path,monkeypatch)
    old=audit.audit_pdf_visual(w,sample_limit=45)
    numbers=[1,2,7,11,24,40]
    audit.record_visual_observations(w,bind_fixture_observations(w, [audit.VisualObservation(page_number=n,assessment='passed',
        scope='page',note='合成夹具单页可见性观察，不是产品实际视觉判断。') for n in numbers]))
    old_bytes=audit.audit_path(w).read_bytes()
    changed.update({43,44})
    with pdf.open('ab') as f:f.write(b'\n% owned new revision\n')
    assert audit.current_visual_audit(w) is None
    new=audit.audit_pdf_visual(w,sample_limit=45)
    assert new.pdf_sha256!=old.pdf_sha256
    assert [o.page_number for o in new.observations]==numbers
    assert all(o.reuse_origin['scope']=='page_visible_only' for o in new.observations)
    assert not (w.root/'runs/pdf-visual-review.json').exists()
    assert any(p.read_bytes()==old_bytes for p in (w.root/'runs/history/visual-audit').glob('*.json'))


@pytest.mark.parametrize('reason',['different_png','context_scope','failed','rule_change','historical_tamper','note_tamper'])
def test_changed_or_unproven_observation_is_not_promoted(tmp_path,monkeypatch,reason):
    w,pdf,changed=fixture(tmp_path,monkeypatch)
    audit.audit_pdf_visual(w,sample_limit=1)
    audit.record_visual_observations(w,bind_fixture_observations(w, [audit.VisualObservation(page_number=1,
        assessment='failed' if reason=='failed' else 'passed',
        scope='context' if reason=='context_scope' else 'page',note='合成边界观察，不能当实际全书人审。')]))
    if reason in {'rule_change','historical_tamper','note_tamper'}:
        data=json.loads(audit.audit_path(w).read_text(encoding='utf8'))
        if reason=='rule_change':data['rule_set']='older-unbound-rule'
        elif reason=='note_tamper':data['observations'][0]['note']='外部更改旧观察不能获得派生权'
        else:data['pages'][0]['high_resolution_image_sha256']='0'*64
        audit.audit_path(w).write_text(json.dumps(data),encoding='utf8')
    if reason=='different_png':changed.add(1)
    with pdf.open('ab') as f:f.write(b'\n% owned revision\n')
    new=audit.audit_pdf_visual(w,sample_limit=1)
    assert new.observations==[]


def test_client_cannot_supply_computed_reuse_provenance(tmp_path):
    w=Workspace.create(tmp_path,'forged-reuse')
    with pytest.raises(ValueError,match='不能手填'):
        audit.record_visual_observations(w,[audit.VisualObservation(page_number=1,
            assessment='passed',note='手填来源不授复用权',reuse_origin={'scope':'page_visible_only'})])


@pytest.mark.parametrize('field',['observation_input_sha256','page_image_sha256'])
def test_new_observation_rejects_stale_input_or_png_without_mutating_report(tmp_path,monkeypatch,field):
    w,_,_=fixture(tmp_path,monkeypatch)
    report=audit.audit_pdf_visual(w,sample_limit=1)
    before=audit.audit_path(w).read_bytes()
    payload={'page_number':1,'assessment':'passed','note':'旧输入不得移绑当前页面。',
        'observation_input_sha256':report.observation_input_sha256,
        'page_image_sha256':report.pages[0].high_resolution_image_sha256}
    payload[field]='0'*64
    with pytest.raises(ValueError,match='过期'):
        audit.record_visual_observations(w,[audit.VisualObservation(**payload)])
    assert audit.audit_path(w).read_bytes()==before


def test_library_writer_also_rejects_new_unbound_observation(tmp_path,monkeypatch):
    w,_,_=fixture(tmp_path,monkeypatch)
    audit.audit_pdf_visual(w,sample_limit=1)
    before=audit.audit_path(w).read_bytes()
    with pytest.raises(ValueError,match='新观察须携'):
        audit.record_visual_observations(w,[audit.VisualObservation(page_number=1,
            assessment='passed',note='不绑定旧输入不能提交新观察。')])
    assert audit.audit_path(w).read_bytes()==before
