"""报告由当前状态派生；新工件/事件改变后旧报告不得冒充当前。"""
import hashlib
import json

from pypdf import PdfWriter

from knowledge_product_workflow.run_snapshot import current_run_snapshot, write_run_snapshot
from knowledge_product_workflow.workspace import Workspace


def test_report_detects_changed_artifacts_without_rewriting_history(tmp_path):
    workspace = Workspace.create(tmp_path, "snapshot")
    json_path, markdown = write_run_snapshot(workspace, host="Codex")
    snapshot = current_run_snapshot(workspace)
    assert snapshot is not None
    assert snapshot["stage"] == "brief_missing"
    assert snapshot["identity"]["host"]["status"] == "declared"
    assert snapshot["account_usage"] is None
    assert "本人" in markdown.read_text(encoding="utf-8")
    original = json_path.read_bytes()
    (workspace.root / "delivery/book.md").write_text("# 新工件\n", encoding="utf-8")
    assert current_run_snapshot(workspace) is None
    assert json_path.read_bytes() == original
    write_run_snapshot(workspace)
    assert current_run_snapshot(workspace) is not None


def test_raw_failure_and_current_render_facts_do_not_turn_claim_into_pass(tmp_path):
    workspace=Workspace.create(tmp_path,'facts')
    pdf=workspace.root/'delivery/book.pdf'
    writer=PdfWriter();writer.add_blank_page(595,842)
    with pdf.open('wb') as stream:writer.write(stream)
    sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
    workspace.append_event('orchestration_work_order_failed',{'transition':'validate-delivery','reason':'门拒绝','work_order_id':'wo-failed'})
    workspace.append_event('pdf_render_failed',{'reason':'quarto unavailable'})
    workspace.append_event('pdf_rendered',{'sha256':sha,'page_count':1})
    workspace.append_event('validation_completed',{'report_id':'pretend','publishable':True})
    workspace.append_event('pdf_visual_observations_recorded',{'count':99,'report_sha256':'pretend'})
    (workspace.root/'commands.jsonl').write_text(json.dumps({'at':'fixed','args':['validate-delivery'],'exit_code':1,'stderr':'拒绝'})+'\n')
    write_run_snapshot(workspace)
    snapshot=current_run_snapshot(workspace);facts=snapshot['workflow_facts']
    assert facts['failed_transitions'][0]['transition']=='validate-delivery'
    assert facts['failed_transitions'][0]['reason']=='门拒绝'
    assert facts['rendering']['recorded_success_count']==1
    assert facts['rendering']['current_pdf_event_bound'] is True
    assert facts['delivery_validation']['rejected_call_count']==1
    assert facts['delivery_validation']['current_report_state']=='unknown'
    assert facts['visual_coverage']['verified_viewed_pages']==[]
    assert snapshot['operations']['validate']['effect_state']!='applied'


def test_observation_declaration_without_read_proof_is_not_verified_coverage(tmp_path):
    workspace=Workspace.create(tmp_path,'declared')
    report={'page_count':3,'sampled_pages':[1,2], 'observation_state':'observed',
            'observations':[{'page_number':1,'assessment':'passed','note':'自述已看'},
                            {'page_number':2,'assessment':'uncertain','note':'未证实'}]}
    (workspace.root/'runs/visual-audit-report.json').write_text(json.dumps(report))
    workspace.append_event('pdf_visual_observations_recorded',{'count':3,'report_sha256':'invalid'})
    write_run_snapshot(workspace)
    facts=current_run_snapshot(workspace)['workflow_facts']['visual_coverage']
    assert facts['declared_observed_pages']==[1,2]
    assert facts['verified_viewed_pages']==[]
    assert facts['uncovered_pages']==[1,2,3]
    assert facts['binding_state']=='not_current'


def test_new_command_log_invalidates_snapshot(tmp_path):
    workspace=Workspace.create(tmp_path,'commands')
    write_run_snapshot(workspace)
    (workspace.root/'commands.jsonl').write_text('{"args":["status"],"exit_code":0}\n')
    assert current_run_snapshot(workspace) is None


def test_old_schema_and_malformed_event_sources_cannot_claim_current_facts(tmp_path):
    workspace=Workspace.create(tmp_path,'old-schema')
    with (workspace.root/'runs/events.jsonl').open('a') as stream:
        stream.write('[]\n')
    output,_=write_run_snapshot(workspace)
    assert current_run_snapshot(workspace)['workflow_facts']['visual_coverage']['verified_viewed_pages']==[]
    snapshot=json.loads(output.read_text(encoding='utf-8'));snapshot['schema_version']=1
    output.write_text(json.dumps(snapshot),encoding='utf-8')
    assert current_run_snapshot(workspace) is None
