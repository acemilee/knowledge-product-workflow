"""展示候选与当前资产的受控切换，保留作者租约与旧合格资产。"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from .workspace_scratch import workspace_scratch


def candidate_root(workspace, call_id: str) -> Path:
    if re.fullmatch(r"image-call-[a-z0-9]+", call_id) is None:
        raise ValueError("候选call_id格式错误")
    return workspace.root / "runs/illustrations/candidates" / call_id


def propose_presentation(workspace, chapter: int, diagram_id: str, presentation):
    from knowledge_product_workflow.diagrams import DiagramInput
    from knowledge_product_workflow.illustration_palette import (
        load_category_colors,
        resolve_diagram_palette,
    )
    from knowledge_product_workflow.illustrations import current_profile
    from knowledge_product_workflow.presentation import _atomic_write
    path = workspace.root / "runs/diagrams" / f"{diagram_id}.input.json"
    source = DiagramInput.model_validate_json(path.read_text(encoding="utf-8"))
    profile = current_profile(workspace)
    if profile.illustration_style_revision != 2:
        raise ValueError("图级展示更新须在预览确认风格2后执行")
    manifest = json.loads((workspace.root / "runs/diagrams" / f"{diagram_id}.json").read_text(encoding="utf-8"))
    if manifest["chapter_number"] != chapter:
        raise ValueError("禁止跨章修订图展示")
    target = DiagramInput.model_validate(source.model_copy(update={"presentation": presentation}).model_dump())
    resolve_diagram_palette(profile, presentation, load_category_colors(workspace))
    output = workspace.root / "runs/diagrams" / f"{diagram_id}.presentation-candidate.json"
    _atomic_write(output, target.model_dump_json(indent=2) + "\n")
    workspace.append_event("diagram_presentation_proposed", {"diagram_id":diagram_id,
        "chapter_number":chapter,"base_sha256":sha256(path.read_bytes()).hexdigest(),
        "target_sha256":sha256(output.read_bytes()).hexdigest()})
    return output


def is_pending_presentation(workspace, semantic) -> bool:
    path = workspace.root / "runs/diagrams" / f"{semantic.diagram_id}.presentation-candidate.json"
    current = workspace.root / "runs/diagrams" / f"{semantic.diagram_id}.input.json"
    if not path.is_file():
        return False
    from knowledge_product_workflow.diagrams import DiagramInput
    if DiagramInput.model_validate_json(path.read_text(encoding="utf-8")) != semantic:
        return False
    events = [json.loads(x) for x in (workspace.root / "runs/events.jsonl").read_text(encoding="utf-8").splitlines() if x]
    events = [e for e in events if e.get("event_type")=="diagram_presentation_proposed" and e["payload"]["diagram_id"]==semantic.diagram_id]
    return bool(events and events[-1]["payload"]["base_sha256"]==sha256(current.read_bytes()).hexdigest()
        and events[-1]["payload"]["target_sha256"]==sha256(path.read_bytes()).hexdigest())


def stage_candidate(workspace, semantic, call_id, prepared, *, chapter_source=None):
    root = candidate_root(workspace, call_id)
    if not root.resolve().is_relative_to(workspace.root.resolve()):
        raise ValueError("候选目录越出工作区")
    root.mkdir(parents=True, exist_ok=True)
    prior_index = root / "index.json"
    if prior_index.is_file():
        history = root / "history" / uuid4().hex
        history.mkdir(parents=True)
        for previous in root.iterdir():
            if previous.is_file():
                (history / previous.name).write_bytes(previous.read_bytes())
    files = []
    for source, destination in prepared:
        if (not source.resolve().is_relative_to(workspace.root.resolve())
                or not destination.resolve().is_relative_to(workspace.root.resolve())):
            raise ValueError("候选源或目标越出工作区")
        target = root / source.name
        target.write_bytes(source.read_bytes())
        files.append({"name":target.name,"destination":destination.relative_to(workspace.root).as_posix(),
                      "sha256":sha256(target.read_bytes()).hexdigest()})
    pending = workspace.root / "runs/diagrams" / f"{semantic.diagram_id}.presentation-candidate.json"
    current = workspace.root / "runs/diagrams" / f"{semantic.diagram_id}.input.json"
    (root / "input.json").write_bytes((pending if pending.is_file() else current).read_bytes())
    staged_manifest = root / f"{semantic.diagram_id}.json"
    manifest = json.loads((staged_manifest if staged_manifest.is_file() else workspace.root / "runs/diagrams" / f"{semantic.diagram_id}.json").read_text(encoding="utf-8"))
    index = {"diagram_id":semantic.diagram_id,"call_id":call_id,"files":files,
             "chapter_number": manifest["chapter_number"],
             "base_sha256":sha256((workspace.root/"runs/diagrams"/f"{semantic.diagram_id}.input.json").read_bytes()).hexdigest(),
             "input_sha256":sha256((root/"input.json").read_bytes()).hexdigest()}
    if chapter_source is not None:
        index["chapter_source"] = chapter_source.relative_to(workspace.root).as_posix()
    path = root / "index.json"
    path.write_text(json.dumps(index,ensure_ascii=False,indent=2),encoding="utf-8")
    workspace.append_event("illustration_candidate_staged", {"call_id":call_id,"sha256":sha256(path.read_bytes()).hexdigest()})


def activate_candidate(workspace, diagram_id, call_id, *, chapter_number=None,
                       confirm_mixed_candidate=False, approved_by=None, user_response=None, review_input=None):
    from knowledge_product_workflow.diagrams import DiagramInput, _atomic_replace_many
    from knowledge_product_workflow.illustration_palette import register_category_colors
    from knowledge_product_workflow.illustrations import current_profile, read_asset_style
    root = candidate_root(workspace,call_id)
    index_path = root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    events=[json.loads(x) for x in (workspace.root/"runs/events.jsonl").read_text(encoding="utf-8").splitlines() if x]
    seals=[e for e in events if e.get("event_type")=="illustration_candidate_staged" and e["payload"]["call_id"]==call_id]
    if not seals or seals[-1]["payload"]["sha256"]!=sha256(index_path.read_bytes()).hexdigest() or index["diagram_id"]!=diagram_id:
        raise ValueError("候选封印不匹配")
    if chapter_number is not None and index.get("chapter_number") != chapter_number:
        raise ValueError("禁止跨章激活候选")
    input_path=workspace.root/"runs/diagrams"/f"{diagram_id}.input.json"
    if sha256(input_path.read_bytes()).hexdigest()!=index["base_sha256"]:
        raise ValueError("候选基础版本已过期")
    proof=read_asset_style(workspace,diagram_id,candidate_call_id=call_id)
    if proof is None or proof["numeric_blockers"] or any(o["assessment"]=="failed" for o in proof["input"]["observations"].values()):
        raise ValueError("候选未检查或failed，不能覆盖当前资产")
    manifest = json.loads((root / f"{diagram_id}.json").read_text(encoding="utf-8"))
    mixed = manifest.get("renderer") == "host-image+svg"
    if mixed:
        from knowledge_product_workflow.diagrams import DiagramManifest, _reader_body, _sha256_text
        from knowledge_product_workflow.experimental_composition import (
            require_current_material_composition,
        )
        material_manifest = DiagramManifest.model_validate(manifest)
        semantic_candidate = DiagramInput.model_validate_json((root / "input.json").read_bytes())
        require_current_material_composition(workspace, material_manifest, semantic_candidate)
        chapter_source = (workspace.root / index.get("chapter_source", "")).resolve()
        if (not chapter_source.is_relative_to(workspace.root.resolve()) or not chapter_source.is_file()
                or _sha256_text(_reader_body(chapter_source)) != material_manifest.chapter_body_sha256):
            raise ValueError("混合候选绑定的当前正文已变化，不能激活")
        if proof["numeric_reviews"] or any(o["assessment"] != "passed" for o in proof["input"]["observations"].values()):
            raise ValueError("混合候选仍有uncertain／待决项，不能激活")
        if (not confirm_mixed_candidate or not approved_by or not approved_by.strip()
                or not user_response or not user_response.strip()):
            raise ValueError("混合候选须当前真实本人响应明确认可；缺少认可时保持pending，不能代签")
        from knowledge_product_workflow.illustrations import style_review_fingerprint
        if review_input != style_review_fingerprint(workspace, diagram_id, call_id):
            raise ValueError("本人响应未绑定当前review-input；重新展示当前候选，不能套用旧认可")
        prior_response = root / "human-response.json"
        if (prior_response.is_file() and json.loads(prior_response.read_bytes()).get("candidate_index_sha256")
                == sha256(index_path.read_bytes()).hexdigest()):
            raise ValueError("当前候选本人响应已消费，不能重复激活")
    pairs=[]
    for item in index["files"]:
        source=root/item["name"]; destination=workspace.root/item["destination"]
        if not source.resolve().is_relative_to(root.resolve()) or not destination.resolve().is_relative_to(workspace.root.resolve()) or sha256(source.read_bytes()).hexdigest()!=item["sha256"]:
            raise ValueError("候选文件或路径被篡改")
        pairs.append((source,destination))
    if sha256((root/"input.json").read_bytes()).hexdigest()!=index["input_sha256"]:
        raise ValueError("候选输入被篡改")
    archive=workspace.root/"runs/illustrations/history"/call_id;archive.mkdir(parents=True,exist_ok=True)
    for _,destination in pairs:
        if destination.is_file():
            (archive/destination.name).write_bytes(destination.read_bytes())
    (archive/"input.json").write_bytes(input_path.read_bytes())
    semantic=DiagramInput.model_validate_json((root/"input.json").read_text(encoding="utf-8"))
    pairs.append((root/"input.json",input_path))
    proof["input"]["candidate_call_id"]=None
    output=workspace.root/"runs/illustrations/asset-checks"/f"{diagram_id}.json"
    converted=root/"activated-style-check.json"
    converted.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    pairs.append((converted,output))
    with workspace_scratch(root=root) as temporary:
        copies=[]
        originals=[]
        for number,(source,destination) in enumerate(pairs):
            copied=Path(temporary)/f"source-{number}"
            copied.write_bytes(source.read_bytes())
            copies.append((copied,destination))
            previous=Path(temporary)/f"previous-{number}"
            if destination.is_file():
                previous.write_bytes(destination.read_bytes())
            originals.append((previous,destination))
        _atomic_replace_many(copies,Path(temporary))
        try:
            register_category_colors(workspace,current_profile(workspace),semantic.presentation)
        except (OSError,ValueError):
            for previous,destination in originals:
                if previous.is_file():
                    previous.replace(destination)
                else:
                    destination.unlink(missing_ok=True)
            workspace.append_event("illustration_candidate_rollback",{"diagram_id":diagram_id,"call_id":call_id})
            raise
    workspace.append_event("illustration_style_checked",{"diagram_id":diagram_id,"sha256":sha256(output.read_bytes()).hexdigest(),"candidate_call_id":None})
    workspace.append_event("illustration_candidate_activated",{"diagram_id":diagram_id,"call_id":call_id})
    if mixed:
        receipt = {"candidate_index_sha256": sha256(index_path.read_bytes()).hexdigest(),
                   "candidate_png_sha256": manifest["png_sha256"], "approved_by": approved_by.strip(),
                   "response_sha256": sha256(user_response.encode()).hexdigest(),
                   "review_input": review_input,
                   "source_origin": manifest.get("material_origin", "unknown"),
                   "origin_verification": "human response and original native evidence require external audit; not inferred by KP"}
        approval_path = root / "human-response.json"
        approval_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        workspace.append_event("mixed_illustration_human_response_bound", {"call_id": call_id,
                              "sha256": sha256(approval_path.read_bytes()).hexdigest()})
    return output


def presentation_update_needed(workspace) -> bool:
    from knowledge_product_workflow.illustration_palette import (
        load_category_colors,
        resolve_diagram_palette,
    )
    from knowledge_product_workflow.illustrations import (
        current_profile,
        review_binding,
        review_semantic,
    )
    profile = current_profile(workspace)
    if profile.illustration_style_revision != 2:
        return False
    for path in (workspace.root / "runs/diagrams").glob("*.json"):
        if path.name.endswith((".input.json", ".spec.json", ".presentation-candidate.json")):
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        semantic = review_semantic(workspace, manifest["diagram_id"])
        try:
            resolve_diagram_palette(profile, semantic.presentation, load_category_colors(workspace))
            if manifest.get("style_binding") and manifest["style_binding"] != review_binding(workspace, manifest["diagram_id"]):
                return True
        except ValueError:
            return True
    return False
