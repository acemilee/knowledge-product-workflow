import pytest

from knowledge_product_workflow.cli import _assembly_failure_hint


@pytest.mark.parametrize("error,expected,unexpected", [
    (FileNotFoundError("缺README"), "缺少输入文件", "关闭文件占用"),
    (PermissionError("不可写"), "权限拒绝", "锁冲突"),
    (ValueError("坏锚点"), "输入或契约无效", "文件占用"),
    (OSError("未知IO"), "OSError", "关闭文件占用"),
])
def test_classification_without_fabricated_receipts(error, expected, unexpected):
    message = _assembly_failure_hint(error, None)
    assert expected in message and unexpected not in message
    assert "未创建" in message and "原操作=" not in message


def test_shared_file_and_existing_operation_are_different_diagnostics():
    error = OSError("sharing violation")
    error.winerror = 32
    message = _assembly_failure_hint(error, "op-original")
    assert "锁冲突" in message and "原操作=op-original" in message and "禁止重发" in message
