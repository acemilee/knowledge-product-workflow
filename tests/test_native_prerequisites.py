"""核对测试前提门，不声明synthetic工具是原生验收。"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import native_test_prerequisites


def request_for(marker, tmp_path):
    return SimpleNamespace(
        node=SimpleNamespace(get_closest_marker=lambda name: name == marker),
        getfixturevalue=lambda name: tmp_path,
    )


def test_unmarked_checks_never_require_native_tools(tmp_path, monkeypatch):
    monkeypatch.setattr('shutil.which', lambda _: pytest.fail('普通测试不得探测原生工具'))
    native_test_prerequisites.__wrapped__(request_for(None, tmp_path))


def test_missing_rasterizer_is_explicitly_unexecuted(tmp_path, monkeypatch):
    monkeypatch.setattr('knowledge_product_workflow.pdf_preview.resolve_pdftoppm', lambda: None)
    with pytest.raises(pytest.skip.Exception, match='未执行'):
        native_test_prerequisites.__wrapped__(request_for('requires_poppler', tmp_path))


def test_available_rasterizer_does_not_skip_original_assertions(tmp_path, monkeypatch):
    monkeypatch.setattr('knowledge_product_workflow.pdf_preview.resolve_pdftoppm', lambda: Path(__file__))
    native_test_prerequisites.__wrapped__(request_for('requires_poppler', tmp_path))


def test_missing_pdf_toolchain_is_explicitly_unexecuted(tmp_path, monkeypatch):
    monkeypatch.setattr('shutil.which', lambda _: None)
    with pytest.raises(pytest.skip.Exception, match='前提缺失'):
        native_test_prerequisites.__wrapped__(request_for('requires_pdf_tools', tmp_path))


def test_unavailable_fonts_are_not_recorded_as_native_success(tmp_path, monkeypatch):
    monkeypatch.setattr('shutil.which', lambda _: 'synthetic-tool')
    monkeypatch.setattr('knowledge_product_workflow.pdf_fonts.probe_preferred_fonts',
                        lambda _: {'font': {'available': False}})
    with pytest.raises(pytest.skip.Exception, match='不代表通过'):
        native_test_prerequisites.__wrapped__(request_for('requires_pdf_tools', tmp_path))
