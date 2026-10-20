"""Version-bound chapter content plans for reader-task and artifact coverage."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath
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

from knowledge_product_workflow.change_impact import proof_contract_digest, proof_contract_matches
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.illustration_tasks import IllustrationTask, validate_task_set
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.presentation import (
    PRESENTATION_PROFILE_ID,
    V5_PRESENTATION_PROFILE_ID,
    PublicationProfile,
    publication_profile_path,
)
from knowledge_product_workflow.production_dependencies import ChapterDependency
from knowledge_product_workflow.verification_context import (
    atomic_replace as _v_atomic_replace,
)
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import (
    load_yaml as _v_load_yaml,
)
from knowledge_product_workflow.verification_context import memoized_read as _memoized_read
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
from knowledge_product_workflow.verification_context import verified_read as _verification_read
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workflows.authoring import (
    ChapterContractRequiredError,
)
from knowledge_product_workflow.workflows.authoring import (
    require_current_chapter_contract_in_view as require_current_chapter_contract,
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


def _asset_resource_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value.startswith("assets/") or "\\" in value or ":" in value or "\x00" in value
            or path.as_posix() != value or any(part in {".", ".."} for part in path.parts)
            or len(path.parts) < 2):
        raise ValueError("资源须使用当前工作区assets/内的规范相对文件路径")
    return value


class CodeExcerpt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body_fence_index: int = Field(gt=0, strict=True)
    start_line: int = Field(gt=0, strict=True)
    end_line: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def ordered_range(self):
        if self.end_line < self.start_line:
            raise ValueError("片段末行不得早于首行")
        return self


class CodeDeliveryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["inline_complete", "excerpt_with_resource", "resource_only"]
    resource_path: str | None = None
    input_files: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=8, max_length=400)
    excerpts: list[CodeExcerpt] = Field(default_factory=list)

    @field_validator("resource_path")
    @classmethod
    def canonical_resource(cls, value):
        return _asset_resource_path(value) if value is not None else None

    @field_validator("input_files")
    @classmethod
    def canonical_inputs(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("资源输入文件不得重复")
        return [_asset_resource_path(path) for path in value]

    @model_validator(mode="after")
    def delivery_shape(self):
        if not self.reason.strip():
            raise ValueError("须说明本节的代码呈现理由")
        if self.mode == "inline_complete":
            if self.resource_path or self.input_files or self.excerpts:
                raise ValueError("完整内嵌模式不接收资源或片段映射")
        elif self.resource_path is None:
            raise ValueError("外置模式必须声明完整资源路径")
        elif self.resource_path in self.input_files:
            raise ValueError("主程序不重复列入输入文件")
        if (self.mode == "excerpt_with_resource") != bool(self.excerpts):
            raise ValueError("只有片段加资源模式须有非空片段映射")
        indices = [part.body_fence_index for part in self.excerpts]
        if len(indices) != len(set(indices)):
            raise ValueError("同节正文围栏不能绑定两个来源范围")
        return self


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
    artifact: str = Field(min_length=1, max_length=120,
        description="无读者产物用none；选择图表资产时须填实际可复用产物；非图表产物须配artifact_kind。")
    artifact_kind: Literal["code", "prompt", "derivation", "decision"] | None = Field(default=None,
        description="非图表产物的类型：code需example或procedure；derivation需explanation或example；decision需decision/comparison/procedure/explanation之一。artifact为none时不填。")
    asset_decision: Literal["none", "diagram", "table", "worksheet"] = Field(
        description="none表示不做图表资产，artifact可为none或配artifact_kind的非图表产物；其余选择须出现在block_sequence且artifact不能为none。")
    asset_rationale: str = Field(min_length=1, max_length=240)
    production_dependencies: list[ChapterDependency] | None = None
    code_delivery: CodeDeliveryPlan | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        value = handler(self)
        if self.production_dependencies is None:
            value.pop("production_dependencies", None)
        if self.artifact_kind is None:
            value.pop("artifact_kind", None)
        if self.code_delivery is None:
            value.pop("code_delivery", None)
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
        if self.code_delivery is not None and self.artifact_kind != "code":
            raise ValueError("代码呈现只适用于明确的程序产物")
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
                required = {"code": {"example", "procedure"}, "prompt": {"example", "procedure", "exercise"}, "derivation": {"explanation", "example"},
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
            "新教学呈现政策区分prompt与程序：完整提示不要求运行器；code须声明code_delivery，资源片段不冒充独立完整程序。旧书须先显式采用Brief教学取向。",
            "sections 必须按当前章节合约的小节树顺序完整覆盖；section_id 必须是加引号的字符串。",
            "asset_decision 为 none 时 artifact 可为 none，或声明当前input_schema支持的非图表工件；完整提示可用prompt，不要求程序。选择资产时artifact非none，且该类型在block_sequence中。",
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
    return sha256(_v_read_bytes(path)).hexdigest()


def _atomic_write(path: Path, payload: str) -> None:
    _v_mkdir(path.parent,parents=True,exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _v_write_text(temporary, payload, encoding="utf-8")
        _v_atomic_replace(temporary, path)
    finally:
        if _v_exists(temporary):
            _v_unlink(temporary)


def _flatten_outline(items: list[SectionOutlineItem]) -> list[tuple[str, str]]:
    flattened: list[tuple[str, str]] = []
    for item in items:
        flattened.append((item.number, item.title))
        flattened.extend(_flatten_outline(item.children))
    return flattened


@_memoized_read
def _requires_content_plan(workspace: Workspace) -> bool:
    # Teaching delivery is a content contract, independent of when a host
    # first selects its PDF presentation profile (including Markdown-only books).
    if teaching_focus(workspace) is not None:
        return True
    from knowledge_product_workflow.workflows.curriculum import parse_blueprint_submission
    blueprint_path = workspace.root / "planning/curriculum-blueprint.md"
    if _v_is_file(blueprint_path):
        _meta, body = parse_markdown_artifact(_v_read_text(blueprint_path, encoding="utf-8"))
        blueprint = parse_blueprint_submission(body)
        if blueprint is not None and blueprint.illustration_policy_revision == 2:
            return True
    path = publication_profile_path(workspace)
    if not _v_is_file(path):
        return False
    try:
        profile = PublicationProfile.model_validate(_v_load_yaml(_v_read_text(path, encoding="utf-8")))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return False
    return profile.profile_id in {PRESENTATION_PROFILE_ID, V5_PRESENTATION_PROFILE_ID} and profile.content_contract_enabled


def create_content_plan_template(workspace: Workspace, chapter_number: int) -> tuple[Path, Path]:
    """Create one semantic template tied to the current approved chapter contract."""
    contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
    output = _template_path(workspace, chapter_number)
    context = _context_path(workspace, chapter_number)
    if _v_exists(output):
        try:
            previous = json.loads(_v_read_text(context, encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        if (previous.get("chapter_number") == chapter_number
                and previous.get("contract_sha256") == proof_contract_digest(workspace, chapter_number, "content_plan")):
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
    if chapter.production_dependencies is not None:
        for section in sections:
            section["production_dependencies"] = [
                d.model_dump(mode="json") for d in chapter.production_dependencies
                if d.section_id == section["section_id"]
            ]
    if chapter.illustration_policy_revision == 2:
        payload["illustration_policy_revision"] = 2
        for section in sections:
            tasks = [task.model_dump(mode="json") for task in chapter.illustration_tasks
                     if task.section_id == section["section_id"]]
            section["illustration_tasks"] = tasks
            for task in tasks:
                if task["production"] == "create":
                    section["block_sequence"].append("diagram")
    focus = teaching_focus(workspace)
    artifact_note = (
        "# 教学取向已在Brief确认：提示工件用prompt；程序用code并声明code_delivery，完整资源不必贴满正文。\n"
        if focus else "# 非图表产物可用artifact_kind=code/derivation/decision，asset_decision=none；必须在正文真实出现。\n"
    )
    template_text = (
        "# 未完成模板：只按当前合约的input_output_artifact/template承诺强制读者工件；其他解释可全部none。\n"
        + artifact_note
        + "# reader_job说明本节新增的判断/动作，避免与其他节同义复述；把占位替换为实际内容。\n"
        "# asset_decision非none时artifact须命名实际可复用产物；非图产物的artifact_kind与block_sequence须匹配，完整约束见context的section_input_schema。\n"
        + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    )
    _atomic_write(output, template_text)
    _atomic_write(
        context,
        json.dumps(
            {
                "chapter_number": chapter_number,
                "section_input_schema": ContentPlanSection.model_json_schema(),
                "contract": str(contract_path.relative_to(workspace.root)).replace("\\", "/"),
                "contract_sha256": proof_contract_digest(workspace, chapter_number, "content_plan"),
                "expected_sections": [
                    {"section_id": number, "title": title}
                    for number, title in _flatten_outline(chapter.section_outline)
                ],
                "allowed_block_kinds": sorted(_BLOCK_KINDS),
                **({"teaching_focus": focus, "code_delivery_schema": CodeDeliveryPlan.model_json_schema()} if focus else {}),
                "note": "这是未完成模板。按当前合约明确承诺选择工件，解释无需强造资产；只填语义字段，不填章节号、哈希、合约版本或内部路径。",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    workspace.append_event("chapter_content_plan_template_created", {"chapter_number": chapter_number})
    return output, context


def _load_input(path: Path, *, data: bytes | None = None) -> ContentPlanInput:
    try:
        text = _v_read_text(path, encoding="utf-8") if data is None else data.decode("utf-8")
        raw = _v_load_yaml(text)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
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


@dataclass(frozen=True)
class PreparedContentPlanInput:
    """Validated request bytes; the model is derived, never supplied independently."""

    path: Path
    data: bytes
    _model_json: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", bytes(self.data))
        object.__setattr__(self, "path", self.path.resolve())
        object.__setattr__(self, "_model_json", _load_input(self.path, data=self.data).model_dump_json())

    def require_unchanged(self, path: Path) -> ContentPlanInput:
        if path.resolve() != self.path or path.read_bytes() != self.data:
            raise ContentPlanError("章节内容计划输入在预检后改变；保留原请求，重新核对后提交")
        return ContentPlanInput.model_validate_json(self._model_json)


def preflight_content_plan_input(workspace: Workspace, input_path: Path) -> PreparedContentPlanInput:
    """Cheap read-only rejection before whole-book orchestration inspection."""
    from knowledge_product_workflow.runtime import require_workflow_mutation_allowed

    require_workflow_mutation_allowed(workspace, "submit-content-plan")
    path = input_path.expanduser().resolve()
    if not _v_is_file(path) or not path.is_relative_to(workspace.root.resolve()):
        raise ContentPlanError("章节内容计划输入必须位于当前书稿工作区内")
    return PreparedContentPlanInput(path, _v_read_bytes(path))


def _requires_reader_artifact(chapter: BlueprintChapter) -> bool:
    return bool({"input_output_artifact", "template"}.intersection(chapter.required_units))


def teaching_focus(workspace: Workspace) -> str | None:
    from knowledge_product_workflow.workflows.product_brief import parse_brief_semantics
    brief = workspace.root / "planning/product-brief.md"
    if not _v_is_file(brief):
        return None
    _, body = parse_markdown_artifact(_v_read_text(brief, encoding="utf-8"))
    return parse_brief_semantics(body).teaching_focus


def _validate_code_delivery_policy(workspace: Workspace, plan: ContentPlanInput) -> None:
    focus = teaching_focus(workspace)
    for section in plan.sections:
        if focus is None and (section.code_delivery is not None or section.artifact_kind == "prompt"):
            raise ContentPlanError("先在Brief显式确认教学呈现，再采用提示/资源分层政策；旧书不自动迁移")
        if focus is not None and section.artifact_kind == "code" and section.code_delivery is None:
            raise ContentPlanError(f"小节{section.section_id}须说明程序保留正文还是配套资源，不默认贴完整运行器")


def _validate_plan_sections(plan: ContentPlanInput, chapter: BlueprintChapter) -> None:
    if chapter.production_dependencies is not None:
        expected_dependencies = [d.model_dump(mode="json") for d in chapter.production_dependencies]
        actual_dependencies = [d.model_dump(mode="json") for s in plan.sections
                               for d in s.production_dependencies or []]
        if actual_dependencies != expected_dependencies:
            raise ContentPlanError("写作前小节依赖必须与批准蓝图一致；不得省略或扩大依赖")
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


@_verification_read(transaction_view=True)
def submit_content_plan(workspace: Workspace, chapter_number: int, input_path: Path, *, replace_draft: bool = False,
                        prepared_input: PreparedContentPlanInput | None = None) -> Path:
    """Freeze a semantic content plan only when it covers the current chapter contract."""
    from knowledge_product_workflow.runtime import require_workflow_mutation_allowed
    require_workflow_mutation_allowed(workspace, "submit-content-plan")
    resolved = input_path.expanduser().resolve()
    if not _v_is_file(resolved) or not resolved.is_relative_to(workspace.root.resolve()):
        raise ContentPlanError("章节内容计划输入必须位于当前书稿工作区内")
    prepared = prepared_input or preflight_content_plan_input(workspace, resolved)
    plan = prepared.require_unchanged(resolved)
    try:
        contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
    except ChapterContractRequiredError as error:
        raise ContentPlanError(str(error)) from error
    _validate_plan_sections(plan, chapter)
    _validate_code_delivery_policy(workspace, plan)
    output = content_plan_path(workspace, chapter_number)
    if _v_exists(output):
        chapter_path = workspace.root / "chapters" / f"chapter-{chapter_number:02d}.md"
        metadata, _ = parse_markdown_artifact(_v_read_text(chapter_path, encoding="utf-8"))
        events = [json.loads(line) for line in _v_read_text(workspace.root / "runs" / "events.jsonl", encoding="utf-8").splitlines() if line.strip()]
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
            _atomic_write(history, _v_read_text(output, encoding="utf-8"))
            workspace.append_event("chapter_content_plan_draft_revision",{"chapter_number":chapter_number,"previous_sha256":old_hash,"archive":history.relative_to(workspace.root).as_posix()})
        elif (metadata.status is not ArtifactStatus.IN_PRODUCTION
                or latest.get("new_draft_version") != metadata.version
                or not archive.is_relative_to(workspace.root.resolve()) or not _v_is_file(archive)
                or _v_read_bytes(archive) != _v_read_bytes(output)):
            raise FileExistsError(f"章节内容计划已存在：{output}；请先发起受控章节修订，保留原计划归档后再替换。")
    frozen = ContentPlan(
        chapter_number=chapter_number,
        contract_sha256=proof_contract_digest(workspace, chapter_number, "content_plan"),
        **plan.model_dump(),
    )
    prepared.require_unchanged(resolved)
    _atomic_write(output, yaml.safe_dump(frozen.model_dump(mode="json"), allow_unicode=True, sort_keys=False))
    workspace.append_event(
        "chapter_content_plan_submitted",
        {"chapter_number": chapter_number, "contract_audit_sha256": _sha256_file(contract_path), "content_plan": str(output.relative_to(workspace.root)).replace("\\", "/")},
    )
    return output


def require_current_content_plan(workspace: Workspace, chapter_number: int) -> ContentPlan | None:
    """Return the plan for v5 workspaces, preserving v4 workspaces without migration."""
    if not _requires_content_plan(workspace):
        return None
    path = content_plan_path(workspace, chapter_number)
    if not _v_is_file(path):
        raise ContentPlanError("缺少当前章节内容计划；请先生成并提交内容计划。")
    try:
        plan = ContentPlan.model_validate(_v_load_yaml(_v_read_text(path, encoding="utf-8")))
    except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
        raise ContentPlanError("章节内容计划无效") from error
    if plan.chapter_number != chapter_number:
        raise ContentPlanError("章节内容计划的章节号不一致")
    _contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
    if not proof_contract_matches(workspace, chapter_number, plan.contract_sha256, "content_plan", path):
        raise ContentPlanError("章节内容计划已过期：它不再绑定当前章节合约。")
    _validate_plan_sections(plan, chapter)
    _validate_code_delivery_policy(workspace, plan)
    return plan
