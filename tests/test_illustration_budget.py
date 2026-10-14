import subprocess
import sys
from decimal import Decimal

import pytest

from knowledge_product_workflow.illustration_budget import (
    freeze_image_budget,
    load_image_budget,
    record_image_call,
    reserve_image,
)
from knowledge_product_workflow.workspace import Workspace


def reserve(workspace, diagram_id="a", chapter=1, semantic="a", key="a", **kwargs):
    return reserve_image(workspace, diagram_id=diagram_id, chapter_number=chapter,
                         semantic_sha256=semantic, cache_key=key, prompt_sha256="a" * 64, **kwargs)


def finish(workspace, call, suffix="a"):
    image = workspace.root / "runs" / f"{suffix}.png"
    image.write_bytes(b"fixture-" + suffix.encode())
    record_image_call(workspace, call.call_id, state="dispatched")
    record_image_call(workspace, call.call_id, state="completed", image_path=image)


def test_slot_cache_and_alias_cannot_create_new_chapter_quota(tmp_path):
    workspace = Workspace.create(tmp_path, "book")
    freeze_image_budget(workspace, approved_by="测试用户")
    call, cached = reserve(workspace)
    assert not cached
    finish(workspace, call)
    same, cached = reserve(workspace, diagram_id="renamed")
    assert cached and same.call_id == call.call_id
    with pytest.raises(ValueError, match="章节归属"):
        reserve(workspace, diagram_id="renamed", chapter=2)
    with pytest.raises(ValueError, match="1张"):
        reserve(workspace, diagram_id="b", semantic="b", key="b")
    assert len(load_image_budget(workspace).slots) == 1


def test_failed_calls_and_brand_change_keep_two_attempts(tmp_path):
    workspace = Workspace.create(tmp_path, "book")
    freeze_image_budget(workspace, approved_by="测试用户")
    first, _ = reserve(workspace)
    record_image_call(workspace, first.call_id, state="dispatched")
    record_image_call(workspace, first.call_id, state="failed")
    second, _ = reserve(workspace, key="purple")
    finish(workspace, second)
    with pytest.raises(ValueError, match="两次"):
        reserve(workspace, key="different-backend", diagram_id="alias")
    assert len(load_image_budget(workspace).slots[0].calls) == 2


def test_unknown_cost_serializes_requests_until_explicit_settlement(tmp_path):
    workspace = Workspace.create(tmp_path, "book")
    freeze_image_budget(workspace, approved_by="测试用户", money_limit=Decimal(1), currency="USD", price_version="fixture")
    call, _ = reserve(workspace, reserved_cost=Decimal("0.6"))
    record_image_call(workspace, call.call_id, state="dispatched")
    record_image_call(workspace, call.call_id, state="unknown")
    with pytest.raises(ValueError, match="未结算"):
        reserve(workspace, diagram_id="b", chapter=2, semantic="b", reserved_cost=Decimal("0.5"))
    assert load_image_budget(workspace).reserved_money == Decimal("0.6")
    record_image_call(workspace, call.call_id, state="failed", actual_cost=Decimal("0.6"), usage_source="actual")
    with pytest.raises(ValueError, match="货币预算"):
        reserve(workspace, diagram_id="b", chapter=2, semantic="b", reserved_cost=Decimal("0.5"))


def test_book_slot_limit_includes_shared_preparation(tmp_path):
    workspace = Workspace.create(tmp_path, "book")
    freeze_image_budget(workspace, approved_by="测试用户")
    for number in range(6):
        call, _ = reserve(workspace, diagram_id=f"fig-{number}", chapter=None if number == 0 else number,
                          semantic=str(number), key=str(number))
        finish(workspace, call, str(number))
    with pytest.raises(ValueError, match="全书"):
        reserve(workspace, diagram_id="seventh", chapter=7, semantic="seventh")


def test_replayed_dispatch_and_cancelled_reservations_do_not_hide_calls(tmp_path):
    workspace = Workspace.create(tmp_path, "book")
    freeze_image_budget(workspace, approved_by="测试用户")
    call, _ = reserve(workspace)
    with pytest.raises(ValueError, match="未结算"):
        reserve(workspace, diagram_id="b", chapter=2, semantic="b")
    record_image_call(workspace, call.call_id, state="cancelled")
    retry, _ = reserve(workspace)
    record_image_call(workspace, retry.call_id, state="dispatched")
    with pytest.raises(ValueError, match="已消费"):
        record_image_call(workspace, retry.call_id, state="dispatched")
    record_image_call(workspace, retry.call_id, state="failed")
    freeze_image_budget(workspace, approved_by="测试用户")
    assert len(load_image_budget(workspace).slots[0].calls) == 2


def test_other_process_cannot_reserve_during_atomic_lock(tmp_path):
    from knowledge_product_workflow.illustration_budget import _locked
    workspace = Workspace.create(tmp_path, "locked-book")
    freeze_image_budget(workspace, approved_by="测试用户")
    code = (
        "from pathlib import Path; from knowledge_product_workflow.workspace import Workspace; "
        "from knowledge_product_workflow.illustration_budget import reserve_image; "
        "reserve_image(Workspace(Path(__import__('sys').argv[1])), diagram_id='a',chapter_number=1,"
        "semantic_sha256='a',cache_key='a',prompt_sha256='a'*64)"
    )
    with _locked(workspace):
        result = subprocess.run([sys.executable, "-c", code, str(workspace.root)],
                                capture_output=True, text=True, encoding="utf-8", timeout=10, check=False)
    assert result.returncode != 0 and "未发送请求" in result.stderr
    assert not load_image_budget(workspace).slots
    assert not reserve(workspace)[1]


def test_budget_reduction_preserves_chapter_consumption(tmp_path):
    workspace = Workspace.create(tmp_path, "custom-book")
    freeze_image_budget(workspace, approved_by="测试用户", mode="custom", chapter_slots=2)
    for name in ("a", "b"):
        call, _ = reserve(workspace, diagram_id=name, semantic=name, key=name)
        finish(workspace, call, name)
    before = (workspace.root / "runs/illustrations/budget.json").read_bytes()
    with pytest.raises(ValueError, match="章节槽位"):
        freeze_image_budget(workspace, approved_by="测试用户")
    assert (workspace.root / "runs/illustrations/budget.json").read_bytes() == before


def test_invalid_settlement_keeps_ledger_and_unfinished_call_blocks_delivery(tmp_path):
    from knowledge_product_workflow.illustration_budget import image_budget_findings
    workspace = Workspace.create(tmp_path, "settlement-book")
    with pytest.raises(ValueError, match="确认身份"):
        freeze_image_budget(workspace, approved_by=" ")
    freeze_image_budget(workspace, approved_by="测试用户")
    call, _ = reserve(workspace)
    record_image_call(workspace, call.call_id, state="dispatched")
    before = (workspace.root / "runs/illustrations/budget.json").read_bytes()
    with pytest.raises(ValueError, match="不能为负"):
        record_image_call(workspace, call.call_id, state="failed", actual_cost=Decimal(-1), usage_source="actual")
    assert (workspace.root / "runs/illustrations/budget.json").read_bytes() == before
    assert image_budget_findings(workspace)[0].code == "ILLUSTRATION_CALL_UNFINISHED"
    record_image_call(workspace, call.call_id, state="failed")
    assert not image_budget_findings(workspace)
