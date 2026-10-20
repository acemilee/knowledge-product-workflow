"""Markdown assembly, PDF rendering, and delivery manifest generation."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import time
import uuid
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from knowledge_product_workflow.atomic_files import replace_file
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
from knowledge_product_workflow.pdf_preview import render_pdf_preview_set
from knowledge_product_workflow.presentation import (
    LEGACY_PRESENTATION_PROFILE_ID,
    PRESENTATION_PROFILE_ID,
    PUBLICATION_METADATA_FILENAME,
    PUBLICATION_PROFILE_FILENAME,
    V5_PRESENTATION_PROFILE_ID,
    PublicationMetadata,
    load_or_create_publication_metadata,
    publication_profile_path,
)
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.quality_exceptions import controlled_exception_proofs
from knowledge_product_workflow.quarto import resolve_quarto
from knowledge_product_workflow.verification_context import (
    atomic_replace as _v_atomic_replace,
)
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import (
    load_yaml as _v_load_yaml,
)
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    read_bytes as _v_read_bytes,
)
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.verification_context import (
    unlink as _v_unlink,
)
from knowledge_product_workflow.verification_context import (
    verified_read as _verification_read,
)
from knowledge_product_workflow.verification_context import (
    write_bytes as _v_write_bytes,
)
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
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
_PDF_PROFILE = PRESENTATION_PROFILE_ID
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


def _publication_call(workspace, stage, function, *args, **kwargs):
    """Bounded stage evidence; returned means a call ended, never quality approval."""
    started = time.perf_counter()
    if workspace is not None:
        workspace.append_event('publication_stage_started', {'stage':stage})
    try:
        result = function(*args, **kwargs)
    except BaseException as error:
        if workspace is not None:
            workspace.append_event('publication_stage_finished', {'stage':stage,
                'outcome':'raised', 'error_type':type(error).__name__, 'elapsed_seconds':time.perf_counter()-started})
        raise
    if workspace is not None:
        workspace.append_event('publication_stage_finished', {'stage':stage,
            'outcome':'returned', 'elapsed_seconds':time.perf_counter()-started})
    return result
_ASSET_REFERENCE = re.compile(r"!\[[^\]]*\]\((?P<target>[^)\s]+)")
_PDF_LAYOUT_MARKERS = (
    "papersize: a4",
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
    "include-in-header:",
    _PDF_STYLE_FILE,
    _PDF_COMPONENTS_FILE,
    "filters:",
    _PDF_COMPONENTS_FILTER_FILE,
)
_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_TEX_DOCUMENTCLASS = re.compile(
    r"\\documentclass(?:\[(?P<options>[^\]]*)\])?\{(?P<documentclass>[^}]+)\}"
)
_PDF_COMPONENT_LEAK = re.compile(r"\{\.kp-(?:source|key-takeaway|practice|note|decision|procedure|worksheet)\}")
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
    return [path for path in paths if _v_is_file(path)]


def _reader_asset_paths(workspace: Workspace, markdown: str) -> list[Path]:
    from knowledge_product_workflow.diagrams import (
        DiagramManifest,
        _diagram_theme,
        _replace_visible_image_references,
        diagram_manifest_path,
    )

    paths: list[Path] = []
    paired_assets_required = _diagram_theme(workspace)[0] == PRESENTATION_PROFILE_ID

    def collect(match: re.Match[str]) -> str:
        stored_target = match.group("target")
        # New reader links are relative to delivery/book.md; historical source
        # references remain root-relative and are never rewritten in place.
        target = stored_target.removeprefix("../")
        relative = PurePosixPath(target)
        if (not target.startswith("assets/") or "\\" in target
                or relative.as_posix() != target
                or any(part in {".", ".."} for part in relative.parts)):
            raise ValueError(f"读者图链不是受控资产相对路径：{stored_target}")
        candidate = workspace.root.joinpath(*relative.parts)
        if (not candidate.resolve().is_relative_to((workspace.root / "assets").resolve())
                or not candidate.resolve().is_relative_to(workspace.root.resolve())):
            raise ValueError(f"读者图链越出工作区资产目录：{stored_target}")
        if not _v_is_file(candidate):
            raise ValueError(f"读者图链资产不存在：{stored_target}")
        if candidate not in paths:
            paths.append(candidate)
        if relative.parent.as_posix() == "assets/figures":
            diagram_id = relative.stem
            if paired_assets_required:
                manifest_path = diagram_manifest_path(workspace, diagram_id)
                try:
                    manifest = DiagramManifest.model_validate_json(_v_read_text(manifest_path, encoding="utf-8"))
                    for suffix, digest in (("png", manifest.png_sha256), ("pdf", manifest.pdf_sha256)):
                        paired = workspace.root / "assets/figures" / f"{diagram_id}.{suffix}"
                        if (not paired.resolve().is_relative_to(workspace.root.resolve())
                                or not _v_is_file(paired) or _sha256(paired) != digest):
                            raise ValueError("双格式资产未绑定当前清单")
                except (OSError, ValueError) as error:
                    raise ValueError(f"DIAGRAM_ASSET_UNBOUND: {diagram_id} 的读者资产未绑定当前清单：{error}") from error
            paths.extend(
                _existing_files(
                    (
                        workspace.root / "assets" / "figures" / f"{diagram_id}.png",
                        workspace.root / "assets" / "figures" / f"{diagram_id}.pdf",
                        workspace.root / "runs" / "diagrams" / f"{diagram_id}.spec.json",
                        workspace.root / "runs" / "diagrams" / f"{diagram_id}.input.json",
                        workspace.root / "runs" / "diagrams" / f"{diagram_id}.d2",
                        workspace.root / "runs" / "diagrams" / f"{diagram_id}.svg",
                        workspace.root / "runs" / "diagrams" / f"{diagram_id}.json",
                        workspace.root / "runs" / "illustrations" / "asset-checks" / f"{diagram_id}.json",
                    )
                )
            )
        return match.group(0)

    _replace_visible_image_references(markdown, _ASSET_REFERENCE, collect)
    return list(dict.fromkeys(paths))


def _reader_resource_payloads(workspace: Workspace, markdown: str) -> dict[str, bytes]:
    """Explicit program/input/document closure; never collect the whole assets tree."""
    from knowledge_product_workflow.reader_artifacts import (
        _asset,
        reader_resource_preflight,
        resolve_reader_resources,
    )
    check = reader_resource_preflight(workspace, markdown)
    if not check["ready"]:
        raise ValueError("READER_RESOURCE_PREFLIGHT_FAILED: " + json.dumps(check["findings"], ensure_ascii=False))
    resources = resolve_reader_resources(workspace, markdown)
    declared = check["declared_files"]
    if not declared:
        return {}
    expected = {}
    for item in resources:
        expected.update(item["inputs"])
    payloads = {}
    for relative in sorted(declared):
        _, data = _asset(workspace, relative)
        if relative in expected and hashlib.sha256(data).hexdigest() != expected[relative]:
            raise ValueError("资源在交付冻结期间改变；停止，不打包旧证明")
        payloads[relative] = data
    for relative in ("assets/README.md", "assets/THIRD_PARTY_NOTICES.md"):
        _, data = _asset(workspace, relative)
        if not data.strip():
            raise ValueError(f"读者资源说明或许可不能为空：{relative}")
        payloads[relative] = data
    return dict(sorted(payloads.items()))


def _reader_bundle_outputs(workspace: Workspace, markdown: str, *, payloads: dict[str, bytes] | None = None) -> dict[Path, bytes]:
    """Plan a deterministic bundle; the existing assembly transaction writes it."""
    payloads = _reader_resource_payloads(workspace, markdown) if payloads is None else payloads
    if not payloads:
        return {}
    manifest = {"schema_version": 1, "layout": "workspace-relative-assets",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}}
    readme = ("# 配套实践资源\n\n在新的交付根目录解压本包，保持assets/相对目录；将book.md置于同一根目录的delivery/book.md。整体移动时保持两目录的相对位置。先阅读assets/README.md，"
              "按其说明进入主程序所在目录；不要把示例运行成功当作模型效果或真实业务验证。\n").encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in {"README.md": readme, **payloads}.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return {workspace.root / "delivery/practice-resources.zip": stream.getvalue(),
        workspace.root / "delivery/practice-resources-manifest.json":
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")}


def reader_resource_bundle_findings(workspace: Workspace, markdown: str) -> list[QualityFinding]:
    """A missing or changed bundle cannot be silently rebuilt by publish."""
    try:
        payloads = _reader_resource_payloads(workspace, markdown)
        if not payloads:
            return []
        archive_path = workspace.root / "delivery/practice-resources.zip"
        manifest_path = workspace.root / "delivery/practice-resources-manifest.json"
        manifest = json.loads(_v_read_bytes(manifest_path))
        expected = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
        if manifest != {"schema_version": 1, "layout": "workspace-relative-assets", "files": expected}:
            raise ValueError("资源成员清单已过期或被改写")
        with zipfile.ZipFile(io.BytesIO(_v_read_bytes(archive_path))) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != {"README.md", *expected}:
                raise ValueError("配套ZIP成员缺失、重复或含未声明文件")
            if not archive.read("README.md").strip():
                raise ValueError("配套ZIP缺少读者启动说明")
            if any(archive.read(name) != data for name, data in payloads.items()):
                raise ValueError("配套ZIP不是当前已声明的资源字节")
        from knowledge_product_workflow.example_checks import (
            _resource_key,
            example_evidence_for_body,
        )
        from knowledge_product_workflow.reader_artifacts import resolve_reader_resources
        verified = {item.get("resource_inputs_sha256") for item in example_evidence_for_body(workspace, markdown)}
        for item in resolve_reader_resources(workspace, markdown):
            if _resource_key(item) not in verified:
                raise ValueError(f"完整资源缺少当前真实实跑证明：{item['resource_path']}；"
                                 "现有自动执行器只支持Python，其他语言未验证，不以打包成功代签")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        return [QualityFinding(code="READER_RESOURCE_BUNDLE_INVALID", severity=Severity.BLOCKER,
            location="delivery/practice-resources.zip", message=f"{error}；先核资源和实际执行，再显式重新汇编/校验，不在发布中补包")]
    return []


def _book_input_paths(workspace: Workspace, chapter_paths: list[Path], markdown: str, qmd_path: Path,
                      *, _planned=(), _resource_payloads=None) -> list[Path]:
    """Collect only current source graph files, never arbitrary workspace residue."""
    paths = [
        workspace.root / "planning" / "product-brief.md",
        publication_profile_path(workspace),
        workspace.root / "planning" / "curriculum-blueprint.md",
        workspace.root / "chapters" / "chapter-01-sample.approved",
        workspace.root / "research" / "source-ledger.jsonl",
        workspace.root / "research" / "assumption-ledger.jsonl",
        workspace.root / "research" / "claim-classifications.jsonl",
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
                workspace.root / "runs" / "editorial" / f"chapter-{number:02d}-developmental-report.json",
                workspace.root / "runs" / "editorial" / f"chapter-{number:02d}-structure-review.json",
                workspace.root / "chapters" / f"chapter-{number:02d}-content-plan.yaml",
            )
        )
    paths.extend(_reader_asset_paths(workspace, markdown))
    resource_payloads = (_reader_resource_payloads(workspace, markdown)
                         if _resource_payloads is None else _resource_payloads)
    if resource_payloads:
        paths.extend(workspace.root / name for name in resource_payloads)
        paths.extend(workspace.root / "delivery" / name for name in
                     ("practice-resources.zip", "practice-resources-manifest.json"))
    return list(dict.fromkeys(path for path in paths if path in _planned or _v_is_file(path)))


def _build_book_input_manifest(
    workspace: Workspace, chapter_paths: list[Path], markdown: str, qmd_path: Path, *, _planned=None, _resource_payloads=None
) -> BookInputManifest:
    planned = _planned or {}
    metadata_path = workspace.root / "delivery" / PUBLICATION_METADATA_FILENAME
    try:
        metadata = PublicationMetadata.model_validate(
            _v_load_yaml(planned[metadata_path].decode('utf8') if metadata_path in planned
                         else _v_read_text(metadata_path, encoding="utf-8"))
        )
        profile_id = metadata.profile_id
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        profile_id = _PDF_PROFILE
    return BookInputManifest(
        pdf_profile=profile_id,
        inputs=[
            HashedArtifact(path=_relative(workspace, path), sha256=hashlib.sha256(planned[path]).hexdigest()
                           if path in planned else _sha256(path))
            for path in _book_input_paths(workspace, chapter_paths, markdown, qmd_path, _planned=planned, _resource_payloads=_resource_payloads)
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
    _v_write_text(output, manifest.model_dump_json(indent=2), encoding="utf-8")
    workspace.append_event(
        "book_inputs_frozen",
        {"manifest": _relative(workspace, output), "input_count": len(manifest.inputs)},
    )
    return output


def _digest_if_file(path: Path) -> str | None:
    return _sha256(path) if _v_is_file(path) else None


def _read_book_input_manifest(path: Path) -> BookInputManifest | None:
    if not _v_is_file(path):
        return None
    try:
        return BookInputManifest.model_validate_json(_v_read_text(path, encoding="utf-8"))
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
    existing = [path for path in candidates if _v_is_file(path)]
    if not existing:
        return []
    archive = workspace.root / "runs" / "delivery-history" / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    )
    _v_mkdir(archive,parents=True,exist_ok=False)
    moved: list[str] = []
    for source in existing:
        target = archive / source.name
        _v_atomic_replace(source, target)
        moved.append(_relative(workspace, target))
    workspace.append_event("delivery_derivatives_archived", {"artifacts": moved})
    return moved


def _chapter_bodies(chapter_paths: list[Path]) -> list[str]:
    return [parse_markdown_artifact(_v_read_text(path, encoding="utf-8"))[1].strip() for path in chapter_paths]


def _reader_book_from_bodies(bodies: list[str], workspace: Workspace) -> str:
    from knowledge_product_workflow.diagrams import reader_png_references
    from knowledge_product_workflow.reader_artifacts import rebase_reader_file_links

    markdown = "\n\n".join(render_reader_markdown(body).strip() for body in bodies) + "\n"
    markdown = reader_png_references(workspace, markdown)
    _reader_asset_paths(workspace, markdown)
    return rebase_reader_file_links(workspace, markdown)


def _qmd_book_from_bodies(bodies: list[str], workspace: Workspace) -> str:
    from knowledge_product_workflow.diagrams import qmd_figure_references
    from knowledge_product_workflow.reader_artifacts import rebase_reader_file_links

    markdown = "\n\n".join(render_qmd_markdown(body).strip() for body in bodies) + "\n"
    return rebase_reader_file_links(workspace, qmd_figure_references(workspace, markdown))


def delivery_component_leak_findings(workspace: Workspace, *, include_pdf: bool) -> list[QualityFinding]:
    """Reject implementation-only component syntax from reader deliverables."""

    findings: list[QualityFinding] = []
    markdown_path = workspace.root / "delivery" / "book.md"
    if _v_is_file(markdown_path):
        for line_number in reader_component_leaks(
            _v_read_text(markdown_path, encoding="utf-8"), allow_fenced_divs=False
        ):
            findings.append(
                _release_finding(
                    "PDF_INTERNAL_COMPONENT_LEAK",
                    f"delivery/book.md:第 {line_number} 行",
                    "读者 Markdown 泄漏了 KP 内部提示框标记；请重新受控汇编。",
                )
            )
    qmd_path = workspace.root / "delivery" / "book.qmd"
    if _v_is_file(qmd_path):
        for line_number in reader_component_leaks(
            _v_read_text(qmd_path, encoding="utf-8"), allow_fenced_divs=True
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
        if _v_is_file(pdf_path):
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


@_verification_read(transaction_view=True)
def resource_preflight_for_chapters(workspace: Workspace) -> dict:
    from knowledge_product_workflow.production_intake import consistent_production_read
    from knowledge_product_workflow.reader_artifacts import reader_resource_preflight
    from knowledge_product_workflow.workspace_transactions import (
        require_transaction_consistency_in_view,
    )
    with consistent_production_read(workspace):
        require_transaction_consistency_in_view(workspace)
        paths = sorted(workspace.root.joinpath("chapters").glob("chapter-[0-9][0-9].md"))
        markdown = "\n\n".join(_chapter_bodies(paths))
        return reader_resource_preflight(workspace, markdown)


def require_reader_resource_preflight(workspace: Workspace) -> None:
    check = resource_preflight_for_chapters(workspace)
    if not check["ready"]:
        raise ValueError("READER_RESOURCE_PREFLIGHT_FAILED: " + json.dumps(check["findings"], ensure_ascii=False)
                         + "；修复后显式继续；本前检不授予发布权限")


@_verification_read
def assemble_book(workspace: Workspace) -> Path:
    from knowledge_product_workflow.production_dependencies import require_no_deferred_repairs
    from knowledge_product_workflow.production_tasks import controller_lock_owned, production_lock
    # Assembly now commits a journal. A read-only token must not span this
    # owned write; hold the existing controller lock and retain byte freshness.
    with nullcontext() if controller_lock_owned(workspace) else production_lock(workspace):
        require_reader_resource_preflight(workspace)
        require_no_deferred_repairs(workspace)
        return _assemble_book(workspace)


def _assemble_book(workspace: Workspace) -> Path:
    """Combine chapter bodies into the canonical Markdown source artifact."""
    from knowledge_product_workflow.production_intake import require_consistent_production

    require_consistent_production(workspace)
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
        markdown = _reader_book_from_bodies(bodies, workspace)
        qmd_markdown = _qmd_book_from_bodies(bodies, workspace)
    except CalloutFormatError as error:
        raise ValueError(f"读者提示框结构无效，无法安全汇编：{error}") from error
    # Resolve the cover contract before replacing the canonical manuscript.
    from knowledge_product_workflow.presentation import (
        load_or_create_publication_profile,
        validate_cover_contract,
    )

    constraints = _constraints_for_workspace(workspace)
    title = _book_title(workspace, markdown)
    brief_path = workspace.root / "planning" / "product-brief.md"
    overrides = None
    if _v_is_file(brief_path):
        _, brief_body = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
        overrides = parse_presentation_overrides(brief_body)
    profile = load_or_create_publication_profile(
        workspace, language=constraints.language if constraints else "zh-CN", overrides=overrides
    )
    validate_cover_contract(profile, title)
    planned = {output: markdown.encode('utf8')}
    resource_payloads = _reader_resource_payloads(workspace, markdown)
    planned.update(_reader_bundle_outputs(workspace, markdown, payloads=resource_payloads))
    qmd_path = create_quarto_source(workspace, output, qmd_markdown=qmd_markdown,
                                   _outputs=planned, _canonical_markdown=markdown)
    current_input_manifest = _build_book_input_manifest(workspace, chapter_paths, markdown, qmd_path, _planned=planned, _resource_payloads=resource_payloads)
    render_inputs_changed = any(before[path] != hashlib.sha256(planned[path]).hexdigest() for path in render_contract_paths)
    existing_derivatives = any(
        _v_is_file(path)
        for path in (
            workspace.root / "delivery" / "book.pdf",
            workspace.root / "delivery" / "book.tex",
            workspace.root / "delivery" / "delivery-manifest.yaml",
        )
    )
    input_graph_changed = prior_input_manifest != current_input_manifest
    outputs = {}
    archived = []
    if render_inputs_changed or input_graph_changed or (existing_derivatives and prior_input_manifest is None):
        archive = workspace.root / 'runs/delivery-history' / (datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8])
        for path in (workspace.root/'delivery/book.pdf', workspace.root/'delivery/book.tex',
                     workspace.root/'delivery/delivery-manifest.yaml', book_input_manifest_path(workspace)):
            if _v_is_file(path):
                outputs[_relative(workspace, archive/path.name)] = _v_read_bytes(path)
                # Attempt locked derivative removal before replacing manuscript files.
                if path != book_input_manifest_path(workspace):
                    outputs[_relative(workspace, path)] = None
                archived.append(_relative(workspace, path))
    planned[book_input_manifest_path(workspace)] = current_input_manifest.model_dump_json(indent=2).encode('utf8')
    outputs.update({_relative(workspace,p): payload for p,payload in planned.items()})
    from knowledge_product_workflow.workspace_transactions import (
        commit_workspace_transaction,
        prepare_workspace_transaction,
    )
    bindings = {entry.path: entry.sha256 for entry in current_input_manifest.inputs if entry.path not in outputs}
    events = [('book_inputs_frozen', {'manifest':_relative(workspace,book_input_manifest_path(workspace)),
                                     'input_count':len(current_input_manifest.inputs)}),
              ('book_assembled', {'chapter_count':len(chapter_paths)})]
    if archived:
        events.insert(0, ('delivery_derivatives_archived', {'artifacts':archived}))
    tx = prepare_workspace_transaction(workspace, outputs, kind='delivery_assembly', scope_chapters=[],
        shared_paths=list(outputs), input_bindings=bindings, events=events)
    commit_workspace_transaction(workspace, tx)
    return output


def render_review_preview(workspace: Workspace) -> Path:
    """Render current body snapshots without approving or replacing delivery."""
    import json
    import shutil

    from knowledge_product_workflow.chapter_compliance import validate_current_chapter_compliance
    from knowledge_product_workflow.content_checkpoint import FIGURE, current_content_checkpoint
    from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract

    contracts = sorted((workspace.root / "chapters").glob("chapter-[0-9][0-9]-contract.md"))
    if not contracts:
        raise ValueError("诊断预览需要当前批准的章节合约")
    bodies, pending, inputs = [], {}, {}
    for path in contracts:
        number = int(path.name.split("-")[1])
        require_current_chapter_contract(workspace, number)
        source = workspace.root / "chapters" / f"chapter-{number:02d}.md"
        if not _v_is_file(source):
            raise ValueError(f"第{number}章正文尚未就绪，不能称为完整诊断预览")
        checkpoint = current_content_checkpoint(workspace, number)
        if checkpoint is None:
            findings = validate_current_chapter_compliance(workspace, source)
            if any(f.severity.value == "blocker" for f in findings):
                raise ValueError(f"第{number}章缺少当前正文检查点/完整合规提交")
        _, body = parse_markdown_artifact(_v_read_text(source, encoding="utf-8"))
        for match in FIGURE.finditer(body):
            diagram_id = match.group("id")
            for relative in (f"assets/figures/{diagram_id}.pdf", f"assets/figures/{diagram_id}.png",
                             f"runs/diagrams/{diagram_id}.input.json", f"runs/diagrams/{diagram_id}.json",
                             f"runs/illustrations/asset-checks/{diagram_id}.json"):
                artifact = workspace.root / relative
                inputs[relative] = _sha256(artifact) if _v_is_file(artifact) else "missing"
        failed = set(checkpoint["pending_assets"]) if checkpoint else set()
        pending[str(number)] = sorted(failed)
        body = FIGURE.sub(lambda m, deferred=failed: ("\n::: {.kp-note}\n插图尚未通过，待原任务修复；此处仅供核验正文与图文位置。\n:::\n"
                                    if m.group("id") in deferred else m.group(0)), body)
        bodies.append(body)
        inputs[source.relative_to(workspace.root).as_posix()] = _sha256(source)
        inputs[path.relative_to(workspace.root).as_posix()] = _sha256(path)
    profile_path = workspace.root / "planning" / PUBLICATION_PROFILE_FILENAME
    inputs[profile_path.relative_to(workspace.root).as_posix()] = _sha256(profile_path)
    fingerprint = hashlib.sha256(json.dumps({"inputs": inputs, "pending": pending}, sort_keys=True).encode()).hexdigest()
    preview = workspace.root / "runs/review-preview" / fingerprint
    output = preview / "book.pdf"
    if _v_is_file(output) and _v_is_file(preview / "preview-manifest.json"):
        cached = json.loads(_v_read_text(preview / "preview-manifest.json", encoding="utf-8"))
        if cached.get("pdf_sha256") == _sha256(output):
            return output
    shadow = Workspace(preview)
    for directory in ("planning", "delivery", "runs", "chapters", "assets/figures"):
        _v_mkdir(preview / directory,parents=True,exist_ok=True)
    shutil.copy2(workspace.root / "planning/product-brief.md", preview / "planning/product-brief.md")
    profile = _v_load_yaml(_v_read_text(profile_path, encoding="utf-8"))
    profile["footer_label"] = "诊断预览 · 未发布"
    _v_write_text(preview / "planning" / PUBLICATION_PROFILE_FILENAME,
        yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8")
    for body in bodies:
        for match in FIGURE.finditer(body):
            for suffix in (".pdf", ".png"):
                asset = workspace.root / "assets/figures" / (match.group("id") + suffix)
                if not _v_is_file(asset):
                    raise ValueError(f"诊断预览中非待办图缺少资产：{asset.name}")
                shutil.copy2(asset, preview / "assets/figures" / asset.name)
    markdown = preview / "delivery/book.md"
    _v_write_text(markdown, _reader_book_from_bodies(bodies, workspace), encoding="utf-8")
    source = create_quarto_source(shadow, markdown, qmd_markdown=_qmd_book_from_bodies(bodies, workspace))
    pdf = render_pdf(source, preview / "delivery", shadow)
    shutil.copy2(pdf, output)
    _v_write_text(preview / "preview-manifest.json", json.dumps({
        "schema_version": 1, "preview_only": True, "publishable": False, "input_fingerprint": fingerprint,
        "inputs": inputs, "pending_assets": pending, "pdf_sha256": _sha256(output)
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    workspace.append_event("review_preview_rendered", {"path": output.relative_to(workspace.root).as_posix(), "publishable": False})
    return output


def _constraints_for_workspace(workspace: Workspace) -> DetailConstraints | None:
    brief_path = workspace.root / "planning" / "product-brief.md"
    if not _v_exists(brief_path):
        return None
    _, body = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
    return parse_detail_constraints(body)


def _title_from_markdown(markdown: str) -> str | None:
    match = _TITLE.search(markdown)
    return match.group(1).strip() if match else None


def _book_title(workspace: Workspace, markdown: str) -> str:
    brief_path = workspace.root / "planning" / "product-brief.md"
    if _v_exists(brief_path):
        _, brief_body = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
        brief_title = _title_from_markdown(brief_body)
        if brief_title:
            return brief_title
    return _title_from_markdown(markdown) or workspace.root.name


def _template_text(filename: str, profile_id: str | None = None, reader_layout_revision: int = 2) -> str:
    asset = files("knowledge_product_workflow")
    parts = (*_PDF_TEMPLATE_DIRECTORY, "v6", filename) if profile_id == PRESENTATION_PROFILE_ID else (*_PDF_TEMPLATE_DIRECTORY, filename)
    if profile_id == PRESENTATION_PROFILE_ID and reader_layout_revision == 1 and filename in {
        _PDF_STYLE_FILE, _PDF_COMPONENTS_FILE, _PDF_COMPONENTS_FILTER_FILE,
    }:
        parts = (*_PDF_TEMPLATE_DIRECTORY, "v6", "layout1", filename)
    for part in parts:
        asset = asset.joinpath(part)
    return _v_read_text(asset, encoding="utf-8")


def pdf_template_input_drift(workspace: Workspace) -> list[str]:
    """Read-only check of copied renderer resources against this runtime package."""
    delivery = workspace.root / "delivery"
    metadata_path = delivery / PUBLICATION_METADATA_FILENAME
    if not _v_is_file(metadata_path):
        return []  # Existing missing-metadata/assembly gates own this case.
    try:
        metadata = PublicationMetadata.model_validate(_v_load_yaml(_v_read_text(metadata_path, encoding="utf-8")))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return [PUBLICATION_METADATA_FILENAME]
    stale = []
    for name in (_PDF_STYLE_FILE, _PDF_TOC_FILE, _PDF_COMPONENTS_FILE, _PDF_COMPONENTS_FILTER_FILE):
        path = delivery / name
        expected = _template_text(name, metadata.profile_id, metadata.reader_layout_revision)
        if not _v_is_file(path) or not _v_read_text(path, encoding="utf-8").endswith(expected):
            stale.append(name)
    return stale


def _style_token_prefix(metadata: PublicationMetadata) -> str:
    palette = metadata.palette
    if metadata.heading_scale == "compact":
        chapter_size, chapter_leading, section_size, section_leading = (20, 28, 14, 20)
    elif metadata.heading_scale == "generous":
        chapter_size, chapter_leading, section_size, section_leading = (24, 32, 16, 22)
    else:
        chapter_size, chapter_leading, section_size, section_leading = (22, 30, 15, 21)
    paragraph_skip = "4pt" if metadata.paragraph_spacing == "relaxed" else "0pt"
    line_spread = "1.40" if metadata.paragraph_spacing == "relaxed" else "1.32"
    running_header = {
        "none": "",
        "chapter": r"\nouppercase{\leftmark}",
        "section": r"\nouppercase{\rightmark}",
    }[metadata.running_header_mode]
    key_frame = "kpRule" if metadata.component_emphasis == "subtle" else "kpBrand"
    source_frame = "kpRule" if metadata.source_presentation == "inline" else "kpBrand"
    return "\n".join(
        (
            "% This file prefix is generated from validated publication design tokens.",
            f"\\providecommand{{\\KnowledgeProductInkHex}}{{{palette.ink}}}",
            f"\\providecommand{{\\KnowledgeProductPaperHex}}{{{palette.paper}}}",
            f"\\providecommand{{\\KnowledgeProductAccentHex}}{{{palette.accent}}}",
            f"\\providecommand{{\\KnowledgeProductAccentSoftHex}}{{{palette.accent_soft}}}",
            f"\\providecommand{{\\KnowledgeProductMutedHex}}{{{palette.muted}}}",
            f"\\providecommand{{\\KnowledgeProductRuleHex}}{{{palette.rule}}}",
            f"\\providecommand{{\\KnowledgeProductChapterSize}}{{{chapter_size}}}",
            f"\\providecommand{{\\KnowledgeProductChapterLeading}}{{{chapter_leading}}}",
            f"\\providecommand{{\\KnowledgeProductSectionSize}}{{{section_size}}}",
            f"\\providecommand{{\\KnowledgeProductSectionLeading}}{{{section_leading}}}",
            f"\\providecommand{{\\KnowledgeProductLineSpread}}{{{line_spread}}}",
            f"\\providecommand{{\\KnowledgeProductParagraphSkip}}{{{paragraph_skip}}}",
            f"\\providecommand{{\\KnowledgeProductFontPolicy}}{{{metadata.font_policy}}}",
            f"\\providecommand{{\\KnowledgeProductRunningHeader}}{{{running_header}}}",
            f"\\providecommand{{\\KnowledgeProductKeyFrameColor}}{{{key_frame}}}",
            f"\\providecommand{{\\KnowledgeProductSourceFrameColor}}{{{source_frame}}}",
            "",
        )
    )


def _geometry_for_publication(metadata: PublicationMetadata) -> str:
    if metadata.profile_id == PRESENTATION_PROFILE_ID:
        return "left=13mm,right=13mm,top=18mm,bottom=18mm"
    if metadata.line_length == "compact":
        return "left=22mm,right=22mm,top=18mm,bottom=18mm"
    return "left=16mm,right=16mm,top=18mm,bottom=18mm"


def _write_pdf_contract_assets(workspace: Workspace, metadata: PublicationMetadata, *, _outputs=None) -> None:
    def save(path, text, *, encoding):
        if _outputs is None:
            _v_write_text(path, text, encoding=encoding)
        else:
            _outputs[path] = text.encode(encoding)
    delivery = workspace.root / "delivery"
    style_path = delivery / _PDF_STYLE_FILE
    cover_path = delivery / _PDF_COVER_FILE
    save(style_path,
        _style_token_prefix(metadata) + _template_text(_PDF_STYLE_FILE, metadata.profile_id, metadata.reader_layout_revision), encoding="utf-8"
    )
    save(delivery / _PDF_TOC_FILE, _template_text(_PDF_TOC_FILE, metadata.profile_id), encoding="utf-8")
    save(delivery / _PDF_COMPONENTS_FILE,
        _template_text(_PDF_COMPONENTS_FILE, metadata.profile_id, metadata.reader_layout_revision), encoding="utf-8"
    )
    save(delivery / _PDF_COMPONENTS_FILTER_FILE,
        _template_text(_PDF_COMPONENTS_FILTER_FILE, metadata.profile_id, metadata.reader_layout_revision), encoding="utf-8"
    )
    cover_prefix = (
        "% This file is generated by the workflow; do not hand-edit it.\n"
        f"\\providecommand{{\\KnowledgeProductTitle}}{{{metadata.title.translate(_TEX_ESCAPE)}}}\n"
        "\\providecommand{\\KnowledgeProductCoverTitle}{"
        + r"\\".join(line.translate(_TEX_ESCAPE) for line in (metadata.cover_title_lines or [metadata.title]))
        + "}\n"
        f"\\providecommand{{\\KnowledgeProductSubtitle}}{{{metadata.subtitle.translate(_TEX_ESCAPE)}}}\n"
        f"\\providecommand{{\\KnowledgeProductAudience}}{{{metadata.audience_label.translate(_TEX_ESCAPE)}}}\n"
        f"\\providecommand{{\\KnowledgeProductEdition}}{{{metadata.edition_label.translate(_TEX_ESCAPE)}}}\n"
        f"\\providecommand{{\\KnowledgeProductPublicationDate}}{{{metadata.publication_date.isoformat()}}}\n"
        f"\\providecommand{{\\KnowledgeProductCoverLabel}}{{{metadata.cover_label.translate(_TEX_ESCAPE)}}}\n"
        f"\\providecommand{{\\KnowledgeProductFooterLabel}}{{{metadata.footer_label.translate(_TEX_ESCAPE)}}}\n"
        f"\\providecommand{{\\KnowledgeProductCopyright}}{{{metadata.copyright_line.translate(_TEX_ESCAPE)}}}\n"
    )
    cover_filename = _PDF_COVER_FILE
    if metadata.cover_layout_revision == 2:
        if not metadata.cover_summary_zh:
            raise ValueError("封面布局2须锁定非空中文梗概；先通过出版预览确认")
        if not re.fullmatch(r"©\s+\d{4}\s+\S.*", metadata.copyright_line):
            raise ValueError("新封面版权行须由用户确认：© 年份 署名")
        cover_filename = "knowledge-book-cover-v2.tex"
        values = {"CoverSummaryZh": metadata.cover_summary_zh, "CoverSummaryEn": metadata.cover_summary_en or "",
                  "CoverMonth": metadata.publication_date.strftime("%Y年%m月"),
                  "CoverNote": metadata.cover_content_note or ""}
        for name,value in values.items():
            cover_prefix += f"\\providecommand{{\\KnowledgeProduct{name}}}{{{value.translate(_TEX_ESCAPE)}}}\n"
        focus = "\\quad ".join("\\KpInlineCode{" + item.translate(_TEX_ESCAPE) + "}" for item in metadata.cover_focus_items)
        cover_prefix += "\\providecommand{\\KnowledgeProductCoverFocus}{" + focus + "}\n"
    save(cover_path, cover_prefix + _template_text(cover_filename, metadata.profile_id), encoding="utf-8")


def missing_pdf_source_contracts(qmd_path: Path) -> list[str]:
    """Return deterministic blockers for missing fixed PDF source-contract pieces."""
    source = _v_read_text(qmd_path, encoding="utf-8") if _v_is_file(qmd_path) else ""
    missing: list[str] = []
    font_match = re.search(r"(?m)^\s*fontsize:\s*(?P<size>\d+)pt\s*$", source)
    publication_path = qmd_path.parent / PUBLICATION_METADATA_FILENAME
    try:
        publication = PublicationMetadata.model_validate(
            _v_load_yaml(_v_read_text(publication_path, encoding="utf-8"))
        )
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        publication = None
    expected_geometry = _geometry_for_publication(publication) if publication is not None else "left=16mm,right=16mm,top=18mm,bottom=18mm"
    if not all(marker in source for marker in _PDF_LAYOUT_MARKERS) or f"geometry: {expected_geometry}" not in source or font_match is None:
        missing.append("LAYOUT_CONTRACT_MISMATCH")
    if publication is not None and (
        font_match is None
        or int(font_match.group("size")) != publication.body_font_size
        or "documentclass: extreport" not in source
    ):
        missing.append("PDF_FONT_SIZE_CONTRACT_FAILED")
    cover_path = qmd_path.parent / _PDF_COVER_FILE
    cover = _v_read_text(cover_path, encoding="utf-8") if _v_is_file(cover_path) else ""
    marker_version = "v6" if publication is not None and publication.profile_id == PRESENTATION_PROFILE_ID else "v4"
    if not all(marker in source for marker in _PDF_COVER_MARKERS) or f"% knowledge-product-pdf-{marker_version}-cover" not in cover:
        missing.append("PDF_COVER_CONTRACT_MISSING")
    if not all(marker in source for marker in _PDF_TOC_MARKERS):
        missing.append("PDF_TOC_CONTRACT_MISSING")
    style_path = qmd_path.parent / _PDF_STYLE_FILE
    style = _v_read_text(style_path, encoding="utf-8") if _v_is_file(style_path) else ""
    toc_path = qmd_path.parent / _PDF_TOC_FILE
    components_path = qmd_path.parent / _PDF_COMPONENTS_FILE
    filter_path = qmd_path.parent / _PDF_COMPONENTS_FILTER_FILE
    toc = _v_read_text(toc_path, encoding="utf-8") if _v_is_file(toc_path) else ""
    components = _v_read_text(components_path, encoding="utf-8") if _v_is_file(components_path) else ""
    component_filter = _v_read_text(filter_path, encoding="utf-8") if _v_is_file(filter_path) else ""
    if (marker_version == "v6" and publication.reader_layout_revision == 2
            and publication.font_policy == "preferred" and not all(item in style for item in (
                r"\setmainfont[Scale=1]{Inter}", r"\setmonofont[Scale=0.99254]{JetBrains Mono}",
                r"\setCJKmainfont[Scale=1,BoldFont={PingFang SC Semibold}]{PingFang SC}",
                r"\setmathfont[Scale=1]{Latin Modern Math}",
                r"\unimathsetup{mathrm=sym,mathit=sym,mathbf=sym}",
                r"\setoperatorfont{\symup}",
                r"\XeTeXgenerateactualtext=1",
                r"\xeCJKsetup{CJKecglue = {\hskip .25em}, xCJKecglue = false}",
            ))):
        missing.append("PDF_DEFAULT_FONT_CONTRACT_STALE")
    if (
        not all(marker in source for marker in _PDF_CHINESE_STYLE_MARKERS)
        or (marker_version == "v4" and not all(item in source for item in ("mainfont: TeX Gyre Termes", "monofont: Consolas", "CJKmainfont: Microsoft YaHei")))
        or (marker_version == "v6" and ("kp-font-role-policy:" not in source or "SimHei" not in style))
        or f"% knowledge-product-pdf-{marker_version}-style" not in style
        or f"% knowledge-product-pdf-{marker_version}-toc" not in toc
        or f"% knowledge-product-pdf-{marker_version}-components" not in components
        or f"-- knowledge-product-pdf-{marker_version}-components-filter" not in component_filter
        or f"-- knowledge-product-pdf-{marker_version}-diagram-layout" not in component_filter
    ):
        missing.append("PDF_CHINESE_STYLE_CONTRACT_MISSING")
    inline_trigger = "\\renewcommand{\\texttt}" in components or (
        "function Code(" in component_filter and
        ("KpInlineCode" in component_filter or
         ("KpCodeGlyph" in component_filter and r"\newcommand{\KpCodeGlyph}" in components))
    )
    if f"% knowledge-product-pdf-{marker_version}-inline-code" not in components or not inline_trigger:
        missing.append("PDF_INLINE_CODE_STYLE_CONTRACT_MISSING")
    expected_profile = publication.profile_id if publication is not None else None
    if expected_profile is None or expected_profile not in source:
        missing.append("PDF_PUBLICATION_METADATA_MISSING")
    return missing


def create_quarto_source(
    workspace: Workspace, markdown_path: Path, *, qmd_markdown: str | None = None,
    _outputs: dict | None = None, _canonical_markdown: str | None = None,
) -> Path:
    """Create the derived Quarto source using the approved fixed layout profile."""
    if _canonical_markdown is None and not _v_is_file(markdown_path):
        raise FileNotFoundError(f"缺少规范 Markdown：{markdown_path}")
    constraints = _constraints_for_workspace(workspace)
    if constraints is not None and constraints.layout_profile != "a4-standard":
        raise ValueError(f"不支持的版式标识：{constraints.layout_profile}")
    qmd_path = workspace.root / "delivery" / "book.qmd"
    canonical_markdown = _canonical_markdown if _canonical_markdown is not None else _v_read_text(markdown_path, encoding="utf-8")
    try:
        markdown = render_reader_markdown(canonical_markdown)
        rendered_markdown = qmd_markdown if qmd_markdown is not None else render_qmd_markdown(canonical_markdown)
    except CalloutFormatError as error:
        raise ValueError(f"Quarto 提示框结构无效：{error}") from error
    if markdown != canonical_markdown:
        if _outputs is None:
            _v_write_text(markdown_path, markdown, encoding="utf-8")
        else:
            _outputs[markdown_path] = markdown.encode('utf8')
    title = _book_title(workspace, markdown)
    audience = _AUDIENCE_LABELS[
        constraints.audience_level.value if constraints else "intermediate"
    ]
    presentation = None
    brief_path = workspace.root / "planning" / "product-brief.md"
    if _v_is_file(brief_path):
        _, brief_body = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
        presentation = parse_presentation_overrides(brief_body)
    publication_metadata = load_or_create_publication_metadata(
        workspace,
        title=title,
        audience_label=audience,
        language=constraints.language if constraints else "zh-CN",
        overrides=presentation,
        _persist=_outputs is None,
    )
    if _outputs is not None:
        _outputs[workspace.root/'delivery'/PUBLICATION_METADATA_FILENAME] = yaml.safe_dump(
            publication_metadata.model_dump(mode='json'), allow_unicode=True, sort_keys=False).encode('utf8')
    _write_pdf_contract_assets(workspace, publication_metadata, _outputs=_outputs)
    metadata = {
        "title": title,
        "title-block": False,
        "pdf-engine": "xelatex",
        "format": {
            "pdf": {
                "papersize": "a4",
                "fontsize": f"{publication_metadata.body_font_size}pt",
                "geometry": _geometry_for_publication(publication_metadata),
                "documentclass": "extreport",
                "keep-tex": True,
                "latex-auto-install": False,
            }
        },
        "toc": True,
        "toc-title": "目录",
        "toc-depth": 2,
        "toc-own-page": True,
        "include-in-header": [_PDF_STYLE_FILE, _PDF_TOC_FILE, _PDF_COMPONENTS_FILE],
        "include-before-body": [_PDF_COVER_FILE],
        "filters": [_PDF_COMPONENTS_FILTER_FILE],
        "kp-pdf-profile": publication_metadata.profile_id,
        "resource-path": [".."],
        "lang": constraints.language if constraints else "zh-CN",
        "mainfont": "TeX Gyre Heros" if publication_metadata.profile_id == PRESENTATION_PROFILE_ID else "TeX Gyre Termes",
        "sansfont": "TeX Gyre Heros",
        "monofont": "Consolas",
        "CJKmainfont": "Microsoft YaHei",
        "CJKsansfont": "Microsoft YaHei",
        "CJKmonofont": "Microsoft YaHei",
    }
    if publication_metadata.profile_id == PRESENTATION_PROFILE_ID:
        for name in ("mainfont", "sansfont", "monofont", "CJKmainfont", "CJKsansfont", "CJKmonofont"):
            metadata.pop(name)
        metadata["kp-font-role-policy"] = publication_metadata.font_policy
    source = f"---\n{yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)}---\n{rendered_markdown}"
    if _outputs is None:
        _v_write_text(qmd_path, source, encoding="utf-8")
    else:
        _outputs[qmd_path] = source.encode('utf8')
    return qmd_path


def count_pdf_pages(pdf_path: Path) -> int:
    """Read the actual page count from a valid rendered PDF."""
    return len(PdfReader(pdf_path).pages)


def pdf_body_page_numbers(workspace: Workspace, pdf_path: Path) -> list[int]:
    """Locate the first actual chapter destination rather than subtracting two pages."""
    chapters = sorted((workspace.root / "chapters").glob("chapter-[0-9][0-9].md"))
    if chapters:
        body = parse_markdown_artifact(_v_read_text(chapters[0], encoding="utf-8"))[1]
    else:
        book = workspace.root / "delivery" / "book.md"
        body = _v_read_text(book, encoding="utf-8") if _v_is_file(book) else ""
    match = _TITLE.search(body)
    if match is None:
        raise ValueError("无法定位当前书稿的首章标题")

    def normalize(title: str) -> str:
        return re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", title).strip()

    expected = normalize(match.group(1))
    reader = PdfReader(pdf_path)

    def locate(items) -> int | None:
        for item in items:
            if isinstance(item, list):
                result = locate(item)
                if result is not None:
                    return result
            elif isinstance(item, dict) and normalize(str(item.get("/Title", ""))) == expected:
                return reader.get_destination_page_number(item)
        return None

    start = locate(reader.outline)
    if start is None:
        raise ValueError("PDF 书签没有与当前首章匹配的实际页目标；不能猜测正文页数")
    return list(range(start + 1, len(reader.pages) + 1))


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
            _v_load_yaml(_v_read_text(metadata_path, encoding="utf-8"))
        )
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return [
            _release_finding(
                "PDF_FONT_SIZE_CONTRACT_FAILED",
                "delivery/publication-metadata.yaml",
                "无法读取当前出版配置中的正文字号。",
            )
        ]
    if not _v_is_file(tex_path):
        return [
            _release_finding(
                "PDF_FONT_SIZE_CONTRACT_FAILED",
                "delivery/book.tex",
                "未保留 XeLaTeX 源稿，无法验证 PDF 的实际正文字号。",
            )
        ]
    match = _TEX_DOCUMENTCLASS.search(_v_read_text(tex_path, encoding="utf-8", errors="replace"))
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


@contextmanager
def _quarto_paths(
    qmd_path: Path, output_dir: Path, cwd: Path, quarto_path: Path, workspace: Workspace | None
) -> Iterator[tuple[Path, Path, Path, dict[str, str]]]:
    """Give Windows Quarto ASCII paths when its Lua filter cannot open Unicode paths."""
    environment = os.environ.copy()
    environment["QUARTO_TEXLIVE_AUTO_INSTALL"] = "false"
    configured_alias = environment.get("KP_WORKSPACE_ASCII_ALIAS")
    physical_root = environment.get("KP_WORKSPACE_PHYSICAL_ROOT")
    if configured_alias or physical_root:
        if not configured_alias or not physical_root or not configured_alias.isascii():
            raise RuntimeError("Quarto工作区路径别名配置不完整或非ASCII。")
        alias = Path(configured_alias)
        physical = Path(physical_root).resolve()
        if not alias.is_absolute() or alias.resolve() != physical:
            raise RuntimeError("Quarto工作区路径别名与实际目录不一致。")
        relative_cwd = cwd.relative_to(physical)
        mapped_cwd = alias / relative_cwd
        local_temp = mapped_cwd / "runs/quarto-temp"
        _v_mkdir(local_temp,parents=True,exist_ok=True)
        environment["TEMP"] = environment["TMP"] = str(local_temp)
        yield alias / qmd_path.relative_to(physical), alias / output_dir.relative_to(physical), mapped_cwd, environment
        return
    if workspace is None or os.name != "nt" or str(cwd).isascii() or not _v_is_file(quarto_path):
        yield qmd_path, output_dir, cwd, environment
        return

    for letter in "ZYXWVUTSRQPONMLKJIH":
        drive = f"{letter}:"
        if _v_exists(Path(f"{drive}\\")):
            continue
        mapped = subprocess.run(
            ["subst", drive, str(cwd)], capture_output=True, timeout=5, check=False
        )
        if mapped.returncode != 0:
            continue
        try:
            alias = Path(f"{drive}\\")
            local_temp = cwd / "runs" / "quarto-temp"
            _v_mkdir(local_temp,parents=True,exist_ok=True)
            environment["TEMP"] = str(alias / "runs" / "quarto-temp")
            environment["TMP"] = environment["TEMP"]
            yield alias / qmd_path.relative_to(cwd), alias / output_dir.relative_to(cwd), alias, environment
        finally:
            removed = subprocess.run(
                ["subst", drive, "/d"], capture_output=True, timeout=5, check=False
            )
            if removed.returncode != 0:
                raise RuntimeError(f"Quarto 临时路径 {drive} 未能撤销。")
        return
    raise RuntimeError("Quarto 无法为含非 ASCII 字符的书稿路径分配临时路径。")


def render_pdf(qmd_path: Path, output_dir: Path, workspace: Workspace | None = None, *,
               compile_timeout_seconds: int = 300) -> Path:
    """Render a Quarto source file to PDF, failing clearly when Quarto is unavailable."""
    if type(compile_timeout_seconds) is not int or not 1 <= compile_timeout_seconds <= 600:
        raise ValueError("本地编译预算须为1—600秒的整数；网络与完整实验预算另计")
    from knowledge_product_workflow.production_dependencies import require_no_deferred_repairs
    if workspace is not None:
        require_reader_resource_preflight(workspace)
        book = workspace.root / "delivery/book.md"
        if _v_is_file(book) and (issues := reader_resource_bundle_findings(workspace, _v_read_text(book, encoding="utf-8"))):
            raise ValueError(issues[0].message)
        require_no_deferred_repairs(workspace)
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
    if not _v_is_file(qmd_path):
        if workspace is not None:
            workspace.append_event("pdf_render_failed", {"reason": "qmd_missing"})
        raise FileNotFoundError(f"{QMD_MISSING}：{qmd_path}")
    qmd_source = _v_read_text(qmd_path, encoding="utf-8")
    style_file = qmd_path.parent / _PDF_STYLE_FILE
    if ("kp-font-role-policy: preferred" in qmd_source and _v_is_file(style_file)
            and "knowledge-product-pdf-v6-reference-fonts" in _v_read_text(style_file, encoding="utf-8")):
        from knowledge_product_workflow.pdf_fonts import probe_preferred_fonts
        checks = _publication_call(workspace, 'font_probe', probe_preferred_fonts, cwd)
        failed = [item for item in checks.values() if not item["available"]]
        if failed:
            missing = [item['font'] for item in failed]
            statuses = {item.get('status', 'missing') for item in failed}
            if workspace is not None:
                workspace.append_event("pdf_render_failed", {
                    "reason": "preferred_fonts_missing" if statuses == {'missing'} else 'preferred_fonts_probe_unavailable',
                    "fonts": missing, 'probe_statuses':sorted(statuses)})
            raise RuntimeError("默认字体缺失或探测不可用：" + "、".join(missing)
                               + "；探针状态=" + ','.join(sorted(statuses))
                               + "。未知时先核本次探针，不盲重试整条流水线。若接受兼容字体，须显式确认font_policy=fallback；不会静默替换。")
    qmd_leaks = reader_component_leaks(qmd_source, allow_fenced_divs=True)
    if qmd_leaks:
        if workspace is not None:
            workspace.append_event("pdf_render_failed", {"reason": "internal_component_leaked", "lines": qmd_leaks})
        raise RuntimeError("Quarto 源稿泄漏了未转换的 KP 提示框标记；请重新受控汇编。")
    quarto = _publication_call(workspace, 'quarto_resolve', resolve_quarto)
    if not quarto.available or quarto.path is None:
        if workspace is not None:
            workspace.append_event(
                "pdf_render_failed",
                {"reason": "quarto_unavailable", "failed_candidates": list(quarto.failed_candidates)},
            )
        raise RuntimeError(f"{QUARTO_UNAVAILABLE}；请运行 kp doctor 查看已检查的位置。")
    render_log = ""
    log_prefix = ""
    previous_pdf = _v_read_bytes(expected_output) if _v_is_file(expected_output) else None
    def restore_failed_output():
        if previous_pdf is None:
            _v_unlink(expected_output, missing_ok=True)
        else:
            restored = expected_output.with_name(f".{expected_output.name}.{uuid.uuid4().hex}.restore")
            try:
                _v_write_bytes(restored, previous_pdf)
                try:
                    replace_file(restored, expected_output)
                except PermissionError as error:
                    raise RuntimeError(f"旧PDF恢复被文件锁阻断；安全副本保留在{restored}，关闭占用后恢复") from error
            finally:
                if not _v_exists(restored) or (_v_is_file(expected_output) and _v_read_bytes(expected_output) == previous_pdf):
                    _v_unlink(restored, missing_ok=True)
    try:
        with _quarto_paths(qmd_path, output_dir, cwd, quarto.path, workspace) as (
            render_source,
            render_output_dir,
            render_cwd,
            environment,
        ):
            from .quarto_runtime import quarto_command
            arguments = ["render", str(render_source), "--to", "pdf", "--output-dir", str(render_output_dir)]
            with quarto_command(quarto.path, arguments, render_cwd, environment) as (command, environment):
                log_prefix = "\n".join(
                    (
                        f"命令：{subprocess.list2cmdline(command)}",
                        f"有效工作目录：{render_cwd}",
                        f"预期输出：{expected_output}",
                        f"本地编译预算：{compile_timeout_seconds}秒（不自动重试；网络及预览另计）",
                        "Quarto 输出：",
                    )
                )
                completed = _publication_call(workspace, 'pdf_compile', subprocess.run,
                    command,
                    check=True,
                    timeout=compile_timeout_seconds,
                    env=environment,
                    cwd=str(render_cwd),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
        render_log = log_prefix + "\n" + (completed.stdout or "") + (completed.stderr or "")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as error:
        restore_failed_output()
        render_log = log_prefix + "\n" + (getattr(error, "stdout", "") or "") + (getattr(error, "stderr", "") or "")
        render_log += f"\n失败原因：{type(error).__name__}: {error}\n"
        if workspace is not None:
            _write_pdf_render_log(workspace, render_log)
        if workspace is not None:
            workspace.append_event("pdf_render_failed", {"reason": type(error).__name__})
        raise RuntimeError("Quarto PDF 渲染失败") from error
    if workspace is not None:
        _write_pdf_render_log(workspace, render_log)
    overflow = [pattern for pattern in _PDF_OVERFLOW_PATTERNS if pattern in render_log]
    if overflow:
        restore_failed_output()
        if workspace is not None:
            workspace.append_event("pdf_render_failed", {"reason": "tex_overflow", "patterns": overflow})
        raise RuntimeError("PDF 渲染检测到 TeX 版式溢出；请修订表格、代码块或长链接后重新渲染。")
    if not _v_is_file(expected_output):
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
        _v_is_file(workspace.root / "delivery" / PUBLICATION_METADATA_FILENAME)
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
    if workspace is not None and _v_is_file(workspace.root / "delivery" / PUBLICATION_METADATA_FILENAME):
        metadata_path = workspace.root / "delivery" / PUBLICATION_METADATA_FILENAME
        workspace.append_event(
            "pdf_compiled",
            {
                "pdf": _relative(workspace, expected_output),
                "pdf_sha256": _sha256(expected_output),
                "qmd_sha256": _sha256(qmd_path),
                "publication_metadata_sha256": _sha256(metadata_path),
            },
        )
        try:
            preview_path, preview = _publication_call(workspace, 'pdf_preview', render_pdf_preview_set,
                expected_output, workspace.root / "runs" / "pdf-preview"
            )
        except (OSError, RuntimeError, ValueError) as error:
            workspace.append_event("pdf_render_failed", {"reason": "preview_generation_failed"})
            raise RuntimeError(f"PDF 已生成，但页面预览失败：{error}") from error
        workspace.append_event(
            "pdf_preview_generated",
            {
                "preview_manifest": _relative(workspace, preview_path),
                "renderer_available": preview.renderer_available,
            },
        )
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
    _v_mkdir(path.parent,parents=True,exist_ok=True)
    _v_write_text(path, contents, encoding="utf-8")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(_v_read_bytes(path)).hexdigest()


def _release_finding(code: str, location: str, message: str) -> QualityFinding:
    return QualityFinding(code=code, severity=Severity.BLOCKER, location=location, message=message)


def _expected_book_markdown(workspace: Workspace) -> str | None:
    chapters = sorted(workspace.root.joinpath("chapters").glob("chapter-[0-9][0-9].md"))
    if not chapters:
        return None
    try:
        return _reader_book_from_bodies(_chapter_bodies(chapters), workspace)
    except (OSError, ValueError, CalloutFormatError):
        return None


@_verification_read(transaction_view=True)
def release_readiness_findings(workspace: Workspace) -> list[QualityFinding]:
    """Recompute the upstream graph required for a v1.0 delivery release."""
    brief_path = workspace.root / "planning" / "product-brief.md"
    # Library-level helpers retain a narrow legacy mode for callers without a KP Brief.
    if not _v_is_file(brief_path):
        return []
    findings: list[QualityFinding] = []
    book = workspace.root / "delivery/book.md"
    if _v_is_file(book):
        findings.extend(reader_resource_bundle_findings(workspace, _v_read_text(book, encoding="utf-8")))
    try:
        brief_metadata, _ = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
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
    if not _v_is_file(workspace.root / "chapters" / "chapter-01-sample.approved"):
        findings.append(
            _release_finding(
                "RELEASE_SAMPLE_NOT_APPROVED",
                "chapters/chapter-01-sample.approved",
                "第一章样章尚未批准。",
            )
        )
    from knowledge_product_workflow.chapter_compliance import (
        validate_current_chapter_compliance_in_view as validate_current_chapter_compliance,
    )
    from knowledge_product_workflow.workflows.authoring import (
        require_current_chapter_contract_in_view as require_current_chapter_contract,
    )

    required_structure_inputs: set[str] = set()
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
        if not _v_is_file(chapter_path):
            findings.append(
                _release_finding(
                    "RELEASE_CHAPTER_MISSING",
                    f"chapters/chapter-{chapter.chapter_number:02d}.md",
                    "当前蓝图要求的章节正文不存在。",
                )
            )
            continue
        findings.extend(validate_current_chapter_compliance(workspace, chapter_path, final_review=True))
        structure_input = f"runs/editorial/chapter-{chapter.chapter_number:02d}-structure-review.json"
        if _v_is_file(workspace.root / structure_input):
            required_structure_inputs.add(structure_input)

    markdown_path = workspace.root / "delivery" / "book.md"
    qmd_path = workspace.root / "delivery" / "book.qmd"
    input_manifest_path = book_input_manifest_path(workspace)
    if not _v_is_file(markdown_path) or not _v_is_file(qmd_path):
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
    if expected_markdown is None or _v_read_text(markdown_path, encoding="utf-8") != expected_markdown:
        findings.append(
            _release_finding(
                "RELEASE_BOOK_STALE",
                "delivery/book.md",
                "书稿与当前章节正文不一致；请重新汇编。",
            )
        )
    if not _v_is_file(input_manifest_path):
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
            _v_read_text(input_manifest_path, encoding="utf-8")
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
    missing_structure_inputs = required_structure_inputs - {item.path for item in input_manifest.inputs}
    if missing_structure_inputs:
        findings.append(_release_finding("RELEASE_INPUT_HASH_MISMATCH", "delivery/book-input-manifest.json",
            "书稿输入清单未绑定当前结构审核：" + "、".join(sorted(missing_structure_inputs)) + "；请重新受控汇编。"))
    if input_manifest.pdf_profile not in {PRESENTATION_PROFILE_ID, V5_PRESENTATION_PROFILE_ID, LEGACY_PRESENTATION_PROFILE_ID}:
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
        if path is None or not _v_is_file(path) or _sha256(path) != artifact.sha256:
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
    if not _v_is_file(path):
        return ["缺少交付清单"]
    try:
        raw = _v_load_yaml(_v_read_text(path, encoding="utf-8"))
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
            if not _v_is_file(candidate) or _sha256(candidate) != item["sha256"]:
                return [f"交付清单输入已过期：{item['path']}"]
    return []


def _review_proofs(workspace: Workspace) -> list[dict[str, object]]:
    """Return internal review provenance after delivery validation has accepted it."""
    from knowledge_product_workflow.review_batches import review_batch_proofs

    return review_batch_proofs(workspace)


@_verification_read
def create_delivery_manifest(
    workspace: Workspace, markdown_path: Path | None, pdf_path: Path | None
) -> Path:
    """Persist checksums for the two final delivery artifacts."""
    from knowledge_product_workflow.production_dependencies import require_no_deferred_repairs
    require_no_deferred_repairs(workspace)
    if markdown_path is not None:
        markdown_path = markdown_path.expanduser().resolve()
    if pdf_path is not None:
        pdf_path = pdf_path.expanduser().resolve()
    constraints = _constraints_for_workspace(workspace)
    requires_markdown = constraints.requires_markdown if constraints else True
    requires_pdf = constraints.requires_pdf if constraints else True
    if requires_markdown and (markdown_path is None or not _v_is_file(markdown_path)):
        raise FileNotFoundError("交付格式要求 Markdown 文件")
    if requires_pdf and (pdf_path is None or not _v_is_file(pdf_path)):
        raise FileNotFoundError("交付格式要求 PDF 文件")
    report_path = workspace.root / "delivery" / "qa-report.json"
    if not _v_is_file(report_path):
        raise FileNotFoundError("缺少交付质量报告；请先运行 kp validate-delivery，再发布")
    from knowledge_product_workflow.quality import (
        QualityReport,
        QualityReportValidity,
        quality_report_archive_path,
        quality_report_validity,
    )

    try:
        report = QualityReport.model_validate_json(_v_read_bytes(report_path))
    except ValueError as error:
        raise ValueError("交付质量报告损坏；先运行 kp validate-delivery，再发布") from error
    archive = quality_report_archive_path(workspace, report.report_id or 'missing-report-id')
    if (not archive.resolve().is_relative_to((workspace.root / 'runs/quality-reports').resolve())
            or not _v_is_file(archive) or _v_read_bytes(archive) != _v_read_bytes(report_path)):
        raise ValueError('交付质量报告与不可变归档不一致；先运行 kp validate-delivery，不接受手改通过')
    if quality_report_validity(workspace, report, "delivery") is not QualityReportValidity.CURRENT:
        raise ValueError("交付质量报告缺少当前完整绑定或已过期；先运行 kp validate-delivery，再发布")
    if not report.publishable:
        raise ValueError("质量报告未通过，无法创建交付清单")
    manifest_path = workspace.root / "delivery" / "delivery-manifest.yaml"
    artifacts: list[dict[str, str]] = []
    if requires_markdown and markdown_path is not None:
        artifacts.append({"path": workspace_relative_path(markdown_path, workspace), "sha256": _sha256(markdown_path)})
    if requires_pdf and pdf_path is not None:
        artifacts.append({"path": workspace_relative_path(pdf_path, workspace), "sha256": _sha256(pdf_path)})
    resource_markdown = workspace.root / "delivery/book.md"
    if _v_is_file(resource_markdown):
        text = _v_read_text(resource_markdown, encoding="utf-8")
        if _reader_resource_payloads(workspace, text):
            if issues := reader_resource_bundle_findings(workspace, text):
                raise ValueError(issues[0].message)
            for name in ("practice-resources.zip", "practice-resources-manifest.json"):
                path = workspace.root / "delivery" / name
                artifacts.append({"path": workspace_relative_path(path, workspace), "sha256": _sha256(path)})
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
        if _v_is_file(candidate):
            rendering_inputs.append({"path": workspace_relative_path(candidate, workspace), "sha256": _sha256(candidate)})
    release_inputs = [{"path": workspace_relative_path(report_path, workspace), "sha256": _sha256(report_path)}]
    input_manifest = book_input_manifest_path(workspace)
    if _v_is_file(input_manifest):
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
    _v_write_text(manifest_path, yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    workspace.append_event("delivery_manifest_created", {"manifest": str(manifest_path)})
    return manifest_path


def refresh_publication_delivery(workspace: Workspace, *, changed_fields: list[str] | None = None) -> dict:
    """Safely rebuild only derived delivery artifacts after a profile update.

    The profile never changes chapters, contracts, reviews, or source ledgers.
    If no book has been assembled yet, there is nothing to render and the
    normal publication phase will consume the profile later.
    """
    from knowledge_product_workflow.diagrams import refresh_diagram_theme

    diagram_fields = {'palette','brand_color_intent','illustration_palette_scheme','illustration_category_colors',
                      'illustration_style_revision','body_font_size','reader_layout_revision','rendering_resources'}
    if changed_fields is None or diagram_fields.intersection(changed_fields):
        refresh_diagram_theme(workspace)
    existing_book = workspace.root / "delivery" / "book.md"
    if not _v_is_file(existing_book):
        return {"refreshed": False, "markdown": None, "pdf": None, "report": None, "manifest": None}

    markdown_path = assemble_book(workspace)
    constraints = _constraints_for_workspace(workspace)
    pdf_path: Path | None = None
    if constraints is None or constraints.requires_pdf:
        pdf_path = render_pdf(
            workspace.root / "delivery" / "book.qmd", workspace.root / "delivery", workspace
        )
        workspace.append_event('publication_delivery_refreshed',{'refreshed':True,'pdf_refreshed':True,
            'delivery_publishable':False,'manifest_created':False,'pending':'pdf_visual_audit'})
        return {'refreshed':True,'markdown':markdown_path,'pdf':pdf_path,'report':None,'manifest':None,
                'pending':'pdf_visual_audit'}

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
