"""Test-only helpers for submitting a minimal current structured blueprint."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path

import yaml

from knowledge_product_workflow.chapter_compliance import submit_chapter
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.humanize import record_humanize_check
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from knowledge_product_workflow.workflows.curriculum import (
    submit_curriculum_blueprint,
    submit_teaching_length_recommendation,
)
from knowledge_product_workflow.workflows.product_brief import (
    brief_contract_fingerprints,
    parse_detail_constraints,
)
from knowledge_product_workflow.workspace import Workspace


def submit_test_blueprint(
    workspace: Workspace,
    brief_path: Path,
    *,
    titles: tuple[str, ...] = ("基础判断", "案例拆解"),
    claim_ids: tuple[tuple[str, ...], ...] | None = None,
    section_outlines: tuple[list[dict[str, object]], ...] | None = None,
    recommendation_max_pages: int | None = None,
) -> Path:
    """Submit a compact, subject-neutral blueprint for workflow tests."""
    metadata, body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    topic = next(line[2:] for line in body.splitlines() if line.startswith("# "))
    audience_match = re.search(
        r"^## (?:目标读者|Target readers)\s*\n+(.*?)(?=^## |\Z)", body, re.MULTILINE | re.DOTALL
    )
    assert audience_match is not None
    constraints = parse_detail_constraints(body)
    chapter_claims = claim_ids or tuple(() for _ in titles)
    if section_outlines is not None and len(section_outlines) != len(titles):
        raise ValueError("测试小节树必须与章节数量一致")
    chapters = []
    for number, (title, claims) in enumerate(zip(titles, chapter_claims, strict=True), start=1):
        if number == 1:
            required_units = ["decision_framework"]
            outline = (
                section_outlines[number - 1]
                if section_outlines is not None
                else _section_outline(number, required_units)
            )
            chapters.append(
                {
                    "chapter_number": number,
                    "title": title,
                    "learning_objectives": ["建立可执行的判断框架"],
                    "scope": "解释本章的决策边界。",
                    "weight": 2,
                    "rationale": "为后续案例提供共同判断基础。",
                    "chapter_archetype": "strategy",
                    "required_units": required_units,
                    "optional_units": ["evidence_slot"],
                    "prohibited_units": [],
                    "claim_ids": list(claims),
                    "section_outline": outline,
                }
            )
        else:
            required_units = [
                "scenario",
                "evidence_boundary",
                "decision_process",
                "retrospective",
            ]
            outline = (
                section_outlines[number - 1]
                if section_outlines is not None
                else _section_outline(number, required_units)
            )
            chapters.append(
                {
                    "chapter_number": number,
                    "title": title,
                    "learning_objectives": ["结合证据复盘一项具体决策"],
                    "scope": "通过限定情境展开案例分析。",
                    "weight": 3,
                    "rationale": "把前述框架迁移到具体情境。",
                    "chapter_archetype": "case_study",
                    "required_units": required_units,
                    "optional_units": [],
                    "prohibited_units": [],
                    "claim_ids": list(claims),
                    "section_outline": outline,
                }
            )
    payload = {
        "schema_version": 2,
        "brief_sha256": sha256(brief_path.read_bytes()).hexdigest(),
        "brief_content_sha256": brief_contract_fingerprints(body)["content_contract_sha256"],
        "brief_version": metadata.version,
        "topic": topic,
        "target_audience": audience_match.group(1).strip(),
        "audience_level": constraints.audience_level.value if constraints else "进阶",
        "product_mode": "general",
        "interview_answer_policy": None,
        "chapters": chapters,
    }
    source = workspace.root / "runs" / "test-blueprint.yaml"
    source.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if constraints is not None and constraints.page_constraint is not None:
        page_constraint = constraints.page_constraint
        recommendation = workspace.root / "runs" / "test-length-recommendation.yaml"
        minimum = page_constraint.minimum or page_constraint.target or 10
        maximum = recommendation_max_pages or page_constraint.maximum or page_constraint.target or max(minimum, 20)
        minimum = min(minimum, maximum)
        recommendation.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "brief_content_sha256": brief_contract_fingerprints(body)["content_contract_sha256"],
                    "brief_version": metadata.version,
                    "recommended_min_pages": minimum,
                    "recommended_max_pages": maximum,
                    "confidence": "high",
                    "rationale": ["测试夹具使用与当前页数约束一致的教学篇幅建议。"],
                    "scope_expansion_units": [],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        submit_teaching_length_recommendation(workspace, recommendation)
    return submit_curriculum_blueprint(workspace, source)


_UNIT_LABELS = {
    "decision_framework": "决策框架",
    "evidence_slot": "证据位置",
    "tradeoffs": "权衡分析",
    "action_plan": "行动计划",
    "scenario": "案例情境",
    "evidence_boundary": "证据边界",
    "decision_process": "决策过程",
    "retrospective": "结果复盘",
    "procedure": "操作步骤",
    "input_output_artifact": "输入与产出物",
    "acceptance_method": "验收方法",
    "concept": "核心概念",
    "mechanism": "运行机制",
    "example": "具体示例",
    "reference_entries": "参考条目",
}


def _section_outline(chapter_number: int, units: list[str]) -> list[dict[str, object]]:
    """Build a minimal reader-visible heading tree for current-version test blueprints."""
    return [
        {
            "number": f"{chapter_number}.{index}",
            "title": _UNIT_LABELS.get(unit, f"{unit} 说明"),
            "children": [],
        }
        for index, unit in enumerate(units, start=1)
    ]


def submit_test_chapter(workspace: Workspace, tmp_path: Path, chapter_number: int) -> Path:
    """Submit one structurally valid test chapter through the public chapter protocol."""
    _, chapter = require_current_chapter_contract(workspace, chapter_number)
    units = chapter.required_units or ["appendix_note"]
    headings = {
        unit: f"{item.number} {item.title}"
        for unit, item in zip(units, chapter.section_outline, strict=True)
    }
    body = f"# {chapter_number}. {chapter.title}\n\n"
    variations = (
        "先还原发生场景，再区分已知事实、个人判断和暂未验证的假设。随后比较两种可行路径的代价，明确下一步只验证最影响结果的变量。",
        "把输入限制写成可检查的条件，说明哪些信息来自材料、哪些结论只是推断。选择方案时记录放弃项，并用可观察结果检验这次选择。",
        "按时间顺序复盘关键决策，找出真正改变方向的证据。若结果不及预期，先定位判断失误还是执行偏差，再提出可重复的修正动作。",
        "将读者需要交付的物件、验收标准和失败信号写清楚。执行后保留反馈记录，使下一轮改进能依据具体现象而非笼统印象。",
    )
    for index, unit in enumerate(units, start=1):
        body += f"## {headings[unit]}\n\n"
        body += (
            f"这一节围绕{headings[unit]}展开具体判断。{variations[(index - 1) % len(variations)]}"
            "完成后回看行动是否产生了预期证据，再决定是否调整方案。\n\n"
        )
    body = body.strip() + "\n"
    objective_anchor = headings[units[0]]
    payload = {
        "learning_objective_anchors": {
            objective: objective_anchor for objective in chapter.learning_objectives
        },
        "unit_anchors": headings,
        "claim_locations": {claim: objective_anchor for claim in chapter.claim_ids},
    }
    source = workspace.root / "runs" / f"chapter-{chapter_number:02d}-source.md"
    compliance = workspace.root / "runs" / f"chapter-{chapter_number:02d}-compliance.json"
    source.write_text(body, encoding="utf-8")
    compliance.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    record_humanize_check(workspace, chapter_number, source)
    chapter_path, _ = submit_chapter(workspace, chapter_number, source, compliance)
    return chapter_path
