"""交付前发现跨章重复，不以旧汇编件代替当前源稿。"""

import pytest

from knowledge_product_workflow.workflows.validation import run_validation
from knowledge_product_workflow.workspace import Workspace

TABLE = "\n".join([
    "| 约束卡 | 使用条件 | 具体核对动作 |", "| --- | --- | --- |",
    *[f"| 阶段{i} | 在明确输入和约束后启动第{i}类任务 | 核对本步骤的证据、失败条件和输出记录{i} |" for i in range(8)],
])


def _book(tmp_path, second=TABLE):
    workspace = Workspace.create(tmp_path, "preflight-regression")
    for n, body in [(1, TABLE), (2, second)]:
        (workspace.root / f"chapters/chapter-{n:02d}.md").write_text(
            f"---\nbook_id: preflight-regression\nartifact_type: chapter\nstatus: in_production\n"
            f"version: 1\ndepends_on: []\napproved_by: null\n---\n# {n}. 任务{n}\n\n{body}\n",
            encoding="utf8",
        )
    return workspace


def test_source_validation_finds_cross_chapter_duplicate_before_assembly(tmp_path):
    workspace = _book(tmp_path)
    _, report = run_validation(workspace)
    duplicate = [f for f in report.findings if f.code == "DUPLICATE_CONTENT_DETECTED"]
    assert duplicate, "两章单独合法不代表汇编后没有跨章重复"
    assert "chapter-02.md" in duplicate[0].location
    assert "chapter-01.md" in duplicate[0].message
    assert not (workspace.root / "delivery/book.md").exists()


def test_early_duplicate_check_uses_current_sources_not_old_delivery(tmp_path):
    workspace = _book(tmp_path)
    (workspace.root / "delivery/book.md").write_text("# 旧稿\n没有重复\n", encoding="utf8")
    _, report = run_validation(workspace)
    assert any(f.code == "DUPLICATE_CONTENT_DETECTED" for f in report.findings)


def test_cross_chapter_reference_index_is_not_a_duplicate(tmp_path):
    workspace = _book(tmp_path, "| 阶段 | 详细用法位置 |\n| --- | --- |\n| 设计 | 见1.1节 |")
    _, report = run_validation(workspace)
    assert not any(f.code == "DUPLICATE_CONTENT_DETECTED" for f in report.findings)


def test_preflight_defers_only_exact_human_code_and_preserves_final_report(tmp_path, monkeypatch):
    from knowledge_product_workflow.delivery_preflight import (
        current_delivery_preflight,
        run_delivery_preflight,
    )
    from knowledge_product_workflow.quality import QualityFinding
    from knowledge_product_workflow.workflows import validation
    workspace = _book(tmp_path, "独立索引")
    old = workspace.root / "delivery/qa-report.json"
    old.write_bytes(b'old-quality-envelope')
    findings = [QualityFinding(code=c, severity="blocker", location="delivery/book.pdf", message=c)
                for c in ["PDF_DIAGRAM_MANUAL_VISUAL_REVIEW_REQUIRED", "PDF_UNKNOWN_BLOCKER"]]
    monkeypatch.setattr(validation, "collect_delivery_findings", lambda _: findings)
    report = run_delivery_preflight(workspace)
    assert not report.ready_for_human_review
    assert [f.code for f in report.findings] == ["PDF_UNKNOWN_BLOCKER"]
    assert len(report.pending_human_items) == 1
    assert old.read_bytes() == b'old-quality-envelope'
    assert current_delivery_preflight(workspace) == report
    assert "publishable" not in report.model_dump()


def test_preflight_freshness_excludes_only_later_human_receipt(tmp_path, monkeypatch):
    import os

    from knowledge_product_workflow.delivery_preflight import (
        current_delivery_preflight,
        run_delivery_preflight,
    )
    from knowledge_product_workflow.workflows import validation
    workspace = _book(tmp_path, "独立索引")
    monkeypatch.setattr(validation, "collect_delivery_findings", lambda _: [])
    report = run_delivery_preflight(workspace)
    (workspace.root / "runs/pdf-visual-review.json").write_text('{}', encoding='utf8')
    assert current_delivery_preflight(workspace) == report
    source = workspace.root / "chapters/chapter-02.md"
    stamp = source.stat()
    source.write_text(source.read_text(encoding='utf8') + '\nchanged', encoding='utf8')
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert current_delivery_preflight(workspace) is None


def test_missing_preflight_cannot_record_human_approval(tmp_path):
    from knowledge_product_workflow.delivery_preflight import require_delivery_preflight
    with pytest.raises(ValueError, match="delivery-preflight"):
        require_delivery_preflight(_book(tmp_path))


def test_nested_current_clear_png_invalidates_preflight_without_binding_old_rasters(tmp_path, monkeypatch):
    import json
    import os

    from knowledge_product_workflow.delivery_preflight import (
        current_delivery_preflight,
        run_delivery_preflight,
    )
    from knowledge_product_workflow.workflows import validation
    w = _book(tmp_path, '独立索引')
    image = w.root/'runs/visual-audit-pages/current/page-001.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'owned-current-image')
    history = w.root/'runs/visual-audit-pages/old/page-001.png'
    history.parent.mkdir(parents=True)
    history.write_bytes(b'owned-old-image')
    (w.root/'runs/visual-audit-report.json').write_text(json.dumps({'pages':[
        {'high_resolution_image':image.relative_to(w.root).as_posix()}]}),encoding='utf8')
    monkeypatch.setattr(validation,'collect_delivery_findings',lambda _:[])
    before = run_delivery_preflight(w)
    assert image.relative_to(w.root).as_posix() in {i.path for i in before.inputs}
    history.write_bytes(b'unrelated historical change')
    assert current_delivery_preflight(w)==before
    stamp=image.stat()
    image.write_bytes(b'owned-corrupted-current-image')
    os.utime(image,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    assert current_delivery_preflight(w) is None


def test_controlled_preflight_closes_its_order_then_routes_to_human(tmp_path, monkeypatch):
    """Real CLI/work-order wiring with synthetic domain findings, no PDF toolchain."""
    from typer.testing import CliRunner

    from knowledge_product_workflow import cli, runtime
    from knowledge_product_workflow.delivery_preflight import current_delivery_preflight
    from knowledge_product_workflow.quality import QualityFinding
    from knowledge_product_workflow.workflows import validation
    from tests.test_chapter_content_plan import _approved_workspace
    workspace = _approved_workspace(tmp_path)
    def status(w):
        ready = current_delivery_preflight(w)
        return runtime._status(
            stage="pdf_manual_review_pending" if ready else "delivery_preflight_pending",
            blocked_by=[], recommended_skill="kp-create" if ready else "kp-quality-editor",
            next_command="", artifacts={}, declared_claim_ids=[], events=[])
    monkeypatch.setattr(runtime, "inspect_workflow", status)
    monkeypatch.setattr(validation, "collect_delivery_findings", lambda _: [QualityFinding(
        code="PDF_DIAGRAM_MANUAL_VISUAL_REVIEW_REQUIRED", severity="blocker", location="book.pdf", message="实际本人仍须确认")])
    order = runtime.read_orchestration_work_order(workspace, runtime.create_orchestration_work_order(workspace))
    assert order.allowed_commands == ["delivery-preflight"]
    result = CliRunner().invoke(cli.app, ['delivery-preflight', '--workspace', str(workspace.root), '--json'])
    assert result.exit_code == 0, result.output
    assert runtime.read_orchestration_work_order(workspace, order.work_order_id).lifecycle.value == "completed"
    assert status(workspace).stage == "pdf_manual_review_pending"
    assert runtime._macro_phase_for_stage("delivery_preflight_pending").value == "delivery"
    assert not (workspace.root / 'runs/pdf-visual-review.json').exists()
    assert not (workspace.root / 'delivery/delivery-manifest.yaml').exists()
