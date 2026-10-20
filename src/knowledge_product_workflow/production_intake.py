"""Single-writer candidate acceptance and recoverable publication of chapter files."""

from __future__ import annotations

import json
import re
import shutil
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

from knowledge_product_workflow.atomic_files import replace_file
from knowledge_product_workflow.production_context import (
    CandidateEvaluationContext,
    CandidateProofSet,
    _missing_proofs,
    verify_candidate_proofs,
)
from knowledge_product_workflow.production_dependencies import binding_digest, file_digest
from knowledge_product_workflow.production_tasks import (
    production_lock,
    resume_production_run,
    save_run,
)
from knowledge_product_workflow.verification_context import (
    current_verification as _v_current_context,
)
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import (
    glob_files as _v_glob_files,
)
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    read_bytes as _v_read_bytes,
)
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.verification_context import (
    write_bytes as _v_write_bytes,
)
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workspace import Workspace


class ProductionConsistencyError(ValueError):
    """Unfinished or concurrently changed controlled chapter transaction."""


def pending_production_commits(workspace: Workspace) -> list[str]:
    """Read-only admission guard shared by status, snapshots, validation and assembly."""
    directory = workspace.root / "runs/production/commits"
    pending = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(_v_read_text(path, encoding="utf-8"))
            if record.get("state") != "completed" and not record.get("diagnostic_only"):
                pending.append(path.stem)
        except (OSError, ValueError):
            pending.append(path.stem)
    return pending


def require_consistent_production(workspace: Workspace, *, chapters: set[int] | None = None) -> None:
    from knowledge_product_workflow.workspace_transactions import require_transaction_consistency
    try:
        require_transaction_consistency(workspace, chapters=chapters)
    except ValueError as error:
        raise ProductionConsistencyError(str(error)) from error
    pending = pending_production_commits(workspace)
    if chapters is not None:
        pending = [identity for identity in pending if _pending_intersects(workspace, identity, chapters)]
    if pending:
        raise ProductionConsistencyError("章节正在提交或待恢复，禁止读取半提交版本：" + ", ".join(pending))


def production_read_token(workspace: Workspace, *, chapters: set[int] | None = None) -> dict[str, str]:
    require_consistent_production(workspace, chapters=chapters)
    return {p.relative_to(workspace.root).as_posix(): file_digest(p) for folder in ("runs/production/commits", "runs/orchestration/transactions") for p in _v_glob_files(workspace.root / folder, "*.json")}


_active_production_read = ContextVar("kp_active_production_read", default=None)


def _read_owner():
    import asyncio
    import threading

    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


def _has_covering_production_read(workspace: Workspace, chapters: set[int] | None) -> bool:
    """Internal reuse is scoped to the live owner and unchanged verification phase."""
    guard = _active_production_read.get()
    if guard is None or not guard["active"] or guard["owner"] != _read_owner():
        return False
    context = guard["context"]
    if (context is None or context.closed or _v_current_context(workspace) is not context
            or workspace.root.absolute() != guard["root"] or context.phase != guard["phase"]):
        return False
    # The parent's lexical scope is not evidence that its lock is still held.
    # Preserve the original reader's live owner check before borrowing it.
    from knowledge_product_workflow.production_tasks import controller_lock_owned

    if _v_exists(workspace.root / "runs/production") and not controller_lock_owned(workspace):
        return False
    allowed = guard["chapters"]
    return allowed is None or (chapters is not None and set(chapters).issubset(allowed))


@contextmanager
def consistent_production_read(workspace: Workspace, *, chapters: set[int] | None = None):
    from contextlib import nullcontext

    from knowledge_product_workflow.production_tasks import controller_lock_owned
    lock = production_lock(workspace) if _v_exists(workspace.root / "runs/production") and not controller_lock_owned(workspace) else nullcontext()
    with ExitStack() as stack:
        try:
            stack.enter_context(lock)
        except ValueError as error:
            raise ProductionConsistencyError(str(error)) from error
        before = production_read_token(workspace, chapters=chapters)
        context = _v_current_context(workspace)
        guard = {"root": workspace.root.absolute(), "context": context,
                 "phase": context.phase if context is not None else None,
                 "chapters": frozenset(chapters) if chapters is not None else None,
                 "owner": _read_owner(), "active": True}
        token = _active_production_read.set(guard)
        try:
            yield
            if production_read_token(workspace, chapters=chapters) != before:
                raise ProductionConsistencyError("读取期间正式版本发生提交；拒绝混合版本结果")
        finally:
            guard["active"] = False  # Copied contexts must not extend the guard's lifetime.
            _active_production_read.reset(token)


def _events_for_recovery(workspace: Workspace, commit_id: str) -> list[dict]:
    events = workspace.root / "runs/events.jsonl"
    raw = _v_read_bytes(events) if _v_is_file(events) else b""
    rows, valid = [], b""
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        try:
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("事件不是对象")
                rows.append(row)
        except ValueError as error:
            if index != len(lines)-1:
                raise ValueError("事件中段损坏，停止恢复并保留日志") from error
            archive = workspace.root / "runs/production/event-recovery"
            _v_mkdir(archive,parents=True,exist_ok=True)
            _v_write_bytes(archive / f"{commit_id}-{uuid4().hex}.original.bin", raw)
            break
        valid += line
    if valid and not valid.endswith(b"\n"):
        valid += b"\n"
    if valid != raw:
        if _v_is_file(events) and _v_read_bytes(events) != raw:
            raise ValueError("恢复核验期间事件日志变化，禁止覆盖")
        temporary = events.with_suffix(".recovery.tmp")
        _v_write_bytes(temporary, valid)
        replace_file(temporary, events)
    return rows


def _relative_target(workspace: Workspace, relative: str, chapter: int) -> Path:
    path = (workspace.root / relative).resolve()
    allowed = {
        f"chapters/chapter-{chapter:02d}.md",
        f"chapters/chapter-{chapter:02d}-content-plan.yaml",
        f"runs/chapter-compliance/chapter-{chapter:02d}.json",
        f"runs/editorial/chapter-{chapter:02d}-structure-review.json",
        f"runs/editorial/chapter-{chapter:02d}-developmental-report.json",
        f"runs/humanize/chapter-{chapter:02d}.json",
    }
    if re.fullmatch(r"runs/review-batches/review-batch-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}/(?:batch\.json|conversion\.json|reviews/chapter-"+f"{chapter:02d}"+r"-review\.json)",relative):
        allowed.add(relative)
    if not path.is_relative_to(workspace.root.resolve()) or relative not in allowed:
        raise ValueError("正式接收路径越章或越界")
    return path


def recover_production_commit(workspace: Workspace, commit_id: str) -> dict:
    if not commit_id.replace("-", "").isalnum():
        raise ValueError("提交ID越界")
    with production_lock(workspace):
        record_path = workspace.root / "runs/production/commits" / f"{commit_id}.json"
        record = json.loads(_v_read_text(record_path, encoding="utf-8"))
        if record.get("workspace_transaction_id"):
            from knowledge_product_workflow.workspace_transactions import (
                recover_workspace_transaction,
            )
            recover_workspace_transaction(workspace, record["workspace_transaction_id"])
            return json.loads(_v_read_text(record_path, encoding="utf-8"))
        if record["state"] == "completed":
            if any(not _v_is_file(_relative_target(workspace, p, record["chapter_number"]))
                   or file_digest(_relative_target(workspace, p, record["chapter_number"])) != v["sha256"]
                   for p, v in record["outputs"].items()):
                raise ValueError("已完成提交的正式文件发生变化；不得用恢复覆盖新版本")
            return record
        for key, expected in record.get("input_bindings", {}).items():
            current_output = record["outputs"].get(key)
            if current_output:
                target = _relative_target(workspace, key, record["chapter_number"])
                if _v_is_file(target) and file_digest(target) == current_output["sha256"]:
                    continue  # A previously recovered file from this same journal.
            if binding_digest(workspace, key) != expected:
                raise ValueError("恢复时实际依赖已变化；保留待恢复日志，禁止旧候选完成")
        for relative, item in record["outputs"].items():
            target = _relative_target(workspace, relative, record["chapter_number"])
            if _v_is_file(target) and file_digest(target) == item["sha256"]:
                continue
            staged = (workspace.root / item["staged"]).resolve()
            if (not staged.is_relative_to(workspace.root / "runs/production")
                    or not _v_is_file(staged) or file_digest(staged) != item["sha256"]):
                raise ValueError("提交恢复输入缺失或被篡改；保持待恢复")
            _v_mkdir(target.parent,parents=True,exist_ok=True)
            replace_file(staged, target)
        _finalize_commit_metadata(workspace, record)
        event_id = "production-" + commit_id
        already_recorded = any(row.get("event_id") == event_id for row in _events_for_recovery(workspace, commit_id))
        if not already_recorded:
            workspace.append_event("production_commit_completed", {"commit_id": commit_id,
                "chapter_number": record["chapter_number"]}, event_id=event_id)
        record["state"] = "completed"
        temporary = record_path.with_suffix(".tmp")
        _v_write_text(temporary, json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_file(temporary, record_path)
        return record


def materialize_candidate_submission(private: Workspace, context: CandidateEvaluationContext,
                                     compliance_input: Path) -> Workspace:
    """Use original draft/submission rules on a disposable copy, retaining logical book identity."""
    from knowledge_product_workflow.chapter_compliance import submit_chapter
    from knowledge_product_workflow.frontmatter import (
        parse_markdown_artifact,
        render_markdown_artifact,
    )
    from knowledge_product_workflow.models import ArtifactStatus
    from knowledge_product_workflow.workflows.authoring import (
        amend_chapter_draft,
        create_chapter_draft,
    )

    context.verify()
    expected=Path(context.private_result_root)/"proof-workspace"
    if private.root.resolve()!=expected.resolve() or not compliance_input.resolve().is_relative_to(expected.resolve()):
        raise ValueError("物化输入必须来自同一私有证明空间")
    target=Path(context.private_result_root)/("commit-materialization-"+uuid4().hex[:8])
    shutil.copytree(private.root,target)
    mapping=target/compliance_input.resolve().relative_to(private.root.resolve())
    result=Workspace(root=target)
    chapter=target/f"chapters/chapter-{context.chapter_number:02d}.md"
    if not _v_exists(chapter):
        create_chapter_draft(result,context.chapter_number)
    else:
        metadata,_=parse_markdown_artifact(_v_read_text(chapter, encoding="utf-8"))
        if metadata.status is not ArtifactStatus.IN_PRODUCTION:
            amend_chapter_draft(result,context.chapter_number,"受控候选接收；保留旧版本及原审批门")
    submit_chapter(result,context.chapter_number,target/context.source_relative,mapping)
    metadata,body=parse_markdown_artifact(_v_read_text(chapter, encoding="utf-8"))
    metadata.book_id=Path(context.canonical_root).name
    _v_write_text(chapter, render_markdown_artifact(metadata,body),encoding="utf-8")
    return result


def commit_production_candidate(
    workspace: Workspace, task_id: str, *, run_id: str,
    context: CandidateEvaluationContext, compliance_input: Path, capabilities=None,
) -> dict:
    """Technical core generates outputs privately; only proven live runs may promote them."""
    from knowledge_product_workflow.chapter_compliance import check_chapter

    with production_lock(workspace):
        run = resume_production_run(workspace, run_id, capabilities=capabilities)
        task = run.task(task_id)
        if run.diagnostic_only or not run.capabilities.admits_live_parallelism():
            raise ValueError("诊断／未资格通过结果不能写正式章节或满足发布门")
        from knowledge_product_workflow.production_tasks import production_review_policy
        policy=production_review_policy(workspace)
        if run.review_policy is not None and run.review_policy!=policy:
            raise ValueError("运行审稿政策与当前批准Brief不一致")
        if task.role != ("reviewer" if policy=="independent_review_required" else "writer") or task.state != "acceptance_ready":
            raise ValueError("同一候选必须先完成版本绑定审核")
        if task.chapter_number == 1:
            raise ValueError("样章修订及重新批准必须沿原串行审批链，不能通过并行接收改签")
        writers = [t for t in run.tasks if t.role == "writer"
                   and t.chapter_number == task.chapter_number
                   and t.candidate_version == task.candidate_version]
        if len(writers) != 1 or context.task_id != writers[0].task_id:
            raise ValueError("作者／审稿候选版本混用")
        writer=writers[0]
        if (not writer.native_result_digest or not writer.host_task_id
                or writer.host_parent_id!=capabilities._native_session_id):
            raise ValueError("作者缺本次真实原生结果接收证明")
        context.verify()
        for relative, expected in {**task.input_bindings, **context.live_bindings}.items():
            if binding_digest(workspace, relative) != expected:
                raise ValueError("候选实际依赖已变化；不得改签旧审稿")
        proof = CandidateProofSet.model_validate_json(
            _v_read_text(Path(context.private_result_root, "proofs.json"), encoding="utf-8")
        )
        verify_candidate_proofs(context, proof)
        review_path = proof.reports.get("candidate_review")
        if policy=="independent_review_required":
            if not review_path or proof.diagnostic_only:
                raise ValueError("候选缺真实独立审稿；诊断技术校验不能替代")
            review = json.loads(_v_read_text(Path(review_path), encoding="utf-8"))
            if (review.get("diagnostic_only") or not review.get("acceptance_ready")
                    or review.get("reviewer_task_id") != task_id):
                raise ValueError("独立审稿未通过或不属于当前受控任务")
        if (proof.task_id != context.task_id or proof.candidate_version != context.candidate_version
                or proof.input_manifest_sha256 != context.input_manifest_sha256
                or proof.required_missing):
            raise ValueError("候选技术证明缺失、过期或跨版本")
        private = Workspace(root=Path(context.private_result_root) / "proof-workspace")
        source = private.root / context.source_relative
        if file_digest(source) != proof.source_sha256:
            raise ValueError("审稿后候选正文变化")
        if _missing_proofs(check_chapter(private, task.chapter_number, source)):
            raise ValueError("最终私有源稿仍有必须刷新证明")
        mapping = compliance_input.resolve()
        if not mapping.is_relative_to(private.root) or not _v_is_file(mapping):
            raise ValueError("合规映射必须来自同一私有候选")
        private = materialize_candidate_submission(private,context,mapping)
        outputs = {}
        commit_id = "commit-" + uuid4().hex[:12]
        staged_root = workspace.root / "runs/production" / run_id / "staging" / commit_id
        _v_mkdir(staged_root,parents=True,exist_ok=False)
        prefix = f"chapter-{task.chapter_number:02d}"
        converted=[]
        if policy=="independent_review_required":
            from knowledge_product_workflow.review_batches import materialize_candidate_review_batch
            converted=materialize_candidate_review_batch(private,context,writer,task,commit_id=commit_id,
                capabilities=capabilities)
        for relative in [f"chapters/{prefix}.md", f"chapters/{prefix}-content-plan.yaml",
            f"runs/chapter-compliance/{prefix}.json", f"runs/editorial/{prefix}-structure-review.json",
            f"runs/editorial/{prefix}-developmental-report.json", f"runs/humanize/{prefix}.json",*converted]:
            path = private.root / relative
            if _v_is_file(path):
                _relative_target(workspace, relative, task.chapter_number)
                staged = staged_root / relative
                _v_mkdir(staged.parent,parents=True,exist_ok=True)
                shutil.copyfile(path, staged)
                outputs[relative] = {"staged": staged.relative_to(workspace.root).as_posix(),
                                     "sha256": file_digest(path)}
        record = {"commit_id": commit_id, "state": "prepared", "diagnostic_only": False,
                  "chapter_number": task.chapter_number, "candidate_version": task.candidate_version,
                  "task_id": task_id, "run_id": run_id, "outputs": outputs,
                  "input_bindings": {**task.input_bindings, **context.live_bindings}}
        from knowledge_product_workflow.workspace_transactions import prepare_workspace_transaction
        record["workspace_transaction_id"] = "tx-" + commit_id
        prepare_workspace_transaction(workspace,
            {relative: _v_read_bytes(workspace.root / item["staged"])
             for relative, item in outputs.items()}, kind="production",
            operation_id=record["workspace_transaction_id"], scope_chapters=[task.chapter_number],
            shared_paths=converted, input_bindings=record["input_bindings"],
            metadata={"production_record": record},
            events=[("production_commit_completed", {"commit_id": commit_id,
                      "chapter_number": task.chapter_number})])
        directory = workspace.root / "runs/production/commits"
        _v_mkdir(directory,parents=True,exist_ok=True)
        _v_write_text(directory / f"{commit_id}.json",
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        task.state = "committing"
        save_run(workspace, run)
    return recover_production_commit(workspace, commit_id)


def _finalize_commit_metadata(workspace: Workspace, record: dict) -> None:
    """Recover chapter outputs, export bindings and queue state before the completion marker."""
    if "run_id" not in record:
        return  # Storage-protocol fixture/legacy journal, not a production qualification.
    from knowledge_product_workflow.developmental import reading_units
    from knowledge_product_workflow.frontmatter import parse_markdown_artifact
    from knowledge_product_workflow.production_dependencies import AcceptedExport, payload_digest
    from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
    run = resume_production_run(workspace, record["run_id"])
    task = run.task(record["task_id"])
    if run.diagnostic_only:
        raise ValueError("诊断不能恢复为正式接收")
    _, _, blueprint = require_current_submitted_blueprint(workspace)
    _, body = parse_markdown_artifact(_v_read_text(workspace.root / f"chapters/chapter-{task.chapter_number:02d}.md", encoding="utf-8"))
    units = {u["unit_id"]: u["unit_sha256"] for u in reading_units(body)}
    registry = workspace.root / "runs/production/accepted-exports.json"
    exports = json.loads(_v_read_text(registry, encoding="utf-8")) if _v_exists(registry) else {}
    proofs = {path: item["sha256"] for path, item in record["outputs"].items() if path.startswith("runs/")}
    for chapter in blueprint.chapters:
        if chapter.chapter_number != task.chapter_number:
            continue
        for item in chapter.production_exports or []:
            if item.kind == "result":
                declaration = {**item.model_dump(), "chapter_number": chapter.chapter_number}
                exports[item.export_id] = AcceptedExport(export_id=item.export_id,
                    chapter_number=chapter.chapter_number, section_id=item.section_id,
                    commit_id=record["commit_id"], unit_sha256=units[item.section_id],
                    declaration_sha256=payload_digest(declaration), proof_bindings=proofs,
                    input_bindings={key:value for key,value in record["input_bindings"].items()
                        if key not in record["outputs"]}, state="valid").model_dump()
    temporary = registry.with_suffix(".tmp")
    _v_write_text(temporary, json.dumps(exports, ensure_ascii=False, indent=2), encoding="utf-8")
    replace_file(temporary, registry)
    for item in run.tasks:
        if item.chapter_number == task.chapter_number and item.candidate_version == task.candidate_version:
            item.state = "accepted"
    if task.chapter_number not in run.accepted_chapters:
        run.accepted_chapters.append(task.chapter_number)
    save_run(workspace, run)


def shared_request_handoffs(run) -> list[dict]:
    commands={"source":"research", "assumption":"assumption", "claim":"classify-claim",
        "diagram":"submit-diagram", "terminology":"amend-outline", "case":"amend-outline"}
    return [{**r,"domain_command":commands[r["kind"]],
        "instruction":"沿原命令Schema及当前工单验证执行；同一来源多claim一组一次，禁止并发追加；完成后production-shared-complete核实际工件"}
        for r in run.shared_requests if r["state"]=="pending_domain_validation"]


def complete_shared_request(workspace: Workspace, run_id: str, request_id: str) -> dict:
    """Consume a domain request only after the original controller command produced its exact artifact."""
    from knowledge_product_workflow.production_dependencies import payload_digest, workspace_file
    with production_lock(workspace):
        run=resume_production_run(workspace,run_id)
        request=next((r for r in run.shared_requests if r["request_id"]==request_id),None)
        if request is None:
            raise ValueError("共享请求不属于当前运行")
        if request["state"]=="completed":
            return request
        payload=request["payload"]
        if request["kind"]=="source":
            from knowledge_product_workflow.claims import current_claim_contract_bindings
            from knowledge_product_workflow.source_ledger import SourceLedgerEntry
            claims=set(payload.get("claim_ids",[]))
            bindings=current_claim_contract_bindings(workspace)
            rows=[SourceLedgerEntry.model_validate_json(line) for line in
                _v_read_text(workspace_file(workspace,"research/source-ledger.jsonl"), encoding="utf-8").splitlines() if line.strip()]
            found=any(r.source_id==payload.get("source_id") and str(r.url)==payload.get("url")
                and r.excerpt==payload.get("excerpt") and r.license_note==payload.get("license_note")
                and claims==set(r.claim_ids) and all(r.claim_contract_sha256.get(c)==bindings.get(c) and bindings.get(c) for c in claims) for r in rows)
            relative="research/source-ledger.jsonl"
        else:
            # Other domains retain their original validators/approval gates. Require
            # the exact requested JSON payload as a subset of the actual result.
            relative=payload.get("result_path","")
            expected=payload.get("expected_result")
            allowed={"assumption":"research/assumption-ledger.jsonl", "claim":"research/claim-classifications.jsonl"}
            if request["kind"] in allowed and relative!=allowed[request["kind"]]:
                raise ValueError("共享回执领域路径不符")
            if request["kind"]=="diagram" and not relative.startswith("runs/diagrams/"):
                raise ValueError("图义回执路径不符")
            if request["kind"] in {"terminology","case"}:
                raise ValueError("公共定义变化必须原蓝图修订／批准，并退休受影响任务；不能在共享队列自动批准")
            if not isinstance(expected,dict) or not expected:
                raise ValueError("共享请求缺明确预期领域工件")
            path=workspace_file(workspace,relative)
            rows=[json.loads(line) for line in _v_read_text(path, encoding="utf-8").splitlines() if line.strip()] if relative.endswith("jsonl") else [json.loads(_v_read_text(path, encoding="utf-8"))]
            found=any(all(row.get(k)==v for k,v in expected.items()) for row in rows)
        if not found:
            raise ValueError("未有匹配原领域命令的实际当前回执；请求继续pending")
        request.update(state="completed", domain_artifact=relative, domain_sha256=file_digest(workspace_file(workspace,relative)),
                       completion_binding=payload_digest(payload))
        save_run(workspace,run)
        return request


def _pending_intersects(workspace: Workspace, identity: str, chapters: set[int]) -> bool:
    try:
        record = json.loads(_v_read_text(workspace.root / "runs/production/commits" / f"{identity}.json", encoding="utf-8"))
        return record.get("chapter_number") is None or record["chapter_number"] in chapters
    except (OSError, ValueError):
        return True
