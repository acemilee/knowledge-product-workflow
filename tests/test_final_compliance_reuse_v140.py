"""一次完整基础校验派生普通/最终结果，模式选择不能复制昂贵基础工作。"""
import json

import pytest

from knowledge_product_workflow import chapter_compliance as compliance
from knowledge_product_workflow.developmental import (
    developmental_report_path,
    require_current_developmental_report,
)
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.verification_context import verification_scope
from tests.test_final_uncertain_gate_v140 import _book, _review


@pytest.mark.parametrize('final_first', [False, True])
def test_two_modes_share_one_complete_base_validation(tmp_path, monkeypatch, final_first):
    workspace, chapter, _ = _book(tmp_path)
    original = compliance.validate_chapter_submission
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(compliance, 'validate_chapter_submission', counted)
    with verification_scope(workspace):
        result = {mode: compliance.validate_current_chapter_compliance(workspace, chapter, final_review=mode)
            for mode in (final_first, not final_first, final_first)}
    assert result[False] == []
    assert any('uncertain' in finding.message for finding in result[True])
    assert len(calls) == 1


def test_unknown_review_cannot_hide_stale_developmental_binding(tmp_path):
    workspace, chapter, _ = _book(tmp_path)
    path = developmental_report_path(workspace, 1)
    report = json.loads(path.read_text(encoding='utf8'))
    report['structure_review_sha256'] = '0'*64
    path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf8')
    body = parse_markdown_artifact(chapter.read_text(encoding='utf8'))[1]
    contract = workspace.root / 'chapters/chapter-01-contract.md'
    for final in (False, True):
        with pytest.raises(ValueError, match='未绑定当前结构审核'):
            require_current_developmental_report(workspace, 1, body, contract, final_review=final)
        assert any('未绑定当前结构审核' in finding.message for finding in
            compliance.validate_current_chapter_compliance(workspace, chapter, final_review=final))


def test_different_actions_do_not_share_base_cache(tmp_path, monkeypatch):
    workspace, chapter, _ = _book(tmp_path)
    original = compliance.validate_chapter_submission
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(compliance, 'validate_chapter_submission', counted)
    for final in (False, True):
        compliance.validate_current_chapter_compliance(workspace, chapter, final_review=final)
    assert len(calls) == 2


def test_returned_mode_lists_do_not_poison_cached_outcome(tmp_path):
    workspace, chapter, _ = _book(tmp_path)
    with verification_scope(workspace):
        result = compliance.validate_current_chapter_compliance(workspace, chapter, final_review=True)
        result.clear()
        assert compliance.validate_current_chapter_compliance(workspace, chapter) == []
        assert compliance.validate_current_chapter_compliance(workspace, chapter, final_review=True)
    _review(workspace, chapter, uncertain=False)
    assert compliance.validate_current_chapter_compliance(workspace, chapter, final_review=True) == []


def test_controlled_review_update_refreshes_both_modes_in_same_context(tmp_path):
    workspace, chapter, _ = _book(tmp_path)
    with verification_scope(workspace):
        assert compliance.validate_current_chapter_compliance(workspace, chapter) == []
        assert compliance.validate_current_chapter_compliance(workspace, chapter, final_review=True)
        _review(workspace, chapter, uncertain=False)
        assert compliance.validate_current_chapter_compliance(workspace, chapter) == []
        assert compliance.validate_current_chapter_compliance(workspace, chapter, final_review=True) == []
