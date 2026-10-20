"""引用消费只在本逻辑块判定，不受下一表行/列表项污染。"""
import pytest

from knowledge_product_workflow.developmental import review_dependencies
from knowledge_product_workflow.workspace import Workspace

ROWS = [
    '| 工具 | 超时、空结果、权限拒绝 | 适配契约与录制回放（第 6 章） |',
    '| 上下文 | 遗忘约定、压缩丢条件 | 状态外置与隔离（第 8 章） |',
    '| 判定 | 好坏无可判定定义 | 先定阈值与样本（第 10 章） |',
]


@pytest.mark.parametrize('body', ['\n'.join(ROWS), '- 参见第 6 章\n- 本项定义新的条件。'])
def test_navigation_does_not_consume_the_next_table_row_or_list_item(tmp_path, body):
    dependencies = review_dependencies(Workspace(tmp_path), 1, body)
    assert dependencies and all(d['kind'] == 'contract' for d in dependencies)


@pytest.mark.parametrize('body', [
    '依据第 6 章\n的计算结果选择阈值。',
    '参见第6章。沿用其结果并检查适用条件。',
    '| 依据第6章的结果 | 选择阈值 |',
])
def test_real_consumption_across_soft_lines_or_inside_a_table_is_retained(tmp_path, body):
    assert {'contract', 'result'} == {d['kind'] for d in review_dependencies(Workspace(tmp_path), 1, body)}


def test_code_and_inline_navigation_examples_do_not_create_dependencies(tmp_path):
    body = '```text\n依据第6章的结果选择阈值\n```\n\n`第8章的定义`'
    assert review_dependencies(Workspace(tmp_path), 1, body) == []
