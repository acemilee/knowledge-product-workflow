"""Product brief workflow and its approved detail-constraint contract."""

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_serializer

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactMetadata, ArtifactStatus
from knowledge_product_workflow.presentation import PresentationOverrides
from knowledge_product_workflow.user_decisions import (
    UserDecisionTarget,
    consume_approved_user_decision,
    invalidate_user_decision,
    managed_user_decisions,
    require_approved_user_decision,
)
from knowledge_product_workflow.verification_context import (
    atomic_replace as _v_atomic_replace,
)
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import load_yaml as _v_load_yaml
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import read_text as _v_read_text
from knowledge_product_workflow.verification_context import (
    unlink as _v_unlink,
)
from knowledge_product_workflow.verification_context import (
    write_bytes as _v_write_bytes,
)
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workspace import Workspace

_CONSTRAINT_BLOCK = re.compile(
    r"^## (?:交付约束|Delivery constraints)\s*\n+```yaml\s*\n(?P<yaml>.*?)\n```",
    re.MULTILINE | re.DOTALL,
)
_PRESENTATION_BLOCK = re.compile(
    r"^## (?:出版信息|Publication information)\s*\n+```yaml\s*\n(?P<yaml>.*?)\n```",
    re.MULTILINE | re.DOTALL,
)
_TOPIC_HEADING = re.compile(r"^#\s+(?P<topic>.+?)\s*$", re.MULTILINE)
_TARGET_AUDIENCE_BLOCK = re.compile(
    r"^## (?:目标读者|Target readers)\s*\n+(?P<audience>.*?)(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)


class BriefSemanticFields(BaseModel):
    """Reader goals carried by the approved Brief, including partial amendments."""

    learning_outcomes: list[str] = Field(default_factory=list, max_length=12)
    product_positioning: str | None = Field(default=None, max_length=600)
    scope_inclusions: list[str] = Field(default_factory=list, max_length=20)
    scope_exclusions: list[str] = Field(default_factory=list, max_length=20)
    fixed_inputs: list[str] = Field(default_factory=list, max_length=100)
    reader_request: str | None = Field(default=None, min_length=1, max_length=8000)
    teaching_focus: Literal["method_practice", "implementation", "mixed"] | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_semantics(self, handler):
        value = handler(self)
        if not self.fixed_inputs:
            value.pop("fixed_inputs", None)
        if self.reader_request is None:
            value.pop("reader_request", None)
        if self.teaching_focus is None:
            value.pop("teaching_focus", None)
        return value

    @field_validator("learning_outcomes", "scope_inclusions", "scope_exclusions", "fixed_inputs")
    @classmethod
    def clean_items(cls, values):
        if any(not item.strip() or len(item.strip()) > 600 for item in values):
            raise ValueError("学习成果/范围每项须为1—600个字符")
        return [item.strip() for item in values]

    @field_validator("product_positioning", "reader_request")
    @classmethod
    def clean_positioning(cls, value):
        if value is not None and not value.strip():
            raise ValueError("产品定位不能为空白")
        return value.strip() if value else None


def parse_brief_semantics(body: str) -> BriefSemanticFields:
    """Read the human-visible lists; legacy generic paragraphs remain defaults."""
    def section(name):
        match = re.search(r"^## " + name + r"\s*\n+(.*?)(?=^## |\Z)", body, re.MULTILINE | re.DOTALL)
        return match.group(1).strip() if match else ""

    outcomes = re.findall(r"^- (.+)$", section("(?:学习成果|Learning outcomes)"), re.MULTILINE)
    positioning = section("(?:产品定位|Product positioning)")
    if positioning in {"面向中文用户、可审计且可交付的专业知识教程。",
                       "An auditable, deliverable professional knowledge tutorial for English-speaking readers."}:
        positioning = None
    scope = section("(?:范围与排除项|Scope and exclusions)")
    parts = re.split(r"^(?:不覆盖：|Exclusions:)\s*$", scope, flags=re.MULTILINE)
    included = re.findall(r"^- (.+)$", parts[0], re.MULTILINE)
    excluded = re.findall(r"^- (.+)$", parts[1], re.MULTILINE) if len(parts) > 1 else []
    focus_section = section("(?:教学呈现|Teaching presentation)")
    focus = None
    if focus_section:
        match = re.fullmatch(r"```yaml\s*\n(.*?)\n```", focus_section, re.DOTALL)
        if match is None:
            raise ValueError("教学呈现须为单个YAML块")
        value = yaml.safe_load(match.group(1))
        if not isinstance(value, dict) or set(value) != {"teaching_focus"}:
            raise ValueError("教学呈现仅允许teaching_focus")
        focus = value["teaching_focus"]
    return BriefSemanticFields(learning_outcomes=outcomes, product_positioning=positioning,
                              scope_inclusions=included, scope_exclusions=excluded,
                              teaching_focus=focus,
                              fixed_inputs=re.findall(r"^- (.+)$", section("(?:固定教学输入|Fixed teaching inputs)"), re.MULTILINE))


class BriefRevisionCandidate(BriefSemanticFields):
    """One reviewed, pending Brief revision."""

    schema_version: int = 1
    prior_brief_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_version: int = Field(ge=1)
    topic: str = Field(min_length=1)
    target_audience: str = Field(min_length=1)
    detail_constraints: DetailConstraints
    presentation: PresentationOverrides | None = None
    revision_kind: str = Field(pattern=r"^(content|delivery)$")
    changed_fields: list[str] = Field(min_length=1)
    change_reason: str | None = None


def render_detail_constraints(constraints: DetailConstraints) -> str:
    """Render the machine-readable contract into the human-reviewable brief."""
    payload = {"detail_constraints": constraints.model_dump(mode="json")}
    rendered = yaml.safe_dump(payload, sort_keys=False).rstrip()
    heading = "## 交付约束" if constraints.language == "zh-CN" else "## Delivery constraints"
    return f"{heading}\n\n```yaml\n{rendered}\n```\n"


def parse_detail_constraints(body: str) -> DetailConstraints | None:
    """Read V2 constraints; return ``None`` for legacy V1 product briefs."""
    match = _CONSTRAINT_BLOCK.search(body)
    if match is None:
        return None
    raw = _v_load_yaml(match.group("yaml"))
    if not isinstance(raw, dict) or "detail_constraints" not in raw:
        raise ValueError("交付约束格式不正确")
    return DetailConstraints.model_validate(raw["detail_constraints"])


def render_presentation_overrides(overrides: PresentationOverrides, language: str) -> str:
    """Render optional public publication values into a human-reviewable Brief."""
    heading = "## 出版信息" if language == "zh-CN" else "## Publication information"
    payload = {"presentation": overrides.model_dump(mode="json", exclude_none=True)}
    rendered = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).rstrip()
    return f"{heading}\n\n```yaml\n{rendered}\n```\n"


def parse_presentation_overrides(body: str) -> PresentationOverrides | None:
    """Read optional public publication values from a Product Brief."""
    match = _PRESENTATION_BLOCK.search(body)
    if match is None:
        return None
    raw = _v_load_yaml(match.group("yaml"))
    if not isinstance(raw, dict) or "presentation" not in raw:
        raise ValueError("出版信息格式不正确")
    return PresentationOverrides.model_validate(raw["presentation"])


def brief_topic(body: str) -> str:
    """Read the human-visible topic from a persisted Brief body."""
    match = _TOPIC_HEADING.search(body)
    if match is None:
        raise ValueError("产品 Brief 缺少主题标题")
    return match.group("topic").strip()


def brief_target_audience(body: str) -> str:
    """Read the human-visible target audience from a persisted Brief body."""
    match = _TARGET_AUDIENCE_BLOCK.search(body)
    if match is None or not match.group("audience").strip():
        raise ValueError("产品 Brief 缺少目标读者")
    return match.group("audience").strip()


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def brief_contract_fingerprints(body: str) -> dict[str, str]:
    """Return separate authoring and delivery fingerprints for one Brief.

    Page/word budgets remain authoring inputs because chapter budgets depend on
    them. Delivery format, layout, and publication metadata only affect output.
    """
    constraints = parse_detail_constraints(body)
    if constraints is None:
        rendered = json.dumps({"legacy_body": body}, ensure_ascii=False, sort_keys=True)
        digest = _sha256_bytes(rendered.encode("utf-8"))
        return {"content_contract_sha256": digest, "delivery_contract_sha256": digest}
    constraint_payload = constraints.model_dump(mode="json")
    reader_brief = _PRESENTATION_BLOCK.sub("", _CONSTRAINT_BLOCK.sub("", body)).strip()
    content_payload: dict[str, Any] = {
        "reader_brief": reader_brief,
        "constraints": {
            key: value
            for key, value in constraint_payload.items()
            if key not in {"delivery_format", "layout_profile"}
        },
    }
    presentation = parse_presentation_overrides(body)
    delivery_payload = {
        "delivery_format": constraint_payload["delivery_format"],
        "layout_profile": constraint_payload["layout_profile"],
        "presentation": presentation.model_dump(mode="json", exclude_none=True) if presentation else None,
    }
    return {
        "content_contract_sha256": _sha256_bytes(
            json.dumps(content_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
        "delivery_contract_sha256": _sha256_bytes(
            json.dumps(delivery_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
    }


def request_contract_path(workspace: Workspace) -> Path:
    """Return the internal user-requirement provenance path."""
    return workspace.root / "runs" / "request-contract.yaml"


def require_original_reader_request(workspace: Workspace, semantics: BriefSemanticFields | None = None,
                                    *, persisted: bool = False) -> None:
    """新托管路径保护捕获原文；旧档案不静默升级，不声称机器证明理解完整。"""
    from knowledge_product_workflow.runtime import _read_orchestration_state
    state = _read_orchestration_state(workspace)
    if state is None or state.request_capture_revision < 1:
        return
    if persisted:
        contract = read_request_contract(workspace) or {}
        original = contract.get('requirements', {}).get('reader_request', {}).get('value')
        if (not isinstance(original, str) or not original.strip()
                or contract.get('reader_request_sha256') != _sha256_bytes(original.encode('utf-8'))):
            raise ValueError('缺少当前原始读者需求或捕获记录已变化；以当前原文重新生成Brief预览，不能跳过审批。')
    elif semantics is None or not semantics.reader_request:
        raise ValueError('新托管Brief必须保留原始读者需求；宿主将当前原文放入 --from 输入的 reader_request 后重试。')


def write_request_contract(
    workspace: Workspace,
    topic: str,
    target_audience: str,
    constraints: DetailConstraints,
    requirement_sources: dict[str, str],
    brand_color_intent: str | None = None,
    semantics: BriefSemanticFields | None = None,
) -> Path:
    """Persist resolved Brief values together with their source classification."""
    values = constraints.model_dump(mode="json")
    payload = {
        "schema_version": 1,
        "topic": topic,
        "target_audience": target_audience,
        "requirements": {
            key: {"value": values.get(key), "source": requirement_sources.get(key, "default")}
            for key in (
                "target_pages",
                "page_constraint",
                "target_words",
                "language",
                "layout_profile",
                "audience_level",
                "delivery_format",
                "review_policy",
            )
        },
    }
    payload["requirements"]["brand_color_intent"] = {
        "value": brand_color_intent or "橙色基调",
        "source": requirement_sources.get("brand_color_intent", "default"),
    }
    if semantics is not None:
        for key in BriefSemanticFields.model_fields:
            value = getattr(semantics, key)
            payload["requirements"][key] = {
                "value": value, "source": requirement_sources.get(key, "model_recommendation")
            }
        if semantics.reader_request:
            payload['reader_request_sha256'] = _sha256_bytes(semantics.reader_request.encode('utf-8'))
            payload['requirements']['reader_request']['source'] = 'user_explicit'
    output = request_contract_path(workspace)
    _v_write_text(output, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return output


def read_request_contract(workspace: Workspace) -> dict[str, Any] | None:
    """Read requirement provenance; it is advisory for legacy workspaces."""
    path = request_contract_path(workspace)
    if not _v_is_file(path):
        return None
    try:
        payload = _v_load_yaml(_v_read_text(path, encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return payload if isinstance(payload, dict) else None


def render_product_brief_body(
    topic: str,
    target_audience: str | None,
    constraints: DetailConstraints | None,
    presentation: PresentationOverrides | None,
    semantics: BriefSemanticFields | None = None,
) -> str:
    """Render the reviewable Brief body for initial creation or a revision."""
    if semantics and semantics.reader_request:
        from knowledge_product_workflow.detail_constraints import validate_source_page_intent
        validate_source_page_intent(semantics.reader_request, constraints)
    constraints_section = f"\n{render_detail_constraints(constraints)}" if constraints else ""
    presentation_section = (
        f"\n{render_presentation_overrides(presentation, constraints.language if constraints else 'zh-CN')}"
        if presentation and presentation.has_values()
        else ""
    )
    reader_description = target_audience or "具备 Python 基础、希望系统学习相关专业主题的中文读者"
    if constraints is not None and constraints.language == "en-US":
        body = f"""# {topic}\n\n## Target readers\n\n{reader_description}\n\n## Learning outcomes\n\nReaders can explain the core concepts, complete verifiable practice, and apply the knowledge in real scenarios.\n\n## Product positioning\n\nAn auditable, deliverable professional knowledge tutorial for English-speaking readers.\n\n## Scope and exclusions\n\nThe tutorial covers concepts, cases, practice, assessment, and engineering methods needed for the topic. It does not replace legal, medical, financial, or other domain-specific professional advice.\n{constraints_section}"""
    else:
        body = f"""# {topic}\n\n## 目标读者\n\n{reader_description}\n\n## 学习成果\n\n能够理解核心概念、完成可验证的实践，并将知识应用到真实场景。\n\n## 产品定位\n\n面向中文用户、可审计且可交付的专业知识教程。\n\n## 范围与排除项\n\n聚焦主题所需的概念、案例、实践、评估与工程化方法；不替代特定业务的法律、医疗或财务专业意见。\n{constraints_section}"""
    if semantics is not None:
        english = constraints is not None and constraints.language == "en-US"
        replacements = {}
        if semantics.learning_outcomes:
            replacements["Learning outcomes" if english else "学习成果"] = "\n".join(
                "- " + item for item in semantics.learning_outcomes
            )
        if semantics.product_positioning:
            replacements["Product positioning" if english else "产品定位"] = semantics.product_positioning
        if semantics.scope_inclusions or semantics.scope_exclusions:
            scope = ("Inclusions:" if english else "覆盖：") + "\n\n"
            scope += "\n".join("- " + item for item in semantics.scope_inclusions)
            scope += "\n\n" + ("Exclusions:" if english else "不覆盖：") + "\n\n"
            scope += "\n".join("- " + item for item in semantics.scope_exclusions)
            replacements["Scope and exclusions" if english else "范围与排除项"] = scope
        for heading, content in replacements.items():
            pattern = r"(^## " + re.escape(heading) + r"\s*\n+).*?(?=^## |\Z)"
            body = re.sub(pattern, lambda m, value=content: m.group(1) + value + "\n\n", body, flags=re.MULTILINE | re.DOTALL)
    if semantics and semantics.fixed_inputs:
        body += "\n## " + ("Fixed teaching inputs" if constraints and constraints.language == "en-US" else "固定教学输入") + "\n\n"
        body += "\n".join("- " + item for item in semantics.fixed_inputs) + "\n"
    if semantics and semantics.teaching_focus is not None:
        title = "Teaching presentation" if constraints and constraints.language == "en-US" else "教学呈现"
        body += f"\n## {title}\n\n```yaml\nteaching_focus: {semantics.teaching_focus}\n```\n"
    return body + presentation_section


def create_product_brief(
    workspace: Workspace,
    topic: str,
    constraints: DetailConstraints | None = None,
    target_audience: str | None = None,
    presentation: PresentationOverrides | None = None,
    semantics: BriefSemanticFields | None = None,
) -> Path:
    """Create a reviewable product brief without overwriting a prior brief."""
    require_original_reader_request(workspace, semantics)
    output = workspace.root / "planning" / "product-brief.md"
    if _v_exists(output):
        raise FileExistsError(f"Product brief already exists: {output}")
    metadata = ArtifactMetadata(
        book_id=workspace.root.name,
        artifact_type="product_brief",
        status=ArtifactStatus.READY_FOR_REVIEW,
        version=1,
    )
    body = render_product_brief_body(topic, target_audience, constraints, presentation, semantics)
    if semantics and semantics.reader_request and not _v_exists(request_contract_path(workspace)):
        write_request_contract(workspace, topic, target_audience or '', constraints or DetailConstraints(), {}, semantics=semantics)
    _v_write_text(output, render_markdown_artifact(metadata, body), encoding="utf-8")
    workspace.append_event(
        "product_brief_created",
        {"topic": topic, "artifact": str(output), "has_detail_constraints": constraints is not None},
    )
    return output


def replace_unapproved_brief_draft(
    workspace: Workspace,
    topic: str,
    constraints: DetailConstraints | None = None,
    target_audience: str | None = None,
    presentation: PresentationOverrides | None = None,
    semantics: BriefSemanticFields | None = None,
) -> Path:
    """Replace an initial Brief draft while retaining an auditable draft history.

    A Brief remains mutable until its first approval. Once a blueprint or
    chapter exists, the product has crossed into the versioned amendment path,
    so this narrow replacement operation must refuse to overwrite it.
    """
    output = workspace.root / "planning" / "product-brief.md"
    if not _v_is_file(output):
        return create_product_brief(workspace, topic, constraints, target_audience, presentation, semantics)
    metadata, _ = parse_markdown_artifact(output.read_text(encoding="utf-8"))
    if metadata.status is not ArtifactStatus.READY_FOR_REVIEW:
        raise ValueError("当前产品 Brief 已进入审批或生产流程，不能替换初始草案")
    downstream = (
        workspace.root / "planning" / "curriculum-blueprint.md",
        workspace.root / "chapters" / "chapter-01-contract.md",
    )
    if any(_v_exists(path) for path in downstream) or any((workspace.root / "chapters").glob("chapter-*.md")):
        raise ValueError("当前 Brief 已被下游工件使用，不能替换初始草案")

    original = output.read_bytes()
    digest = _sha256_bytes(original)[:12]
    history = workspace.root / "planning" / "draft-history"
    _v_mkdir(history,parents=True,exist_ok=True)
    archived = history / f"product-brief.v{metadata.version}-{digest}.md"
    if not _v_exists(archived):
        _v_write_bytes(archived, original)

    request_contract = request_contract_path(workspace)
    if _v_is_file(request_contract):
        request_archive = history / f"request-contract.v{metadata.version}-{digest}.yaml"
        if not _v_exists(request_archive):
            _v_write_bytes(request_archive, request_contract.read_bytes())

    invalidate_user_decision(
        workspace,
        UserDecisionTarget.BRIEF,
        reason="初始 Brief 草案已更新",
        include_consumed=True,
    )
    replacement = ArtifactMetadata(
        book_id=metadata.book_id,
        artifact_type=metadata.artifact_type,
        status=ArtifactStatus.READY_FOR_REVIEW,
        version=metadata.version + 1,
        created_at=metadata.created_at,
        depends_on=metadata.depends_on,
    )
    body = render_product_brief_body(topic, target_audience, constraints, presentation, semantics)
    temporary = output.with_suffix(".md.tmp")
    _v_write_text(temporary, render_markdown_artifact(replacement, body), encoding="utf-8")
    _v_atomic_replace(temporary, output)
    workspace.append_event(
        "product_brief_draft_replaced",
        {
            "topic": topic,
            "artifact": str(output),
            "prior_version": metadata.version,
            "replacement_version": replacement.version,
            "archived": str(archived.relative_to(workspace.root)).replace("\\", "/"),
        },
    )
    return output


def brief_amendment_preview_path(workspace: Workspace) -> Path:
    """Return the one controlled pending Brief revision path."""
    return workspace.root / "runs" / "brief-amendment-preview.json"


def create_brief_amendment_preview(
    workspace: Workspace,
    candidate: BriefRevisionCandidate,
) -> Path:
    """Persist a checked amendment candidate without mutating the approved Brief."""
    output = brief_amendment_preview_path(workspace)
    invalidate_user_decision(
        workspace,
        UserDecisionTarget.BRIEF_AMENDMENT,
        reason="Brief 修订预览已更新",
    )
    _v_write_text(output, candidate.model_dump_json(indent=2), encoding="utf-8")
    workspace.append_event(
        "brief_amendment_previewed",
        {
            "prior_version": candidate.prior_version,
            "revision_kind": candidate.revision_kind,
            "changed_fields": candidate.changed_fields,
        },
    )
    return output


def approve_brief_amendment(workspace: Workspace, approved_by: str) -> Path:
    """Commit one reviewed Brief revision and archive its exact predecessor."""
    brief_path = workspace.root / "planning" / "product-brief.md"
    preview_path = brief_amendment_preview_path(workspace)
    if not _v_is_file(brief_path) or not _v_is_file(preview_path):
        raise ValueError("缺少当前 Brief 或待批准的 Brief 修订预览")
    decision_record = None
    if managed_user_decisions(workspace):
        decision_record = require_approved_user_decision(
            workspace,
            UserDecisionTarget.BRIEF_AMENDMENT,
        )
    metadata, _ = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    if metadata.status is not ArtifactStatus.APPROVED:
        raise ValueError("当前产品 Brief 尚未批准，不能创建修订")
    try:
        candidate = BriefRevisionCandidate.model_validate_json(preview_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Brief 修订预览无效：{error}") from error
    current_sha = _sha256_bytes(brief_path.read_bytes())
    if candidate.prior_brief_sha256 != current_sha or candidate.prior_version != metadata.version:
        raise ValueError("当前 Brief 已变化，待批准修订过期；请重新运行 kp amend-brief")
    history = workspace.root / "planning" / "history"
    _v_mkdir(history,exist_ok=True)
    archived = history / f"product-brief.v{metadata.version}.md"
    if _v_exists(archived):
        raise FileExistsError(f"Brief 历史版本已存在：{archived}")
    _v_write_bytes(archived, brief_path.read_bytes())
    current_request = read_request_contract(workspace)
    request_path = request_contract_path(workspace)
    if _v_is_file(request_path):
        _v_write_bytes(history / f"request-contract.v{metadata.version}.yaml", request_path.read_bytes())
    revised = ArtifactMetadata(
        book_id=metadata.book_id,
        artifact_type=metadata.artifact_type,
        status=ArtifactStatus.APPROVED,
        version=metadata.version + 1,
        created_at=metadata.created_at,
        depends_on=metadata.depends_on,
        approved_by=approved_by,
    )
    body = render_product_brief_body(
        candidate.topic,
        candidate.target_audience,
        candidate.detail_constraints,
        candidate.presentation,
        candidate,
    )
    _v_write_text(brief_path, render_markdown_artifact(revised, body), encoding="utf-8")
    sources = {key: item.get("source", "default")
               for key, item in (current_request or {}).get("requirements", {}).items()}
    for field in candidate.changed_fields:
        sources[field] = "approved_revision"
    if "target_pages" in candidate.changed_fields:
        sources["page_constraint"] = "approved_revision"
    write_request_contract(workspace, candidate.topic, candidate.target_audience,
                           candidate.detail_constraints, sources,
                           candidate.presentation.brand_color_intent if candidate.presentation else None,
                           candidate)
    if decision_record is not None:
        consume_approved_user_decision(
            workspace,
            UserDecisionTarget.BRIEF_AMENDMENT,
            record=decision_record,
        )
    _v_unlink(preview_path)
    workspace.append_event(
        "brief_amendment_approved",
        {
            "from_version": metadata.version,
            "to_version": revised.version,
            "revision_kind": candidate.revision_kind,
            "changed_fields": candidate.changed_fields,
            "archived": str(archived.relative_to(workspace.root)).replace("\\", "/"),
        },
    )
    return brief_path
