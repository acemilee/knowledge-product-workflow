from __future__ import annotations

import struct
import subprocess
import zlib
from hashlib import sha256
from pathlib import Path

import pytest
from pypdf import PdfWriter

from knowledge_product_workflow.pdf_preview import (
    current_pdf_preview,
    render_pdf_preview_set,
    resolve_pdftoppm,
)


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


def test_real_rasterizer_handles_padded_names_and_repairs_a_missing_preview(tmp_path) -> None:
    if resolve_pdftoppm() is None:
        return
    pdf_path = tmp_path / "book.pdf"
    writer = PdfWriter()
    for _ in range(11):
        writer.add_blank_page(width=595, height=842)
    with pdf_path.open("wb") as handle:
        writer.write(handle)
    directory = tmp_path / "preview"

    path, manifest = render_pdf_preview_set(pdf_path, directory)
    assert len(manifest.pages) == 11
    assert [Path(page.image_path).name for page in manifest.pages] == [f"page-{number}.png" for number in range(1, 12)]
    assert current_pdf_preview(pdf_path, directory) is not None
    original_bytes = path.read_bytes()
    assert render_pdf_preview_set(pdf_path, directory)[0].read_bytes() == original_bytes

    (directory / manifest.pages[0].image_path).unlink()
    assert current_pdf_preview(pdf_path, directory) is None
    repaired_path, _ = render_pdf_preview_set(pdf_path, directory)
    assert repaired_path.is_file()
    assert current_pdf_preview(pdf_path, directory) is not None


@pytest.mark.parametrize("deep_input", [False, True])
def test_real_rasterizer_writes_previews_in_a_deep_workspace_directory(tmp_path, deep_input) -> None:
    if resolve_pdftoppm() is None:
        pytest.skip("需要实际Poppler；跳过不代表深路径预览通过")
    directory = tmp_path / ("a" * 70) / ("b" * 70) / ("c" * 70) / "preview"
    directory.mkdir(parents=True)
    pdf = (directory if deep_input else tmp_path) / "book.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(pdf)
    assert len(str(directory.resolve())) > 260
    _, manifest = render_pdf_preview_set(pdf, directory)
    assert manifest.renderer_available
    assert len(manifest.pages) == 1
    assert (directory / manifest.pages[0].image_path).is_file()
    assert current_pdf_preview(pdf, directory) is not None


def _pixel_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\0\xff\xff\xff")) + chunk(b"IEND", b"")


@pytest.mark.parametrize("page_count", [1, 9, 10, 66, 76, 100])
@pytest.mark.parametrize("padded", [False, True])
def test_preview_normalizes_numeric_page_names_in_short_batches(tmp_path, monkeypatch, page_count, padded) -> None:
    pdf = tmp_path / "book.pdf"
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(595, 842)
    writer.write(pdf)
    monkeypatch.setattr("knowledge_product_workflow.pdf_preview.resolve_pdftoppm", lambda: Path("pdftoppm"))
    batches = []

    def render(command, **kwargs):
        first, last = int(command[command.index("-f") + 1]), int(command[command.index("-l") + 1])
        batches.append((first, last, kwargs["timeout"]))
        for number in range(first, last + 1):
            suffix = str(number).zfill(len(str(page_count))) if padded else str(number)
            (Path(kwargs.get("cwd", ".")) / f"{command[-1]}-{suffix}.png").write_bytes(_pixel_png())

    monkeypatch.setattr("knowledge_product_workflow.pdf_preview.subprocess.run", render)
    _, manifest = render_pdf_preview_set(pdf, tmp_path / "preview")
    assert [Path(page.image_path).name for page in manifest.pages] == [f"page-{number}.png" for number in range(1, page_count + 1)]
    assert all(last - first < 10 and timeout == 30 for first, last, timeout in batches)


@pytest.mark.parametrize("failure", ["missing", "duplicate", "corrupt", "timeout"])
def test_preview_failure_names_the_page_and_preserves_previous_manifest(tmp_path, monkeypatch, failure) -> None:
    pdf = tmp_path / "book.pdf"
    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    writer.write(pdf)
    directory = tmp_path / "preview"
    directory.mkdir()
    old_manifest = directory / "preview-manifest.json"
    old_manifest.write_text("previous evidence", encoding="utf-8")
    monkeypatch.setattr("knowledge_product_workflow.pdf_preview.resolve_pdftoppm", lambda: Path("pdftoppm"))

    def render(command, **_kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 30)
        if failure == "missing":
            return
        prefix = Path(_kwargs.get("cwd", ".")) / command[-1]
        Path(f"{prefix}-01.png").write_bytes(b"broken" if failure == "corrupt" else _pixel_png())
        if failure == "duplicate":
            Path(f"{prefix}-1.png").write_bytes(_pixel_png())

    monkeypatch.setattr("knowledge_product_workflow.pdf_preview.subprocess.run", render)
    with pytest.raises(RuntimeError) as error:
        render_pdf_preview_set(pdf, directory)
    assert "1" in str(error.value)
    if failure == "timeout":
        assert "超时" in str(error.value)
    assert old_manifest.read_text(encoding="utf-8") == "previous evidence"
