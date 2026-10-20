"""预览世代隔离：中途失败不能破坏上一完整集合，缩页不混入旧页。"""
import shutil
from pathlib import Path

import pytest
from pypdf import PdfWriter

from knowledge_product_workflow import pdf_preview


def write_pdf(path, pages, width=595):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width, 842)
    with path.open("wb") as stream:
        writer.write(stream)


def test_failed_new_generation_does_not_mutate_previous_preview(tmp_path, monkeypatch):
    if pdf_preview.resolve_pdftoppm() is None:
        pytest.skip("需要本机Poppler")
    pdf = tmp_path / "book.pdf"
    write_pdf(pdf, 2)
    directory = tmp_path / "preview"
    manifest_path, old = pdf_preview.render_pdf_preview_set(pdf, directory)
    old_manifest = manifest_path.read_bytes()
    old_pdf = tmp_path / "old.pdf"
    shutil.copyfile(pdf, old_pdf)
    old_images = {p.image_path: (directory / p.image_path).read_bytes() for p in old.pages}
    write_pdf(pdf, 2, width=500)
    original_replace = pdf_preview.os.replace
    def fail_second_page(source, destination):
        if Path(destination).name == "page-2.png":
            raise OSError("injected publication interruption")
        return original_replace(source, destination)
    monkeypatch.setattr(pdf_preview.os, "replace", fail_second_page)
    with pytest.raises(OSError, match="injected"):
        pdf_preview.render_pdf_preview_set(pdf, directory)
    assert manifest_path.read_bytes() == old_manifest
    assert all((directory / path).read_bytes() == raw for path, raw in old_images.items())
    assert pdf_preview.current_pdf_preview(old_pdf, directory) is not None


def test_current_gallery_after_shrinking_contains_only_current_pages(tmp_path):
    if pdf_preview.resolve_pdftoppm() is None:
        pytest.skip("需要本机Poppler")
    pdf = tmp_path / "book.pdf"
    write_pdf(pdf, 3)
    directory = tmp_path / "preview"
    _, before = pdf_preview.render_pdf_preview_set(pdf, directory)
    old_paths = [directory / p.image_path for p in before.pages]
    write_pdf(pdf, 1)
    _, after = pdf_preview.render_pdf_preview_set(pdf, directory)
    assert len(after.pages) == 1
    gallery = (directory / after.gallery_path).read_text(encoding="utf-8")
    assert 'page-1.png' in gallery and 'page-2.png' not in gallery
    assert all(path.is_file() for path in old_paths)
    assert pdf_preview.current_pdf_preview(pdf, directory) is not None
