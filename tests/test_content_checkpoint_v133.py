"""Offline failed-asset isolation through current contracts and public CLI."""

import json

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.chapter_compliance import submit_chapter
from knowledge_product_workflow.cli import app
from knowledge_product_workflow.content_checkpoint import current_content_checkpoint
from knowledge_product_workflow.content_plan import submit_content_plan
from knowledge_product_workflow.developmental import record_developmental_check
from knowledge_product_workflow.diagrams import DiagramInput
from knowledge_product_workflow.humanize import record_humanize_check
from knowledge_product_workflow.presentation import load_or_create_publication_profile
from knowledge_product_workflow.runtime import create_orchestration_work_order, inspect_workflow
from knowledge_product_workflow.user_decisions import (
    UserDecisionTarget,
    UserResponseDecision,
    record_user_decision,
    request_user_decision,
)
from knowledge_product_workflow.workflows.authoring import (
    compile_chapter_contracts,
    create_chapter_draft,
    require_current_chapter_contract,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint
from tests.test_chapter_compliance import _approve, _mapping
from tests.test_structure_review_v133 import simulated_review


def _fixture(tmp_path):
    w = Workspace.create(tmp_path, "isolation")
    brief = create_product_brief(w, "方案判断入门", target_audience="工程师")
    _approve(brief)
    load_or_create_publication_profile(w, language="zh-CN")
    blueprint = submit_test_blueprint(w, brief, titles=("基础判断", "进阶判断"), section_outlines=(
        [{"number": "1.1", "title": "决策框架", "children": []}],
        [{"number": "2.1", "title": "决策框架", "children": []}],
    ))
    _approve(blueprint)
    compile_chapter_contracts(w)
    create_chapter_draft(w, 1)
    source = w.root / "runs/chapter-01-source.md"
    source.write_text(_body(1, "基础判断") + "\n![方案关系](assets/figures/pending-flow.pdf)\n", encoding="utf-8")
    semantic = DiagramInput(diagram_id="pending-flow", diagram_type="flow", caption="方案关系",
                            reader_conclusion="先明确条件，再比较方案。",
                            nodes=[{"node_id": "condition", "label": "明确条件"},
                                   {"node_id": "compare", "label": "比较方案"}],
                            edges=[{"source": "condition", "target": "compare"}])
    (w.root / "runs/diagrams").mkdir(exist_ok=True)
    (w.root / "runs/diagrams/pending-flow.input.json").write_text(semantic.model_dump_json(), encoding="utf-8")
    _plan(w, 1, diagram=True)
    simulated_review(w, source)
    record_developmental_check(w, 1, source)
    record_humanize_check(w, 1, source)
    mapping = w.root / "runs/mapping-01.json"
    mapping.write_text(json.dumps(_mapping(), ensure_ascii=False), encoding="utf-8")
    create_orchestration_work_order(w)
    return w, source, mapping


def _plan(w, number, diagram=False):
    artifact = "方案关系图" if diagram else "方案取舍核对表"
    payload = {"sections": [{"section_id": f"{number}.1", "reader_job": "明确问题边界，并比较方案与条件的对应关系。",
        "opening_mode": "decision", "block_sequence": ["explanation", "diagram"] if diagram else ["explanation", "table"],
        "asset_decision": "diagram" if diagram else "table", "artifact": artifact,
        "asset_rationale": "展示已批准关系，像素资产可在正文之后完成。" if diagram else "本节用短解释完成判断，不需要图表。"}]}
    p = w.root / "runs" / f"plan-{number}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    submit_content_plan(w, number, p)


def _body(number, title):
    return f"""# {number}. {title}

::: {{.kp-chapter-lead}}
本章围绕方案取舍，先辨别问题边界与约束，再把可用证据和行动成本写清楚。读完后，你可以解释条件怎样影响方案，而不是只记住一组抽象名词。
:::

## {number}.1 决策框架

先把问题边界、可用证据和行动成本写清楚，再选择工具或方案。这个顺序能让读者把抽象术语放进具体决策中，而不是只记住一组名词。面对信息不完整的情况，应当保留假设、说明验证方式，并根据风险调整下一步动作。说明当前选择与证据的对应关系，记录仍需核实的条件，并在获得新信息后复核结论。
"""


def _checkpoint(w, source, mapping):
    result = CliRunner().invoke(app, ["checkpoint-chapter-content", "1", "--workspace", str(w.root),
                                      "--from", str(source), "--compliance", str(mapping)])
    assert result.exit_code == 0, result.output


@pytest.mark.requires_pdf_tools
def test_failed_figure_releases_body_lease_and_next_chapter_can_be_written(tmp_path):
    w, source, mapping = _fixture(tmp_path)
    old_lease = json.loads((w.root / "runs/orchestration/leases/chapter-01.json").read_text(encoding="utf-8"))["lease_id"]
    _checkpoint(w, source, mapping)
    record = current_content_checkpoint(w, 1)
    assert record and record["pending_assets"] == ["pending-flow"]
    assert not record["publishable"]
    events = [json.loads(line) for line in (w.root / "runs/events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["event_type"] == "chapter_lease_released" and e["payload"]["lease_id"] == old_lease for e in events)
    assert inspect_workflow(w).stage == "sample_awaiting_approval"
    request_user_decision(w, UserDecisionTarget.SAMPLE)
    record_user_decision(w, UserDecisionTarget.SAMPLE, UserResponseDecision.APPROVE,
                         approved_by="测试用户", response="隔离测试：图仍待办，只批准当前正文和继续生产。")
    approval = CliRunner().invoke(app, ["approve-sample", "--workspace", str(w.root), "--by", "测试用户"])
    assert approval.exit_code == 0, approval.output
    assert inspect_workflow(w).stage == "chapter_drafts_incomplete"
    create_chapter_draft(w, 2)
    later = w.root / "runs/chapter-02-source.md"
    later.write_text(_body(2, "进阶判断") + """

以下为假设案例：团队需要在两个方案之间选择。方案A满足当前已经明确的约束，方案B仍缺少一项判断依据；这不是实际项目结果，也不能推出B一定较差。先保存当前条件与证据，再判断哪一项缺口可能改变选择。

| 方案 | 已知条件 | 待核实事项 |
| --- | --- | --- |
| A | 满足当前明确的边界 | 实施代价仍需确认 |
| B | 能回应部分需求 | 关键证据尚未补齐 |

这个表仅归纳当前案例中的信息。使用时逐行检查条件是否来自输入，缺项明确保留；如果范围或证据变了，应先更新表，再重新比较选择，不能把暂时的优势写成永久结论。

1. 对照原始条件，定位每一项选择依据。
2. 优先核实可能改变取舍的缺项。
3. 记录本次选择与仍未解决的边界。

复盘时检查：输入范围、证据缺口、实际采用的条件以及下一步行动是否彼此对应。只有取得新证据，才据此修正原结论；保留此前记录能解释为什么发生修改。这是用于学习判断过程的限定案例，不替代真实项目验证。
""", encoding="utf-8")
    _plan(w, 2)
    simulated_review(w, later, number=2)
    record_developmental_check(w, 2, later)
    record_humanize_check(w, 2, later)
    _, contract = require_current_chapter_contract(w, 2)
    mapped = {"learning_objective_anchors": {key: "2.1 决策框架" for key in contract.learning_objectives},
              "unit_anchors": {key: "2.1 决策框架" for key in contract.required_units}, "claim_locations": {}}
    mp = w.root / "runs/mapping-02.json"
    mp.write_text(json.dumps(mapped, ensure_ascii=False), encoding="utf-8")
    submit_chapter(w, 2, later, mp)
    assert inspect_workflow(w).stage == "chapter_assets_pending"
    result = CliRunner().invoke(app, ["publish", "--workspace", str(w.root)])
    assert result.exit_code != 0
    assert not (w.root / "delivery/delivery-manifest.yaml").exists()
    from knowledge_product_workflow.publishing import render_review_preview
    preview = render_review_preview(w)
    assert preview.is_file() and preview.is_relative_to(w.root / "runs/review-preview")
    manifest = json.loads((preview.parent / "preview-manifest.json").read_text(encoding="utf-8"))
    assert manifest["preview_only"] and not manifest["publishable"]
    assert render_review_preview(w) == preview


def test_checkpoint_does_not_defer_body_errors_or_reuse_changed_body(tmp_path):
    w, source, mapping = _fixture(tmp_path)
    original = source.read_text(encoding="utf-8")
    source.write_text(original.replace("## 1.1 决策框架", "## 1.9 错误标题"), encoding="utf-8")
    result = CliRunner().invoke(app, ["checkpoint-chapter-content", "1", "--workspace", str(w.root),
                                    "--from", str(source), "--compliance", str(mapping)])
    assert result.exit_code != 0
    assert current_content_checkpoint(w, 1) is None
    source.write_text(original, encoding="utf-8")
    create_orchestration_work_order(w)
    _checkpoint(w, source, mapping)
    canonical = w.root / "chapters/chapter-01.md"
    canonical.write_text(canonical.read_text(encoding="utf-8") + "新内容未审核。\n", encoding="utf-8")
    assert current_content_checkpoint(w, 1) is None


def test_complete_review_preview_still_refuses_a_missing_later_chapter(tmp_path):
    from knowledge_product_workflow.publishing import render_review_preview
    w, source, mapping = _fixture(tmp_path)
    _checkpoint(w, source, mapping)
    with pytest.raises(ValueError, match="第2章正文尚未就绪"):
        render_review_preview(w)


@pytest.mark.parametrize("changed_body", [False, True])
@pytest.mark.requires_d2
def test_repaired_asset_submission_preserves_only_unchanged_sample_approval(tmp_path, changed_body):
    """真实矢量资产＋模拟风格观察，只验证恢复闭环，不声称AI风格验收。"""
    from knowledge_product_workflow.diagrams import submit_diagram
    from knowledge_product_workflow.illustrations import (
        record_asset_style,
    )
    from tests.test_illustration_style import review

    w, source, mapping = _fixture(tmp_path)
    _checkpoint(w, source, mapping)
    request_user_decision(w, UserDecisionTarget.SAMPLE)
    record_user_decision(w, UserDecisionTarget.SAMPLE, UserResponseDecision.APPROVE,
                         approved_by="测试用户", response="离线故障测试：确认当前正文，图仍待办。")
    runner = CliRunner()
    result = runner.invoke(app, ["approve-sample", "--workspace", str(w.root), "--by", "测试用户"])
    assert result.exit_code == 0, result.output
    marker = w.root / "chapters/chapter-01-sample.approved"
    marker_before = marker.read_bytes()
    create_chapter_draft(w, 2)
    later = w.root / "runs/later.md"
    later.write_text(_body(2, "进阶判断") + "\n\n" + """
| 方案 | 条件 | 待核实 |
| --- | --- | --- |
| A | 当前边界明确 | 实施成本 |
| B | 仍有信息缺口 | 条件适用范围 |

这里是用于学习的假设案例。方案A与方案B的名称、条件和待核实事项均是教学设计，不能据此认定真实项目中的某个方案已经可行。读者先逐行对照输入条件，检查是否存在遗漏，再记录尚未明确的依据；新信息出现后重新判断，保存变化原因。

1. 记录当前输入条件和每项待核实事项。
2. 找到最可能改变选择的缺口，说明如何取得证据。
3. 将实际新证据与先前记录比较，再决定是否修正选择。

检查结果时，保持条件、证据和结论之间的对应关系，不用空泛结论替代缺项。当前不能确定的内容继续标明边界，交接时说明下一步责任；这样的记录用于复核过程，并不保证实施一定成功。
""", encoding="utf-8")
    _plan(w, 2)
    simulated_review(w, later, number=2)
    record_developmental_check(w, 2, later)
    record_humanize_check(w, 2, later)
    _, contract = require_current_chapter_contract(w, 2)
    payload = {"learning_objective_anchors": {k: "2.1 决策框架" for k in contract.learning_objectives},
               "unit_anchors": {k: "2.1 决策框架" for k in contract.required_units}, "claim_locations": {}}
    mapping2 = w.root / "runs/later-compliance.json"
    mapping2.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    submit_chapter(w, 2, later, mapping2)
    assert inspect_workflow(w).stage == "chapter_assets_pending"
    canonical = w.root / "chapters/chapter-01.md"
    submitted_source = canonical
    if changed_body:
        submitted_source = w.root / "runs/changed-sample.md"
        submitted_source.write_text(source.read_text(encoding="utf-8").replace("先把问题边界", "先明确问题边界"), encoding="utf-8")
        simulated_review(w, submitted_source)
        record_developmental_check(w, 1, submitted_source)
        record_humanize_check(w, 1, submitted_source)
    submit_diagram(w, 1, submitted_source, w.root / "runs/diagrams/pending-flow.input.json")
    record_asset_style(w, review(diagram_id="pending-flow", reviewed_by="离线恢复测试模拟观察"))
    create_orchestration_work_order(w)
    result = runner.invoke(app, ["submit-chapter", "1", "--workspace", str(w.root),
                                 "--from", str(submitted_source), "--compliance", str(mapping)])
    assert result.exit_code == 0, result.output
    if changed_body:
        assert not marker.exists()
        assert inspect_workflow(w).stage == "sample_awaiting_approval"
        decision = json.loads((w.root / "runs/interaction/sample.json").read_text(encoding="utf-8"))
        assert decision["state"] == "invalidated"
    else:
        assert marker.read_bytes() == marker_before
        assert inspect_workflow(w).stage == "quality_validation_pending"
    assert not (w.root / "delivery/delivery-manifest.yaml").exists()
