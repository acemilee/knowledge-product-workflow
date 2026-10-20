"""新执行者在首次模板中取得真实约束，不以试错找字段。"""
import json

from knowledge_product_workflow.content_plan import create_content_plan_template
from knowledge_product_workflow.developmental import TeachingCheck, create_structure_review_template
from tests.test_structure_review_v133 import _fixture


def test_template_names_required_units_and_uses_real_teaching_schema(tmp_path):
    w,source,_=_fixture(tmp_path)
    template=create_structure_review_template(w,1,source)
    assert template['required_unit_ids']==[u['unit_id'] for u in template['units'] if u['required']]
    assert template['teaching_check_schema']==TeachingCheck.model_json_schema()


def test_plan_context_includes_current_payload_schema(tmp_path):
    from knowledge_product_workflow.content_plan import ContentPlanSection
    w,_,_=_fixture(tmp_path)
    _,context=create_content_plan_template(w,1)
    schema=json.loads(context.read_text(encoding='utf8'))['section_input_schema']
    assert schema==ContentPlanSection.model_json_schema()
    assert 'artifact_kind' in schema['properties']['asset_decision']['description']


def test_first_plan_json_returns_the_current_submission_schema_in_one_command(tmp_path):
    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app
    from knowledge_product_workflow.content_plan import content_plan_schema
    w,_,_=_fixture(tmp_path)
    result=CliRunner().invoke(app,['content-plan-template','1','--workspace',str(w.root),'--json'])
    assert result.exit_code==0,result.output
    data=json.loads(result.output)
    assert data['submission_schema']==content_plan_schema()
    assert data['context_data']['chapter_number']==1


def test_visual_submission_schema_requires_current_input_and_png():
    import json

    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app
    result=CliRunner().invoke(app,['pdf-observation-schema'])
    assert result.exit_code==0,result.output
    schema=json.loads(result.output)
    assert {'observation_input_sha256','page_image_sha256'} <= set(schema['items']['required'])
    assert all(schema['items']['properties'][name]['type']=='string'
        for name in ('observation_input_sha256','page_image_sha256'))


def test_group_capability_matches_the_actual_renderer(tmp_path):
    from knowledge_product_workflow.diagrams import create_diagram_template
    from tests.test_diagram_preflight_o11_v134 import _fixture as svg_fixture
    from tests.test_diagrams import _approved_workspace
    legacy=_approved_workspace(tmp_path/'legacy')
    source=legacy.root/'runs/source.md';source.write_text('# 1. 架构基础\n',encoding='utf8')
    _,context=create_diagram_template(legacy,1,source,'legacy-flow')
    constraints=json.loads(context.read_text(encoding='utf8'))['rendering_constraints']
    assert not constraints['structured_svg'] and constraints['group_support']
    current,source,_,_=svg_fixture(tmp_path/'current')
    _,context=create_diagram_template(current,1,source,'dependency-map')
    constraints=json.loads(context.read_text(encoding='utf8'))['rendering_constraints']
    assert constraints['structured_svg'] and not constraints['group_support']
