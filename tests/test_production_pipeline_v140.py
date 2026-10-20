import pytest

from knowledge_product_workflow.production_tasks import (
    ProductionPolicy,
    ProductionRun,
    next_dispatches,
)


@pytest.mark.parametrize("limit", [1, 4, 5, 6])
def test_twenty_one_chapters_roll_through_same_pool_with_no_duplicate_dispatch(limit):
    run = ProductionRun.diagnostic_run(21, ProductionPolicy(requested_parallelism=limit))
    seen = set()
    review_started_before_all_authors_finished = False
    for _tick in range(100):
        tasks = next_dispatches(run)
        assert run.active_count <= limit
        for task in tasks:
            assert task.task_id not in seen
            seen.add(task.task_id)
            if task.role == "writer":
                run.complete_edit(task.task_id, f"诊断夹具第{task.chapter_number}章",
                                  input_manifest_sha256=task.input_manifest_sha256)
            else:
                if any(t.role == "writer" and t.state == "edit_ready" for t in run.tasks):
                    review_started_before_all_authors_finished = True
                run.complete_review(task.task_id, passed=True,
                                    input_manifest_sha256=task.input_manifest_sha256)
                run.accepted_chapters.append(task.chapter_number)
                for writer in run.tasks:
                    if writer.chapter_number == task.chapter_number:
                        writer.state = "accepted"
        if len(run.accepted_chapters) == 21:
            break
    assert sorted(run.accepted_chapters) == list(range(1, 22))
    assert run.dispatch_count == len(seen) == 42
    assert review_started_before_all_authors_finished
    assert run.diagnostic_only
    assert not run.capabilities.production_verified


@pytest.mark.parametrize("shape", ["chain", "mixed"])
@pytest.mark.parametrize("limit", [1, 3, 4, 5, 6])
def test_twenty_one_declared_dependency_chapters_never_start_before_required_result(shape, limit):
    from knowledge_product_workflow.production_dependencies import (
        AcceptedExport,
        dependency_plan_from_chapters,
        payload_digest,
    )
    chapters = []
    for n in range(1,22):
        parent = n-1 if shape == "chain" else n-1 if n % 3 == 0 else None
        chapters.append({"chapter_number": n, "production_exports": [{"export_id": f"export-{n}",
            "section_id": f"{n}.1", "kind": "result", "definition": "仅离线诊断结果"}],
            "production_dependencies": [{"producer_chapter": parent, "section_id": f"{n}.1",
                "kind": "result", "export_id": f"export-{parent}", "purpose": "使用真实结果"}] if parent else []})
    run = ProductionRun.diagnostic_run(1, ProductionPolicy(requested_parallelism=limit))
    run.tasks.clear()
    accepted = {}
    dispatched = set()
    for _tick in range(100):
        plan = dependency_plan_from_chapters(chapters, sample_approved=True, accepted_exports=accepted)
        existing = {t.chapter_number for t in run.tasks}
        for n in plan.ready_chapters:
            if n not in existing:
                run.add_writer(n,{})
        tasks = next_dispatches(run)
        assert run.active_count <= limit
        for task in tasks:
            assert task.task_id not in dispatched
            dispatched.add(task.task_id)
            if task.role == "writer":
                assert task.chapter_number in plan.ready_chapters
                run.complete_edit(task.task_id, "合成诊断稿", input_manifest_sha256=task.input_manifest_sha256)
            else:
                run.complete_review(task.task_id, passed=True, input_manifest_sha256=task.input_manifest_sha256)
                declaration = plan.exports[f"export-{task.chapter_number}"]
                accepted[declaration["export_id"]] = AcceptedExport(export_id=declaration["export_id"],
                    chapter_number=task.chapter_number, section_id=declaration["section_id"], commit_id="diagnostic",
                    unit_sha256="synthetic", declaration_sha256=payload_digest(declaration), state="valid")
                run.accepted_chapters.append(task.chapter_number)
                for item in run.tasks:
                    if item.chapter_number == task.chapter_number:
                        item.state="accepted"
        if len(run.accepted_chapters) == 21:
            break
    assert len(run.accepted_chapters) == 21 and len(dispatched)==42
    assert run.diagnostic_only and not run.capabilities.production_verified
