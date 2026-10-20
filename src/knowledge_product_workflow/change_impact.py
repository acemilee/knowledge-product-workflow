"""Versioned semantic projections, separate from immutable byte audit bindings."""

from __future__ import annotations

import hashlib
import json
import re
import types
import unicodedata
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, ConfigDict, Field

from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import glob_files as _v_glob_files
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import (
    load_yaml as _v_load_yaml,
)
from knowledge_product_workflow.verification_context import memoized_read as _memoized_read
from knowledge_product_workflow.verification_context import (
    read_bytes as _v_read_bytes,
)
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.workspace import Workspace


def _normalize(value):
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = _normalize(key)
            if normalized in result:
                raise ValueError("归一化后出现重复键")
            result[normalized] = _normalize(item)
        return result
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def semantic_digest(value: object) -> str:
    encoded = json.dumps(_normalize(value), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(_v_read_bytes(path)).hexdigest()


def binding_policy_revision(workspace: Workspace) -> int:
    path = workspace.root / "runs/orchestration/state.json"
    if not _v_exists(path):
        return 1
    raw = json.loads(_v_read_text(path, encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("绑定政策记录损坏；先恢复原状态")
    revision = raw.get("binding_policy_revision", 1)
    if type(revision) is not int or revision not in (1, 2):
        raise ValueError("未知绑定政策；不能解释为旧书或等价输入")
    return revision


def _model_type(annotation):
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) in (Union, types.UnionType):
        return next((t for a in get_args(annotation) if (t := _model_type(a))), None)
    return None


def reject_unknown_fields(raw: object, model: type[BaseModel]) -> None:
    """Do not let Pydantic's legacy extra-ignore erase a new semantic requirement."""
    if not isinstance(raw, dict):
        raise TypeError("语义输入必须为对象")
    extra = set(raw) - set(model.model_fields)
    if extra:
        raise ValueError("未知字段：" + ", ".join(sorted(extra)))
    for key, value in raw.items():
        annotation = model.model_fields[key].annotation
        nested = _model_type(annotation)
        if nested and value is not None:
            reject_unknown_fields(value, nested)
        elif get_origin(annotation) is list and isinstance(value, list):
            nested = _model_type(get_args(annotation)[0])
            if nested:
                for item in value:
                    reject_unknown_fields(item, nested)


class ChapterSemanticBindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    chapter_number: int = Field(gt=0)
    content: str
    assets: dict[str, str]
    review: str
    shared: str
    dependencies: str
    audit: dict[str, str] = Field(default_factory=dict)

    def semantic_values(self) -> dict:
        return self.model_dump(exclude={"audit"})


def bindings_for_chapter(chapter: dict, *, shared: dict | None = None,
                         brief_content_sha256: str = "", review_policy: dict | None = None,
                         budget: dict | None = None, audit: dict | None = None,
                         dependency_inputs: dict | None = None) -> ChapterSemanticBindings:
    from knowledge_product_workflow.workflows.curriculum import BlueprintChapter

    reject_unknown_fields(chapter, BlueprintChapter)
    value = BlueprintChapter.model_validate(chapter).model_dump(mode="json")
    asset_values = value.pop("illustration_tasks", [])
    illustration_revision = value.pop("illustration_policy_revision", 1)
    dependencies = {key: value.get(key) for key in ("production_dependencies", "production_exports")}
    return ChapterSemanticBindings(chapter_number=value["chapter_number"],
        content=semantic_digest({"chapter": value, "effective_budget": budget}),
        assets={item.get("task_id") or f"none:{item['section_id']}": semantic_digest({
            "task": item, "policy": illustration_revision}) for item in asset_values},
        review=semantic_digest(review_policy or {}),
        shared=semantic_digest({"shared": shared or {}, "brief_content": brief_content_sha256}),
        dependencies=semantic_digest({"declarations": dependencies,
            "inputs": dependency_inputs or {}}), audit=audit or {})


def raw_blueprint(body: str) -> dict:
    for match in re.finditer(r"```yaml\s*\n(.*?)\n```", body, re.DOTALL):
        value = _v_load_yaml(match.group(1))
        if isinstance(value, dict) and isinstance(value.get("blueprint"), dict):
            return value["blueprint"]
    raise ValueError("缺少可核验的结构化蓝图")


@_memoized_read
def _workspace_inputs(workspace: Workspace):
    from knowledge_product_workflow.workflows.curriculum import BlueprintSubmission
    from knowledge_product_workflow.workflows.product_brief import brief_contract_fingerprints

    blueprint = workspace.root / "planning/curriculum-blueprint.md"
    brief = workspace.root / "planning/product-brief.md"
    meta, body = parse_markdown_artifact(_v_read_text(blueprint, encoding="utf-8"))
    brief_meta, brief_body = parse_markdown_artifact(_v_read_text(brief, encoding="utf-8"))
    if meta.book_id != brief_meta.book_id:
        raise ValueError("蓝图与Brief不属于同一书稿")
    raw = raw_blueprint(body)
    reject_unknown_fields(raw, BlueprintSubmission)
    submission = BlueprintSubmission.model_validate(raw)
    return submission, body, brief_body, brief_contract_fingerprints(brief_body), meta.book_id


def _bindings(workspace: Workspace, submission, chapter, brief_body: str, fingerprints: dict,
              blueprint_body: str = "") -> ChapterSemanticBindings:
    from knowledge_product_workflow.workflows.curriculum import ChapterSpec, allocate_budgets
    from knowledge_product_workflow.workflows.product_brief import parse_detail_constraints

    global_fields = submission.model_dump(mode="json", exclude={"chapters", "brief_sha256",
        "brief_content_sha256", "brief_version"})
    state_path = workspace.root / "runs/orchestration/state.json"
    state = json.loads(_v_read_text(state_path, encoding="utf-8")) if _v_is_file(state_path) else {}
    constraints = parse_detail_constraints(brief_body)
    budgets = {b.chapter_number: b for b in allocate_budgets([
        ChapterSpec(chapter_number=c.chapter_number, weight=c.weight,
                    rationale=c.rationale, required_units=c.required_units)
        for c in submission.chapters], constraints)} if constraints is not None else {}
    budget = budgets.get(chapter.chapter_number)
    by_number = {c.chapter_number: c for c in submission.chapters}
    dependency_inputs = {}
    for dependency in chapter.production_dependencies or []:
        source = by_number[dependency.producer_chapter]
        if dependency.kind == "navigation":
            dependency_inputs[str(source.chapter_number)] = {"title": source.title,
                "outline": [s.model_dump(mode="json") for s in source.section_outline]}
        else:
            export = next((e for e in source.production_exports or []
                           if e.export_id == dependency.export_id), None)
            dependency_inputs[dependency.export_id] = export.model_dump(mode="json") if export else None
    return bindings_for_chapter(chapter.model_dump(mode="json"), shared={"fields": global_fields, "case_input_bytes": hashlib.sha256(json.dumps(
            global_fields.get("shared_context"), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()},
        brief_content_sha256=fingerprints["content_contract_sha256"],
        review_policy={key: state.get(key, 1) for key in
            ("reading_structure_revision", "reading_review_rubric_revision", "request_capture_revision")},
        budget=budget.model_dump(mode="json") if isinstance(budget, BaseModel) else budget,
        dependency_inputs=dependency_inputs,
        audit={p: file_sha(workspace.root / p) for p in
            ("planning/curriculum-blueprint.md", "planning/product-brief.md")})


@_memoized_read
def chapter_bindings(workspace: Workspace, chapter_number: int) -> ChapterSemanticBindings:
    submission, body, brief, fingerprints, _ = _workspace_inputs(workspace)
    chapter = next((c for c in submission.chapters if c.chapter_number == chapter_number), None)
    if chapter is None:
        raise ValueError("当前蓝图不存在该章")
    return _bindings(workspace, submission, chapter, brief, fingerprints, body)


def _proof_projection(bindings: ChapterSemanticBindings, domain: str) -> dict:
    values = {key: getattr(bindings, key) for key in ("content", "shared", "dependencies")}
    if domain in {"developmental", "structure", "structure_contract", "independent_review"}:
        values["review"] = bindings.review
    if domain.startswith("diagram:"):
        values["asset"] = bindings.assets.get(domain.split(":", 1)[1])
    if domain == "independent_review":
        values["assets"] = bindings.assets
    return values


def _needs_result_binding(domain: str) -> bool:
    return domain in {"compliance", "developmental", "structure", "independent_review"} or domain.startswith(("result:", "diagram:"))


def declared_result_bindings(workspace: Workspace, number: int, *, section_ids: set[str] | None = None) -> dict:
    """The meaning actually consumed, even when prose omits a chapter-number reference."""
    from knowledge_product_workflow.production_dependencies import binding_digest
    submission, _, _, _, _ = _workspace_inputs(workspace)
    chapter = next(c for c in submission.chapters if c.chapter_number == number)
    result = {}
    for dependency in chapter.production_dependencies or []:
        if dependency.kind != "result" or (section_ids is not None and dependency.section_id not in section_ids):
            continue
        key = "runs/production/accepted-exports.json#export=" + dependency.export_id
        if not _v_is_file(workspace.root / "runs/production/accepted-exports.json"):
            result[dependency.export_id] = semantic_digest({"export_id": dependency.export_id, "state": "missing"})
            continue
        try:
            result[dependency.export_id] = binding_digest(workspace, key)
        except KeyError:
            result[dependency.export_id] = semantic_digest({"export_id": dependency.export_id, "state": "missing"})
    return result


def proof_contract_digest(workspace: Workspace, number: int, domain: str,
                          legacy_digest: str | None = None) -> str:
    if binding_policy_revision(workspace) != 2:
        return legacy_digest or file_sha(workspace.root / f"chapters/chapter-{number:02d}-contract.md")
    if domain == "navigation":
        submission, _, _, _, _ = _workspace_inputs(workspace)
        chapter = next(c for c in submission.chapters if c.chapter_number == number)
        return semantic_digest({"title": chapter.title,
            "outline": [s.model_dump(mode="json") for s in chapter.section_outline]})
    if domain.startswith("result:"):
        section = domain.split(":", 1)[1]
        submission, _, _, _, _ = _workspace_inputs(workspace)
        chapter = next(c for c in submission.chapters if c.chapter_number == number)
        bindings = chapter_bindings(workspace, number)
        projection = _proof_projection(bindings, "independent_review")
        projection["assets"] = {t.task_id or f"none:{t.section_id}":
            bindings.assets[t.task_id or f"none:{t.section_id}"]
            for t in chapter.illustration_tasks if t.section_id == section}
        projection["accepted_results"] = declared_result_bindings(workspace, number, section_ids={section})
        return semantic_digest({"policy": 2, "domain": domain, "inputs": projection})
    projection = _proof_projection(chapter_bindings(workspace, number), domain)
    if _needs_result_binding(domain):
        projection["accepted_results"] = declared_result_bindings(workspace, number)
    return semantic_digest({"policy": 2, "domain": domain, "inputs": projection})


def proof_contract_matches(workspace: Workspace, number: int, reported: str, domain: str,
                           proof_path: Path | None = None) -> bool:
    contract = workspace.root / f"chapters/chapter-{number:02d}-contract.md"
    if reported == proof_contract_digest(workspace, number, domain):
        return True
    result_bound = (binding_policy_revision(workspace) == 2 and _needs_result_binding(domain)
                    and bool(declared_result_bindings(workspace, number)))
    if reported == file_sha(contract) and not result_bound:
        return True
    if result_bound:
        return False  # Raw historic contract bytes never prove which result was consumed.
    if binding_policy_revision(workspace) != 2 or proof_path is None:
        return False
    for path in _v_glob_files(workspace.root / "runs/orchestration/proof-refresh", "*.json"):
        try:
            record = json.loads(_v_read_text(path, encoding="utf-8"))
            tx = json.loads(_v_read_text(workspace.root / "runs/orchestration/transactions" /
                f"{record['operation_id']}.json", encoding="utf-8"))
            relative = path.relative_to(workspace.root).as_posix()
            proof_relative = proof_path.relative_to(workspace.root).as_posix()
            if (tx["state"] == "completed" and tx["outputs"][relative]["sha256"] == file_sha(path)
                    and record["book_id"] == _book_id(workspace)
                    and record["chapter_number"] == number
                    and record["current_contract_sha256"] == file_sha(contract)
                    and record["proofs"].get(proof_relative) == file_sha(proof_path)
                    and reported in {record["old_contract_sha256"], record.get("old_contract_body_sha256")}
                    and domain in record["equivalent_domains"]):
                # Independently recompute equivalence; a JSON flag is never authority.
                old = Path(workspace.root / record["old_contract_path"]).resolve()
                if (not old.is_relative_to(workspace.root.resolve())
                        or file_sha(old) != record["old_contract_sha256"]):
                    continue
                _, old_body = parse_markdown_artifact(_v_read_text(old, encoding="utf-8"))
                from knowledge_product_workflow.workflows.authoring import (
                    parse_chapter_contract_binding,
                )
                binding = parse_chapter_contract_binding(old_body)
                previous = ChapterSemanticBindings.model_validate(binding["semantic_bindings"])
                if _proof_projection(previous, domain) == _proof_projection(chapter_bindings(workspace, number), domain):
                    return True
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return False


def _book_id(workspace: Workspace) -> str:
    return parse_markdown_artifact(_v_read_text(workspace.root / "planning/product-brief.md", encoding="utf-8"))[0].book_id


def refresh_chapter_proofs(workspace: Workspace, chapter_number: int, *, impact_id: str) -> Path:
    """Issue an immutable equivalence receipt; raw proof bytes and reader text stay intact."""
    from knowledge_product_workflow.runtime import authorize_workflow_transition
    from knowledge_product_workflow.workflows.authoring import parse_chapter_contract_binding
    from knowledge_product_workflow.workspace_transactions import (
        commit_workspace_transaction,
        prepare_workspace_transaction,
    )
    authorize_workflow_transition(workspace, "refresh-chapter-proofs")
    if binding_policy_revision(workspace) != 2 or not re.fullmatch(r"impact-[a-f0-9]{64}", impact_id):
        raise ValueError("证明更新需要政策2和程序生成的影响标识")
    from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
    _, _, approved = require_current_submitted_blueprint(workspace)
    impact_path = workspace.root / f"runs/orchestration/change-impacts/{impact_id}.json"
    impact = ChangeImpactPlan.model_validate_json(_v_read_text(impact_path, encoding="utf-8"))
    if (impact.book_id != _book_id(workspace) or impact.impact_id != impact_id
            or impact.proposed_semantic_sha256 != semantic_digest(_submission_values(approved))):
        raise ValueError("影响计划不属于当前批准输入")
    if not any(_completed_output_matches(workspace, impact_path, tx_path)
               for tx_path in (workspace.root / "runs/orchestration/transactions").glob("*.json")):
        raise ValueError("影响计划未由受控事务物化")
    current = chapter_bindings(workspace, chapter_number)
    current_contract = workspace.root / f"chapters/chapter-{chapter_number:02d}-contract.md"
    proofs = {}
    for relative in [f"chapters/chapter-{chapter_number:02d}-content-plan.yaml",
        f"chapters/chapter-{chapter_number:02d}-review.json",
        f"runs/chapter-revisions/chapter-{chapter_number:02d}.jsonl",
        f"runs/chapter-compliance/chapter-{chapter_number:02d}.json",
        f"runs/humanize/chapter-{chapter_number:02d}.json",
        f"runs/editorial/chapter-{chapter_number:02d}-structure-review.json",
        f"runs/editorial/chapter-{chapter_number:02d}-developmental-report.json",
        f"runs/orchestration/content-ready/chapter-{chapter_number:02d}.json"]:
        path = workspace.root / relative
        if _v_is_file(path):
            proofs[relative] = file_sha(path)
    old_contract = None
    previous = None
    for path in sorted((workspace.root / "chapters/history").glob(f"chapter-{chapter_number:02d}-contract.*.md"), reverse=True):
        _, body = parse_markdown_artifact(_v_read_text(path, encoding="utf-8"))
        binding = parse_chapter_contract_binding(body)
        if binding and binding.get("schema_version") == 2:
            old_contract, previous = path, ChapterSemanticBindings.model_validate(binding["semantic_bindings"])
            break
    if previous is None:
        raise ValueError("缺少可核验的原合约快照；不能补造等价证明")
    domains = [domain for domain in ("content_plan", "humanize", "developmental", "structure", "compliance", "independent_review")
               if _proof_projection(previous, domain) == _proof_projection(current, domain)
               and not (_needs_result_binding(domain) and declared_result_bindings(workspace, chapter_number))]
    derived_outputs = {}
    # Reader-authored plan fields remain byte-for-byte values. Only control-plane
    # declarations derived from the already approved blueprint may be rematerialized.
    plan_relative = f"chapters/chapter-{chapter_number:02d}-content-plan.yaml"
    if plan_relative in proofs and "content_plan" in domains:
        from knowledge_product_workflow.content_plan import ContentPlan, _validate_plan_sections
        chapter = next(c for c in approved.chapters if c.chapter_number == chapter_number)
        old_plan = ContentPlan.model_validate(_v_load_yaml(_v_read_text(workspace.root / plan_relative, encoding="utf-8")))
        new_plan = old_plan.model_copy(deep=True)
        new_plan.contract_sha256 = proof_contract_digest(workspace, chapter_number, "content_plan")
        for section in new_plan.sections:
            tasks = [t for t in chapter.illustration_tasks if t.section_id == section.section_id]
            old_identity = {(t.task_id, t.decision, t.production) for t in section.illustration_tasks}
            if old_identity != {(t.task_id, t.decision, t.production) for t in tasks}:
                raise ValueError("计划的读者任务或制作准入已变化；需定向修订计划，不能伪写等价")
            section.illustration_tasks = tasks
        _validate_plan_sections(new_plan, chapter)
        content = yaml.safe_dump(new_plan.model_dump(mode="json"), allow_unicode=True, sort_keys=False).encode("utf-8")
        if file_sha(workspace.root / plan_relative) != hashlib.sha256(content).hexdigest():
            archive = f"runs/content-plan-history/chapter-{chapter_number:02d}-{proofs[plan_relative]}.yaml"
            derived_outputs[archive] = _v_read_bytes(workspace.root / plan_relative)
            derived_outputs[plan_relative] = content
    input_proofs = dict(proofs)
    for relative, content in derived_outputs.items():
        if relative in proofs:
            proofs[relative] = hashlib.sha256(content).hexdigest()
    payload = {"book_id": _book_id(workspace), "chapter_number": chapter_number,
        "old_contract_path": old_contract.relative_to(workspace.root).as_posix(),
        "old_contract_sha256": file_sha(old_contract), "current_contract_sha256": file_sha(current_contract),
        "old_contract_body_sha256": hashlib.sha256(parse_markdown_artifact(_v_read_text(old_contract, encoding="utf-8"))[1].encode("utf-8")).hexdigest(),
        "proofs": proofs, "equivalent_domains": domains, "impact_id": impact_id}
    operation_id = "tx-refresh-" + semantic_digest(payload)
    payload["operation_id"] = operation_id
    relative = f"runs/orchestration/proof-refresh/{operation_id}.json"
    output = workspace.root / relative
    if _v_is_file(output):
        from knowledge_product_workflow.workspace_transactions import recover_workspace_transaction
        recover_workspace_transaction(workspace, operation_id)
        return output
    derived_outputs[relative] = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    tx = prepare_workspace_transaction(workspace,
        derived_outputs,
        kind="proof_refresh", scope_chapters=[chapter_number], shared_paths=list(derived_outputs),
        input_bindings={current_contract.relative_to(workspace.root).as_posix(): file_sha(current_contract), **input_proofs},
        operation_id=operation_id, events=[("chapter_proofs_refreshed", {"chapter_number": chapter_number,
            "equivalent_domains": domains})])
    commit_workspace_transaction(workspace, tx)
    return output


def plan_binding_matches(workspace: Workspace, number: int, reported_sha256: str) -> bool:
    current = workspace.root / f"chapters/chapter-{number:02d}-content-plan.yaml"
    if file_sha(current) == reported_sha256:
        return True
    if binding_policy_revision(workspace) != 2:
        return False
    now = _v_load_yaml(_v_read_text(current, encoding="utf-8"))
    for old in _v_glob_files(workspace.root / "runs/content-plan-history", f"chapter-{number:02d}-{reported_sha256}.yaml"):
        if file_sha(old) != reported_sha256:
            continue
        previous = _v_load_yaml(_v_read_text(old, encoding="utf-8"))
        for data in (previous, now):
            data.pop("contract_sha256", None)
            for section in data.get("sections", []):
                section.pop("illustration_tasks", None)
        if previous == now:
            return True
    return False


class ChapterImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapter_number: int
    disposition: Literal["unaffected", "equivalent_refresh", "review_required", "content_revision", "unknown"]
    reason: list[str] = Field(default_factory=list)
    affected_proofs: list[str] = Field(default_factory=list)
    invalidated_exports: list[str] = Field(default_factory=list)
    proof_actions: dict[str, str] = Field(default_factory=dict)


class ChangeImpactPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    impact_id: str
    book_id: str
    baseline_bindings: dict[str, str]
    proposed_sha256: str
    proposed_semantic_sha256: str
    policy_revision: int
    changed_inputs: list[str]
    chapters: list[ChapterImpact]
    unknown_reasons: list[str] = Field(default_factory=list)
    sample_action: Literal["retain", "reconfirm", "unknown"] = "unknown"


def preview_change_impact(workspace: Workspace, proposed: Path) -> ChangeImpactPlan:
    from knowledge_product_workflow.workflows.curriculum import BlueprintSubmission

    proposed = proposed.resolve()
    if not proposed.is_relative_to(workspace.root.resolve()) or not _v_is_file(proposed):
        raise ValueError("变更候选必须位于当前书稿内")
    old, body, brief, fingerprints, book_id = _workspace_inputs(workspace)
    raw = _v_load_yaml(_v_read_text(proposed, encoding="utf-8"))
    if isinstance(raw, dict) and "blueprint" in raw:
        raw = raw["blueprint"]
    reject_unknown_fields(raw, BlueprintSubmission)
    new = BlueprintSubmission.model_validate(raw)
    # Compute effective allocations from the proposed complete blueprint, not the old budgets.
    from knowledge_product_workflow.workflows.curriculum import _render_submission_contract
    new_body = _render_submission_contract(new, "zh-CN")
    previous = {c.chapter_number: _bindings(workspace, old, c, brief, fingerprints, body)
                for c in old.chapters}
    old_chapters = {c.chapter_number: c for c in old.chapters}
    impacts = []
    for chapter in new.chapters:
        current = _bindings(workspace, new, chapter, brief, fingerprints, new_body)
        prior = previous.pop(chapter.chapter_number, None)
        changed = [key for key in ("content", "assets", "review", "shared", "dependencies")
                   if prior is None or getattr(prior, key) != getattr(current, key)]
        disposition = "unaffected" if not changed else (
            "content_revision" if any(k in changed for k in ("content", "shared"))
            else "review_required")
        affected_exports = []
        if changed:
            changed_sections = set()
            for item in [*chapter.illustration_tasks,
                         *(old_chapters[chapter.chapter_number].illustration_tasks
                           if chapter.chapter_number in old_chapters else [])]:
                key = item.task_id or f"none:{item.section_id}"
                if prior is None or prior.assets.get(key) != current.assets.get(key):
                    changed_sections.add(item.section_id)
            affected_exports = [e.export_id for e in chapter.production_exports or []
                if e.kind == "result" and (set(changed) != {"assets"}
                                           or e.section_id in changed_sections)]
        impacts.append(ChapterImpact(chapter_number=chapter.chapter_number,
            disposition=disposition, reason=changed, affected_proofs=changed,
            invalidated_exports=affected_exports))
    impacts.extend(ChapterImpact(chapter_number=n, disposition="content_revision", reason=["removed"])
                   for n in previous)
    # A changed result is not an accepted result. Mark the actual result-consumer
    # closure for recheck, without declaring that every consumer must be rewritten.
    indexed = {item.chapter_number: item for item in impacts}
    for _ in new.chapters:
        propagated = False
        for consumer in new.chapters:
            item = indexed[consumer.chapter_number]
            for dependency in consumer.production_dependencies or []:
                producer = indexed.get(dependency.producer_chapter)
                reason = f"result_dependency:{dependency.producer_chapter}"
                if (dependency.kind == "result" and producer
                        and dependency.export_id in producer.invalidated_exports
                        and reason not in item.reason):
                    item.reason.append(reason)
                    item.affected_proofs.append("dependencies")
                    if item.disposition == "unaffected":
                        item.disposition = "review_required"
                    item.invalidated_exports = list(dict.fromkeys([*item.invalidated_exports,
                        *(e.export_id for e in consumer.production_exports or [] if e.kind == "result")]))
                    propagated = True
        if not propagated:
            break
    baseline = {p: file_sha(workspace.root / p) for p in
                ("planning/curriculum-blueprint.md", "planning/product-brief.md")}
    payload = {"book_id": book_id, "baseline": baseline, "proposed": file_sha(proposed)}
    first = next((c for c in impacts if c.chapter_number == 1), None)
    return ChangeImpactPlan(impact_id="impact-" + semantic_digest(payload), book_id=book_id,
        baseline_bindings=baseline, proposed_sha256=file_sha(proposed),
        proposed_semantic_sha256=semantic_digest(_submission_values(new)),
        policy_revision=binding_policy_revision(workspace), changed_inputs=["blueprint"],
        chapters=impacts, sample_action="retain" if first and not first.reason else "reconfirm")


def _submission_values(submission) -> dict:
    return submission.model_dump(mode="json", exclude={"brief_sha256", "brief_content_sha256", "brief_version"})


def _completed_output_matches(workspace: Workspace, output: Path, transaction_path: Path) -> bool:
    try:
        tx = json.loads(_v_read_text(transaction_path, encoding="utf-8"))
        return (tx["state"] == "completed" and tx["book_id"] == _book_id(workspace)
                and tx["outputs"][output.relative_to(workspace.root).as_posix()]["sha256"] == file_sha(output))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def adopt_binding_policy(workspace: Workspace, *, confirm: bool = False) -> dict:
    """Preview first; opt in without inventing historic proof or human authority."""
    from knowledge_product_workflow.runtime import recovery_view
    from knowledge_product_workflow.workspace_transactions import (
        commit_workspace_transaction,
        prepare_workspace_transaction,
    )
    revision = binding_policy_revision(workspace)
    contracts = sorted(workspace.root.glob("chapters/chapter-*-contract.md"))
    marker = workspace.root / "chapters/chapter-01-sample.approved"
    preview = {"current_policy": revision, "target_policy": 2, "already_adopted": revision == 2,
        "requires_confirmation": revision == 1,
        "contracts_to_archive": [p.relative_to(workspace.root).as_posix() for p in contracts],
        "historic_proofs": "保留原件；缺少领域绑定依据的证明必须重新核验，不自动转通过",
        "sample_action": "不恢复已撤销批准；旧批准缺少policy2依据时回原人工门",
        "production_mode": "serial", "next_command": "kp compile-contracts"}
    if not confirm or revision == 2:
        return preview
    view = recovery_view(workspace)
    if not view["can_resume"]:
        raise ValueError("存在未决事务或未知任务；先按 recovery-view 核对原动作")
    intent_path = workspace.root / "runs/orchestration/run-intent.json"
    if _v_is_file(intent_path):
        intent = json.loads(_v_read_text(intent_path, encoding="utf-8"))
        if intent.get("state") not in {"paused", "completed", "blocked"}:
            raise ValueError("旧书必须先合作暂停，不允许热迁移")
    for path in workspace.root.glob("chapters/chapter-*.md"):
        metadata, _ = parse_markdown_artifact(_v_read_text(path, encoding="utf-8"))
        if metadata.status.value == "in_production":
            raise ValueError("尚有编辑中的正文；先保存并收尾原动作")
    state_path = workspace.root / "runs/orchestration/state.json"
    if not _v_is_file(state_path):
        raise ValueError("请先显式接入旧书编排，再预览绑定迁移")
    raw = json.loads(_v_read_text(state_path, encoding="utf-8"))
    raw.update(binding_policy_revision=2, production_mode="serial")
    source_hash = file_sha(state_path)
    archive = "runs/orchestration/binding-migration/" + source_hash
    outputs = {"runs/orchestration/state.json": (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode(),
               archive + "/state.policy1.json": _v_read_bytes(state_path)}
    for path in [*contracts, *([marker] if _v_is_file(marker) else [])]:
        relative = path.relative_to(workspace.root).as_posix()
        outputs[archive + "/" + relative] = _v_read_bytes(path)
        outputs[relative] = None
    transaction = prepare_workspace_transaction(workspace, outputs, kind="binding_migration",
        scope_chapters=[], shared_paths=list(outputs),
        input_bindings={"runs/orchestration/state.json": source_hash},
        events=[("binding_policy_adopted", preview)], authority={"explicit_confirmation": True})
    commit_workspace_transaction(workspace, transaction)
    return {**preview, "adopted": True, "operation_id": transaction.operation_id}
