import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from knowledge_product_workflow.pdf_preview import render_pdf_preview_set
from knowledge_product_workflow.pdf_visual import (
    record_manual_pdf_visual_review,
    validate_pdf_visual_contract,
)
from knowledge_product_workflow.publishing import create_quarto_source
from knowledge_product_workflow.visual_audit import audit_pdf_visual, current_visual_audit
from knowledge_product_workflow.workspace import Workspace
from tests.visual_support import bind_fixture_observations


def _pdf(workspace: Workspace, *, body_size: int = 12) -> Path:
    writer = PdfWriter()
    for text, size in [("Reading guide", 20), ("Table of Contents", 11), ("Body reading example", body_size)]:
        page = writer.add_blank_page(595.28, 841.89)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 {size} Tf 1 0 0 1 40 700 Tm ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    path = workspace.root / "delivery" / "book.pdf"
    writer.write(path)
    return path


def _workspace(tmp_path: Path, *, body_size: int = 12) -> Workspace:
    workspace = Workspace.create(tmp_path, "visual-guide")
    from knowledge_product_workflow.workflows.product_brief import create_product_brief
    create_product_brief(workspace, "阅读指南")
    markdown = workspace.root / "delivery" / "book.md"
    markdown.write_text("# 阅读指南\n", encoding="utf-8")
    create_quarto_source(workspace, markdown)
    pdf = _pdf(workspace, body_size=body_size)
    render_pdf_preview_set(pdf, workspace.root / "runs" / "pdf-preview")
    return workspace


@pytest.mark.requires_poppler
def test_current_short_tutorial_covers_all_thirteen_clear_pages(tmp_path):
    from pypdf import PdfReader
    workspace = _workspace(tmp_path)
    pdf = workspace.root / 'delivery/book.pdf'
    pages = PdfReader(pdf).pages
    writer = PdfWriter()
    for page in pages:
        writer.add_page(page)
    for _ in range(10):
        writer.add_page(pages[-1])
    writer.write(pdf)
    render_pdf_preview_set(pdf, workspace.root / 'runs/pdf-preview')
    report = audit_pdf_visual(workspace)
    assert report.page_count == 13
    assert report.sampled_pages == list(range(1,14)), '十几页教程不能因默认12抽样漏掉页面'
    assert all(page.high_resolution_dpi >= 144 and page.high_resolution_image for page in report.pages)


@pytest.mark.requires_poppler
def test_v6_audit_must_precede_human_review_and_is_invalidated_by_pdf_change(tmp_path: Path, monkeypatch) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(ValueError, match="audit-pdf-visual"):
        record_manual_pdf_visual_review(workspace, "测试用户")
    report = audit_pdf_visual(workspace)
    assert report.page_count == 3
    assert report.pages[1].page_type == "toc"
    assert report.pages[2].body_font_median_pt == 12
    assert all(page.high_resolution_dpi == 160 for page in report.pages)
    assert audit_pdf_visual(workspace).created_at == report.created_at
    # Unit fixture only: production observers must actually inspect these pages.
    from knowledge_product_workflow.visual_audit import (
        VisualObservation,
        record_visual_observations,
    )
    record_visual_observations(workspace, bind_fixture_observations(workspace, [VisualObservation(page_number=page, assessment="passed",
        note="单元测试夹具观察，用于验证绑定和人审顺序；不代表实际产品验收。") for page in report.sampled_pages]))
    from tests.visual_support import approve_fixture_pdf
    approve_fixture_pdf(workspace,monkeypatch,['visual'],'软件视觉确认测试夹具，非用户批准。')
    record_manual_pdf_visual_review(workspace, "测试用户")
    assert not any(item.severity.value == "blocker" for item in validate_pdf_visual_contract(workspace))
    with (workspace.root / "delivery" / "book.pdf").open("ab") as handle:
        handle.write(b"\n% changed\n")
    assert current_visual_audit(workspace) is None


def test_reliably_measured_small_text_cannot_be_overridden_by_human_pass(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, body_size=6)
    report = audit_pdf_visual(workspace)
    assert "PDF_SMALL_TEXT_BLOCKER" in {item.code for item in report.findings}
    with pytest.raises(ValueError, match="blocker"):
        record_manual_pdf_visual_review(workspace, "测试用户")


def test_missing_rasterizer_keeps_metrics_manual_and_refuses_fake_model_observation(tmp_path, monkeypatch):
    from knowledge_product_workflow.visual_audit import (
        VisualObservation,
        record_visual_observations,
    )
    workspace = _workspace(tmp_path)
    monkeypatch.setattr("knowledge_product_workflow.visual_audit.resolve_pdftoppm", lambda: None)
    report = audit_pdf_visual(workspace, sample_limit=1)
    assert report.sampled_pages == [1, 2, 3]
    assert report.observation_state == "manual_required"
    assert report.pages[2].body_font_median_pt == 12
    with pytest.raises(ValueError, match="清晰"):
        record_visual_observations(workspace, bind_fixture_observations(workspace, [VisualObservation(page_number=1, assessment="passed", note="这不是实际查看，不应接受此记录。")]))
    with pytest.raises(ValueError, match="用户确认"):
        record_manual_pdf_visual_review(workspace, "测试用户", confirm_manual_coverage=True)
    # Explicit unit fixture approval; the real workflow never simulates this.
    from tests.visual_support import approve_fixture_pdf
    approve_fixture_pdf(workspace,monkeypatch,['visual','manual_full_coverage','content'],
                        '单元测试模拟当前整份PDF的内容与视觉人工核对。')
    record_manual_pdf_visual_review(workspace, "测试用户", confirm_manual_coverage=True,
                                   confirm_content=True, user_response="单元测试模拟当前整份PDF的内容与视觉人工核对。")


def test_mixed_role_drift_is_not_hidden_by_a_readable_page_median(tmp_path):
    workspace = _workspace(tmp_path)
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(595.28, 841.89)
    page = writer.add_blank_page(595.28, 841.89)
    fonts = {}
    for key, name in [("C", "PingFangSC-Regular"), ("L", "Inter-Regular"), ("M", "LatinModernMath-Regular")]:
        fonts[NameObject("/" + key)] = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/" + name),
        }))
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject(fonts)})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /C 10.8772 Tf 1 0 0 1 40 700 Tm (Body explanation sample) Tj "
                    b"/L 11.9552 Tf 1 0 0 1 200 700 Tm (Latin text sample) Tj "
                    b"/M 15.1423 Tf 1 0 0 1 320 700 Tm (2.4+3.0+1.8=7.2) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(workspace.root / "delivery/book.pdf")
    render_pdf_preview_set(workspace.root / "delivery/book.pdf", workspace.root / "runs/pdf-preview")
    report = audit_pdf_visual(workspace)
    assert report.typography_measurement_revision == 2
    assert report.pages[2].body_role_base_pt["math"] == 15.1423
    assert "PDF_BODY_ROLE_SIZE_DRIFT" in {item.code for item in report.findings}
    with pytest.raises(ValueError, match="blocker"):
        record_manual_pdf_visual_review(workspace, "测试用户")


def test_old_audit_without_role_measurements_is_not_current(tmp_path):
    workspace = _workspace(tmp_path)
    audit_pdf_visual(workspace)
    path = workspace.root / "runs/visual-audit-report.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    old.pop("typography_measurement_revision")
    path.write_text(json.dumps(old), encoding="utf-8")
    assert current_visual_audit(workspace) is None


def test_font_switch_origin_alias_is_not_body_role_evidence():
    from types import SimpleNamespace

    from knowledge_product_workflow.visual_audit import _measure_page, _page_findings
    class Page:
        mediabox = SimpleNamespace(width=595.28, height=841.89, left=0, right=595.28)
        images = ()
        def extract_text(self, visitor_text, visitor_operand_before=None):
            cm = [1, 0, 0, 1, 72, 769.89]
            for text, font, size, x, y in [
                ("当前正文中文说明"*8, "PingFangSC-Regular", 11.9552, 20, -150),
                ("CLI", "Inter-Regular", 11.9552, 200, -150),
                ("3.2 SKILL.md", "Inter-Regular", 10.9091, -35.15, 48.354),
                ("10", "Inter-Regular", 10.9091, 219.978, -748.754),
                ("Agent Skills footer", "Inter-Regular", 10.9091, 0, 0),
            ]:
                visitor_text(text, cm, [1,0,0,1,x,y], {"/BaseFont": "/"+font}, size)
            return "普通章节正文与运行页眉页脚"
    metric = _measure_page(Page(), 3, 12*72/72.27)
    assert metric.body_role_base_pt["latin"] == 11.9552
    assert metric.body_role_sample_characters["latin"] == 3
    assert "PDF_BODY_ROLE_SIZE_DRIFT" not in {x.code for x in _page_findings(metric)}


@pytest.mark.requires_poppler
def test_measurement_refresh_reuses_only_identical_visual_input_and_keeps_failure(tmp_path):
    from knowledge_product_workflow.visual_audit import VisualObservation, audit_path
    workspace = _workspace(tmp_path)
    report = audit_pdf_visual(workspace)
    report.observations = [VisualObservation(page_number=3, assessment="failed", note="测试夹具实际观察：本页留白用途不明，未通过。", responsible_skill="kp-publisher")]
    report.observation_state = "observed"
    report.typography_measurement_revision = 1
    audit_path(workspace).write_text(report.model_dump_json(), encoding="utf-8")
    from hashlib import sha256
    workspace.append_event("pdf_visual_observations_recorded", {"report_sha256": sha256(audit_path(workspace).read_bytes()).hexdigest()})
    refreshed = audit_pdf_visual(workspace)
    assert refreshed.typography_measurement_revision == 2
    assert refreshed.observations[0].assessment == "failed"
    assert refreshed.observation_input_sha256 == report.observation_input_sha256
    _pdf(workspace, body_size=10)
    render_pdf_preview_set(workspace.root / "delivery/book.pdf", workspace.root / "runs/pdf-preview")
    changed = audit_pdf_visual(workspace)
    assert changed.observations == []
