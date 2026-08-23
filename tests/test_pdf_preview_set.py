from __future__ import annotations

from hashlib import sha256

from pypdf import PdfWriter

from knowledge_product_workflow.pdf_preview import render_pdf_preview_set


def test_preview_manifest_binds_pdf_and_records_renderer_availability(tmp_path) -> None:
    pdf_path = tmp_path / "book.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    manifest_path, manifest = render_pdf_preview_set(pdf_path, tmp_path / "preview")

    assert manifest_path.is_file()
    assert manifest.pdf_sha256 == sha256(pdf_path.read_bytes()).hexdigest()
    assert manifest.pages[0].page_number == 1
    assert manifest.needs_manual_visual_review is True
    if manifest.renderer_available:
        assert manifest.pages[0].image_path is not None
        assert manifest.pages[0].ink_detected is False
    else:
        assert manifest.pages[0].image_path is None
