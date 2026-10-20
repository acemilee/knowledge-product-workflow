"""软件机制夹具；不代表用户实际看过合成PDF。"""
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from knowledge_product_workflow import delivery_preflight as preflight
from knowledge_product_workflow import pdf_visual
from knowledge_product_workflow import user_decisions as decisions
from knowledge_product_workflow.presentation import load_or_create_publication_profile
from knowledge_product_workflow.publishing import create_quarto_source
from knowledge_product_workflow.quality import QualityFinding
from knowledge_product_workflow.workflows.product_brief import (
    BriefSemanticFields,
    create_product_brief,
)
from knowledge_product_workflow.workspace import Workspace


def fixture(tmp_path, monkeypatch, *, full=False):
    w = Workspace.create(tmp_path, 'scope', current_policy=True)
    create_product_brief(w, '批准范围测试夹具', semantics=BriefSemanticFields(
        reader_request='仅使用合成PDF验证人工批准范围，不作为真实用户批准或语义审稿。'))
    load_or_create_publication_profile(w,language='zh-CN')
    markdown=w.root/'delivery/book.md';markdown.write_text('# 批准范围机制夹具\n',encoding='utf8')
    create_quarto_source(w,markdown)
    assert pdf_visual.uses_v6_profile(w)
    pdf = w.root/'delivery/book.pdf'
    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    writer.write(pdf)
    manifest = w.root/'runs/pdf-preview/preview-manifest.json'
    manifest.parent.mkdir(exist_ok=True)
    manifest.write_text('{}', encoding='utf8')
    audit_path = w.root/'runs/visual-audit-report.json';audit_path.write_text('{}', encoding='utf8')
    report = SimpleNamespace(page_count=82,evidence_revision=0,findings=[],sampled_pages=list(range(1,83)),
        observations=[SimpleNamespace(page_number=i,assessment='passed') for i in range(1,83 if full else 7)],
        pages=[SimpleNamespace(high_resolution_image='owned.png',high_resolution_dpi=160) for _ in range(82)])
    preview = lambda *_: (manifest, SimpleNamespace(pages=[]))
    monkeypatch.setattr('knowledge_product_workflow.pdf_preview.current_pdf_preview',preview)
    monkeypatch.setattr(pdf_visual,'current_pdf_preview',preview)
    monkeypatch.setattr('knowledge_product_workflow.visual_audit.current_visual_audit',lambda _:report)
    monkeypatch.setattr(pdf_visual,'current_visual_audit',lambda _:report)
    monkeypatch.setattr(pdf_visual,'_current_style_anchor',lambda _:None)
    monkeypatch.setattr('knowledge_product_workflow.workflows.validation.collect_delivery_findings',
        lambda _:[QualityFinding(code=c,severity='blocker',location='delivery/book.pdf',message=c) for c in
            ['PDF_FULL_VISUAL_COVERAGE_REQUIRED','PDF_DIAGRAM_MANUAL_VISUAL_REVIEW_REQUIRED']])
    preflight.run_delivery_preflight(w)
    return w, report


def approve(w, scopes=None, confirmed=None):
    request = decisions.request_user_decision(w,decisions.UserDecisionTarget.PDF,scopes=scopes)
    decisions.record_user_decision(w,decisions.UserDecisionTarget.PDF,decisions.UserResponseDecision.APPROVE,
        approved_by='隔离测试夹具，非用户批准',response='测试范围答复',confirmed_scope=confirmed)
    return request


def test_preflight_allows_explicit_human_alternative_without_authorizing_it(tmp_path,monkeypatch):
    w,_=fixture(tmp_path,monkeypatch)
    assert preflight.current_delivery_preflight(w).ready_for_human_review
    approve(w)
    with pytest.raises(ValueError,match='范围'):
        pdf_visual.record_manual_pdf_visual_review(w,'测试夹具',confirm_manual_coverage=True)
    assert not (w.root/'runs/pdf-visual-review.json').exists()


def test_explicit_requested_and_confirmed_full_coverage_does_not_stale_preflight(tmp_path,monkeypatch):
    w,_=fixture(tmp_path,monkeypatch)
    before=preflight.current_delivery_preflight(w)
    scopes=['visual','content','manual_full_coverage']
    request=approve(w,scopes,scopes)
    assert '82页' in request.question
    assert preflight.current_delivery_preflight(w)==before
    pdf_visual.record_manual_pdf_visual_review(w,'测试夹具',confirm_manual_coverage=True,confirm_content=True)
    assert not pdf_visual.manual_visual_review_needed(w)


def test_model_full_coverage_plus_visual_approval_is_a_legal_other_path(tmp_path,monkeypatch):
    w,_=fixture(tmp_path,monkeypatch,full=True)
    approve(w)
    pdf_visual.record_manual_pdf_visual_review(w,'测试夹具')
    assert not pdf_visual.manual_visual_review_needed(w)


def test_scope_cannot_exceed_the_displayed_question_and_changed_audit_rejects_reply(tmp_path,monkeypatch):
    w,_=fixture(tmp_path,monkeypatch)
    decisions.request_user_decision(w,decisions.UserDecisionTarget.PDF)
    with pytest.raises(ValueError,match='超出'):
        decisions.record_user_decision(w,decisions.UserDecisionTarget.PDF,decisions.UserResponseDecision.APPROVE,
            approved_by='测试夹具',response='同意',confirmed_scope=['visual','manual_full_coverage'])
    (w.root/'runs/visual-audit-report.json').write_text('{"changed":true}',encoding='utf8')
    with pytest.raises(ValueError,match='更新'):
        decisions.record_user_decision(w,decisions.UserDecisionTarget.PDF,decisions.UserResponseDecision.APPROVE,
            approved_by='测试夹具',response='同意')


def test_human_full_coverage_cannot_override_a_failed_page(tmp_path,monkeypatch):
    w,report=fixture(tmp_path,monkeypatch)
    approve(w,['visual','manual_full_coverage'],['visual','manual_full_coverage'])
    report.observations[0].assessment='failed'
    with pytest.raises(ValueError,match='失败'):
        pdf_visual.record_manual_pdf_visual_review(w,'测试夹具',confirm_manual_coverage=True)


def test_legacy_without_state_and_corrupt_publication_record_do_not_crash(tmp_path):
    from datetime import UTC, datetime
    legacy=tmp_path/'legacy'
    for directory in ('planning','research','chapters','delivery','runs'):
        (legacy/directory).mkdir(parents=True,exist_ok=True)
    w=Workspace.open(legacy)
    review=pdf_visual.PdfVisualReview(reviewed_at=datetime.now(UTC),reviewed_by='夹具',pdf_sha256='0'*64,result='passed')
    assert pdf_visual._decision_is_current(w,review)
    with pytest.raises(FileNotFoundError):
        pdf_visual.record_manual_pdf_visual_review(w,'夹具')
    current=Workspace.create(tmp_path,'corrupt',current_policy=True)
    (current.root/'delivery/delivery-manifest.yaml').write_text('artifacts: [\n',encoding='utf8')
    assert not pdf_visual._decision_is_current(current,review)


def test_unpublished_v6_legacy_cannot_issue_new_unscoped_approval(tmp_path,monkeypatch):
    import json
    from datetime import UTC, datetime
    w,_=fixture(tmp_path,monkeypatch,full=True)
    path=w.root/'runs/orchestration/state.json';state=json.loads(path.read_text(encoding='utf8'))
    state['request_capture_revision']=0;path.write_text(json.dumps(state),encoding='utf8')
    old=pdf_visual.PdfVisualReview(reviewed_at=datetime.now(UTC),reviewed_by='旧夹具',pdf_sha256='0'*64,result='passed')
    assert not pdf_visual._decision_is_current(w,old)
    with pytest.raises(ValueError,match='用户确认'):
        pdf_visual.record_manual_pdf_visual_review(w,'旧夹具')
    from hashlib import sha256
    old.pdf_sha256=sha256((w.root/'delivery/book.pdf').read_bytes()).hexdigest()
    (w.root/'delivery/delivery-manifest.yaml').write_text(json.dumps({'artifacts':[
        {'path':'delivery/book.pdf','sha256':old.pdf_sha256}]}),encoding='utf8')
    assert pdf_visual._decision_is_current(w,old), '已发布同版历史不回溯改签'


def test_missing_decision_rejects_before_any_deep_cli_status(tmp_path,monkeypatch):
    from typer.testing import CliRunner

    from knowledge_product_workflow import cli
    w=Workspace.create(tmp_path,'fast-reject',current_policy=True)
    monkeypatch.setattr(cli,'inspect_workflow',lambda _:pytest.fail('缺决定不可深扫'))
    result=CliRunner().invoke(cli.app,['record-pdf-visual-review','--workspace',str(w.root),'--response','同意'])
    assert result.exit_code==1 and 'request-user-decision' in result.output


def test_scope_mismatch_is_a_cheap_rejection_and_next_action_is_complete(tmp_path,monkeypatch):
    from knowledge_product_workflow import runtime
    w,_=fixture(tmp_path,monkeypatch,full=True)
    status=runtime.WorkflowStatus(stage='pdf_manual_review_pending',recommended_skill='kp-publisher',next_command='复核')
    assert runtime._fallback_next_actions(w,status)[0].command=='request-user-decision'
    decisions.request_user_decision(w,decisions.UserDecisionTarget.PDF)
    assert runtime._fallback_next_actions(w,status)[0].command=='record-user-decision'
    decisions.record_user_decision(w,decisions.UserDecisionTarget.PDF,decisions.UserResponseDecision.APPROVE,
        approved_by='软件夹具',response='只确认视觉',confirmed_scope=['visual'])
    assert runtime._fallback_next_actions(w,status)[0].command=='record-pdf-visual-review'
    monkeypatch.setattr(decisions,'_artifact_snapshot',lambda *_:pytest.fail('scope错不读取产物'))
    with pytest.raises(ValueError,match='scope'):
        decisions.precheck_pdf_scope(w,{'visual','content'},'只确认视觉')


def test_next_action_preserves_confirmed_flags_and_routes_stale_decision_to_request(tmp_path,monkeypatch):
    from knowledge_product_workflow import runtime
    w,_=fixture(tmp_path,monkeypatch)
    approve(w,['visual','content','manual_full_coverage'],['visual','content','manual_full_coverage'])
    status=runtime.WorkflowStatus(stage='pdf_manual_review_pending',recommended_skill='kp-publisher',next_command='复核')
    argv=runtime._fallback_next_actions(w,status)[0].argv
    assert '--confirm-content' in argv and '--confirm-manual-coverage' in argv
    (w.root/'runs/visual-audit-report.json').write_text('{"changed":true}',encoding='utf8')
    action=runtime._fallback_next_actions(w,status)[0]
    assert action.command=='request-user-decision' and 'manual_full_coverage' in action.argv


def test_pdf_decision_changes_the_control_input_manifest(tmp_path,monkeypatch):
    from knowledge_product_workflow import runtime
    w,_=fixture(tmp_path,monkeypatch,full=True)
    before=runtime.inspect_workflow(w)
    assert 'runs/interaction/pdf.json' in before.artifacts
    digest=runtime._manifest_sha256(runtime._input_manifest(w,before))
    approve(w)
    after=runtime.inspect_workflow(w)
    assert runtime._manifest_sha256(runtime._input_manifest(w,after))!=digest


def test_pdf_cli_controlled_updates_replace_the_previous_order(tmp_path,monkeypatch):
    from typer.testing import CliRunner

    from knowledge_product_workflow import cli, runtime
    w,_=fixture(tmp_path,monkeypatch,full=True)
    runner=CliRunner()
    runtime.create_orchestration_work_order(w,refresh_reason='软件夹具设置完成，冻结实际已有输入，不授产品批准')
    requested=runner.invoke(cli.app,['request-user-decision','pdf','--workspace',str(w.root),'--scope','visual'])
    assert requested.exit_code==0,requested.output
    before=[o.work_order_id for _,o in runtime._read_all_orchestration_work_orders(w) if runtime._is_live(o)]
    recorded=runner.invoke(cli.app,['record-user-decision','pdf','--workspace',str(w.root),
        '--decision','approve','--response','软件夹具仅确认视觉','--scope','visual','--by','软件夹具'])
    assert recorded.exit_code==0,recorded.output
    after=[o.work_order_id for _,o in runtime._read_all_orchestration_work_orders(w) if runtime._is_live(o)]
    assert len(before)==len(after)==1 and before!=after
