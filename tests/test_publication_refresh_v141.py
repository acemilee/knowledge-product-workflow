"""出版更新只读枚举与人审前终止；无真实工具调用。"""
import json

import pytest

from knowledge_product_workflow import publishing
from knowledge_product_workflow.diagrams import referenced_diagram_manifests
from knowledge_product_workflow.presentation import (
    PublicationUpdateInput,
    apply_publication_update,
    preview_publication_update,
)
from knowledge_product_workflow.workspace import Workspace
from tests.test_publication_updates import _assembled_workspace


def test_unrelated_json_is_not_a_manifest(tmp_path):
    w=Workspace.create(tmp_path,'types')
    directory=w.root/'runs/diagrams';directory.mkdir()
    (directory/'fig-example.metadata.json').write_text(json.dumps({'diagram_id':'fig-example','task_id':'fig-example'}),encoding='utf8')
    assert referenced_diagram_manifests(w)==[]


def test_bad_referenced_canonical_is_located_before_profile_commit(tmp_path):
    w=_assembled_workspace(tmp_path)
    chapter=w.root/'chapters/chapter-01.md'
    chapter.write_text(chapter.read_text(encoding='utf8')+'\n![受控图](assets/figures/example.pdf)\n',encoding='utf8')
    directory=w.root/'runs/diagrams';directory.mkdir(exist_ok=True)
    (directory/'example.json').write_text('{}',encoding='utf8')
    preview_publication_update(w,PublicationUpdateInput(cover_label='新封面',version_bump='patch'),language='zh-CN')
    profile=w.root/'planning/publication-profile.yaml';before=profile.read_bytes()
    with pytest.raises(ValueError,match=r'example.json.*DiagramManifest'):
        apply_publication_update(w,'测试夹具')
    assert profile.read_bytes()==before


def test_cover_only_pdf_refresh_stops_before_final_validation(tmp_path,monkeypatch):
    w=Workspace.create(tmp_path,'pdf-refresh')
    (w.root/'delivery/book.md').write_text('owned',encoding='utf8')
    monkeypatch.setattr('knowledge_product_workflow.diagrams.refresh_diagram_theme',lambda _:pytest.fail('cover must not refresh diagrams'))
    monkeypatch.setattr(publishing,'assemble_book',lambda _:w.root/'delivery/book.md')
    monkeypatch.setattr(publishing,'_constraints_for_workspace',lambda _:None)
    monkeypatch.setattr(publishing,'render_pdf',lambda *_:w.root/'delivery/book.pdf')
    monkeypatch.setattr('knowledge_product_workflow.workflows.validation.run_delivery_validation',lambda _:pytest.fail('no final validation before human'))
    result=publishing.refresh_publication_delivery(w,changed_fields=['cover_title_lines','edition_label'])
    assert result['pending']=='pdf_visual_audit' and result['manifest'] is None


def test_stale_manifest_routes_a_real_publish_work_order(tmp_path,monkeypatch):
    from knowledge_product_workflow import runtime
    from tests.test_chapter_content_plan import _approved_workspace
    w=_approved_workspace(tmp_path)
    def status(_):
        return runtime._status(stage='delivery_manifest_stale',blocked_by=['清单绑定旧PDF'],
            recommended_skill='kp-publisher',next_command='',artifacts={},declared_claim_ids=[],events=[])
    monkeypatch.setattr(runtime,'inspect_workflow',status)
    order=runtime.read_orchestration_work_order(w,runtime.create_orchestration_work_order(w))
    assert order.allowed_commands==['publish']
    assert runtime.capture_orchestration_action(w,'publish')==order.work_order_id
    old=order.model_copy(update={'allowed_commands':[],'actions':[]})
    path=w.root/'runs/orchestration/work-orders'/f'{order.work_order_id}.json'
    path.write_text(old.model_dump_json(),encoding='utf8')
    current=runtime.read_orchestration_work_order(w,runtime.create_orchestration_work_order(w,
        refresh_reason='隔离夹具复现旧空动作清单工单，按当前受控映射刷新'))
    assert current.work_order_id!=old.work_order_id and current.allowed_commands==['publish']
    assert runtime.read_orchestration_work_order(w,old.work_order_id).lifecycle.value=='superseded'
