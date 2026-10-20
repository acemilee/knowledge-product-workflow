"""Immutable task inputs and private proof workspaces containing exact approved bytes."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from knowledge_product_workflow.production_dependencies import (
    file_digest,
    payload_digest,
    workspace_file,
)
from knowledge_product_workflow.verification_context import copyfile as _v_copyfile
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import read_bytes as _v_read_bytes
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workspace import Workspace


class CandidateProofSet(BaseModel):
    task_id: str
    candidate_version: int
    source_sha256: str
    input_manifest_sha256: str
    reports: dict[str, str] = Field(default_factory=dict)
    report_bindings: dict[str, str] = Field(default_factory=dict)
    private_input_bindings: dict[str, str] = Field(default_factory=dict)
    checks: dict = Field(default_factory=dict)
    required_missing: list[str] = Field(default_factory=list)
    diagnostic_only: bool = True


class CandidateEvaluationContext(BaseModel):
    task_id: str
    chapter_number: int
    candidate_version: int
    snapshot_root: str
    private_result_root: str
    source_relative: str
    input_bindings: dict[str, str]
    input_manifest_sha256: str
    canonical_root: str
    live_bindings: dict[str, str] = Field(default_factory=dict)
    boundary_repetition_advice: dict = Field(default_factory=dict)

    def verify(self) -> None:
        self._verified_input_bytes()

    def _verified_input_bytes(self) -> dict[str, bytes]:
        """One owned byte snapshot for internal projection; never a lasting grant."""
        snapshot = Path(self.snapshot_root).resolve()
        canonical = Path(self.canonical_root).resolve()
        if (self.chapter_number < 1 or self.candidate_version < 1
                or not re.fullmatch(r"[a-zA-Z0-9_-]+", self.task_id)
                or self.source_relative not in self.input_bindings
                or not snapshot.is_relative_to(canonical / "runs/production")
                or snapshot.name != "inputs" or snapshot.parent.name != f"v{self.candidate_version}"
                or snapshot.parent.parent.name != self.task_id
                or Path(self.private_result_root).resolve() != snapshot.parent / "results"):
            raise ValueError("候选上下文跨任务或路径越界")
        inputs = {}
        for relative, expected in self.input_bindings.items():
            path = (snapshot / relative).resolve()
            if not path.is_relative_to(snapshot) or not _v_is_file(path):
                raise ValueError(f"冻结输入缺失、越界或被篡改：{relative}")
            data = _v_read_bytes(path)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"冻结输入缺失、越界或被篡改：{relative}")
            inputs[relative] = data
        if payload_digest({"task_id": self.task_id, "chapter_number": self.chapter_number,
                           "candidate_version": self.candidate_version,
                           "input_bindings": self.input_bindings,
                           "live_bindings": self.live_bindings,
                           **({"boundary_advice": self.boundary_repetition_advice} if self.boundary_repetition_advice else {})}) != self.input_manifest_sha256:
            raise ValueError("输入清单被篡改")
        return inputs

    def read_input(self, relative: str) -> str:
        if relative not in self.input_bindings:
            raise ValueError("读取路径不在本任务输入清单中")
        self.verify()
        return _v_read_text(Path(self.snapshot_root) / relative, encoding="utf-8")


def freeze_candidate_context(
    workspace: Workspace, *, chapter_number: int, candidate_version: int, source: Path,
    input_paths: list[str], run_id: str, task_id: str,
) -> CandidateEvaluationContext:
    """Never discover additional canonical evidence after the manifest is frozen."""
    from knowledge_product_workflow.production_intake import require_consistent_production
    from knowledge_product_workflow.production_tasks import production_lock

    with production_lock(workspace):
        require_consistent_production(workspace)
        return _freeze_candidate_context(workspace, chapter_number=chapter_number,
            candidate_version=candidate_version, source=source, input_paths=input_paths,
            run_id=run_id, task_id=task_id)


def _freeze_candidate_context(workspace: Workspace, *, chapter_number: int,
    candidate_version: int, source: Path, input_paths: list[str], run_id: str,
    task_id: str) -> CandidateEvaluationContext:
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", value) for value in (run_id, task_id)):
        raise ValueError("任务／运行ID格式不合法")
    source = source.resolve()
    if not source.is_relative_to(workspace.root.resolve()) or not _v_is_file(source):
        raise ValueError("候选源稿缺失或越界")
    relative_source = source.relative_to(workspace.root.resolve()).as_posix()
    paths = sorted({Path(p).as_posix() for p in [*input_paths, relative_source]})
    if any(Path(p).is_absolute() or ".." in Path(p).parts for p in paths):
        raise ValueError("输入清单必须为工作区内相对路径")
    bindings = {relative: file_digest(workspace_file(workspace, relative)) for relative in paths}
    from knowledge_product_workflow.developmental import boundary_repetition_context
    from knowledge_product_workflow.frontmatter import parse_markdown_artifact
    text = _v_read_text(source, encoding="utf-8")
    try:
        text = parse_markdown_artifact(text)[1]
    except ValueError:
        pass
    advice = boundary_repetition_context(workspace, chapter_number, text)
    base = workspace.root / "runs/production" / run_id / "tasks" / task_id / f"v{candidate_version}"
    if not base.resolve().is_relative_to(workspace.root.resolve()):
        raise ValueError("任务目录越界")
    snapshot = base / "inputs"
    _v_mkdir(snapshot, parents=True, exist_ok=False)
    for relative in paths:
        original = workspace_file(workspace, relative)
        target = snapshot / relative
        _v_mkdir(target.parent, parents=True, exist_ok=True)
        _v_copyfile(original, target)
        if file_digest(target) != bindings[relative] or file_digest(original) != bindings[relative]:
            raise ValueError("冻结输入期间版本变化，保留未完成快照")
    context = CandidateEvaluationContext(
        task_id=task_id, chapter_number=chapter_number, candidate_version=candidate_version,
        snapshot_root=str(snapshot), private_result_root=str(base / "results"),
        source_relative=relative_source, input_bindings=bindings,
        input_manifest_sha256=payload_digest({"task_id": task_id, "chapter_number": chapter_number,
            "candidate_version": candidate_version, "input_bindings": bindings, "live_bindings": {}, "boundary_advice": advice}),
        boundary_repetition_advice=advice,
        canonical_root=str(workspace.root.resolve()),
    )
    context.verify()
    _v_write_text(base / "context.json", context.model_dump_json(indent=2), encoding="utf-8")
    return context


def prepare_candidate_context(workspace: Workspace, task) -> CandidateEvaluationContext:
    from contextlib import nullcontext

    from knowledge_product_workflow.production_intake import require_consistent_production
    from knowledge_product_workflow.production_tasks import controller_lock_owned, production_lock
    with nullcontext() if controller_lock_owned(workspace) else production_lock(workspace):
        require_consistent_production(workspace)
        return _prepare_candidate_context(workspace, task)


def _prepare_candidate_context(workspace: Workspace, task) -> CandidateEvaluationContext:
    from knowledge_product_workflow.production_dependencies import (
        binding_digest,
        candidate_input_paths,
    )
    if task.source_relative is None:
        raise ValueError("作者尚未交付候选源稿")
    source = workspace_file(workspace, task.source_relative)
    context = _freeze_candidate_context(
        workspace, chapter_number=task.chapter_number, candidate_version=task.candidate_version,
        source=source, input_paths=candidate_input_paths(workspace, task.chapter_number, source),
        run_id=task.run_id, task_id=task.task_id,
    )
    live = dict(task.input_bindings)
    from knowledge_product_workflow.production_dependencies import (
        WRITER_FORMAT_BINDING,
        comparison_input_bindings,
    )
    _, comparisons = comparison_input_bindings(workspace, task.chapter_number)
    live.update(comparisons)
    for relative in context.input_bindings:
        # Existing unit/claim selectors define exactly what invalidates downstream.
        if (any(p != WRITER_FORMAT_BINDING and p.split("#", 1)[0] == relative for p in live)
                or relative == context.source_relative):
            continue
        if relative == "runs/events.jsonl":
            continue
        live[relative] = binding_digest(workspace, relative)
    context.live_bindings = live
    context.input_manifest_sha256 = payload_digest({"task_id": context.task_id,
        "chapter_number": context.chapter_number, "candidate_version": context.candidate_version,
        "input_bindings": context.input_bindings, "live_bindings": live,
        **({"boundary_advice": context.boundary_repetition_advice} if context.boundary_repetition_advice else {})})
    context.verify()
    _v_write_text(Path(context.snapshot_root).parent.joinpath("context.json"), context.model_dump_json(indent=2), encoding="utf-8")
    return context


def _proof_workspace(context: CandidateEvaluationContext) -> Workspace:
    """Materialize original approved artifacts; never synthesize approvals or missing proof."""
    context.verify()
    target = Path(context.private_result_root) / "proof-workspace"
    if _v_exists(target):
        raise ValueError("该候选版本已生成证明；复用已有结果或创建新版本")
    _v_mkdir(target, parents=True)
    for directory in ("planning", "research", "chapters", "runs", "delivery", "assets"):
        _v_mkdir(target / directory)
    for relative in context.input_bindings:
        destination = target / relative
        _v_mkdir(destination.parent, parents=True, exist_ok=True)
        _v_copyfile(Path(context.snapshot_root) / relative, destination)
    if context.boundary_repetition_advice:
        advice_path = target / "runs/editorial/frozen-boundary-advice.json"
        _v_mkdir(advice_path.parent, parents=True, exist_ok=True)
        _v_write_text(advice_path, json.dumps(context.boundary_repetition_advice, ensure_ascii=False), encoding="utf-8")
    return Workspace(root=target)


def evaluate_candidate(context: CandidateEvaluationContext) -> CandidateProofSet:
    """Execute current core checks privately; caller supplies every required frozen input."""
    from knowledge_product_workflow.chapter_compliance import check_chapter
    from knowledge_product_workflow.developmental import (
        create_structure_review_template,
        developmental_report_path,
        structure_review_path,
    )
    from knowledge_product_workflow.humanize import humanize_report_path, record_humanize_check

    context.verify()
    cached = Path(context.private_result_root, "proofs.json")
    if _v_is_file(cached):
        result = CandidateProofSet.model_validate_json(_v_read_text(cached, encoding="utf-8"))
        verify_candidate_proofs(context, result)
        return result
    for required in ("planning/product-brief.md", "planning/curriculum-blueprint.md",
        f"chapters/chapter-{context.chapter_number:02d}-contract.md"):
        if required not in context.input_bindings:
            raise ValueError("候选缺必需批准输入：" + required)
    canonical_state = Path(context.canonical_root, "runs/orchestration/state.json")
    if _v_is_file(canonical_state) and "runs/orchestration/state.json" not in context.input_bindings:
        raise ValueError("候选缺冻结Rubric／语言政策；禁止默认降级")
    workspace = _proof_workspace(context)
    source = workspace.root / context.source_relative
    result = CandidateProofSet(
        task_id=context.task_id, candidate_version=context.candidate_version,
        source_sha256=file_digest(source), input_manifest_sha256=context.input_manifest_sha256,
    )
    try:
        report = check_chapter(workspace, context.chapter_number, source)
        result.checks = report["checks"]
        result.required_missing = _missing_proofs(report)
        if report["checks"].get("source_structure", {}).get("state") != "blocked":
            if report["checks"].get("content_plan", {}).get("state") == "current":
                template = create_structure_review_template(workspace, context.chapter_number, source)
                template_path = Path(context.private_result_root) / "structure-template.json"
                _v_write_text(template_path, json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
                result.reports["structure_template"] = str(template_path)
            humanize, _ = record_humanize_check(workspace, context.chapter_number, source)
            result.reports["humanize"] = str(humanize)
        for label, path in (
            ("structure", structure_review_path(workspace, context.chapter_number)),
            ("developmental", developmental_report_path(workspace, context.chapter_number)),
            ("humanize", humanize_report_path(workspace, context.chapter_number)),
        ):
            if _v_is_file(path):
                result.reports[label] = str(path)
        result.required_missing = _missing_proofs(check_chapter(workspace, context.chapter_number, source))
        from knowledge_product_workflow.production_dependencies import (
            undeclared_candidate_dependencies,
        )
        result.required_missing.extend(undeclared_candidate_dependencies(workspace, context.chapter_number, _v_read_text(source, encoding="utf-8")))
    except (ValueError, FileNotFoundError) as error:
        result.required_missing.append(str(error))
    context.verify()
    result.report_bindings = {p: file_digest(Path(p)) for p in result.reports.values()}
    result.private_input_bindings = _private_input_bindings(context, result)
    _v_write_text(Path(context.private_result_root, "proofs.json"),
        result.model_dump_json(indent=2), encoding="utf-8"
    )
    return result


def _missing_proofs(report: dict) -> list[str]:
    # The controlled submission generates the compliance mapping. It is a next
    # action, not a proof a never-submitted private candidate can already have.
    return [a for a in report["refresh_actions"]
            if not a.startswith(("kp chapter-compliance-template ", "kp submit-chapter ", "kp recheck-chapter "))]


def verify_candidate_proofs(context: CandidateEvaluationContext, proof: CandidateProofSet) -> None:
    context.verify()
    source = Path(context.private_result_root, "proof-workspace", context.source_relative)
    if (proof.task_id != context.task_id or proof.candidate_version != context.candidate_version
            or proof.input_manifest_sha256 != context.input_manifest_sha256
            or not _v_is_file(source) or file_digest(source) != proof.source_sha256):
        raise ValueError("候选证明跨版本或正文被修改")
    if set(proof.reports.values()) != set(proof.report_bindings):
        raise ValueError("候选报告清单缺少版本绑定")
    for relative, sha in proof.private_input_bindings.items():
        path = Path(context.private_result_root, "proof-workspace", relative).resolve()
        if (not path.is_relative_to(Path(context.private_result_root).resolve())
                or not _v_is_file(path) or file_digest(path) != sha):
            raise ValueError("私有校验输入被修改；必须创建新候选版本")
    for path, sha in proof.report_bindings.items():
        resolved = Path(path).resolve()
        if (not resolved.is_relative_to(Path(context.private_result_root).resolve())
                or not _v_is_file(resolved) or file_digest(resolved) != sha):
            raise ValueError("候选证明报告被修改或越界")


def _private_input_bindings(context: CandidateEvaluationContext, proof: CandidateProofSet) -> dict[str, str]:
    root = Path(context.private_result_root) / "proof-workspace"
    derived = {Path(p).resolve() for p in proof.reports.values()}
    derived.add((root / "runs/editorial/book-reading-index.json").resolve())
    return {relative: file_digest(root / relative) for relative in context.input_bindings
            if (root / relative).resolve() not in derived}


def record_candidate_structure_review(context: CandidateEvaluationContext, submission: dict):
    from knowledge_product_workflow.chapter_compliance import check_chapter
    from knowledge_product_workflow.developmental import (
        StructureReviewInput,
        record_developmental_check,
        record_structure_review,
    )

    context.verify()
    original = CandidateProofSet.model_validate_json(_v_read_text(Path(context.private_result_root, "proofs.json"), encoding="utf-8"))
    verify_candidate_proofs(context, original)
    workspace = Workspace(root=Path(context.private_result_root) / "proof-workspace")
    source = workspace.root / context.source_relative
    record_structure_review(workspace, context.chapter_number, source,
                            StructureReviewInput.model_validate(submission))
    record_developmental_check(workspace, context.chapter_number, source)
    checks = check_chapter(workspace, context.chapter_number, source)
    path = Path(context.private_result_root) / "proofs.json"
    result = CandidateProofSet.model_validate_json(_v_read_text(path, encoding="utf-8"))
    result.checks = checks["checks"]
    result.required_missing = _missing_proofs(checks)
    from knowledge_product_workflow.production_dependencies import undeclared_candidate_dependencies
    result.required_missing.extend(undeclared_candidate_dependencies(workspace, context.chapter_number, _v_read_text(source, encoding="utf-8")))
    for label, relative in (("structure", f"runs/editorial/chapter-{context.chapter_number:02d}-structure-review.json"),
                            ("developmental", f"runs/editorial/chapter-{context.chapter_number:02d}-developmental-report.json")):
        result.reports[label] = str(workspace.root / relative)
    result.report_bindings = {p: file_digest(Path(p)) for p in result.reports.values()}
    result.private_input_bindings = _private_input_bindings(context, result)
    context.verify()
    _v_write_text(path, result.model_dump_json(indent=2), encoding="utf-8")
    return result
