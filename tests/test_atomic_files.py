import os

import pytest

from knowledge_product_workflow.atomic_files import replace_file


def test_short_lived_lock_keeps_atomic_result_and_does_not_mutate_before_retry(tmp_path, monkeypatch):
    source, target = tmp_path/"new", tmp_path/"current"
    source.write_bytes(b"new");target.write_bytes(b"previous")
    original = os.replace
    calls = []
    def locked_once(before, after):
        calls.append((before, after))
        if len(calls) == 1:
            assert target.read_bytes() == b"previous"
            raise PermissionError("temporary reader lock")
        original(before, after)
    monkeypatch.setattr("knowledge_product_workflow.atomic_files.os.replace", locked_once)
    monkeypatch.setattr("knowledge_product_workflow.atomic_files.time.sleep", lambda duration:None)
    if os.name != "nt":
        pytest.skip("仅Windows重试短时文件锁")
    replace_file(source, target)
    assert len(calls) == 2 and target.read_bytes() == b"new" and not source.exists()


def test_persistent_lock_is_bounded_and_leaves_both_versions_intact(tmp_path, monkeypatch):
    source, target = tmp_path/"new", tmp_path/"current"
    source.write_bytes(b"new");target.write_bytes(b"previous")
    calls = []
    def always_locked(*args):
        calls.append(args)
        raise PermissionError("persistent lock")
    monkeypatch.setattr("knowledge_product_workflow.atomic_files.os.replace", always_locked)
    monkeypatch.setattr("knowledge_product_workflow.atomic_files.time.sleep", lambda duration:None)
    with pytest.raises(PermissionError):
        replace_file(source, target)
    assert len(calls) == (4 if os.name == "nt" else 1)
    assert target.read_bytes() == b"previous" and source.read_bytes() == b"new"
