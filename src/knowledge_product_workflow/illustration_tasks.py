"""Teaching-driven illustration decisions shared by the blueprint and chapter plan."""

from __future__ import annotations

import re
from hashlib import sha256
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

DiagramKind = Literal["flow", "layered_architecture", "comparison", "sequence", "lifecycle",
                      "nested_structure", "card_matrix", "stage_flow"]

RELATION_FAMILIES = {"handoff": ("stage_flow", "right"), "sequence": ("sequence", "right"),
                     "hierarchy": ("layered_architecture", "down"), "containment": ("nested_structure", "down"),
                     "comparison": ("comparison", "right"), "dependency": ("flow", "down"),
                     "matrix": ("card_matrix", "right"), "lifecycle": ("lifecycle", "right")}


class IllustrationTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(pattern=r"^\d+(?:\.\d+)+$")
    relation_kind: Literal["handoff", "sequence", "hierarchy", "containment", "comparison", "dependency", "matrix", "lifecycle"] | None = None
    direction: Literal["right", "down"] | None = None
    anchor_hint: str | None = Field(default=None, min_length=4, max_length=120)

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler):
        value = handler(self)
        for name in ("relation_kind", "direction", "anchor_hint"):
            if getattr(self, name) is None:
                value.pop(name, None)
        return value
    decision: Literal["required", "useful", "none"]
    task_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]*$")
    production: Literal["create", "reference", "omit"] | None = None
    trigger: Literal["structure", "hidden_mechanism", "coordination", "selection", "synthesis"] | None = None
    read_question: str | None = Field(default=None, min_length=4, max_length=240)
    viewpoint: str | None = Field(default=None, min_length=2, max_length=120)
    visual_gain: str | None = Field(default=None, min_length=8, max_length=240)
    alternative_rationale: str = Field(min_length=8, max_length=240)
    diagram_type: DiagramKind | None = None
    backend: Literal["vector", "host-image"] = "vector"
    placement: Literal["orientation", "synthesis", "viewpoint_change"] | None = None
    after_anchor: str | None = Field(default=None, pattern=r"^block-[0-9a-f]{16}$")
    before_anchor: str | None = Field(default=None, pattern=r"^block-[0-9a-f]{16}$")
    reference_task_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]*$")

    @field_validator("read_question", "viewpoint", "visual_gain", "alternative_rationale")
    @classmethod
    def require_specific_text(cls, value, info):
        if value is not None:
            value = value.strip()
            minimum = {"read_question": 4, "viewpoint": 2}.get(info.field_name, 8)
            if len(value) < minimum or (value.startswith("<") and value.endswith(">")):
                raise ValueError("图任务理由须具体填写，不能用空白或模板占位")
            if value in {"更美观", "降低理解成本", "有助理解"}:
                raise ValueError("须说明具体读图问题及组织增益，不以装饰理由准入")
        return value

    @model_validator(mode="after")
    def consistent_decision(self):
        if self.production is None:
            self.production = "create" if self.decision == "required" else "omit"
        if self.decision == "none":
            if self.production != "omit" or self.task_id is not None:
                raise ValueError("不画决定不能声明生成任务或调用授权")
            if any((self.trigger, self.read_question, self.viewpoint, self.visual_gain,
                    self.diagram_type, self.placement, self.after_anchor,
                    self.before_anchor, self.reference_task_id)):
                raise ValueError("不画只记录理由，不虚构图型、节点或视觉增益")
        else:
            if not all((self.task_id, self.trigger, self.read_question,
                        self.viewpoint, self.visual_gain)):
                raise ValueError("图任务须有稳定task_id、触发场景、读图问题、视角与增益")
            if self.decision == "required" and self.production == "omit":
                raise ValueError("必需图未满足，不能省略或降为不画以躲预算")
            if self.production == "create" and (self.diagram_type is None or self.placement is None):
                raise ValueError("制作任务须声明图型及阅读位置")
            if self.production == "create" and self.backend == "host-image" and self.diagram_type in {"comparison", "sequence", "lifecycle"}:
                raise ValueError("当前宿主图族不支持该图型；按Brief说明使用vector，不新增自由图族")
            if (self.production == "reference") != bool(self.reference_task_id):
                raise ValueError("引用已有图须指定reference_task_id，且不生成新资产")
        if self.after_anchor and self.before_anchor:
            raise ValueError("一个图位置只选择一个前/后锚点，不能提交冲突定位")
        if self.decision == "none" and any((self.relation_kind, self.direction, self.anchor_hint)):
            raise ValueError("不画不声明图族、方向或位置提示")
        if self.production == "create" and self.relation_kind:
            family, direction = RELATION_FAMILIES[self.relation_kind]
            if self.diagram_type != family:
                raise ValueError(f"{self.relation_kind}关系应采用{family}；复杂分支请明确为dependency，不能强塞交接卡片")
            if self.relation_kind == "handoff" and self.direction not in {None, direction}:
                raise ValueError("线性交接stage_flow使用right方向")
        return self

    def blueprint_binding(self) -> dict:
        """Paragraph positions are refined later and do not create new authorizations."""
        return self.model_dump(mode="json", exclude={"after_anchor", "before_anchor", "anchor_hint"})


def validate_task_set(tasks: list[IllustrationTask], section_ids: set[str]) -> None:
    if {task.section_id for task in tasks} != section_ids:
        raise ValueError("政策2须逐小节明确用图/不用图决定，不能增加或漏掉小节")
    seen_ids = set()
    seen_views = set()
    for task in tasks:
        peers = [item for item in tasks if item.section_id == task.section_id]
        if task.decision == "none" and len(peers) != 1:
            raise ValueError("同一小节不能同时声明不画和多个图任务")
        if task.task_id:
            if task.task_id in seen_ids:
                raise ValueError("图任务task_id必须唯一，不能重复授权")
            seen_ids.add(task.task_id)
        if task.production == "create":
            view = tuple(re.sub(r"\s+", "", value).casefold()
                         for value in (task.read_question, task.viewpoint))
            if view in seen_views:
                raise ValueError("同义读图问题/视角不能改ID获得新的调用额度")
            seen_views.add(view)


def approved_illustration_tasks(workspace):
    """Read the one approved blueprint, never an agent-supplied count or registry."""
    from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint

    path, _metadata, blueprint = require_current_submitted_blueprint(workspace)
    tasks = [(chapter.chapter_number, task) for chapter in blueprint.chapters
             for task in chapter.illustration_tasks]
    digest = sha256(path.read_bytes()).hexdigest()
    from knowledge_product_workflow.change_impact import binding_policy_revision, semantic_digest
    if binding_policy_revision(workspace) == 2:
        digest = semantic_digest({"topic": blueprint.topic, "audience": blueprint.target_audience,
            "shared": blueprint.shared_context.model_dump(mode="json") if blueprint.shared_context else None,
            "native_tasks": [{"chapter": c.chapter_number, "scope": c.scope,
                "task": t.blueprint_binding()} for c in blueprint.chapters for t in c.illustration_tasks
                if t.production == "create" and t.backend == "host-image"]})
    return blueprint.illustration_policy_revision, digest, tasks


def require_diagram_task(workspace, chapter_number, semantic):
    """Bind the diagram to a current teaching task, independently of the primary artifact."""
    from knowledge_product_workflow.content_plan import require_current_content_plan
    from knowledge_product_workflow.workflows.authoring import (
        require_current_chapter_contract_in_view as require_current_chapter_contract,
    )

    _path, chapter = require_current_chapter_contract(workspace, chapter_number)
    if chapter.illustration_policy_revision == 1:
        if semantic is not None and semantic.task_id is not None:
            raise ValueError("图task_id属于政策2，不能绕过旧书的采纳流程")
        return None
    if semantic is None or semantic.task_id is None:
        raise ValueError("政策2图语义须绑定当前已批准task_id")
    plan = require_current_content_plan(workspace, chapter_number)
    if plan is None:
        raise ValueError("政策2须先提交当前章节内容计划，不能绕过图任务授权")
    match = [(section, task) for section in plan.sections for task in section.illustration_tasks
             if task.task_id == semantic.task_id and task.production == "create"]
    if len(match) != 1:
        raise ValueError("图任务未获当前内容计划的制作授权")
    section, task = match[0]
    if task.backend != semantic.backend or task.diagram_type != semantic.diagram_type:
        raise ValueError("图后端/图型与批准任务不一致；先受控修订，不能静默替换")
    from knowledge_product_workflow.presentation import _read_profile, publication_profile_path
    path = publication_profile_path(workspace)
    if path.is_file() and _read_profile(path).schema_version >= 4:
        expected = task.direction or ("right" if task.diagram_type in {"stage_flow", "sequence"} else "down")
        if semantic.direction != expected:
            raise ValueError(f"图方向与批准任务不一致：应为{expected}；需要其他方向须在蓝图明确声明")
    if semantic.heading_anchor is None or semantic.heading_anchor.split()[0] != task.section_id:
        raise ValueError("图标题位置与批准任务所属小节不一致")
    return section, task


def paragraph_anchors(text: str) -> list[dict]:
    """Locate top-level paragraph/example blocks; IDs survive controlled image insertions."""
    found = []
    section = None
    start = None
    offset = 0
    fence = None
    div_depth = 0

    def flush(end):
        nonlocal start
        if start is not None and section is not None:
            block = text[start:end].strip()
            if block and not block.startswith("!["):
                key = sha256((section + "\n" + block).encode()).hexdigest()[:16]
                found.append({"anchor": "block-" + key, "section_id": section,
                              "start": start, "end": end,
                              "preview": block.splitlines()[0][:100]})
        start = None

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = None
                flush(offset + len(line))
            offset += len(line)
            continue
        if div_depth:
            if stripped.startswith(":::"):
                div_depth += -1 if stripped == ":::" else 1
                if div_depth == 0:
                    flush(offset + len(line))
            offset += len(line)
            continue
        heading = re.match(r"^#{1,6}\s+(\d+(?:\.\d+)+)\s+", line)
        if stripped.startswith("#"):
            flush(offset)
            section = heading.group(1) if heading else None
        elif marker or stripped.startswith(":::"):
            flush(offset)
            start = offset
            if marker:
                fence = marker.group(1)
            else:
                div_depth = 1
        elif not stripped or stripped.startswith("!["):
            flush(offset)
        elif start is None and section is not None:
            start = offset
        offset += len(line)
    flush(len(text))
    counts = {item["anchor"]: sum(other["anchor"] == item["anchor"] for other in found) for item in found}
    return [item for item in found if counts[item["anchor"]] == 1]


def resolve_task_anchor(task: IllustrationTask, text: str, saved: dict | None = None, *, rebind: bool = False) -> dict:
    """Resolve one semantic location; explicit/stale/ambiguous cases never guess."""
    anchors = [a for a in paragraph_anchors(text) if a["section_id"] == task.section_id]
    key = task.after_anchor or task.before_anchor
    after = task.before_anchor is None
    if not key and saved and not rebind:
        key, after = saved.get("anchor"), saved.get("after", True)
    if key:
        chosen = [a for a in anchors if a["anchor"] == key]
    else:
        plain = [a for a in anchors if not a["preview"].startswith(("```", "~~~", "::: ", "|", "- "))]
        if task.anchor_hint:
            chosen = [a for a in plain if task.anchor_hint in text[a["start"]:a["end"]]]
        else:
            chosen = [a for a in plain if re.search(r"下图|下方.{0,8}图|这张图|图中", a["preview"])]
            if not chosen and plain:
                chosen = [plain[0]] if task.placement == "orientation" else [plain[-1]] if task.placement == "synthesis" else plain
    if len(chosen) != 1:
        raise ValueError("正文位置锚点缺失/已变/不唯一；首次用anchor_hint澄清；已保存位置变更使用--rebind-position，不重绘图")
    return {**chosen[0], "after": after}


def task_reference_findings(workspace, chapter_number: int, body: str):
    """Verify fulfillment separately from the teaching decision and budget state."""
    from knowledge_product_workflow.content_plan import require_current_content_plan
    from knowledge_product_workflow.diagrams import _IMAGE, DiagramInput
    from knowledge_product_workflow.quality import QualityFinding, Severity

    plan = require_current_content_plan(workspace, chapter_number)
    expected = {task.task_id: task for section in plan.sections for task in section.illustration_tasks
                if task.production == "create"}
    observed = set()
    findings = []

    def fail(code, message):
        findings.append(QualityFinding(code=code, severity=Severity.BLOCKER,
                                       location=f"chapters/chapter-{chapter_number:02d}.md", message=message))

    for reference in _IMAGE.finditer(body):
        path = workspace.root / "runs/diagrams" / f"{reference.group('id')}.input.json"
        try:
            semantic = DiagramInput.model_validate_json(path.read_bytes())
            require_diagram_task(workspace, chapter_number, semantic)
        except (OSError, ValueError) as error:
            fail("ILLUSTRATION_TASK_UNREGISTERED", f"图引用未绑定当前制作任务：{error}")
            continue
        if semantic.task_id in observed:
            fail("ILLUSTRATION_TASK_DUPLICATED", "同一任务被重复绘制/嵌入，不能靠不同diagram_id重画")
        observed.add(semantic.task_id)
    for task_id in expected.keys() - observed:
        fail("ILLUSTRATION_TASK_UNMET", f"已选择制作的图任务{task_id}未满足；不能因预算不足改成不需要图")
    return findings
