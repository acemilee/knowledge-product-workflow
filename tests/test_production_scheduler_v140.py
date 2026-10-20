import pytest

from knowledge_product_workflow.production_tasks import (
    ProductionHostCapabilities,
    ProductionPolicy,
    ProductionRun,
    next_dispatches,
)


@pytest.mark.parametrize("limit", [4, 5, 6])
def test_all_roles_share_total_pool_and_review_backlog_gets_slots(limit):
    run = ProductionRun.diagnostic_run(21, ProductionPolicy(requested_parallelism=limit))
    first = next_dispatches(run)
    assert len(first) == limit
    for task in first[:2]:
        run.complete_edit(task.task_id, "fixture", input_manifest_sha256=task.input_manifest_sha256)
    new = next_dispatches(run)
    assert len(new) == 2
    assert all(task.role == "reviewer" for task in new)
    assert run.active_count == limit


def test_same_chapter_cannot_have_two_editors_and_unknown_does_not_release_slot():
    run = ProductionRun.diagnostic_run(3, ProductionPolicy(requested_parallelism=1))
    first = next_dispatches(run)[0]
    run.mark_unknown(first.task_id)
    assert next_dispatches(run) == []
    assert run.active_count == 1
    with pytest.raises(ValueError, match="版本"):
        run.complete_edit(first.task_id, "bad", input_manifest_sha256="0"*64)


def test_hand_filled_capabilities_and_nested_agents_cannot_bypass_total_limit():
    capabilities = ProductionHostCapabilities(host_kind="codex-app-server", max_parallelism=64,
        production_verified=True, permissions_verified=True,
        parent_child_receipts_verified=True, release_verified=True)
    assert not capabilities.admits_live_parallelism()
    run = ProductionRun.diagnostic_run(20, ProductionPolicy(requested_parallelism=4))
    run.nested_active_ids.update({"nested-one", "nested-two"})
    assert len(next_dispatches(run)) == 2
    assert run.active_count == 4


def test_capacity_shrink_and_shared_quota_never_create_extra_slots():
    run = ProductionRun.diagnostic_run(21, ProductionPolicy(requested_parallelism=6))
    first = next_dispatches(run)
    run.resize(3)
    assert next_dispatches(run) == [] and run.active_count == 6
    run.reserve_resources(first[0].task_id, ["answer:one"], quota_key="answer", quota_limit=1)
    with pytest.raises(ValueError, match="配额"):
        run.reserve_resources(first[1].task_id, ["answer:two"], quota_key="answer", quota_limit=1)
    with pytest.raises(ValueError, match="预约"):
        run.reserve_resources(first[1].task_id, ["answer:one"])
    run.reserve_resources(first[0].task_id, ["answer:one"], quota_key="answer", quota_limit=1)
    assert len(run.resource_reservations) == 1


def test_shared_request_is_idempotent_and_does_not_register_claims():
    run = ProductionRun.diagnostic_run(1, ProductionPolicy())
    task = run.tasks[0]
    request = run.queue_shared_request(task.task_id, "source", {"claim_ids": ["claim-one", "claim-two"]})
    assert run.queue_shared_request(task.task_id, "source", {"claim_ids": ["claim-one", "claim-two"]}) == request
    assert len(run.shared_requests) == 1 and request["state"] == "pending_domain_validation"


def test_reviewer_priority_has_bounded_aging_for_waiting_writer():
    run=ProductionRun.diagnostic_run(10,ProductionPolicy(requested_parallelism=1))
    writer=next_dispatches(run)[0]
    run.complete_edit(writer.task_id,"合成稿",input_manifest_sha256=writer.input_manifest_sha256)
    assert next_dispatches(run)[0].role=="reviewer"
    reviewer=next(t for t in run.tasks if t.role=="reviewer")
    run.complete_review(reviewer.task_id,passed=True,input_manifest_sha256=reviewer.input_manifest_sha256)
    assert next_dispatches(run)[0].chapter_number==2
