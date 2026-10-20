"""Sampling is not full-book visual acceptance; test fixtures are not approval."""
import json
from hashlib import sha256

import pytest
from pypdf import PdfReader, PdfWriter
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.pdf_preview import render_pdf_preview_set
from knowledge_product_workflow.pdf_visual import record_manual_pdf_visual_review
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.visual_audit import (
    VisualObservation,
    VisualPageMetric,
    _sample_pages,
    audit_path,
    audit_pdf_visual,
    current_visual_audit,
    record_visual_observations,
)
from tests.test_visual_audit import _workspace
from tests.visual_support import bind_fixture_observations


def test_deep_workspace_keeps_clear_previews_and_readonly_cache(tmp_path):
    from knowledge_product_workflow.pdf_preview import render_pdf_first_page, resolve_pdftoppm
    if resolve_pdftoppm() is None:
        pytest.skip("需要实际Poppler；跳过不是深路径清晰预览通过")
    parent = tmp_path / ("a" * 70) / ("b" * 70) / ("c" * 70)
    parent.mkdir(parents=True)
    workspace = _workspace(parent)
    assert len(str(workspace.root)) > 260
    pdf = workspace.root / "delivery/book.pdf"
    assert render_pdf_first_page(pdf, workspace.root / "runs/first-page").is_file()
    report = audit_pdf_visual(workspace)
    assert report.sampled_pages == [1, 2, 3]
    assert all(page.high_resolution_dpi == 160 and
        (workspace.root / page.high_resolution_image).is_file() for page in report.pages)
    report_bytes = audit_path(workspace).read_bytes()
    events = (workspace.root / "runs/events.jsonl").read_bytes()
    assert current_visual_audit(workspace) is not None
    assert audit_pdf_visual(workspace).created_at == report.created_at
    assert audit_path(workspace).read_bytes() == report_bytes
    assert (workspace.root / "runs/events.jsonl").read_bytes() == events


def long_workspace(tmp_path):
    workspace = _workspace(tmp_path)
    pdf = workspace.root / "delivery/book.pdf"
    pages = PdfReader(pdf).pages
    writer = PdfWriter()
    for page in pages:
        writer.add_page(page)
    for _ in range(29):
        writer.add_page(pages[-1])
    writer.write(pdf)
    render_pdf_preview_set(pdf, workspace.root / "runs/pdf-preview")
    return workspace


def bind_fixture_report(workspace, report):
    path = audit_path(workspace)
    path.write_text(report.model_dump_json(), encoding="utf-8")
    workspace.append_event("pdf_visual_audited", {"report_sha256": sha256(path.read_bytes()).hexdigest()})


def test_mandatory_pages_survive_early_code_pages_and_overflow():
    metrics = [VisualPageMetric(page_number=n, page_type="code" if 2 <= n <= 30 else "body",
        width_pt=595.28, height_pt=841.89, text_characters=100) for n in range(1, 101)]
    findings = [QualityFinding(code="ANOMALY", severity=Severity.WARNING, location="第 90 页", message="夹具异常")]
    selected = _sample_pages(metrics, findings, 20)
    assert {1, 90, 100} <= set(selected)
    assert selected == sorted(set(selected)) and len(selected) == 20
    findings.extend(QualityFinding(code="ANOMALY", severity=Severity.WARNING, location=f"第 {n} 页", message="夹具异常") for n in range(31, 70))
    overflow = _sample_pages(metrics, findings, 20)
    assert {1, 90, 100, *range(31, 70)} <= set(overflow)
    assert overflow == sorted(set(overflow)) and all(1 <= n <= 100 for n in overflow)


@pytest.mark.requires_poppler
def test_32_page_expansion_only_renders_missing_pages_and_full_cache_is_readonly(tmp_path, monkeypatch):
    import knowledge_product_workflow.visual_audit as module
    workspace = long_workspace(tmp_path)
    original_run = module.subprocess.run
    calls = []
    def count(command, **kwargs):
        calls.append(command)
        return original_run(command, **kwargs)
    monkeypatch.setattr(module.subprocess, "run", count)
    sampled = audit_pdf_visual(workspace)
    existing = {p.page_number: p.high_resolution_image for p in sampled.pages if p.high_resolution_image}
    full = audit_pdf_visual(workspace, sample_limit=32)
    assert full.sampled_pages == list(range(1, 33))
    assert len(calls) == 32
    assert all(p.high_resolution_image and p.high_resolution_dpi >= 144 for p in full.pages)
    assert all(p.high_resolution_image == existing[p.page_number] for p in full.pages if p.page_number in existing)
    events = (workspace.root / "runs/events.jsonl").read_bytes()
    assert audit_pdf_visual(workspace, sample_limit=100).created_at == full.created_at
    assert len(calls) == 32 and (workspace.root / "runs/events.jsonl").read_bytes() == events


@pytest.mark.parametrize("mutation", ["missing-rule", "missing-cover", "missing-image", "dpi"])
@pytest.mark.requires_poppler
def test_stale_selection_or_clear_image_cannot_be_current(tmp_path, mutation):
    workspace = _workspace(tmp_path)
    report = audit_pdf_visual(workspace)
    if mutation == "missing-cover":
        report.sampled_pages = [2, 3]
    elif mutation == "missing-image":
        report.pages[0].high_resolution_image = None
    elif mutation == "dpi":
        report.pages[0].high_resolution_dpi = 96
    bind_fixture_report(workspace, report)
    if mutation == "missing-rule":
        path = audit_path(workspace)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("rule_set")
        path.write_text(json.dumps(raw), encoding="utf-8")
        workspace.append_event("pdf_visual_audited", {"report_sha256": sha256(path.read_bytes()).hexdigest()})
    assert current_visual_audit(workspace) is None


@pytest.mark.requires_poppler
def test_selection_rule_refresh_reuses_actual_same_page_failure(tmp_path, monkeypatch):
    import knowledge_product_workflow.visual_audit as module
    workspace = _workspace(tmp_path)
    audit_pdf_visual(workspace)
    report = record_visual_observations(workspace, bind_fixture_observations(workspace, [VisualObservation(page_number=3, assessment="failed", note="单元夹具：实际观察记录仍失败，不是最终审批。")]))
    report.rule_set = "kp-pdf-v6-visual-4"
    report.observation_input_sha256 = sha256(b"old-selection-identity").hexdigest()
    bind_fixture_report(workspace, report)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("same valid page image must be reused"))
    refreshed = audit_pdf_visual(workspace)
    from knowledge_product_workflow.visual_audit import VISUAL_RULE_SET
    assert refreshed.rule_set == VISUAL_RULE_SET
    assert refreshed.observations[0].assessment == "failed"


@pytest.mark.requires_poppler
def test_sampling_cannot_be_final_book_acceptance_without_real_manual_coverage(tmp_path):
    workspace = long_workspace(tmp_path)
    report = audit_pdf_visual(workspace)
    record_visual_observations(workspace, bind_fixture_observations(workspace, [VisualObservation(page_number=n, assessment="passed", note="合成观察只验证覆盖协议，不代表看过真实产品。") for n in report.sampled_pages]))
    with pytest.raises(ValueError, match="全部.*页|全书"):
        record_manual_pdf_visual_review(workspace, "测试用户")
    with pytest.raises(ValueError, match="用户确认"):
        record_manual_pdf_visual_review(workspace, "测试用户", confirm_manual_coverage=True)


def test_cli_defaults_to_full_pages_and_exposes_optional_initial_sample(tmp_path, monkeypatch):
    import knowledge_product_workflow.cli as module
    workspace = long_workspace(tmp_path)
    received = []
    original = module.audit_pdf_visual
    monkeypatch.setattr(module, "authorize_command", lambda *a, **k: None)
    monkeypatch.setattr(module, "_capture_orchestration_action_or_exit", lambda *a, **k: None)
    monkeypatch.setattr(module, "complete_orchestration_action", lambda *a, **k: None)
    def capture(opened, **kwargs):
        received.append(kwargs["sample_limit"])
        return original(opened, **kwargs)
    monkeypatch.setattr(module, "audit_pdf_visual", capture)
    runner = CliRunner()
    result = runner.invoke(app, ["audit-pdf-visual", "--workspace", str(workspace.root)])
    assert result.exit_code == 0, result.output
    assert received == [32]
    result = runner.invoke(app, ["audit-pdf-visual", "--workspace", str(workspace.root), "--sample-limit", "12"])
    assert result.exit_code == 0, result.output
    assert received == [32, 12]


@pytest.mark.parametrize("damage", ["missing", "tampered", "truncated", "bad-pixels"])
@pytest.mark.requires_poppler
def test_declared_png_is_verified_even_without_rasterizer(tmp_path, monkeypatch, damage):
    import knowledge_product_workflow.visual_audit as module
    workspace = _workspace(tmp_path)
    report = audit_pdf_visual(workspace)
    image = workspace.root / report.pages[0].high_resolution_image
    if damage == "missing":
        image.rename(image.with_suffix(".retained.png"))
    elif damage == "tampered":
        image.write_bytes(b"not-png")
    else:
        raw = image.read_bytes()
        image.write_bytes(raw[:24] if damage == "truncated" else raw[:50] + b"broken" + raw[56:])
        report.pages[0].high_resolution_image_sha256 = sha256(image.read_bytes()).hexdigest()
        bind_fixture_report(workspace, report)
    monkeypatch.setattr(module, "resolve_pdftoppm", lambda: None)
    assert current_visual_audit(workspace) is None


@pytest.mark.requires_poppler
def test_timeout_preserves_partial_report_and_only_missing_pages_are_retried(tmp_path, monkeypatch):
    import subprocess

    import knowledge_product_workflow.visual_audit as module
    workspace = _workspace(tmp_path)
    original = module.subprocess.run
    calls = []
    def fail_second(command, **kwargs):
        calls.append(int(command[command.index("-f") + 1]))
        if len(calls) == 2:
            raise subprocess.TimeoutExpired(command, 30)
        return original(command, **kwargs)
    monkeypatch.setattr(module.subprocess, "run", fail_second)
    with pytest.raises(RuntimeError, match="30 秒"):
        audit_pdf_visual(workspace)
    partial = json.loads(audit_path(workspace).read_text(encoding="utf-8"))
    assert partial["pages"][0]["high_resolution_image"]
    assert not partial["pages"][1]["high_resolution_image"]
    assert current_visual_audit(workspace) is None
    full = audit_pdf_visual(workspace)
    assert calls == [1, 2, 2, 3]
    assert full.pages[0].high_resolution_image == partial["pages"][0]["high_resolution_image"]
    assert list((workspace.root / "runs/history/visual-audit").glob("*.json"))


@pytest.mark.parametrize("second_filter,wanted", [(0, True), (5, False)])
def test_png_late_filter_is_checked_before_first_pixel_row(tmp_path, second_filter, wanted):
    import struct
    import zlib

    from knowledge_product_workflow.visual_audit import _clear_image_valid
    from knowledge_product_workflow.workspace import Workspace
    workspace = Workspace.create(tmp_path, "tiny-png")
    def chunk(kind, payload):
        return struct.pack("!I", len(payload)) + kind + payload + struct.pack("!I", zlib.crc32(kind + payload) & 0xffffffff)
    pixels = b"\x00" + b"\x00" * 6 + bytes([second_filter]) + b"\x00" * 6
    image = workspace.root / "runs/tiny.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) +
                      chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))
    metric = VisualPageMetric(page_number=1, page_type="body", width_pt=1, height_pt=1, text_characters=0,
        high_resolution_image="runs/tiny.png", high_resolution_image_sha256=sha256(image.read_bytes()).hexdigest(), high_resolution_dpi=144)
    assert _clear_image_valid(workspace, metric) is wanted


@pytest.mark.requires_poppler
def test_reused_same_blank_png_retains_known_blocker_on_rule_refresh(tmp_path, monkeypatch):
    import knowledge_product_workflow.visual_audit as module
    workspace = _workspace(tmp_path)
    pdf = workspace.root / "delivery/book.pdf"
    pages = PdfReader(pdf).pages
    writer = PdfWriter()
    writer.add_blank_page(595.28, 841.89)
    writer.add_page(pages[1])
    writer.add_page(pages[2])
    writer.write(pdf)
    render_pdf_preview_set(pdf, workspace.root / "runs/pdf-preview")
    old = audit_pdf_visual(workspace)
    assert any(f.code == "PDF_CLEAR_PREVIEW_BLANK_PAGE" for f in old.findings)
    old.rule_set = "kp-pdf-v6-visual-4"
    bind_fixture_report(workspace, old)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("same clear images should not rerender"))
    refreshed = audit_pdf_visual(workspace)
    assert any(f.code == "PDF_CLEAR_PREVIEW_BLANK_PAGE" and f.page_number == 1 for f in refreshed.findings)


def test_known_blank_is_mandatory_before_long_book_selection(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import knowledge_product_workflow.visual_audit as module
    workspace = _workspace(tmp_path)
    old = audit_pdf_visual(workspace)
    metrics = [VisualPageMetric(page_number=n, page_type="body", width_pt=595.28, height_pt=841.89,
                               text_characters=800) for n in range(1, 101)]
    known = module.VisualAuditFinding(code="PDF_CLEAR_PREVIEW_BLANK_PAGE", severity=Severity.BLOCKER,
        location="delivery/book.pdf:第 87 页", page_number=87, message="合成选择夹具：既有已知空白失败，不是100页实测。")
    old = old.model_copy(update={"page_count": 100, "pages": metrics, "findings": [known]})
    monkeypatch.setattr(module, "current_visual_audit", lambda *a: None)
    monkeypatch.setattr(module, "_bound_visual_report", lambda *a: old)
    monkeypatch.setattr(module, "PdfReader", lambda *a: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: '')] * 100))
    monkeypatch.setattr(module, "_measure_page", lambda page, number, expected: metrics[number-1])
    original = module._sample_pages
    def probe(pages, findings, limit):
        selected = original(pages, findings, limit)
        assert {1, 87, 100} <= set(selected)
        raise RuntimeError("selection-probe-completed-before-render")
    monkeypatch.setattr(module, "_sample_pages", probe)
    with pytest.raises(RuntimeError, match="selection-probe-completed"):
        audit_pdf_visual(workspace, sample_limit=12)
