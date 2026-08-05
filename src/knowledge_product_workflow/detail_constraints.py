"""Typed delivery constraints, budgets, and deterministic content counting."""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator


class AudienceLevel(StrEnum):
    """Supported learner expertise levels."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    EXPERT = "expert"


class DeliveryFormat(StrEnum):
    """Delivery artifact combinations supported by the workflow."""

    MARKDOWN = "markdown"
    PDF = "pdf"
    BOTH = "both"


class ReviewPolicy(StrEnum):
    """Truthful publication policy for optional or independently evidenced review."""

    DETERMINISTIC_ONLY = "deterministic_only"
    INDEPENDENT_REVIEW_REQUIRED = "independent_review_required"


class PageConstraintMode(StrEnum):
    """Whether a page request is an exact target, range, lower bound, or open-ended."""

    UNBOUNDED = "unbounded"
    MINIMUM = "minimum"
    RANGE = "range"
    EXACT = "exact"


class PageScope(StrEnum):
    """The pages counted by a user-facing page constraint."""

    DELIVERY_TOTAL = "delivery_total"
    BODY_ONLY = "body_only"


_CONSTRAINT_VALUE_ALIASES = {
    "language": {
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "中文": "zh-CN",
        "简体中文": "zh-CN",
        "chinese": "zh-CN",
        "en": "en-US",
        "en-us": "en-US",
        "英文": "en-US",
        "英语": "en-US",
        "english": "en-US",
    },
    "layout_profile": {
        "a4-standard": "a4-standard",
        "a4 标准版式": "a4-standard",
        "a4标准版式": "a4-standard",
    },
    "audience_level": {
        "入门": "beginner",
        "初学者": "beginner",
        "beginner": "beginner",
        "进阶": "intermediate",
        "中级": "intermediate",
        "intermediate": "intermediate",
        "专家": "expert",
        "expert": "expert",
    },
    "delivery_format": {
        "markdown": "markdown",
        "md": "markdown",
        "pdf": "pdf",
        "两者": "both",
        "两种": "both",
        "markdown + pdf": "both",
        "markdown+pdf": "both",
        "both": "both",
    },
    "review_policy": {
        "deterministic_only": "deterministic_only",
        "仅确定性质量门": "deterministic_only",
        "independent_review_required": "independent_review_required",
        "需要独立审稿": "independent_review_required",
    },
}


def normalize_localized_constraints(values: dict[str, Any]) -> dict[str, Any]:
    """Normalize Chinese and English display values before Pydantic enum validation."""
    normalized = dict(values)
    for field, aliases in _CONSTRAINT_VALUE_ALIASES.items():
        value = normalized.get(field)
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        canonical = aliases.get(cleaned.lower())
        if canonical is not None:
            normalized[field] = canonical
    return normalized


class WordBudget(BaseModel):
    """Approved whole-book word-count interval."""

    minimum: int = Field(gt=0)
    target: int = Field(gt=0)
    maximum: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if not self.minimum <= self.target <= self.maximum:
            raise ValueError("字数预算必须满足 最小值 <= 目标值 <= 最大值")
        return self


def parse_word_budget(value: int | str) -> WordBudget:
    """Parse one exact target or a ``minimum-maximum`` interval."""
    if isinstance(value, int):
        return WordBudget(minimum=value, target=value, maximum=value)
    match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", value)
    if match is None:
        try:
            exact = int(value.strip())
        except ValueError as error:
            raise ValueError("目标字数应为正整数或形如 30000-50000 的区间") from error
        return WordBudget(minimum=exact, target=exact, maximum=exact)
    minimum, maximum = (int(part) for part in match.groups())
    return WordBudget(minimum=minimum, target=(minimum + maximum) // 2, maximum=maximum)


class PageConstraint(BaseModel):
    """A reader-facing page requirement that does not invent a writing target."""

    mode: PageConstraintMode
    scope: PageScope = PageScope.DELIVERY_TOTAL
    minimum: int | None = Field(default=None, gt=0)
    target: int | None = Field(default=None, gt=0)
    maximum: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.mode is PageConstraintMode.UNBOUNDED:
            if any(value is not None for value in (self.minimum, self.target, self.maximum)):
                raise ValueError("无限页数约束不能同时提供页数边界")
        elif self.mode is PageConstraintMode.MINIMUM:
            if self.minimum is None or self.target is not None or self.maximum is not None:
                raise ValueError("最低页数约束只能提供 minimum")
        elif self.mode is PageConstraintMode.EXACT:
            if self.target is None:
                raise ValueError("精确页数约束必须提供 target")
            self.minimum = self.target
            self.maximum = self.target
        else:
            if self.minimum is None or self.maximum is None:
                raise ValueError("页数范围必须提供最小值和最大值")
            if self.minimum > self.maximum:
                raise ValueError("页数范围的最小值不能大于最大值")
            self.target = self.target or (self.minimum + self.maximum) // 2
            if not self.minimum <= self.target <= self.maximum:
                raise ValueError("页数范围目标必须位于最小值和最大值之间")
        return self

    @property
    def is_writing_target(self) -> bool:
        """Only exact/range choices may become an initial chapter allocation target."""
        return self.mode in {PageConstraintMode.EXACT, PageConstraintMode.RANGE}


# Kept as a source-compatible name for integrations that imported the former type.
PageBudget = PageConstraint


def _normalized_page_text(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace("－", "-")
        .replace("—", "-")
        .replace("～", "-")
        .replace("≥", ">=")
        .replace("＋", "+")
    )


def parse_page_budget(value: int | str) -> PageConstraint:
    """Parse exact, range, lower-bound, unlimited, and body-only page requests."""
    if isinstance(value, int):
        return PageConstraint(mode=PageConstraintMode.EXACT, target=value)
    text = _normalized_page_text(value)
    if not text:
        raise ValueError("目标页数不能为空；可使用 15、10-20、至少10页或不限页数")
    scope = (
        PageScope.BODY_ONLY
        if any(marker in text for marker in ("正文", "除去封面", "不含封面", "excluding cover", "body only"))
        else PageScope.DELIVERY_TOTAL
    )
    text = re.sub(
        r"(?:正文|除去封面(?:与目录)?外|不含封面(?:与目录)?|excluding\s+cover(?:\s+and\s+toc)?|body\s+only)",
        "",
        text,
    ).strip()
    if any(marker in text for marker in ("不限", "无上限", "unbounded", "unlimited")):
        return PageConstraint(mode=PageConstraintMode.UNBOUNDED, scope=scope)
    lower = re.fullmatch(
        r"(?:至少|不少于|不低于|>=)\s*(\d+)\s*(?:页)?"
        r"(?:\s*[，,;；]\s*(?:目标(?:设为|为)?\s*)?\d+\s*(?:页)?)?",
        text,
    )
    if lower is None:
        lower = re.fullmatch(r"(\d+)\s*(?:页)?\s*(?:(?:及)?以上|起|\+\s*(?:页)?)", text)
    if lower is not None:
        return PageConstraint(
            mode=PageConstraintMode.MINIMUM, scope=scope, minimum=int(lower.group(1))
        )
    page_range = re.fullmatch(r"(?:正文\s*)?(\d+)\s*(?:页)?\s*(?:-|至|到)\s*(\d+)\s*(?:页)?", text)
    if page_range is not None:
        minimum, maximum = (int(part) for part in page_range.groups())
        return PageConstraint(
            mode=PageConstraintMode.RANGE,
            scope=scope,
            minimum=minimum,
            target=(minimum + maximum) // 2,
            maximum=maximum,
        )
    exact = re.fullmatch(r"(?:正文\s*)?(\d+)\s*(?:页)?", text)
    if exact is None:
        raise ValueError("目标页数应为 15、10-20、至少10页、10+、页数无上限或正文15页")
    return PageConstraint(mode=PageConstraintMode.EXACT, scope=scope, target=int(exact.group(1)))


class DetailConstraints(BaseModel):
    """Approved, machine-readable detail and delivery requirements."""

    target_pages: int | None = Field(default=None, gt=0)
    page_min: int | None = Field(default=None, gt=0)
    page_target: int | None = Field(default=None, gt=0)
    page_max: int | None = Field(default=None, gt=0)
    page_constraint: PageConstraint | None = None
    target_words: int | None = Field(default=None, gt=0)
    word_min: int | None = Field(default=None, gt=0)
    word_target: int | None = Field(default=None, gt=0)
    word_max: int | None = Field(default=None, gt=0)
    language: str = "zh-CN"
    layout_profile: str = "a4-standard"
    audience_level: AudienceLevel = AudienceLevel.INTERMEDIATE
    delivery_format: DeliveryFormat = DeliveryFormat.MARKDOWN
    review_policy: ReviewPolicy = ReviewPolicy.DETERMINISTIC_ONLY

    @model_validator(mode="before")
    @classmethod
    def normalize_display_values(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return normalize_localized_constraints(value)
        return value

    @field_validator("language")
    @classmethod
    def require_language(cls, value: str) -> str:
        """Keep book language inside the supported bilingual product contract."""
        if value not in {"zh-CN", "en-US"}:
            raise ValueError("书稿语言仅支持 中文（zh-CN）或英文（en-US）")
        return value

    @field_validator("layout_profile")
    @classmethod
    def require_supported_layout(cls, value: str) -> str:
        """V2 deliberately exposes one reproducible layout profile."""
        if value != "a4-standard":
            raise ValueError("layout_profile must be a4-standard")
        return value

    @model_validator(mode="after")
    def normalize_budgets(self) -> Self:
        """Normalize optional page and word budgets, including legacy exact targets."""
        legacy_pages = (self.page_min, self.page_target, self.page_max)
        if self.page_constraint is None and self.target_pages is not None and self.page_target is None:
            self.page_constraint = PageConstraint(
                mode=PageConstraintMode.EXACT, target=self.target_pages
            )
        if self.page_constraint is None and any(value is not None for value in legacy_pages):
            if None in legacy_pages:
                raise ValueError("旧版页数预算必须同时提供最小值、目标值和最大值")
            mode = (
                PageConstraintMode.EXACT
                if self.page_min == self.page_target == self.page_max
                else PageConstraintMode.RANGE
            )
            self.page_constraint = PageConstraint(
                mode=mode,
                minimum=self.page_min,
                target=self.page_target,
                maximum=self.page_max,
            )
        if self.page_constraint is not None:
            constraint = self.page_constraint
            if self.target_pages is not None and constraint.target is not None and self.target_pages != constraint.target:
                raise ValueError("target_pages 必须等于页数约束的 target")
            self.page_min = constraint.minimum
            self.page_target = constraint.target
            self.page_max = constraint.maximum
            self.target_pages = constraint.target
        if self.target_words is not None and self.word_target is None:
            self.word_min = self.target_words
            self.word_target = self.target_words
            self.word_max = self.target_words
        if any(value is not None for value in (self.word_min, self.word_target, self.word_max)):
            if None in {self.word_min, self.word_target, self.word_max}:
                raise ValueError("字数预算必须同时提供最小值、目标值和最大值")
            WordBudget(minimum=self.word_min, target=self.word_target, maximum=self.word_max)
            if self.target_words is not None and self.target_words != self.word_target:
                raise ValueError("target_words 必须等于 word_target")
            self.target_words = self.word_target
        return self

    @property
    def page_budget(self) -> PageConstraint | None:
        """Return the full page constraint, including minimum/unbounded modes."""
        return self.page_constraint

    @property
    def production_page_target(self) -> int | None:
        """Return a chapter-allocation target only when the user actually supplied one."""
        if self.page_constraint is None or not self.page_constraint.is_writing_target:
            return None
        return self.page_constraint.target

    @property
    def word_budget(self) -> WordBudget | None:
        """Return the normalized word interval when the product has one."""
        if self.word_target is None:
            return None
        return WordBudget(minimum=self.word_min or 1, target=self.word_target, maximum=self.word_max or 1)

    @property
    def requires_markdown(self) -> bool:
        """Whether the selected delivery includes Markdown."""
        return self.delivery_format in {DeliveryFormat.MARKDOWN, DeliveryFormat.BOTH}

    @property
    def requires_pdf(self) -> bool:
        """Whether the selected delivery includes PDF."""
        return self.delivery_format in {DeliveryFormat.PDF, DeliveryFormat.BOTH}


class ChapterBudget(BaseModel):
    """One chapter's approved share of the total detail budget."""

    chapter_number: int = Field(gt=0)
    weight: float = Field(gt=0)
    target_words: int | None = Field(default=None, ge=0)
    word_min: int | None = Field(default=None, ge=0)
    word_target: int | None = Field(default=None, ge=0)
    word_max: int | None = Field(default=None, ge=0)
    target_pages: int | None = Field(default=None, ge=0)
    rationale: str = ""
    required_units: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_word_range(self) -> Self:
        values = (self.word_min, self.word_target, self.word_max)
        if any(value is not None for value in values):
            if None in values:
                raise ValueError("章节字数预算必须完整")
            if not self.word_min <= self.word_target <= self.word_max:
                raise ValueError("章节字数预算顺序不正确")
            self.target_words = self.word_target
        return self


class ChapterSpec(BaseModel):
    """Human-approvable chapter importance and teaching requirements."""

    chapter_number: int = Field(gt=0)
    weight: float = Field(gt=0)
    rationale: str = Field(min_length=1)
    required_units: list[str] = Field(min_length=1)


def _allocate_total(total: int | None, weights: list[float]) -> list[int | None]:
    if total is None:
        return [None] * len(weights)
    if not weights or any(weight <= 0 for weight in weights):
        raise ValueError("weights must be a non-empty list of positive values")
    weight_sum = sum(weights)
    raw = [total * weight / weight_sum for weight in weights]
    allocated = [math.floor(value) for value in raw]
    remainder = total - sum(allocated)
    ranked = sorted(range(len(weights)), key=lambda index: (raw[index] - allocated[index], -index), reverse=True)
    for index in ranked[:remainder]:
        allocated[index] += 1
    return allocated


def allocate_budgets(
    chapters: list[int] | list[ChapterSpec], constraints: DetailConstraints
) -> list[ChapterBudget]:
    """Allocate whole-book ranges by approved teaching weights."""
    if not chapters:
        raise ValueError("章节列表不能为空")
    if isinstance(chapters[0], int):
        chapter_specs = [
            ChapterSpec(
                chapter_number=number,
                weight=1,
                rationale="兼容旧工作流的默认章节权重",
                required_units=["concept_explanation"],
            )
            for number in chapters
        ]
    else:
        chapter_specs = chapters
    numbers = [spec.chapter_number for spec in chapter_specs]
    if len(set(numbers)) != len(numbers):
        raise ValueError("章节编号必须唯一")
    weights = [float(spec.weight) for spec in chapter_specs]
    word_minimums = _allocate_total(constraints.word_min, weights)
    word_targets = _allocate_total(constraints.word_target, weights)
    word_maximums = _allocate_total(constraints.word_max, weights)
    page_targets = _allocate_total(constraints.production_page_target, weights)
    return [
        ChapterBudget(
            chapter_number=spec.chapter_number,
            weight=spec.weight,
            word_min=word_minimums[index],
            target_words=word_targets[index],
            word_target=word_targets[index],
            word_max=word_maximums[index],
            target_pages=page_targets[index],
            rationale=spec.rationale,
            required_units=spec.required_units,
        )
        for index, spec in enumerate(chapter_specs)
    ]


_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def count_words(markdown: str) -> int:
    """Count CJK characters and Latin/numeric tokens in rendered Markdown content."""
    in_code_fence = False
    count = 0
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            comment = re.match(r"(?:#|//|--)+\s*(.*)$", line)
            line = comment.group(1) if comment else ""
        line = _MARKDOWN_LINK.sub(r"\1", line)
        count += len(_CJK_CHARACTER.findall(line))
        without_cjk = _CJK_CHARACTER.sub(" ", line)
        count += len(_LATIN_TOKEN.findall(without_cjk))
    return count
