"""纯目录、设计说明及仅标题守恒在同一真实蓝图上验证。"""
import copy
import json

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow import outline_navigation as navigation
from knowledge_product_workflow.cli import app
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.workflows import curriculum
from knowledge_product_workflow.workflows.product_brief import (
    create_product_brief,
    parse_detail_constraints,
)
from knowledge_product_workflow.workspace import Workspace
from tests.test_blueprint_submission import _approve
from tests.test_outline_navigation_v142 import legacy_payload, modern_payload


def book(tmp_path, modern=True):
    w = Workspace.create(tmp_path, "navigation")
    brief = create_product_brief(w, "专业教程", DetailConstraints(delivery_format="markdown"), target_audience="工程师")
    _approve(brief)
    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    data = modern_payload() if modern else legacy_payload()
    from hashlib import sha256
    data.update(brief_sha256=sha256(brief.read_bytes()).hexdigest(), brief_version=metadata.version,
                audience_level=parse_detail_constraints(body).audience_level.value)
    source = w.root / "runs/input.json"
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    curriculum.submit_curriculum_blueprint(w, source)
    return w, data, source


def files(w):
    return {p.relative_to(w.root).as_posix(): p.read_bytes() for p in w.root.rglob("*") if p.is_file()}


def test_pure_directory_has_exact_titles_and_design_is_separate():
    submission = curriculum.BlueprintSubmission.model_validate(modern_payload())
    toc = navigation.render_outline_view(submission, "toc")
    assert toc["markdown"] == "1. 评测设计\n  - 1.1 评分器\n"
    assert "体裁" not in toc["markdown"] and "取舍" not in toc["markdown"] and " — " not in toc["markdown"]
    assert all(set(row) == {"number", "title", "level", "parent_number"} for row in toc["headings"])
    design = navigation.render_outline_view(submission, "design")
    assert "建立可复核的评测方法" in design["markdown"]
    assert "评分口径" in design["markdown"] and "结果判定" in design["markdown"]
    assert design["semantic_approval"] is False


def test_review_does_not_equate_unchanged_scope_with_semantic_quality():
    current = curriculum.BlueprintSubmission.model_validate(modern_payload())
    data = modern_payload()
    data["chapters"][0]["title"] = "评测集构建"
    proposed = curriculum.BlueprintSubmission.model_validate(data)
    review = navigation.compare_outline_navigation(current, proposed)
    assert review["changed_non_title_fields"] == []
    assert review["semantic_approval"] is False
    assert review["changes"][0]["before"] == "评测设计" and review["changes"][0]["after"] == "评测集构建"
    assert review["changes"][0]["navigation"]["topics"] == ["评测集", "评分器"]
    assert review["semantic_checks_required"]


def test_title_comparison_never_discards_nested_domain_title_fields():
    data = modern_payload()
    data.update(production_policy_revision=1, shared_context={"terminology": {}, "cases": {"case": {"title": "旧案例"}}, "conventions": [], "claim_ids": []})
    data["chapters"][0].update(production_dependencies=[], production_exports=[])
    current = curriculum.BlueprintSubmission.model_validate(data)
    changed = copy.deepcopy(data)
    changed["shared_context"]["cases"]["case"]["title"] = "另一个案例"
    result = navigation.compare_outline_navigation(current, curriculum.BlueprintSubmission.model_validate(changed))
    assert "shared_context.cases.case.title" in result["changed_non_title_fields"]


def test_readonly_view_of_pending_blueprint_changes_no_business_files(tmp_path):
    w, _, _ = book(tmp_path)
    before = files(w)
    value = curriculum.inspect_curriculum_outline(w, view="toc")
    assert value["markdown"].startswith("1. 评测设计")
    assert value["coherent_snapshot"] and value["semantic_approval"] is False
    assert files(w) == before


def test_blueprint_markdown_and_compiled_contract_keep_design_out_of_toc(tmp_path):
    w, _, _ = book(tmp_path)
    path = w.root / "planning/curriculum-blueprint.md"
    body = parse_markdown_artifact(path.read_text(encoding="utf-8"))[1]
    toc = body.split("## 学习路径\n", 1)[1].split("## 设计说明", 1)[0]
    assert "取舍" not in toc and "体裁" not in toc and "评分器" in toc
    assert "建立可复核的评测方法" in body
    _approve(path)
    from knowledge_product_workflow.workflows.authoring import compile_chapter_contracts
    contract = compile_chapter_contracts(w)[0].read_text(encoding="utf-8")
    assert "评分口径" in contract and "结果判定" in contract


@pytest.mark.parametrize("mutation", ["scope", "topics", "reader_job", "weight", "style"])
def test_title_only_refuses_non_title_change_before_any_write(tmp_path, mutation):
    w, data, source = book(tmp_path)
    proposed = copy.deepcopy(data)
    chapter = proposed["chapters"][0]
    chapter["title"] = "评测方法"
    if mutation == "scope":
        chapter["scope"] = "另一范围。"
    elif mutation in {"topics", "reader_job"}:
        chapter["navigation"][mutation] = ["评测集"] if mutation == "topics" else "另一任务。"
    elif mutation == "weight":
        chapter["weight"] = 2
    else:
        proposed["title_style"]["mode"] = "question"
    source.write_text(json.dumps(proposed, ensure_ascii=False), encoding="utf-8")
    before = files(w)
    with pytest.raises(curriculum.BlueprintSubmissionError, match="仅标题|非标题"):
        curriculum.amend_curriculum_blueprint(w, source, titles_only=True)
    assert files(w) == before


def test_title_only_normalizes_models_and_still_waits_for_approval(tmp_path):
    w, data, source = book(tmp_path)
    data["chapters"][0].update(title="评测方法", weight=1.0, required_units=["decision_framework"])
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    output = curriculum.amend_curriculum_blueprint(w, source, titles_only=True)
    metadata, body = parse_markdown_artifact(output.read_text(encoding="utf-8"))
    assert metadata.version == 2 and metadata.status.value == "ready_for_review"
    assert "评测方法" in body and (w.root / "planning/history/curriculum-blueprint.v1.md").is_file()


def test_candidate_review_matches_title_only_normalization(tmp_path):
    w, data, source = book(tmp_path)
    data["chapters"][0]["title"] = "评测方法"
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    before = files(w)
    view = curriculum.inspect_curriculum_outline(w, input_path=source, view="review")
    assert view["changed_non_title_fields"] == []
    assert view["current_binding_status"] == "current" and view["semantic_approval"] is False
    assert files(w) == before


def test_legacy_view_is_explicitly_unknown_and_not_auto_adopted(tmp_path):
    w, _, _ = book(tmp_path, modern=False)
    before = files(w)
    view = curriculum.inspect_curriculum_outline(w, view="review")
    assert view["coverage_status"] == "legacy_unknown" and view["semantic_approval"] is False
    assert files(w) == before


def test_cli_view_rejects_external_input_and_exposes_readonly_metadata(tmp_path):
    w, _, source = book(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(source.read_bytes())
    runner = CliRunner()
    rejected = runner.invoke(app, ["outline-view", "--workspace", str(w.root), "--from", str(outside), "--json"])
    assert rejected.exit_code == 1 and "工作区" in rejected.output and "Traceback" not in rejected.output
    before = files(w)
    good = runner.invoke(app, ["outline-view", "--workspace", str(w.root), "--view", "toc", "--json"])
    assert good.exit_code == 0, good.output
    assert json.loads(good.stdout)["coherent_snapshot"]
    assert files(w) == before


def test_cli_rejects_same_mtime_drift_before_printing_json(tmp_path, monkeypatch):
    import os

    w, _, _ = book(tmp_path)
    path = w.root / "planning/curriculum-blueprint.md"
    original = navigation.render_outline_view

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        stamp = path.stat()
        path.write_bytes(path.read_bytes() + b"\nchanged externally\n")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        return result

    monkeypatch.setattr(navigation, "render_outline_view", changed)
    result = CliRunner().invoke(app, ["outline-view", "--workspace", str(w.root), "--json"])
    assert result.exit_code == 1 and '"coherent_snapshot"' not in result.output


def test_mixed_transaction_and_live_reader_lock_are_not_bypassed(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from knowledge_product_workflow.production_tasks import production_lock
    from tests.test_batch_chapter_read_view_v140 import record

    w, _, _ = book(tmp_path)
    journal = w.root / "runs/orchestration/transactions/tx-batch.json"
    journal.write_text(json.dumps(record(w)), encoding="utf-8")
    before = files(w)
    with pytest.raises(ValueError, match="事务待恢复"):
        curriculum.inspect_curriculum_outline(w)
    assert files(w) == before
    journal.write_text(json.dumps(record(w, "completed")), encoding="utf-8")
    (w.root / "runs/production").mkdir(exist_ok=True)
    with production_lock(w), ThreadPoolExecutor(max_workers=1) as pool, pytest.raises(ValueError, match="CONTROL_LOCK_BUSY"):
        pool.submit(curriculum.inspect_curriculum_outline, w).result(timeout=10)


def test_candidate_review_does_not_repair_stale_brief_or_wash_title_only_binding(tmp_path):
    from hashlib import sha256

    w, data, source = book(tmp_path)
    brief = w.root / "planning/product-brief.md"
    brief.write_text(brief.read_text(encoding="utf-8") + "\n新内容要求。\n", encoding="utf-8")
    data["brief_sha256"] = sha256(brief.read_bytes()).hexdigest()
    data["chapters"][0]["title"] = "评测方法"
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    before = files(w)
    with pytest.raises(curriculum.CurriculumPrerequisiteError, match="过期"):
        curriculum.amend_curriculum_blueprint(w, source, titles_only=True)
    assert files(w) == before


def test_command_discovery_exposes_exact_view_choices(tmp_path):
    result = CliRunner().invoke(app, ["commands", "--all", "--command", "outline-view", "--json"])
    assert result.exit_code == 0, result.output
    command = json.loads(result.stdout)["commands"][0]
    assert command["required_role"] == "kp-curriculum-architect"
    choices = next(p["choices"] for p in command["parameters"] if p["name"] == "view")
    assert choices == ["toc", "design", "review"]
