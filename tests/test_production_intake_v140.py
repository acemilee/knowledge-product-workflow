import json

import pytest

from knowledge_product_workflow.production_dependencies import file_digest
from knowledge_product_workflow.production_intake import (
    recover_production_commit,
    require_consistent_production,
)
from knowledge_product_workflow.workspace import Workspace


def test_partial_commit_blocks_readers_and_recovers_only_its_files(tmp_path):
    workspace = Workspace.create(tmp_path, "recover")
    root = workspace.root / "runs/production/commits"
    root.mkdir(parents=True)
    staged = workspace.root / "runs/production/staged.md"
    staged.write_text("冻结正文", encoding="utf-8")
    record = {"state": "prepared", "diagnostic_only": False, "chapter_number": 2,
              "outputs": {"chapters/chapter-02.md": {"staged": "runs/production/staged.md",
                                                      "sha256": file_digest(staged)}}}
    (root / "commit-one.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="半提交"):
        require_consistent_production(workspace)
    recover_production_commit(workspace, "commit-one")
    require_consistent_production(workspace)
    assert (workspace.root / "chapters/chapter-02.md").read_text(encoding="utf-8") == "冻结正文"
    before = (workspace.root / "runs/events.jsonl").read_bytes()
    recover_production_commit(workspace, "commit-one")
    assert (workspace.root / "runs/events.jsonl").read_bytes() == before


def test_reader_index_and_snapshot_refuse_half_commits(tmp_path):
    from knowledge_product_workflow.developmental import book_reading_index
    from knowledge_product_workflow.production_context import freeze_candidate_context
    workspace = Workspace.create(tmp_path, "pending")
    root = workspace.root / "runs/production/commits"
    root.mkdir(parents=True)
    (root / "pending.json").write_text('{"state":"prepared"}', encoding="utf-8")
    source = workspace.root / "runs/source.md"
    source.write_text("候选", encoding="utf-8")
    with pytest.raises(ValueError, match="半提交"):
        book_reading_index(workspace, 1, "## 1.1 正文")
    with pytest.raises(ValueError, match="半提交"):
        freeze_candidate_context(workspace, chapter_number=1, candidate_version=1,
            source=source, input_paths=[], run_id="run-one", task_id="task-one")


@pytest.mark.parametrize("failure_point", [1, 2, 3])
def test_commit_each_replace_failure_keeps_marker_and_idempotent_event(tmp_path, monkeypatch, failure_point):
    import knowledge_product_workflow.production_intake as module
    workspace = Workspace.create(tmp_path, "failure")
    root = workspace.root / "runs/production/commits"
    root.mkdir(parents=True)
    outputs = {}
    for name in ("chapters/chapter-02.md", "runs/humanize/chapter-02.json"):
        staged = workspace.root / "runs/production/staged" / name
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text("冻结输入" + name, encoding="utf-8")
        outputs[name] = {"staged": staged.relative_to(workspace.root).as_posix(), "sha256": file_digest(staged)}
    (root / "commit-one.json").write_text(json.dumps({"state": "prepared", "diagnostic_only": False,
        "chapter_number": 2, "outputs": outputs}), encoding="utf-8")
    original = module.replace_file
    calls = 0
    def inject(source, target):
        nonlocal calls
        calls += 1
        if calls == failure_point:
            raise OSError("注入提交点失败")
        return original(source, target)
    monkeypatch.setattr(module, "replace_file", inject)
    with pytest.raises(OSError, match="提交点"):
        recover_production_commit(workspace, "commit-one")
    with pytest.raises(ValueError, match="半提交"):
        require_consistent_production(workspace)
    monkeypatch.setattr(module, "replace_file", original)
    recover_production_commit(workspace, "commit-one")
    before = (workspace.root / "runs/events.jsonl").read_bytes()
    recover_production_commit(workspace, "commit-one")
    assert (workspace.root / "runs/events.jsonl").read_bytes() == before
    assert before.count(b"production_commit_completed") == 1


def test_private_submission_creates_controlled_draft_and_keeps_logical_book_id(tmp_path):
    from pathlib import Path

    from knowledge_product_workflow.frontmatter import parse_markdown_artifact
    from knowledge_product_workflow.production_context import (
        evaluate_candidate,
        freeze_candidate_context,
        record_candidate_structure_review,
    )
    from knowledge_product_workflow.production_intake import materialize_candidate_submission
    from tests.test_structure_review_v133 import _fixture, fixture_teaching_checks
    workspace,source,body=_fixture(tmp_path)
    body=body.replace("\n\n## ","\n\n::: {.kp-chapter-lead}\n本章从输入、使用条件与可检查产出展开。\n:::\n\n## ",1)
    source.write_text(body,encoding="utf-8")
    # Diagnostic domain fixture only: no native production qualification is fabricated.
    inputs=[p.relative_to(workspace.root).as_posix() for p in workspace.root.rglob("*") if p.is_file()]
    context=freeze_candidate_context(workspace,chapter_number=1,candidate_version=1,source=source,input_paths=inputs,run_id="private-submit",task_id="writer-one")
    proof=evaluate_candidate(context)
    template=json.loads(Path(proof.reports["structure_template"]).read_text(encoding="utf-8"))
    submission={"template_id":template["template_id"],"observations":[{"unit_id":o["unit_id"],"assessment":"passed","note":"本地协议夹具验证结构字段绑定与受控物化，不代表真实语义已验收。","teaching_checks":fixture_teaching_checks(),"assertion_checks":[]} for o in template["observations"]]}
    record_candidate_structure_review(context,submission)
    private=Workspace(root=Path(context.private_result_root)/"proof-workspace")
    mapping=private.root/"runs/compliance-input.json"
    mapping.write_text(json.dumps({"learning_objective_anchors":{"建立可执行的判断框架":"1.1 决策框架"},"unit_anchors":{"decision_framework":"1.1 决策框架"},"claim_locations":{}},ensure_ascii=False),encoding="utf-8")
    assert not (private.root/"chapters/chapter-01.md").exists()
    result=materialize_candidate_submission(private,context,mapping)
    metadata,_=parse_markdown_artifact((result.root/"chapters/chapter-01.md").read_text(encoding="utf-8"))
    assert metadata.book_id==workspace.root.name
    assert not (workspace.root/"chapters/chapter-01.md").exists()
    assert not (private.root/"chapters/chapter-01.md").exists()

    from datetime import UTC, datetime

    from knowledge_product_workflow.production_tasks import (
        ProductionHostCapabilities,
        ProductionPolicy,
        ProductionRun,
        next_dispatches,
    )
    from knowledge_product_workflow.review_batches import (
        ReviewResultInput,
        materialize_candidate_review_batch,
        record_candidate_review,
        review_batch_proofs,
        validate_review_batches,
    )
    run=ProductionRun.diagnostic_run(1,ProductionPolicy())
    writer=next_dispatches(run)[0]
    writer.task_id=context.task_id
    writer.host_task_id="fixture-author"
    run.complete_edit(writer.task_id,body,input_manifest_sha256=writer.input_manifest_sha256)
    reviewer=next_dispatches(run)[0]
    reviewer.host_task_id="fixture-reviewer"
    reviewer.host_parent_id="fixture-parent"
    reviewer.host_dispatched_at=datetime.now(UTC)
    review=ReviewResultInput(status="passed",model="fixture",model_version="offline",prompt_hash="0"*64,
        reviewer_identity="fixture-reviewer",reviewer_session_id="fixture-reviewer",
        scores={key:4 for key in ["factual_grounding","teaching_progression","interview_actionability","case_specificity","expression_novelty"]},
        unit_evidence=[{"unit":"decision_framework","location":"1.1 决策框架","note":"本地转换夹具，不是真实生产审稿。"}])
    record_candidate_review(context,author_identity=writer.task_id,author_session_id=writer.host_task_id,
        reviewer_task=reviewer,result=review,capabilities=ProductionHostCapabilities(),diagnostic_only=True)
    converted=materialize_candidate_review_batch(result,context,writer,reviewer,commit_id="commit-fixture",
        capabilities=ProductionHostCapabilities(),diagnostic_only=True)
    assert len(converted)==3
    assert review_batch_proofs(result)
    assert any(f.code=="REVIEW_INDEPENDENCE_UNVERIFIED" for f in validate_review_batches(result))

    changed=materialize_candidate_submission(private,context,mapping)
    contract=changed.root/"chapters/chapter-01-contract.md"
    contract.write_text(contract.read_text(encoding="utf-8")+"\n<!-- 合约变更反例 -->\n",encoding="utf-8")
    with pytest.raises(ValueError,match="物化输入"):
        materialize_candidate_review_batch(changed,context,writer,reviewer,commit_id="commit-changed-contract",
            capabilities=ProductionHostCapabilities(),diagnostic_only=True)
