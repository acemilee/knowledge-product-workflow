"""Optional local PDF screenshot helpers for visual regression tests."""

from __future__ import annotations

import shutil
import struct
import subprocess
import zlib
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader


def resolve_pdftoppm() -> Path | None:
    """Return the local Poppler/TeX Live rasterizer when available."""
    value = shutil.which("pdftoppm")
    return Path(value) if value else None


def render_pdf_first_page(pdf_path: Path, output_directory: Path) -> Path:
    """Render one 96-DPI PNG preview locally; never downloads a renderer."""
    executable = resolve_pdftoppm()
    if executable is None:
        raise RuntimeError("未检测到 pdftoppm，无法生成 PDF 截图预览。")
    output_directory.mkdir(parents=True, exist_ok=True)
    prefix = output_directory / "page-1"
    subprocess.run(
        [
            str(executable),
            "-f",
            "1",
            "-l",
            "1",
            "-r",
            "96",
            "-png",
            "-singlefile",
            str(pdf_path),
            str(prefix),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    output = prefix.with_suffix(".png")
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("pdftoppm 未生成预期的 PNG 截图。")
    return output


def png_has_visible_ink(path: Path) -> bool:
    """Check a normal RGB PNG contains a non-white pixel without a Pillow dependency."""
    raw = path.read_bytes()
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    width = height = bit_depth = color_type = None
    compressed = bytearray()
    while offset + 12 <= len(raw):
        length = struct.unpack("!I", raw[offset : offset + 4])[0]
        kind = raw[offset + 4 : offset + 8]
        data = raw[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, _ = struct.unpack("!IIBBBBB", data)
        elif kind == b"IDAT":
            compressed.extend(data)
        elif kind == b"IEND":
            break
    if not width or not height or bit_depth != 8 or color_type != 2:
        return False
    try:
        inflated = zlib.decompress(bytes(compressed))
    except zlib.error:
        return False
    stride = width * 3
    expected = height * (stride + 1)
    if len(inflated) != expected:
        return False
    previous = bytearray(stride)
    cursor = 0
    for _ in range(height):
        filter_type = inflated[cursor]
        cursor += 1
        encoded = inflated[cursor : cursor + stride]
        cursor += stride
        row = bytearray(stride)
        for index, value in enumerate(encoded):
            left = row[index - 3] if index >= 3 else 0
            above = previous[index]
            upper_left = previous[index - 3] if index >= 3 else 0
            if filter_type == 0:
                decoded = value
            elif filter_type == 1:
                decoded = (value + left) & 0xFF
            elif filter_type == 2:
                decoded = (value + above) & 0xFF
            elif filter_type == 3:
                decoded = (value + ((left + above) // 2)) & 0xFF
            elif filter_type == 4:
                predictor = left + above - upper_left
                distances = (abs(predictor - left), abs(predictor - above), abs(predictor - upper_left))
                nearest = left if distances[0] <= distances[1] and distances[0] <= distances[2] else (
                    above if distances[1] <= distances[2] else upper_left
                )
                decoded = (value + nearest) & 0xFF
            else:
                return False
            row[index] = decoded
        if any(value < 245 for value in row):
            return True
        previous = row
    return False


class PdfPreviewPage(BaseModel):
    """One deterministic preview-page observation bound to a rendered PDF."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(gt=0)
    page_kind: str = Field(min_length=1)
    extracted_text_length: int = Field(ge=0)
    image_path: str | None = None
    image_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    ink_detected: bool | None = None
    sparse: bool = False


class PdfPreviewManifest(BaseModel):
    """A PDF-byte-bound preview set; unavailable rasterizers are recorded honestly."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    generated_at: datetime
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    renderer_available: bool
    needs_manual_visual_review: bool
    pages: list[PdfPreviewPage] = Field(min_length=1)


def preview_manifest_path(output_directory: Path) -> Path:
    return output_directory / "preview-manifest.json"


def _page_kind(page_number: int, text: str) -> str:
    if page_number == 1:
        return "cover"
    if "目录" in text or "Table of Contents" in text:
        return "toc"
    if "```" in text:
        return "code"
    if "|" in text:
        return "table"
    return "body"


def render_pdf_preview_set(pdf_path: Path, output_directory: Path) -> tuple[Path, PdfPreviewManifest]:
    """Create local page previews without downloading a renderer or inspecting remote assets."""
    pdf_path = pdf_path.expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"找不到待预览 PDF：{pdf_path}")
    output_directory.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(pdf_path)
    texts = [page.extract_text() or "" for page in reader.pages]
    executable = resolve_pdftoppm()
    generated: dict[int, Path] = {}
    if executable is not None:
        prefix = output_directory / "page"
        subprocess.run(
            [str(executable), "-r", "96", "-png", str(pdf_path), str(prefix)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        generated = {
            page_number: output_directory / f"page-{page_number}.png"
            for page_number in range(1, len(reader.pages) + 1)
        }
        missing = [path for path in generated.values() if not path.is_file()]
        if missing:
            raise RuntimeError("pdftoppm 未生成完整 PDF 页面预览集。")
    pages: list[PdfPreviewPage] = []
    for page_number, text in enumerate(texts, start=1):
        image = generated.get(page_number)
        ink = png_has_visible_ink(image) if image is not None else None
        kind = _page_kind(page_number, text)
        pages.append(
            PdfPreviewPage(
                page_number=page_number,
                page_kind=kind,
                extracted_text_length=len(text.strip()),
                image_path=image.name if image is not None else None,
                image_sha256=sha256(image.read_bytes()).hexdigest() if image is not None else None,
                ink_detected=ink,
                sparse=kind == "body" and len(text.strip()) < 120,
            )
        )
    manifest = PdfPreviewManifest(
        generated_at=datetime.now(UTC),
        pdf_sha256=sha256(pdf_path.read_bytes()).hexdigest(),
        renderer_available=executable is not None,
        needs_manual_visual_review=True,
        pages=pages,
    )
    path = preview_manifest_path(output_directory)
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path, manifest
