"""Curriculum blueprint submission and budget allocation contracts."""

import json
import re
import unicodedata
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)

from knowledge_product_workflow.detail_constraints import (
    ChapterBudget,
    ChapterSpec,
    DetailConstraints,
    PageConstraintMode,
    allocate_budgets,
)
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.illustration_tasks import (
    RELATION_FAMILIES,
    IllustrationTask,
    validate_task_set,
)
from knowledge_product_workflow.models import ArtifactMetadata, ArtifactStatus
from knowledge_product_workflow.production_dependencies import (
    ChapterDependency,
    ChapterExport,
    SharedContext,
    dependency_plan_from_chapters,
)
from knowledge_product_workflow.user_decisions import UserDecisionTarget
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
    write_text as _v_write_text,
)
from knowledge_product_workflow.workflows.product_brief import (
    brief_contract_fingerprints,
    brief_target_audience,
    brief_topic,
    parse_detail_constraints,
)
from knowledge_product_workflow.workspace import Workspace


class CurriculumPrerequisiteError(ValueError):
    """Raised when curriculum generation starts before brief approval."""


class BlueprintSubmissionError(ValueError):
    """Raised when a host-generated blueprint cannot enter the controlled workflow."""


class TeachingLengthRecommendation(BaseModel):
    """Topic-specific teaching-length advice, bound to the Brief it evaluates."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    brief_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    brief_version: int = Field(ge=1)
    recommended_min_pages: int = Field(gt=0)
    recommended_max_pages: int = Field(gt=0)
    confidence: str = Field(pattern=r"^(low|medium|high)$")
    rationale: list[str] = Field(min_length=1)
    scope_expansion_units: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_range(self) -> "TeachingLengthRecommendation":
        if self.recommended_min_pages > self.recommended_max_pages:
            raise ValueError("推荐页数下限不能大于上限")
        if any(not unit.strip() for unit in self.scope_expansion_units):
            raise ValueError("范围扩展教学单元不能为空")
        return self


class ChapterArchetype(StrEnum):
    """Teaching forms that keep a book from repeating one chapter template."""

    CONCEPT = "concept"
    CASE_STUDY = "case_study"
    WORKSHOP = "workshop"
    STRATEGY = "strategy"
    REFERENCE = "reference"
    APPENDIX = "appendix"


class ProductMode(StrEnum):
    """Reader-product modes with additional structural constraints."""

    GENERAL = "general"
    INTERVIEW = "interview"


class InterviewAnswerPolicy(BaseModel):
    """Budget complete interview answer demonstrations instead of scripting every section."""

    total_complete_answer_limit: int = Field(ge=0)
    chapter_complete_answer_limits: dict[int, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_budget(self) -> "InterviewAnswerPolicy":
        if any(chapter_number <= 0 or limit < 0 for chapter_number, limit in self.chapter_complete_answer_limits.items()):
            raise ValueError("章节完整示范回答预算必须使用正章节号和非负数量")
        if sum(self.chapter_complete_answer_limits.values()) > self.total_complete_answer_limit:
            raise ValueError("章节完整示范回答预算不得超过全书示范回答预算")
        return self


_ARCHETYPE_ALLOWED_UNITS: dict[ChapterArchetype, frozenset[str]] = {
    ChapterArchetype.CONCEPT: frozenset({"concept", "mechanism", "example", "reflection", "summary"}),
    ChapterArchetype.CASE_STUDY: frozenset(
        {"scenario", "evidence_boundary", "decision_process", "retrospective", "exercise"}
    ),
    ChapterArchetype.WORKSHOP: frozenset(
        {"procedure", "input_output_artifact", "acceptance_method", "exercise", "common_mistakes"}
    ),
    ChapterArchetype.STRATEGY: frozenset(
        {
            "decision_framework",
            "tradeoffs",
            "action_plan",
            "answer_framework",
            "complete_answer_demo",
            "evidence_slot",
            "common_mistakes",
            "exercise",
        }
    ),
    ChapterArchetype.REFERENCE: frozenset({"reference_entries", "glossary", "checklist"}),
    ChapterArchetype.APPENDIX: frozenset({"template", "glossary", "reference_entries"}),
}
_ARCHETYPE_REQUIRED_UNITS: dict[ChapterArchetype, frozenset[str]] = {
    ChapterArchetype.CONCEPT: frozenset({"concept"}),
    ChapterArchetype.CASE_STUDY: frozenset(
        {"scenario", "evidence_boundary", "decision_process", "retrospective"}
    ),
    ChapterArchetype.WORKSHOP: frozenset(
        {"procedure", "input_output_artifact", "acceptance_method"}
    ),
    ChapterArchetype.STRATEGY: frozenset({"decision_framework"}),
    ChapterArchetype.REFERENCE: frozenset({"reference_entries"}),
    ChapterArchetype.APPENDIX: frozenset(),
}
_ARCHETYPE_LABELS: dict[ChapterArchetype, str] = {
    ChapterArchetype.CONCEPT: "概念讲解",
    ChapterArchetype.CASE_STUDY: "案例研究",
    ChapterArchetype.WORKSHOP: "实践工作坊",
    ChapterArchetype.STRATEGY: "策略与决策",
    ChapterArchetype.REFERENCE: "参考资料",
    ChapterArchetype.APPENDIX: "附录",
}
_UNIT_LABELS: dict[str, str] = {
    "acceptance_method": "验收方法",
    "action_plan": "行动计划",
    "answer_framework": "答题框架",
    "checklist": "检查清单",
    "common_mistakes": "常见误区",
    "complete_answer_demo": "完整示范回答",
    "concept": "核心概念",
    "decision_framework": "决策框架",
    "decision_process": "决策过程",
    "evidence_boundary": "证据边界",
    "evidence_slot": "证据槽位",
    "example": "具体示例",
    "exercise": "练习",
    "glossary": "术语表",
    "input_output_artifact": "输入与产出物",
    "mechanism": "运行机制",
    "procedure": "操作步骤",
    "reference_entries": "参考条目",
    "reflection": "反思",
    "retrospective": "复盘",
    "scenario": "情境",
    "summary": "小结",
    "template": "模板",
    "tradeoffs": "权衡分析",
}

_SECTION_NUMBER = re.compile(r"^\d+(?:\.\d+)+$")
_MARKDOWN_HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")


class SectionOutlineItem(BaseModel):
    """One reader-visible numbered heading declared by an approved blueprint."""

    model_config = ConfigDict(extra="forbid")

    number: str = Field(min_length=3)
    title: str = Field(min_length=1)
    children: list["SectionOutlineItem"] = Field(default_factory=list)

    @field_validator("number", mode="before")
    @classmethod
    def require_string_number(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError(  # noqa: TRY004 - Pydantic turns this input error into a validation error.
                '小节编号 number 必须是字符串；YAML 请写 number: "1.1"，不能写未加引号的 1.1'
            )
        return value


def _normalized_outline_title(title: str) -> str:
    return unicodedata.normalize("NFC", title).strip()


def _validate_section_outline(chapter_number: int, items: list[SectionOutlineItem]) -> None:
    """Require an exact depth-first, continuous numbered heading tree."""
    if not items:
        raise ValueError("每章必须声明至少一个编号小节 section_outline")

    def visit(children: list[SectionOutlineItem], parent: tuple[int, ...]) -> None:
        for index, item in enumerate(children, start=1):
            if not _SECTION_NUMBER.fullmatch(item.number):
                raise ValueError(f"小节编号格式无效：{item.number}")
            actual = tuple(int(part) for part in item.number.split("."))
            expected = parent + (index,)
            expected_text = ".".join(map(str, expected))
            if actual != expected:
                raise ValueError(
                    f"小节编号必须按父级从 1 连续递增；期望 {expected_text}，实际 {item.number}"
                )
            if len(actual) > 6:
                raise ValueError(f"小节编号层级不能超过 Markdown 的 6 级：{item.number}")
            if not _normalized_outline_title(item.title):
                raise ValueError(f"小节标题不能为空：{item.number}")
            visit(item.children, actual)

    visit(items, (chapter_number,))


def curriculum_outline_schema() -> dict[str, object]:
    """Return the public, stable contract accepted by curriculum submissions."""
    known_units = set().union(*_ARCHETYPE_ALLOWED_UNITS.values())
    archetypes: dict[str, object] = {}
    for archetype in ChapterArchetype:
        allowed = sorted(_ARCHETYPE_ALLOWED_UNITS[archetype])
        defaults = sorted(_ARCHETYPE_REQUIRED_UNITS[archetype])
        archetypes[archetype.value] = {
            "label": _ARCHETYPE_LABELS[archetype],
            "allowed_units": allowed,
            "default_required_units": defaults,
            "disallowed_units": sorted(known_units - set(allowed)),
            "unit_labels": {unit: _UNIT_LABELS[unit] for unit in allowed},
        }
    return {
        "schema_version": 2,
        "submission_schema": BlueprintSubmission.model_json_schema(),
        "submission_notes": [
            "submission_schema由实际输入模型生成；以下体裁/编号/绑定规则仍由业务校验执行。",
            "shared_context.cases是案例ID到对象的映射，值不能是字符串；对象内按本书案例填写。",
            "word_min/word_target/word_max及批准状态由系统派生，不作为蓝图输入填写。",
            "production_policy_revision=1须显式声明shared_context、每章production_dependencies和production_exports；空列表表示无依赖，省略表示未知。",
        ],
        "section_outline": {
            "required_for_new_submissions": True,
            "description": "读者可见的编号小节树；编号、标题、层级和顺序批准后冻结。",
            "item": {
                "number": "字符串，例如 3.1 或 3.1.1；同级必须从 1 连续递增",
                "title": "读者可见的小节标题，按 NFC 和首尾空白归一化后精确匹配正文",
                "children": "子小节数组；可省略或使用 []",
            },
        },
        "chapter_archetypes": archetypes,
        "product_modes": {
            ProductMode.GENERAL.value: "通用知识教程",
            ProductMode.INTERVIEW.value: "面试指导产品；完整示范回答受配额限制",
        },
        "claim_ids": {
            "pattern": r"^claim-[a-z0-9][a-z0-9-]*$",
            "unique": True,
            "description": "事实声明标识须使用小写claim-前缀；实际来源核验另行提交。",
        },
        "illustration_tasks": {
            "policy": "政策2须覆盖全部section_outline编号；政策1不接受新图任务。",
            "item_schema": IllustrationTask.model_json_schema(),
            "none_example": {
                "section_id": "1.1",
                "decision": "none",
                "alternative_rationale": "本节用有序列表即可表达操作顺序，没有需要图形组织的关系。",
            },
            "relation_constraints": {
                "applies_when": {"production": "create", "relation_kind": "declared"},
                "families": {
                    relation: {
                        "diagram_type": family,
                        "recommended_direction": direction,
                        "allowed_directions": (
                            [None, direction] if relation == "handoff" else [None, "right", "down"]
                        ),
                    }
                    for relation, (family, direction) in RELATION_FAMILIES.items()
                },
                "direction_rule": (
                    "recommended_direction是方向建议；只有handoff硬限为未填或right，"
                    "其他关系允许未填、right或down。"
                ),
            },
            "decision_rules": [
                "none只填写section_id、decision和alternative_rationale，不声明task_id或图型。",
                "required/useful须声明task_id、trigger、read_question、viewpoint和visual_gain。",
                "create须声明diagram_type和placement；reference须声明reference_task_id。",
                "production=create且声明relation_kind时，diagram_type须精确匹配relation_constraints；"
                "未声明关系及none/reference/omit沿用原规则。",
                "同节可有互补制作任务；none须独占本节，任务标识和制作视角不能重复。",
            ],
        },
    }


def _template_schema_comments() -> str:
    schema = curriculum_outline_schema()
    archetypes = schema["chapter_archetypes"]
    assert isinstance(archetypes, dict)
    lines = [
        "# 课程蓝图填写说明：仅填写当前主题的章节，不生成通用章节。",
        "# 每章必须填写 section_outline：它是批准后冻结的读者可见标题树，正文不得自行改名、跳号或增删。",
        "# 教学单元公开契约：完整机器可读清单请运行 kp outline-schema。",
        "# required_units 可省略或填写 []；提交时会按章节体裁补入默认必需单元。",
        "# 若显式填写 required_units/optional_units/prohibited_units，只能使用下列英文标识。",
        "#",
        "# 提交前必须满足的绑定规则（违反会在 kp submit-outline 报错，请勿踩坑）：",
        "# - topic / target_audience / audience_level 必须与已批准 Brief 完全一致；本模板已预填，请勿改动。",
        "# - Brief 声明页数目标时，必须先由 kp-curriculum-architect 提交教学篇幅评估",
        "#   （kp submit-length-recommendation），否则 kp submit-outline 会拒绝。",
        "# - 若用户页数超过教学建议上限，必须在本蓝图顶层填写 scope_expansion_units（非重复的新教学单元），",
        "#   否则会以“不能以重复内容填充”拒绝。",
        "# - 每章 weight 决定按教学权重的章节预算分配；Brief 只给页数时，CLI 会按页数估算字数额门。",
        "# - 本章 word 预算（word_min/word_target/word_max）由 CLI 按 weight 派生并写入章节合约，",
        "#   正文写作须对照该预算，避免交付阶段页数不达标。",
        "# - claim_ids须为唯一的小写claim-标识，例如claim-skill-format。",
        "# - shared_context.cases的每个值必须是对象，例如 cases: {case-main: {input: '待分类资料', expected: '有效'}}；不能写案例ID到字符串的映射。",
        "# - 并行依赖政策1须显式填写共享设定和各章依赖/提供列表；这些字段不解锁未经资格验证的正式并行。",
        "# - 政策2的illustration_tasks必须覆盖section_outline的全部编号，包括不用图的小节。",
        "#   不用图填写section_id、decision: none、alternative_rationale；不填写task_id或图型。",
        "#   下方none仅是结构示例，须按实际教学问题逐节判断；完整字段见kp outline-schema。",
        "# - production=create 且声明 relation_kind 时，diagram_type 必须匹配下列关系映射。",
        "#   只有 handoff 的 direction 硬限为未填或 right；其他关系的方向只是建议，允许未填、right 或 down。",
        "#   未声明关系及 none/reference/omit 沿用原规则，不自动补写或修改作者字段。",
    ]
    illustration_schema = schema["illustration_tasks"]
    assert isinstance(illustration_schema, dict)
    relation_constraints = illustration_schema["relation_constraints"]
    for relation, constraint in relation_constraints["families"].items():
        lines.append(
            f"#   {relation} -> diagram_type: {constraint['diagram_type']}；"
            f"建议 direction: {constraint['recommended_direction']}"
        )
    for archetype in ChapterArchetype:
        payload = archetypes[archetype.value]
        assert isinstance(payload, dict)
        allowed = ", ".join(payload["allowed_units"])
        defaults = ", ".join(payload["default_required_units"]) or "无"
        lines.extend(
            [
                f"# - {archetype.value}（{payload['label']}）",
                f"#   allowed: {allowed}",
                f"#   default_required: {defaults}",
            ]
        )
    return "\n".join(lines) + "\n\n"


class BlueprintChapter(BaseModel):
    """One host-designed chapter with a constrained teaching form."""

    chapter_number: int = Field(gt=0)
    title: str = Field(min_length=1)
    learning_objectives: list[str] = Field(default_factory=list)
    scope: str = Field(min_length=1)
    weight: float = Field(gt=0)
    rationale: str = Field(min_length=1)
    chapter_archetype: ChapterArchetype
    required_units: list[str] = Field(default_factory=list)
    optional_units: list[str] = Field(default_factory=list)
    prohibited_units: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    section_outline: list[SectionOutlineItem] = Field(default_factory=list)
    illustration_policy_revision: int = Field(default=1, ge=1, le=2)
    illustration_tasks: list[IllustrationTask] = Field(default_factory=list)

    production_dependencies: list[ChapterDependency] | None = None
    production_exports: list[ChapterExport] | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        value = handler(self)
        if self.production_dependencies is None:
            value.pop("production_dependencies", None)
        if self.production_exports is None:
            value.pop("production_exports", None)
        if self.illustration_policy_revision == 1:
            value.pop("illustration_policy_revision", None)
            value.pop("illustration_tasks", None)
        return value

    @model_validator(mode="after")
    def validate_teaching_form(self) -> "BlueprintChapter":
        if self.chapter_archetype not in {ChapterArchetype.REFERENCE, ChapterArchetype.APPENDIX} and not self.learning_objectives:
            raise ValueError("非参考或附录章节必须至少声明一个学习目标")
        if not self.required_units:
            self.required_units = sorted(_ARCHETYPE_REQUIRED_UNITS[self.chapter_archetype])
        collections = (self.required_units, self.optional_units, self.prohibited_units)
        if any(any(not unit.strip() for unit in units) for units in collections):
            raise ValueError("教学单元不能为空")
        combined = [unit for units in collections for unit in units]
        if len(combined) != len(set(combined)):
            raise ValueError("教学单元不能同时出现在必需、可选或禁止列表中")
        allowed = _ARCHETYPE_ALLOWED_UNITS[self.chapter_archetype]
        active_units = set(self.required_units) | set(self.optional_units)
        unknown = sorted(active_units - allowed)
        if unknown:
            allowed_text = ", ".join(sorted(allowed))
            defaults = ", ".join(sorted(_ARCHETYPE_REQUIRED_UNITS[self.chapter_archetype])) or "无"
            raise ValueError(
                f"体裁 {self.chapter_archetype.value} 不允许教学单元: {', '.join(unknown)}；"
                f"可选值：{allowed_text}；默认必需单元：{defaults}"
            )
        known_units = set().union(*_ARCHETYPE_ALLOWED_UNITS.values())
        unknown_prohibited = sorted(set(self.prohibited_units) - known_units)
        if unknown_prohibited:
            raise ValueError(f"禁止教学单元不存在: {', '.join(unknown_prohibited)}")
        missing = _ARCHETYPE_REQUIRED_UNITS[self.chapter_archetype] - set(self.required_units)
        if missing:
            raise ValueError(
                f"体裁 {self.chapter_archetype.value} 缺少必需教学单元: {', '.join(sorted(missing))}"
            )
        if len(self.claim_ids) != len(set(self.claim_ids)) or any(
            not re.fullmatch(r"claim-[a-z0-9][a-z0-9-]*", claim) for claim in self.claim_ids
        ):
            raise ValueError("Claim 清单必须使用唯一的小写 claim- 标识")
        if self.section_outline:
            _validate_section_outline(self.chapter_number, self.section_outline)
        if self.illustration_policy_revision == 2:
            def numbers(items):
                return {item.number for item in items} | set().union(
                    *(numbers(item.children) for item in items)) if items else set()
            validate_task_set(self.illustration_tasks, numbers(self.section_outline))
        elif self.illustration_tasks:
            raise ValueError("旧政策不接收新图任务；先受控采纳政策2")
        return self


class BlueprintSubmission(BaseModel):
    """The only host-generated curriculum payload accepted by the CLI."""

    schema_version: int = Field(default=2, ge=1)
    brief_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    brief_content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    brief_version: int = Field(ge=1)
    topic: str = Field(min_length=1)
    target_audience: str = Field(min_length=1)
    audience_level: str = Field(min_length=1)
    product_mode: ProductMode = ProductMode.GENERAL
    interview_answer_policy: InterviewAnswerPolicy | None = None
    scope_expansion_units: list[str] = Field(default_factory=list)
    chapters: list[BlueprintChapter] = Field(min_length=1)
    illustration_policy_revision: int = Field(default=1, ge=1, le=2)
    production_policy_revision: int = Field(default=0, ge=0, le=1)
    shared_context: SharedContext | None = None

    @model_validator(mode="before")
    @classmethod
    def propagate_illustration_policy(cls, value):
        if isinstance(value, dict) and value.get("illustration_policy_revision") == 2:
            value = dict(value)
            if not isinstance(value.get("chapters"), list):
                return value
            if any(isinstance(chapter, dict) and chapter.get("illustration_policy_revision", 2) != 2
                   for chapter in value["chapters"]):
                raise ValueError("蓝图与章节插图政策不一致")
            value["chapters"] = [dict(chapter, illustration_policy_revision=2)
                                 if isinstance(chapter, dict) and "illustration_policy_revision" not in chapter
                                 else chapter for chapter in value.get("chapters", [])]
        return value

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        value = handler(self)
        if self.production_policy_revision == 0:
            value.pop("production_policy_revision", None)
            value.pop("shared_context", None)
        if self.illustration_policy_revision == 1:
            value.pop("illustration_policy_revision", None)
        return value

    @model_validator(mode="after")
    def validate_chapters_and_mode(self) -> "BlueprintSubmission":
        if self.production_policy_revision == 1:
            if self.shared_context is None or any(
                c.production_dependencies is None or c.production_exports is None
                for c in self.chapters
            ):
                raise ValueError("并行蓝图必须在写作前明确公共设定、依赖及提供清单；None不等于无依赖")
            for chapter in self.chapters:
                def section_numbers(items):
                    return {i.number for i in items} | set().union(
                        *(section_numbers(i.children) for i in items)
                    )
                sections = section_numbers(chapter.section_outline)
                if any(d.section_id not in sections for d in chapter.production_dependencies):
                    raise ValueError("依赖使用小节不在批准小节树中")
                if any(e.section_id not in sections for e in chapter.production_exports):
                    raise ValueError("输出提供小节不在批准小节树中")
            dependency_plan_from_chapters(
                [c.model_dump(mode="json") for c in self.chapters], sample_approved=True
            )
        if any(chapter.illustration_policy_revision != self.illustration_policy_revision
               for chapter in self.chapters):
            raise ValueError("蓝图与章节插图政策版本必须一致")
        if self.illustration_policy_revision == 2:
            previous = set()
            created = set()
            views = set()
            for chapter in self.chapters:
                for task in chapter.illustration_tasks:
                    if task.task_id and task.task_id in previous:
                        raise ValueError("全书task_id不能重复")
                    if task.production == "reference" and task.reference_task_id not in created:
                        raise ValueError("只能指引回看此前已经规划的图任务")
                    if task.production == "create":
                        view = ("".join(task.read_question.split()), "".join(task.viewpoint.split()))
                        if view in views:
                            raise ValueError("全书同义图任务重复，优先引用已有图")
                        views.add(view)
                        created.add(task.task_id)
                    if task.task_id:
                        previous.add(task.task_id)
        numbers = [chapter.chapter_number for chapter in self.chapters]
        if len(numbers) != len(set(numbers)):
            raise ValueError("章节编号必须唯一")
        if numbers != sorted(numbers):
            raise ValueError("章节必须按编号升序提交")
        if self.product_mode is ProductMode.INTERVIEW and self.interview_answer_policy is None:
            raise ValueError("面试类产品必须声明完整示范回答策略")
        if self.product_mode is ProductMode.GENERAL and self.interview_answer_policy is not None:
            raise ValueError("非面试产品不得声明完整示范回答策略")
        if self.interview_answer_policy is not None:
            unknown = set(self.interview_answer_policy.chapter_complete_answer_limits) - set(numbers)
            if unknown:
                raise ValueError(f"示范回答预算引用了不存在的章节: {', '.join(map(str, sorted(unknown)))}")
            if any(
                "complete_answer_demo" in chapter.required_units + chapter.optional_units
                and chapter.chapter_archetype is not ChapterArchetype.STRATEGY
                for chapter in self.chapters
            ):
                raise ValueError("完整示范回答只能用于策略型章节")
        elif any(
            "complete_answer_demo" in chapter.required_units + chapter.optional_units
            for chapter in self.chapters
        ):
            raise ValueError("非面试产品不得使用完整示范回答单元")
        if len(self.chapters) >= 3 and len({chapter.chapter_archetype for chapter in self.chapters}) == 1:
            normalized_rationales = {"".join(chapter.rationale.split()) for chapter in self.chapters}
            if len(normalized_rationales) == 1:
                raise ValueError("全书使用同一章节体裁时，每章必须提供不同且具体的教学取舍理由")
        if self.schema_version >= 2:
            missing_outlines = [
                str(chapter.chapter_number) for chapter in self.chapters if not chapter.section_outline
            ]
            if missing_outlines:
                raise ValueError(
                    "当前蓝图版本要求每章填写 section_outline；缺少章节: "
                    + ", ".join(missing_outlines)
                )
        return self


def teaching_length_recommendation_path(workspace: Workspace) -> Path:
    """Return the current architecture-owned teaching-length recommendation path."""
    return workspace.root / "planning" / "teaching-length-recommendation.yaml"


def _current_length_recommendation(
    workspace: Workspace, brief_path: Path, metadata: ArtifactMetadata, brief_body: str
) -> TeachingLengthRecommendation | None:
    path = teaching_length_recommendation_path(workspace)
    if not _v_is_file(path):
        return None
    try:
        raw = _v_load_yaml(_v_read_text(path, encoding="utf-8"))
        recommendation = TeachingLengthRecommendation.model_validate(raw)
    except (OSError, ValueError, yaml.YAMLError):
        return None
    fingerprint = brief_contract_fingerprints(brief_body)["content_contract_sha256"]
    if (
        recommendation.brief_content_sha256 != fingerprint
        or recommendation.brief_version != metadata.version
    ):
        return None
    return recommendation


def create_teaching_length_recommendation_template(workspace: Workspace) -> tuple[Path, Path]:
    """Create the semantic template the curriculum architect uses before blueprinting."""
    brief_path = workspace.root / "planning" / "product-brief.md"
    if not _v_is_file(brief_path):
        raise CurriculumPrerequisiteError("生成教学篇幅评估前必须先创建并批准产品 Brief")
    metadata, brief_body = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
    if metadata.status is not ArtifactStatus.APPROVED:
        raise CurriculumPrerequisiteError("生成教学篇幅评估前必须先批准产品 Brief")
    fingerprint = brief_contract_fingerprints(brief_body)["content_contract_sha256"]
    template = workspace.root / "planning" / "teaching-length-recommendation.template.yaml"
    context = workspace.root / "planning" / "teaching-length-recommendation.context.yaml"
    _v_write_text(template,
        yaml.safe_dump(
            {
                "schema_version": 1,
                "brief_content_sha256": fingerprint,
                "brief_version": metadata.version,
                "recommended_min_pages": None,
                "recommended_max_pages": None,
                "confidence": "medium",
                "rationale": [],
                "scope_expansion_units": [],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    constraints = parse_detail_constraints(brief_body)
    _v_write_text(context,
        yaml.safe_dump(
            {
                "topic": brief_topic(brief_body),
                "target_audience": brief_target_audience(brief_body),
                "audience_level": constraints.audience_level.value if constraints else None,
                "page_constraint": constraints.page_constraint.model_dump(mode="json") if constraints and constraints.page_constraint else None,
                "instruction": "基于教学单元、案例、练习、代码和图表密度给出建议；不要为凑页数制造同义小节。",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    workspace.append_event("teaching_length_recommendation_template_created", {})
    return template, context


def submit_teaching_length_recommendation(workspace: Workspace, input_path: Path) -> Path:
    """Freeze the curriculum architect's page recommendation for the active Brief."""
    brief_path = workspace.root / "planning" / "product-brief.md"
    if not _v_is_file(brief_path):
        raise CurriculumPrerequisiteError("提交教学篇幅评估前必须先创建并批准产品 Brief")
    metadata, brief_body = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
    if metadata.status is not ArtifactStatus.APPROVED:
        raise CurriculumPrerequisiteError("提交教学篇幅评估前必须先批准产品 Brief")
    try:
        source = input_path.expanduser().resolve()
        source.relative_to(workspace.root)
        raw = _v_load_yaml(_v_read_text(source, encoding="utf-8"))
        recommendation = TeachingLengthRecommendation.model_validate(raw)
    except ValidationError as error:
        fields = "；".join(
            f"{'.'.join(map(str, item['loc'])) or '推荐区间'}：{item['msg']}（{item['type']}）"
            for item in error.errors(include_input=False, include_url=False)[:10]
        )
        raise BlueprintSubmissionError(
            f"教学篇幅评估输入无效；字段：{fields}。"
            "保留当前模板的brief_content_sha256和brief_version，修正所列字段后重交；"
            "修改文件名或查其他书稿不能修复字段错误。"
        ) from error
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise BlueprintSubmissionError("教学篇幅评估输入无效") from error
    fingerprint = brief_contract_fingerprints(brief_body)["content_contract_sha256"]
    if recommendation.brief_content_sha256 != fingerprint or recommendation.brief_version != metadata.version:
        raise BlueprintSubmissionError("教学篇幅评估与当前 Brief 不匹配")
    output = teaching_length_recommendation_path(workspace)
    _v_write_text(output,
        yaml.safe_dump(recommendation.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    workspace.append_event("teaching_length_recommendation_submitted", {})
    return output


_BUDGET_BLOCK = re.compile(
    r"^## (?:章节预算|Detail budgets)\s*\n+```yaml\s*\n(?P<yaml>.*?)\n```",
    re.MULTILINE | re.DOTALL,
)
_BLUEPRINT_CONTRACT_BLOCK = re.compile(
    r"^## (?:蓝图契约|Blueprint contract)\s*\n+```yaml\s*\n(?P<yaml>.*?)\n```",
    re.MULTILINE | re.DOTALL,
)


def parse_chapter_budgets(
    body: str, constraints: DetailConstraints | None = None
) -> dict[int, ChapterBudget]:
    """Read budget entries persisted by the curriculum workflow."""
    match = _BUDGET_BLOCK.search(body)
    if match is None:
        return {}
    raw = _v_load_yaml(match.group("yaml"))
    if not isinstance(raw, dict) or not isinstance(raw.get("chapter_budgets"), list):
        raise TypeError("章节预算格式不正确")
    budgets = [ChapterBudget.model_validate(item) for item in raw["chapter_budgets"]]
    chapter_numbers = [budget.chapter_number for budget in budgets]
    if len(chapter_numbers) != len(set(chapter_numbers)):
        raise ValueError("章节预算中的章节编号必须唯一")
    if constraints is not None:
        totals = {
            "word_min": constraints.word_min,
            "word_target": constraints.word_target,
            "word_max": constraints.word_max,
        }
        for field_name, expected in totals.items():
            if expected is None:
                continue
            actual = sum(getattr(budget, field_name) or 0 for budget in budgets)
            if actual != expected:
                raise ValueError(
                    f"章节预算的 {field_name} 总和为 {actual}，与 Brief 约束 {expected} 不一致"
                )
    return {budget.chapter_number: budget for budget in budgets}


def parse_blueprint_submission(body: str) -> BlueprintSubmission | None:
    """Read the controlled host submission stored with a curriculum blueprint."""
    match = _BLUEPRINT_CONTRACT_BLOCK.search(body)
    if match is None:
        return None
    try:
        raw = _v_load_yaml(match.group("yaml"))
    except yaml.YAMLError as error:
        raise BlueprintSubmissionError(f"蓝图契约 YAML 无法解析：{error}") from error
    if not isinstance(raw, dict) or "blueprint" not in raw:
        raise BlueprintSubmissionError("蓝图契约格式不正确")
    try:
        return BlueprintSubmission.model_validate(raw["blueprint"])
    except ValueError as error:
        raise BlueprintSubmissionError(f"蓝图契约字段无效：{error}") from error


def _load_submission(path: Path) -> BlueprintSubmission:
    """Load a host payload from YAML or JSON without accepting positional terminal input."""
    try:
        raw_text = _v_read_text(path, encoding="utf-8")
    except OSError as error:
        raise BlueprintSubmissionError(f"无法读取蓝图提交文件：{error}") from error
    try:
        raw = json.loads(raw_text) if path.suffix.casefold() == ".json" else _v_load_yaml(raw_text)
    except (json.JSONDecodeError, yaml.YAMLError) as error:
        raise BlueprintSubmissionError(f"蓝图提交必须是有效 YAML 或 JSON：{error}") from error
    if not isinstance(raw, dict):
        raise BlueprintSubmissionError("蓝图提交必须是一个对象")
    try:
        submission = BlueprintSubmission.model_validate(raw)
    except ValueError as error:
        raise BlueprintSubmissionError(f"蓝图提交字段无效：{error}") from error
    if submission.schema_version < 2:
        raise BlueprintSubmissionError(
            "新蓝图提交必须使用 schema_version: 2 并为每章提供 section_outline；请重新运行 kp outline。"
        )
    return submission


def _brief_topic(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    raise BlueprintSubmissionError("已批准 Brief 缺少主题标题")


def _brief_target_audience(body: str) -> str:
    match = re.search(
        r"^## (?:目标读者|Target readers)\s*\n+(?P<audience>.*?)(?=^## |\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise BlueprintSubmissionError("已批准 Brief 缺少目标读者")
    audience = match.group("audience").strip()
    if not audience:
        raise BlueprintSubmissionError("已批准 Brief 的目标读者不能为空")
    return audience


def _sha256_file(path: Path) -> str:
    return sha256(_v_read_bytes(path)).hexdigest()


def _validate_brief_binding(
    submission: BlueprintSubmission,
    brief_path: Path,
    metadata: ArtifactMetadata,
    brief_body: str,
) -> None:
    """Validate a new content-binding submission or a legacy full-file binding."""
    raw_sha256 = _sha256_file(brief_path)
    fingerprints = brief_contract_fingerprints(brief_body)
    content_sha256 = fingerprints["content_contract_sha256"]
    if submission.brief_content_sha256 is not None:
        if submission.brief_content_sha256 != content_sha256:
            raise BlueprintSubmissionError("课程蓝图提交与当前 Brief 的内容契约 SHA-256 不一致")
    elif submission.brief_sha256 != raw_sha256:
        raise BlueprintSubmissionError("课程蓝图提交与已批准 Brief 的 SHA-256 不一致")
    if submission.brief_version != metadata.version:
        raise BlueprintSubmissionError("课程蓝图提交与已批准 Brief 的版本不一致")
    if submission.topic != brief_topic(brief_body):
        raise BlueprintSubmissionError("课程蓝图主题必须与已批准 Brief 一致")
    if submission.target_audience != brief_target_audience(brief_body):
        raise BlueprintSubmissionError("课程蓝图目标读者必须与已批准 Brief 一致")


def _require_length_recommendation(
    workspace: Workspace,
    brief_path: Path,
    metadata: ArtifactMetadata,
    brief_body: str,
    submission: BlueprintSubmission,
) -> None:
    """Require real scope evidence before a hard page target can drive production."""
    constraints = parse_detail_constraints(brief_body)
    if constraints is None or constraints.page_constraint is None:
        return
    recommendation = _current_length_recommendation(workspace, brief_path, metadata, brief_body)
    if recommendation is None:
        raise BlueprintSubmissionError(
            "当前页数约束尚未获得教学篇幅评估；请先由课程架构生成并提交篇幅建议。"
        )
    page_constraint = constraints.page_constraint
    if page_constraint.mode not in {PageConstraintMode.EXACT, PageConstraintMode.RANGE}:
        return
    requested_upper = page_constraint.maximum or page_constraint.target
    if requested_upper is None or requested_upper <= recommendation.recommended_max_pages:
        return
    declared = set(submission.scope_expansion_units)
    required = set(recommendation.scope_expansion_units)
    if not required:
        raise BlueprintSubmissionError(
            "用户页数超过教学建议上限，但篇幅评估未列出可验证的范围扩展单元；不能以重复内容填充。"
        )
    if not required <= declared:
        raise BlueprintSubmissionError(
            "用户页数超过教学建议上限；课程蓝图必须包含篇幅评估声明的范围扩展教学单元。"
        )


def _normalize_amendment_binding(
    submission: BlueprintSubmission,
    brief_path: Path,
    metadata: ArtifactMetadata,
    brief_body: str,
) -> BlueprintSubmission:
    """Let the CLI, rather than the host, write Brief binding proof on revision."""
    fingerprints = brief_contract_fingerprints(brief_body)
    return submission.model_copy(
        update={
            "brief_sha256": _sha256_file(brief_path),
            "brief_content_sha256": fingerprints["content_contract_sha256"],
            "brief_version": metadata.version,
        }
    )


def _render_submission_contract(submission: BlueprintSubmission, language: str) -> str:
    heading = "蓝图契约" if language == "zh-CN" else "Blueprint contract"
    payload = {"blueprint": submission.model_dump(mode="json")}
    rendered = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).rstrip()
    return f"## {heading}\n\n```yaml\n{rendered}\n```\n"


def _render_learning_path(submission: BlueprintSubmission, language: str) -> str:
    heading = "学习路径" if language == "zh-CN" else "Learning path"
    rendered = []
    for chapter in submission.chapters:
        if language == "zh-CN":
            rendered.append(
                f"{chapter.chapter_number}. {chapter.title}（体裁：{chapter.chapter_archetype.value}；"
                f"取舍：{chapter.rationale}）"
            )
        else:
            rendered.append(
                f"{chapter.chapter_number}. {chapter.title} ({chapter.chapter_archetype.value}; {chapter.rationale})"
            )
        rendered.extend(_render_section_outline(chapter.section_outline, depth=1))
    return f"## {heading}\n\n" + "\n".join(rendered) + "\n"


def _render_section_outline(items: list[SectionOutlineItem], depth: int = 0) -> list[str]:
    lines: list[str] = []
    for item in items:
        lines.append(f"{'  ' * depth}- {item.number} {item.title}")
        lines.extend(_render_section_outline(item.children, depth + 1))
    return lines


def _write_curriculum_blueprint(
    workspace: Workspace,
    brief_path: Path,
    brief_metadata: ArtifactMetadata,
    brief_body: str,
    submission: BlueprintSubmission,
    output: Path,
    version: int,
    event_type: str,
    extra_outputs: dict[str, bytes | None] | None = None,
    extra_events: list | None = None,
) -> Path:
    """Write one validated blueprint revision at the canonical current path."""
    events = []
    constraints = parse_detail_constraints(brief_body)
    if constraints is not None and submission.audience_level != constraints.audience_level:
        raise BlueprintSubmissionError("课程蓝图受众级别必须与已批准 Brief 一致")
    language = constraints.language if constraints is not None else "zh-CN"
    try:
        chapter_specs = [
            ChapterSpec(
                chapter_number=chapter.chapter_number,
                weight=chapter.weight,
                rationale=chapter.rationale,
                required_units=chapter.required_units,
            )
            for chapter in submission.chapters
        ]
        budgets = allocate_budgets(chapter_specs, constraints) if constraints is not None else []
    except ValueError as error:
        raise BlueprintSubmissionError(f"BLUEPRINT_BUDGET_INVALID: 章节教学单元或篇幅预算无效：{error}") from error
    if (
        constraints is not None
        and constraints.word_min is None
        and constraints.production_page_target is not None
        and budgets
        and budgets[0].word_min is not None
    ):
        events.append((
            "word_budget_estimated",
            {
                "estimated_word_min": sum(budget.word_min or 0 for budget in budgets),
                "estimated_word_target": sum(budget.word_target or 0 for budget in budgets),
                "estimated_word_max": sum(budget.word_max or 0 for budget in budgets),
                "pages": constraints.production_page_target,
            },
        ))
    artifact = ArtifactMetadata(
        book_id=workspace.root.name,
        artifact_type="curriculum_blueprint",
        status=ArtifactStatus.READY_FOR_REVIEW,
        version=version,
        depends_on=[str(brief_path.relative_to(workspace.root))],
    )
    body = f"# {submission.topic}\n\n"
    body += _render_learning_path(submission, language)
    if submission.production_policy_revision == 1:
        body += "\n## 写作前依赖确认\n\n下列依赖与公共定义随本蓝图批准；结果依赖须等当前受控结果，接口与导航不等待正文。宿主资格未通过时保持串行。\n\n"
        for chapter in submission.chapters:
            dependencies = "；".join(f"{d.section_id}使用第{d.producer_chapter}章 {d.kind}：{d.export_id or '导航'}"
                                     for d in chapter.production_dependencies) or "已明确无跨章生产依赖"
            body += f"- 第{chapter.chapter_number}章：{dependencies}。\n"
    if submission.illustration_policy_revision == 2:
        from knowledge_product_workflow.illustration_budget import budget_path, load_image_budget
        made = [(chapter.chapter_number, task) for chapter in submission.chapters
                for task in chapter.illustration_tasks if task.production == "create"]
        native = {task.task_id for _chapter, task in made if task.backend == "host-image"}
        consumed = 0
        remaining = 2 * len(native)
        if _v_is_file(budget_path(workspace)):
            from knowledge_product_workflow.illustration_budget import _sent
            old = load_image_budget(workspace)
            consumed = sum(_sent(slot) for slot in old.slots)
            remaining = sum(max(0, 2 - sum(_sent(slot) for slot in old.slots
                                           if slot.task_id == task_id or task_id in slot.diagram_ids))
                            for task_id in native)
        body += (f"\n## 插图任务与成本预估\n\n拟制作{len(made)}幅教学图，其中原生生图任务{len(native)}个；"
                 f"已发送历史{consumed}次，当前可用尝试最多{remaining}次，拟冻结总发送上限{consumed + remaining}次。"
                 "图数由读者任务推导，每任务最多两次，串行；资产与成页质量仍须实际核验，不设文本token额度门，实际图像token未知。\n\n")
        for number, task in made:
            body += f"- 第{number}章 {task.section_id}：{task.read_question}（{task.viewpoint}；{'AI插图' if task.backend == 'host-image' else '确定性图'}）。\n"
    body += "\n" + _render_submission_contract(submission, language)
    if budgets:
        body += _render_budgets(budgets, language)
    from knowledge_product_workflow.user_decisions import prepare_decision_invalidation
    from knowledge_product_workflow.workspace_transactions import (
        commit_workspace_transaction,
        prepare_workspace_transaction,
    )
    outputs = dict(extra_outputs or {})
    outputs[output.relative_to(workspace.root).as_posix()] = render_markdown_artifact(artifact, body).encode("utf-8")
    decisions, decision_events = prepare_decision_invalidation(workspace, UserDecisionTarget.BLUEPRINT,
        reason="课程蓝图已创建或修订")
    outputs.update(decisions)
    events.extend(decision_events)
    events.append((event_type, {"artifact": str(output.relative_to(workspace.root)),
        "brief_sha256": submission.brief_sha256,
        "brief_content_sha256": submission.brief_content_sha256,
        "chapter_count": len(submission.chapters), "product_mode": submission.product_mode.value}))
    events.extend(extra_events or [])
    transaction = prepare_workspace_transaction(workspace, outputs, kind="blueprint_revision",
        scope_chapters=[c.chapter_number for c in submission.chapters], shared_paths=list(outputs),
        input_bindings={brief_path.relative_to(workspace.root).as_posix(): _sha256_file(brief_path)},
        events=events)
    commit_workspace_transaction(workspace, transaction)
    return output


def submit_curriculum_blueprint(workspace: Workspace, input_path: Path) -> Path:
    """Validate and freeze the first topic-specific host blueprint submission."""
    from knowledge_product_workflow.runtime import authorize_workflow_transition

    authorize_workflow_transition(workspace, "submit-outline")
    brief_path = workspace.root / "planning" / "product-brief.md"
    if not _v_is_file(brief_path):
        raise CurriculumPrerequisiteError("提交课程蓝图前必须先创建并批准产品 Brief")
    metadata, brief_body = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
    if metadata.status is not ArtifactStatus.APPROVED:
        raise CurriculumPrerequisiteError("提交课程蓝图前必须先批准产品 Brief")
    submission = _load_submission(input_path)
    _validate_brief_binding(submission, brief_path, metadata, brief_body)
    _require_length_recommendation(workspace, brief_path, metadata, brief_body, submission)
    output = workspace.root / "planning" / "curriculum-blueprint.md"
    if _v_exists(output):
        raise FileExistsError(
            f"课程蓝图已存在：{output}；请使用 kp amend-outline 创建受控修订，禁止删除后重建。"
        )
    return _write_curriculum_blueprint(
        workspace,
        brief_path,
        metadata,
        brief_body,
        submission,
        output,
        version=1,
        event_type="curriculum_submitted",
    )


def amend_curriculum_blueprint(workspace: Workspace, input_path: Path) -> Path:
    """Archive and replace a stale blueprint through an explicit revision command."""
    from knowledge_product_workflow.runtime import authorize_workflow_transition

    authorize_workflow_transition(workspace, "amend-outline")
    brief_path = workspace.root / "planning" / "product-brief.md"
    output = workspace.root / "planning" / "curriculum-blueprint.md"
    if not _v_is_file(brief_path) or not _v_is_file(output):
        raise CurriculumPrerequisiteError("修订课程蓝图需要当前 Brief 与已有课程蓝图")
    brief_metadata, brief_body = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
    blueprint_metadata, _ = parse_markdown_artifact(_v_read_text(output, encoding="utf-8"))
    if brief_metadata.status is not ArtifactStatus.APPROVED:
        raise CurriculumPrerequisiteError("修订课程蓝图前必须先批准当前产品 Brief")
    if blueprint_metadata.status not in {ArtifactStatus.APPROVED, ArtifactStatus.READY_FOR_REVIEW}:
        raise CurriculumPrerequisiteError("当前课程蓝图状态不允许创建修订")
    if blueprint_metadata.status is ArtifactStatus.READY_FOR_REVIEW and any(
        (workspace.root / "chapters").glob("chapter-*.md")
    ):
        raise CurriculumPrerequisiteError("待审批蓝图已被下游工件使用，不能替换初始草案")
    submission = _normalize_amendment_binding(
        _load_submission(input_path), brief_path, brief_metadata, brief_body
    )
    _require_length_recommendation(workspace, brief_path, brief_metadata, brief_body, submission)
    _validate_brief_binding(submission, brief_path, brief_metadata, brief_body)
    history = workspace.root / "planning" / "history"
    _v_mkdir(history,exist_ok=True)
    archived = history / f"curriculum-blueprint.v{blueprint_metadata.version}.md"
    if _v_exists(archived):
        raise FileExistsError(f"课程蓝图历史版本已存在：{archived}")
    extra_outputs = {archived.relative_to(workspace.root).as_posix(): _v_read_bytes(output)}
    contract_history = workspace.root / "chapters" / "history"
    from knowledge_product_workflow.change_impact import (
        binding_policy_revision,
        preview_change_impact,
    )
    precise = binding_policy_revision(workspace) == 2
    changed_chapters = None
    if precise:
        impact = preview_change_impact(workspace, input_path)
        changed_chapters = {item.chapter_number for item in impact.chapters
                            if item.disposition != "unaffected"}
        extra_outputs[f"runs/orchestration/change-impacts/{impact.impact_id}.json"] = impact.model_dump_json(indent=2).encode("utf-8")
    for contract in sorted((workspace.root / "chapters").glob("chapter-*-contract.md")):
        number = int(contract.stem.split("-")[1])
        if changed_chapters is not None and number not in changed_chapters:
            continue
        _v_mkdir(contract_history,exist_ok=True)
        archived_contract = contract_history / f"{contract.stem}.blueprint-v{blueprint_metadata.version}.md"
        if _v_exists(archived_contract):
            raise FileExistsError(f"章节合约历史版本已存在：{archived_contract}")
        extra_outputs[archived_contract.relative_to(workspace.root).as_posix()] = _v_read_bytes(contract)
        extra_outputs[contract.relative_to(workspace.root).as_posix()] = None
    result = _write_curriculum_blueprint(
        workspace,
        brief_path,
        brief_metadata,
        brief_body,
        submission,
        output,
        version=blueprint_metadata.version + 1,
        event_type="curriculum_amended", extra_outputs=extra_outputs,
        extra_events=[("curriculum_contracts_archived", {"blueprint_version": blueprint_metadata.version,
            "archived": archived.relative_to(workspace.root).as_posix()})],
    )
    return result


@_memoized_read
def require_current_submitted_blueprint(
    workspace: Workspace,
    *, require_approved: bool = True,
) -> tuple[Path, ArtifactMetadata, BlueprintSubmission]:
    """Return an approved blueprint only when it still binds the current approved Brief."""
    brief_path = workspace.root / "planning" / "product-brief.md"
    blueprint_path = workspace.root / "planning" / "curriculum-blueprint.md"
    if not _v_is_file(brief_path) or not _v_is_file(blueprint_path):
        raise CurriculumPrerequisiteError("缺少已批准 Brief 或课程蓝图")
    brief_metadata, brief_body = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
    blueprint_metadata, blueprint_body = parse_markdown_artifact(
        _v_read_text(blueprint_path, encoding="utf-8")
    )
    if brief_metadata.status is not ArtifactStatus.APPROVED:
        raise CurriculumPrerequisiteError("当前产品 Brief 尚未获得批准")
    if require_approved and blueprint_metadata.status is not ArtifactStatus.APPROVED:
        raise CurriculumPrerequisiteError("当前课程蓝图尚未获得批准")
    submission = parse_blueprint_submission(blueprint_body)
    if submission is None:
        raise CurriculumPrerequisiteError(
            "当前课程蓝图是旧模板；请由 kp-curriculum-architect 重新提交主题化蓝图"
        )
    fingerprints = brief_contract_fingerprints(brief_body)
    expected_content_sha256 = fingerprints["content_contract_sha256"]
    legacy_binding_matches = submission.brief_sha256 == _sha256_file(brief_path)
    content_binding_matches = submission.brief_content_sha256 == expected_content_sha256
    binding_matches = content_binding_matches or (
        submission.brief_content_sha256 is None
        and submission.brief_version == brief_metadata.version
        and legacy_binding_matches
    )
    if not binding_matches:
        raise CurriculumPrerequisiteError("课程蓝图已过期：它不再绑定当前已批准 Brief 的内容契约")
    if submission.topic != brief_topic(brief_body):
        raise CurriculumPrerequisiteError("课程蓝图主题与当前 Brief 不一致")
    if submission.target_audience != brief_target_audience(brief_body):
        raise CurriculumPrerequisiteError("课程蓝图目标读者与当前 Brief 不一致")
    constraints = parse_detail_constraints(brief_body)
    if constraints is not None and submission.audience_level != constraints.audience_level:
        raise CurriculumPrerequisiteError("课程蓝图受众级别与当前 Brief 不一致")
    return blueprint_path, blueprint_metadata, submission


def _render_budgets(budgets: list[ChapterBudget], language: str) -> str:
    payload = {"chapter_budgets": [budget.model_dump(mode="json") for budget in budgets]}
    heading = "章节预算" if language == "zh-CN" else "Detail budgets"
    return f"\n## {heading}\n\n```yaml\n{yaml.safe_dump(payload, sort_keys=False).rstrip()}\n```\n"


def create_curriculum_blueprint_template(workspace: Workspace, brief_path: Path) -> Path:
    """Write a blank host-input template; never create topic content or a real blueprint."""
    metadata, brief_body = parse_markdown_artifact(_v_read_text(brief_path, encoding="utf-8"))
    if metadata.status is not ArtifactStatus.APPROVED:
        raise CurriculumPrerequisiteError("生成课程蓝图模板前必须先批准产品 Brief")
    output = workspace.root / "planning" / "curriculum-blueprint.template.yaml"
    if _v_exists(output):
        raise FileExistsError(
            f"课程蓝图提交模板已存在：{output}；如需修订请使用 kp amend-outline --from <修订蓝图>，禁止删除后重建。"
        )
    constraints = parse_detail_constraints(brief_body)
    payload = {
        "schema_version": 2,
        "illustration_policy_revision": 2,
        "brief_sha256": _sha256_file(brief_path),
        "brief_content_sha256": brief_contract_fingerprints(brief_body)["content_contract_sha256"],
        "brief_version": metadata.version,
        "topic": brief_topic(brief_body),
        "target_audience": brief_target_audience(brief_body),
        "audience_level": constraints.audience_level.value if constraints is not None else "进阶",
        "product_mode": "general",
        "interview_answer_policy": None,
        "chapters": [
            {
                "chapter_number": "<正整数>",
                "title": "<主题化章节标题>",
                "learning_objectives": ["<读者完成后可达成的结果>"],
                "scope": "<本章覆盖与明确不覆盖的内容>",
                "weight": "<正数，相对其他章节的教学权重>",
                "rationale": "<为何本章应有此权重和体裁>",
                "chapter_archetype": "<concept|case_study|workshop|strategy|reference|appendix>",
                "required_units": [],
                "optional_units": [],
                "prohibited_units": [],
                "claim_ids": [],
                "illustration_tasks": [
                    {
                        "section_id": "<章节号.1>",
                        "decision": "none",
                        "alternative_rationale": "<说明本节为何不需要图以及采用的表达形式>",
                    }
                ],
                "section_outline": [
                    {
                        "number": "<章节号.1>",
                        "title": "<主题化小节标题>",
                        "children": [],
                    }
                ],
            }
        ],
    }
    from knowledge_product_workflow.runtime import _read_orchestration_state

    state = _read_orchestration_state(workspace)
    if state and state.production_mode == "adaptive":
        payload["production_policy_revision"] = 1
        payload["shared_context"] = {"terminology": {}, "cases": {}, "conventions": [], "claim_ids": []}
        for chapter in payload["chapters"]:
            chapter["production_dependencies"] = None
            chapter["production_exports"] = None
    _v_write_text(output,
        _template_schema_comments() + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    workspace.append_event(
        "curriculum_template_created",
        {"brief": str(brief_path.relative_to(workspace.root)), "artifact": str(output.relative_to(workspace.root))},
    )
    return output


def create_section_outline_migration_draft(workspace: Workspace) -> Path:
    """Extract a read-only heading-tree candidate for an old approved blueprint.

    The result intentionally is not a valid ``submit-outline`` payload. A host
    must review it with the user, copy the chosen tree into a v2 input, and use
    ``amend-outline`` for the only state-changing migration path.
    """
    blueprint_path, metadata, submission = require_current_submitted_blueprint(workspace)
    output = (
        workspace.root
        / "runs"
        / "templates"
        / f"curriculum-blueprint.v{metadata.version}.section-outline-candidate.yaml"
    )
    if _v_exists(output):
        raise FileExistsError(f"小节结构迁移候选已存在：{output}")
    candidates: list[dict[str, object]] = []
    for chapter in submission.chapters:
        chapter_path = workspace.root / "chapters" / f"chapter-{chapter.chapter_number:02d}.md"
        headings: list[dict[str, object]] = []
        if _v_is_file(chapter_path):
            _, body = parse_markdown_artifact(_v_read_text(chapter_path, encoding="utf-8"))
            headings = _extract_numbered_heading_candidates(body, chapter.chapter_number)
        candidates.append(
            {
                "chapter_number": chapter.chapter_number,
                "title": chapter.title,
                "section_outline_candidate": headings,
            }
        )
    payload = {
        "schema_version": 1,
        "migration_only": True,
        "source_blueprint": str(blueprint_path.relative_to(workspace.root)).replace("\\", "/"),
        "source_blueprint_version": metadata.version,
        "instructions": (
            "只读候选结构。请人工审阅并将确认后的小节树复制到 schema_version: 2 的蓝图输入，"
            "随后运行 kp amend-outline；不得直接修改当前蓝图或将本文件提交。"
        ),
        "chapters": candidates,
    }
    _v_mkdir(output.parent,parents=True,exist_ok=True)
    _v_write_text(output, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    workspace.append_event(
        "section_outline_migration_candidate_created",
        {"artifact": str(output.relative_to(workspace.root)).replace("\\", "/")},
    )
    return output


def _extract_numbered_heading_candidates(body: str, chapter_number: int) -> list[dict[str, object]]:
    """Read candidate titles only; invalid ordering remains visible for human repair."""
    roots: list[dict[str, object]] = []
    nodes: dict[tuple[int, ...], dict[str, object]] = {}
    in_fence = False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _MARKDOWN_HEADING.match(line)
        if match is None:
            continue
        text = match.group("text").strip()
        numbered = _SECTION_NUMBER.match(text.split(maxsplit=1)[0]) if text else None
        if numbered is None:
            continue
        number = numbered.group(0)
        remainder = text[len(number) :].strip()
        parts = tuple(int(part) for part in number.split("."))
        if len(parts) < 2 or parts[0] != chapter_number or len(parts) != len(match.group("marks")):
            continue
        node: dict[str, object] = {"number": number, "title": remainder, "children": []}
        nodes[parts] = node
        parent = nodes.get(parts[:-1])
        if parent is None:
            roots.append(node)
        else:
            children = parent["children"]
            assert isinstance(children, list)
            children.append(node)
    return roots
