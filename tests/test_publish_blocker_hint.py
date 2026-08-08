"""KP-BUG-001 回归：publish 在内容性 blocker 时给出真实修复动作，而非循环提示。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_product_workflow import runtime
from knowledge_product_workflow.quality import QualityFinding, QualityReport, Severity
from knowledge_product_workflow.runtime import WorkflowAuthorizationError
from knowledge_product_workflow.workspace import Workspace


def _write_blocker_report(workspace: Workspace, codes: list[str] = ("PDF_PAGE_COUNT_NOT_MET",)) -> Path:
    report = QualityReport(
        findings=[
            QualityFinding(
                code=code,
                severity=Severity.BLOCKER,
                location="delivery/book.pdf",
                message="当前 PDF 为 4 页，未达到目标页数约束 6-6 页的下限。",
            )
            for code in codes
        ]
    )
    (workspace.root / "delivery").mkdir(parents=True, exist_ok=True)
    path = workspace.root / "delivery" / "qa-report.json"
    path.write_text(report.model_dump_json(), encoding="utf-8")
    return path


def test_delivery_blocker_details_lists_real_findings(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _write_blocker_report(workspace, ["PDF_PAGE_COUNT_NOT_MET", "DUPLICATE_CONTENT_DETECTED"])

    details = runtime._delivery_blocker_details(workspace)

    assert "PDF_PAGE_COUNT_NOT_MET" in details
    assert "DUPLICATE_CONTENT_DETECTED" in details
    assert "未达到目标页数约束" in details


def test_delivery_blocker_details_handles_missing_or_invalid_report(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    assert runtime._delivery_blocker_details(workspace) == "交付质量报告缺失"

    (workspace.root / "delivery").mkdir(exist_ok=True)
    (workspace.root / "delivery" / "qa-report.json").write_text("{not-json", encoding="utf-8")
    assert runtime._delivery_blocker_details(workspace) == "交付质量报告无法解析"


def test_publish_blocker_hint_breaks_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    _write_blocker_report(workspace)
    monkeypatch.setattr(
        runtime, "authorize_workflow_transition", lambda _workspace, _command: None
    )
    monkeypatch.setattr(
        runtime,
        "inspect_workflow",
        lambda _workspace: SimpleNamespace(stage="delivery_quality_blocked", next_command=""),
    )

    with pytest.raises(WorkflowAuthorizationError) as exc_info:
        runtime.authorize_command(workspace, "publish")

    message = str(exc_info.value)
    assert "PDF_PAGE_COUNT_NOT_MET" in message, "必须列出真实 blocker 而不是循环提示"
    assert "不会改变内容性 blocker" in message, "必须明确重跑 validate-delivery 无济于事"
    assert "请先执行" not in message, "不得再出现循环提示"


def test_publish_blocker_hint_keeps_generic_message_for_other_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    monkeypatch.setattr(
        runtime, "authorize_workflow_transition", lambda _workspace, _command: None
    )
    monkeypatch.setattr(
        runtime,
        "inspect_workflow",
        lambda _workspace: SimpleNamespace(stage="delivery_quality_report_stale", next_command="kp validate-delivery"),
    )

    with pytest.raises(WorkflowAuthorizationError) as exc_info:
        runtime.authorize_command(workspace, "publish")

    assert "请先执行" in str(exc_info.value), "stale/invalid 场景保留原提示（重跑有效）"
