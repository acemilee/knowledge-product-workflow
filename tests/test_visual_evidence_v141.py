"""新视觉输入的声明/绑定门，不声称机器证明主观判断为真。"""
from datetime import UTC, datetime

import pytest

from knowledge_product_workflow import visual_audit as audit
from knowledge_product_workflow.workspace import Workspace


def fixture(tmp_path, monkeypatch):
    w = Workspace.create(tmp_path, 'visual-evidence')
    report = audit.VisualAuditReport(created_at=datetime.now(UTC), evidence_revision=1,
        pdf_sha256='0'*64, preview_manifest_sha256='1'*64, profile_sha256='2'*64,
        observation_input_sha256='3'*64, figure_sources_sha256='4'*64,
        page_count=2, sampled_pages=[1,2], sample_limit=2,
        pages=[audit.VisualPageMetric(page_number=n,page_type='body',width_pt=595,height_pt=842,
            text_characters=60,high_resolution_image=f'runs/page-{n}.png',
            high_resolution_image_sha256=str(n)*64,high_resolution_dpi=160) for n in (1,2)],findings=[])
    audit.audit_path(w).write_text(report.model_dump_json(),encoding='utf8')
    monkeypatch.setattr(audit,'current_visual_audit',lambda _:report)
    return w,report


def observation(**changes):
    return audit.VisualObservation(page_number=1,assessment='passed',scope='page',
        note='软件夹具：页首输入表与页尾核对说明分开，未发现裁切。',
        observation_input_sha256='3'*64,page_image_sha256='1'*64,**changes)


def test_hashes_and_generic_pass_do_not_prove_clear_page_review(tmp_path,monkeypatch):
    w,_=fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='细查|依据|维度'):
        audit.record_visual_observations(w,[observation()])


@pytest.mark.parametrize('view_kind',['overview','machine'])
def test_overview_and_machine_metrics_cannot_fill_passed_coverage(tmp_path,monkeypatch,view_kind):
    w,_=fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='细查|概览|机器'):
        audit.record_visual_observations(w,[observation(view_kind=view_kind,
            evidence_anchor='页首输入表与页尾核对说明',checked_dimensions=['legibility','clipping','layout'])])


def test_context_observation_requires_exact_related_page_bindings(tmp_path,monkeypatch):
    w,report=fixture(tmp_path,monkeypatch)
    o=observation(view_kind='clear_page',evidence_anchor='固定输入表与下一页续表',
        checked_dimensions=['legibility','clipping','layout'])
    o.scope='context'
    with pytest.raises(ValueError,match='相邻|跨页|context'):
        audit.record_visual_observations(w,[o])
    o.context_page_sha256={1:'1'*64,2:'0'*64}
    with pytest.raises(ValueError,match='过期|身份'):
        audit.record_visual_observations(w,[o])
    o.context_page_sha256={1:'1'*64,2:'2'*64}
    assert audit.record_visual_observations(w,[o]).observations
    assert not audit.full_visual_observation_coverage(report)


def test_clear_png_decode_is_once_per_verified_command_but_external_change_is_rejected(tmp_path,monkeypatch):
    from hashlib import sha256

    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        verification_scope,
    )
    from tests.test_visual_observation_reuse_v141 import _png
    w,report=fixture(tmp_path,monkeypatch)
    p=w.root/'runs/page-1.png';p.write_bytes(_png(30))
    page=report.pages[0];page.high_resolution_image_sha256=sha256(p.read_bytes()).hexdigest()
    calls=[];original=audit.iter_png_rgb_rows
    def decode(*args,**kw):
        calls.append(1)
        return original(*args,**kw)
    monkeypatch.setattr(audit,'iter_png_rgb_rows',decode)
    with verification_scope(w):
        assert audit._clear_image_valid(w,page)
        assert audit._clear_image_valid(w,page)
    assert len(calls)==1
    with pytest.raises(SnapshotChangedError), verification_scope(w):
        assert audit._clear_image_valid(w,page)
        p.write_bytes(_png(60))
    assert len(calls)==2


def test_confirmed_object_pushback_cannot_be_signed_passed(tmp_path,monkeypatch):
    w,report=fixture(tmp_path,monkeypatch)
    report.pages[0].layout_context_required_pages=[1,2]
    o=observation(view_kind='clear_page',evidence_anchor='表后大白与后页图示',
        checked_dimensions=['legibility','clipping','layout'],context_page_sha256={1:'1'*64,2:'2'*64},
        flow_cause='object_pushback')
    o.scope='context'
    with pytest.raises(ValueError,match='不可通过'):
        audit.record_visual_observations(w,[o])
