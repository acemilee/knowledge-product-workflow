"""旧Brief缺省兼容不应掩盖显式坏数据。"""
import pytest

from knowledge_product_workflow.workflows.product_brief import parse_brief_semantics


def test_legacy_brief_without_positioning_keeps_default_semantics():
    parsed = parse_brief_semantics('# 旧书\n\n## 目标读者\n\n工程师\n')
    assert parsed.product_positioning is None
    assert parsed.teaching_focus is None


def test_explicit_empty_positioning_remains_invalid():
    with pytest.raises(ValueError, match='产品定位'):
        parse_brief_semantics('# 新书\n\n## 产品定位\n\n## 目标读者\n\n工程师\n')


def test_legacy_default_does_not_hide_invalid_explicit_teaching_focus():
    with pytest.raises(ValueError):
        parse_brief_semantics('# 旧书\n\n## 教学呈现\n\n```yaml\nteaching_focus: unknown\n```\n')
