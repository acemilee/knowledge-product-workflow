"""冻结交付QA复用及陈旧拒绝；软件夹具不代表产品质量批准。"""
import json

import pytest

from knowledge_product_workflow import publishing, quality
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def fixture(tmp_path):
    w = Workspace.create(tmp_path, 'qa-reuse')
    create_product_brief(w, '交付QA机制夹具', DetailConstraints(delivery_format='markdown'))
    md = w.root / 'delivery/book.md'
    md.write_text('# 固定软件夹具\n\n不声明实际教学质量。\n', encoding='utf8')
    path, report = quality.write_quality_report(w, 'delivery', [])
    return w, md, path, report


def test_publish_reuses_current_delivery_report_without_running_validation(tmp_path, monkeypatch):
    w, md, path, report = fixture(tmp_path)
    before = path.read_bytes()
    def unexpected(_):
        pytest.fail('publish不能隐式重跑完整终检')
    monkeypatch.setattr('knowledge_product_workflow.workflows.validation.run_delivery_validation', unexpected)
    manifest = publishing.create_delivery_manifest(w, md, None)
    assert manifest.is_file()
    assert path.read_bytes() == before
    assert json.loads(path.read_text(encoding='utf8'))['report_id'] == report.report_id


@pytest.mark.parametrize('change', ['body', 'same_mtime', 'asset_added', 'policy', 'stage', 'missing', 'blocked'])
def test_publish_never_repairs_or_accepts_an_invalid_report(tmp_path, monkeypatch, change):
    import os
    w, md, path, _ = fixture(tmp_path)
    before = path.read_bytes()
    if change in {'body', 'same_mtime'}:
        stamp = md.stat()
        md.write_text('# 外部修改\n', encoding='utf8')
        if change == 'same_mtime':
            os.utime(md, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    elif change == 'asset_added':
        (w.root / 'assets/new.txt').write_text('new', encoding='utf8')
    elif change == 'missing':
        path.unlink()
    else:
        data = json.loads(before)
        if change == 'policy':
            data['policy_revision'] = 'old'
        elif change == 'stage':
            data['validation_stage'] = 'quality'
        else:
            data['findings'] = [{'code':'FIXTURE_BLOCK', 'severity':'blocker', 'location':'fixture', 'message':'固定阻断'}]
        path.write_text(json.dumps(data), encoding='utf8')
    def unexpected(_):
        pytest.fail('失效报告也不允许publish自行重算')
    monkeypatch.setattr('knowledge_product_workflow.workflows.validation.run_delivery_validation', unexpected)
    with pytest.raises((ValueError, FileNotFoundError), match='validate-delivery|质量报告'):
        publishing.create_delivery_manifest(w, md, None)
    assert not (w.root / 'delivery/delivery-manifest.yaml').exists()


def test_current_report_cannot_override_its_blocked_archive(tmp_path):
    w,md,path,_=fixture(tmp_path)
    quality.write_quality_report(w,'delivery',[quality.QualityFinding(
        code='FIXTURE_BLOCK',severity='blocker',location='fixture',message='不可移除的失败')])
    data=json.loads(path.read_text(encoding='utf8'));data['findings']=[]
    path.write_text(json.dumps(data),encoding='utf8')
    with pytest.raises(ValueError,match='归档'):
        publishing.create_delivery_manifest(w,md,None)
    assert not (w.root/'delivery/delivery-manifest.yaml').exists()


@pytest.mark.parametrize('drift',['bytes','membership'])
def test_input_drift_after_validity_rejects_before_manifest_write(tmp_path,monkeypatch,drift):
    from knowledge_product_workflow.verification_context import SnapshotChangedError
    w,md,_,_=fixture(tmp_path)
    asset=w.root/'assets/example.txt';asset.write_text('original',encoding='utf8')
    quality.write_quality_report(w,'delivery',[])
    original=quality.quality_report_validity
    def change_after_check(*args,**kw):
        result=original(*args,**kw)
        if drift=='bytes':
            asset.write_text('external',encoding='utf8')
        else:
            (asset.parent/'new.txt').write_text('external',encoding='utf8')
        return result
    monkeypatch.setattr(quality,'quality_report_validity',change_after_check)
    with pytest.raises(SnapshotChangedError):
        publishing.create_delivery_manifest(w,md,None)
    assert not (w.root/'delivery/delivery-manifest.yaml').exists()
