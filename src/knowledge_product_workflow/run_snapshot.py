"""当前控制面与交付证据的派生报告，不保存第二套工作流状态。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from knowledge_product_workflow.identity import runtime_identity
from knowledge_product_workflow.runtime import _read_all_orchestration_work_orders, inspect_workflow
from knowledge_product_workflow.verification_context import (
    atomic_replace as _v_atomic_replace,
)
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workspace import Workspace


def _cli_paths(workspace: Workspace) -> list[Path]:
    roots=[workspace.root]
    if workspace.root.parent.name=="output":
        roots.append(workspace.root.parent.parent)
    return [root/name for root in roots for name in ("commands.jsonl","execution-log.jsonl")]


def _files(workspace: Workspace) -> dict[str, str]:
    root = workspace.root
    paths = list((root / "planning").glob("*")) + list((root / "chapters").glob("*"))
    for relative in ("runs/orchestration", "runs/editorial", "runs/diagrams"):
        paths += list((root / relative).rglob("*.json"))
    for pattern in ("*.md", "*.qmd", "*.pdf", "*.json", "*.yaml", "*.tex", "*.lua"):
        paths += list((root / "delivery").glob(pattern))
    paths += list((root / "assets/figures").rglob("*"))
    paths += [root / "runs" / name for name in ("events.jsonl", "request-contract.yaml", "visual-audit-report.json", "pdf-visual-review.json", "pdf-preview/preview-manifest.json")]
    paths+=list((root/"runs/visual-audit-pages").rglob("*.png"))
    files={path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
           for path in sorted(set(paths)) if _v_is_file(path) and path.resolve().is_relative_to(root.resolve())}
    for path in _cli_paths(workspace):
        if _v_is_file(path):
            name=path.relative_to(root).as_posix() if path.is_relative_to(root) else "observation-source:"+str(path.resolve())
            files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _signature(files: dict) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def _effects(workspace: Workspace, status) -> dict:
    effects = {operation: {"effect_state": "unknown", "evidence": None}
               for operation in ("validate", "render-pdf", "update-publication")}
    for path, order in _read_all_orchestration_work_orders(workspace):
        proof = order.completion_proof
        if proof is None or proof.transition not in effects or not proof.output_manifest:
            continue
        valid = True
        for entry in [*order.inputs, *proof.output_manifest]:
            actual = (workspace.root / entry.path).resolve()
            if (not actual.is_relative_to(workspace.root.resolve()) or getattr(entry, "selector", None)
                    or not _v_is_file(actual) or hashlib.sha256(actual.read_bytes()).hexdigest() != entry.sha256):
                valid = False
                break
        if valid:
            effects[proof.transition] = {"effect_state": "applied", "evidence": path.relative_to(workspace.root).as_posix()}
    # Verified downstream gates are an alternative when the final receipt was
    # interrupted. Presence of a PDF or an old report alone proves nothing.
    if status.macro_phase.value == "delivery":
        effects["validate"] = {"effect_state": "applied", "evidence": "current validated workflow gate"}
    if status.stage in {"pdf_preview_repair_pending", "pdf_visual_audit_pending", "pdf_visual_repair_pending", "pdf_manual_review_pending",
                        "delivery_validation_pending", "delivery_quality_blocked", "publish_pending", "published"}:
        effects["render-pdf"] = {"effect_state": "applied", "evidence": "current rendered PDF workflow gate"}
    if status.stage == "brief_missing" and not _v_exists(workspace.root / "delivery/book.pdf"):
        effects = {key: {"effect_state": "not_applied", "evidence": "new workspace; no production artifacts"} for key in effects}
    return effects


def _raw_rows(path: Path) -> list[tuple[int, dict]]:
    if not _v_is_file(path):
        return []
    rows=[]
    for number,line in enumerate(path.read_text(encoding="utf-8",errors="replace").splitlines(),1):
        if not line.strip():
            continue
        try:
            value=json.loads(line)
            if not isinstance(value,dict):
                raise TypeError("not an object")
        except (TypeError,ValueError):
            value={"event_type":"observation_parse_error"}
        rows.append((number,value))
    return rows


def _workflow_facts(workspace: Workspace) -> dict:
    """原始控制事件与当前工件分别列证据；声明不升级为视觉实看或校验通过。"""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    from knowledge_product_workflow.quality import QualityReport, quality_report_validity
    from knowledge_product_workflow.visual_audit import current_visual_audit
    events=_raw_rows(workspace.root/"runs/events.jsonl")
    failures=[];renders=[];delivery_events=[]
    for line,row in events:
        kind=row.get("event_type");payload=row.get("payload",{})
        if not isinstance(payload,dict):
            continue
        source={"source":"runs/events.jsonl","line":line,"event_id":row.get("event_id"),"at":row.get("at")}
        if kind in ("orchestration_work_order_failed","pdf_render_failed"):
            failures.append({**source,"transition":payload.get("transition","render-pdf"),
                             "reason":payload.get("reason"),"work_order_id":payload.get("work_order_id")})
        elif kind=="pdf_rendered":
            renders.append({**source,"sha256":payload.get("sha256"),"page_count":payload.get("page_count"),
                            "evidence_state":"recorded_control_event"})
        elif kind=="delivery_validation_completed":
            delivery_events.append({**source,"report_id":payload.get("report_id")})
    pdf=workspace.root/"delivery/book.pdf";pdf_sha=None;page_count=None
    if _v_is_file(pdf):
        pdf_sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
        try:
            page_count=len(PdfReader(pdf).pages)
        except (OSError,ValueError,PdfReadError):
            pass
    bound=bool(renders and page_count and renders[-1]["sha256"]==pdf_sha and renders[-1]["page_count"]==page_count)
    calls=[];seen={}
    for path in _cli_paths(workspace):
        for line,row in _raw_rows(path):
            args=row.get("args",[])
            if not isinstance(args,list) or not args or args[0]!="validate-delivery":
                continue
            code=row.get("exit_code") if path.name=="commands.jsonl" else row.get("exit")
            canonical={k:v for k,v in row.items() if k not in ("exit_code","exit")};canonical["exit_code"]=code
            key=json.dumps(canonical,sort_keys=True);sources=seen.setdefault(key,{})
            occurrence=sources.get(str(path),0)+1;sources[str(path)]=occurrence
            if any(n>=occurrence for p,n in sources.items() if p!=str(path)):
                continue
            calls.append({"source":str(path),"line":line,"command":"validate-delivery","exit_code":code,
                          "effect_state":"rejected" if type(code) is int and code!=0 else "not_proved_by_exit_alone"})
    report_path=workspace.root/"delivery/qa-report.json";report_state="unknown";report_id=None
    if _v_is_file(report_path):
        try:
            report=QualityReport.model_validate_json(report_path.read_bytes())
            report_id=report.report_id;validity=quality_report_validity(workspace,report,"delivery").value
            linked=any(row["report_id"]==report_id for row in delivery_events)
            report_state=("passed" if report.publishable else "blocked") if validity=="current" and linked else validity
            if validity=="current" and not linked:
                report_state="current_without_event_binding"
        except (OSError,ValueError):
            report_state="invalid"
    visual_path=workspace.root/"runs/visual-audit-report.json";raw_visual={}
    if _v_is_file(visual_path):
        try:
            raw_visual=json.loads(visual_path.read_bytes())
            if not isinstance(raw_visual,dict):
                raw_visual={}
        except (OSError,ValueError):
            pass
    visual=current_visual_audit(workspace)
    observations=raw_visual.get("observations",[])
    if not isinstance(observations,list):
        observations=[]
    declared=sorted({row.get("page_number") for row in observations if isinstance(row,dict) and type(row.get("page_number")) is int})
    total=page_count if page_count is not None else raw_visual.get("page_count")
    # Existing observation records have declarations and input binding, but no independent viewer receipt.
    # Report that limitation explicitly instead of treating a 'count' or 'passed' string as actual viewing.
    coverage={"binding_state":"current" if visual is not None else "not_current",
              "reported_page_count":total,"sampled_pages":raw_visual.get("sampled_pages",[]),
              "declared_observed_pages":declared,"verified_viewed_pages":[],
              "uncovered_pages":list(range(1,total+1)) if type(total) is int and total>0 else [],
              "read_evidence_state":"not_available_in_workspace_records",
              "assessment_source":"declared_observations","evidence":"runs/visual-audit-report.json" if raw_visual else None}
    return {"failed_transitions":failures,"rendering":{"recorded_success_count":len(renders),"events":renders,
            "current_pdf_sha256":pdf_sha,"page_count":page_count,"current_pdf_event_bound":bound},
            "delivery_validation":{"calls":calls,"rejected_call_count":sum(c["effect_state"]=="rejected" for c in calls),
                                   "completion_events":delivery_events,"current_report_state":report_state,"report_id":report_id},
            "visual_coverage":coverage,"final_human_approval":"pending_unless_actual_bound_user_receipt_verified"}


def write_run_snapshot(workspace: Workspace, *, host: str | None = None, model: str | None = None) -> tuple[Path, Path]:
    status = inspect_workflow(workspace)
    files = _files(workspace)
    snapshot = {"schema_version": 2, "generated_at": datetime.now().astimezone().isoformat(),
                "stage": status.stage, "macro_phase": status.macro_phase.value,
                "identity": runtime_identity(host, model), "artifacts": files, "input_signature": _signature(files),
                "operations": _effects(workspace, status), "blocked_by": status.blocked_by,
                "workflow_facts": _workflow_facts(workspace),
                "next_actions": [action.model_dump(mode="json") for action in status.next_actions],
                "final_user_approval": "see current bound pdf-visual-review; never inferred from file presence",
                "text_budget": "removed", "account_usage": None}
    folder = workspace.root / "runs"
    json_path, markdown_path = folder / "run-snapshot.json", folder / "run-snapshot.md"
    for path, text in ((json_path, json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"),
                       (markdown_path, f"# 当前运行快照\n\n生成时间：{snapshot['generated_at']}\n\n当前阶段：{status.stage}\n\n"
                        f"实际源码版本：{snapshot['identity']['source_version']}；安装元数据：{snapshot['identity']['distribution_version']}。\n\n"
                        "文档已生成不等于验收通过；最终本人判断只认当前绑定的真实回执。文本预算已取消，账户实耗未知。\n\n"
                        f"失败transition：{len(snapshot['workflow_facts']['failed_transitions'])}；成功渲染事件：{snapshot['workflow_facts']['rendering']['recorded_success_count']}；"
                        f"当前PDF事件绑定：{snapshot['workflow_facts']['rendering']['current_pdf_event_bound']}。\n\n"
                        f"交付校验拒绝调用：{snapshot['workflow_facts']['delivery_validation']['rejected_call_count']}；当前绑定报告：{snapshot['workflow_facts']['delivery_validation']['current_report_state']}。\n\n"
                        "视觉观察声明与真实读取证据分列；当前工作区记录缺少独立查看回执，未覆盖页保持待核，不以观察count签通过。\n\n"
                        "阻断/待办：\n\n" + "\n".join("- " + item for item in status.blocked_by) +
                        "\n\n当前工件：\n\n" + "\n".join(f"- `{name}`：`{digest}`" for name, digest in files.items()) + "\n")):
        temporary = path.with_suffix(path.suffix + ".tmp")
        _v_write_text(temporary, text, encoding="utf-8")
        _v_atomic_replace(temporary, path)
    return json_path, markdown_path


def current_run_snapshot(workspace: Workspace) -> dict | None:
    try:
        data = json.loads((workspace.root / "runs/run-snapshot.json").read_text(encoding="utf-8"))
        return data if data.get("schema_version")==2 and data["input_signature"] == _signature(_files(workspace)) and data["stage"] == inspect_workflow(workspace).stage else None
    except (OSError, KeyError, ValueError, TypeError):
        return None
