"""宿主模拟黄金路径自检：真实 CLI 端到端 + P0 恢复闭环。

模拟宿主（按 kp-create 提示词行动）在隔离工作区用真实 CLI 完成
定义 → 架构 → 生产 → 质量 → 交付 全流程，并验证交付质量报告过期后
的恢复工单闭环。需要本机 Quarto / XeLaTeX / 中文字体 / D2 / Chrome
（`kp doctor` 全绿）；环境缺失时自动跳过，CI（windows-latest 无 PDF
工具链）不会误报。

本地运行（含报告输出）：

    uv run pytest tests/test_skill_self_check.py -v -s
    $env:KP_SELF_CHECK_REPORT_DIR = "tmp/self-check-report"; uv run pytest tests/test_skill_self_check.py -v -s

本测试不发起任何网络请求，也不调用外部模型 API。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from pypdf import PdfReader
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.quality import QualityReport
from knowledge_product_workflow.runtime import (
    inspect_workflow,
    read_orchestration_work_order,
    read_run_intent,
    require_delivery_orchestration_ready,
)
from knowledge_product_workflow.workflows.product_brief import brief_contract_fingerprints
from knowledge_product_workflow.workspace import Workspace

BOOK_ID = "self-check-minimal-book"
INTERNAL_PROMPT = """创建一份面向中文初学者的决策树分类器最小教程。
正文只保留解释信息熵、选择切分和验证过拟合所需内容；用一个真实流程图、代码、公式和表格支撑教学，不堆叠口号或重复小结。"""
EXPECTED_PDF_PAGES = 4


@lru_cache(maxsize=1)
def _environment_ready() -> bool:
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--json"])
    if result.exit_code != 0:
        return False
    checks = json.loads(result.output)
    required = ["quarto", "xelatex", "pingfang_sc", "pingfang_sc_semibold", "inter", "jetbrains_mono", "d2", "diagram_browser"]
    return all(checks.get(name, {}).get("available") for name in required)


pytestmark = pytest.mark.skipif(
    not _environment_ready(),
    reason="需要本机 Quarto/XeLaTeX/中文字体/D2/Chrome（kp doctor 全绿）",
)


def _invoke(runner: CliRunner, args: list[str]) -> str:
    result = runner.invoke(app, args)
    if result.exit_code != 0:
        raise AssertionError(f"命令失败：{' '.join(args)}\n{result.output}\n{result.exception!r}")
    return result.output


def _decide_and_approve(runner: CliRunner, workspace: Path, target: str, artifact: Path) -> None:
    _invoke(runner, ["request-user-decision", target, "--workspace", str(workspace)])
    _invoke(
        runner,
        [
            "record-user-decision",
            target,
            "--workspace",
            str(workspace),
            "--decision",
            "approve",
            "--response",
            "批准",
            "--by",
            "用户",
        ],
    )
    _invoke(runner, ["approve", str(artifact), "--workspace", str(workspace)])


def _write_brief_input(workspace: Workspace) -> Path:
    payload = {
        "topic": "决策树分类器的最小验证闭环",
        "reader_request": f"面向能完成基础实验的中文入门读者，制作决策树分类器的最小验证闭环教程，交付Markdown和PDF，总计{EXPECTED_PDF_PAGES}页；解释熵如何选择切分，区分训练集拟合与验证集泛化。",
        "target_audience": "需要理解机器学习分类器基础并能完成一次可验证实验的中文初学者",
        "target_pages": EXPECTED_PDF_PAGES,
        "language": "中文",
        "layout_profile": "A4 标准版式",
        "audience_level": "入门",
        "delivery_format": "Markdown + PDF",
        "review_policy": "deterministic_only",
        "subtitle": "从一次切分到可复现的验证",
    }
    path = workspace.root / "runs" / "brief-input.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _write_length_recommendation(workspace: Workspace) -> Path:
    brief = workspace.root / "planning" / "product-brief.md"
    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    payload = {
        "schema_version": 1,
        "brief_content_sha256": brief_contract_fingerprints(body)["content_contract_sha256"],
        "brief_version": metadata.version,
        "recommended_min_pages": EXPECTED_PDF_PAGES,
        "recommended_max_pages": EXPECTED_PDF_PAGES,
        "confidence": "high",
        "rationale": [
            "单章最小教程需要封面、目录和约一至两页正文；四页可以完整呈现一次概念到验证的闭环。"
        ],
        "scope_expansion_units": [],
    }
    path = workspace.root / "runs" / "length-recommendation.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _write_blueprint(workspace: Workspace) -> Path:
    brief = workspace.root / "planning" / "product-brief.md"
    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    payload = {
        "schema_version": 2,
        "brief_sha256": sha256(brief.read_bytes()).hexdigest(),
        "brief_content_sha256": brief_contract_fingerprints(body)["content_contract_sha256"],
        "brief_version": metadata.version,
        "topic": "决策树分类器的最小验证闭环",
        "target_audience": "需要理解机器学习分类器基础并能完成一次可验证实验的中文初学者",
        "audience_level": "beginner",
        "product_mode": "general",
        "interview_answer_policy": None,
        "chapters": [
            {
                "chapter_number": 1,
                "title": "决策树分类器的最小验证闭环",
                "learning_objectives": [
                    "解释信息熵为何可用于选择一次切分。",
                    "用训练集与验证集区分拟合结果和泛化结果。",
                ],
                "scope": "只讲二分类数据上的一次切分、递归训练与验证；不展开剪枝算法和集成学习。",
                "weight": 1,
                "rationale": "一个完整但克制的单章可验证概念、图表、代码、公式和交付质量门。",
                "chapter_archetype": "concept",
                "required_units": ["concept"],
                "optional_units": ["mechanism", "example"],
                "prohibited_units": [],
                "claim_ids": [],
                "section_outline": [
                    {"number": "1.1", "title": "熵如何帮助选择切分", "children": []},
                    {"number": "1.2", "title": "从特征阈值到递归训练", "children": []},
                    {"number": "1.3", "title": "用验证集判断是否过拟合", "children": []},
                ],
            }
        ],
    }
    path = workspace.root / "runs" / "classifier-blueprint.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _write_chapter_source(workspace: Workspace) -> Path:
    source = workspace.root / "runs" / "chapter-01-source.md"
    source.write_text(
        """# 1. 决策树分类器的最小验证闭环

::: {.kp-chapter-lead}
理解一次分类实验，先要分清切分依据、训练过程和验证结果。本章用信息熵说明如何选择候选切分，再以代码和记录表组织实验，让读者判断哪些结果需要继续复核，而不是把代码运行成功当作模型可靠的证明。
:::

## 1.1 熵如何帮助选择切分

二分类样本中，标签越混杂，当前节点越不确定。设正类比例为 $p$，信息熵写作：

$$
H(p) = -p\\log_2p - (1-p)\\log_2(1-p)
$$

候选切分比较的是加权熵是否下降。信息增益 $IG$ 越大，说明这次切分减少的不确定性越多；它仍不是因果解释。

| 节点样本 | 正类比例 | 熵的直观含义 | 下一步 |
| --- | ---: | --- | --- |
| 全部训练样本 | 0.50 | 标签最混杂 | 比较候选特征 |
| 已认证分支 | 0.85 | 标签更集中 | 可继续检查停止条件 |
| 未认证分支 | 0.45 | 仍然混杂 | 尝试其他特征或停止 |

行内变量 `max_depth` 是复杂度上限，不是提高准确率的开关。它应和验证结果一起解释。

## 1.2 从特征阈值到递归训练

训练过程选择信息增益最大的切分，再在子节点继续。最小实验先固定数据切分，避免把代码运行成功误当作模型可靠。

```python
from sklearn.tree import DecisionTreeClassifier

model = DecisionTreeClassifier(max_depth=3, random_state=7)
model.fit(x_train, y_train)
print(model.score(x_valid, y_valid))
```

若类别极不平衡，仅看 `score()` 容易掩盖少数类的错误，应补充混淆矩阵。

## 1.3 用验证集判断是否过拟合

训练集很高、验证集明显较低时，优先限制树深或补充代表性数据；两者都低时先检查特征与数据质量。

流程图固定每次实验应留下的证据：数据切分、参数和验证指标。

::: {.kp-worksheet}
使用说明：填写一次训练与验证结果。

输入：`max_depth`、随机种子和数据切分方式。

| 配置 | 训练指标 | 验证指标 | 判断 |
| --- | ---: | ---: | --- |
| 深度 3 | 填写 | 填写 | 填写 |

输出示例：验证指标明显较低，先限制复杂度再补充数据。

复盘提示：下一次实验只改变一个变量。
:::
""",
        encoding="utf-8",
    )
    return source


def _write_diagram_input(workspace: Workspace) -> Path:
    payload = {
        "diagram_id": "classifier-validation-flow",
        "diagram_type": "flow",
        "direction": "right",
        "caption": "决策树分类器的最小验证顺序",
        "reader_conclusion": "先固定数据切分，再训练模型，最后以验证结果决定是否调整复杂度。",
        "nodes": [
            {"node_id": "split", "label": "固定训练与验证集", "group": None},
            {"node_id": "train", "label": "训练受限深度的树", "group": None},
            {"node_id": "compare", "label": "比较训练与验证指标", "group": None},
            {"node_id": "adjust", "label": "调整复杂度或数据", "group": None},
        ],
        "edges": [
            {"source": "split", "target": "train", "label": "同一随机种子"},
            {"source": "train", "target": "compare", "label": "记录指标"},
            {"source": "compare", "target": "adjust", "label": "泛化差时"},
        ],
    }
    path = workspace.root / "runs" / "classifier-validation-flow.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _write_content_plan(workspace: Workspace) -> Path:
    payload = {
        "sections": [
            {
                "section_id": "1.1",
                "reader_job": "根据熵下降判断一次切分是否值得保留。",
                "opening_mode": "claim",
                "block_sequence": ["explanation", "table"],
                "artifact": "切分比较表",
                "asset_decision": "table",
                "asset_rationale": "表格能比较节点标签状态和下一步动作。",
            },
            {
                "section_id": "1.2",
                "reader_job": "完成一次训练与验证记录。",
                "opening_mode": "procedure",
                "block_sequence": ["explanation", "example"],
                "artifact": "none",
                "asset_decision": "none",
                "asset_rationale": "代码示例直接说明最小训练记录。",
            },
            {
                "section_id": "1.3",
                "reader_job": "识别过拟合信号并复盘下一次实验。",
                "opening_mode": "decision",
                "block_sequence": ["explanation", "worksheet", "diagram"],
                "artifact": "验证记录工作表",
                "asset_decision": "worksheet",
                "asset_rationale": "工作表让读者记录指标与下一步变量。",
            },
        ]
    }
    path = workspace.root / "runs" / "content-plan.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _write_compliance_input(workspace: Workspace) -> Path:
    payload = {
        "learning_objective_anchors": {
            "解释信息熵为何可用于选择一次切分。": "1.1 熵如何帮助选择切分",
            "用训练集与验证集区分拟合结果和泛化结果。": "1.3 用验证集判断是否过拟合",
        },
        "unit_anchors": {"concept": "1.1 熵如何帮助选择切分"},
        "claim_locations": {},
    }
    path = workspace.root / "runs" / "chapter-01-compliance.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _run_golden_path(tmp_path: Path) -> Workspace:
    """按 kp-create 提示词推进到交付就绪：init -> 审批 -> 合约 -> 样章 -> 质量门 -> PDF。"""
    runner = CliRunner()
    _invoke(runner, ["init", BOOK_ID, "--root", str(tmp_path)])
    workspace = Workspace.open(tmp_path / BOOK_ID)
    _invoke(runner, ["start-run-intent", "--workspace", str(workspace.root)])

    brief_input = _write_brief_input(workspace)
    _invoke(runner, ["brief", "--workspace", str(workspace.root), "--from", str(brief_input), "--confirm"])
    _decide_and_approve(runner, workspace.root, "brief", workspace.root / "planning" / "product-brief.md")

    publication = workspace.root / "runs" / "publication-update.yaml"
    publication.write_text(
        "design_family: technical-manual\nreading_context: 课后实验与屏幕阅读\nversion_bump: none\n",
        encoding="utf-8",
    )
    _invoke(runner, ["update-publication", "--workspace", str(workspace.root), "--from", str(publication)])
    _invoke(runner, ["update-publication", "--workspace", str(workspace.root), "--from", str(publication), "--confirm"])

    _invoke(runner, ["length-recommendation-template", "--workspace", str(workspace.root)])
    recommendation = _write_length_recommendation(workspace)
    _invoke(runner, ["submit-length-recommendation", "--workspace", str(workspace.root), "--from", str(recommendation)])
    _invoke(runner, ["outline", "--workspace", str(workspace.root)])
    blueprint = _write_blueprint(workspace)
    _invoke(runner, ["submit-outline", "--workspace", str(workspace.root), "--from", str(blueprint)])
    _decide_and_approve(runner, workspace.root, "blueprint", workspace.root / "planning" / "curriculum-blueprint.md")

    _invoke(runner, ["compile-contracts", "--all", "--workspace", str(workspace.root)])
    _invoke(runner, ["draft", "1", "--workspace", str(workspace.root)])
    chapter_source = _write_chapter_source(workspace)
    _invoke(runner, ["content-plan-template", "1", "--workspace", str(workspace.root)])
    content_plan = _write_content_plan(workspace)
    _invoke(runner, ["submit-content-plan", "1", "--workspace", str(workspace.root), "--from", str(content_plan)])
    _invoke(
        runner,
        [
            "diagram-template",
            "1",
            "--workspace",
            str(workspace.root),
            "--chapter-source",
            str(chapter_source),
            "--diagram-id",
            "classifier-validation-flow",
        ],
    )
    diagram_input = _write_diagram_input(workspace)
    _invoke(
        runner,
        [
            "insert-diagram-reference",
            "1",
            "classifier-validation-flow",
            "--workspace",
            str(workspace.root),
            "--from",
            str(chapter_source),
            "--diagram-input",
            str(diagram_input),
        ],
    )
    _invoke(
        runner,
        [
            "submit-diagram",
            "1",
            "--workspace",
            str(workspace.root),
            "--from",
            str(diagram_input),
            "--chapter-source",
            str(chapter_source),
        ],
    )
    # This is an explicitly simulated golden path, not a real reader acceptance.
    from tests.test_illustration_style import review as simulated_asset_review
    diagram_id = json.loads(diagram_input.read_text(encoding="utf-8"))["diagram_id"]
    _invoke(runner, ["illustration-style-template", diagram_id, "--workspace", str(workspace.root)])
    style_input = workspace.root / "runs/simulated-asset-style.json"
    style_input.write_text(simulated_asset_review(diagram_id=diagram_id, reviewed_by="单元测试模拟风格观察").model_dump_json(), encoding="utf-8")
    _invoke(runner, ["record-illustration-style-check", "--workspace", str(workspace.root), "--from", str(style_input)])
    from tests.test_structure_review_v133 import simulated_review
    simulated_review(workspace, chapter_source)
    _invoke(runner, ["developmental-check", "1", "--workspace", str(workspace.root), "--from", str(chapter_source)])
    _invoke(runner, ["humanize-check", "1", "--workspace", str(workspace.root), "--from", str(chapter_source)])
    _invoke(
        runner,
        ["chapter-compliance-template", "1", "--workspace", str(workspace.root), "--from", str(chapter_source)],
    )
    compliance = _write_compliance_input(workspace)
    _invoke(
        runner,
        [
            "submit-chapter",
            "1",
            "--workspace",
            str(workspace.root),
            "--from",
            str(chapter_source),
            "--compliance",
            str(compliance),
        ],
    )
    _invoke(runner, ["request-user-decision", "sample", "--workspace", str(workspace.root)])
    _invoke(
        runner,
        [
            "record-user-decision",
            "sample",
            "--workspace",
            str(workspace.root),
            "--decision",
            "approve",
            "--response",
            "批准并继续完成全书，直到交付",
            "--by",
            "用户",
        ],
    )
    _invoke(runner, ["approve-sample", "--workspace", str(workspace.root)])
    _invoke(runner, ["validate", "--workspace", str(workspace.root)])
    _invoke(runner, ["assemble", "--workspace", str(workspace.root)])
    _invoke(runner, ["render-pdf", "--workspace", str(workspace.root)])
    return workspace


def test_host_simulation_golden_path_and_p0_recovery(tmp_path: Path) -> None:
    workspace = _run_golden_path(tmp_path)
    runner = CliRunner()
    pdf = workspace.root / "delivery" / "book.pdf"

    assert pdf.is_file(), "PDF 未生成"
    assert len(PdfReader(pdf).pages) == EXPECTED_PDF_PAGES

    # 首次交付校验：视觉复核 -> validate-delivery（进入交付质量报告有效状态）。
    initial_status = inspect_workflow(workspace)
    if initial_status.stage == "pdf_visual_audit_pending":
        _invoke(runner, ["audit-pdf-visual", "--workspace", str(workspace.root)])
    if inspect_workflow(workspace).stage in {"delivery_validation_pending", "pdf_manual_review_pending"}:
        _invoke(runner, ["record-pdf-visual-review", "--workspace", str(workspace.root), "--by", "用户", "--confirm-manual-coverage", "--confirm-content", "--response", "单元测试模拟用户已检查当前全部页面并批准内容与版式；不是真实产品批准。"])
        _invoke(runner, ["validate-delivery", "--workspace", str(workspace.root)])
        before_text = "".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
        with pdf.open("ab") as handle:
            handle.write(b"\n% kp-self-check-stale-recovery-probe\n")
        after_text = "".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
        assert before_text == after_text, "恢复探针意外改变了 PDF 读者文本"

    # P0 恢复闭环：PDF 字节改变后先修复预览，再重新校验交付质量。
    stale_status = inspect_workflow(workspace)
    assert stale_status.stage == "pdf_preview_repair_pending", stale_status.stage
    live_orders = [
        read_orchestration_work_order(workspace, path)
        for path in (workspace.root / "runs" / "orchestration" / "work-orders").glob("*.json")
        if read_orchestration_work_order(workspace, path).lifecycle.value in {"pending", "active"}
    ]
    assert len(live_orders) == 1, f"报告过期时的活跃工单数异常：{len(live_orders)}"
    assert live_orders[0].allowed_commands == ["publish"]
    previous_work_order_id = live_orders[0].work_order_id
    _invoke(
        runner,
        [
            "cancel-orchestration-work-order",
            "--workspace",
            str(workspace.root),
            "--work-order-id",
            previous_work_order_id,
            "--reason",
            "自检：将发布工单切换到 PDF 预览恢复工单",
        ],
    )
    _invoke(runner, ["orchestration-plan", "--workspace", str(workspace.root)])
    recovery_status = inspect_workflow(workspace)
    assert recovery_status.current_work_order is not None, "恢复阶段未创建当前工单"
    recovery_order = read_orchestration_work_order(workspace, recovery_status.current_work_order)
    assert recovery_order.allowed_commands == ["repair-pdf-preview"], recovery_order.allowed_commands
    _invoke(runner, ["repair-pdf-preview", "--workspace", str(workspace.root)])
    if inspect_workflow(workspace).stage == "pdf_visual_audit_pending":
        _invoke(runner, ["audit-pdf-visual", "--workspace", str(workspace.root)])
    _invoke(runner, ["record-pdf-visual-review", "--workspace", str(workspace.root), "--by", "用户", "--confirm-manual-coverage", "--confirm-content", "--response", "单元测试模拟用户已检查当前全部页面并批准内容与版式；不是真实产品批准。"])
    assert inspect_workflow(workspace).stage == "delivery_quality_report_stale"
    require_delivery_orchestration_ready(workspace, "validate-delivery")

    # 当前预览恢复后重新视觉复核，再校验并发布。
    _invoke(runner, ["validate-delivery", "--workspace", str(workspace.root)])
    _invoke(runner, ["publish", "--workspace", str(workspace.root)])

    # 终局断言：8 项自动检查全部通过。
    final_status = inspect_workflow(workspace)
    run_intent = read_run_intent(workspace)
    work_orders = [
        read_orchestration_work_order(workspace, path)
        for path in (workspace.root / "runs" / "orchestration" / "work-orders").glob("*.json")
    ]
    pending = [order.work_order_id for order in work_orders if order.lifecycle.value in {"pending", "active"}]
    proof_missing = [
        order.work_order_id
        for order in work_orders
        if order.lifecycle.value in {"completed", "failed", "cancelled", "superseded"}
        and order.completion_proof is None
    ]
    book_markdown = (workspace.root / "delivery" / "book.md").read_text(encoding="utf-8")
    forbidden = [token for token in ("{.kp-", "claim-", "来源台账", "┌", "└") if token in book_markdown]
    report = QualityReport.model_validate_json(
        (workspace.root / "delivery" / "qa-report.json").read_text(encoding="utf-8")
    )
    pages = len(PdfReader(pdf).pages)
    checks = {
        "workflow_completed": final_status.stage == "completed",
        "run_intent_completed": run_intent is not None and run_intent.state.value == "completed",
        "no_live_work_orders": not pending,
        "terminal_proofs_complete": not proof_missing,
        "recovery_work_order_authorized": recovery_order.allowed_commands == ["repair-pdf-preview"],
        "delivery_report_publishable": report.publishable,
        "reader_content_has_no_internal_leak": not forbidden,
        "pdf_pages_within_target": pages == EXPECTED_PDF_PAGES,
        "v6_publication_profile": "knowledge-product-pdf-v6" in (workspace.root / "delivery" / "publication-metadata.yaml").read_text(encoding="utf-8"),
        "visual_audit_bound": (workspace.root / "runs" / "visual-audit-report.json").is_file(),
        "content_plan_bound": (workspace.root / "chapters" / "chapter-01-content-plan.yaml").is_file(),
        "developmental_report_bound": (workspace.root / "runs" / "editorial" / "chapter-01-developmental-report.json").is_file(),
        "preview_manifest_bound": (workspace.root / "runs" / "pdf-preview" / "preview-manifest.json").is_file(),
    }
    assert all(checks.values()), json.dumps(
        {
            "checks": checks,
            "pending": pending,
            "proof_missing": proof_missing,
            "forbidden": forbidden,
        },
        ensure_ascii=False,
        indent=2,
    )

    summary = {
        "internal_prompt": INTERNAL_PROMPT,
        "pages": pages,
        "checks": checks,
        "work_order_count": len(work_orders),
        "recovery_work_order": recovery_order.work_order_id,
    }
    report_dir = os.environ.get("KP_SELF_CHECK_REPORT_DIR")
    if report_dir:
        output = Path(report_dir)
        output.mkdir(parents=True, exist_ok=True)
        (output / "SELF_CHECK_SUMMARY.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (output / "SELF_CHECK_REPORT.md").write_text(
            "# KP 自检报告\n\n"
            "## 结论\n\n"
            "**通过。** 宿主模拟黄金路径与 P0 恢复闭环均完成。\n\n"
            "## 自动检查\n\n"
            + "\n".join(f"- `{name}`：通过" for name, passed in checks.items() if passed)
            + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
