"""Reader-facing publication profile and derived PDF metadata."""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_product_workflow.workspace import Workspace

LEGACY_PRESENTATION_PROFILE_ID = "knowledge-product-pdf-v4"
PRESENTATION_PROFILE_ID = "knowledge-product-pdf-v5"
PUBLICATION_PROFILE_FILENAME = "publication-profile.yaml"
PUBLICATION_METADATA_FILENAME = "publication-metadata.yaml"
PUBLICATION_UPDATE_PREVIEW_FILENAME = "publication-update-preview.json"
_SEMVER = re.compile(r"^v(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")
_SUPPORTED_BODY_FONT_SIZES = frozenset({12, 14})
_HEX_COLOR = re.compile(r"^[0-9A-F]{6}$")


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
        "running_header_mode": "section",
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


class PresentationOverrides(BaseModel):
    """Legacy optional values accepted from an existing Product Brief.

    New reader-facing changes belong to ``publication-profile.yaml``. These
    fields remain only so pre-v1.0.2 Brief files can seed a profile once.
    """

    model_config = ConfigDict(extra="forbid")

    subtitle: str | None = Field(default=None, max_length=180)
    edition: str | None = Field(default=None, max_length=80)
    publication_date: date | None = None
    publisher_label: str | None = Field(default=None, max_length=100)
    copyright_line: str | None = Field(default=None, max_length=180)

    @field_validator("subtitle", "edition", "publisher_label", "copyright_line")
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


class PublicationProfile(BaseModel):
    """The only user-editable, reader-visible publication configuration."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=2, ge=1)
    profile_id: str = PRESENTATION_PROFILE_ID
    revision: int = Field(default=1, ge=1)
    subtitle: str = Field(min_length=1, max_length=180)
    cover_label: str = Field(min_length=1, max_length=100)
    footer_label: str = Field(min_length=1, max_length=100)
    copyright_line: str = Field(min_length=1, max_length=180)
    edition_label: str = Field(min_length=1, max_length=80)
    publication_date: date
    body_font_size: int = 12
    reading_context: str = Field(default="专业知识阅读", min_length=1, max_length=80)
    product_promise: str = Field(default="可执行的专业知识指导", min_length=1, max_length=160)
    visual_direction: str = Field(default="清晰、克制、以阅读任务为中心", min_length=1, max_length=120)
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

    @model_validator(mode="before")
    @classmethod
    def seed_design_defaults(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        values = dict(raw)
        profile_id = values.get("profile_id", PRESENTATION_PROFILE_ID)
        schema_version = values.get("schema_version", 2)
        if profile_id == LEGACY_PRESENTATION_PROFILE_ID or schema_version == 1:
            return values
        family = DesignFamily(values.get("design_family", DesignFamily.TECHNICAL_MANUAL))
        values.setdefault("schema_version", 2)
        values.setdefault("profile_id", PRESENTATION_PROFILE_ID)
        for field, value in _DESIGN_DEFAULTS[family].items():
            values.setdefault(field, value)
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
        if self.profile_id != PRESENTATION_PROFILE_ID or self.schema_version != 2:
            raise ValueError(f"不支持的出版视觉配置：{self.profile_id}/v{self.schema_version}")
        if _contrast_ratio(self.palette.ink, self.palette.paper) < 4.5:
            raise ValueError("正文颜色与纸色对比度不足，无法保证可读性。")
        if _contrast_ratio(self.palette.accent, self.palette.paper) < 3.0:
            raise ValueError("强调色与纸色对比度不足，无法保证导航可读性。")
        return self


class PublicationMetadata(BaseModel):
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


class PublicationUpdateInput(BaseModel):
    """Host-filled semantic input for one reader-visible publication update."""

    model_config = ConfigDict(extra="forbid")

    subtitle: str | None = Field(default=None, max_length=180)
    cover_label: str | None = Field(default=None, max_length=100)
    footer_label: str | None = Field(default=None, max_length=100)
    copyright_line: str | None = Field(default=None, max_length=180)
    body_font_size: int | None = None
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
        "copyright_line": f"版权所有 {publication_date.year} 知识产品工作流",
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
        "copyright_line": overrides.copyright_line or values["copyright_line"],
        # Retain a legacy reader-visible label. SemVer bumping will later
        # request an explicit vX.Y.Z label before it can calculate an increment.
        "edition_label": legacy_edition,
        "publication_date": overrides.publication_date or values["publication_date"],
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
    profile = PublicationProfile(
        **_legacy_seed(
            overrides,
            _defaults(language, published_on),
            published_on,
            _legacy_metadata_values(workspace),
        )
    )
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
    if current.profile_id == LEGACY_PRESENTATION_PROFILE_ID:
        values["schema_version"] = 2
        values["profile_id"] = PRESENTATION_PROFILE_ID
        for field, value in _DESIGN_DEFAULTS[DesignFamily.TECHNICAL_MANUAL].items():
            values[field] = value
    if update.design_family is not None and update.palette is None:
        for field, value in _DESIGN_DEFAULTS[update.design_family].items():
            values[field] = value
        values["content_contract_enabled"] = True
    for field in (
        "subtitle",
        "cover_label",
        "footer_label",
        "copyright_line",
        "edition_label",
        "publication_date",
        "body_font_size",
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
    ):
        value = getattr(update, field)
        if value is not None:
            values[field] = value
    values["edition_label"] = _next_semver(values["edition_label"], update.version_bump)
    if update.version_bump != "none" and update.publication_date is None:
        values["publication_date"] = datetime.now(UTC).date()
    values["revision"] = current.revision + 1
    candidate_profile = PublicationProfile.model_validate(values)
    changed_fields = sorted(
        field
        for field in PublicationProfile.model_fields
        if field not in {"revision", "schema_version", "profile_id"}
        and getattr(current, field) != getattr(candidate_profile, field)
    )
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
    )
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
