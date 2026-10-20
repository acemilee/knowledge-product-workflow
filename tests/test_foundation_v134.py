"""新书默认、真实身份、页数原意与跨章审核失效的真实负例。"""
import json

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.detail_constraints import parse_page_budget
from knowledge_product_workflow.presentation import load_or_create_publication_profile
from knowledge_product_workflow.workspace import Workspace


def test_identity_reports_actual_distribution_and_unknown_host():
    result = CliRunner().invoke(app, ["identity", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["effective_source"]
    assert data["distribution_version"]
    assert data["source_version"]
    assert data["resources_sha256"]
    assert data["host"]["status"] == "unknown"
    assert data["model"]["status"] == "unknown"


def test_new_book_never_silently_falls_back_to_cover_one(tmp_path):
    workspace = Workspace.create(tmp_path, "new-cover")
    with pytest.raises(ValueError, match="中文梗概"):
        load_or_create_publication_profile(workspace, language="zh-CN")
    assert not (workspace.root / "planning/publication-profile.yaml").exists()


def test_new_publication_uses_current_frozen_style_without_host_overrides(tmp_path):
    from knowledge_product_workflow.workflows.product_brief import (
        BriefSemanticFields,
        create_product_brief,
    )
    workspace = Workspace.create(tmp_path, "current-style")
    create_product_brief(workspace, "知识检索", semantics=BriefSemanticFields(product_positioning="用小语料建立检索与回答的清楚边界。"))
    profile = load_or_create_publication_profile(workspace, language="zh-CN")
    assert profile.cover_layout_revision == 2
    assert profile.cover_summary_zh == "用小语料建立检索与回答的清楚边界。"
    assert profile.illustration_style_revision == 2
    assert profile.paragraph_spacing == "compact"
    assert profile.font_policy == "preferred"


def test_preferred_range_and_hard_max_preserve_reader_intent():
    constraint = parse_page_budget("目标6—8页，最多10页，封面和目录计入")
    assert constraint.scope.value == "delivery_total"
    assert constraint.preferred_minimum == 6
    assert constraint.preferred_maximum == 8
    assert constraint.maximum == 10
    assert constraint.minimum is None
    assert constraint.assess(8) == "preferred_met"
    assert constraint.assess(9) == "preferred_deviation"
    assert constraint.assess(10) == "preferred_deviation"
    assert constraint.assess(11) == "hard_violation"


def test_old_range_remains_hard():
    constraint = parse_page_budget("6—8")
    assert constraint.assess(9) == "hard_violation"
    assert "preferred_minimum" not in constraint.model_dump()


@pytest.mark.parametrize("separator", ["、", "，", ",", "；", ";"])
def test_original_short_request_page_punctuation(separator):
    from knowledge_product_workflow.detail_constraints import (
        DetailConstraints,
        validate_source_page_intent,
    )
    request = f"生成Markdown与PDF，目标6—8页{separator}最多10页，封面目录计入。"
    original = f"目标6—8页{separator}最多10页，封面目录计入。"
    constraint = parse_page_budget(original)
    assert constraint.maximum == 10
    validate_source_page_intent(request, DetailConstraints(page_constraint=constraint))
    with pytest.raises(ValueError, match="原始"):
        validate_source_page_intent(request, DetailConstraints(page_constraint=parse_page_budget("目标6-8页")))


def test_source_page_check_does_not_infer_absent_maximum():
    from knowledge_product_workflow.detail_constraints import (
        DetailConstraints,
        validate_source_page_intent,
    )
    validate_source_page_intent("目标6—8页。", DetailConstraints(page_constraint=parse_page_budget("目标6-8页，最多10页")))


@pytest.mark.parametrize('reader_request', [
    '正文软目标500至800汉字', '建议500-800字', '目标2至3章',
    '首选3到5个练习', '模型建议10-20 tokens',
])
def test_free_text_non_page_quantities_are_not_page_constraints(reader_request):
    from knowledge_product_workflow.detail_constraints import (
        DetailConstraints,
        validate_source_page_intent,
    )
    validate_source_page_intent(reader_request, DetailConstraints())


@pytest.mark.parametrize('reader_request', [
    '正文软目标500至800汉字；目标6—8页，最多10页。',
    '页数：目标6—8，最多10页。',
    '目标6—8页，最多10。',
])
def test_explicit_page_context_still_preserves_hard_maximum(reader_request):
    from knowledge_product_workflow.detail_constraints import (
        DetailConstraints,
        validate_source_page_intent,
    )
    validate_source_page_intent(reader_request, DetailConstraints(page_constraint=parse_page_budget('目标6-8页，最多10页')))
    with pytest.raises(ValueError, match='原始'):
        validate_source_page_intent(reader_request, DetailConstraints(page_constraint=parse_page_budget('目标6-8页')))


def test_other_unit_hard_max_is_not_a_page_limit():
    from knowledge_product_workflow.detail_constraints import (
        DetailConstraints,
        validate_source_page_intent,
    )
    validate_source_page_intent('目标6—8页，最多10章。',
        DetailConstraints(page_constraint=parse_page_budget('目标6-8页')))


def test_current_brief_preserves_original_word_request_without_page_inference(tmp_path):
    from knowledge_product_workflow.detail_constraints import DetailConstraints
    from knowledge_product_workflow.workflows.product_brief import (
        BriefSemanticFields,
        create_product_brief,
        read_request_contract,
    )
    workspace = Workspace.create(tmp_path, 'word-request', current_policy=True, binding_policy_revision=2)
    request = '制作自有假设情境教程，正文软目标500至800汉字。'
    path = create_product_brief(workspace, '任务协作', DetailConstraints(target_words=1),
        target_audience='工程师', semantics=BriefSemanticFields(reader_request=request))
    assert path.is_file()
    assert read_request_contract(workspace)['requirements']['reader_request']['value'] == request


def test_preferred_pages_do_not_invent_chapter_word_quotas():
    from knowledge_product_workflow.detail_constraints import DetailConstraints, allocate_budgets
    constraints = DetailConstraints(page_constraint=parse_page_budget("目标为6–8页，最多10页"))
    budgets = allocate_budgets([1, 2, 3], constraints)
    assert sum(b.target_pages for b in budgets) == 5
    assert all(b.target_words is None for b in budgets)


def test_yaml_schemas_explicitly_support_json_without_changing_old_default():
    import yaml
    runner = CliRunner()
    for command in ("outline-schema", "controlled-exception-schema"):
        old = runner.invoke(app, [command])
        new = runner.invoke(app, [command, "--json"])
        assert old.exit_code == new.exit_code == 0
        assert yaml.safe_load(old.output) == json.loads(new.output)


def test_fixed_inputs_preserve_exact_text_in_brief_and_request(tmp_path):
    from knowledge_product_workflow.detail_constraints import DetailConstraints
    from knowledge_product_workflow.frontmatter import parse_markdown_artifact
    from knowledge_product_workflow.workflows.product_brief import (
        BriefSemanticFields,
        create_product_brief,
        parse_brief_semantics,
        write_request_contract,
    )
    workspace = Workspace.create(tmp_path, "fixed-inputs")
    fixed = ["doc-2：知识库每周二更新一次。", "q = (1, 0)"]
    semantics = BriefSemanticFields(fixed_inputs=fixed)
    brief = create_product_brief(workspace, "检索", DetailConstraints(), semantics=semantics)
    _, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    assert parse_brief_semantics(body).fixed_inputs == fixed
    path = write_request_contract(workspace, "检索", "学习者", DetailConstraints(), {"fixed_inputs": "user_explicit"}, semantics=semantics)
    import yaml
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["requirements"]["fixed_inputs"]["value"] == fixed


def test_reader_original_detects_host_page_scope_and_bound_drift(tmp_path):
    from knowledge_product_workflow.detail_constraints import DetailConstraints
    from knowledge_product_workflow.workflows.product_brief import (
        BriefSemanticFields,
        create_product_brief,
    )
    semantics = BriefSemanticFields(reader_request="生成中文短教程，目标为6—8页，最多10页，封面和目录计入。")
    for index, wrong in enumerate(("正文6-8", "6-8", "10")):
        workspace = Workspace.create(tmp_path, f"wrong-{index}")
        with pytest.raises(ValueError, match="原始"):
            create_product_brief(workspace, "知识检索", DetailConstraints(page_constraint=parse_page_budget(wrong)), semantics=semantics)
    valid = Workspace.create(tmp_path, "valid-source")
    create_product_brief(valid, "知识检索", DetailConstraints(page_constraint=parse_page_budget("目标6-8页，最多10页")), semantics=semantics)


def test_future_navigation_does_not_invalidate_prior_review(tmp_path):
    from knowledge_product_workflow.developmental import _review_context_digest
    from tests.test_structure_review_v133 import _fixture

    workspace, _, body = _fixture(tmp_path)
    body += "\n操作将在第2章继续。\n"
    before = _review_context_digest(workspace, 1, body)
    (workspace.root / "chapters/chapter-02.md").write_text("# 第2章\n\n新写的后续内容。", encoding="utf-8")
    assert _review_context_digest(workspace, 1, body) == before


def test_decimal_and_code_do_not_create_chapter_dependencies(tmp_path):
    from knowledge_product_workflow.developmental import _review_context_digest
    from tests.test_structure_review_v133 import _fixture

    workspace, _, body = _fixture(tmp_path)
    body += "\n分数2.0，版本v2.1；`2.3`是数据。\n```text\n第2章\n```\n"
    before = _review_context_digest(workspace, 1, body)
    (workspace.root / "chapters/chapter-02.md").write_text("# 第2章\n\n任意修订。", encoding="utf-8")
    assert _review_context_digest(workspace, 1, body) == before


def test_real_section_dependency_only_tracks_that_section(tmp_path):
    from knowledge_product_workflow.developmental import _review_context_digest
    from tests.test_structure_review_v133 import _fixture

    workspace, _, body = _fixture(tmp_path)
    body += "\n参照第2章的2.1节核对条件。\n"
    other = workspace.root / "chapters/chapter-02.md"
    other.write_text("## 2.1 条件\n\n条件为A。\n\n## 2.2 其他\n\n无关内容。\n", encoding="utf-8")
    before = _review_context_digest(workspace, 1, body)
    other.write_text(other.read_text(encoding="utf-8").replace("无关内容", "无关内容已更新"), encoding="utf-8")
    assert _review_context_digest(workspace, 1, body) == before
    other.write_text(other.read_text(encoding="utf-8").replace("条件为A", "条件为B"), encoding="utf-8")
    assert _review_context_digest(workspace, 1, body) != before


def test_navigation_keeps_actual_review_reusable(tmp_path):
    from knowledge_product_workflow.developmental import (
        create_structure_review_template,
        record_developmental_check,
    )
    from tests.test_structure_review_v133 import _fixture, simulated_review

    workspace, source, body = _fixture(tmp_path)
    source.write_text(body + "\n操作将在第2章继续。\n", encoding="utf-8")
    simulated_review(workspace, source)
    (workspace.root / "chapters/chapter-02.md").write_text("## 2.1 后续\n\n新写内容。\n", encoding="utf-8")
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"] == ["1.1"]
    record_developmental_check(workspace, 1, source)


@pytest.mark.parametrize("boundary", ["。", "；", "！", "？", "\n\n"])
def test_navigation_cannot_consume_a_conclusion_from_a_later_sentence(tmp_path, boundary):
    from knowledge_product_workflow.developmental import (
        create_structure_review_template,
        record_developmental_check,
    )
    from tests.test_structure_review_v133 import _fixture, simulated_review

    workspace, source, body = _fixture(tmp_path)
    source.write_text(body + f"\n后续内容见第2章{boundary}这里按三步给出结论。\n", encoding="utf-8")
    simulated_review(workspace, source)
    (workspace.root / "chapters/chapter-02.md").write_text("## 2.1 后续\n\n首次写入正文。\n", encoding="utf-8")
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"] == ["1.1"]
    record_developmental_check(workspace, 1, source)


def test_policy2_navigation_and_actual_result_keep_distinct_review_bindings(tmp_path):
    from knowledge_product_workflow.developmental import _review_context_digest
    from tests.test_change_impact_v140 import workspace_fixture

    workspace = workspace_fixture(tmp_path, 2)
    bodies = {
        "navigation": "## 1.1 教学任务\n\n后续内容见第2章。这里按三步给出结论。",
        "result": "## 1.1 教学任务\n\n依据第2章给出的结论，完成本章计算。",
        "wrapped_result": "## 1.1 教学任务\n\n依据第2章\n的结果完成本章计算。",
        "continued_result": "## 1.1 教学任务\n\n复核第2章。其结论用于本章计算。",
        "section": "## 1.1 教学任务\n\n依据第2章的2.1节核对条件。",
    }
    before = {key: _review_context_digest(workspace, 1, body) for key, body in bodies.items()}
    (workspace.root / "chapters/chapter-02.md").write_text("## 2.1 后续\n\n新结果。\n", encoding="utf-8")
    for key, body in bodies.items():
        assert (_review_context_digest(workspace, 1, body) == before[key]) is (key == "navigation")


@pytest.mark.parametrize("continuation", [
    "其主要结论用于本章计算。",
    "该章给出的关键结果用于本章计算。",
    "上述结论用于本章计算。",
    "这里沿用之前的计算结果。",
    "本章使用该章经过验证后给出的主要结论。",
])
def test_cross_sentence_result_consumption_still_invalidates_real_review(tmp_path, continuation):
    from knowledge_product_workflow.developmental import (
        DevelopmentalEditError,
        create_structure_review_template,
        record_developmental_check,
    )
    from tests.test_structure_review_v133 import _fixture, simulated_review

    workspace, source, body = _fixture(tmp_path)
    source.write_text(body + "\n复核第2章。" + continuation + "\n", encoding="utf-8")
    other = workspace.root / "chapters/chapter-02.md"
    other.write_text("## 2.1 后续\n\n原结果。\n", encoding="utf-8")
    simulated_review(workspace, source)
    other.write_text("## 2.1 后续\n\n修正后的结果。\n", encoding="utf-8")
    assert create_structure_review_template(workspace, 1, source)["reusable_unit_ids"] == []
    with pytest.raises(DevelopmentalEditError, match="过期"):
        record_developmental_check(workspace, 1, source)
