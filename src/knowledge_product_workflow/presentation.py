"""Reader-facing publication profile and derived PDF metadata."""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, date, datetime
from enum import StrEnum
from html import escape
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_product_workflow.workspace import Workspace

LEGACY_PRESENTATION_PROFILE_ID = "knowledge-product-pdf-v4"
V5_PRESENTATION_PROFILE_ID = "knowledge-product-pdf-v5"
PRESENTATION_PROFILE_ID = "knowledge-product-pdf-v6"
PUBLICATION_PROFILE_FILENAME = "publication-profile.yaml"
PUBLICATION_METADATA_FILENAME = "publication-metadata.yaml"
PUBLICATION_UPDATE_PREVIEW_FILENAME = "publication-update-preview.json"
_SEMVER = re.compile(r"^v(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")
_SUPPORTED_BODY_FONT_SIZES = frozenset({12, 14})
_HEX_COLOR = re.compile(r"^[0-9A-F]{6}$")


def _brand_palette(intent: str) -> PublicationPalette:
    normalized = intent.strip().casefold()
    if normalized in {"橙色", "橙色基调", "orange", "default_orange"}:
        accent, soft = "B65E2B", "F8EBE2"
    elif normalized in {"紫色", "紫色基调", "purple"}:
        accent, soft = "6847A5", "EEE8F7"
    else:
        candidate = normalized.removeprefix("#").upper()
        if not _HEX_COLOR.fullmatch(candidate):
            raise ValueError("品牌色请明确为橙色基调、紫色基调或 6 位 HEX；含糊描述须在 Brief 预览前选定。")
        accent = candidate
        channels = [int(candidate[index:index + 2], 16) for index in (0, 2, 4)]
        soft = "".join(f"{round(channel * 0.12 + 255 * 0.88):02X}" for channel in channels)
    palette = PublicationPalette(ink="202124", paper="FFFFFF", accent=accent, accent_soft=soft, muted="66616A", rule="DED8D5")
    if _contrast_ratio(palette.accent, palette.paper) < 3.0 or _contrast_ratio(palette.accent, palette.accent_soft) < 3.0:
        raise ValueError("所选品牌色与白底或提示框浅底对比度不足；请改用较深的 HEX 颜色。")
    return palette


class DesignFamily(StrEnum):
    """Finite reader-facing visual systems supported by the PDF renderer."""

    EDITORIAL_GUIDE = "editorial-guide"
    FIELD_PLAYBOOK = "field-playbook"
    TECHNICAL_MANUAL = "technical-manual"


class PublicationPalette(BaseModel):
    """Safe colour tokens consumed by fixed TeX templates, never raw TeX."""

    model_config = ConfigDict(extra="forbid")

    ink: str = "202124"
    paper: str = "FFFCFA"
    accent: str = "C75D45"
    accent_soft: str = "F6EAE6"
    muted: str = "6B6B6B"
    rule: str = "DDD6D2"

    @field_validator("ink", "paper", "accent", "accent_soft", "muted", "rule")
    @classmethod
    def require_safe_hex(cls, value: str) -> str:
        normalized = value.strip().upper()
        if _HEX_COLOR.fullmatch(normalized) is None:
            raise ValueError("颜色必须是 6 位 RGB HEX，不允许 TeX、CSS 或其他控制文本。")
        return normalized


_DESIGN_DEFAULTS: dict[DesignFamily, dict[str, object]] = {
    DesignFamily.TECHNICAL_MANUAL: {
        "palette": PublicationPalette(),
        "heading_scale": "standard",
        "line_length": "standard",
        "paragraph_spacing": "compact",
        "running_header_mode": "chapter",
        "chapter_opener_mode": "structured",
        "component_emphasis": "balanced",
        "source_presentation": "inline",
    },
    DesignFamily.EDITORIAL_GUIDE: {
        "palette": PublicationPalette(
            ink="24312D", paper="FFFDF8", accent="2B6F6D", accent_soft="E6F2F0", muted="59615B", rule="D6DFDA"
        ),
        "heading_scale": "generous",
        "line_length": "compact",
        "paragraph_spacing": "relaxed",
        "running_header_mode": "chapter",
        "chapter_opener_mode": "editorial",
        "component_emphasis": "subtle",
        "source_presentation": "inline",
    },
    DesignFamily.FIELD_PLAYBOOK: {
        "palette": PublicationPalette(
            ink="243B53", paper="FFFCF7", accent="B76E32", accent_soft="F7EBDD", muted="5E6268", rule="E0D2C1"
        ),
        "heading_scale": "standard",
        "line_length": "standard",
        "paragraph_spacing": "relaxed",
        "running_header_mode": "section",
        "chapter_opener_mode": "practical",
        "component_emphasis": "balanced",
        "source_presentation": "card",
    },
}


def _contrast_ratio(left: str, right: str) -> float:
    def luminance(value: str) -> float:
        components = [int(value[index : index + 2], 16) / 255 for index in range(0, 6, 2)]
        converted = [component / 12.92 if component <= 0.04045 else ((component + 0.055) / 1.055) ** 2.4 for component in components]
        return 0.2126 * converted[0] + 0.7152 * converted[1] + 0.0722 * converted[2]

    first, second = luminance(left), luminance(right)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


COVER_FIELDS = ("cover_layout_revision", "cover_title_lines", "cover_summary_zh", "cover_summary_en", "cover_focus_items", "cover_content_note", "illustration_palette_policy", "illustration_palette_scheme", "illustration_backend_preference", "illustration_category_colors")


class PublicationFeatureFields(BaseModel):
    illustration_palette_policy: Literal["semantic", "locked"] = "semantic"
    illustration_palette_scheme: Literal["warm-focus", "balanced", "cool-structure"] = "balanced"
    illustration_backend_preference: Literal["vector", "host-image"] = "vector"
    illustration_category_colors: dict[str, Literal["brand", "peach", "sand", "rose", "sage", "blue", "lavender", "stone"]] = Field(default_factory=dict)
    cover_layout_revision: Literal[1, 2] = 1
    cover_title_lines: list[str] | None = Field(default=None, min_length=1, max_length=3)
    cover_summary_zh: str | None = Field(default=None, max_length=180)
    cover_summary_en: str | None = Field(default=None, max_length=600)
    cover_focus_items: list[str] = Field(default_factory=list, max_length=4)
    cover_content_note: str | None = Field(default=None, max_length=180)

    @field_validator("cover_title_lines")
    @classmethod
    def clean_title_lines(cls, values):
        if values is not None and any(not v.strip() or "\n" in v or len(v) > 240 for v in values):
            raise ValueError("封面标题行必须为1—240字符的单行文字")
        return [v.strip() for v in values] if values is not None else None

    @field_validator("illustration_category_colors")
    @classmethod
    def clean_concept_colors(cls, values):
        if values is None:
            return None
        cleaned = {}
        for name,token in values.items():
            name = " ".join(name.split())
            if not name or (name in cleaned and cleaned[name] != token):
                raise ValueError("概念颜色名空白或规范化后冲突")
            cleaned[name] = token
        return cleaned

    @field_validator("cover_summary_zh", "cover_summary_en", "cover_content_note")
    @classmethod
    def clean_cover_copy(cls, value):
        if value is not None and not value.strip():
            raise ValueError("封面文案不能为空白")
        return value.strip() if value is not None else None

    @field_validator("cover_focus_items")
    @classmethod
    def clean_focus_items(cls, values):
        if values is not None and any(not v.strip() or len(v.strip()) > 24 for v in values):
            raise ValueError("学习重点每项须1—24字符")
        return [v.strip() for v in values] if values is not None else None


class PresentationOverrides(PublicationFeatureFields):
    """Legacy optional values accepted from an existing Product Brief.

    New reader-facing changes belong to ``publication-profile.yaml``. These
    fields remain only so pre-v1.0.2 Brief files can seed a profile once.
    """

    model_config = ConfigDict(extra="forbid")

    cover_layout_revision: Literal[1, 2] | None = None
    illustration_palette_policy: Literal["semantic", "locked"] | None = None
    illustration_palette_scheme: Literal["warm-focus", "balanced", "cool-structure"] | None = None
    illustration_backend_preference: Literal["vector", "host-image"] | None = None
    illustration_category_colors: dict[str, Literal["brand", "peach", "sand", "rose", "sage", "blue", "lavender", "stone"]] | None = None
    cover_focus_items: list[str] | None = Field(default=None, max_length=4)

    subtitle: str | None = Field(default=None, max_length=180)
    edition: str | None = Field(default=None, max_length=80)
    publication_date: date | None = None
    publisher_label: str | None = Field(default=None, max_length=100)
    copyright_line: str | None = Field(default=None, max_length=180)
    brand_color_intent: str | None = Field(default=None, max_length=80)

    @field_validator("subtitle", "edition", "publisher_label", "copyright_line", "brand_color_intent")
    @classmethod
    def normalize_optional_text(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} 不能是空白字符串")
        return cleaned

    def has_values(self) -> bool:
        return any(value is not None for value in self.model_dump().values())


class PublicationProfile(PublicationFeatureFields):
    """The only user-editable, reader-visible publication configuration."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=3, ge=1)
    profile_id: str = PRESENTATION_PROFILE_ID
    revision: int = Field(default=1, ge=1)
    subtitle: str = Field(min_length=1, max_length=180)
    cover_label: str = Field(min_length=1, max_length=100)
    footer_label: str = Field(min_length=1, max_length=100)
    copyright_line: str = Field(min_length=1, max_length=180)
    edition_label: str = Field(min_length=1, max_length=80)
    publication_date: date
    body_font_size: int = 12
    brand_color_intent: str = "橙色基调"
    font_policy: Literal["preferred", "fallback"] = "preferred"
    visual_rule_set: Literal["kp-pdf-v6-visual-2"] = "kp-pdf-v6-visual-2"
    reading_context: str = Field(default="专业知识阅读", min_length=1, max_length=80)
    product_promise: str = Field(default="可执行的专业知识指导", min_length=1, max_length=160)
    visual_direction: str = Field(default="清晰、克制、以阅读任务为中心", min_length=1, max_length=120)
    design_family: DesignFamily = DesignFamily.TECHNICAL_MANUAL
    palette: PublicationPalette = Field(default_factory=PublicationPalette)
    heading_scale: Literal["compact", "standard", "generous"] = "standard"
    line_length: Literal["compact", "standard"] = "standard"
    paragraph_spacing: Literal["compact", "relaxed"] = "relaxed"
    running_header_mode: Literal["none", "chapter", "section"] = "section"
    chapter_opener_mode: Literal["structured", "editorial", "practical"] = "structured"
    component_emphasis: Literal["subtle", "balanced"] = "balanced"
    source_presentation: Literal["inline", "card"] = "inline"
    content_contract_enabled: bool = True
    reader_layout_revision: Literal[1, 2] = 1
    illustration_style_revision: Literal[0, 1, 2] = 0

    @model_validator(mode="before")
    @classmethod
    def seed_design_defaults(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        values = dict(raw)
        profile_id = values.get("profile_id", PRESENTATION_PROFILE_ID)
        schema_version = values.get("schema_version", 3)
        if profile_id in {LEGACY_PRESENTATION_PROFILE_ID, V5_PRESENTATION_PROFILE_ID} or schema_version in {1, 2}:
            values.setdefault("content_contract_enabled", False)
            values.setdefault("body_font_size", 12)
            family = DesignFamily(values.get("design_family", DesignFamily.TECHNICAL_MANUAL))
            if family is DesignFamily.TECHNICAL_MANUAL:
                values.setdefault("running_header_mode", "section")
            for field, value in _DESIGN_DEFAULTS[family].items():
                values.setdefault(field, value)
            return values
        family = DesignFamily(values.get("design_family", DesignFamily.TECHNICAL_MANUAL))
        # Historical layout-1 profiles missing this field retain their old
        # navigation. Explicit saved choices always win over new defaults.
        if family is DesignFamily.TECHNICAL_MANUAL and values.get("reader_layout_revision", 1) == 1:
            values.setdefault("running_header_mode", "section")
        values.setdefault("schema_version", 3)
        values.setdefault("profile_id", PRESENTATION_PROFILE_ID)
        for field, value in _DESIGN_DEFAULTS[family].items():
            if field not in {"palette", "paragraph_spacing"}:
                values.setdefault(field, value)
        values.setdefault("brand_color_intent", "橙色基调")
        values.setdefault("palette", _brand_palette(str(values["brand_color_intent"])))
        return values

    @field_validator(
        "subtitle",
        "cover_label",
        "footer_label",
        "copyright_line",
        "edition_label",
        "reading_context",
        "product_promise",
        "visual_direction",
        "brand_color_intent",
    )
    @classmethod
    def normalize_required_text(cls, value: str, info) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} 不能为空")
        return cleaned

    @field_validator("body_font_size")
    @classmethod
    def require_supported_body_font_size(cls, value: int) -> int:
        if value not in _SUPPORTED_BODY_FONT_SIZES:
            raise ValueError("body_font_size 当前仅支持 12 或 14。")
        return value

    @model_validator(mode="after")
    def require_supported_profile_and_contrast(self) -> PublicationProfile:
        if self.profile_id == LEGACY_PRESENTATION_PROFILE_ID and self.schema_version == 1:
            return self
        if not (
            (self.profile_id == V5_PRESENTATION_PROFILE_ID and self.schema_version == 2)
            or (self.profile_id == PRESENTATION_PROFILE_ID and self.schema_version in {3, 4})
        ):
            raise ValueError(f"不支持的出版视觉配置：{self.profile_id}/v{self.schema_version}")
        if _contrast_ratio(self.palette.ink, self.palette.paper) < 4.5:
            raise ValueError("正文颜色与纸色对比度不足，无法保证可读性。")
        if _contrast_ratio(self.palette.accent, self.palette.paper) < 3.0:
            raise ValueError("强调色与纸色对比度不足，无法保证导航可读性。")
        if _contrast_ratio(self.palette.ink, self.palette.accent_soft) < 4.5:
            raise ValueError("提示框/表格浅底与正文色对比度不足。")
        if _contrast_ratio(self.palette.accent, self.palette.accent_soft) < 3.0:
            raise ValueError("强调色与浅底色对比度不足。")
        if self.profile_id == PRESENTATION_PROFILE_ID and not self.content_contract_enabled:
            raise ValueError("v6 出版档案必须保留内容计划与发展性质量门，不能关闭。")
        if self.profile_id == PRESENTATION_PROFILE_ID and self.palette.accent != _brand_palette(self.brand_color_intent).accent:
            raise ValueError("品牌色意图与主色 token 不一致；自定义主色请同时将 brand_color_intent 写为相同 HEX。")
        if self.profile_id == PRESENTATION_PROFILE_ID and _contrast_ratio(self.palette.muted, self.palette.paper) < 3.0:
            raise ValueError("辅助文字与纸色对比度不足。")
        return self


class PublicationMetadata(PublicationFeatureFields):
    """Derived, frozen-per-render metadata consumed by the PDF templates."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = PRESENTATION_PROFILE_ID
    profile_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=240)
    subtitle: str = Field(min_length=1, max_length=180)
    audience_label: str = Field(min_length=1, max_length=240)
    edition_label: str = Field(min_length=1, max_length=80)
    publication_date: date
    cover_label: str = Field(min_length=1, max_length=100)
    footer_label: str = Field(min_length=1, max_length=100)
    copyright_line: str = Field(min_length=1, max_length=180)
    body_font_size: int
    brand_color_intent: str = "橙色基调"
    font_policy: Literal["preferred", "fallback"] = "preferred"
    visual_rule_set: Literal["kp-pdf-v6-visual-2"] = "kp-pdf-v6-visual-2"
    design_family: DesignFamily = DesignFamily.TECHNICAL_MANUAL
    palette: PublicationPalette = Field(default_factory=PublicationPalette)
    heading_scale: Literal["compact", "standard", "generous"] = "standard"
    line_length: Literal["compact", "standard"] = "standard"
    paragraph_spacing: Literal["compact", "relaxed"] = "compact"
    running_header_mode: Literal["none", "chapter", "section"] = "section"
    chapter_opener_mode: Literal["structured", "editorial", "practical"] = "structured"
    component_emphasis: Literal["subtle", "balanced"] = "balanced"
    source_presentation: Literal["inline", "card"] = "inline"
    content_contract_enabled: bool = False
    reader_layout_revision: Literal[1, 2] = 1
    illustration_style_revision: Literal[0, 1, 2] = 0

    @field_validator(
        "profile_id",
        "title",
        "subtitle",
        "audience_label",
        "edition_label",
        "cover_label",
        "footer_label",
        "copyright_line",
    )
    @classmethod
    def normalize_required_text(cls, value: str, info) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} 不能为空")
        return cleaned

    @field_validator("body_font_size")
    @classmethod
    def require_supported_body_font_size(cls, value: int) -> int:
        if value not in _SUPPORTED_BODY_FONT_SIZES:
            raise ValueError("body_font_size 当前仅支持 12 或 14。")
        return value

    @property
    def edition(self) -> str:
        """Compatibility accessor for callers written against v1.0.1."""
        return self.edition_label

    @property
    def publisher_label(self) -> str:
        """Compatibility accessor; new templates never use this shared label."""
        return self.cover_label


class PublicationUpdateInput(PublicationFeatureFields):
    """Host-filled semantic input for one reader-visible publication update."""

    model_config = ConfigDict(extra="forbid")

    cover_layout_revision: Literal[1, 2] | None = None
    illustration_palette_policy: Literal["semantic", "locked"] | None = None
    illustration_palette_scheme: Literal["warm-focus", "balanced", "cool-structure"] | None = None
    illustration_backend_preference: Literal["vector", "host-image"] | None = None
    illustration_category_colors: dict[str, Literal["brand", "peach", "sand", "rose", "sage", "blue", "lavender", "stone"]] | None = None
    cover_focus_items: list[str] | None = Field(default=None, max_length=4)

    subtitle: str | None = Field(default=None, max_length=180)
    cover_label: str | None = Field(default=None, max_length=100)
    footer_label: str | None = Field(default=None, max_length=100)
    copyright_line: str | None = Field(default=None, max_length=180)
    body_font_size: int | None = None
    brand_color_intent: str | None = Field(default=None, max_length=80)
    font_policy: Literal["preferred", "fallback"] | None = None
    edition_label: str | None = Field(default=None, max_length=80)
    publication_date: date | None = None
    reading_context: str | None = Field(default=None, max_length=80)
    product_promise: str | None = Field(default=None, max_length=160)
    visual_direction: str | None = Field(default=None, max_length=120)
    design_family: DesignFamily | None = None
    palette: PublicationPalette | None = None
    heading_scale: Literal["compact", "standard", "generous"] | None = None
    line_length: Literal["compact", "standard"] | None = None
    paragraph_spacing: Literal["compact", "relaxed"] | None = None
    running_header_mode: Literal["none", "chapter", "section"] | None = None
    chapter_opener_mode: Literal["structured", "editorial", "practical"] | None = None
    component_emphasis: Literal["subtle", "balanced"] | None = None
    source_presentation: Literal["inline", "card"] | None = None
    content_contract_enabled: bool | None = None
    reader_layout_revision: Literal[1, 2] | None = None
    illustration_style_revision: Literal[0, 1, 2] | None = None
    version_bump: Literal["patch", "minor", "major", "none"]

    @field_validator(
        "subtitle",
        "cover_label",
        "footer_label",
        "copyright_line",
        "edition_label",
        "reading_context",
        "product_promise",
        "visual_direction",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} 不能是空白字符串")
        if info.field_name == "edition_label" and _SEMVER.fullmatch(cleaned) is None:
            raise ValueError("edition_label 必须使用 vX.Y.Z 格式，例如 v1.0.0")
        return cleaned

    @field_validator("body_font_size")
    @classmethod
    def require_supported_optional_body_font_size(cls, value: int | None) -> int | None:
        if value is not None and value not in _SUPPORTED_BODY_FONT_SIZES:
            raise ValueError("body_font_size 当前仅支持 12 或 14。")
        return value

    @model_validator(mode="after")
    def require_a_real_change(self) -> PublicationUpdateInput:
        visible_fields = set(self.model_fields_set) - {"version_bump"}
        if not visible_fields and self.version_bump == "none":
            raise ValueError("出版信息更新至少需要一项可见字段，或选择版本递增。")
        if self.edition_label is not None and self.version_bump != "none":
            raise ValueError("edition_label 与 version_bump 不能同时指定；请二选一。")
        return self


class PublicationUpdateCandidate(BaseModel):
    """A single confirmation-bound publication profile revision."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    prior_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_revision: int = Field(ge=1)
    version_bump: Literal["patch", "minor", "major", "none"]
    changed_fields: list[str] = Field(min_length=1)
    profile: PublicationProfile


def publication_profile_path(workspace: Workspace) -> Path:
    return workspace.root / "planning" / PUBLICATION_PROFILE_FILENAME


def publication_update_preview_path(workspace: Workspace) -> Path:
    return workspace.root / "runs" / PUBLICATION_UPDATE_PREVIEW_FILENAME


def publication_metadata_path(workspace: Workspace) -> Path:
    return workspace.root / "delivery" / PUBLICATION_METADATA_FILENAME


def _workspace_title(workspace: Workspace) -> str | None:
    from knowledge_product_workflow.frontmatter import parse_markdown_artifact
    from knowledge_product_workflow.workflows.product_brief import brief_topic

    path = workspace.root / "planning" / "product-brief.md"
    if not path.is_file():
        return None
    _, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    return brief_topic(body)


def validate_cover_contract(profile, title: str | None = None) -> None:
    """Validate cover inputs before writing rendering derivatives."""
    import re

    if profile.cover_layout_revision == 2:
        if not profile.cover_summary_zh:
            raise ValueError("封面布局2须先确认非空中文梗概")
        copyright = profile.copyright_line or f"© {datetime.now(UTC).year} 知识产品工作流"
        if not re.fullmatch(r"©\s+\d{4}\s+\S.*", copyright):
            raise ValueError("封面布局2版权行须为：© 年份 署名；请在出版预览确认修正")
    if profile.cover_title_lines and title is not None:
        from itertools import product

        lines = profile.cover_title_lines
        candidates = {lines[0] + "".join(separator + line for separator, line in zip(separators, lines[1:], strict=True))
                      for separators in product(("", " "), repeat=len(lines)-1)}
        if title not in candidates:
            raise ValueError("封面标题分行必须完整保留正式主题，不能增删或改写字符与英文词间空格")


def create_publication_design_preview(workspace: Workspace, brand_color_intent: str) -> Path:
    """Render a safe fixed design specimen before Brief approval, never a mock book."""
    palette = _brand_palette(brand_color_intent)
    path = workspace.root / "runs" / "publication-design-preview.html"
    html = f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><title>出版设计示意</title>
<style>
body{{background:#ececec;color:#{palette.ink};font:16px/1.75 Inter,"PingFang SC",sans-serif;margin:0;padding:30px}}
main{{max-width:760px;margin:auto;background:#{palette.paper};padding:48px;border-radius:6px}}
h1{{font-size:30px}} h2{{font-size:23px;border-top:1px solid #{palette.rule};border-bottom:1px solid #{palette.rule};padding:8px 0}}h1,h2,strong{{font-family:Inter,"PingFang SC Semibold","PingFang SC",sans-serif;font-weight:600}}
.brand,li::marker{{color:#{palette.accent}}}.swatch{{display:inline-block;width:60px;height:34px;background:#{palette.accent};vertical-align:middle;margin:0 12px}}
.note,.diagram{{background:#{palette.accent_soft};padding:18px;border-left:3px solid #{palette.accent};border-radius:5px}}
table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border:1px solid #{palette.rule};text-align:left}}th{{background:#{palette.accent_soft}}}
code{{font:14px "JetBrains Mono",monospace;background:#{palette.accent_soft};border:1px solid #{palette.rule};border-radius:3px;padding:3px 6px}}
.diagram{{display:flex;gap:14px;border:0;margin:20px 0}}.card{{flex:1;background:white;border:1px solid #{palette.accent};padding:16px;border-radius:10px;text-align:center}}
.caption{{color:#{palette.muted};font-size:13px}}footer{{margin-top:30px;color:#{palette.muted};font-size:13px}}
</style><main><p class="caption">设计示意 · 固定样例，尚未生成真实章节；浏览器字体可能替代，默认字体须由doctor探测及PDF嵌入验收确认</p>
<h1>专业知识指南</h1><p>品牌色意图：{escape(brand_color_intent)}<span class="swatch"></span>#{palette.accent}</p>
<div class="note">章导读示意：本章先确定输入、判断与交接边界，再用关系图和记录表完成一次可复核操作。导读解释本章主线，不增加目录层级。</div>
<h2>目录样式</h2><ol><li>理解问题与判断条件</li><li>运行实验与核验结果</li></ol>
<h2><span class="brand">1.1</span> 从概念到可验证操作</h2>
<p>这一段仅用于确认字号、行距、段间距与颜色。正文保持深色，品牌色承担导航与重点提示，避免整页强调色带来的浏览负担。</p>
<div class="note">提示框：先写清输入与输出，再决定采用的操作。示例命令 <code>run_example()</code> 使用等宽字体。</div>
<div class="diagram"><div class="card">输入条件</div><div class="card">执行与观察</div><div class="card">核验结果</div></div><h2>辅助色按教学任务选择</h2><p>warm-focus：暖色聚焦；balanced：冷暖平衡；cool-structure：冷色结构。品牌强调保持当前主色，同概念跨图同色。</p><div style="display:flex;gap:8px"><span style="background:#F8E5D8;padding:12px">peach</span><span style="background:#FFF4DE;padding:12px">sand</span><span style="background:#E7F0E4;padding:12px">sage</span><span style="background:#E4EDF5;padding:12px">blue</span><span style="background:#ECE6F3;padding:12px">lavender</span></div>
<table><tr><th>比较维度</th><th>示意方案</th></tr><tr><td>正文角色</td><td>清晰的无衬线中文与拉丁文</td></tr><tr><td>读者工件</td><td>图、表与提示框共享已批准色板</td></tr></table>
<footer>A4 纸张与原有目录结构保持；最终字号和分页以真实 PDF 量测与复核为准。</footer></main></html>"""
    _atomic_write(path, html)
    return path


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _defaults(language: str, publication_date: date) -> dict[str, str]:
    subtitle = "Professional Knowledge Guide" if language == "en-US" else "专业知识教程"
    return {
        "subtitle": subtitle,
        "cover_label": "知识产品工作流",
        "footer_label": "知识产品工作流",
        "copyright_line": f"© {publication_date.year} 知识产品工作流",
        "edition_label": "v1.0.0",
    }


def _read_profile(path: Path) -> PublicationProfile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"出版配置格式无效：{path}") from error
    if not isinstance(raw, dict):
        raise TypeError(f"出版配置必须是 YAML 对象：{path}")
    try:
        return PublicationProfile.model_validate(raw)
    except ValueError as error:
        raise ValueError(f"出版配置字段无效：{path}") from error


def _legacy_metadata_values(workspace: Workspace) -> dict[str, object]:
    """Read v1.0.1 render metadata only to seed a missing modern profile."""
    path = publication_metadata_path(workspace)
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(raw, dict):
        return {}
    values: dict[str, object] = {}
    for source, target in (
        ("subtitle", "subtitle"),
        ("publisher_label", "cover_label"),
        ("publisher_label", "footer_label"),
        ("copyright_line", "copyright_line"),
        ("edition", "edition_label"),
        ("publication_date", "publication_date"),
    ):
        value = raw.get(source)
        if isinstance(value, str) and value.strip():
            values[target] = value.strip()
        elif source == "publication_date" and isinstance(value, date):
            values[target] = value
    return values


def _legacy_seed(
    overrides: PresentationOverrides | None,
    defaults: dict[str, str],
    published_on: date,
    legacy_metadata: dict[str, object],
) -> dict[str, object]:
    values: dict[str, object] = {**defaults, "publication_date": published_on}
    values.update(legacy_metadata)
    if overrides is None:
        return values
    legacy_edition = overrides.edition or values["edition_label"]
    return {
        "subtitle": overrides.subtitle or values["subtitle"],
        "cover_label": overrides.publisher_label or values["cover_label"],
        "footer_label": overrides.publisher_label or values["footer_label"],
        "copyright_line": overrides.copyright_line or (
            f"© {published_on.year} {overrides.publisher_label}"
            if overrides.publisher_label and not legacy_metadata else values["copyright_line"]
        ),
        # Retain a legacy reader-visible label. SemVer bumping will later
        # request an explicit vX.Y.Z label before it can calculate an increment.
        "edition_label": legacy_edition,
        "publication_date": overrides.publication_date or values["publication_date"],
        "brand_color_intent": overrides.brand_color_intent or "橙色基调",
    }


def load_or_create_publication_profile(
    workspace: Workspace,
    *,
    language: str,
    overrides: PresentationOverrides | None = None,
    today: date | None = None,
) -> PublicationProfile:
    """Read the stable profile, creating an explicit current default if needed."""
    path = publication_profile_path(workspace)
    if path.is_file():
        return _read_profile(path)
    published_on = today or datetime.now(UTC).date()
    seed = _legacy_seed(
        overrides,
        _defaults(language, published_on),
        published_on,
        _legacy_metadata_values(workspace),
    )
    # A pre-profile manuscript is historical content. Do not turn on new
    # authoring gates or silently enlarge its typography at first re-render.
    if any((workspace.root / "chapters").glob("chapter-[0-9][0-9].md")) or _legacy_metadata_values(workspace):
        seed.update({
            "profile_id": V5_PRESENTATION_PROFILE_ID, "schema_version": 2,
            "body_font_size": 12, "content_contract_enabled": False,
            **_DESIGN_DEFAULTS[DesignFamily.TECHNICAL_MANUAL],
            "running_header_mode": "section",
        })
    else:
        seed["schema_version"] = 4
        seed["reader_layout_revision"] = 2
        seed["cover_layout_revision"] = 2
        seed["illustration_style_revision"] = 2
        seed["paragraph_spacing"] = "compact"
        # Only consume already materialized Brief content. The renderer never
        # invents a summary; absent content returns a recoverable preflight error.
        brief = workspace.root / "planning/product-brief.md"
        if brief.is_file():
            from knowledge_product_workflow.frontmatter import parse_markdown_artifact
            from knowledge_product_workflow.workflows.product_brief import (
                parse_presentation_overrides,
            )
            _, brief_body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
            supplied = parse_presentation_overrides(brief_body)
            summary = re.search(r"^## (?:产品定位|Product positioning)\s*\n+(.*?)(?=^## |\Z)", brief_body, re.MULTILINE | re.DOTALL)
            if supplied and supplied.cover_summary_zh:
                seed["cover_summary_zh"] = supplied.cover_summary_zh
            elif summary and summary.group(1).strip():
                seed["cover_summary_zh"] = summary.group(1).strip()
        # Native generation is opt-in through the Brief publication preview.
        # The CLI cannot infer the host's subscription or available tools.
        seed["illustration_backend_preference"] = "vector"
    if overrides is not None:
        seed.update({name: getattr(overrides, name) for name in COVER_FIELDS if getattr(overrides, name) is not None})
    profile = PublicationProfile(**seed)
    validate_cover_contract(profile, _workspace_title(workspace))
    _atomic_write(
        path,
        yaml.safe_dump(profile.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
    )
    workspace.append_event(
        "publication_profile_created",
        {"profile_id": profile.profile_id, "revision": profile.revision, "profile": "planning/publication-profile.yaml"},
    )
    return profile


def _next_semver(value: str, bump: Literal["patch", "minor", "major", "none"]) -> str:
    if bump == "none":
        return value
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ValueError("当前 edition_label 不是 vX.Y.Z；请先显式设置版本号后再使用版本递增。")
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    if bump == "patch":
        return f"v{major}.{minor}.{patch + 1}"
    if bump == "minor":
        return f"v{major}.{minor + 1}.0"
    return f"v{major + 1}.0.0"


def preview_publication_update(
    workspace: Workspace,
    update: PublicationUpdateInput,
    *,
    language: str,
    overrides: PresentationOverrides | None = None,
) -> tuple[Path, PublicationUpdateCandidate]:
    """Write one pending, reviewable profile update without changing the live profile."""
    current = load_or_create_publication_profile(workspace, language=language, overrides=overrides)
    path = publication_profile_path(workspace)
    values = current.model_dump(mode="python")
    migrating_to_v6 = (
        current.profile_id in {LEGACY_PRESENTATION_PROFILE_ID, V5_PRESENTATION_PROFILE_ID}
        and update.brand_color_intent is not None
    )
    if migrating_to_v6:
        values["schema_version"] = 3
        values["profile_id"] = PRESENTATION_PROFILE_ID
        values["brand_color_intent"] = update.brand_color_intent
        values["palette"] = _brand_palette(update.brand_color_intent)
        values["body_font_size"] = 12
        values["paragraph_spacing"] = "relaxed"
        values["content_contract_enabled"] = True
    if update.design_family is not None and update.palette is None:
        for field, value in _DESIGN_DEFAULTS[update.design_family].items():
            if field != "palette" or values.get("profile_id") != PRESENTATION_PROFILE_ID:
                values[field] = value
        values["content_contract_enabled"] = True
    if update.brand_color_intent is not None and not migrating_to_v6:
        values["palette"] = _brand_palette(update.brand_color_intent)
    for field in (
        "subtitle",
        "cover_label",
        "footer_label",
        "copyright_line",
        "edition_label",
        "publication_date",
        "body_font_size",
        "brand_color_intent",
        "font_policy",
        "reading_context",
        "product_promise",
        "visual_direction",
        "design_family",
        "palette",
        "heading_scale",
        "line_length",
        "paragraph_spacing",
        "running_header_mode",
        "chapter_opener_mode",
        "component_emphasis",
        "source_presentation",
        "reader_layout_revision",
        "illustration_style_revision",
        *COVER_FIELDS,
    ):
        value = getattr(update, field)
        if value is not None:
            values[field] = value
    values["edition_label"] = _next_semver(values["edition_label"], update.version_bump)
    if update.version_bump != "none" and update.publication_date is None:
        values["publication_date"] = datetime.now(UTC).date()
    values["revision"] = current.revision + 1
    candidate_profile = PublicationProfile.model_validate(values)
    validate_cover_contract(candidate_profile, _workspace_title(workspace))
    if current.reader_layout_revision == 1 and candidate_profile.reader_layout_revision == 2:
        from knowledge_product_workflow.frontmatter import parse_markdown_artifact
        from knowledge_product_workflow.presentation_validation import validate_chapter_lead
        for chapter_path in sorted((workspace.root / "chapters").glob("chapter-[0-9][0-9].md")):
            _, chapter_body = parse_markdown_artifact(chapter_path.read_text(encoding="utf-8"))
            problems = validate_chapter_lead(chapter_body, chapter_path.name, workspace, required=True)
            if any(item.severity.value == "blocker" for item in problems):
                raise ValueError(f"布局2升级预检：{chapter_path.name}缺少合规导读；先在原布局amend-chapter并提交，再确认升级")
    changed_fields = sorted(
        field
        for field in PublicationProfile.model_fields
        if field not in {"revision", "schema_version", "profile_id"}
        and getattr(current, field) != getattr(candidate_profile, field)
    )
    if not changed_fields:
        from knowledge_product_workflow.publishing import _template_text

        # A package repair may change reader-visible rendering without changing
        # approved metadata. Only existing, stale canonical components qualify.
        for filename in ("knowledge-book-components.lua", "knowledge-book-components.tex", "knowledge-book-toc.tex", "knowledge-book-style.tex"):
            rendered = workspace.root / "delivery" / filename
            if not rendered.is_file():
                continue
            actual = rendered.read_text(encoding="utf-8")
            canonical = _template_text(filename, current.profile_id, current.reader_layout_revision)
            matches = actual.endswith(canonical) if filename == "knowledge-book-style.tex" else actual == canonical
            if not matches:
                changed_fields = ["rendering_resources"]
                break
        if not changed_fields:
            raise ValueError("出版信息更新没有产生任何可见变化。")
    candidate = PublicationUpdateCandidate(
        prior_profile_sha256=_sha256(path),
        prior_revision=current.revision,
        version_bump=update.version_bump,
        changed_fields=changed_fields,
        profile=candidate_profile,
    )
    output = publication_update_preview_path(workspace)
    _atomic_write(output, candidate.model_dump_json(indent=2) + "\n")
    workspace.append_event(
        "publication_profile_previewed",
        {"from_revision": current.revision, "changed_fields": changed_fields, "version_bump": update.version_bump},
    )
    return output, candidate


def apply_publication_update(workspace: Workspace, approved_by: str) -> PublicationProfile:
    """Approve a preview and atomically replace the current publication profile."""
    identity = approved_by.strip()
    if not identity:
        raise ValueError("提交出版信息更新需要审批身份")
    profile_path = publication_profile_path(workspace)
    preview_path = publication_update_preview_path(workspace)
    if not profile_path.is_file() or not preview_path.is_file():
        raise ValueError("缺少当前出版配置或待确认的出版信息预览")
    try:
        candidate = PublicationUpdateCandidate.model_validate_json(preview_path.read_text(encoding="utf-8"))
        current = _read_profile(profile_path)
    except (OSError, ValueError) as error:
        raise ValueError("出版信息预览或当前配置无效") from error
    if candidate.prior_profile_sha256 != _sha256(profile_path) or candidate.prior_revision != current.revision:
        raise ValueError("当前出版配置已变化，待确认预览已过期；请重新生成预览。")
    history = workspace.root / "planning" / "history"
    history.mkdir(parents=True, exist_ok=True)
    archived = history / f"publication-profile.r{current.revision}.yaml"
    if archived.exists():
        raise FileExistsError(f"出版配置历史版本已存在：{archived}")
    archived.write_bytes(profile_path.read_bytes())
    _atomic_write(
        profile_path,
        yaml.safe_dump(candidate.profile.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
    )
    preview_path.unlink()
    workspace.append_event(
        "publication_profile_updated",
        {
            "from_revision": current.revision,
            "to_revision": candidate.profile.revision,
            "changed_fields": candidate.changed_fields,
            "version_bump": candidate.version_bump,
            "approved_by": identity,
            "archived": str(archived.relative_to(workspace.root)).replace("\\", "/"),
        },
    )
    return candidate.profile


def _sha256(path: Path) -> str:
    from hashlib import sha256

    return sha256(path.read_bytes()).hexdigest()


def load_or_create_publication_metadata(
    workspace: Workspace,
    *,
    title: str,
    audience_label: str,
    language: str,
    overrides: PresentationOverrides | None = None,
    today: date | None = None,
) -> PublicationMetadata:
    """Derive current render metadata from Brief fields and the publication profile."""
    profile = load_or_create_publication_profile(
        workspace, language=language, overrides=overrides, today=today
    )
    metadata = PublicationMetadata(
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        title=title,
        audience_label=audience_label,
        subtitle=profile.subtitle,
        edition_label=profile.edition_label,
        publication_date=profile.publication_date,
        cover_label=profile.cover_label,
        footer_label=profile.footer_label,
        copyright_line=profile.copyright_line,
        body_font_size=profile.body_font_size,
        brand_color_intent=profile.brand_color_intent,
        font_policy=profile.font_policy,
        visual_rule_set=profile.visual_rule_set,
        design_family=profile.design_family,
        palette=profile.palette,
        heading_scale=profile.heading_scale,
        line_length=profile.line_length,
        paragraph_spacing=profile.paragraph_spacing,
        running_header_mode=profile.running_header_mode,
        chapter_opener_mode=profile.chapter_opener_mode,
        component_emphasis=profile.component_emphasis,
        source_presentation=profile.source_presentation,
        content_contract_enabled=profile.content_contract_enabled,
        reader_layout_revision=profile.reader_layout_revision,
        illustration_style_revision=profile.illustration_style_revision,
        **{name: getattr(profile, name) for name in COVER_FIELDS},
    )
    validate_cover_contract(metadata, title)
    path = publication_metadata_path(workspace)
    payload = yaml.safe_dump(metadata.model_dump(mode="json"), allow_unicode=True, sort_keys=False)
    previous = path.read_text(encoding="utf-8") if path.is_file() else None
    if previous != payload:
        _atomic_write(path, payload)
        workspace.append_event(
            "publication_metadata_refreshed",
            {"profile_id": metadata.profile_id, "profile_revision": metadata.profile_revision},
        )
    return metadata
