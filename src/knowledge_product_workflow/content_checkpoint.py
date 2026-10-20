"""Body readiness is distinct from complete, publishable chapter submission."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path

from knowledge_product_workflow.chapter_compliance import (
    ChapterComplianceSubmission,
    _load_mapping,
    _reader_body_from_source,
    _require_workspace_file,
    _requires_chinese_humanize,
    validate_chapter_submission,
)
from knowledge_product_workflow.content_plan import content_plan_path
from knowledge_product_workflow.developmental import (
    developmental_report_path,
    require_current_developmental_report,
    structure_review_path,
)
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.humanize import (
    humanize_report_path,
    require_current_humanize_report,
)
from knowledge_product_workflow.illustration_tasks import require_diagram_task
from knowledge_product_workflow.illustrations import review_semantic
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.quality import Severity
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from knowledge_product_workflow.workspace import Workspace

DEFERRED_ASSET_CODES = frozenset({
    "PRESENTATION_ASSET_MISSING", "DIAGRAM_ASSET_UNBOUND", "DIAGRAM_VISUAL_CONTRACT_INVALID",
    "ILLUSTRATION_STYLE_PROOF_REQUIRED", "ILLUSTRATION_STYLE_FAILED", "ILLUSTRATION_CONTENT_FAILED",
})
FIGURE = re.compile(r"!\[[^\]]*\]\(assets/figures/(?P<id>[a-z0-9-]+)\.(?:pdf|png)\)")


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def checkpoint_path(workspace: Workspace, number: int) -> Path:
    return workspace.root / "runs/orchestration/content-ready" / f"chapter-{number:02d}.json"


def current_content_checkpoint(workspace: Workspace, number: int) -> dict | None:
    path = checkpoint_path(workspace, number)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        body = _reader_body_from_source(workspace.root / "chapters" / f"chapter-{number:02d}.md").strip() + "\n"
        if record["chapter_number"] != number or record["chapter_body_sha256"] != sha256(body.encode()).hexdigest():
            return None
        require_current_chapter_contract(workspace, number)
        contract, _ = require_current_chapter_contract(workspace, number)
        require_current_developmental_report(workspace, number, body, contract)
        if _requires_chinese_humanize(workspace):
            require_current_humanize_report(workspace, number, body, contract)
        for relative, digest in record["inputs"].items():
            input_path = (workspace.root / relative).resolve()
            if not input_path.is_relative_to(workspace.root.resolve()):
                return None
            if _digest(input_path) != digest:
                from knowledge_product_workflow.change_impact import (
                    plan_binding_matches,
                    proof_contract_matches,
                )
                if relative == f"chapters/chapter-{number:02d}-contract.md":
                    if not proof_contract_matches(workspace, number, digest, "compliance", path):
                        return None
                elif relative == f"chapters/chapter-{number:02d}-content-plan.yaml":
                    if not plan_binding_matches(workspace, number, digest):
                        return None
                else:
                    return None
        return record
    except (OSError, ValueError, KeyError):
        return None


def checkpoint_chapter_content(workspace: Workspace, number: int, source_path: Path,
                               mapping_path: Path) -> Path:
    source = _require_workspace_file(workspace, source_path, label="正文检查点源稿")
    mapping_path = _require_workspace_file(workspace, mapping_path, label="正文合规映射")
    destination = workspace.root / "chapters" / f"chapter-{number:02d}.md"
    metadata, _ = parse_markdown_artifact(destination.read_text(encoding="utf-8"))
    if metadata.status is not ArtifactStatus.IN_PRODUCTION:
        raise ValueError("正文检查点只能在当前in_production章节租约内创建")
    body = _reader_body_from_source(source).strip() + "\n"
    contract, _ = require_current_chapter_contract(workspace, number)
    mapping = _load_mapping(workspace, number, mapping_path)
    submission = ChapterComplianceSubmission(
        schema_version=1, chapter_number=number, chapter_body_sha256=sha256(body.encode()).hexdigest(),
        contract_sha256=_digest(contract), **mapping.model_dump(),
    )
    findings = validate_chapter_submission(workspace, number, body, submission, path=destination)
    blockers = [f for f in findings if f.severity is Severity.BLOCKER]
    unsafe = [f for f in blockers if f.code not in DEFERRED_ASSET_CODES]
    if unsafe:
        raise ValueError("正文尚未就绪：" + "；".join(f.message for f in unsafe[:3]))
    if not blockers:
        raise ValueError("无待办资产，使用submit-chapter完成正式提交")
    ids = sorted({m.group("id") for m in FIGURE.finditer(body)})
    if not ids:
        raise ValueError("待办资产必须先有受控图语义与合法引用，不能跳过图任务计划")
    for diagram_id in ids:
        semantic = review_semantic(workspace, diagram_id)
        if semantic is None:
            raise ValueError(f"图{diagram_id}缺少独立于像素资产的受控语义")
        require_diagram_task(workspace, number, semantic)
    require_current_developmental_report(workspace, number, body, contract)
    if _requires_chinese_humanize(workspace):
        require_current_humanize_report(workspace, number, body, contract)
    pending = {f.location.removeprefix("图:") for f in blockers if f.location.startswith("图:")}
    if any(f.code in {"DIAGRAM_ASSET_UNBOUND", "PRESENTATION_ASSET_MISSING", "DIAGRAM_VISUAL_CONTRACT_INVALID"} for f in blockers):
        pending.update(ids)
    inputs = [contract, content_plan_path(workspace, number), developmental_report_path(workspace, number),
              structure_review_path(workspace, number), workspace.root / "research/source-ledger.jsonl",
              humanize_report_path(workspace, number)]
    inputs.extend(workspace.root / "runs/diagrams" / f"{diagram_id}.input.json" for diagram_id in ids)
    record = {"schema_version": 1, "chapter_number": number,
              "chapter_body_sha256": submission.chapter_body_sha256,
              "inputs": {p.relative_to(workspace.root).as_posix(): _digest(p) for p in inputs if _v_is_file(p)},
              "pending_assets": sorted(pending or ids), "asset_findings": [f.model_dump(mode="json") for f in blockers],
              "compliance": submission.model_dump(mode="json"), "publishable": False}
    output = checkpoint_path(workspace, number)
    _v_mkdir(output.parent,parents=True,exist_ok=True)
    encoded = json.dumps(record, ensure_ascii=False, indent=2)
    if _v_is_file(output) and output.read_text(encoding="utf-8") == encoded and current_content_checkpoint(workspace, number):
        return output
    _v_write_text(destination, render_markdown_artifact(metadata, body), encoding="utf-8")
    _v_write_text(output, encoded, encoding="utf-8")
    workspace.append_event("chapter_content_checkpointed", {"chapter_number": number,
                           "pending_assets": record["pending_assets"], "publishable": False})
    return output
