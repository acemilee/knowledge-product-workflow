"""Reader-facing labels and their declared teaching intent, without semantic auto-approval."""
import unicodedata
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OutlineViewKind(StrEnum):
    TOC = "toc"
    DESIGN = "design"
    REVIEW = "review"


def _text(value: str) -> str:
    value = unicodedata.normalize("NFC", value).strip()
    if not value or (value.startswith("<") and value.endswith(">")):
        raise ValueError("目录意图须填写具体内容，不能是空白或模板占位符")
    return value


class TitleStyle(BaseModel):
    """One explicit book-wide navigation style, confirmed with its blueprint."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["topic", "procedure", "question"] = Field(default="topic", description="topic以具体统摄中心命名，不把覆盖清单抄进标题；procedure为操作项，question为短问题。子节负责展开覆盖内容。")
    vocabulary: list[str] = Field(default_factory=list, max_length=40)
    examples: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("vocabulary", "examples")
    @classmethod
    def clean_references(cls, values: list[str]) -> list[str]:
        result = [_text(value) for value in values]
        if len(result) != len(set(result)):
            raise ValueError("目录风格用词或正例不能重复")
        return result


class HeadingIntent(BaseModel):
    """Content intent remains visible when a short label is renamed."""

    model_config = ConfigDict(extra="forbid")
    focus: str = Field(min_length=1, max_length=300, description="本节点的具体统摄中心，不是并排罗列的教学范围清单。")
    topics: list[str] = Field(min_length=1, max_length=30, description="不能遗漏的覆盖内容；可在明确子节定位，无须全部写入父标题。")
    reader_job: str = Field(min_length=1, max_length=600)

    @field_validator("focus", "reader_job")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return _text(value)

    @field_validator("topics")
    @classmethod
    def clean_topics(cls, values: list[str]) -> list[str]:
        result = [_text(value) for value in values]
        if len(result) != len(set(result)):
            raise ValueError("标题覆盖主题清单不能重复")
        return result


def chapter_heading_rows(chapter) -> list[dict]:
    """One fresh row per heading, also used by the authoring contract."""
    rows = []

    def add(node, number: str, parent: str | None):
        rows.append({"number": number, "title": node.title, "level": len(number.split(".")),
                     "parent_number": parent,
                     "navigation": node.navigation.model_dump(mode="json") if node.navigation else None})

    def sections(items, parent):
        for item in items:
            add(item, item.number, parent)
            sections(item.children, item.number)

    number = str(chapter.chapter_number)
    add(chapter, number, None)
    sections(chapter.section_outline, number)
    return rows


def outline_rows(submission) -> list[dict]:
    return [row for chapter in submission.chapters for row in chapter_heading_rows(chapter)]


def render_outline_view(submission, view: str) -> dict:
    """A view is evidence to inspect, never a model-quality or user approval."""
    if view not in {"toc", "design", "review"}:
        raise ValueError("蓝图视图仅支持toc、design或review")
    rows = outline_rows(submission)
    result = {"view": view, "navigation_policy_revision": submission.navigation_policy_revision,
              "title_style": submission.title_style.model_dump(mode="json") if submission.title_style else None,
              "semantic_approval": False}
    if view == "toc":
        result["headings"] = [{key: row[key] for key in ("number", "title", "level", "parent_number")} for row in rows]
        result["markdown"] = "".join(
            f"{row['number']}. {row['title']}\n" if row["level"] == 1
            else f"{'  ' * (row['level'] - 1)}- {row['number']} {row['title']}\n" for row in rows)
        return result
    result.update(headings=rows,
                  coverage_status="declared_not_semantically_verified" if submission.navigation_policy_revision == 1 else "legacy_unknown")
    lines = []
    if submission.title_style:
        style = submission.title_style
        lines.extend([f"目录风格：{style.mode}", f"读者用词：{'、'.join(style.vocabulary) or '由当前读者语境判断'}",
                      f"风格正例：{'；'.join(style.examples) or '未提供，不预设本书目录答案'}", ""])
    for chapter in submission.chapters:
        lines.extend([f"### {chapter.chapter_number}. {chapter.title}",
                      f"体裁：{chapter.chapter_archetype.value}；权重：{chapter.weight}",
                      f"范围：{chapter.scope}", f"教学取舍：{chapter.rationale}",
                      "学习目标：" + "；".join(chapter.learning_objectives)])
        for row in chapter_heading_rows(chapter):
            intent = row["navigation"]
            if intent:
                lines.append(f"- {row['number']} 中心：{intent['focus']}；覆盖：{'、'.join(intent['topics'])}；读者任务：{intent['reader_job']}")
            else:
                lines.append(f"- {row['number']}：旧蓝图未声明目录意图，覆盖信息未知。")
        lines.append("")
    result["markdown"] = "\n".join(lines).rstrip() + "\n"
    if view == "review":
        result.update(changes=[], changed_non_title_fields=[], semantic_checks_required=semantic_review_checks())
    return result


def semantic_review_checks() -> list[str]:
    return ["逐标题核对中心是否具体且能由目标读者理解。",
            "对照topics与reader_job，关键内容能否由本标题或明确的父子语境定位；不能因缩名而隐藏另一项教学任务。",
            "核同级区分及父子范围，必要比较与长术语可以保留。",
            "主展示仅用toc；设计说明单独展示，不在每个目录条目后加解释。",
            "字段未变、短标题或零告警不代表语义通过；未解决项交原蓝图审批。"]


def _without_heading_titles(submission) -> dict:
    value = submission.model_dump(mode="json")

    def sections(items):
        for item in items:
            item.pop("title")
            sections(item["children"])

    for chapter in value["chapters"]:
        chapter.pop("title")
        sections(chapter["section_outline"])
    return value


def _different_fields(before, after, path="") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys(), key=str):
            field = f"{path}.{key}" if path else key
            if key not in before or key not in after:
                result.append(field)
            else:
                result.extend(_different_fields(before[key], after[key], field))
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [field for index, (left, right) in enumerate(zip(before, after, strict=True))
                for field in _different_fields(left, right, f"{path}[{index}]")]
    return [] if before == after else [path]


def compare_outline_navigation(current, candidate) -> dict:
    """Compare normalized meaning fields while retaining all domain data named title."""
    result = render_outline_view(candidate, "review")
    before = {row["number"]: row for row in outline_rows(current)}
    changes = []
    for row in outline_rows(candidate):
        previous = before.get(row["number"])
        if previous is None or previous["title"] != row["title"]:
            changes.append({"number": row["number"], "before": previous["title"] if previous else None,
                            "after": row["title"], "navigation": previous["navigation"] if previous else None,
                            "proposed_navigation": row["navigation"]})
    result["changes"] = changes
    result["changed_non_title_fields"] = _different_fields(_without_heading_titles(current), _without_heading_titles(candidate))
    result["comparison_scope"] = "normalized_non_title_fields_only; not a semantic no-information-loss certificate"
    if changes:
        result["markdown"] = "\n".join(
            f"- {row['number']}：{row['before'] or '新增'} → {row['after']}" for row in changes
        ) + "\n\n" + result["markdown"]
    return result
