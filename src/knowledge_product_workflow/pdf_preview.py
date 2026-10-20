"""Optional local PDF screenshot helpers for visual regression tests."""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import uuid
import zlib
from datetime import UTC, datetime
from hashlib import sha256
from html import escape
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from knowledge_product_workflow.verification_context import read_bytes as _v_read_bytes

from .workspace_scratch import workspace_scratch


def resolve_pdftoppm() -> Path | None:
    """Return the local Poppler/TeX Live rasterizer when available."""
    value = shutil.which("pdftoppm")
    return Path(value) if value else None


def _render_png_via_pipe(executable: Path, pdf_path: Path, output: Path, page: int, dpi: int) -> None:
    # Native Windows file output/cwd can reject deep paths. Keep both file
    # paths in Python; Poppler reads one PDF and emits one PNG through pipes.
    result = subprocess.run(
        [str(executable), "-f", str(page), "-l", str(page), "-r", str(dpi),
         "-singlefile", "-png", "-"],
        input=pdf_path.read_bytes(), capture_output=True, timeout=30, check=False,
    )
    if result.returncode or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        detail = result.stderr.decode("utf-8", "replace")[:300]
        raise RuntimeError(f"pdftoppm 第 {page} 页管道渲染失败：{detail or result.returncode}")
    output.write_bytes(result.stdout)


def render_pdf_first_page(pdf_path: Path, output_directory: Path) -> Path:
    """Render one 96-DPI PNG preview locally; never downloads a renderer."""
    executable = resolve_pdftoppm()
    if executable is None:
        raise RuntimeError("未检测到 pdftoppm，无法生成 PDF 截图预览。")
    output_directory.mkdir(parents=True, exist_ok=True)
    prefix = output_directory / "page-1"
    output = prefix.with_suffix(".png")
    if os.name == "nt" and max(len(str(pdf_path.resolve())), len(str(output.resolve()))) >= 260:
        _render_png_via_pipe(executable, pdf_path, output, 1, 96)
        return output
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
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("pdftoppm 未生成预期的 PNG 截图。")
    return output


def iter_png_rgb_rows(path: Path, *, include_alpha: bool = False):
    """Yield decoded RGB rows; optional RGBA preserves alpha for strict read-only comparisons."""
    raw = _v_read_bytes(path)
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"预览不是有效 PNG：{path.name}")
    offset = 8
    width = height = bit_depth = color_type = None
    compressed = bytearray()
    ended=False
    while offset + 12 <= len(raw):
        length = struct.unpack("!I", raw[offset : offset + 4])[0]
        kind = raw[offset + 4 : offset + 8]
        data = raw[offset + 8 : offset + 8 + length]
        if include_alpha and (offset+12+length>len(raw) or zlib.crc32(kind+data)&0xffffffff!=struct.unpack('!I',raw[offset+8+length:offset+12+length])[0]):
            raise ValueError(f"预览 PNG 块不完整或校验失败：{path.name}")
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack("!IIBBBBB", data)
            if include_alpha and (compression,filtering,interlace)!=(0,0,0):
                raise ValueError(f"预览 PNG 编码不支持严格像素比较：{path.name}")
        elif kind == b"IDAT":
            compressed.extend(data)
        elif kind == b"IEND":
            ended=True
            break
    if include_alpha and not ended:
        raise ValueError(f"预览 PNG 缺少结束块：{path.name}")
    if not width or not height or bit_depth != 8 or color_type not in ((2,6) if include_alpha else (2,)):
        raise ValueError(f"预览 PNG 尺寸或 RGB 编码无效：{path.name}")
    try:
        inflated = zlib.decompress(bytes(compressed))
    except zlib.error as error:
        raise ValueError(f"预览 PNG 压缩内容损坏：{path.name}") from error
    channels=4 if color_type==6 else 3
    stride = width * channels
    expected = height * (stride + 1)
    if len(inflated) != expected:
        raise ValueError(f"预览 PNG 像素数据不完整：{path.name}")
    if any(inflated[row * (stride + 1)] > 4 for row in range(height)):
        raise ValueError(f"预览 PNG 使用无效滤波器：{path.name}")
    previous = bytearray(stride)
    cursor = 0
    for _ in range(height):
        filter_type = inflated[cursor]
        cursor += 1
        encoded = inflated[cursor : cursor + stride]
        cursor += stride
        row = bytearray(stride)
        for index, value in enumerate(encoded):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
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
                raise ValueError(f"预览 PNG 使用无效滤波器：{path.name}")
            row[index] = decoded
        yield row
        previous = row


def png_has_visible_ink(path: Path) -> bool:
    """Check visible ink with the same validated decoder used by measurements."""
    return any(any(value < 245 for value in row) for row in iter_png_rgb_rows(path))


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
    gallery_path: str | None = None
    gallery_sha256: str | None = None


def preview_manifest_path(output_directory: Path) -> Path:
    return output_directory / "preview-manifest.json"


def current_pdf_preview(pdf_path: Path, output_directory: Path) -> tuple[Path, PdfPreviewManifest] | None:
    """Read a complete preview of the current PDF without changing its evidence."""
    path = preview_manifest_path(output_directory)
    if not path.is_file() or not pdf_path.is_file():
        return None
    try:
        manifest = PdfPreviewManifest.model_validate_json(path.read_text(encoding="utf-8"))
        if manifest.pdf_sha256 != sha256(pdf_path.read_bytes()).hexdigest():
            return None
        if len(manifest.pages) != len(PdfReader(pdf_path).pages):
            return None
        for number, page in enumerate(manifest.pages, start=1):
            if page.page_number != number:
                return None
            if not manifest.renderer_available:
                if page.image_path is not None or page.image_sha256 is not None:
                    return None
                continue
            if page.image_path is None or page.image_sha256 is None or page.ink_detected is None:
                return None
            image = (output_directory / page.image_path).resolve()
            if not image.is_relative_to(output_directory.resolve()):
                return None
            if not image.is_file() or sha256(image.read_bytes()).hexdigest() != page.image_sha256:
                return None
        if manifest.schema_version >= 2:
            if not manifest.gallery_path or not manifest.gallery_sha256:
                return None
            gallery = (output_directory / manifest.gallery_path).resolve()
            if (not gallery.is_relative_to(output_directory.resolve()) or not gallery.is_file()
                    or sha256(gallery.read_bytes()).hexdigest() != manifest.gallery_sha256):
                return None
    except (OSError, PdfReadError, ValueError):
        return None
    return path, manifest


def _page_kind(page_number: int, text: str) -> str:
    if page_number == 1:
        return "cover"
    first_lines = [line.strip() for line in text.splitlines()[:3]]
    if "目录" in first_lines or "Table of Contents" in first_lines:
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
    current = current_pdf_preview(pdf_path, output_directory)
    if current is not None:
        return current
    reader = PdfReader(pdf_path)
    texts = [page.extract_text() or "" for page in reader.pages]
    executable = resolve_pdftoppm()
    generated: dict[int, Path] = {}
    ink_observations: dict[int, bool] = {}
    generation = output_directory / "generations" / uuid.uuid4().hex[:12]
    generation.mkdir(parents=True)
    if executable is not None:
        with workspace_scratch(prefix=".preview-", root=output_directory) as directory:
            staging = Path(directory)
            for first in range(1, len(reader.pages) + 1, 10):
                last = min(first + 9, len(reader.pages))
                try:
                    if os.name == "nt" and max(len(str(pdf_path)), len(str(staging / "page-100.png"))) >= 260:
                        for number in range(first, last + 1):
                            _render_png_via_pipe(executable, pdf_path, staging / f"page-{number}.png", number, 96)
                    else:
                        subprocess.run(
                            [str(executable), "-f", str(first), "-l", str(last), "-r", "96", "-png",
                             str(pdf_path), str(staging / "page")],
                            check=True, capture_output=True, timeout=30,
                        )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    detail = "30 秒超时" if isinstance(error, subprocess.TimeoutExpired) else (getattr(error, "stderr", b"") or b"").decode("utf-8", "replace")[:300]
                    raise RuntimeError(f"pdftoppm 第 {first}–{last} 页生成失败：{detail}") from error
            found: dict[int, list[Path]] = {}
            for candidate in staging.glob("page-*.png"):
                match = re.fullmatch(r"page-(\d+)\.png", candidate.name)
                if match is not None:
                    found.setdefault(int(match.group(1)), []).append(candidate)
            missing = [number for number in range(1, len(reader.pages) + 1) if number not in found]
            duplicate = [number for number, paths in found.items() if len(paths) != 1]
            unexpected = sorted(number for number in found if not 1 <= number <= len(reader.pages))
            if missing or duplicate or unexpected:
                raise RuntimeError(
                    f"PDF 共 {len(reader.pages)} 页，预览图片缺页={missing[:20]}、重复页={duplicate[:20]}、"
                    f"越界页={unexpected[:20]}；本次 pdftoppm 输出位于 {staging}。"
                )
            for page_number in range(1, len(reader.pages) + 1):
                candidate = found[page_number][0]
                if candidate.stat().st_size == 0:
                    raise RuntimeError(f"PDF 第 {page_number} 页的预览图片为空：{candidate.name}")
                try:
                    ink_observations[page_number] = png_has_visible_ink(candidate)
                except (ValueError, struct.error) as error:
                    raise RuntimeError(f"PDF 第 {page_number} 页预览损坏：{candidate.name}；{error}") from error
            for page_number in range(1, len(reader.pages) + 1):
                candidate = found[page_number][0]
                destination = generation / f"page-{page_number}.png"
                os.replace(candidate, destination)
                generated[page_number] = destination
    pages: list[PdfPreviewPage] = []
    for page_number, text in enumerate(texts, start=1):
        image = generated.get(page_number)
        ink = ink_observations.get(page_number) if image is not None else None
        kind = _page_kind(page_number, text)
        pages.append(
            PdfPreviewPage(
                page_number=page_number,
                page_kind=kind,
                extracted_text_length=len(text.strip()),
                image_path=image.relative_to(output_directory).as_posix() if image is not None else None,
                image_sha256=sha256(image.read_bytes()).hexdigest() if image is not None else None,
                ink_detected=ink,
                sparse=kind == "body" and len(text.strip()) < 120,
            )
        )
    gallery = generation / "index.html"
    cards = "\n".join(f'<figure><figcaption>第{p.page_number}页</figcaption><img loading="lazy" src="{escape(Path(p.image_path).name, quote=True)}" alt="第{p.page_number}页"></figure>'
                      for p in pages if p.image_path)
    gallery.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>当前PDF页面预览</title>'
                       '<style>body{background:#eee;margin:24px;font:16px sans-serif}figure{margin:20px auto;max-width:900px}img{width:100%;background:white}figcaption{margin:8px 0}</style>'
                       f'<h1>当前PDF：{len(pages)}页</h1><p>本入口只含本次完整预览，历史文件保留。</p>{cards}</html>', encoding="utf-8")
    manifest = PdfPreviewManifest(
        schema_version=2,
        generated_at=datetime.now(UTC),
        pdf_sha256=sha256(pdf_path.read_bytes()).hexdigest(),
        renderer_available=executable is not None,
        needs_manual_visual_review=True,
        pages=pages,
        gallery_path=gallery.relative_to(output_directory).as_posix(),
        gallery_sha256=sha256(gallery.read_bytes()).hexdigest(),
    )
    path = preview_manifest_path(output_directory)
    temporary = output_directory / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path, manifest
