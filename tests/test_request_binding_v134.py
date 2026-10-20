"""新托管入口保留原意；旧库工作区明确兼容。"""
import json

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.runtime import initialize_new_orchestration
from knowledge_product_workflow.workflows.product_brief import (
    BriefSemanticFields,
    create_product_brief,
)
from knowledge_product_workflow.workspace import Workspace


def test_new_managed_brief_refuses_missing_original_before_materialization(tmp_path):
    workspace = Workspace.create(tmp_path, 'managed-request', current_policy=True)
    initialize_new_orchestration(workspace)
    with pytest.raises(ValueError, match='原始读者'):
        create_product_brief(workspace, 'RAG 入门')
    assert not (workspace.root / 'planning/product-brief.md').exists()


def test_new_cli_preview_refuses_missing_original_without_writing_preview(tmp_path):
    workspace = Workspace.create(tmp_path, 'preview-request', current_policy=True)
    initialize_new_orchestration(workspace)
    data = workspace.root / 'runs/input.json'
    data.write_text(json.dumps({'topic':'RAG 入门', 'target_audience':'AI Agent 初学者', 'audience_level':'beginner'}), encoding='utf-8')
    result = CliRunner().invoke(app, ['brief','--workspace',str(workspace.root),'--from',str(data),'--no-interactive'])
    assert result.exit_code != 0
    assert '原始读者' in result.output
    assert not (workspace.root / 'runs/brief-preview.json').exists()


def test_original_request_captured_without_adding_it_to_reader_document(tmp_path):
    from knowledge_product_workflow.workflows.product_brief import read_request_contract
    workspace = Workspace.create(tmp_path, 'captured-request', current_policy=True)
    initialize_new_orchestration(workspace)
    original = '生成中文 RAG 入门教程，用三条固定语料说明检索与回答边界。'
    path = create_product_brief(workspace, 'RAG 入门', semantics=BriefSemanticFields(reader_request=original))
    contract = read_request_contract(workspace)
    assert contract['requirements']['reader_request']['value'] == original
    assert contract['reader_request_sha256']
    assert original not in path.read_text(encoding='utf-8')


def test_legacy_library_call_remains_compatible(tmp_path):
    workspace = Workspace.create(tmp_path, 'legacy-request')
    assert create_product_brief(workspace, '旧库知识教程').is_file()


def test_default_orange_cannot_be_classified_as_explicit_reader_request():
    from knowledge_product_workflow.cli import StructuredBriefInput, _brief_requirement_sources
    from knowledge_product_workflow.detail_constraints import DetailConstraints
    data = StructuredBriefInput(topic='RAG 入门', target_audience='初学者', audience_level='beginner',
        reader_request='生成中文 RAG 入门教程。', brand_color_intent='橙色基调')
    sources = _brief_requirement_sources(data, DetailConstraints(), interactive=False, brand_color_explicit=True)
    assert sources['brand_color_intent'] == 'default'
    data.reader_request += '品牌采用紫色。'
    data.brand_color_intent = '紫色基调'
    assert _brief_requirement_sources(data, DetailConstraints(), interactive=False)['brand_color_intent'] == 'user_explicit'
