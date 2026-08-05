from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from knowledge_product_workflow.chapter_compliance import (
    ChapterComplianceMappingInput,
    ChapterComplianceSubmission,
    ChapterSubmissionError,
    chapter_compliance_schema,
    create_chapter_compliance_template,
    submit_chapter,
    validate_chapter_submission,
    validate_current_chapter_compliance,
)
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.humanize import record_humanize_check
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.authoring import (
    compile_chapter_contracts,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint


def _approve(path: Path) -> None:
    metadata, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    path.write_text(
        render_markdown_artifact(
            transition(metadata, ArtifactStatus.APPROVED, approved_by="reviewer"), body
        ),
        encoding="utf-8",
    )


def _setup(
    tmp_path: Path, section_outline: list[dict[str, object]] | None = None
) -> tuple[Workspace, Path]:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(workspace, "专业教程", target_audience="工程师")
    _approve(brief)
    blueprint = submit_test_blueprint(
        workspace,
        brief,
        titles=("基础判断",),
        section_outlines=(section_outline,) if section_outline is not None else None,
    )
    _approve(blueprint)
    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    return workspace, workspace.root / "chapters" / "chapter-01-contract.md"


def _body() -> str:
    return """# 1. 基础判断

## 1.1 决策框架

先把问题边界、可用证据和行动成本写清楚，再选择工具或方案。这个顺序能让读者把抽象术语放进具体决策中，而不是只记住一组名词。面对信息不完整的情况，应当保留假设、说明验证方式，并根据风险调整下一步动作。
"""


def _compliance(contract: Path, body: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "chapter_number": 1,
        "chapter_body_sha256": sha256((body.strip() + "\n").encode("utf-8")).hexdigest(),
        "contract_sha256": sha256(contract.read_bytes()).hexdigest(),
        "learning_objective_anchors": {"建立可执行的判断框架": "1.1 决策框架"},
        "unit_anchors": {"decision_framework": "1.1 决策框架"},
        "claim_locations": {},
    }


def _mapping() -> dict[str, object]:
    return {
        "learning_objective_anchors": {"建立可执行的判断框架": "1.1 决策框架"},
        "unit_anchors": {"decision_framework": "1.1 决策框架"},
        "claim_locations": {},
    }


def _workspace_input(workspace: Workspace, name: str, payload: dict[str, object]) -> Path:
    output = workspace.root / "runs" / name
    output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return output


def test_submit_chapter_binds_current_body_contract_and_compliance(tmp_path: Path) -> None:
    workspace, _ = _setup(tmp_path)
    body = _body()
    source = workspace.root / "runs" / "chapter.md"
    compliance = _workspace_input(workspace, "chapter-compliance.json", _mapping())
    source.write_text(body, encoding="utf-8")
    record_humanize_check(workspace, 1, source)

    chapter, record = submit_chapter(workspace, 1, source, compliance)

    assert chapter.is_file()
    assert record.is_file()
    assert validate_current_chapter_compliance(workspace, chapter) == []


def test_heading_order_components_and_ascii_diagrams_are_blocked_but_code_is_ignored(tmp_path: Path) -> None:
    workspace, contract = _setup(tmp_path)
    body = _body() + """
## 1.10 后写的小节

这是一段补充说明，用于确保编号顺序检查能定位真实正文中的错误。

## 1.9 更早的小节

.kp-note

┌────┐
│ 图 │
└────┘

```text
## 1.8 代码内的伪标题
┌────┐
```
"""
    findings = validate_chapter_submission(
        workspace,
        1,
        body.strip() + "\n",
        ChapterComplianceSubmission.model_validate(_compliance(contract, body)),
    )

    codes = {finding.code for finding in findings}
    assert {"HEADING_NUMBER_OUT_OF_ORDER", "INVALID_READER_COMPONENT", "ASCII_DIAGRAM_DETECTED"} <= codes


def test_compliance_becomes_stale_when_reader_body_changes(tmp_path: Path) -> None:
    workspace, _ = _setup(tmp_path)
    body = _body()
    source = workspace.root / "runs" / "chapter.md"
    compliance = _workspace_input(workspace, "chapter-compliance.json", _mapping())
    source.write_text(body, encoding="utf-8")
    record_humanize_check(workspace, 1, source)
    chapter, _ = submit_chapter(workspace, 1, source, compliance)
    metadata, submitted_body = parse_markdown_artifact(chapter.read_text(encoding="utf-8"))
    chapter.write_text(render_markdown_artifact(metadata, submitted_body + "\n新增句子。\n"), encoding="utf-8")

    assert "CHAPTER_COMPLIANCE_STALE" in {
        finding.code for finding in validate_current_chapter_compliance(workspace, chapter)
    }


def test_compliance_template_exposes_only_semantic_fields_and_current_context(tmp_path: Path) -> None:
    workspace, _ = _setup(tmp_path)
    source = workspace.root / "runs" / "chapter.md"
    source.write_text(_body(), encoding="utf-8")

    template, context = create_chapter_compliance_template(workspace, 1, source)

    payload = json.loads(template.read_text(encoding="utf-8"))
    current = json.loads(context.read_text(encoding="utf-8"))
    assert set(payload) == {"learning_objective_anchors", "unit_anchors", "claim_locations"}
    assert payload["learning_objective_anchors"] == {"建立可执行的判断框架": ""}
    assert current["current_heading_candidates"] == ["1. 基础判断", "1.1 决策框架"]
    assert current["expected_section_outline"] == [
        {"number": "1.1", "title": "决策框架", "children": []}
    ]
    assert "chapter_body_sha256" not in chapter_compliance_schema()["fields"]


def test_submit_chapter_rejects_legacy_internal_proof_with_one_migration_diagnostic(tmp_path: Path) -> None:
    workspace, contract = _setup(tmp_path)
    source = workspace.root / "runs" / "chapter.md"
    source.write_text(_body(), encoding="utf-8")
    legacy = _workspace_input(workspace, "legacy.json", _compliance(contract, _body()))

    with pytest.raises(ChapterSubmissionError, match="CHAPTER_COMPLIANCE_LEGACY_INPUT") as raised:
        submit_chapter(workspace, 1, source, legacy)

    diagnostic = workspace.root / raised.value.diagnostic_path
    assert diagnostic.is_file()
    assert json.loads(diagnostic.read_text(encoding="utf-8"))["next_command"].startswith(
        "kp chapter-compliance-template 1"
    )


def test_submit_chapter_rejects_unknown_mapping_key_without_pydantic_field_dump(tmp_path: Path) -> None:
    workspace, _ = _setup(tmp_path)
    source = workspace.root / "runs" / "chapter.md"
    source.write_text(_body(), encoding="utf-8")
    malformed = _workspace_input(workspace, "malformed.json", {**_mapping(), "unit_mappings": {}})

    with pytest.raises(ChapterSubmissionError, match="CHAPTER_COMPLIANCE_INPUT_INVALID") as raised:
        submit_chapter(workspace, 1, source, malformed)

    assert "extra_forbidden" not in str(raised.value)


def test_mapping_input_disallows_internal_proof_fields() -> None:
    with pytest.raises(ValueError):
        ChapterComplianceMappingInput.model_validate({**_mapping(), "chapter_body_sha256": "a" * 64})


@pytest.mark.parametrize(
    ("replacement", "expected_code"),
    [
        ("## 1.2 决策框架", "HEADING_NUMBER_GAP"),
        (
            (
                "## 1.1 决策框架\n\n"
                "第一节先说明最小可行的决策边界。\n\n"
                "## 1.3 补充说明"
            ),
            "HEADING_NUMBER_GAP",
        ),
        (
            (
                "## 1.1 决策框架\n\n"
                "第一节先说明最小可行的决策边界。\n\n"
                "## 1.1 重复小节"
            ),
            "HEADING_NUMBER_OUT_OF_ORDER",
        ),
        ("## 1.1 被替换的标题", "HEADING_OUTLINE_TITLE_MISMATCH"),
        ("## 1.4 未声明小节", "HEADING_OUTLINE_ITEM_UNDECLARED"),
    ],
)
def test_numbered_heading_contract_rejects_gaps_drift_and_undeclared_items(
    tmp_path: Path, replacement: str, expected_code: str
) -> None:
    workspace, contract = _setup(tmp_path)
    body = _body().replace("## 1.1 决策框架", replacement)

    findings = validate_chapter_submission(
        workspace,
        1,
        body.strip() + "\n",
        ChapterComplianceSubmission.model_validate(_compliance(contract, body)),
    )

    assert expected_code in {finding.code for finding in findings}


def test_numbered_heading_contract_rejects_missing_blueprint_item(tmp_path: Path) -> None:
    workspace, contract = _setup(tmp_path)
    body = _body().replace("## 1.1 决策框架\n\n", "")

    findings = validate_chapter_submission(
        workspace,
        1,
        body.strip() + "\n",
        ChapterComplianceSubmission.model_validate(_compliance(contract, body)),
    )

    assert "HEADING_OUTLINE_ITEM_MISSING" in {finding.code for finding in findings}


def test_numbered_heading_contract_accepts_nested_outline_and_ignores_code_fence(tmp_path: Path) -> None:
    outline = [
        {
            "number": "1.1",
            "title": "设计基础",
            "children": [{"number": "1.1.1", "title": "输入边界", "children": []}],
        },
        {"number": "1.2", "title": "后续动作", "children": []},
    ]
    workspace, contract = _setup(tmp_path, outline)
    body = """# 1. 基础判断

## 1.1 设计基础

先明确问题的决策边界、依赖条件和读者需要取得的产出，再讨论任何具体工具。这样能避免将工具名称误当作方案本身，也能把后续验证保持在可观察的范围内。

### 1.1.1 输入边界

输入应区分已核验事实、待验证假设和个人偏好；三者采用不同的记录和复核方式。只要边界仍然含混，下一步就不应承诺确定性结论。

## 1.2 后续动作

先选择一项最能降低不确定性的验证动作，记录结果，再依据结果调整方案。这样读者可以复用过程，而不是照抄一个脱离场景的结论。

```markdown
## 1.3 代码块中的伪标题不属于正文结构
```
"""
    mapping = _compliance(contract, body)
    mapping["learning_objective_anchors"] = {"建立可执行的判断框架": "1.1 设计基础"}
    mapping["unit_anchors"] = {"decision_framework": "1.1 设计基础"}

    findings = validate_chapter_submission(
        workspace,
        1,
        body.strip() + "\n",
        ChapterComplianceSubmission.model_validate(mapping),
    )

    assert findings == []


def test_numbered_heading_contract_reports_outline_order_mismatch(tmp_path: Path) -> None:
    outline = [
        {"number": "1.1", "title": "第一节", "children": []},
        {"number": "1.2", "title": "第二节", "children": []},
    ]
    workspace, contract = _setup(tmp_path, outline)
    body = """# 1. 基础判断

## 1.2 第二节

先写第二节以复现标题顺序与蓝图不一致的提交事故。内容需要足够完整，避免最小正文长度检查掩盖本测试要验证的结构错误。

## 1.1 第一节

再写第一节。这里保留额外说明，使正文具有足够的可读字符，同时只触发标题树的连续性和顺序检查。
"""
    mapping = _compliance(contract, body)
    mapping["learning_objective_anchors"] = {"建立可执行的判断框架": "1.1 第一节"}
    mapping["unit_anchors"] = {"decision_framework": "1.1 第一节"}

    findings = validate_chapter_submission(
        workspace,
        1,
        body.strip() + "\n",
        ChapterComplianceSubmission.model_validate(mapping),
    )

    assert {"HEADING_NUMBER_GAP", "HEADING_NUMBER_OUT_OF_ORDER", "HEADING_OUTLINE_ORDER_MISMATCH"} <= {
        finding.code for finding in findings
    }
