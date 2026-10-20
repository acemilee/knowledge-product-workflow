"""已提交旧章只待上下文复审时，不阻断独立后章；真实正文错误仍阻断。"""
import pytest

from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.runtime import inspect_workflow
from knowledge_product_workflow.workflows.authoring import (
    compile_chapter_contracts,
    create_chapter_draft,
)
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_chapter
from tests.test_runtime import _approved_blueprint


@pytest.mark.parametrize("code,message,expected_chapter", [
    ("DEVELOPMENTAL_REPORT_REQUIRED", "结构审核已过期；变化输入：review_context_current", 2),
    ("CHAPTER_COMPLIANCE_STALE", "正文内容已变化", 1),
])
def test_only_external_review_staleness_can_wait_for_independent_production(tmp_path, monkeypatch, code, message, expected_chapter):
    workspace = Workspace.create(tmp_path, "recovery")
    _approved_blueprint(workspace)
    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    submit_test_chapter(workspace, tmp_path, 1)
    (workspace.root / "chapters/chapter-01-sample.approved").write_text("test fixture approval\n", encoding="utf-8")
    blocker = QualityFinding(code=code, severity=Severity.BLOCKER, location="chapter-01", message=message)
    monkeypatch.setattr("knowledge_product_workflow.runtime.validate_current_chapter_compliance",
                        lambda _workspace, path: [blocker] if path.name == "chapter-01.md" else [])
    status = inspect_workflow(workspace)
    assert status.target_chapter_number == expected_chapter
    if expected_chapter == 2:
        assert status.stage == "chapter_drafts_incomplete"
        create_chapter_draft(workspace, 2)
        pending = inspect_workflow(workspace)
        assert pending.target_chapter_number == 1 and pending.stage == "chapter_compliance_pending"
