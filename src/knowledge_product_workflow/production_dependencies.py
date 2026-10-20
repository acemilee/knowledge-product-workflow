"""Declared chapter inputs, exports and readiness, before any prose is dispatched."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import memoized_read as _memoized_read
from knowledge_product_workflow.verification_context import (
    read_bytes as _v_read_bytes,
)
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.verification_context import (
    verified_read as _verification_read,
)
from knowledge_product_workflow.workspace import Workspace


class ChapterExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    export_id: str = Field(pattern=r"^export-[a-z0-9][a-z0-9-]*$")
    section_id: str = Field(pattern=r"^\d+(?:\.\d+)+$")
    kind: Literal["interface", "result"]
    definition: str = Field(min_length=1)
    conditions: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


class ChapterDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    producer_chapter: int = Field(gt=0)
    section_id: str = Field(pattern=r"^\d+(?:\.\d+)+$")
    kind: Literal["navigation", "interface", "result"]
    export_id: str | None = None
    purpose: str = Field(min_length=1)


class SharedContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    terminology: dict[str, str] = Field(default_factory=dict)
    cases: dict[str, dict] = Field(default_factory=dict)
    conventions: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


class AcceptedExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    export_id: str
    chapter_number: int
    section_id: str
    commit_id: str
    unit_sha256: str
    declaration_sha256: str
    proof_bindings: dict[str, str] = Field(default_factory=dict)
    input_bindings: dict[str, str] = Field(default_factory=dict)
    state: Literal["valid", "stale", "unknown", "failed"] = "unknown"
    schema_version: Literal[1, 2] = 1
    commit_kind: Literal["production", "serial"] = "production"

    @model_serializer(mode="wrap")
    def compatible_dump(self, handler):
        data = handler(self)
        if self.schema_version == 1 and self.commit_kind == "production":
            data.pop("schema_version", None)
            data.pop("commit_kind", None)
        return data


class DependencyPlan(BaseModel):
    schema_version: int = 1
    phase: Literal["pre_sample", "approved_production"]
    baseline_bindings: dict[str, str] = Field(default_factory=dict)
    chapters: list[dict]
    exports: dict[str, dict] = Field(default_factory=dict)
    ready_chapters: list[int] = Field(default_factory=list)
    blocked_chapters: dict[int, list[str]] = Field(default_factory=dict)
    shared_context: SharedContext = Field(default_factory=SharedContext)
    legacy: bool = False


def payload_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(_v_read_bytes(path)).hexdigest()


def workspace_file(workspace: Workspace, relative: str) -> Path:
    path = (workspace.root / relative).resolve()
    if not path.is_relative_to(workspace.root.resolve()) or not _v_is_file(path):
        raise ValueError(f"输入缺失或越界：{relative}")
    return path


def dependency_plan_from_chapters(
    chapters: list[dict], *, sample_approved: bool,
    accepted_exports: dict[str, AcceptedExport] | None = None,
) -> DependencyPlan:
    """Pure graph construction; workspace admission separately verifies current proofs."""
    numbers = [c["chapter_number"] for c in chapters]
    if len(numbers) != len(set(numbers)):
        raise ValueError("章节编号重复")
    exports: dict[str, dict] = {}
    edges: dict[int, set[int]] = {n: set() for n in numbers}
    reasons: dict[int, list[str]] = {}
    for chapter in chapters:
        number = chapter["chapter_number"]
        if chapter.get("production_dependencies") is None:
            reasons[number] = ["dependencies_unknown"]
        for raw in chapter.get("production_exports") or []:
            item = ChapterExport.model_validate(raw)
            if int(item.section_id.split(".")[0]) != number:
                raise ValueError("输出小节不属于提供章节")
            if item.export_id in exports:
                raise ValueError(f"输出所有者重复：{item.export_id}")
            exports[item.export_id] = {**item.model_dump(), "chapter_number": number}
    for chapter in chapters:
        number = chapter["chapter_number"]
        for raw in chapter.get("production_dependencies") or []:
            dependency = ChapterDependency.model_validate(raw)
            if dependency.producer_chapter not in edges:
                raise ValueError("依赖引用不存在章节")
            if int(dependency.section_id.split(".")[0]) != number:
                raise ValueError("使用小节不属于当前章节")
            if dependency.kind == "navigation":
                continue
            export = exports.get(dependency.export_id or "")
            if (export is None or export["chapter_number"] != dependency.producer_chapter
                    or export["kind"] != dependency.kind):
                raise ValueError(f"缺失或类型不符的输出：{dependency.export_id}")
            if dependency.kind == "result":
                edges[number].add(dependency.producer_chapter)
                accepted = (accepted_exports or {}).get(dependency.export_id)
                if (accepted is None or accepted.state != "valid"
                        or accepted.declaration_sha256 != payload_digest(export)):
                    reasons.setdefault(number, []).append(
                        f"result_not_current:{dependency.export_id}"
                    )
    active, finished = set(), set()

    def visit(number: int) -> None:
        if number in active:
            raise ValueError(f"真实结果依赖循环：{sorted(active | {number})}")
        if number in finished:
            return
        active.add(number)
        for parent in sorted(edges[number]):
            visit(parent)
        active.remove(number)
        finished.add(number)

    for number in numbers:
        visit(number)
        if not sample_approved and number != 1:
            reasons.setdefault(number, []).append("sample_approval_required")
    return DependencyPlan(
        phase="approved_production" if sample_approved else "pre_sample", chapters=chapters,
        exports=exports, ready_chapters=sorted(set(numbers) - set(reasons)),
        blocked_chapters=reasons,
    )


def export_is_current(workspace: Workspace, value: AcceptedExport, declaration: dict,
                      visited: frozenset[str] = frozenset()) -> bool:
    """Never use historical accepted state as evidence of current result validity."""
    try:
        if value.state != "valid" or value.declaration_sha256 != payload_digest(declaration):
            return False
        from knowledge_product_workflow.workspace_transactions import (
            require_transaction_consistency,
        )
        require_transaction_consistency(workspace, chapters={value.chapter_number})
        relative = (f"runs/orchestration/transactions/{value.commit_id}.json" if value.commit_kind == "serial"
                    else f"runs/production/commits/{value.commit_id}.json")
        commit = json.loads(_v_read_text(workspace_file(workspace, relative), encoding="utf-8"))
        if commit.get("state") != "completed" or commit.get("diagnostic_only"):
            return False
        if value.commit_kind == "serial" and (value.schema_version != 2
                or commit.get("kind") != "chapter_submission"
                or commit.get("scope_chapters") != [value.chapter_number]):
            return False
        if commit.get("workspace_transaction_id"):
            transaction = workspace_file(workspace,
                "runs/orchestration/transactions/" + commit["workspace_transaction_id"] + ".json")
            if json.loads(_v_read_text(transaction, encoding="utf-8")).get("state") != "completed":
                return False
        from knowledge_product_workflow.developmental import reading_units

        metadata, body = parse_markdown_artifact(_v_read_text(workspace_file(
            workspace, f"chapters/chapter-{value.chapter_number:02d}.md"
        ), encoding="utf-8"))
        if value.commit_kind == "serial":
            if commit.get("book_id") != metadata.book_id:
                return False
            item = commit.get("outputs", {}).get(f"chapters/chapter-{value.chapter_number:02d}.md")
            if not item:
                return False
            staged = workspace_file(workspace, item["staged"])
            if (not staged.is_relative_to(workspace.root / "runs/orchestration/transactions" / value.commit_id)
                    or file_digest(staged) != item["sha256"]):
                return False
            _, accepted_body = parse_markdown_artifact(_v_read_text(staged, encoding="utf-8"))
            accepted_unit = next((u for u in reading_units(accepted_body) if u["unit_id"] == value.section_id), None)
            if not accepted_unit or accepted_unit["unit_sha256"] != value.unit_sha256:
                return False
            # Whole-chapter proof files must still describe the real current body;
            # a digest-preserving old report does not validate new surrounding text.
            body_sha = hashlib.sha256((body.strip() + "\n").encode("utf-8")).hexdigest()
            for relative in value.proof_bindings:
                path = workspace_file(workspace, relative)
                if path.suffix == ".json":
                    proof = json.loads(_v_read_text(path, encoding="utf-8"))
                    if proof.get("chapter_body_sha256", body_sha) != body_sha:
                        return False
        unit = next((u for u in reading_units(body) if u["unit_id"] == value.section_id), None)
        return bool(unit and unit["unit_sha256"] == value.unit_sha256 and value.proof_bindings
                    and all(file_digest(workspace_file(workspace, p)) == sha
                            for p, sha in value.proof_bindings.items())
                    and all(binding_digest(workspace, p, visited=visited | {value.export_id}) == sha
                            for p, sha in value.input_bindings.items()))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def build_dependency_plan(workspace: Workspace) -> DependencyPlan:
    from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint

    blueprint_path, metadata, blueprint = require_current_submitted_blueprint(workspace)
    if metadata.status is not ArtifactStatus.APPROVED:
        raise ValueError("写作前必须批准当前蓝图")
    chapters = [chapter.model_dump(mode="json") for chapter in blueprint.chapters]
    sample = workspace.root / "chapters/chapter-01-sample.approved"
    # Current core owns sample approval. Include actual sample bytes, not a fabricated binding.
    sample_current = _v_is_file(sample) and _v_is_file(workspace.root / "chapters/chapter-01.md")
    from knowledge_product_workflow.change_impact import binding_policy_revision
    precise = binding_policy_revision(workspace) == 2
    if precise:
        from knowledge_product_workflow.user_decisions import sample_approval_is_current
        sample_current = sample_approval_is_current(workspace)
    if sample_current and not precise:
        from knowledge_product_workflow.chapter_compliance import (
            validate_current_chapter_compliance,
        )
        sample_current = not any(f.severity.value == "blocker" for f in
            validate_current_chapter_compliance(workspace, workspace.root / "chapters/chapter-01.md"))
    plan = dependency_plan_from_chapters(chapters, sample_approved=sample_current)
    plan.legacy = blueprint.production_policy_revision == 0
    plan.shared_context = blueprint.shared_context or SharedContext()
    plan.baseline_bindings = {
        blueprint_path.relative_to(workspace.root).as_posix(): file_digest(blueprint_path),
        "planning/product-brief.md": file_digest(workspace_file(workspace, "planning/product-brief.md")),
    }
    if sample_current:
        plan.baseline_bindings.update({
            "chapters/chapter-01-sample.approved": file_digest(sample),
            "chapters/chapter-01.md": file_digest(workspace.root / "chapters/chapter-01.md"),
        })
    registry = workspace.root / "runs/production/accepted-exports.json"
    current = {}
    if _v_is_file(registry):
        raw = json.loads(_v_read_text(registry, encoding="utf-8"))
        for key, item in raw.items():
            export = AcceptedExport.model_validate(item)
            if key in plan.exports and export_is_current(workspace, export, plan.exports[key]):
                current[key] = export
    evaluated = dependency_plan_from_chapters(
        chapters, sample_approved=sample_current, accepted_exports=current
    )
    plan.ready_chapters = evaluated.ready_chapters
    plan.blocked_chapters = evaluated.blocked_chapters
    return plan


def ready_chapters(plan: DependencyPlan, accepted_exports: dict) -> list[int]:
    return dependency_plan_from_chapters(
        plan.chapters, sample_approved=plan.phase == "approved_production",
        accepted_exports={k: AcceptedExport.model_validate(v) for k, v in accepted_exports.items()},
    ).ready_chapters


WRITER_FORMAT_BINDING = "planning/publication-profile.yaml#authoring-format=1"


def writer_format_values(workspace: Workspace) -> dict:
    """Bind author-visible grammar, excluding palette/font and other visual choices."""
    from knowledge_product_workflow.presentation import PublicationProfile
    from knowledge_product_workflow.verification_context import load_yaml

    relative = "planning/publication-profile.yaml"
    if not _v_is_file(workspace.root / relative):
        return {"reader_layout_revision": 1}
    profile = PublicationProfile.model_validate(load_yaml(
        _v_read_text(workspace_file(workspace, relative), encoding="utf-8")))
    return {"reader_layout_revision": profile.reader_layout_revision}


def binding_digest(workspace: Workspace, key: str, *, visited: frozenset[str] = frozenset()) -> str:
    if "#authoring-format=" in key:
        if key != WRITER_FORMAT_BINDING:
            raise ValueError("未知作者格式绑定")
        return payload_digest(writer_format_values(workspace))
    if "#proof=" in key:
        from knowledge_product_workflow.change_impact import proof_contract_digest
        relative, value = key.split("#proof=", 1)
        number, domain = value.split(":", 1)
        if relative != "planning/curriculum-blueprint.md" or not number.isdecimal():
            raise ValueError("证明语义绑定越界")
        return proof_contract_digest(workspace, int(number), domain)
    if "#chapter=" in key:
        from knowledge_product_workflow.change_impact import (
            binding_policy_revision,
            chapter_bindings,
            semantic_digest,
        )
        relative, number = key.split("#chapter=", 1)
        if (relative not in {"planning/curriculum-blueprint.md", "planning/product-brief.md"}
                or not number.isdecimal() or binding_policy_revision(workspace) != 2):
            raise ValueError("未知章节语义绑定")
        return semantic_digest(chapter_bindings(workspace, int(number)).semantic_values())
    if "#export=" in key:
        relative, export_id = key.split("#export=", 1)
        registry = json.loads(_v_read_text(workspace_file(workspace, relative), encoding="utf-8"))
        export = AcceptedExport.model_validate(registry[export_id])
        # Result chains are acyclic in the approved graph. Reject corrupted registries
        # that would recursively bind the same output rather than trusting their flag.
        if export_id in visited or any(p == key for p in export.input_bindings):
            raise ValueError("输出证据自循环")
        from knowledge_product_workflow.workflows.curriculum import (
            require_current_submitted_blueprint,
        )
        _, _, blueprint = require_current_submitted_blueprint(workspace)
        declaration = next(({**e.model_dump(), "chapter_number": c.chapter_number}
            for c in blueprint.chapters for e in c.production_exports or []
            if e.export_id == export_id), None)
        identity = export.model_dump() if export.schema_version == 1 else {
            k: export.model_dump()[k] for k in ("export_id", "chapter_number", "section_id",
                "unit_sha256", "declaration_sha256", "input_bindings")}
        return payload_digest({"export": identity,
            "current": bool(declaration and export_is_current(workspace, export, declaration, visited))})
    if "#claims=" in key:
        from knowledge_product_workflow.claims import claim_scoped_evidence_digests

        relative, claims = key.split("#claims=", 1)
        return claim_scoped_evidence_digests(workspace, set(filter(None, claims.split(","))))[relative]
    if "#unit=" not in key:
        return file_digest(workspace_file(workspace, key))
    from knowledge_product_workflow.developmental import reading_units

    relative, unit_id = key.split("#unit=", 1)
    text = _v_read_text(workspace_file(workspace, relative), encoding="utf-8")
    try:
        _, text = parse_markdown_artifact(text)
    except ValueError:
        pass
    unit = next((u for u in reading_units(text) if u["unit_id"] == unit_id), None)
    if unit is None:
        raise ValueError(f"缺少依赖单元：{key}")
    return unit["unit_sha256"]


def build_task_context(workspace: Workspace, chapter_number: int, role: str) -> dict:
    """A declared read set; another chapter's unrelated output does not retire it."""
    plan = build_dependency_plan(workspace)
    chapter = next(c for c in plan.chapters if c["chapter_number"] == chapter_number)
    paths = set(plan.baseline_bindings)
    for relative in (
        f"chapters/chapter-{chapter_number:02d}-contract.md",
        f"chapters/chapter-{chapter_number:02d}-content-plan.yaml",
        "runs/orchestration/state.json", "runs/orchestration/capability-snapshot.json", "research/source-ledger.jsonl",
        "research/assumption-ledger.jsonl", "research/claim-classifications.jsonl",
    ):
        if _v_is_file(workspace.root / relative):
            paths.add(relative)
    live = {p: binding_digest(workspace, p) for p in paths}
    if role == "writer":
        live[WRITER_FORMAT_BINDING] = binding_digest(workspace, WRITER_FORMAT_BINDING)
        if _v_is_file(workspace.root / "planning/publication-profile.yaml"):
            paths.add("planning/publication-profile.yaml")
    from knowledge_product_workflow.change_impact import binding_policy_revision
    if binding_policy_revision(workspace) == 2:
        for relative in ("planning/curriculum-blueprint.md", "planning/product-brief.md"):
            live.pop(relative, None)
            key = relative + "#chapter=" + str(chapter_number)
            live[key] = binding_digest(workspace, key)
    claims = ",".join(sorted(set(chapter.get("claim_ids", [])) | set(plan.shared_context.claim_ids)))
    for path in list(live):
        if path.startswith("research/"):
            del live[path]
            key = path + "#claims=" + claims
            live[key] = binding_digest(workspace, key)
    excerpts = {}
    for dependency in chapter.get("production_dependencies") or []:
        if dependency["kind"] == "navigation":
            continue
        export = plan.exports[dependency["export_id"]]
        if dependency["kind"] == "result":
            relative = f"chapters/chapter-{export['chapter_number']:02d}.md"
            key = relative + "#unit=" + export["section_id"]
            live[key] = binding_digest(workspace, key)
            proof_key = "runs/production/accepted-exports.json#export=" + export["export_id"]
            live[proof_key] = binding_digest(workspace, proof_key)
            paths.add(relative)
            for related in (f"chapters/chapter-{export['chapter_number']:02d}-contract.md",
                            f"chapters/chapter-{export['chapter_number']:02d}-content-plan.yaml"):
                if _v_is_file(workspace.root / related):
                    paths.add(related)
                    live[related] = binding_digest(workspace, related)
            excerpts[export["export_id"]] = {"section": export["section_id"], "binding": live[key]}
        else:
            excerpts[export["export_id"]] = export
    return {"chapter_number": chapter_number, "role": role,
            "snapshot_paths": sorted(paths), "live_bindings": live,
            "shared_context": plan.shared_context.model_dump(), "dependencies": excerpts,
            "blocked_by": plan.blocked_chapters.get(chapter_number, []),
            "scope": "批准公共契约、当前章及显式依赖；不读取全部章节"}


def comparison_input_bindings(workspace: Workspace, chapter_number: int) -> tuple[set[str], dict[str, str]]:
    """Carry declared editorial comparisons, including supported ad-hoc body-only units."""
    report = workspace.root / f'runs/editorial/chapter-{chapter_number:02d}-structure-review.json'
    if not _v_is_file(report):
        return set(), {}
    comparisons = json.loads(_v_read_text(report, encoding='utf-8')).get('comparison_context', {})
    if not isinstance(comparisons, dict):
        raise TypeError('比较单元清单必须为对象')
    paths, bindings = set(), {}
    for unit_id in comparisons:
        if not re.fullmatch(r'\d+(?:\.\d+)+', unit_id):
            raise ValueError('比较单元标识无效')
        number = int(unit_id.split('.')[0])
        if number == chapter_number:
            continue  # The complete current candidate, not its old canonical draft, owns these units.
        relative = f'chapters/chapter-{number:02d}.md'
        key = relative + '#unit=' + unit_id
        bindings[key] = binding_digest(workspace, key)
        paths.add(relative)
        for metadata in (f'chapters/chapter-{number:02d}-contract.md',
                         f'chapters/chapter-{number:02d}-content-plan.yaml'):
            if _v_is_file(workspace.root / metadata):
                paths.add(metadata)
    return paths, bindings


def candidate_input_paths(workspace: Workspace, chapter_number: int, source: Path) -> list[str]:
    """Discover existing private-check inputs once, before freezing the candidate."""
    context = build_task_context(workspace, chapter_number, "reviewer")
    paths = set(context["snapshot_paths"])
    sample_plan = "chapters/chapter-01-content-plan.yaml"
    if ({"chapters/chapter-01.md", "chapters/chapter-01-sample.approved"}.issubset(paths)
            and _v_is_file(workspace.root / sample_plan)):
        paths.add(sample_plan)
    comparison_paths, _ = comparison_input_bindings(workspace, chapter_number)
    paths.update(comparison_paths)
    from knowledge_product_workflow.change_impact import binding_policy_revision
    if binding_policy_revision(workspace) == 2:
        # Carry original authority/provenance into private submission checks.
        # Only declared result ancestors are traversed; no all-book prose scan.
        for relative in ("runs/interaction/sample.json", "runs/approval-profile.json"):
            if _v_is_file(workspace.root / relative):
                paths.add(relative)
        registry_path = workspace.root / "runs/production/accepted-exports.json"
        registry = json.loads(_v_read_text(registry_path, encoding="utf-8")) if _v_is_file(registry_path) else {}
        queue = [key.split("#export=", 1)[1] for key in context["live_bindings"] if "#export=" in key]
        visited = set()
        while queue:
            export_id = queue.pop()
            if export_id in visited:
                continue
            visited.add(export_id)
            value = AcceptedExport.model_validate(registry[export_id])
            paths.add("runs/production/accepted-exports.json")
            relative = (f"runs/orchestration/transactions/{value.commit_id}.json"
                        if value.commit_kind == "serial" else f"runs/production/commits/{value.commit_id}.json")
            paths.add(relative)
            commit = json.loads(_v_read_text(workspace_file(workspace, relative), encoding="utf-8"))
            if value.commit_kind == "serial":
                for output in commit["outputs"].values():
                    if output.get("staged"):
                        paths.add(output["staged"])
            if commit.get("workspace_transaction_id"):
                paths.add(f"runs/orchestration/transactions/{commit['workspace_transaction_id']}.json")
            for key in [*value.proof_bindings, *value.input_bindings]:
                relative = key.split("#", 1)[0]
                if _v_is_file(workspace.root / relative):
                    paths.add(relative)
                if "#export=" in key:
                    queue.append(key.split("#export=", 1)[1])
    # Global claim ownership validation needs contracts, never all other prose.
    paths.update(p.relative_to(workspace.root).as_posix()
        for p in (workspace.root/"chapters").glob("chapter-*-contract.md") if _v_is_file(p))
    body = _v_read_text(source, encoding="utf-8")
    for relative in (f"chapters/chapter-{chapter_number:02d}.md",
        f"runs/editorial/chapter-{chapter_number:02d}-structure-review.json",
        f"runs/editorial/chapter-{chapter_number:02d}-developmental-report.json",
        f"runs/humanize/chapter-{chapter_number:02d}.json",
        "runs/events.jsonl", "planning/publication-profile.yaml", "planning/teaching-length-recommendation.yaml"):
        if _v_is_file(workspace.root / relative):
            paths.add(relative)
    from knowledge_product_workflow.example_checks import _verified_example_records
    for _, path in _verified_example_records(workspace, body).values():
        paths.update(p.relative_to(workspace.root).as_posix() for p in path.parent.iterdir() if _v_is_file(p))
    for diagram_id in re.findall(r"assets/figures/([a-z0-9-]+)\.(?:pdf|svg|png)", body):
        for directory in (workspace.root / "runs/diagrams", workspace.root / "assets/figures"):
            paths.update(p.relative_to(workspace.root).as_posix() for p in directory.glob(diagram_id + ".*") if _v_is_file(p))
    return sorted(paths)


def undeclared_candidate_dependencies(workspace: Workspace, chapter_number: int, body: str) -> list[str]:
    """Conservative discovery is a review trigger, never a claim of full semantic coverage."""
    from knowledge_product_workflow.developmental import reading_units, review_dependencies
    from knowledge_product_workflow.workflows.authoring import (
        require_current_chapter_contract_in_view as require_current_chapter_contract,
    )
    _, chapter = require_current_chapter_contract(workspace, chapter_number)
    if chapter.production_dependencies is None:
        return []  # Legacy serial policy; no silent new parallel declaration.
    declared = chapter.production_dependencies
    issues = []
    units = reading_units(body)
    for index, unit in enumerate(units):
        end = units[index+1]["start_line"]-1 if index+1 < len(units) else len(body.splitlines())
        text = "\n".join(body.splitlines()[unit["start_line"]-1:end])
        for dependency in review_dependencies(workspace, chapter_number, text):
            if dependency["kind"] == "contract":
                continue
            if not any(d.section_id == unit["unit_id"] and d.producer_chapter == dependency["chapter"]
                       and d.kind in {"result", "interface"} for d in declared):
                issues.append(f"dependency_review_required:{unit['unit_id']}→chapter-{dependency['chapter']}")
    return sorted(set(issues))


@_verification_read(transaction_view=True)
def deferred_repairs(workspace: Workspace, *, final_review: bool = False) -> list[dict]:
    """Read-only repair debt, independent of which author happens to be next."""
    from knowledge_product_workflow.workspace_transactions import pending_workspace_transactions
    pending = pending_workspace_transactions(workspace)
    return _deferred_repairs_payload(workspace, final_review=final_review,
        pending_json=json.dumps(pending, sort_keys=True, default=str))


@_memoized_read
def _deferred_repairs_payload(workspace: Workspace, *, final_review: bool, pending_json: str) -> list[dict]:
    from knowledge_product_workflow.change_impact import binding_policy_revision
    from knowledge_product_workflow.chapter_compliance import (
        check_chapter_in_view as check_chapter,
    )
    from knowledge_product_workflow.chapter_compliance import (
        validate_current_chapter_compliance_in_view as validate_current_chapter_compliance,
    )
    from knowledge_product_workflow.production_context import _missing_proofs
    from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
    if binding_policy_revision(workspace) != 2:
        return []
    _, _, blueprint = require_current_submitted_blueprint(workspace)
    pending = json.loads(pending_json)
    result = []
    for chapter in blueprint.chapters:
        number = chapter.chapter_number
        owned = [r for r in pending if number in r.get("scope_chapters", [])]
        if owned:
            result.append({"chapter_number": number, "kind": "transaction_pending",
                           "reasons": [r["operation_id"] for r in owned]})
            continue
        path = workspace.root / f"chapters/chapter-{number:02d}.md"
        if not _v_is_file(path):
            continue
        try:
            metadata, _ = parse_markdown_artifact(_v_read_text(path, encoding="utf-8"))
            if metadata.status.value in {"draft", "in_production"}:
                continue
            missing = [f.message for f in validate_current_chapter_compliance(workspace, path, final_review=final_review)
                       if f.severity.value == "blocker"]
            missing.extend(_missing_proofs(check_chapter(workspace, number, path)))
        except (OSError, ValueError) as error:
            missing = [str(error)]
        kind = "proof_or_content_recheck"
        if not missing:
            from knowledge_product_workflow.review_batches import validate_review_batches
            from knowledge_product_workflow.workflows.product_brief import parse_detail_constraints
            brief = parse_markdown_artifact(_v_read_text(workspace.root / "planning/product-brief.md", encoding="utf-8"))[1]
            constraints = parse_detail_constraints(brief)
            if constraints and constraints.review_policy.value == "independent_review_required":
                missing = [f.message for f in validate_review_batches(workspace, chapter_numbers={number})]
                kind = "independent_review"
        if missing:
            result.append({"chapter_number": number, "kind": kind, "reasons": missing})
    return result


@_verification_read(transaction_view=True)
def require_no_deferred_repairs(workspace: Workspace) -> None:
    from knowledge_product_workflow.change_impact import binding_policy_revision
    if binding_policy_revision(workspace) != 2:
        return
    from knowledge_product_workflow.workspace_transactions import require_transaction_consistency
    require_transaction_consistency(workspace)
    plan = build_dependency_plan(workspace)
    debts = deferred_repairs(workspace, final_review=True)
    missing = [c["chapter_number"] for c in plan.chapters
               if not _v_is_file(workspace.root / f"chapters/chapter-{c['chapter_number']:02d}.md")
               or parse_markdown_artifact(_v_read_text(workspace.root / f"chapters/chapter-{c['chapter_number']:02d}.md", encoding="utf-8"))[0].status.value not in {"approved", "in_validation"}]
    if debts or missing or plan.blocked_chapters:
        raise ValueError("章节修复／依赖待办尚未清零：" + str({"repairs": debts,
            "missing_chapters": missing, "dependencies": plan.blocked_chapters}))


def finalize_serial_exports(workspace: Workspace, *, chapter_number: int,
                            operation_id: str | None = None) -> list[AcceptedExport]:
    from knowledge_product_workflow.change_impact import binding_policy_revision
    from knowledge_product_workflow.chapter_compliance import (
        check_chapter,
        validate_current_chapter_compliance,
    )
    from knowledge_product_workflow.developmental import reading_units
    from knowledge_product_workflow.production_context import _missing_proofs
    from knowledge_product_workflow.review_batches import validate_review_batches
    from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
    from knowledge_product_workflow.workflows.product_brief import parse_detail_constraints
    from knowledge_product_workflow.workspace_transactions import (
        commit_workspace_transaction,
        prepare_workspace_transaction,
    )
    if (binding_policy_revision(workspace) != 2
            or not _v_is_file(workspace.root / "planning/curriculum-blueprint.md")):
        return []
    _, _, blueprint = require_current_submitted_blueprint(workspace)
    chapter = next(c for c in blueprint.chapters if c.chapter_number == chapter_number)
    declarations = [e for e in chapter.production_exports or [] if e.kind == "result"]
    if not declarations:
        return []
    path = workspace.root / f"chapters/chapter-{chapter_number:02d}.md"
    if not _v_is_file(path):
        return []
    matching = []
    for journal in (workspace.root / "runs/orchestration/transactions").glob("*.json"):
        data = json.loads(_v_read_text(journal, encoding="utf-8"))
        output = data.get("outputs", {}).get(path.relative_to(workspace.root).as_posix())
        if (data.get("kind") == "chapter_submission" and data.get("state") == "completed"
                and output and output["sha256"] == file_digest(path)
                and (operation_id is None or data["operation_id"] == operation_id)):
            matching.append(data)
    if not matching:
        return []
    commit = max(matching, key=lambda d: d["events"][0]["at"] if d["events"] else d["operation_id"])
    if (not _v_is_file(path) or _missing_proofs(check_chapter(workspace, chapter_number, path))
            or any(f.severity.value == "blocker" for f in validate_current_chapter_compliance(workspace, path))):
        return []
    brief = parse_markdown_artifact(_v_read_text(workspace.root / "planning/product-brief.md", encoding="utf-8"))[1]
    constraints = parse_detail_constraints(brief)
    if (constraints and constraints.review_policy.value == "independent_review_required"
            and validate_review_batches(workspace, chapter_numbers={chapter_number})):
        return []
    _, body = parse_markdown_artifact(_v_read_text(path, encoding="utf-8"))
    units = {u["unit_id"]: u for u in reading_units(body)}
    proof_paths = [f"runs/chapter-compliance/chapter-{chapter_number:02d}.json",
        f"runs/humanize/chapter-{chapter_number:02d}.json",
        f"runs/editorial/chapter-{chapter_number:02d}-developmental-report.json",
        f"runs/editorial/chapter-{chapter_number:02d}-structure-review.json"]
    proofs = {p: file_digest(workspace.root / p) for p in proof_paths if _v_is_file(workspace.root / p)}
    context = build_task_context(workspace, chapter_number, "writer")
    inputs = {key: value for key, value in context["live_bindings"].items()
              if key.startswith("research/") or "#export=" in key or "#unit=" in key}
    semantic_key = f"planning/curriculum-blueprint.md#proof={chapter_number}:content_plan"
    inputs[semantic_key] = binding_digest(workspace, semantic_key)
    values = []
    for declaration in declarations:
        if declaration.section_id not in units:
            return []
        section_inputs = dict(inputs)
        result_key = f"planning/curriculum-blueprint.md#proof={chapter_number}:result:{declaration.section_id}"
        section_inputs[result_key] = binding_digest(workspace, result_key)
        values.append(AcceptedExport(schema_version=2, commit_kind="serial",
            export_id=declaration.export_id, chapter_number=chapter_number, section_id=declaration.section_id,
            commit_id=commit["operation_id"], unit_sha256=units[declaration.section_id]["unit_sha256"],
            declaration_sha256=payload_digest({**declaration.model_dump(), "chapter_number": chapter_number}),
            proof_bindings=proofs, input_bindings=section_inputs, state="valid"))
    registry_path = workspace.root / "runs/production/accepted-exports.json"
    registry = json.loads(_v_read_text(registry_path, encoding="utf-8")) if _v_is_file(registry_path) else {}
    updated = {**registry, **{e.export_id: e.model_dump() for e in values}}
    if updated != registry:
        relative = registry_path.relative_to(workspace.root).as_posix()
        tx = prepare_workspace_transaction(workspace,
            {relative: json.dumps(updated, ensure_ascii=False, sort_keys=True).encode("utf-8")},
            kind="export_update", scope_chapters=[chapter_number], shared_paths=[relative],
            input_bindings={path.relative_to(workspace.root).as_posix(): file_digest(path), **proofs},
            events=[("serial_exports_accepted", {"chapter_number": chapter_number,
                     "operation_id": commit["operation_id"], "exports": [e.export_id for e in values]})])
        commit_workspace_transaction(workspace, tx)
    return values


def robustness_quality_findings(workspace: Workspace) -> list:
    from knowledge_product_workflow.quality import QualityFinding, Severity
    try:
        require_no_deferred_repairs(workspace)
    except (ValueError, OSError) as error:
        return [QualityFinding(code="DEFERRED_REPAIR_REQUIRED", severity=Severity.BLOCKER,
            location="runs/orchestration", message=str(error))]
    return []
