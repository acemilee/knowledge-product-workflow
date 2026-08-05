"""Markdown assembly, PDF rendering, and delivery manifest generation."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from knowledge_product_workflow.callouts import (
    CalloutFormatError,
    reader_component_leaks,
    render_qmd_markdown,
    render_reader_markdown,
)
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.localization import QMD_MISSING, QUARTO_UNAVAILABLE
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.presentation import (
    PRESENTATION_PROFILE_ID,
    PUBLICATION_METADATA_FILENAME,
    PublicationMetadata,
    load_or_create_publication_metadata,
    publication_profile_path,
)
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.quality_exceptions import controlled_exception_proofs
from knowledge_product_workflow.quarto import resolve_quarto
from knowledge_product_workflow.workflows.product_brief import (
    parse_detail_constraints,
    parse_presentation_overrides,
)
from knowledge_product_workflow.workspace import Workspace

_PDF_STYLE_FILE = "knowledge-book-style.tex"
_PDF_COVER_FILE = "knowledge-book-cover.tex"
_PDF_TOC_FILE = "knowledge-book-toc.tex"
_PDF_COMPONENTS_FILE = "knowledge-book-components.tex"
_PDF_COMPONENTS_FILTER_FILE = "knowledge-book-components.lua"
_PDF_PROFILE = "knowledge-product-pdf-v4"
_PDF_STYLE_MARKER = "% knowledge-product-pdf-v4-style"
_PDF_COVER_MARKER = "% knowledge-product-pdf-v4-cover"
_PDF_TOC_MARKER = "% knowledge-product-pdf-v4-toc"
_PDF_COMPONENTS_MARKER = "% knowledge-product-pdf-v4-components"
_PDF_COMPONENTS_FILTER_MARKER = "-- knowledge-product-pdf-v4-components-filter"
_PDF_DIAGRAM_LAYOUT_MARKER = "-- knowledge-product-pdf-v4-diagram-layout"
_PDF_INLINE_CODE_MARKER = "% knowledge-product-pdf-v4-inline-code"
_PDF_TEMPLATE_DIRECTORY = ("templates", "pdf")
_PDF_RENDER_LOG_FILENAME = "pdf-render.log"
_PDF_OVERFLOW_PATTERNS = ("Overfull \\hbox", "Overfull \\vbox")
_BOOK_INPUT_MANIFEST_FILENAME = "book-input-manifest.json"
_RELEASE_MANIFEST_SCHEMA_VERSION = 2
_ASSET_REFERENCE = re.compile(r"!\[[^\]]*\]\((?P<target>assets/[^)\s]+)")
_PDF_LAYOUT_MARKERS = (
    "papersize: a4",
    "geometry: left=16mm,right=16mm,top=18mm,bottom=18mm",
    "documentclass: extreport",
)
_PDF_COVER_MARKERS = (
    "title-block: false",
    "include-before-body:",
    _PDF_COVER_FILE,
)
_PDF_TOC_MARKERS = ("toc: true", "toc-title: 目录", "toc-own-page: true", _PDF_TOC_FILE)
_PDF_CHINESE_STYLE_MARKERS = (
    "pdf-engine: xelatex",
    "CJKmainfont: Microsoft YaHei",
    "mainfont: TeX Gyre Termes",
    "monofont: Consolas",
    "include-in-header:",
    _PDF_STYLE_FILE,
    _PDF_COMPONENTS_FILE,
    "filters:",
    _PDF_COMPONENTS_FILTER_FILE,
    _PDF_PROFILE,
)
_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_TEX_DOCUMENTCLASS = re.compile(
    r"\\documentclass(?:\[(?P<options>[^\]]*)\])?\{(?P<documentclass>[^}]+)\}"
)
_PDF_COMPONENT_LEAK = re.compile(r"\{\.kp-(?:source|key-takeaway|practice|note)\}")
_TEX_ESCAPE = str.maketrans(
    {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
)
_AUDIENCE_LABELS = {
    "beginner": "面向入门级中文读者",
    "intermediate": "面向进阶级中文读者",
    "expert": "面向专家级中文读者",
}


class HashedArtifact(BaseModel):
    """One local immutable input recorded in an internal release manifest."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BookInputManifest(BaseModel):
    """The exact source graph used to assemble one Markdown/QMD book revision."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    pdf_profile: str = _PDF_PROFILE
    inputs: list[HashedArtifact] = Field(min_length=1)


def book_input_manifest_path(workspace: Workspace) -> Path:
    """Return the internal hash manifest associated with the current assembled book."""
    return workspace.root / "delivery" / _BOOK_INPUT_MANIFEST_FILENAME


def workspace_relative_path(path: Path, workspace: Workspace) -> str:
    """Encode one existing or planned path as a canonical workspace-relative POSIX path."""
    root = workspace.root.resolve()
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError(f"路径位于工作区之外，不能写入受控清单：{path}") from error


def _workspace_path_from_manifest(workspace: Workspace, stored_path: str) -> Path:
    """Resolve a manifest path only when it uses canonical, non-escaping POSIX syntax."""
    if not stored_path or "\\" in stored_path:
        raise ValueError("交付清单路径必须是规范的工作区相对 POSIX 路径")
    relative = PurePosixPath(stored_path)
    if (
        relative.is_absolute()
        or relative.as_posix() != stored_path
        or any(part in {".", ".."} for part in relative.parts)
    ):
        raise ValueError("交付清单路径不能是绝对路径或包含路径逃逸")
    candidate = workspace.root.resolve().joinpath(*relative.parts).resolve()
    if not candidate.is_relative_to(workspace.root.resolve()):
        raise ValueError("交付清单路径越出工作区")
    return candidate


def _relative(workspace: Workspace, path: Path) -> str:
    return workspace_relative_path(path, workspace)


def _existing_files(paths: Iterable[Path]) -> list[Path]:
    return [path for path in paths if path.is_file()]


def _reader_asset_paths(workspace: Workspace, markdown: str) -> list[Path]:
    paths: list[Path] = []
    for match in _ASSET_REFERENCE.finditer(markdown):
        target = Path(match.group("target"))
        candidate = workspace.root / target
        if candidate.is_file() and candidate not in paths:
            paths.append(candidate)
        if target.parent.as_posix() == "assets/figures":
            diagram_id = target.stem
            paths.extend(
                _existing_files(
                    (
                        workspace.root / "assets" / "figures" / f"{diagram_id}.png",
                        workspace.root / "runs" / "diagrams" / f"{diagram_id}.spec.json",
                        workspace.root / "runs" / "diagrams" / f"{diagram_id}.d2",
                        workspace.root / "runs" / "diagrams" / f"{diagram_id}.svg",
                        workspace.root / "runs" / "diagrams" / f"{diagram_id}.json",
                    )
                )
            )
    return list(dict.fromkeys(paths))


def _book_input_paths(workspace: Workspace, chapter_paths: list[Path], markdown: str, qmd_path: Path) -> list[Path]:
    """Collect only current source graph files, never arbitrary workspace residue."""
    paths = [
        workspace.root / "planning" / "product-brief.md",
        publication_profile_path(workspace),
        workspace.root / "planning" / "curriculum-blueprint.md",
        workspace.root / "chapters" / "chapter-01-sample.approved",
        workspace.root / "research" / "source-ledger.jsonl",
        workspace.root / "research" / "assumption-ledger.jsonl",
        workspace.root / "delivery" / "book.md",
        qmd_path,
        workspace.root / "delivery" / PUBLICATION_METADATA_FILENAME,
        workspace.root / "delivery" / _PDF_COVER_FILE,
        workspace.root / "delivery" / _PDF_STYLE_FILE,
        workspace.root / "delivery" / _PDF_TOC_FILE,
        workspace.root / "delivery" / _PDF_COMPONENTS_FILE,
        workspace.root / "delivery" / _PDF_COMPONENTS_FILTER_FILE,
    ]
    for chapter_path in chapter_paths:
        number = int(chapter_path.stem.split("-")[1])
        paths.extend(
            (
                chapter_path,
                workspace.root / "chapters" / f"chapter-{number:02d}-contract.md",
                workspace.root / "runs" / "chapter-compliance" / f"chapter-{number:02d}.json",
                workspace.root / "runs" / "humanize" / f"chapter-{number:02d}.json",
            )
        )
    paths.extend(_reader_asset_paths(workspace, markdown))
    return list(dict.fromkeys(_existing_files(paths)))


def _build_book_input_manifest(
    workspace: Workspace, chapter_paths: list[Path], markdown: str, qmd_path: Path
) -> BookInputManifest:
    return BookInputManifest(
        inputs=[
            HashedArtifact(path=_relative(workspace, path), sha256=_sha256(path))
            for path in _book_input_paths(workspace, chapter_paths, markdown, qmd_path)
        ]
    )


def _write_book_input_manifest(
    workspace: Workspace,
    chapter_paths: list[Path],
    markdown: str,
    qmd_path: Path,
    *,
    manifest: BookInputManifest | None = None,
) -> Path:
    manifest = manifest or _build_book_input_manifest(workspace, chapter_paths, markdown, qmd_path)
    output = book_input_manifest_path(workspace)
    output.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    workspace.append_event(
        "book_inputs_frozen",
        {"manifest": _relative(workspace, output), "input_count": len(manifest.inputs)},
    )
    return output


def _digest_if_file(path: Path) -> str | None:
    return _sha256(path) if path.is_file() else None


def _read_book_input_manifest(path: Path) -> BookInputManifest | None:
    if not path.is_file():
        return None
    try:
        return BookInputManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _archive_stale_delivery_derivatives(workspace: Workspace) -> list[str]:
    """Move obsolete render outputs out of delivery before the next controlled render."""
    candidates = (
        workspace.root / "delivery" / "book.pdf",
        workspace.root / "delivery" / "book.tex",
        workspace.root / "delivery" / "delivery-manifest.yaml",
        book_input_manifest_path(workspace),
    )
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return []
    archive = workspace.root / "runs" / "delivery-history" / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    )
    archive.mkdir(parents=True, exist_ok=False)
    moved: list[str] = []
    for source in existing:
        target = archive / source.name
        os.replace(source, target)
        moved.append(_relative(workspace, target))
    workspace.append_event("delivery_derivatives_archived", {"artifacts": moved})
    return moved


def _chapter_bodies(chapter_paths: list[Path]) -> list[str]:
    return [parse_markdown_artifact(path.read_text(encoding="utf-8"))[1].strip() for path in chapter_paths]


def _reader_book_from_bodies(bodies: list[str]) -> str:
    return "\n\n".join(render_reader_markdown(body).strip() for body in bodies) + "\n"


def _qmd_book_from_bodies(bodies: list[str]) -> str:
    return "\n\n".join(render_qmd_markdown(body).strip() for body in bodies) + "\n"


def delivery_component_leak_findings(workspace: Workspace, *, include_pdf: bool) -> list[QualityFinding]:
    """Reject implementation-only component syntax from reader deliverables."""

    findings: list[QualityFinding] = []
    markdown_path = workspace.root / "delivery" / "book.md"
    if markdown_path.is_file():
        for line_number in reader_component_leaks(
            markdown_path.read_text(encoding="utf-8"), allow_fenced_divs=False
        ):
            findings.append(
                _release_finding(
                    "PDF_INTERNAL_COMPONENT_LEAK",
                    f"delivery/book.md:第 {line_number} 行",
                    "读者 Markdown 泄漏了 KP 内部提示框标记；请重新受控汇编。",
                )
            )
    qmd_path = workspace.root / "delivery" / "book.qmd"
    if qmd_path.is_file():
        for line_number in reader_component_leaks(
            qmd_path.read_text(encoding="utf-8"), allow_fenced_divs=True
        ):
            findings.append(
                _release_finding(
                    "PDF_INTERNAL_COMPONENT_LEAK",
                    f"delivery/book.qmd:第 {line_number} 行",
                    "Quarto 源稿包含未转换的 KP 内部提示框标记；请重新受控汇编。",
                )
            )
    if include_pdf:
        pdf_path = workspace.root / "delivery" / "book.pdf"
        if pdf_path.is_file():
            try:
                text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf_path).pages)
            except (OSError, ValueError):
                text = ""
            if _PDF_COMPONENT_LEAK.search(text):
                findings.append(
                    _release_finding(
                        "PDF_INTERNAL_COMPONENT_LEAK",
                        "delivery/book.pdf",
                        "PDF 泄漏了 KP 内部提示框标记；禁止发布该交付物。",
                    )
                )
    return findings


def assemble_book(workspace: Workspace) -> Path:
    """Combine chapter bodies into the canonical Markdown source artifact."""
    chapter_paths = sorted(workspace.root.joinpath("chapters").glob("chapter-[0-9][0-9].md"))
    if not chapter_paths:
        raise FileNotFoundError("至少需要一个章节才能汇编书稿")
    bodies = _chapter_bodies(chapter_paths)
    output = workspace.root / "delivery" / "book.md"
    qmd_path = workspace.root / "delivery" / "book.qmd"
    render_contract_paths = (
        output,
        qmd_path,
        workspace.root / "delivery" / PUBLICATION_METADATA_FILENAME,
        workspace.root / "delivery" / _PDF_COVER_FILE,
        workspace.root / "delivery" / _PDF_STYLE_FILE,
        workspace.root / "delivery" / _PDF_TOC_FILE,
        workspace.root / "delivery" / _PDF_COMPONENTS_FILE,
        workspace.root / "delivery" / _PDF_COMPONENTS_FILTER_FILE,
    )
    before = {path: _digest_if_file(path) for path in render_contract_paths}
    prior_input_manifest = _read_book_input_manifest(book_input_manifest_path(workspace))
    try:
        markdown = _reader_book_from_bodies(bodies)
        qmd_markdown = _qmd_book_from_bodies(bodies)
    except CalloutFormatError as error:
        raise ValueError(f"读者提示框结构无效，无法安全汇编：{error}") from error
    output.write_text(markdown, encoding="utf-8")
    qmd_path = create_quarto_source(workspace, output, qmd_markdown=qmd_markdown)
    current_input_manifest = _build_book_input_manifest(workspace, chapter_paths, markdown, qmd_path)
    render_inputs_changed = any(before[path] != _digest_if_file(path) for path in render_contract_paths)
    existing_derivatives = any(
        path.is_file()
        for path in (
            workspace.root / "delivery" / "book.pdf",
            workspace.root / "delivery" / "book.tex",
            workspace.root / "delivery" / "delivery-manifest.yaml",
        )
    )
    input_graph_changed = prior_input_manifest != current_input_manifest
    if render_inputs_changed or input_graph_changed or (existing_derivatives and prior_input_manifest is None):
        _archive_stale_delivery_derivatives(workspace)
    _write_book_input_manifest(
        workspace, chapter_paths, markdown, qmd_path, manifest=current_input_manifest
    )
    workspace.append_event("book_assembled", {"chapter_count": len(chapter_paths)})
    return output


def _constraints_for_workspace(workspace: Workspace) -> DetailConstraints | None:
    brief_path = workspace.root / "planning" / "product-brief.md"
    if not brief_path.exists():
        return None
    _, body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    return parse_detail_constraints(body)


def _title_from_markdown(markdown: str) -> str | None:
    match = _TITLE.search(markdown)
    return match.group(1).strip() if match else None


def _book_title(workspace: Workspace, markdown: str) -> str:
    brief_path = workspace.root / "planning" / "product-brief.md"
    if brief_path.exists():
        _, brief_body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
        brief_title = _title_from_markdown(brief_body)
        if brief_title:
            return brief_title
    return _title_from_markdown(markdown) or workspace.root.name


def _template_text(filename: str) -> str:
    asset = files("knowledge_product_workflow")
    for part in (*_PDF_TEMPLATE_DIRECTORY, filename):
        asset = asset.joinpath(part)
    return asset.read_text(encoding="utf-8")


def _write_pdf_contract_assets(workspace: Workspace, metadata: PublicationMetadata) -> None:
    delivery = workspace.root / "delivery"
    style_path = delivery / _PDF_STYLE_FILE
    cover_path = delivery / _PDF_COVER_FILE
    style_path.write_text(_template_text(_PDF_STYLE_FILE), encoding="utf-8")
    (delivery / _PDF_TOC_FILE).write_text(_template_text(_PDF_TOC_FILE), encoding="utf-8")
    (delivery / _PDF_COMPONENTS_FILE).write_text(
        _template_text(_PDF_COMPONENTS_FILE), encoding="utf-8"
    )
    (delivery / _PDF_COMPONENTS_FILTER_FILE).write_text(
        _template_text(_PDF_COMPONENTS_FILTER_FILE), encoding="utf-8"
    )
    cover_prefix = (
        "% This file is generated by the workflow; do not hand-edit it.\n"
        f"\\providecommand{{\\KnowledgeProductTitle}}{{{metadata.title.translate(_TEX_ESCAPE)}}}\n"
        f"\\providecommand{{\\KnowledgeProductSubtitle}}{{{metadata.subtitle.translate(_TEX_ESCAPE)}}}\n"
        f"\\providecommand{{\\KnowledgeProductAudience}}{{{metadata.audience_label.translate(_TEX_ESCAPE)}}}\n"
        f"\\providecommand{{\\KnowledgeProductEdition}}{{{metadata.edition_label.translate(_TEX_ESCAPE)}}}\n"
        f"\\providecommand{{\\KnowledgeProductPublicationDate}}{{{metadata.publication_date.isoformat()}}}\n"
        f"\\providecommand{{\\KnowledgeProductCoverLabel}}{{{metadata.cover_label.translate(_TEX_ESCAPE)}}}\n"
        f"\\providecommand{{\\KnowledgeProductFooterLabel}}{{{metadata.footer_label.translate(_TEX_ESCAPE)}}}\n"
        f"\\providecommand{{\\KnowledgeProductCopyright}}{{{metadata.copyright_line.translate(_TEX_ESCAPE)}}}\n"
    )
    cover_path.write_text(cover_prefix + _template_text(_PDF_COVER_FILE), encoding="utf-8")


def missing_pdf_source_contracts(qmd_path: Path) -> list[str]:
    """Return deterministic blockers for missing fixed PDF source-contract pieces."""
    source = qmd_path.read_text(encoding="utf-8") if qmd_path.is_file() else ""
    missing: list[str] = []
    font_match = re.search(r"(?m)^\s*fontsize:\s*(?P<size>\d+)pt\s*$", source)
    if not all(marker in source for marker in _PDF_LAYOUT_MARKERS) or font_match is None:
        missing.append("LAYOUT_CONTRACT_MISMATCH")
    publication_path = qmd_path.parent / PUBLICATION_METADATA_FILENAME
    try:
        publication = PublicationMetadata.model_validate(
            yaml.safe_load(publication_path.read_text(encoding="utf-8"))
        )
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        publication = None
    if publication is not None and (
        font_match is None
        or int(font_match.group("size")) != publication.body_font_size
        or "documentclass: extreport" not in source
    ):
        missing.append("PDF_FONT_SIZE_CONTRACT_FAILED")
    cover_path = qmd_path.parent / _PDF_COVER_FILE
    cover = cover_path.read_text(encoding="utf-8") if cover_path.is_file() else ""
    if not all(marker in source for marker in _PDF_COVER_MARKERS) or _PDF_COVER_MARKER not in cover:
        missing.append("PDF_COVER_CONTRACT_MISSING")
    if not all(marker in source for marker in _PDF_TOC_MARKERS):
        missing.append("PDF_TOC_CONTRACT_MISSING")
    style_path = qmd_path.parent / _PDF_STYLE_FILE
    style = style_path.read_text(encoding="utf-8") if style_path.is_file() else ""
    toc_path = qmd_path.parent / _PDF_TOC_FILE
    components_path = qmd_path.parent / _PDF_COMPONENTS_FILE
    filter_path = qmd_path.parent / _PDF_COMPONENTS_FILTER_FILE
    toc = toc_path.read_text(encoding="utf-8") if toc_path.is_file() else ""
    components = components_path.read_text(encoding="utf-8") if components_path.is_file() else ""
    component_filter = filter_path.read_text(encoding="utf-8") if filter_path.is_file() else ""
    if (
        not all(marker in source for marker in _PDF_CHINESE_STYLE_MARKERS)
        or _PDF_STYLE_MARKER not in style
        or _PDF_TOC_MARKER not in toc
        or _PDF_COMPONENTS_MARKER not in components
        or _PDF_COMPONENTS_FILTER_MARKER not in component_filter
        or _PDF_DIAGRAM_LAYOUT_MARKER not in component_filter
    ):
        missing.append("PDF_CHINESE_STYLE_CONTRACT_MISSING")
    if _PDF_INLINE_CODE_MARKER not in components or "\\renewcommand{\\texttt}" not in components:
        missing.append("PDF_INLINE_CODE_STYLE_CONTRACT_MISSING")
    publication = publication_path.read_text(encoding="utf-8") if publication_path.is_file() else ""
    if PRESENTATION_PROFILE_ID not in publication:
        missing.append("PDF_PUBLICATION_METADATA_MISSING")
    return missing


def create_quarto_source(
    workspace: Workspace, markdown_path: Path, *, qmd_markdown: str | None = None
) -> Path:
    """Create the derived Quarto source using the approved fixed layout profile."""
    if not markdown_path.is_file():
        raise FileNotFoundError(f"缺少规范 Markdown：{markdown_path}")
    constraints = _constraints_for_workspace(workspace)
    if constraints is not None and constraints.layout_profile != "a4-standard":
        raise ValueError(f"不支持的版式标识：{constraints.layout_profile}")
    qmd_path = workspace.root / "delivery" / "book.qmd"
    canonical_markdown = markdown_path.read_text(encoding="utf-8")
    try:
        markdown = render_reader_markdown(canonical_markdown)
        rendered_markdown = qmd_markdown if qmd_markdown is not None else render_qmd_markdown(canonical_markdown)
    except CalloutFormatError as error:
        raise ValueError(f"Quarto 提示框结构无效：{error}") from error
    if markdown != canonical_markdown:
        markdown_path.write_text(markdown, encoding="utf-8")
    title = _book_title(workspace, markdown)
    audience = _AUDIENCE_LABELS[
        constraints.audience_level.value if constraints else "intermediate"
    ]
    presentation = None
    brief_path = workspace.root / "planning" / "product-brief.md"
    if brief_path.is_file():
        _, brief_body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
        presentation = parse_presentation_overrides(brief_body)
    publication_metadata = load_or_create_publication_metadata(
        workspace,
        title=title,
        audience_label=audience,
        language=constraints.language if constraints else "zh-CN",
        overrides=presentation,
    )
    _write_pdf_contract_assets(workspace, publication_metadata)
    metadata = {
        "title": title,
        "title-block": False,
        "pdf-engine": "xelatex",
        "format": {
            "pdf": {
                "papersize": "a4",
                "fontsize": f"{publication_metadata.body_font_size}pt",
                "geometry": "left=16mm,right=16mm,top=18mm,bottom=18mm",
                "documentclass": "extreport",
                "keep-tex": True,
            }
        },
        "toc": True,
        "toc-title": "目录",
        "toc-depth": 2,
        "toc-own-page": True,
        "include-in-header": [_PDF_STYLE_FILE, _PDF_TOC_FILE, _PDF_COMPONENTS_FILE],
        "include-before-body": [_PDF_COVER_FILE],
        "filters": [_PDF_COMPONENTS_FILTER_FILE],
        "kp-pdf-profile": _PDF_PROFILE,
        "resource-path": [".."],
        "lang": constraints.language if constraints else "zh-CN",
        "mainfont": "TeX Gyre Termes",
        "sansfont": "TeX Gyre Heros",
        "monofont": "Consolas",
        "CJKmainfont": "Microsoft YaHei",
        "CJKsansfont": "Microsoft YaHei",
        "CJKmonofont": "Microsoft YaHei",
    }
    source = f"---\n{yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)}---\n{rendered_markdown}"
    qmd_path.write_text(source, encoding="utf-8")
    return qmd_path


def count_pdf_pages(pdf_path: Path) -> int:
    """Read the actual page count from a valid rendered PDF."""
    return len(PdfReader(pdf_path).pages)


def _count_outline_nodes(nodes: object) -> int:
    """Count nested PDF outline entries returned by pypdf."""
    if not isinstance(nodes, list):
        return 0
    count = 0
    for node in nodes:
        if isinstance(node, list):
            count += _count_outline_nodes(node)
        else:
            count += 1
    return count


def count_pdf_bookmarks(pdf_path: Path) -> int:
    """Return the number of reader-visible PDF outline entries."""
    return _count_outline_nodes(PdfReader(pdf_path).outline)


def count_pdf_external_links(pdf_path: Path) -> int:
    """Return reader-visible URI links embedded in a rendered PDF."""
    count = 0
    for page in PdfReader(pdf_path).pages:
        for reference in page.get("/Annots", []):
            annotation = reference.get_object()
            action = annotation.get("/A")
            if action is not None and action.get("/S") == "/URI" and action.get("/URI"):
                count += 1
    return count


def pdf_font_size_contract_findings(workspace: Workspace) -> list[QualityFinding]:
    """Verify the rendered Pandoc TeX uses the profile's supported base font size."""

    metadata_path = workspace.root / "delivery" / PUBLICATION_METADATA_FILENAME
    tex_path = workspace.root / "delivery" / "book.tex"
    try:
        metadata = PublicationMetadata.model_validate(
            yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        )
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return [
            _release_finding(
                "PDF_FONT_SIZE_CONTRACT_FAILED",
                "delivery/publication-metadata.yaml",
                "无法读取当前出版配置中的正文字号。",
            )
        ]
    if not tex_path.is_file():
        return [
            _release_finding(
                "PDF_FONT_SIZE_CONTRACT_FAILED",
                "delivery/book.tex",
                "未保留 XeLaTeX 源稿，无法验证 PDF 的实际正文字号。",
            )
        ]
    match = _TEX_DOCUMENTCLASS.search(tex_path.read_text(encoding="utf-8", errors="replace"))
    expected = f"{metadata.body_font_size}pt"
    options = {item.strip() for item in (match.group("options") or "").split(",")} if match else set()
    if match is None or match.group("documentclass") != "extreport" or expected not in options:
        return [
            _release_finding(
                "PDF_FONT_SIZE_CONTRACT_FAILED",
                "delivery/book.tex",
                f"XeLaTeX 实际文档类或基准字号与出版配置不一致；预期 extreport + {expected}。",
            )
        ]
    return []


def render_pdf(qmd_path: Path, output_dir: Path, workspace: Workspace | None = None) -> Path:
    """Render a Quarto source file to PDF, failing clearly when Quarto is unavailable."""
    qmd_path = qmd_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    expected_output = output_dir / f"{qmd_path.stem}.pdf"
    cwd = qmd_path.parent
    if workspace is not None:
        workspace_root = workspace.root.expanduser().resolve()
        expected_qmd = workspace_root / "delivery" / "book.qmd"
        expected_output_dir = workspace_root / "delivery"
        if qmd_path != expected_qmd or output_dir != expected_output_dir:
            workspace.append_event("pdf_render_failed", {"reason": "path_contract_invalid"})
            raise ValueError("PDF 渲染路径契约无效：只能渲染工作区 delivery/book.qmd 到 delivery/ 目录。")
        expected_output = expected_output_dir / "book.pdf"
        cwd = workspace_root
    if not qmd_path.is_file():
        if workspace is not None:
            workspace.append_event("pdf_render_failed", {"reason": "qmd_missing"})
        raise FileNotFoundError(f"{QMD_MISSING}：{qmd_path}")
    qmd_source = qmd_path.read_text(encoding="utf-8")
    qmd_leaks = reader_component_leaks(qmd_source, allow_fenced_divs=True)
    if qmd_leaks:
        if workspace is not None:
            workspace.append_event("pdf_render_failed", {"reason": "internal_component_leaked", "lines": qmd_leaks})
        raise RuntimeError("Quarto 源稿泄漏了未转换的 KP 提示框标记；请重新受控汇编。")
    quarto = resolve_quarto()
    if not quarto.available or quarto.path is None:
        if workspace is not None:
            workspace.append_event(
                "pdf_render_failed",
                {"reason": "quarto_unavailable", "failed_candidates": list(quarto.failed_candidates)},
            )
        raise RuntimeError(f"{QUARTO_UNAVAILABLE}；请运行 kp doctor 查看已检查的位置。")
    render_log = ""
    command = [str(quarto.path), "render", str(qmd_path), "--to", "pdf", "--output-dir", str(output_dir)]
    log_prefix = "\n".join(
        (
            f"命令：{subprocess.list2cmdline(command)}",
            f"有效工作目录：{cwd}",
            f"预期输出：{expected_output}",
            "Quarto 输出：",
        )
    )
    try:
        environment = os.environ.copy()
        environment["QUARTO_TEXLIVE_AUTO_INSTALL"] = "false"
        completed = subprocess.run(
            command,
            check=True,
            timeout=120,
            env=environment,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        render_log = log_prefix + "\n" + (completed.stdout or "") + (completed.stderr or "")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        render_log = log_prefix + "\n" + (getattr(error, "stdout", "") or "") + (getattr(error, "stderr", "") or "")
        if workspace is not None:
            _write_pdf_render_log(workspace, render_log)
        if workspace is not None:
            workspace.append_event("pdf_render_failed", {"reason": type(error).__name__})
        raise RuntimeError("Quarto PDF 渲染失败") from error
    if workspace is not None:
        _write_pdf_render_log(workspace, render_log)
    overflow = [pattern for pattern in _PDF_OVERFLOW_PATTERNS if pattern in render_log]
    if overflow:
        if workspace is not None:
            workspace.append_event("pdf_render_failed", {"reason": "tex_overflow", "patterns": overflow})
        raise RuntimeError("PDF 渲染检测到 TeX 版式溢出；请修订表格、代码块或长链接后重新渲染。")
    if not expected_output.is_file():
        nested_outputs = [
            candidate
            for candidate in output_dir.glob(f"**/{expected_output.name}")
            if candidate != expected_output
        ]
        if workspace is not None:
            workspace.append_event(
                "pdf_render_failed",
                {
                    "reason": "output_path_contract_failed" if nested_outputs else "output_missing",
                    "expected_output": str(expected_output),
                    "nested_outputs": [str(candidate) for candidate in nested_outputs],
                },
            )
        if nested_outputs:
            raise RuntimeError(
                "PDF 输出路径契约失败：预期文件未生成，检测到嵌套输出；"
                f"请查看渲染日志：{pdf_render_log_path(workspace) if workspace else '无工作区日志'}"
            )
        raise RuntimeError("Quarto 已结束，但未生成预期的 PDF 文件")
    if workspace is not None and (
        (workspace.root / "delivery" / PUBLICATION_METADATA_FILENAME).is_file()
        or "kp-pdf-profile:" in qmd_source
    ):
        component_findings = delivery_component_leak_findings(workspace, include_pdf=True)
        font_findings = pdf_font_size_contract_findings(workspace)
        if component_findings or font_findings:
            workspace.append_event(
                "pdf_render_failed",
                {
                    "reason": "delivery_contract_failed",
                    "component_leaks": len(component_findings),
                    "font_contract_failures": len(font_findings),
                },
            )
            details = "；".join(finding.message for finding in component_findings + font_findings)
            raise RuntimeError(f"PDF 交付契约失败：{details}")
    if workspace is not None:
        workspace.append_event(
            "pdf_rendered",
            {
                "pdf": str(expected_output),
                "sha256": _sha256(expected_output),
                "page_count": count_pdf_pages(expected_output),
                "bookmark_count": count_pdf_bookmarks(expected_output),
            },
        )
    return expected_output


def pdf_render_log_path(workspace: Workspace) -> Path:
    """Return the internal Quarto/XeLaTeX render log path for a workspace."""
    return workspace.root / "runs" / _PDF_RENDER_LOG_FILENAME


def _write_pdf_render_log(workspace: Workspace, contents: str) -> Path:
    path = pdf_render_log_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _release_finding(code: str, location: str, message: str) -> QualityFinding:
    return QualityFinding(code=code, severity=Severity.BLOCKER, location=location, message=message)


def _expected_book_markdown(workspace: Workspace) -> str | None:
    chapters = sorted(workspace.root.joinpath("chapters").glob("chapter-[0-9][0-9].md"))
    if not chapters:
        return None
    try:
        return _reader_book_from_bodies(_chapter_bodies(chapters))
    except (OSError, ValueError, CalloutFormatError):
        return None


def release_readiness_findings(workspace: Workspace) -> list[QualityFinding]:
    """Recompute the upstream graph required for a v1.0 delivery release."""
    brief_path = workspace.root / "planning" / "product-brief.md"
    # Library-level helpers retain a narrow legacy mode for callers without a KP Brief.
    if not brief_path.is_file():
        return []
    findings: list[QualityFinding] = []
    try:
        brief_metadata, _ = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [_release_finding("RELEASE_BRIEF_INVALID", "planning/product-brief.md", "产品 Brief 无法读取。")]
    if brief_metadata.status is not ArtifactStatus.APPROVED:
        findings.append(
            _release_finding("RELEASE_BRIEF_NOT_APPROVED", "planning/product-brief.md", "产品 Brief 尚未批准。")
        )
    try:
        from knowledge_product_workflow.workflows.curriculum import (
            require_current_submitted_blueprint,
        )

        blueprint_path, blueprint_metadata, blueprint = require_current_submitted_blueprint(workspace)
    except (OSError, ValueError) as error:
        return findings + [
            _release_finding("RELEASE_BLUEPRINT_STALE", "planning/curriculum-blueprint.md", str(error))
        ]
    if blueprint_metadata.status is not ArtifactStatus.APPROVED:
        findings.append(
            _release_finding(
                "RELEASE_BLUEPRINT_NOT_APPROVED",
                _relative(workspace, blueprint_path),
                "课程蓝图尚未批准。",
            )
        )
    if not (workspace.root / "chapters" / "chapter-01-sample.approved").is_file():
        findings.append(
            _release_finding(
                "RELEASE_SAMPLE_NOT_APPROVED",
                "chapters/chapter-01-sample.approved",
                "第一章样章尚未批准。",
            )
        )
    from knowledge_product_workflow.chapter_compliance import validate_current_chapter_compliance
    from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract

    for chapter in blueprint.chapters:
        chapter_path = workspace.root / "chapters" / f"chapter-{chapter.chapter_number:02d}.md"
        try:
            require_current_chapter_contract(workspace, chapter.chapter_number)
        except ValueError as error:
            findings.append(
                _release_finding(
                    "RELEASE_CONTRACT_STALE",
                    f"chapters/chapter-{chapter.chapter_number:02d}-contract.md",
                    str(error),
                )
            )
        if not chapter_path.is_file():
            findings.append(
                _release_finding(
                    "RELEASE_CHAPTER_MISSING",
                    f"chapters/chapter-{chapter.chapter_number:02d}.md",
                    "当前蓝图要求的章节正文不存在。",
                )
            )
            continue
        findings.extend(validate_current_chapter_compliance(workspace, chapter_path))

    markdown_path = workspace.root / "delivery" / "book.md"
    qmd_path = workspace.root / "delivery" / "book.qmd"
    input_manifest_path = book_input_manifest_path(workspace)
    if not markdown_path.is_file() or not qmd_path.is_file():
        findings.append(
            _release_finding(
                "RELEASE_BOOK_INPUTS_MISSING",
                "delivery/book.md",
                "缺少受控 Markdown 或 Quarto 源稿；请重新汇编。",
            )
        )
        return findings
    findings.extend(delivery_component_leak_findings(workspace, include_pdf=False))
    expected_markdown = _expected_book_markdown(workspace)
    if expected_markdown is None or markdown_path.read_text(encoding="utf-8") != expected_markdown:
        findings.append(
            _release_finding(
                "RELEASE_BOOK_STALE",
                "delivery/book.md",
                "书稿与当前章节正文不一致；请重新汇编。",
            )
        )
    if not input_manifest_path.is_file():
        findings.append(
            _release_finding(
                "RELEASE_BOOK_INPUT_MANIFEST_MISSING",
                "delivery/book-input-manifest.json",
                "缺少书稿输入哈希清单；请重新汇编。",
            )
        )
        return findings
    try:
        input_manifest = BookInputManifest.model_validate_json(
            input_manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        findings.append(
            _release_finding(
                "RELEASE_BOOK_INPUT_MANIFEST_INVALID",
                "delivery/book-input-manifest.json",
                "书稿输入哈希清单格式无效。",
            )
        )
        return findings
    if input_manifest.pdf_profile != _PDF_PROFILE:
        findings.append(
            _release_finding(
                "RELEASE_BOOK_INPUT_MANIFEST_INVALID",
                "delivery/book-input-manifest.json",
                "书稿输入清单绑定了不受支持的 PDF 视觉 profile。",
            )
        )
    for artifact in input_manifest.inputs:
        try:
            path = _workspace_path_from_manifest(workspace, artifact.path)
        except ValueError:
            path = None
        if path is None or not path.is_file() or _sha256(path) != artifact.sha256:
            findings.append(
                _release_finding(
                    "RELEASE_INPUT_HASH_MISMATCH",
                    artifact.path,
                    "发布输入文件缺失、越出工作区或已被修改；请重新执行上游阶段。",
                )
            )
    return findings


def delivery_manifest_issues(workspace: Workspace) -> list[str]:
    """Return human-readable stale reasons for an already created delivery manifest."""
    path = workspace.root / "delivery" / "delivery-manifest.yaml"
    if not path.is_file():
        return ["缺少交付清单"]
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ["交付清单格式无效"]
    if not isinstance(raw, dict):
        return ["交付清单格式无效"]
    if raw.get("schema_version") != _RELEASE_MANIFEST_SCHEMA_VERSION:
        return ["交付清单版本过旧；请运行 kp publish 受控重建"]
    for field in ("artifacts", "rendering_inputs", "release_inputs"):
        values = raw.get(field, [])
        if not isinstance(values, list):
            return [f"交付清单字段无效：{field}"]
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
                return [f"交付清单条目无效：{field}"]
            try:
                candidate = _workspace_path_from_manifest(workspace, item["path"])
            except ValueError:
                return [f"交付清单路径无效：{item['path']}"]
            if not candidate.is_file() or _sha256(candidate) != item["sha256"]:
                return [f"交付清单输入已过期：{item['path']}"]
    return []


def _review_proofs(workspace: Workspace) -> list[dict[str, object]]:
    """Return internal review provenance after delivery validation has accepted it."""
    from knowledge_product_workflow.review_batches import review_batch_proofs

    return review_batch_proofs(workspace)


def create_delivery_manifest(
    workspace: Workspace, markdown_path: Path | None, pdf_path: Path | None
) -> Path:
    """Persist checksums for the two final delivery artifacts."""
    if markdown_path is not None:
        markdown_path = markdown_path.expanduser().resolve()
    if pdf_path is not None:
        pdf_path = pdf_path.expanduser().resolve()
    constraints = _constraints_for_workspace(workspace)
    requires_markdown = constraints.requires_markdown if constraints else True
    requires_pdf = constraints.requires_pdf if constraints else True
    if requires_markdown and (markdown_path is None or not markdown_path.is_file()):
        raise FileNotFoundError("交付格式要求 Markdown 文件")
    if requires_pdf and (pdf_path is None or not pdf_path.is_file()):
        raise FileNotFoundError("交付格式要求 PDF 文件")
    report_path = workspace.root / "delivery" / "qa-report.json"
    if not report_path.is_file():
        raise FileNotFoundError("创建交付清单前必须先生成质量报告")
    from knowledge_product_workflow.workflows.validation import run_delivery_validation

    _, report = run_delivery_validation(workspace)
    if not report.publishable:
        raise ValueError("质量报告未通过，无法创建交付清单")
    manifest_path = workspace.root / "delivery" / "delivery-manifest.yaml"
    artifacts: list[dict[str, str]] = []
    if requires_markdown and markdown_path is not None:
        artifacts.append({"path": workspace_relative_path(markdown_path, workspace), "sha256": _sha256(markdown_path)})
    if requires_pdf and pdf_path is not None:
        artifacts.append({"path": workspace_relative_path(pdf_path, workspace), "sha256": _sha256(pdf_path)})
    rendering_inputs: list[dict[str, str]] = []
    for candidate in (
        workspace.root / "delivery" / "book.qmd",
        workspace.root / "delivery" / "publication-metadata.yaml",
        workspace.root / "delivery" / _PDF_COVER_FILE,
        workspace.root / "delivery" / _PDF_STYLE_FILE,
        workspace.root / "delivery" / _PDF_TOC_FILE,
        workspace.root / "delivery" / _PDF_COMPONENTS_FILE,
        workspace.root / "delivery" / _PDF_COMPONENTS_FILTER_FILE,
        workspace.root / "delivery" / "book.tex",
    ):
        if candidate.is_file():
            rendering_inputs.append({"path": workspace_relative_path(candidate, workspace), "sha256": _sha256(candidate)})
    release_inputs = [{"path": workspace_relative_path(report_path, workspace), "sha256": _sha256(report_path)}]
    input_manifest = book_input_manifest_path(workspace)
    if input_manifest.is_file():
        release_inputs.insert(
            0, {"path": workspace_relative_path(input_manifest, workspace), "sha256": _sha256(input_manifest)}
        )
    review_policy = "deterministic_only"
    if constraints is not None:
        review_policy = constraints.review_policy.value
    manifest = {
        "schema_version": _RELEASE_MANIFEST_SCHEMA_VERSION,
        "book_id": workspace.root.name,
        "artifacts": artifacts,
        "rendering_inputs": rendering_inputs,
        "release_inputs": release_inputs,
        "review": {
            "policy": review_policy,
            "independent_review_executed": review_policy == "independent_review_required",
            "statement": (
                "独立审稿已按 Brief 策略作为交付门验证。"
                if review_policy == "independent_review_required"
                else "本交付采用确定性质量门；未声明或执行独立审稿。"
            ),
        },
        "review_proofs": _review_proofs(workspace),
        "controlled_exceptions": controlled_exception_proofs(workspace),
    }
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    workspace.append_event("delivery_manifest_created", {"manifest": str(manifest_path)})
    return manifest_path


def refresh_publication_delivery(workspace: Workspace) -> dict[str, Path | bool | None]:
    """Safely rebuild only derived delivery artifacts after a profile update.

    The profile never changes chapters, contracts, reviews, or source ledgers.
    If no book has been assembled yet, there is nothing to render and the
    normal publication phase will consume the profile later.
    """
    existing_book = workspace.root / "delivery" / "book.md"
    if not existing_book.is_file():
        return {"refreshed": False, "markdown": None, "pdf": None, "report": None, "manifest": None}

    markdown_path = assemble_book(workspace)
    constraints = _constraints_for_workspace(workspace)
    pdf_path: Path | None = None
    if constraints is None or constraints.requires_pdf:
        pdf_path = render_pdf(
            workspace.root / "delivery" / "book.qmd", workspace.root / "delivery", workspace
        )

    from knowledge_product_workflow.workflows.validation import run_delivery_validation

    report_path, report = run_delivery_validation(workspace)
    manifest_path: Path | None = None
    if report.publishable:
        manifest_path = create_delivery_manifest(workspace, markdown_path, pdf_path)
    workspace.append_event(
        "publication_delivery_refreshed",
        {
            "refreshed": True,
            "pdf_refreshed": pdf_path is not None,
            "delivery_publishable": report.publishable,
            "manifest_created": manifest_path is not None,
        },
    )
    return {
        "refreshed": True,
        "markdown": markdown_path,
        "pdf": pdf_path,
        "report": report_path,
        "manifest": manifest_path,
    }
