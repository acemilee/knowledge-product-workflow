"""出版写入中断保留原事务，不重做正文或编译。"""
import json

import pytest

from knowledge_product_workflow import workspace_transactions as transactions
from knowledge_product_workflow.operation_receipts import (
    operation_scope,
    operation_status,
    recover_operation,
)
from knowledge_product_workflow.publishing import assemble_book
from tests.test_delivery_preflight_v141 import _book


def test_locked_pdf_keeps_old_manuscript_and_original_operation_is_recoverable(tmp_path, monkeypatch):
    workspace = _book(tmp_path, "独立索引")
    assemble_book(workspace)
    pdf = workspace.root / 'delivery/book.pdf'
    pdf.write_bytes(b'%PDF-owned-original')
    book = workspace.root / 'delivery/book.md'
    book.write_text('# 私有过期稿\n', encoding='utf8')
    before = book.read_bytes()
    original = transactions._v_unlink
    def locked(path, **kwargs):
        if path == pdf:
            raise PermissionError(32, 'owned locked PDF')
        return original(path, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(transactions, '_v_unlink', locked)
        with pytest.raises(PermissionError), operation_scope(workspace, action='assemble', chapter_number=0, operation_id='op-owned-assembly'):
            assemble_book(workspace)
    assert book.read_bytes() == before
    assert pdf.read_bytes() == b'%PDF-owned-original'
    status = operation_status(workspace, 'op-owned-assembly')
    assert status['transaction_id'].startswith('tx-')
    assert status['phase'] != 'completed'
    tx_id = status['transaction_id']
    result = recover_operation(workspace, 'op-owned-assembly')
    assert result.phase == 'completed' and result.transaction_id == tx_id
    assert book.read_bytes() != before
    assert not pdf.exists()
    events = (workspace.root / 'runs/events.jsonl').read_bytes()
    assert recover_operation(workspace, 'op-owned-assembly') == result
    assert events == (workspace.root / 'runs/events.jsonl').read_bytes()
    assert any(p.read_bytes() == b'%PDF-owned-original' for p in (workspace.root / 'runs/delivery-history').rglob('book.pdf'))


@pytest.mark.parametrize('boundary', ['before_replace:delivery/book.md', 'after_replace:delivery/book.md',
    'after_replace:delivery/book.qmd', 'before_completed'])
def test_interrupted_assembly_has_no_false_completion_and_resumes_same_transaction(tmp_path, monkeypatch, boundary):
    workspace = _book(tmp_path, "独立索引")
    def interrupt(phase):
        if phase == boundary:
            raise RuntimeError('owned simulated interruption')
    with monkeypatch.context() as patch:
        patch.setattr(transactions, '_fault', interrupt)
        with pytest.raises(RuntimeError), operation_scope(workspace, action='assemble', chapter_number=0, operation_id='op-owned-interrupt'):
            assemble_book(workspace)
    status = operation_status(workspace, 'op-owned-interrupt')
    assert status['phase'] != 'completed'
    assert status['transaction_id']
    result = recover_operation(workspace, 'op-owned-interrupt')
    assert result.phase == 'completed'
    manifest = json.loads((workspace.root / 'delivery/book-input-manifest.json').read_text(encoding='utf8'))
    assert len(manifest['inputs']) > 2


def test_external_change_prevents_recovery_overwriting_it(tmp_path, monkeypatch):
    workspace = _book(tmp_path, "独立索引")
    with monkeypatch.context() as patch:
        patch.setattr(transactions, '_fault', lambda phase: (_ for _ in ()).throw(RuntimeError('owned'))
            if phase == 'before_replace:delivery/book.md' else None)
        with pytest.raises(RuntimeError), operation_scope(workspace, action='assemble', chapter_number=0, operation_id='op-owned-conflict'):
            assemble_book(workspace)
    book = workspace.root / 'delivery/book.md'
    book.write_text('another actor changed it', encoding='utf8')
    with pytest.raises(ValueError, match='不能覆盖其他修改'):
        recover_operation(workspace, 'op-owned-conflict')
    assert book.read_text(encoding='utf8') == 'another actor changed it'


def test_owned_windows_sharing_lock_is_recoverable_without_closing_other_processes(tmp_path):
    import ctypes
    import os
    from ctypes import wintypes
    if os.name != 'nt':
        pytest.skip('Windows sharing violation requires Windows')
    w=_book(tmp_path,'独立索引')
    assemble_book(w)
    pdf=w.root/'delivery/book.pdf';pdf.write_bytes(b'%PDF-owned-sharing-lock')
    book=w.root/'delivery/book.md';book.write_text('# 旧派生稿',encoding='utf8')
    before=book.read_bytes()
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,
                                wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateFileW('\\\\?\\'+str(pdf.resolve()),0x80000000,1,None,3,0x80,None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        with pytest.raises(PermissionError), operation_scope(w,action='assemble',chapter_number=0,operation_id='op-owned-win-lock'):
            assemble_book(w)
        assert book.read_bytes()==before and pdf.read_bytes()==b'%PDF-owned-sharing-lock'
    finally:
        kernel.CloseHandle(handle)
    assert recover_operation(w,'op-owned-win-lock').phase=='completed'
