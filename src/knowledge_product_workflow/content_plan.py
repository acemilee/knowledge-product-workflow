"""Version-bound chapter content plans for reader-task and artifact coverage."""

from __future__ import annotations

import json
import os
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_product_workflow.presentation import (
    PRESENTATION_PROFILE_ID,
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
    reader_job: str = Field(min_length=8, max_length=240)
    opening_mode: Literal["claim", "decision", "scenario", "procedure", "reference"]
    block_sequence: list[str] = Field(min_length=1, max_length=8)
    artifact: str = Field(min_length=1, max_length=120)
    asset_decision: Literal["none", "diagram", "table", "worksheet"]
    asset_rationale: str = Field(min_length=1, max_length=240)

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
        if len(value) != len(set(value)):
            raise ValueError("内容块顺序不能重复同一种块")
        unknown = set(value) - _BLOCK_KINDS
        if unknown:
            raise ValueError(f"内容块类型不受支持：{', '.join(sorted(unknown))}")
        return value

    @model_validator(mode="after")
    def require_asset_reasoning(self) -> ContentPlanSection:
        if self.asset_decision == "none":
            if self.artifact != "none":
                raise ValueError("不使用图表、表格或工作表时 artifact 必须为 none")
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
    path = publication_profile_path(workspace)
    if not path.is_file():
        return False
    try:
        profile = PublicationProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return False
    return profile.profile_id == PRESENTATION_PROFILE_ID and profile.content_contract_enabled


def create_content_plan_template(workspace: Workspace, chapter_number: int) -> tuple[Path, Path]:
    """Create one semantic template tied to the current approved chapter contract."""
    contract_path, chapter = require_current_chapter_contract(workspace, chapter_number)
    output = _template_path(workspace, chapter_number)
    if output.exists():
        raise FileExistsError(f"当前章节内容计划模板已存在：{output}")
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
    _atomic_write(output, yaml.safe_dump({"sections": sections}, allow_unicode=True, sort_keys=False))
    context = _context_path(workspace, chapter_number)
    _atomic_write(
        context,
        json.dumps(
            {
                "chapter_number": chapter_number,
                "contract": str(contract_path.relative_to(workspace.root)).replace("\\", "/"),
                "expected_sections": [
                    {"section_id": number, "title": title}
                    for number, title in _flatten_outline(chapter.section_outline)
                ],
                "allowed_block_kinds": sorted(_BLOCK_KINDS),
                "note": "只填写内容计划语义字段；不要填写章节号、哈希、合约版本或内部路径。",
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
    return chapter.chapter_archetype.value not in {"reference", "appendix"}


def _validate_plan_sections(plan: ContentPlanInput, chapter: BlueprintChapter) -> None:
    expected = [number for number, _ in _flatten_outline(chapter.section_outline)]
    actual = [section.section_id for section in plan.sections]
    if actual != expected:
        raise ContentPlanError("内容计划必须按章节合约的小节树顺序逐项覆盖，不可增加、跳过或重排小节")
    if _requires_reader_artifact(chapter) and all(section.artifact == "none" for section in plan.sections):
        raise ContentPlanError("当前章节体裁至少需要一个读者可复用 artifact，不能全部声明为 none")


def submit_content_plan(workspace: Workspace, chapter_number: int, input_path: Path) -> Path:
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
        raise FileExistsError(f"章节内容计划已存在：{output}；请先发起受控章节修订。")
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
