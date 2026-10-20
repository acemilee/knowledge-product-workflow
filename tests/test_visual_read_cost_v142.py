"""视觉读上下文只在单次调用复用；同mtime改动不能复用通过。"""

import hashlib
import os
from datetime import UTC, datetime

import pytest

from knowledge_product_workflow import visual_audit as audit
from knowledge_product_workflow.publishing import create_quarto_source
from knowledge_product_workflow.verification_context import SnapshotChangedError
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace
from tests.test_visual_observation_reuse_v141 import _png


def fixture(tmp_path, monkeypatch):
    w = Workspace.create(tmp_path, "one-read")
    create_product_brief(w, "读路径核验")
    md = w.root / "delivery/book.md"
    md.write_text("# 读路径核验\n", encoding="utf-8")
    create_quarto_source(w, md)
    image = w.root / "runs/page.png"
    image.write_bytes(_png(60))
    page = audit.VisualPageMetric(page_number=1, page_type="body", width_pt=595.28,
        height_pt=841.89, text_characters=0, high_resolution_image="runs/page.png",
        high_resolution_image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(), high_resolution_dpi=160)
    report = audit.VisualAuditReport(created_at=datetime.now(UTC), typography_measurement_revision=2,
        pdf_sha256="a"*64, preview_manifest_sha256="a"*64, profile_sha256="a"*64,
        observation_input_sha256="a"*64, figure_sources_sha256="a"*64,
        page_count=1, pages=[page], sampled_pages=[1], findings=[])
    monkeypatch.setattr(audit, "_bound_visual_report", lambda _: report.model_copy(deep=True))
    monkeypatch.setattr(audit, "resolve_pdftoppm", lambda: image)
    return w, image


def test_direct_public_reader_decodes_each_page_once_per_call(tmp_path, monkeypatch):
    w, _ = fixture(tmp_path, monkeypatch)
    original = audit.iter_png_rgb_rows
    calls = []
    def decode(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(audit, "iter_png_rgb_rows", decode)
    assert audit.current_visual_audit(w) is not None
    assert len(calls) == 1
    assert audit.current_visual_audit(w) is not None
    assert len(calls) == 2, "不同调用不可复用上一命令的通过状态"


def test_same_mtime_external_png_change_is_rejected_in_next_call(tmp_path, monkeypatch):
    w, image = fixture(tmp_path, monkeypatch)
    assert audit.current_visual_audit(w) is not None
    info = image.stat()
    image.write_bytes(_png(0))
    os.utime(image, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert audit.current_visual_audit(w) is None


def test_mutation_during_read_is_rejected_at_scope_exit(tmp_path, monkeypatch):
    w, image = fixture(tmp_path, monkeypatch)
    original = audit._clear_image_valid_uncached
    def mutate(*args, **kwargs):
        valid = original(*args, **kwargs)
        image.write_bytes(_png(0))
        return valid
    monkeypatch.setattr(audit, "_clear_image_valid_uncached", mutate)
    with pytest.raises(SnapshotChangedError):
        audit.current_visual_audit(w)
