"""Version-bound chapter content plans for reader-task and artifact coverage."""

from __future__ import annotations

import json
import os
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.illustration_tasks import IllustrationTask, validate_task_set
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.presentation import (
    PRESENTATION_PROFILE_ID,
    V5_PRESENTATION_PROFILE_ID,
    PublicationProfile,
    publication_profile_path,
)
from knowledge_product_workflow.workflows.authoring import (
    ChapterContractRequiredError,
    require_current_chapter_contract,
)
from knowledge_product_workflow.workflows.curriculum import BlueprintChapter, SectionOutlineItem
from knowledge_product_workflow.workspace import Workspace

CONTENT_PLAN_SCHEMA_VERSION = 1
_BLOCK_KINDS = frozenset(
    {
        "explanation",
        "example",
        "comparison",
        "decision",
        "procedure",
        "exercise",
        "source",
        "diagram",
        "table",
        "worksheet",
    }
)


class ContentPlanError(ValueError):
    """Raised when a chapter content plan is absent, stale, or semantically invalid."""


class ContentPlanSection(BaseModel):
    """One reader-visible section's intended job and content form."""

    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(pattern=r"^\d+(?:\.\d+)+$")
    illustration_policy_revision: int = Field(default=1, ge=1, le=2)
    illustration_tasks: list[IllustrationTask] = Field(default_factory=list)
    reader_job: str = Field(min_length=8, max_length=240)
    opening_mode: Literal["claim", "decision", "scenario", "procedure", "reference"]
    block_sequence: list[str] = Field(min_length=1,
                                      json_schema_extra={"items": {"type": "string", "enum": sorted(_BLOCK_KINDS)}})
    artifact: str = Field(min_length=1, max_length=120)
    artifact_kind: Literal["code", "derivation", "decision"] | None = None
    asset_decision: Literal["none", "diagram", "table", "worksheet"]
    asset_rationale: str = Field(min_length=1, max_length=240)

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        value = handler(self)
        if self.artifact_kind is None:
            value.pop("artifact_kind", None)
        if self.illustration_policy_revision == 1:
            value.pop("illustration_policy_revision", None)
            value.pop("illustration_tasks", None)
        return value

    @field_validator("reader_job", "artifact", "asset_rationale")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("内容计划文本字段不能为空")
        return normalized

    @field_validator("block_sequence")
    @classmethod
    def require_known_blocks(cls, value: list[str]) -> list[str]:
        unknown = set(value) - _BLOCK_KINDS
        if unknown:
            raise ValueError(f"内容块类型不受支持：{', '.join(sorted(unknown))}")
        return value

    @model_validator(mode="after")
    def require_asset_reasoning(self) -> ContentPlanSection:
        if self.illustration_policy_revision == 1:
            if len(self.block_sequence) > 8 or len(set(self.block_sequence)) != len(self.block_sequence):
                raise ValueError("旧政策内容块最多8种，且不能重复同一种块")
            if self.illustration_tasks:
                raise ValueError("旧政策不接收新图任务")
        else:
            validate_task_set(self.illustration_tasks, {self.section_id})
            count = sum(task.production == "create" for task in self.illustration_tasks)
            if self.block_sequence.count("diagram") != count:
                raise ValueError("有序diagram实例数必须与实际制作任务对应")
        if self.asset_decision == "none":
            if self.artifact != "none":
                if self.artifact_kind is None:
                    raise ValueError("非图表读者产物须声明artifact_kind；否则artifact必须为none")
                required = {"code": {"example", "procedure"}, "derivation": {"explanation", "example"},
                            "decision": {"decision", "comparison", "procedure", "explanation"}}[self.artifact_kind]
                if not required.intersection(self.block_sequence):
                    raise ValueError("非图表产物与内容块不一致")
            elif self.artifact_kind is not None:
                raise ValueError("没有读者产物时不能声明artifact_kind")
            return self
        if self.artifact == "none":
            raise ValueError("选择读者资产时必须声明可复用 artifact")
        if self.asset_decision not in self.block_sequence:
            raise ValueError("asset_decision 必须出现在 block_sequence 中")
        return self


class ContentPlanInput(BaseModel):
    """The only host-writable semantic input for a chapter content plan."""

    model_config = ConfigDict(extra="forbid")

    sections: list[ContentPlanSection] = Field(min_length=1)
    illustration_policy_revision: int = Field(default=1, ge=1, le=2)

    @model_validator(mode="before")
    @classmethod
    def propagate_policy(cls, value):
        if isinstance(value, dict) and value.get("illustration_policy_revision") == 2:
            value = dict(value)
            if not isinstance(value.get("sections"), list):
                return value
            value["sections"] = [dict(section, illustration_policy_revision=2)
                                 if isinstance(section, dict) and "illustration_policy_revision" not in section
                                 else section for section in value.get("sections", [])]
        return value

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        value = handler(self)
        if self.illustration_policy_revision == 1:
            value.pop("illustration_policy_revision", None)
        return value

    @model_validator(mode="after")
    def consistent_policy(self):
        if any(section.illustration_policy_revision != self.illustration_policy_revision
               for section in self.sections):
            raise ValueError("内容计划与小节插图政策不一致")
        return self


def content_plan_schema() -> dict[str, object]:
    """Expose the actual input model plus its chapter-level business rules."""
    return {
        "schema_version": CONTENT_PLAN_SCHEMA_VERSION,
        "input_schema": ContentPlanInput.model_json_schema(),
        "rules": [
            "sections 必须按当前章节合约的小节树顺序完整覆盖；section_id 必须是加引号的字符串。",
            "asset_decision 为 none 时 artifact 可为 none，或明确声明 code/derivation/decision 的非图表产物；选择资产时 artifact 非 none，且该类型在 block_sequence 中。",
            "仅当前合约required_units含input_output_artifact或template时必须有可复用工件；其他章可全部none，但显式承诺仍须兑现并实际审核。",
            "政策1内容块类型不得重复且最多8种；政策2按图任务有序实例核对，不按图数配额。",
            "reader_job 和 asset_rationale 必须是已填写的具体文本。",
            "代码与公式属于 example/explanation 块，不使用 code/formula 类型；完整枚举在 input_schema 中。",
        ],
        "example": {
            "sections": [{
                "section_id": "1.1",
                "reader_job": "读者能用对照表比较两种方案并选出适用条件",
                "opening_mode": "decision",
                "block_sequence": ["explanation", "table"],
                "artifact": "方案选择对照表",
                "asset_decision": "table",
                "asset_rationale": "并排展示条件与取舍，便于复用。",
            }],
        },
    }


class ContentPlan(ContentPlanInput):
    """Internal proof that binds an approved semantic plan to the current contract."""

    schema_version: int = Field(default=CONTENT_PLAN_SCHEMA_VERSION, ge=1)
    chapter_number: int = Field(gt=0)
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def content_plan_path(workspace: Workspace, chapter_number: int) -> Path:
    return workspace.root / "chapters" / f"chapter-{chapter_number:02d}-content-plan.yaml"


def _template_path(workspace: Workspace, chapter_number: int) -> Path:
    return workspace.root / "runs" / "templates" / f"chapter-{chapter_number:02d}-content-plan.template.yaml"


def _context_path(workspace: Workspace, chapter_number: int) -> Path:
    return workspace.root / "runs" / "templates" / f"chapter-{chapter_number:02d}-content-plan.context.json"


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _flatten_outline(items: list[SectionOutlineItem]) -> list[tuple[str, str]]:
    flattened: list[tuple[str, str]] = []
    for item in items:
        flattened.append((item.number, item.title))
        flattened.extend(_flatten_outline(item.children))
    return flattened


def _requires_content_plan(workspace: Workspace) -> bool:
    from knowledge_product_workflow.workflows.curriculum import parse_blueprint_submission
    blueprint_path = workspace.root / "planning/curriculum-blueprint.md"
    if blueprint_path.is_file():
        _meta, body = parse_markdown_artifact(blueprint_path.read_text(encoding="utf-8"))
        blueprint = parse_blueprint_submission(body)
        if blueprint is not None and blueprint.illustration_policy_revision == 2:
            return True
    path = publication_profile_path(workspace)
    if not path.is_file():
        return False
    try:
        profile = PublicationProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return False
    return profile.profile_id in {PRESENTATION_PROFILE_ID, V5_PRESENTATION_PROFILE_ID} and profile.content_contract_enabled


def create_content_plan_template(workspace: Workspace, chapter_number: int) -> tuple[Path, Path]:
    """Create one semantic template tied to the current approved chapter contract."""
    contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
    output = _template_path(workspace, chapter_number)
    context = _context_path(workspace, chapter_number)
    if output.exists():
        try:
            previous = json.loads(context.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        if (previous.get("chapter_number") == chapter_number
                and previous.get("contract_sha256") == _sha256_file(contract_path)):
            return output, context
        raise ContentPlanError(
            f"内容计划模板上下文已过期或缺少合同绑定：{output}；已保留填写内容。"
            f"请读取当前章节合约与 kp content-plan-schema，将语义内容校对到新的工作区内 YAML，"
            f"再运行 kp submit-content-plan {chapter_number} --workspace <工作区> --from <新YAML>。"
        )
    sections = [
        {
            "section_id": number,
            "reader_job": f"<读者在 {title} 获得的判断、能力或产物>",
            "opening_mode": "claim",
            "block_sequence": ["explanation", "example"],
            "artifact": "none",
            "asset_decision": "none",
            "asset_rationale": "不需要额外资产；散文可直接降低理解成本。",
        }
        for number, title in _flatten_outline(chapter.section_outline)
    ]
    payload = {"sections": sections}
    if chapter.illustration_policy_revision == 2:
        payload["illustration_policy_revision"] = 2
        for section in sections:
            tasks = [task.model_dump(mode="json") for task in chapter.illustration_tasks
                     if task.section_id == section["section_id"]]
            section["illustration_tasks"] = tasks
            for task in tasks:
                if task["production"] == "create":
                    section["block_sequence"].append("diagram")
    template_text = (
        "# 未完成模板：只按当前合约的input_output_artifact/template承诺强制读者工件；其他解释可全部none。\n"
        "# 非图表产物可用artifact_kind=code/derivation/decision，asset_decision=none；必须在正文真实出现。\n"
        "# reader_job说明本节新增的判断/动作，避免与其他节同义复述；把占位替换为实际内容。\n"
        + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    )
    _atomic_write(output, template_text)
    _atomic_write(
        context,
        json.dumps(
            {
                "chapter_number": chapter_number,
                "contract": str(contract_path.relative_to(workspace.root)).replace("\\", "/"),
                "contract_sha256": _sha256_file(contract_path),
                "expected_sections": [
                    {"section_id": number, "title": title}
                    for number, title in _flatten_outline(chapter.section_outline)
                ],
                "allowed_block_kinds": sorted(_BLOCK_KINDS),
                "note": "这是未完成模板。按当前合约明确承诺选择工件，解释无需强造资产；只填语义字段，不填章节号、哈希、合约版本或内部路径。",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    workspace.append_event("chapter_content_plan_template_created", {"chapter_number": chapter_number})
    return output, context


def _load_input(path: Path) -> ContentPlanInput:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ContentPlanError("章节内容计划输入必须是有效 UTF-8 YAML") from error
    if not isinstance(raw, dict):
        raise ContentPlanError("章节内容计划输入必须是 YAML 对象")
    forbidden = {"schema_version", "chapter_number", "contract_sha256"} & set(raw)
    if forbidden:
        raise ContentPlanError(f"章节内容计划输入不能填写内部字段：{', '.join(sorted(forbidden))}")
    try:
        return ContentPlanInput.model_validate(raw)
    except ValueError as error:
        raise ContentPlanError(f"章节内容计划字段无效：{error}") from error


def _requires_reader_artifact(chapter: BlueprintChapter) -> bool:
    return bool({"input_output_artifact", "template"}.intersection(chapter.required_units))


def _validate_plan_sections(plan: ContentPlanInput, chapter: BlueprintChapter) -> None:
    if plan.illustration_policy_revision != chapter.illustration_policy_revision:
        raise ContentPlanError("内容计划与批准蓝图插图政策不一致")
    if chapter.illustration_policy_revision == 2:
        approved = {(task.section_id, task.task_id): task.blueprint_binding()
                    for task in chapter.illustration_tasks}
        detailed = {(task.section_id, task.task_id): task.blueprint_binding()
                    for section in plan.sections for task in section.illustration_tasks}
        if detailed != approved:
            raise ContentPlanError("图任务与当前批准蓝图不一致；新增/改范围须先修订蓝图")
    unfinished = [section.section_id for section in plan.sections if "<" in section.reader_job or ">" in section.reader_job]
    if unfinished:
        raise ContentPlanError(f"内容计划模板未完成：请填写小节 {', '.join(unfinished)} 的 reader_job")
    expected = [number for number, _ in _flatten_outline(chapter.section_outline)]
    actual = [section.section_id for section in plan.sections]
    if actual != expected:
        raise ContentPlanError("内容计划必须按章节合约的小节树顺序逐项覆盖，不可增加、跳过或重排小节")
    if _requires_reader_artifact(chapter) and all(section.artifact == "none" for section in plan.sections):
        raise ContentPlanError("当前章节合约至少需要一个读者可复用 artifact，不能以说明图代替并全部声明为 none")


def submit_content_plan(workspace: Workspace, chapter_number: int, input_path: Path, *, replace_draft: bool = False) -> Path:
    """Freeze a semantic content plan only when it covers the current chapter contract."""
    resolved = input_path.expanduser().resolve()
    if not resolved.is_file() or not resolved.is_relative_to(workspace.root.resolve()):
        raise ContentPlanError("章节内容计划输入必须位于当前书稿工作区内")
    try:
        contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
    except ChapterContractRequiredError as error:
        raise ContentPlanError(str(error)) from error
    plan = _load_input(resolved)
    _validate_plan_sections(plan, chapter)
    output = content_plan_path(workspace, chapter_number)
    if output.exists():
        chapter_path = workspace.root / "chapters" / f"chapter-{chapter_number:02d}.md"
        metadata, _ = parse_markdown_artifact(chapter_path.read_text(encoding="utf-8"))
        events = [json.loads(line) for line in (workspace.root / "runs" / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        revisions = [event["payload"] for event in events if event.get("event_type") == "chapter_amendment_started"
                     and event["payload"].get("chapter_number") == chapter_number]
        latest = revisions[-1] if revisions else {}
        archive = (workspace.root / latest.get("archive", "missing") / output.name).resolve()
        if replace_draft:
            if metadata.status not in {ArtifactStatus.DRAFT, ArtifactStatus.IN_PRODUCTION}:
                raise ContentPlanError("已提交章节须先amend-chapter，不能使用草稿计划替换")
            from knowledge_product_workflow.runtime import capture_orchestration_action
            capture_orchestration_action(workspace,"submit-chapter",chapter_number=chapter_number)
            old_hash = _sha256_file(output)
            history=workspace.root/"runs/content-plan-history"/f"chapter-{chapter_number:02d}-{old_hash}.yaml"
            _atomic_write(history, output.read_text(encoding="utf-8"))
            workspace.append_event("chapter_content_plan_draft_revision",{"chapter_number":chapter_number,"previous_sha256":old_hash,"archive":history.relative_to(workspace.root).as_posix()})
        elif (metadata.status is not ArtifactStatus.IN_PRODUCTION
                or latest.get("new_draft_version") != metadata.version
                or not archive.is_relative_to(workspace.root.resolve()) or not archive.is_file()
                or archive.read_bytes() != output.read_bytes()):
            raise FileExistsError(f"章节内容计划已存在：{output}；请先发起受控章节修订，保留原计划归档后再替换。")
    frozen = ContentPlan(
        chapter_number=chapter_number,
        contract_sha256=_sha256_file(contract_path),
        **plan.model_dump(),
    )
    _atomic_write(output, yaml.safe_dump(frozen.model_dump(mode="json"), allow_unicode=True, sort_keys=False))
    workspace.append_event(
        "chapter_content_plan_submitted",
        {"chapter_number": chapter_number, "content_plan": str(output.relative_to(workspace.root)).replace("\\", "/")},
    )
    return output


def require_current_content_plan(workspace: Workspace, chapter_number: int) -> ContentPlan | None:
    """Return the plan for v5 workspaces, preserving v4 workspaces without migration."""
    if not _requires_content_plan(workspace):
        return None
    path = content_plan_path(workspace, chapter_number)
    if not path.is_file():
        raise ContentPlanError("缺少当前章节内容计划；请先生成并提交内容计划。")
    try:
        plan = ContentPlan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
        raise ContentPlanError("章节内容计划无效") from error
    if plan.chapter_number != chapter_number:
        raise ContentPlanError("章节内容计划的章节号不一致")
    contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
    if plan.contract_sha256 != _sha256_file(contract_path):
        raise ContentPlanError("章节内容计划已过期：它不再绑定当前章节合约。")
    _validate_plan_sections(plan, chapter)
    return plan
