"""KP-BUG-003 回归：Brief 只有页数目标时，按页数估算章节 word 预算并提示。

避免正文写完才发现页数不达标、拖到交付阶段以 PDF_PAGE_COUNT_NOT_MET 返工。
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.detail_constraints import (
    WORDS_PER_PAGE_ESTIMATE,
    DetailConstraints,
    allocate_budgets,
)
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.workflows.curriculum import parse_chapter_budgets
from knowledge_product_workflow.workspace import Workspace


def test_allocate_budgets_estimates_word_budget_from_page_target() -> None:
    constraints = DetailConstraints(target_pages="6", language="zh-CN")
    budgets = allocate_budgets([1], constraints)

    assert len(budgets) == 1
    budget = budgets[0]
    assert budget.word_target == 6 * WORDS_PER_PAGE_ESTIMATE
    assert budget.word_min is not None and budget.word_min < budget.word_target
    assert budget.word_max is not None and budget.word_max > budget.word_target
    assert budget.target_pages == 6


def test_allocate_budgets_keeps_none_without_page_or_word_target() -> None:
    constraints = DetailConstraints(language="zh-CN")
    budget = allocate_budgets([1], constraints)[0]
    assert budget.word_min is None and budget.word_target is None and budget.word_max is None


def test_allocate_budgets_does_not_override_explicit_word_gate() -> None:
    constraints = DetailConstraints(
        target_pages="6", word_min=3000, word_target=4000, word_max=4000, language="zh-CN"
    )
    budget = allocate_budgets([1], constraints)[0]
    assert budget.word_target == 4000, "显式字数额门优先，不得按页数覆盖"
    assert budget.word_min == 3000


def _run_brief_to_blueprint(runner: CliRunner, workspace: Workspace) -> None:
    """走完 init -> brief 批准 -> 篇幅评估 -> outline -> submit-outline。"""
    from hashlib import sha256

    import yaml

    from knowledge_product_workflow.workflows.product_brief import brief_contract_fingerprints

    brief_input = {
        "topic": "页数预算探针",
        "target_audience": "中文初学者",
        "target_pages": "4",
        "language": "中文",
        "layout_profile": "A4 标准版式",
        "audience_level": "入门",
        "delivery_format": "Markdown",
        "review_policy": "deterministic_only",
    }
    input_path = workspace.root / "runs" / "brief-input.json"
    input_path.write_text(json.dumps(brief_input, ensure_ascii=False), encoding="utf-8")
    result = runner.invoke(
        app, ["brief", "页数预算探针", "--workspace", str(workspace.root), "--from", str(input_path), "--confirm"]
    )
    assert result.exit_code == 0, result.output
    for command in (
        ["request-user-decision", "brief", "--workspace", str(workspace.root)],
        ["record-user-decision", "brief", "--workspace", str(workspace.root), "--decision", "approve", "--response", "可以", "--by", "用户"],
        ["approve", str(workspace.root / "planning" / "product-brief.md"), "--workspace", str(workspace.root)],
    ):
        result = runner.invoke(app, command)
        assert result.exit_code == 0, (command, result.output)

    brief = workspace.root / "planning" / "product-brief.md"
    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    recommendation = {
        "schema_version": 1,
        "brief_content_sha256": brief_contract_fingerprints(body)["content_contract_sha256"],
        "brief_version": metadata.version,
        "recommended_min_pages": 4,
        "recommended_max_pages": 4,
        "confidence": "high",
        "rationale": ["探针"],
        "scope_expansion_units": [],
    }
    rec_path = workspace.root / "runs" / "length-recommendation.yaml"
    rec_path.write_text(yaml.safe_dump(recommendation, allow_unicode=True, sort_keys=False), encoding="utf-8")
    result = runner.invoke(
        app, ["submit-length-recommendation", "--workspace", str(workspace.root), "--from", str(rec_path)]
    )
    assert result.exit_code == 0, result.output

    blueprint = {
        "schema_version": 2,
        "brief_sha256": sha256(brief.read_bytes()).hexdigest(),
        "brief_content_sha256": brief_contract_fingerprints(body)["content_contract_sha256"],
        "brief_version": metadata.version,
        "topic": "页数预算探针",
        "target_audience": "中文初学者",
        "audience_level": "beginner",
        "product_mode": "general",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "页数预算探针",
                "learning_objectives": ["目标一"],
                "scope": "最小范围",
                "weight": 1,
                "rationale": "探针",
                "chapter_archetype": "concept",
                "required_units": ["concept"],
                "section_outline": [{"number": "1.1", "title": "小节一", "children": []}],
            }
        ],
    }
    blueprint_path = workspace.root / "runs" / "probe-blueprint.yaml"
    blueprint_path.write_text(yaml.safe_dump(blueprint, allow_unicode=True, sort_keys=False), encoding="utf-8")
    result = runner.invoke(
        app, ["submit-outline", "--workspace", str(workspace.root), "--from", str(blueprint_path)]
    )
    assert result.exit_code == 0, result.output


def test_submit_outline_estimates_budget_and_warns(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    runner = CliRunner()
    _run_brief_to_blueprint(runner, workspace)

    # 蓝图 body 必须携带按页数估算的章节预算。
    _, blueprint_body = parse_markdown_artifact(
        (workspace.root / "planning" / "curriculum-blueprint.md").read_text(encoding="utf-8")
    )
    budgets = parse_chapter_budgets(blueprint_body)
    assert budgets, "蓝图必须包含章节预算块"
    assert budgets[1].word_min is not None and budgets[1].word_max is not None
    assert budgets[1].word_target == 4 * WORDS_PER_PAGE_ESTIMATE
