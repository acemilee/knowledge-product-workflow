from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.authoring import (
    CurriculumApprovalRequiredError,
    compile_chapter_contracts,
    create_chapter_contract,
    parse_chapter_contract_binding,
)
from knowledge_product_workflow.workflows.curriculum import (
    BlueprintSubmissionError,
    create_curriculum_blueprint_template,
    create_section_outline_migration_draft,
    curriculum_outline_schema,
    parse_blueprint_submission,
    submit_curriculum_blueprint,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def _approve(path: Path) -> None:
    metadata, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    path.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="reviewer"), body
        ),
        encoding="utf-8",
    )


def _submission(brief: Path) -> dict[str, object]:
    metadata, _ = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    return {
        "schema_version": 2,
        "brief_sha256": sha256(brief.read_bytes()).hexdigest(),
        "brief_version": metadata.version,
        "topic": "Agent 开发面试指导",
        "target_audience": "希望转行 Agent 开发的程序员",
        "audience_level": "intermediate",
        "product_mode": "interview",
        "interview_answer_policy": {
            "total_complete_answer_limit": 3,
            "chapter_complete_answer_limits": {"1": 1, "2": 2},
        },
        "chapters": [
            {
                "chapter_number": 1,
                "title": "岗位与能力地图",
                "learning_objectives": ["识别目标岗位的能力边界"],
                "scope": "解释岗位职责和能力要求。",
                "weight": 2,
                "rationale": "先建立岗位判断框架。",
                "chapter_archetype": "strategy",
                "required_units": ["decision_framework", "evidence_slot"],
                "optional_units": ["complete_answer_demo", "common_mistakes"],
                "prohibited_units": [],
                "claim_ids": [],
                "section_outline": [
                    {"number": "1.1", "title": "岗位范围", "children": []},
                    {"number": "1.2", "title": "能力判断", "children": []},
                ],
            },
            {
                "chapter_number": 2,
                "title": "RAG 项目复盘",
                "learning_objectives": ["用证据解释一个 RAG 项目的取舍"],
                "scope": "以情境案例拆解项目面试表达。",
                "weight": 5,
                "rationale": "项目证据决定技术面深度。",
                "chapter_archetype": "case_study",
                "required_units": [
                    "scenario",
                    "evidence_boundary",
                    "decision_process",
                    "retrospective",
                ],
                "optional_units": [],
                "prohibited_units": [],
                "claim_ids": ["claim-rag-001"],
                "section_outline": [
                    {"number": "2.1", "title": "项目情境", "children": []},
                    {"number": "2.2", "title": "证据与取舍", "children": []},
                ],
            },
            {
                "chapter_number": 3,
                "title": "术语速查",
                "learning_objectives": [],
                "scope": "提供查阅用术语表。",
                "weight": 1,
                "rationale": "供读者按需回查。",
                "chapter_archetype": "reference",
                "required_units": ["reference_entries"],
                "optional_units": ["glossary"],
                "prohibited_units": ["exercise"],
                "claim_ids": [],
                "section_outline": [
                    {"number": "3.1", "title": "术语条目", "children": []},
                ],
            },
        ],
    }


def test_submit_outline_binds_brief_and_derives_weighted_budgets(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "agent-interview")
    brief = create_product_brief(
        workspace,
        "Agent 开发面试指导",
        DetailConstraints(word_min=8000, word_target=10000, word_max=12000),
        target_audience="希望转行 Agent 开发的程序员",
    )
    _approve(brief)
    payload_path = tmp_path / "outline.yaml"
    payload_path.write_text(yaml.safe_dump(_submission(brief), allow_unicode=True), encoding="utf-8")

    blueprint = submit_curriculum_blueprint(workspace, payload_path)
    metadata, body = parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))
    submitted = parse_blueprint_submission(body)

    assert metadata.status is ArtifactStatus.READY_FOR_REVIEW
    assert submitted is not None
    assert [chapter.chapter_archetype.value for chapter in submitted.chapters] == [
        "strategy",
        "case_study",
        "reference",
    ]
    assert "chapter_budgets:" in body
    assert "word_target: 2500" in body
    assert "word_target: 6250" in body


def test_submit_outline_rejects_brief_mismatch_and_invalid_interview_policy(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "agent-interview")
    brief = create_product_brief(
        workspace, "Agent 开发面试指导", target_audience="希望转行 Agent 开发的程序员"
    )
    _approve(brief)
    payload = _submission(brief)
    payload["brief_sha256"] = "0" * 64
    payload_path = tmp_path / "outline.yaml"
    payload_path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(BlueprintSubmissionError, match="SHA-256"):
        submit_curriculum_blueprint(workspace, payload_path)

    payload = _submission(brief)
    payload["interview_answer_policy"] = {
        "total_complete_answer_limit": 1,
        "chapter_complete_answer_limits": {"1": 1, "2": 2},
    }
    payload_path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(BlueprintSubmissionError, match="示范回答"):
        submit_curriculum_blueprint(workspace, payload_path)


def test_submit_outline_rejects_legacy_schema_even_when_section_outline_is_present(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "agent-interview")
    brief = create_product_brief(
        workspace, "Agent 开发面试指导", target_audience="希望转行 Agent 开发的程序员"
    )
    _approve(brief)
    payload = _submission(brief)
    payload["schema_version"] = 1
    payload_path = tmp_path / "outline.yaml"
    payload_path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(BlueprintSubmissionError, match="schema_version: 2"):
        submit_curriculum_blueprint(workspace, payload_path)


@pytest.mark.parametrize(
    ("section_outline", "message"),
    [
        ([], "section_outline"),
        ([{"number": "1.2", "title": "跳号", "children": []}], "连续递增"),
        ([{"number": "2.1", "title": "跨章", "children": []}], "期望 1.1"),
        (
            [
                {
                    "number": "1.1",
                    "title": "父级",
                    "children": [{"number": "1.1.2", "title": "子级", "children": []}],
                }
            ],
            "期望 1.1.1",
        ),
    ],
)
def test_submit_outline_rejects_missing_or_non_continuous_section_outline(
    tmp_path: Path, section_outline: list[dict[str, object]], message: str
) -> None:
    workspace = Workspace.create(tmp_path, "agent-interview")
    brief = create_product_brief(
        workspace, "Agent 开发面试指导", target_audience="希望转行 Agent 开发的程序员"
    )
    _approve(brief)
    payload = _submission(brief)
    chapters = payload["chapters"]
    assert isinstance(chapters, list)
    assert isinstance(chapters[0], dict)
    chapters[0]["section_outline"] = section_outline
    payload_path = tmp_path / "outline.yaml"
    payload_path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(BlueprintSubmissionError, match=message):
        submit_curriculum_blueprint(workspace, payload_path)


def test_submit_outline_rejects_archetype_units_that_recreate_the_same_template(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "agent-interview")
    brief = create_product_brief(
        workspace, "Agent 开发面试指导", target_audience="希望转行 Agent 开发的程序员"
    )
    _approve(brief)
    payload = _submission(brief)
    first = payload["chapters"][0]
    assert isinstance(first, dict)
    first["required_units"] = ["decision_framework", "concept"]
    payload_path = tmp_path / "outline.yaml"
    payload_path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(BlueprintSubmissionError, match="可选值"):
        submit_curriculum_blueprint(workspace, payload_path)


def test_outline_schema_and_template_expose_teaching_units_without_guessing(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "agent-interview")
    brief = create_product_brief(
        workspace, "Agent 开发面试指导", target_audience="希望转行 Agent 开发的程序员"
    )
    _approve(brief)

    schema = curriculum_outline_schema()
    template = create_curriculum_blueprint_template(workspace, brief)
    template_text = template.read_text(encoding="utf-8")

    strategy = schema["chapter_archetypes"]["strategy"]
    assert "decision_framework" in strategy["allowed_units"]
    assert strategy["default_required_units"] == ["decision_framework"]
    assert "<适配体裁的必需教学单元>" not in template_text
    assert "教学单元公开契约" in template_text
    assert "kp outline-schema" in template_text


def test_submit_outline_derives_default_required_units_when_host_omits_them(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "agent-interview")
    brief = create_product_brief(
        workspace, "Agent 开发面试指导", target_audience="希望转行 Agent 开发的程序员"
    )
    _approve(brief)
    payload = _submission(brief)
    chapters = payload["chapters"]
    assert isinstance(chapters, list)
    for chapter in chapters:
        assert isinstance(chapter, dict)
        chapter.pop("required_units")
    payload_path = tmp_path / "outline.yaml"
    payload_path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    blueprint = submit_curriculum_blueprint(workspace, payload_path)
    _, body = parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))
    submitted = parse_blueprint_submission(body)

    assert submitted is not None
    assert submitted.chapters[0].required_units == ["decision_framework"]
    assert submitted.chapters[1].required_units == [
        "decision_process",
        "evidence_boundary",
        "retrospective",
        "scenario",
    ]
    assert submitted.chapters[2].required_units == ["reference_entries"]


def test_submit_outline_rejects_same_archetype_with_repeated_teaching_rationale(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "agent-interview")
    brief = create_product_brief(
        workspace, "Agent 开发面试指导", target_audience="希望转行 Agent 开发的程序员"
    )
    _approve(brief)
    payload = _submission(brief)
    payload["product_mode"] = "general"
    payload["interview_answer_policy"] = None
    chapters = payload["chapters"]
    assert isinstance(chapters, list)
    for index, chapter in enumerate(chapters, start=1):
        assert isinstance(chapter, dict)
        chapter.update(
            {
                "chapter_number": index,
                "chapter_archetype": "concept",
                "learning_objectives": ["建立一致的基础判断"],
                "required_units": ["concept"],
                "optional_units": [],
                "prohibited_units": [],
                "rationale": "使用统一结构。",
            }
        )
    payload_path = tmp_path / "outline.yaml"
    payload_path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(BlueprintSubmissionError, match="同一章节体裁"):
        submit_curriculum_blueprint(workspace, payload_path)


def test_submit_outline_cli_receives_a_host_payload_without_positional_answers(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "agent-interview")
    brief = create_product_brief(
        workspace, "Agent 开发面试指导", target_audience="希望转行 Agent 开发的程序员"
    )
    _approve(brief)
    payload_path = tmp_path / "outline.json"
    payload_path.write_text(json.dumps(_submission(brief), ensure_ascii=False), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["submit-outline", "--workspace", str(workspace.root), "--from", str(payload_path)],
    )

    assert result.exit_code == 0, result.output
    assert "已提交课程蓝图" in result.output


def test_outline_schema_cli_is_machine_readable_and_chinese(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["outline-schema"])

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload["schema_version"] == 2
    assert payload["chapter_archetypes"]["concept"]["label"] == "概念讲解"


def test_compile_contracts_derives_every_chapter_from_the_approved_submission(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "agent-interview")
    brief = create_product_brief(
        workspace, "Agent 开发面试指导", target_audience="希望转行 Agent 开发的程序员"
    )
    _approve(brief)
    payload_path = tmp_path / "outline.yaml"
    payload_path.write_text(yaml.safe_dump(_submission(brief), allow_unicode=True), encoding="utf-8")
    blueprint = submit_curriculum_blueprint(workspace, payload_path)
    _approve(blueprint)

    contracts = compile_chapter_contracts(workspace)
    metadata, body = parse_markdown_artifact(contracts[1].read_text(encoding="utf-8"))
    binding = parse_chapter_contract_binding(body)

    assert [path.name for path in contracts] == [
        "chapter-01-contract.md",
        "chapter-02-contract.md",
        "chapter-03-contract.md",
    ]
    assert metadata.status is ArtifactStatus.APPROVED
    assert binding["chapter"]["title"] == "RAG 项目复盘"
    assert binding["chapter"]["chapter_archetype"] == "case_study"
    assert binding["chapter"]["claim_ids"] == ["claim-rag-001"]
    with pytest.raises(ValueError, match="标题"):
        create_chapter_contract(workspace, 2, "任意替换标题")


def test_legacy_blueprint_can_only_create_a_read_only_migration_candidate(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "agent-interview")
    brief = create_product_brief(
        workspace, "Agent 开发面试指导", target_audience="希望转行 Agent 开发的程序员"
    )
    _approve(brief)
    payload_path = tmp_path / "outline.yaml"
    payload_path.write_text(yaml.safe_dump(_submission(brief), allow_unicode=True), encoding="utf-8")
    blueprint = submit_curriculum_blueprint(workspace, payload_path)
    _approve(blueprint)
    metadata, body = parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))
    current = parse_blueprint_submission(body)
    assert current is not None
    legacy_payload = current.model_dump(mode="json")
    legacy_payload["schema_version"] = 1
    for chapter in legacy_payload["chapters"]:
        assert isinstance(chapter, dict)
        chapter.pop("section_outline")
    marker = "## 蓝图契约\n\n```yaml\n"
    start = body.index(marker)
    end = body.index("```", start + len(marker)) + 3
    legacy_contract = marker + yaml.safe_dump(
        {"blueprint": legacy_payload}, allow_unicode=True, sort_keys=False
    ).rstrip() + "\n```"
    blueprint.write_text(
        render_markdown_artifact(metadata, body[:start] + legacy_contract + body[end:]), encoding="utf-8"
    )

    with pytest.raises(CurriculumApprovalRequiredError, match="section_outline"):
        compile_chapter_contracts(workspace)

    candidate = create_section_outline_migration_draft(workspace)
    candidate_payload = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    assert candidate_payload["migration_only"] is True
    assert candidate_payload["source_blueprint_version"] == 1
    assert "不得直接修改当前蓝图" in candidate_payload["instructions"]


def test_appendix_with_empty_units_can_be_submitted_and_budgeted(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "appendix-guide")
    brief = create_product_brief(workspace, "Agent 开发面试指导", DetailConstraints(target_words=100), target_audience="希望转行 Agent 开发的程序员")
    _approve(brief)
    payload = _submission(brief)
    payload["product_mode"] = "general"
    payload["interview_answer_policy"] = None
    chapter = payload["chapters"][0]
    chapter.update({
        "chapter_archetype": "appendix", "required_units": [], "optional_units": [],
        "learning_objectives": [], "section_outline": [{"number": "1.1", "title": "操作索引", "children": []}],
    })
    payload["chapters"] = [chapter]
    source = workspace.root / "runs" / "appendix.yaml"
    source.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    blueprint = submit_curriculum_blueprint(workspace, source)
    assert parse_blueprint_submission(parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))[1]).chapters[0].required_units == []
    _approve(blueprint)
    assert compile_chapter_contracts(workspace)


def test_float_section_number_has_actionable_cli_error_without_traceback(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "number-guide")
    brief = create_product_brief(workspace, "Agent 开发面试指导", DetailConstraints(target_words=100), target_audience="希望转行 Agent 开发的程序员")
    _approve(brief)
    payload = _submission(brief)
    payload["chapters"][0]["section_outline"][0]["number"] = 1.1
    source = workspace.root / "runs" / "number.yaml"
    source.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    result = CliRunner().invoke(app, ["submit-outline", "--workspace", str(workspace.root), "--from", str(source)])
    assert result.exit_code == 1
    assert 'number: "1.1"' in result.output
    assert "Traceback" not in result.output
    assert not (workspace.root / "planning" / "curriculum-blueprint.md").exists()
