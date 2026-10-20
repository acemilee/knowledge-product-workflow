"""工具链分型测试使用自有桩，不调用真实TeX或截图工具。"""
import json

import pytest

from knowledge_product_workflow import publishing
from knowledge_product_workflow.pdf_fonts import PREFERRED_FONTS
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


@pytest.mark.parametrize('status', ['missing', 'timeout', 'compile_failed', 'probe_unavailable'])
def test_font_failure_classification_stops_before_real_compilation(tmp_path, monkeypatch, status):
    w = Workspace.create(tmp_path, 'font-status')
    create_product_brief(w, '字体状态测试')
    md = w.root / 'delivery/book.md'
    md.write_text('# 字体状态\n\n独立内容\n', encoding='utf8')
    qmd = publishing.create_quarto_source(w, md)
    pdf = w.root / 'delivery/book.pdf'
    pdf.write_bytes(b'old PDF')
    monkeypatch.setattr('knowledge_product_workflow.pdf_fonts.probe_preferred_fonts', lambda _: {
        k:{'font':v,'available':False,'status':status} for k,v in PREFERRED_FONTS.items()})
    monkeypatch.setattr(publishing, 'resolve_quarto', lambda: pytest.fail('failed font gate must not compile'))
    with pytest.raises(RuntimeError):
        publishing.render_pdf(qmd, pdf.parent, w)
    events = [json.loads(line) for line in (w.root / 'runs/events.jsonl').read_text(encoding='utf8').splitlines()]
    failed = [e['payload'] for e in events if e['event_type']=='pdf_render_failed'][-1]
    assert failed['reason'] == ('preferred_fonts_missing' if status=='missing' else 'preferred_fonts_probe_unavailable')
    assert pdf.read_bytes() == b'old PDF'


@pytest.mark.parametrize('mode,expected', [('missing_exe','probe_unavailable'), ('oserror','probe_unavailable'),
    ('timeout','timeout'), ('bad_compile','compile_failed'), ('incomplete','compile_failed'),
    ('available','available'), ('missing','missing')])
def test_probe_preserves_all_five_states_with_one_owned_call(tmp_path, monkeypatch, mode, expected):
    import subprocess
    from types import SimpleNamespace

    from knowledge_product_workflow import pdf_fonts
    calls = []
    monkeypatch.setattr(pdf_fonts.shutil, 'which', lambda _: None if mode=='missing_exe' else 'owned-xelatex')
    def run(argv, **kw):
        calls.append(argv)
        assert kw['timeout']==8
        if mode=='oserror':
            raise OSError('owned probe unavailable')
        if mode=='timeout':
            raise subprocess.TimeoutExpired('owned',8)
        text = '' if mode=='incomplete' else '\n'.join(f'KP-FONT-{i}={"YES" if mode=="available" else "NO"}' for i in range(4))
        (kw['cwd']/'probe.log').write_text(text, encoding='utf8')
        return SimpleNamespace(returncode=1 if mode=='bad_compile' else 0)
    monkeypatch.setattr(pdf_fonts.subprocess, 'run', run)
    result = pdf_fonts.probe_preferred_fonts(tmp_path)
    assert {v['status'] for v in result.values()}=={expected}
    assert len(calls)==(0 if mode=='missing_exe' else 1)
    assert not list(tmp_path.iterdir())


def test_stage_receipt_records_failure_without_claiming_approval(tmp_path):
    w = Workspace.create(tmp_path, 'owned-stages')
    def failed():
        raise RuntimeError('owned error')
    with pytest.raises(RuntimeError):
        publishing._publication_call(w, 'owned_test', failed)
    events = [json.loads(line) for line in (w.root/'runs/events.jsonl').read_text(encoding='utf8').splitlines()]
    assert events[-1]['payload']['outcome']=='raised'
    assert events[-1]['payload']['elapsed_seconds']>=0
    assert not (w.root/'delivery/book.pdf').exists()
