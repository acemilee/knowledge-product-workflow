import pytest

from knowledge_product_workflow.production_dependencies import dependency_plan_from_chapters


def test_result_waits_but_navigation_and_frozen_interface_allow_parallel():
    chapters = [
        {"chapter_number": 1, "production_dependencies": [], "production_exports": [
            {"export_id": "export-input", "section_id": "1.1", "kind": "interface",
             "definition": "固定案例输入"},
            {"export_id": "export-result", "section_id": "1.2", "kind": "result",
             "definition": "实际计算结果"}]},
        {"chapter_number": 2, "production_dependencies": [
            {"producer_chapter": 1, "section_id": "2.1", "kind": "interface",
             "export_id": "export-input", "purpose": "使用固定输入"}], "production_exports": []},
        {"chapter_number": 3, "production_dependencies": [
            {"producer_chapter": 1, "section_id": "3.1", "kind": "result",
             "export_id": "export-result", "purpose": "引用真实结果"}], "production_exports": []},
    ]
    plan = dependency_plan_from_chapters(chapters, sample_approved=True)
    assert plan.ready_chapters == [1, 2]
    assert "result_not_current" in plan.blocked_chapters[3][0]


def test_unknown_dependencies_never_mean_independent():
    plan = dependency_plan_from_chapters([
        {"chapter_number": 1, "production_dependencies": None, "production_exports": None}
    ], sample_approved=False)
    assert plan.ready_chapters == []
    assert plan.blocked_chapters[1] == ["dependencies_unknown"]


def test_result_cycle_is_rejected_before_writing():
    chapters = [
        {"chapter_number": n, "production_dependencies": [
            {"producer_chapter": 3-n, "section_id": f"{n}.1", "kind": "result",
             "export_id": f"export-{3-n}", "purpose": "相互引用"}],
         "production_exports": [{"export_id": f"export-{n}", "section_id": f"{n}.1",
                                 "kind": "result", "definition": "结果"}]}
        for n in (1, 2)
    ]
    with pytest.raises(ValueError, match="循环"):
        dependency_plan_from_chapters(chapters, sample_approved=True)


def test_twenty_independent_chapters_and_sample_gate():
    chapters = [{"chapter_number": n, "production_dependencies": [],
                 "production_exports": []} for n in range(1, 22)]
    assert dependency_plan_from_chapters(chapters, sample_approved=False).ready_chapters == [1]
    assert dependency_plan_from_chapters(chapters, sample_approved=True).ready_chapters == list(range(1, 22))


def test_old_blueprint_chapter_serialization_does_not_change_bindings():
    from knowledge_product_workflow.workflows.curriculum import BlueprintChapter

    chapter = BlueprintChapter(chapter_number=1, title="查阅", scope="定义",
        weight=1, rationale="查询定义", chapter_archetype="reference")
    assert "production_dependencies" not in chapter.model_dump()
    assert "production_exports" not in chapter.model_dump()


def test_new_schema_requires_explicit_every_chapter_and_keeps_legacy_compat(tmp_path):
    from knowledge_product_workflow.workflows.curriculum import (
        BlueprintSubmission,
        require_current_submitted_blueprint,
    )
    from tests.test_chapter_content_plan import _approved_workspace
    workspace = _approved_workspace(tmp_path)
    _, _, baseline = require_current_submitted_blueprint(workspace)
    payload = baseline.model_dump(mode="json")
    payload.update(production_policy_revision=1, shared_context={"terminology": {"输入": "执行前的固定条件"}})
    with pytest.raises(ValueError, match="明确"):
        BlueprintSubmission.model_validate(payload)
    for c in payload["chapters"]:
        c.update(production_dependencies=[], production_exports=[])
    submission = BlueprintSubmission.model_validate(payload)
    assert all(c.production_dependencies == [] for c in submission.chapters)
    payload["chapters"][0]["production_dependencies"] = [{"producer_chapter": 2,
        "section_id": "9.9", "kind": "navigation", "purpose": "无效使用位置"}]
    with pytest.raises(ValueError):
        BlueprintSubmission.model_validate(payload)


def test_unchanged_body_cannot_supply_result_after_evidence_or_review_drift(tmp_path):
    import json

    from knowledge_product_workflow.frontmatter import render_markdown_artifact
    from knowledge_product_workflow.models import ArtifactMetadata, ArtifactStatus
    from knowledge_product_workflow.production_dependencies import (
        AcceptedExport,
        export_is_current,
        file_digest,
        payload_digest,
    )
    from knowledge_product_workflow.workspace import Workspace
    workspace = Workspace.create(tmp_path, "evidence")
    chapter = workspace.root / "chapters/chapter-03.md"
    chapter.write_text(render_markdown_artifact(ArtifactMetadata(book_id="evidence", artifact_type="chapter",
        status=ArtifactStatus.IN_VALIDATION, version=1), "# 3. 结果\n\n## 3.3 实验\n\n输出为7。\n"), encoding="utf-8")
    from knowledge_product_workflow.developmental import reading_units
    proof = workspace.root / "runs/proof.json"
    proof.write_text('{"passed":true}', encoding="utf-8")
    evidence = workspace.root / "research/source-ledger.jsonl"
    evidence.write_text("来源A", encoding="utf-8")
    commits = workspace.root / "runs/production/commits"
    commits.mkdir(parents=True)
    (commits / "commit-one.json").write_text(json.dumps({"state": "completed", "diagnostic_only": False}), encoding="utf-8")
    declaration = {"chapter_number": 3, "section_id": "3.3", "kind": "result", "export_id": "export-result", "definition": "实验结果"}
    unit = reading_units("## 3.3 实验\n\n输出为7。\n")[0]
    item = AcceptedExport(export_id="export-result", chapter_number=3, section_id="3.3", commit_id="commit-one",
        unit_sha256=unit["unit_sha256"], declaration_sha256=payload_digest(declaration), state="valid",
        proof_bindings={"runs/proof.json": file_digest(proof)}, input_bindings={"research/source-ledger.jsonl": file_digest(evidence)})
    before = chapter.read_bytes()
    assert export_is_current(workspace, item, declaration)
    evidence.write_text("来源已失效", encoding="utf-8")
    assert not export_is_current(workspace, item, declaration)
    evidence.write_text("来源A", encoding="utf-8")
    assert export_is_current(workspace, item, declaration)
    proof.write_text('{"passed":false}', encoding="utf-8")
    assert not export_is_current(workspace, item, declaration)
    assert chapter.read_bytes() == before


def test_newly_discovered_cross_chapter_result_requires_dependency_review(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from knowledge_product_workflow.production_dependencies import undeclared_candidate_dependencies
    from knowledge_product_workflow.workflows import authoring
    from knowledge_product_workflow.workspace import Workspace
    workspace=Workspace.create(tmp_path,"new-reference")
    chapter=SimpleNamespace(production_dependencies=[])
    monkeypatch.setattr(authoring,"require_current_chapter_contract_in_view",lambda *_: (None,chapter))
    body="## 4.2 迁移\n\n沿用第3章的实验结果确定参数。\n"
    assert undeclared_candidate_dependencies(workspace,4,body)==["dependency_review_required:4.2→chapter-3"]
    assert not undeclared_candidate_dependencies(workspace,4,"## 4.2 示例\n\n```text\n沿用第3章的实验结果\n```\n")
