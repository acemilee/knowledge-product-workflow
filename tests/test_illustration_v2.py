import json
import struct
import zlib
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from knowledge_product_workflow.diagrams import DiagramInput
from knowledge_product_workflow.illustration_palette import DiagramPresentationInput
from knowledge_product_workflow.illustration_updates import (
    activate_candidate,
    candidate_root,
    is_pending_presentation,
    propose_presentation,
    stage_candidate,
)
from knowledge_product_workflow.illustrations import (
    preflight_illustration,
    read_asset_style,
    record_asset_style,
    style_resources,
)
from tests.test_illustration_style import review, style_workspace


@pytest.mark.parametrize("selected", [False, True])
def test_v2_native_route_checks_brief_selection_before_reserving(tmp_path, monkeypatch, selected):
    from knowledge_product_workflow.illustration_budget import budget_path
    from knowledge_product_workflow.presentation import (
        PublicationUpdateInput,
        apply_publication_update,
        preview_publication_update,
    )

    w = style_workspace(tmp_path)
    preview_publication_update(
        w, PublicationUpdateInput(
            illustration_style_revision=2,
            illustration_backend_preference="host-image" if selected else "vector",
            version_bump="none",
        ), language="zh-CN",
    )
    apply_publication_update(w, "测试用户")
    semantic = DiagramInput(
        diagram_id="route-check", diagram_type="flow", backend="host-image",
        caption="条件分支", reader_conclusion="按既定条件分支",
        nodes=[{"node_id": "a", "label": "输入"}, {"node_id": "b", "label": "输出"}],
        edges=[{"source": "a", "target": "b"}],
        admission={"family": "flow", "reader_job": "理解校验结果的条件分支",
                   "visual_gain": "通过箭头区分输入和不同结果", "information_form": "branching_flow",
                   "evidence_boundary": "仅表达当前示例声明的关系"},
    )
    # Isolate the new route gate; the existing contract gate is tested separately.
    monkeypatch.setattr(
        "knowledge_product_workflow.workflows.authoring.require_current_chapter_contract",
        lambda *args: None,
    )
    message = "参考" if selected else "Brief出版预览"
    with pytest.raises(ValueError, match=message):
        preflight_illustration(w, 1, semantic, supports_references=False, reference_capacity=3)
    assert not budget_path(w).exists()


def png(color, ink_height=0):
    def chunk(kind, value):
        return struct.pack('!I',len(value))+kind+value+struct.pack('!I',zlib.crc32(kind+value)&0xffffffff)
    width,height=1600,900
    data=b''.join(b'\0'+(bytes(color)*10+b'\0\0\0'*20+bytes(color)*(width-30) if 10<=y<10+ink_height else bytes(color)*width) for y in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(data))+chunk(b'IEND',b'')


def v2_workspace(tmp_path, color):
    w=style_workspace(tmp_path)
    profile_path=w.root/'planning/publication-profile.yaml'
    p=yaml.safe_load(profile_path.read_text(encoding='utf-8'));p['illustration_style_revision']=2
    profile_path.write_text(yaml.safe_dump(p,allow_unicode=True),encoding='utf-8')
    payload={'diagram_id': 'fixture','diagram_type': 'flow','caption': '测试','reader_conclusion': '解释分支',
        'nodes': [{'node_id': 'a','label': '甲'},{'node_id': 'b','label': '乙'}],'edges': [{'source': 'a','target': 'b'}],
        'presentation': {'scheme': 'warm-focus','node_colors': {'a':'peach','b':'sand'},'group_colors': {},'concept_nodes': {}}}
    path=w.root/'runs/diagrams/fixture.input.json';path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    image=w.root/'assets/figures/fixture.png';image.write_bytes(png(color))
    manifest=w.root/'runs/diagrams/fixture.json';m=json.loads(manifest.read_text(encoding='utf-8'))
    m.update(png_sha256=sha256(image.read_bytes()).hexdigest(),semantic_input_sha256=sha256(path.read_bytes()).hexdigest())
    manifest.write_text(json.dumps(m),encoding='utf-8')
    return w


@pytest.mark.parametrize('delta,expected',[(0,'passed'),(19,'review'),(31,'blocker')])
def test_flat_color_thresholds_are_bound_to_declared_node(tmp_path,delta,expected):
    w=v2_workspace(tmp_path,(248,229-delta,216))
    value=review().model_dump();value['color_regions']=[{'object_id':'a','box':[50,50,8,8]}]
    record_asset_style(w,type(review()).model_validate(value))
    proof=read_asset_style(w,'fixture')
    assert bool(proof['numeric_blockers']) == (expected=='blocker')
    assert bool(proof['numeric_reviews']) == (expected=='review')


def test_presentation_proposal_keeps_current_input_and_detects_tampering(tmp_path):
    w=v2_workspace(tmp_path,(248,229,216));path=w.root/'runs/diagrams/fixture.input.json';old=path.read_bytes()
    target=DiagramPresentationInput(scheme='cool-structure',node_colors={'a':'blue','b':'lavender'},group_colors={},concept_nodes={})
    candidate=propose_presentation(w,1,'fixture',target)
    assert path.read_bytes()==old
    value=DiagramInput.model_validate_json(candidate.read_text(encoding='utf-8'))
    assert is_pending_presentation(w,value)
    candidate.write_text(candidate.read_text(encoding='utf-8')+' ',encoding='utf-8')
    assert not is_pending_presentation(w,value)


def test_v1_resources_are_not_replaced_by_v2():
    root=Path(__file__).parents[1]/'src/knowledge_product_workflow/templates/illustrations'
    assert sha256((root/'style-anchor.png').read_bytes()).hexdigest()==style_resources(1)['anchor_sha256']
    assert style_resources(2)['anchor_sha256'] != style_resources(1)['anchor_sha256']
    assert '辅助色只限浅绿/浅蓝/浅紫' not in style_resources(2)['visual_language']


def staged_review(w, assessment='passed'):
    target=DiagramPresentationInput(scheme='cool-structure',node_colors={'a':'blue','b':'lavender'},group_colors={},concept_nodes={})
    pending=propose_presentation(w,1,'fixture',target)
    semantic=DiagramInput.model_validate_json(pending.read_bytes())
    temp=w.root/'runs/prepared';temp.mkdir()
    image=temp/'fixture.png';image.write_bytes(png((228,237,245)))
    manifest=temp/'fixture.json'
    value=json.loads((w.root/'runs/diagrams/fixture.json').read_bytes())
    value.update(png_sha256=sha256(image.read_bytes()).hexdigest(),semantic_input_sha256=sha256(pending.read_bytes()).hexdigest())
    manifest.write_text(json.dumps(value),encoding='utf-8')
    call='image-call-testcandidate'
    stage_candidate(w,semantic,call,[(image,w.root/'assets/figures/fixture.png'),(manifest,w.root/'runs/diagrams/fixture.json')])
    value=review(candidate_call_id=call).model_dump()
    value['observations']['typography']['assessment']=assessment
    record_asset_style(w,type(review()).model_validate(value))
    return call


def test_failed_candidate_never_replaces_current_image_input_or_proof(tmp_path):
    w=v2_workspace(tmp_path,(248,229,216));record_asset_style(w,review())
    paths=[w.root/'assets/figures/fixture.png',w.root/'runs/diagrams/fixture.input.json',w.root/'runs/illustrations/asset-checks/fixture.json']
    old=[p.read_bytes() for p in paths]
    call=staged_review(w,'failed')
    with pytest.raises(ValueError,match='failed'):
        activate_candidate(w,'fixture',call)
    assert [p.read_bytes() for p in paths]==old


def test_uncertain_candidate_keeps_history_and_current_proof_pending(tmp_path):
    w=v2_workspace(tmp_path,(248,229,216));record_asset_style(w,review())
    original=(w.root/'assets/figures/fixture.png').read_bytes()
    call=staged_review(w,'uncertain')
    lease=w.root/'runs/orchestration/leases/chapter-01.json'
    lease.parent.mkdir(parents=True,exist_ok=True)
    lease.write_bytes(b'author lease sentinel')
    with pytest.raises(ValueError,match='跨章'):
        activate_candidate(w,'fixture',call,chapter_number=2)
    activate_candidate(w,'fixture',call,chapter_number=1)
    assert (w.root/'runs/illustrations/history'/call/'fixture.png').read_bytes()==original
    assert read_asset_style(w,'fixture')['input']['observations']['typography']['assessment']=='uncertain'
    assert (candidate_root(w,call)/'fixture.png').is_file()
    assert lease.read_bytes()==b'author lease sentinel'


def test_registry_failure_restores_previous_assets_and_proof(tmp_path,monkeypatch):
    w=v2_workspace(tmp_path,(248,229,216));record_asset_style(w,review())
    paths=[w.root/'assets/figures/fixture.png',w.root/'runs/diagrams/fixture.input.json',w.root/'runs/illustrations/asset-checks/fixture.json']
    old=[p.read_bytes() for p in paths]
    call=staged_review(w)
    def fail(*args):
        raise OSError('simulated registry failure')
    monkeypatch.setattr('knowledge_product_workflow.illustration_palette.register_category_colors',fail)
    with pytest.raises(OSError,match='registry'):
        activate_candidate(w,'fixture',call)
    assert [p.read_bytes() for p in paths]==old
    assert read_asset_style(w,'fixture') is not None


def test_cache_reference_bytes_are_bound_but_absolute_path_is_irrelevant(tmp_path):
    from knowledge_product_workflow.illustrations import image_cache_key
    w=v2_workspace(tmp_path,(248,229,216))
    semantic=DiagramInput.model_validate_json((w.root/'runs/diagrams/fixture.input.json').read_bytes())
    refs=[{'role':'palette','sha256':'a'*64,'path':'old/path'}]
    key=image_cache_key(w,semantic,None,references=refs)
    refs[0]['path']='new/path'
    assert image_cache_key(w,semantic,None,references=refs)==key
    refs[0]['sha256']='b'*64
    assert image_cache_key(w,semantic,None,references=refs)!=key


def test_vector_export_uses_true_viewbox_and_preserves_explicit_label_lines(tmp_path):
    from knowledge_product_workflow.d2 import _svg_viewport
    from knowledge_product_workflow.figure_templates import _lines
    svg=tmp_path/'wide.svg'
    svg.write_text('<svg viewBox="0 0 1098 316"></svg>',encoding='utf-8')
    assert _svg_viewport(svg,tight=True)==(1098,316)
    assert _svg_viewport(svg)==(1146,480)
    assert _lines('原始资料\n索引只定位',44)==['原始资料','索引只定位']


def test_repeated_scheme_is_only_information_and_locked_palette_is_exempt(tmp_path):
    from knowledge_product_workflow.illustrations import validate_style_assets
    w=v2_workspace(tmp_path,(248,229,216));record_asset_style(w,review())
    source=w.root/'runs/diagrams/fixture.input.json'
    value=json.loads(source.read_bytes());value['diagram_id']='second'
    (source.parent/'second.input.json').write_text(json.dumps(value),encoding='utf-8')
    body='![a](assets/figures/fixture.pdf) ![b](assets/figures/second.pdf)'
    hint=[f for f in validate_style_assets(w,body) if f.code=='ILLUSTRATION_PALETTE_REPETITION_REVIEW']
    assert len(hint)==1 and hint[0].severity.value=='info'
    profile_path=w.root/'planning/publication-profile.yaml'
    profile=yaml.safe_load(profile_path.read_text(encoding='utf-8'));profile['illustration_palette_policy']='locked';profile['illustration_palette_scheme']='warm-focus'
    profile_path.write_text(yaml.safe_dump(profile,allow_unicode=True),encoding='utf-8')
    assert not any(f.code=='ILLUSTRATION_PALETTE_REPETITION_REVIEW' for f in validate_style_assets(w,body))


def test_vector_presentation_update_binds_new_input_without_changing_source(tmp_path,monkeypatch):
    from knowledge_product_workflow.d2 import D2Resolution
    from knowledge_product_workflow.diagrams import submit_diagram
    from tests.test_diagrams import _approved_workspace, _source_and_input
    w=_approved_workspace(tmp_path,v6=True)
    source,input_path=_source_and_input(w)
    value=json.loads(input_path.read_bytes())
    value['diagram_type']='flow'
    value['presentation']={'scheme':'balanced','node_colors':{'application':'blue','governance':'lavender'},'group_colors':{'业务':'blue','基础':'lavender'},'concept_nodes':{}}
    input_path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    frozen=w.root/'runs/diagrams/agent-layers.input.json'
    frozen.write_bytes(input_path.read_bytes())
    p=w.root/'planning/publication-profile.yaml'
    profile=yaml.safe_load(p.read_text(encoding='utf-8'));profile.update(illustration_style_revision=2,font_policy='fallback')
    p.write_text(yaml.safe_dump(profile,allow_unicode=True),encoding='utf-8')
    monkeypatch.setattr('knowledge_product_workflow.diagrams.resolve_d2',lambda:D2Resolution(True,Path('d2.exe'),'test','0.7',()))
    monkeypatch.setattr('knowledge_product_workflow.diagrams.resolve_diagram_browser',lambda:type('Browser',(),{'available':True,'path':Path('chrome.exe')})())
    def render(command,**kwargs):
        Path(command[-1]).write_bytes(b'diagram')
    monkeypatch.setattr('knowledge_product_workflow.diagrams.subprocess.run',render)
    monkeypatch.setattr('knowledge_product_workflow.diagrams.render_svg_with_browser',lambda browser,svg,pdf,png: (pdf.write_bytes(b'pdf'),png.write_bytes(b'png')))
    submit_diagram(w,1,source,input_path)
    before=source.read_bytes()
    target=DiagramPresentationInput(scheme='balanced',node_colors={'application':'blue','governance':'lavender'},group_colors={'业务':'blue','基础':'lavender'},concept_nodes={},highlight_nodes=['application'])
    candidate=propose_presentation(w,1,'agent-layers',target)
    _,manifest=submit_diagram(w,1,source,candidate)
    assert frozen.read_bytes()==candidate.read_bytes()
    assert manifest.semantic_input_sha256==sha256(frozen.read_bytes()).hexdigest()
    assert source.read_bytes()==before


@pytest.mark.parametrize(
    "observed_pages,clear_dpi",
    [([1, 2], 160), ([1], 160), ([1, 2], 96)],
    ids=["complete", "missing-observation", "low-dpi"],
)
def test_candidate_anchor_requires_explicit_human_confirmation_and_binds_hash(
    tmp_path, monkeypatch, observed_pages, clear_dpi,
):
    from datetime import UTC, datetime

    from knowledge_product_workflow.pdf_visual import record_manual_pdf_visual_review
    from knowledge_product_workflow.visual_audit import (
        VisualAuditReport,
        VisualObservation,
        VisualPageMetric,
    )

    w=v2_workspace(tmp_path,(248,229,216))
    (w.root/'delivery/book.pdf').write_bytes(b'%PDF test')
    (w.root/'delivery/book.qmd').write_text('![fixture](assets/figures/fixture.pdf)',encoding='utf-8')
    preview=w.root/'runs/preview.json';preview.write_text('{}',encoding='utf-8')
    audit = VisualAuditReport(
        created_at=datetime.now(UTC),
        pdf_sha256=sha256((w.root/'delivery/book.pdf').read_bytes()).hexdigest(),
        preview_manifest_sha256=sha256(preview.read_bytes()).hexdigest(),
        profile_sha256=sha256((w.root/'planning/publication-profile.yaml').read_bytes()).hexdigest(),
        observation_input_sha256=sha256(b'synthetic-test-observations').hexdigest(),
        figure_sources_sha256=sha256((w.root/'runs/diagrams/fixture.input.json').read_bytes()).hexdigest(),
        page_count=2,
        pages=[VisualPageMetric(
            page_number=number, page_type='body', width_pt=595.28, height_pt=841.89,
            text_characters=20, high_resolution_image='assets/figures/fixture.png',
            high_resolution_image_sha256=sha256((w.root/'assets/figures/fixture.png').read_bytes()).hexdigest(),
            high_resolution_dpi=clear_dpi,
        ) for number in (1, 2)],
        sampled_pages=[1, 2],
        findings=[],
        observations=[VisualObservation(
            page_number=number, assessment='passed',
            note='合成测试观察，只验证全页审核及母版确认协议。',
        ) for number in observed_pages],
        observation_state='observed',
    )
    (w.root/'runs/visual-audit-report.json').write_text(audit.model_dump_json(),encoding='utf-8')
    monkeypatch.setattr('knowledge_product_workflow.pdf_visual.uses_v6_profile',lambda w:True)
    monkeypatch.setattr('knowledge_product_workflow.pdf_visual.current_pdf_preview',lambda *args:(preview,None))
    monkeypatch.setattr('knowledge_product_workflow.pdf_visual.current_visual_audit',lambda w:audit)
    if observed_pages != [1, 2] or clear_dpi < 144:
        with pytest.raises(ValueError,match='全书|全部.*页'):
            record_manual_pdf_visual_review(w,'测试用户',confirm_style_anchor=True)
        assert not (w.root/'runs/pdf-visual-review.json').exists()
        return
    with pytest.raises(ValueError,match='候选'):
        record_manual_pdf_visual_review(w,'测试用户')
    assert not (w.root/'runs/pdf-visual-review.json').exists()
    path=record_manual_pdf_visual_review(w,'测试用户',confirm_style_anchor=True)
    recorded = json.loads(path.read_bytes())
    assert recorded['style_anchor_sha256']==style_resources(2)['anchor_sha256']
    assert recorded['manual_coverage_confirmed'] is False


def test_grouped_vector_grid_keeps_every_declared_relation_label():
    from knowledge_product_workflow.diagrams import DiagramSpec, render_d2_source
    nodes=[{'node_id':'n'+str(i),'label':'节点','group':str(i)} for i in range(4)]
    p=DiagramPresentationInput(scheme='balanced',node_colors={'n'+str(i):'blue' for i in range(4)},group_colors={str(i):'blue' for i in range(4)},concept_nodes={})
    spec=DiagramSpec(diagram_id='grid',chapter_number=1,chapter_body_sha256='a'*64,contract_sha256='b'*64,diagram_type='flow',direction='right',caption='测试',reader_conclusion='说明关系',nodes=nodes,edges=[{'source':'n0','target':'n2','label':'读取原始字段'}],presentation=p)
    source=render_d2_source(spec)
    assert 'grid-rows: 2' in source and 'horizontal-gap: 280' in source
    assert source.count('label.near: top-left') == 4
    assert '读取原始字段' in source and 'group_1.n0 -> group_3.n2' in source


@pytest.mark.parametrize('ink_height,blocked',[(27,True),(35,False)])
def test_planned_width_checks_visible_ink_height_not_only_role_deviation(tmp_path,ink_height,blocked):
    w=v2_workspace(tmp_path,(255,255,255))
    path=w.root/'runs/diagrams/fixture.input.json';value=json.loads(path.read_bytes())
    value['admission']={'family':'flow','reader_job':'测试读取分支关系和当前边界','visual_gain':'测试显示分支关系和当前边界','information_form':'branching_flow','evidence_boundary':'只使用人工测试的合成字框，不冒充真实中文字体','planned_width_mm':184}
    path.write_text(json.dumps(value),encoding='utf-8')
    image=w.root/'assets/figures/fixture.png';image.write_bytes(png((255,255,255),ink_height))
    manifest=w.root/'runs/diagrams/fixture.json';m=json.loads(manifest.read_bytes());m.update(png_sha256=sha256(image.read_bytes()).hexdigest(),semantic_input_sha256=sha256(path.read_bytes()).hexdigest());manifest.write_text(json.dumps(m),encoding='utf-8')
    record_asset_style(w,review(text_regions=[{'role':'description','box':[8,8,30,ink_height+4]}]))
    proof=read_asset_style(w,'fixture')
    assert ('description:planned-legibility' in proof['numeric_blockers'])==blocked
