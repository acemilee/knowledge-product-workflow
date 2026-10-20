"""Command-line entry point for the knowledge product workflow."""

import json
import re
import shutil
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from knowledge_product_workflow.assumption_ledger import (
    AssumptionDisposition,
    AssumptionImpact,
    AssumptionLedgerEntry,
    append_assumption_entry,
)
from knowledge_product_workflow.chapter_compliance import (
    ChapterComplianceMappingInput,
    ChapterSubmissionError,
    chapter_compliance_schema,
    check_chapter,
    create_chapter_compliance_template,
    submit_chapter,
)
from knowledge_product_workflow.claim_classification import (
    ClaimClassificationInput,
    append_claim_classification,
    classification_schema,
)
from knowledge_product_workflow.claims import current_claim_contract_bindings
from knowledge_product_workflow.content_plan import (
    ContentPlanError,
    content_plan_schema,
    create_content_plan_template,
    preflight_content_plan_input,
    submit_content_plan,
)
from knowledge_product_workflow.d2 import resolve_d2, resolve_diagram_browser
from knowledge_product_workflow.detail_constraints import (
    DetailConstraints,
    normalize_localized_constraints,
    parse_page_budget,
    parse_word_budget,
)
from knowledge_product_workflow.developmental import (
    DevelopmentalEditError,
    StructureReviewInput,
    create_structure_review_template,
    record_developmental_check,
    record_structure_review,
)
from knowledge_product_workflow.diagrams import (
    DiagramSubmissionError,
    create_diagram_template,
    diagram_schema,
    insert_diagram_reference,
    submit_diagram,
)
from knowledge_product_workflow.frontmatter import (
    parse_markdown_artifact,
    render_markdown_artifact,
)
from knowledge_product_workflow.humanize import HumanizeReportError, record_humanize_check
from knowledge_product_workflow.illustration_budget import (
    freeze_image_budget,
    load_image_budget,
    record_image_call,
)
from knowledge_product_workflow.illustrations import (
    AssetStyleInput,
    preflight_illustration,
    record_asset_style,
    style_resources,
)
from knowledge_product_workflow.localization import ChineseTyperCommand, ChineseTyperGroup
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.outline_navigation import OutlineViewKind
from knowledge_product_workflow.pdf_preview import render_pdf_preview_set, resolve_pdftoppm
from knowledge_product_workflow.pdf_visual import record_manual_pdf_visual_review
from knowledge_product_workflow.presentation import (
    PresentationOverrides,
    PublicationUpdateInput,
    _brand_palette,
    apply_publication_update,
    create_publication_design_preview,
    load_or_create_publication_profile,
    preview_publication_update,
    validate_cover_contract,
)
from knowledge_product_workflow.publishing import (
    assemble_book,
    create_delivery_manifest,
    refresh_publication_delivery,
    render_pdf,
)
from knowledge_product_workflow.quality import QualityReport, Severity
from knowledge_product_workflow.quality_exceptions import (
    ControlledExceptionRequest,
    controlled_exception_schema,
    submit_controlled_exceptions,
)
from knowledge_product_workflow.quarto import resolve_quarto
from knowledge_product_workflow.review import (
    ReviewCheckpointRequiredError,
    checkpoint_chapter,
    create_review_work_order,
)
from knowledge_product_workflow.review_batches import (
    ReviewResultInput,
    submit_review_batch,
    submit_review_result,
    validate_review_batches,
)
from knowledge_product_workflow.runtime import (
    HostCapabilityPreflightError,
    OrchestrationWorkOrderError,
    ResearchImpact,
    WorkflowAuthorizationError,
    _prepare_delivery_action,
    authorize_command,
    authorize_workflow_transition,
    cancel_orchestration_work_order,
    capture_orchestration_action,
    capture_orchestration_update,
    complete_orchestration_action,
    complete_orchestration_work_order,
    create_orchestration_work_order,
    create_work_order,
    fail_orchestration_action,
    inspect_workflow,
    orchestration_migration_preview,
    pause_run_intent,
    preflight_independent_review,
    record_research_impact,
    refresh_orchestration_after_controlled_update,
    route_workflow,
    start_run_intent,
    supersede_orchestration_work_orders,
)
from knowledge_product_workflow.source_ledger import (
    SourceLedgerEntry,
    append_source_entry,
    validate_claims,
)
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.user_decisions import (
    UserDecisionTarget,
    UserResponseDecision,
    consume_approved_user_decision,
    managed_user_decisions,
    request_user_decision,
    require_approved_user_decision,
    target_for_artifact,
)
from knowledge_product_workflow.user_decisions import (
    record_user_decision as persist_user_decision,
)
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import read_bytes as _v_read_bytes
from knowledge_product_workflow.visual_audit import (
    VisualObservation,
    audit_path,
    audit_pdf_visual,
    record_visual_observations,
)
from knowledge_product_workflow.workflows.authoring import (
    amend_chapter_draft,
    approve_sample_chapter,
    compile_chapter_contracts,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.curriculum import (
    BlueprintSubmissionError,
    CurriculumPrerequisiteError,
    amend_curriculum_blueprint,
    create_curriculum_blueprint_template,
    create_section_outline_migration_draft,
    create_teaching_length_recommendation_template,
    curriculum_outline_schema,
    inspect_curriculum_outline,
    parse_chapter_budgets,
    submit_curriculum_blueprint,
    submit_teaching_length_recommendation,
)
from knowledge_product_workflow.workflows.product_brief import (
    BriefRevisionCandidate,
    BriefSemanticFields,
    approve_brief_amendment,
    brief_contract_fingerprints,
    brief_target_audience,
    brief_topic,
    create_brief_amendment_preview,
    create_product_brief,
    parse_brief_semantics,
    parse_detail_constraints,
    parse_presentation_overrides,
    read_request_contract,
    render_product_brief_body,
    replace_unapproved_brief_draft,
    request_contract_path,
    require_original_reader_request,
    write_request_contract,
)
from knowledge_product_workflow.workflows.validation import run_delivery_validation, run_validation
from knowledge_product_workflow.workspace import Workspace


def _illustration_authorization(workspace: Workspace, chapter_number: int, source: Path) -> None:
    """Conditional child work stays inside author lease; display repairs preserve frozen text."""
    submitted = workspace.root / "chapters" / f"chapter-{chapter_number:02d}.md"
    if _v_is_file(submitted):
        metadata, body = parse_markdown_artifact(submitted.read_text(encoding="utf-8"))
        if metadata.status not in {ArtifactStatus.DRAFT, ArtifactStatus.IN_PRODUCTION}:
            from knowledge_product_workflow.diagrams import _reader_body
            if body.strip() != _reader_body(source).strip():
                raise ValueError("已提交章节的正文改变须先amend-chapter；纯展示修复不能改正文")
            return
    capture_orchestration_action(workspace, "submit-chapter", chapter_number=chapter_number)


def _configure_piped_text_streams() -> None:
    """Make captured CLI text UTF-8 without changing an interactive Windows console.

    Windows inherits a legacy code page for a redirected Python stream on some
    hosts. JSON and error output are then GBK bytes even though Skill hosts
    decode subprocess output as UTF-8. A TTY keeps its own console encoding;
    only pipe/file streams are normalized here.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="strict")
        except (AttributeError, OSError):
            # Test runners and embedded hosts may supply a text proxy without reconfigure().
            continue


_configure_piped_text_streams()


app = typer.Typer(
    help="知识产品工作流。完整命令索引：kp commands --all。",
    cls=ChineseTyperGroup,
    rich_markup_mode=None,
    options_metavar="[选项]",
    subcommand_metavar="命令 [参数]...",
    add_completion=False,
)
WorkspacePath = Annotated[Path, typer.Option(..., help="已有书稿工作区路径")]
EditorIdentity = Annotated[str | None, typer.Option(help="临时覆盖已保存的审批身份")]
AuthorIdentity = Annotated[str, typer.Option(..., help="本章正文作者身份标识")]
AuthorSessionIdentity = Annotated[str, typer.Option(..., help="本章作者宿主会话标识")]
ClaimId = Annotated[list[str], typer.Option(..., help="需要来源支撑的事实声明标识；可重复指定 --claim，同一证据组可关联多项声明")]
EvidenceExcerpt = Annotated[str, typer.Option(..., help="支撑事实声明的来源摘录")]
LicenseNote = Annotated[str, typer.Option(..., help="来源许可或使用说明")]
_STAGE_LABELS = {
    "delivery_preflight_pending": "等待自动交付预检",
    "delivery_preflight_blocked": "自动交付预检发现阻断项",
    "brief_missing": "缺少产品 Brief",
    "brief_invalid": "产品 Brief 无效",
    "brief_awaiting_approval": "等待产品 Brief 审批",
    "independent_review_capability_pending": "独立审稿能力未确认",
    "blueprint_missing": "缺少课程蓝图",
    "blueprint_invalid": "课程蓝图无效",
    "blueprint_awaiting_approval": "等待课程蓝图审批",
    "chapter_contracts_incomplete": "章节合约未完成",
    "sample_compliance_pending": "样章合规校验未通过",
    "chapter_compliance_pending": "章节正文合规校验未通过",
    "research_ledger_invalid": "来源台账无效",
    "research_required": "需要补充来源核验",
    "research_impact_decision_pending": "等待研究影响决策",
    "sample_draft_missing": "缺少第一章样章",
    "sample_awaiting_approval": "等待样章审批",
    "chapter_drafts_incomplete": "章节正文未完成",
    "reviews_missing": "缺少审稿准备工件",
    "reviews_pending": "等待审稿",
    "reviews_stale": "审稿报告已过期或未绑定当前版本",
    "reviews_failed": "审稿质量门未通过",
    "review_batch_pending": "等待提交全部章节送审",
    "review_dispatch_unavailable": "历史审稿批次需要迁移",
    "review_dispatching": "正在准备宿主审稿任务",
    "review_tasks_pending": "等待宿主 Skill 审稿",
    "review_tasks_blocked": "审稿任务被阻断",
    "review_batch_superseded": "章节修订后需要重新送审",
    "review_results_failed": "审稿质量门未通过",
    "pdf_environment_unavailable": "PDF 渲染环境不可用",
    "quality_validation_pending": "等待全书质量校验",
    "quality_report_invalid": "全书质量报告无效",
    "quality_report_stale": "全书质量报告已过期",
    "quality_blocked": "全书质量门未通过",
    "assembly_pending": "等待汇编书稿",
    "pdf_render_pending": "等待渲染 PDF",
    "pdf_preview_repair_pending": "等待修复 PDF 页面预览",
    "delivery_validation_pending": "等待交付质量校验",
    "delivery_quality_report_invalid": "交付质量报告无效",
    "delivery_quality_report_stale": "交付质量报告已过期",
    "delivery_quality_blocked": "交付质量门未通过",
    "delivery_manifest_stale": "交付清单已过期",
    "publish_pending": "等待生成交付清单",
    "completed": "已完成交付",
}


class WorkflowCommand(ChineseTyperCommand):
    """Stop auxiliary CLI writes too, before entering a paused book's callback."""

    def invoke(self, ctx):
        workspace = ctx.params.get("workspace")
        readonly = {"identity", "commands", "doctor", "status", "check-chapter", "check-chapters", "reader-resource-preflight", "outline-view", "recovery-view", "operation-status", "wait-operation",
                    "preview-change-impact", "production-plan", "orchestration-migration-preview"}
        allowed = (self.name in readonly or self.name.endswith("-schema")
                   or self.name.startswith(("recover", "production-recover", "cancel", "pause"))
                   or self.name == "start-run-intent"
                   or (self.name.startswith("adopt-") and not ctx.params.get("confirm"))
                   or (self.name == "run-snapshot" and ctx.params.get("verify_current")))
        if workspace is not None and not allowed:
            from knowledge_product_workflow.runtime import require_workflow_mutation_allowed
            try:
                require_workflow_mutation_allowed(Workspace.open(Path(workspace)), self.name)
            except (OSError, ValueError, TypeError) as error:
                typer.echo(str(error), err=True)
                raise typer.Exit(1) from error
        cached = {"status", "recovery-view", "outline-view", "submit-content-plan", "draft", "checkpoint-chapter-content",
                  "submit-chapter", "humanize-check", "developmental-check", "record-developmental-review",
                  "check-chapter", "check-chapters", "reader-resource-preflight", "amend-chapter", "validate", "assemble", "production-plan", "preview-change-impact",
                  "refresh-chapter-proofs", "recheck-chapter", "compile-contracts", "submit-outline",
                  "validate-delivery", "publish", "pdf-observation-template", "record-pdf-visual-observations"}
        if workspace is not None and self.name in cached:
            from knowledge_product_workflow.verification_context import (
                SnapshotChangedError,
                verification_scope,
            )
            try:
                with verification_scope(Workspace.open(Path(workspace))):
                    return super().invoke(ctx)
            except SnapshotChangedError as error:
                typer.echo(str(error), err=True)
                raise typer.Exit(1) from error
        return super().invoke(ctx)


def zh_command(name: str | None = None, *, user_visible: bool = False):
    """Register a Chinese command, hiding control-plane commands by default."""
    return app.command(name, cls=WorkflowCommand, hidden=not user_visible)


@app.callback()
def main() -> None:
    """运行文件驱动的知识产品工作流。"""


@zh_command("identity", user_visible=True)
def identity_command(
    json_output: Annotated[bool, typer.Option("--json", help="输出运行身份JSON")] = False,
    host: Annotated[str | None, typer.Option(help="宿主自报名称，不视为verified")] = None,
    model: Annotated[str | None, typer.Option(help="宿主自报模型，不视为verified")] = None,
) -> None:
    """只读核对实际源码、安装元数据、规范与渲染资源；不自动安装。"""
    from knowledge_product_workflow.identity import runtime_identity
    identity = runtime_identity(host, model)
    if json_output:
        typer.echo(json.dumps(identity, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"实际源码：{identity['source_version']}；安装元数据：{identity['distribution_version']}；一致：{identity['version_consistent']}")
        typer.echo(f"入口：{identity['effective_source']}；宿主/模型无回执时保留unknown。")


@zh_command("commands", user_visible=True)
def commands_index(
    all_commands: Annotated[bool, typer.Option("--all", help="包含受阶段授权约束的隐藏命令")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="输出机器可读 JSON")] = False,
    command_name: Annotated[str | None, typer.Option("--command", help="只查看一个精确命令及其参数，不授予执行权限")] = None,
) -> None:
    """查看命令用途、参数与隐藏状态；查看不会授予执行权限。"""
    command_group = typer.main.get_command(app)
    entries: list[dict[str, object]] = []
    for name, command in sorted(command_group.commands.items()):
        if command_name is not None and name != command_name:
            continue
        if command.hidden and not all_commands:
            continue
        argv = ["kp", name]
        for parameter in command.params:
            if parameter.required:
                flags = list(getattr(parameter, "opts", []))
                if flags and flags[0].startswith("-"):
                    argv.append(flags[0])
                argv.append(f"<{parameter.name}>")
        entries.append({
            "command": name,
            "purpose": (command.help or "").strip(),
            "argv": argv,
            "required_role": _command_role(name),
            "input_templates": _command_input_templates(name),
            "hidden": bool(command.hidden),
            "supports_json_flag": any("--json" in getattr(p, "opts", []) for p in command.params),
            "output_format": "yaml_default_json_optional" if name in {"outline-schema", "controlled-exception-schema"}
                             else "json" if name.endswith("-schema") or name == "pdf-observation-template" else "see_parameters",
            "parameters": [
                {
                    "name": parameter.name,
                    "flags": list(getattr(parameter, "opts", [])),
                    "required": bool(parameter.required),
                    "choices": list(getattr(parameter.type, "choices", [])),
                    "help": getattr(parameter, "help", None),
                    "multiple": bool(getattr(parameter, "multiple", False)),
                    "nargs": parameter.nargs,
                    **_parameter_default_metadata(parameter.default),
                }
                for parameter in command.params
            ],
        })
    if command_name is not None and not entries:
        typer.echo(f"未找到可见命令：{command_name}；受控命令需加 --all 查看。", err=True)
        raise typer.Exit(code=2)
    if json_output:
        typer.echo(json.dumps({"commands": entries}, ensure_ascii=False, indent=2))
        return
    for entry in entries:
        label = " [受控]" if entry["hidden"] else ""
        typer.echo(f"kp {entry['command']}{label} — {entry['purpose']}")


def _parameter_default_metadata(value: object) -> dict[str, object]:
    """Describe defaults without evaluating dynamic values or repr hooks."""
    if callable(value):
        return {"default": None, "default_kind": "dynamic"}

    def encode(item: object) -> object:
        if isinstance(item, Enum):
            return encode(item.value)
        if isinstance(item, Path):
            return str(item)
        if item is None or isinstance(item, (bool, str, int, float)):
            return item
        if isinstance(item, (list, tuple)):
            return [encode(element) for element in item]
        raise TypeError("默认值不可安全编码")

    try:
        default = encode(value)
        json.dumps(default, allow_nan=False)
    except (TypeError, ValueError):
        return {"default": None, "default_kind": "unavailable"}
    return {"default": default, "default_kind": "literal"}


def _command_role(name: str) -> str:
    if name == "delivery-preflight":
        return "kp-quality-editor"
    if name == "production-plan":
        return "kp-curriculum-architect"
    if name == "production-structure-result":
        return "kp-developmental-editor"
    if name == "production-evaluate":
        return "kp-quality-editor"
    if "observation" in name or name in {"audit-pdf-visual", "record-pdf-visual-review"}:
        return "kp-visual-reviewer"
    if "diagram" in name or "illustration" in name:
        return "kp-diagram-designer"
    if "developmental" in name:
        return "kp-developmental-editor"
    if "content-plan" in name or "example" in name or "chapter" in name or name == "draft":
        return "kp-chapter-writer"
    if "outline" in name or name in {"compile-contracts", "contract"}:
        return "kp-curriculum-architect"
    if "brief" in name:
        return "kp-product-strategist"
    return "kp-create"


def _command_input_templates(name: str) -> list[dict[str, str]]:
    templates = {
        "submit-content-plan": ("content-plan-template", "runs/templates/chapter-<章节号>-content-plan.template.yaml"),
        "record-developmental-review": ("developmental-review-template", "stdout JSON；保存到当前工作区并填写 observations"),
        "record-pdf-visual-observations": ("pdf-observation-template", "stdout JSON 的 observations 数组；实际看图后填写"),
        "submit-outline": ("outline", "planning/curriculum-blueprint.template.yaml"),
        "submit-diagram": ("diagram-template", "runs/templates/chapter-<章节号>-<图ID>.diagram.template.json"),
        "submit-chapter": ("chapter-compliance-template", "runs/templates/chapter-<章节号>-compliance.template.json"),
    }
    if name not in templates:
        return []
    command, path = templates[name]
    schemas = {"submit-content-plan": "content-plan-schema", "record-developmental-review": "developmental-review-schema",
               "record-pdf-visual-observations": "pdf-observation-schema", "submit-outline": "outline-schema",
               "submit-diagram": "diagram-schema", "submit-chapter": "chapter-compliance-schema"}
    return [{"command": command, "path": path, "schema_command": schemas[name]}]


@zh_command("run-snapshot")
def run_snapshot_command(workspace: WorkspacePath,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    verify_current: Annotated[bool, typer.Option("--verify-current", help="只读检查已保存快照是否仍当前")] = False,
    host: Annotated[str | None, typer.Option(help="宿主声明，非自动验证")] = None,
    model: Annotated[str | None, typer.Option(help="模型声明，未知可省略")] = None) -> None:
    """从当前控制面生成后台JSON/中文运行报告；过时报告不冒当前。"""
    from knowledge_product_workflow.run_snapshot import current_run_snapshot, write_run_snapshot
    opened = _open_workspace(workspace)
    if verify_current:
        snapshot = current_run_snapshot(opened)
        if snapshot is None:
            typer.echo("运行快照缺失或已过期；读取当前状态后重新生成。", err=True)
            raise typer.Exit(1)
    else:
        path, _ = write_run_snapshot(opened, host=host, model=model)
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    typer.echo(json.dumps(snapshot, ensure_ascii=False, indent=2) if json_output else "已生成/核对当前运行快照：runs/run-snapshot.json 与 run-snapshot.md；最终本人批准单列。")


@zh_command(user_visible=True)
def init(
    book_id: Annotated[str, typer.Argument(help="书稿唯一标识")],
    root: Annotated[Path, typer.Option(help="书稿工作区根目录")] = Path("output"),
) -> None:
    """初始化隔离的书稿工作区。"""
    workspace = Workspace.create(root, book_id, current_policy=True, binding_policy_revision=2)
    checks = _doctor_checks()
    workspace.append_event(
        "environment_diagnosed",
        {
            "quarto_available": bool(checks["quarto"]["available"]),
            "quarto_source": checks["quarto"].get("source"),
            "xelatex_available": bool(checks["xelatex"]["available"]),
        },
    )
    typer.echo(f"已初始化书稿工作区：{workspace.root}")


def _open_workspace(path: Path) -> Workspace:
    return Workspace.open(path)


def _preflight_semantic_input(workspace: Workspace, path: Path, label: str, model=None):
    """Reject local request errors without failure events, routing or diagnostics."""
    from knowledge_product_workflow.verification_context import read_text

    try:
        resolved = _workspace_input_path(workspace, path, label=label)
        text = read_text(resolved, encoding="utf-8")
        if not text.strip():
            raise ValueError(f"{label}不能为空")
        return resolved, model.model_validate_json(text) if model is not None else text
    except (OSError, ValueError) as error:
        typer.echo(f"无法读取{label}：{error}", err=True)
        raise typer.Exit(1) from error


@zh_command("preview-change-impact", user_visible=True)
def preview_change_impact_command(workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--from", help="书稿内拟提交蓝图 YAML")],
    json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """修改蓝图前只读预览逐章失效与可复用范围，不要求重写无关章节。"""
    from knowledge_product_workflow.change_impact import preview_change_impact
    try:
        result = preview_change_impact(_open_workspace(workspace), input_path)
    except (OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(result.model_dump_json(indent=2))


@zh_command("refresh-chapter-proofs", user_visible=True)
def refresh_chapter_proofs_command(chapter_number: int, workspace: WorkspacePath,
    impact_id: Annotated[str, typer.Option("--impact-id", help="受控蓝图修订产生的影响标识")]) -> None:
    """正文未变时建立逐证明派生链；不amend正文，不替代受影响复核。"""
    from knowledge_product_workflow.change_impact import refresh_chapter_proofs
    try:
        output = refresh_chapter_proofs(_open_workspace(workspace), chapter_number, impact_id=impact_id)
    except (OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(str(output))


@zh_command("recovery-view", user_visible=True)
def recovery_view_command(workspace: WorkspacePath,
    json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """暂停、换模型或断流后先只读核对保存稿、原事务及未知任务，未知不重发。"""
    from knowledge_product_workflow.runtime import recovery_view
    typer.echo(json.dumps(recovery_view(_open_workspace(workspace)), ensure_ascii=False, indent=2))


@zh_command('operation-status', user_visible=True)
def operation_status_command(workspace: WorkspacePath,
    operation_id: Annotated[str, typer.Option('--operation-id')],
    json_output: Annotated[bool, typer.Option('--json')] = False) -> None:
    """常量读取原操作回执；等待时优先使用，不深扫全书、不派单。"""
    from knowledge_product_workflow.operation_receipts import operation_status
    typer.echo(json.dumps(operation_status(_open_workspace(workspace), operation_id), ensure_ascii=False))


@zh_command('recover-operation', user_visible=True)
def recover_operation_command(workspace: WorkspacePath,
    operation_id: Annotated[str, typer.Option('--operation-id')],
    release_wait_owner: Annotated[bool, typer.Option('--release-wait-owner', help='仅在原等待进程身份已证失效时释放等待owner')] = False) -> None:
    """只恢复原事务和控制面收尾，不重新提交正文；未知保留、不重发。"""
    from knowledge_product_workflow.operation_receipts import recover_operation
    try:
        if release_wait_owner:
            from knowledge_product_workflow.operation_receipts import recover_wait_owner
            recover_wait_owner(_open_workspace(workspace), operation_id)
            typer.echo('原等待owner已证失效，已释放；原生产任务终态仍须核对')
            return
        receipt = recover_operation(_open_workspace(workspace), operation_id)
    except (OSError, ValueError) as error:
        typer.echo(f'原操作未恢复：{error}', err=True)
        raise typer.Exit(1) from error
    typer.echo(receipt.model_dump_json())


@zh_command('close-failed-operation', user_visible=True)
def close_failed_operation_command(workspace: WorkspacePath,
    operation_id: Annotated[str, typer.Option('--operation-id')],
    reason: Annotated[str, typer.Option('--reason')]) -> None:
    """对已有充分证据的新提交前失败结案，失败不冒充成功。"""
    from knowledge_product_workflow.operation_receipts import close_failed_operation
    try:
        receipt = close_failed_operation(_open_workspace(workspace), operation_id, reason=reason)
    except (OSError, ValueError) as error:
        typer.echo(f'失败操作未结案：{error}',err=True)
        raise typer.Exit(1) from error
    typer.echo(receipt.model_dump_json())


@zh_command('wait-operation', user_visible=True)
def wait_operation_command(workspace: WorkspacePath,
    operation_id: Annotated[str, typer.Option('--operation-id')],
    timeout: Annotated[float, typer.Option('--timeout', min=0.1, max=3600)] = 60) -> None:
    """单一程序owner等待原操作，stdout进展JSONL；真实宿主UI接线pending，不能另开模型协调者。"""
    from knowledge_product_workflow.operation_receipts import wait_operation
    try:
        wait_operation(_open_workspace(workspace), operation_id, timeout=timeout,
            emit=lambda frame: typer.echo(json.dumps(frame, ensure_ascii=False)))
    except (OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


@zh_command("recheck-chapter", user_visible=True)
def recheck_chapter_command(chapter_number: int, workspace: WorkspacePath,
    compliance_path: Annotated[Path, typer.Option("--compliance", help="已完成语义复核后的当前锚点映射")]) -> None:
    """受影响复核通过后仅更新证明；核原提交正文，不改版本，不代替独立审稿。"""
    from knowledge_product_workflow.chapter_compliance import recheck_submitted_chapter
    opened = _open_workspace(workspace)
    try:
        snapshot = capture_orchestration_update(opened)
        output = recheck_submitted_chapter(opened, chapter_number, compliance_path)
        refresh_orchestration_after_controlled_update(opened, "recheck-chapter", snapshot=snapshot, output_paths=[output])
    except (OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(str(output))


@zh_command("recover-workspace-transaction", user_visible=True)
def recover_workspace_transaction_command(operation_id: str, workspace: WorkspacePath) -> None:
    """仅恢复原operation_id拥有的短事务；不会派发新模型任务。"""
    from knowledge_product_workflow.workspace_transactions import recover_workspace_transaction
    try:
        result = recover_workspace_transaction(_open_workspace(workspace), operation_id)
    except (OSError, ValueError, RuntimeError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@zh_command("adopt-binding-policy", user_visible=True)
def adopt_binding_policy_command(workspace: WorkspacePath,
    confirm: Annotated[bool, typer.Option("--confirm", help="暂停旧书后显式确认迁移；默认只读预览")] = False) -> None:
    """显式迁移旧书精确绑定；归档旧依据，不自动恢复被撤销的样章批准。"""
    from knowledge_product_workflow.change_impact import adopt_binding_policy
    try:
        result = adopt_binding_policy(_open_workspace(workspace), confirm=confirm)
    except (OSError, ValueError, RuntimeError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@zh_command("production-host-connect")
def production_host_connect_command(workspace: WorkspacePath,
    binary: Annotated[Path, typer.Option("--binary")],
    expected_sha256: Annotated[str, typer.Option("--expected-sha256")]) -> None:
    """零模型私有stdio连接诊断；不授予生产资格，不读出或复制账户凭据。"""
    from uuid import uuid4

    from knowledge_product_workflow.production_host import (
        NativeAppServerClient,
        NativeProtocolError,
        codex_app_server_command,
    )
    opened=_open_workspace(workspace)
    home=opened.root/"runs/production/native"/("connection-"+uuid4().hex[:12])
    try:
        command=codex_app_server_command(binary.resolve(),home)
        with NativeAppServerClient(command,cwd=opened.root,private_home=home,
                native_binary_sha256=expected_sha256,model_turn_limit=0) as client:
            info=client.request("initialize",{"clientInfo":{"name":"kp-native-connection","version":"1.4.0"},
                "capabilities":{"experimentalApi":True}})
            if Path(info.get("codexHome","")).resolve()!=home.resolve():
                raise NativeProtocolError("私有CODEX_HOME未被宿主确认，停止")
            client.notify_initialized()
            result={"connection":"ready","native_binary_verified":client.native_origin,
                "private_home":str(home),"model_turns_requested":client.model_turns,
                "production_qualification":"not_granted","deployment":False}
    except (NativeProtocolError,OSError) as error:
        typer.echo(str(error),err=True)
        raise typer.Exit(1) from error
    (home/"connection.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    typer.echo(json.dumps(result,ensure_ascii=False,indent=2))


@zh_command("production-host-replay")
def production_host_replay_command(workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--input")],
    parent_id: Annotated[str, typer.Option("--parent")]) -> None:
    """只读回放原生事件归属和占槽；文件／模型文本回放永不授予真实资格。"""
    from knowledge_product_workflow.production_host import NativeTaskLedger
    opened=_open_workspace(workspace)
    path=_workspace_input_path(opened,input_path,label="诊断事件")
    frames=json.loads(path.read_text(encoding="utf-8"))
    ledger=NativeTaskLedger(parent_id,{})
    for frame in frames:
        ledger.consume(frame)
    typer.echo(json.dumps({"diagnostic_only":True,"parent_id":parent_id,
        "active_children":sorted(ledger.active_children),"children":ledger.children,
        "native_qualification":False,"model_turns_requested":0},ensure_ascii=False,indent=2))


@zh_command("production-host-session")
def production_host_session_command(workspace: WorkspacePath,
    binary: Annotated[Path, typer.Option("--binary")],
    expected_sha256: Annotated[str, typer.Option("--expected-sha256")],
    model: Annotated[str, typer.Option("--model")],
    parallelism: Annotated[int, typer.Option("--parallelism", min=1, max=64)] = 4,
    model_turn_limit: Annotated[int, typer.Option("--model-turn-limit", min=0)] = 0,
    qualification_shell: Annotated[Path | None, typer.Option("--qualification-shell")] = None,
    qualification_python: Annotated[Path | None, typer.Option("--qualification-python")] = None) -> None:
    """显式JSONL控制会话；默认零推理，真实资格未通过不接受正式生产。

    stdin每行一个对象，stdout返回ok/result或ok/error/retry=false；全程保持同一进程。
    零模型动作：{"op":"status"}、{"op":"close"}，create-parent仅建立受限元数据会话。
    认证：auth-status、auth-start/mode(browser或device)、auth-poll/timeout、auth-cancel。
    仅本进程内存认证；不读生产auth，不接收API key或外部token。认证页面由账号持有人完成。
    未来获得真实试验授权后才可提高model-turn-limit：qualification/count、collect-qualification/timeout、admit。
    qualification须显式成对指定qualification-shell/pwsh.exe及qualification-python/python.exe，建立私有只读禁网环境。
    受控运行：start/parallelism（diagnostic=true仅私有诊断）、resume/run_id、dispatch、collect/timeout、freeze/task_id。
    接收：commit/task_id/context/compliance；恢复：recover/commit_id。路径必须来自当前运行私有目录。
    模型额度只计turn/start及turn/steer请求，不保证底层HTTP次数、token或硬费用上限；unknown不重发。
    """
    import sys
    from contextlib import ExitStack
    from uuid import uuid4

    from knowledge_product_workflow.production_controller import (
        NativeInlineBridge,
        NativeProductionSession,
        serve_native_session,
    )
    from knowledge_product_workflow.production_host import (
        NativeAppServerClient,
        NativeProtocolError,
        NativeQualificationHost,
        codex_app_server_command,
    )
    opened=_open_workspace(workspace)
    home=opened.root/"runs/production/native"/("session-"+uuid4().hex[:12])
    try:
        if (qualification_shell is None) != (qualification_python is None):
            raise NativeProtocolError("资格执行环境须同时指定--qualification-shell及--qualification-python")
        with ExitStack() as stack:
            if qualification_shell is not None:
                execution = stack.enter_context(NativeQualificationHost(binary, cwd=opened.root,
                    private_home=home, shell_executable=qualification_shell, python_executable=qualification_python,
                    native_binary_sha256=expected_sha256, model_turn_limit=model_turn_limit, max_children=parallelism))
                client = execution.client
            else:
                client = stack.enter_context(NativeAppServerClient(
                    codex_app_server_command(binary.resolve(),home,max_children=parallelism),cwd=opened.root,
                    private_home=home,native_binary_sha256=expected_sha256,model_turn_limit=model_turn_limit))
            session=NativeProductionSession(opened,NativeInlineBridge(client,model=model,max_children=parallelism))
            serve_native_session(session,sys.stdin,sys.stdout)
    except (NativeProtocolError,OSError) as error:
        typer.echo(str(error),err=True)
        raise typer.Exit(1) from error


@zh_command("production-plan")
def production_plan_command(workspace: WorkspacePath,
    json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """只读列出写作前依赖、就绪章节与真实资格；不派发模型。"""
    from knowledge_product_workflow.production_dependencies import build_dependency_plan

    try:
        plan = build_dependency_plan(_open_workspace(workspace))
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    payload = {**plan.model_dump(mode="json"), "native_parallel_enabled": False,
               "qualification": "pending", "fallback": "serial",
               "note": "宿主资格未通过，离线调度不构成真实并行／独立审稿证据"}
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2) if json_output else
               f"就绪章节：{plan.ready_chapters}；原生并行待资格验证，保持串行。")


@zh_command("production-start")
def production_start_command(workspace: WorkspacePath,
    parallelism: Annotated[int, typer.Option("--parallelism", min=1, max=64)] = 4,
    diagnostic: Annotated[bool, typer.Option("--diagnostic", help="仅私有诊断，不能作生产／发布证据")] = False) -> None:
    """建立受控生产队列；宿主未资格通过时禁止原生并行。"""
    from knowledge_product_workflow.production_tasks import (
        ProductionHostCapabilities,
        ProductionPolicy,
        create_production_run,
    )
    from knowledge_product_workflow.runtime import _read_orchestration_state
    try:
        opened = _open_workspace(workspace)
        state = _read_orchestration_state(opened)
        run = create_production_run(opened,
            ProductionPolicy(mode=state.production_mode if state else "serial",
                             requested_parallelism=parallelism), ProductionHostCapabilities(),
            diagnostic_only=diagnostic)
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(run.model_dump_json(indent=2))


@zh_command("production-next")
def production_next_command(workspace: WorkspacePath,
    run_id: Annotated[str, typer.Option("--run")]) -> None:
    """获取就绪任务，全部角色共用总池；未知任务不重复派发。"""
    from knowledge_product_workflow.production_tasks import (
        next_dispatches,
        production_lock,
        refresh_production_dependencies,
        resume_production_run,
        save_run,
    )
    opened = _open_workspace(workspace)
    with production_lock(opened):
        run = resume_production_run(opened, run_id)
        if not run.diagnostic_only and not run.capabilities.admits_live_parallelism():
            raise typer.BadParameter("原生宿主资格未通过")
        refresh_production_dependencies(opened, run)
        tasks = next_dispatches(run)
        save_run(opened, run)
    typer.echo(json.dumps({"diagnostic_only": run.diagnostic_only,
        "active": run.active_count, "effective_parallelism": run.effective_parallelism,
        "tasks": [task.model_dump(mode="json") for task in tasks]}, ensure_ascii=False, indent=2))


@zh_command("production-result")
def production_result_command(workspace: WorkspacePath,
    run_id: Annotated[str, typer.Option("--run")],
    task_id: Annotated[str, typer.Option("--task")],
    input_path: Annotated[Path, typer.Option("--input")]) -> None:
    """接收本任务版本绑定结果，不允许模型填写宿主资格。"""
    from knowledge_product_workflow.production_tasks import record_task_result

    opened = _open_workspace(workspace)
    path = _workspace_input_path(opened, input_path, label="任务结果")
    try:
        run = record_task_result(opened, task_id, json.loads(path.read_text(encoding="utf-8")),
                                 run_id=run_id)
    except (ValueError, KeyError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(run.model_dump_json(indent=2))


@zh_command("production-evaluate")
def production_evaluate_command(workspace: WorkspacePath,
    context_path: Annotated[Path, typer.Option("--context")]) -> None:
    """在同一候选私有空间执行既有校验，不回落读取实时正式报告。"""
    from knowledge_product_workflow.production_context import (
        CandidateEvaluationContext,
        evaluate_candidate,
    )

    opened = _open_workspace(workspace)
    path = _workspace_input_path(opened, context_path, label="候选上下文")
    context = CandidateEvaluationContext.model_validate_json(path.read_text(encoding="utf-8"))
    if Path(context.canonical_root).resolve() != opened.root.resolve():
        raise typer.BadParameter("候选上下文不属于当前书稿")
    try:
        proof = evaluate_candidate(context)
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    from knowledge_product_workflow.production_tasks import (
        production_lock,
        resume_production_run,
        save_run,
    )
    run_id = Path(context.snapshot_root).parents[3].name
    with production_lock(opened):
        run_path = opened.root / "runs/production" / run_id / "run.json"
        if _v_is_file(run_path):
            run = resume_production_run(opened, run_id)
            writer = run.task(context.task_id)
            for task in run.tasks:
                if task.role == "semantic" and task.chapter_number == writer.chapter_number and task.candidate_version == writer.candidate_version and task.state == "waiting_snapshot" and "structure_template" in proof.reports:
                    task.state = "review_ready"
                    task.candidate_context_path = str(Path(context.snapshot_root).parent / "context.json")
                    task.candidate_source_sha256 = proof.source_sha256
            if writer.state == "local_check" and not proof.required_missing:
                run.local_check_complete(writer.task_id, passed=True)
                for task in run.tasks:
                    if task.role == "reviewer" and task.chapter_number == writer.chapter_number and task.candidate_version == writer.candidate_version:
                        task.candidate_context_path = str(Path(context.snapshot_root).parent / "context.json")
                        task.candidate_source_sha256 = proof.source_sha256
            save_run(opened, run)
    typer.echo(proof.model_dump_json(indent=2))


@zh_command("production-freeze")
def production_freeze_command(workspace: WorkspacePath,
    run_id: Annotated[str, typer.Option("--run")],
    task_id: Annotated[str, typer.Option("--task")]) -> None:
    """父控制面冻结指定候选与实际输入；不执行模型或正式接收。"""
    from knowledge_product_workflow.production_context import prepare_candidate_context
    from knowledge_product_workflow.production_tasks import resume_production_run
    opened = _open_workspace(workspace)
    try:
        run = resume_production_run(opened, run_id)
        context = prepare_candidate_context(opened, run.task(task_id))
    except (ValueError, FileNotFoundError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(context.model_dump_json(indent=2))


@zh_command("production-structure-result")
def production_structure_result_command(workspace: WorkspacePath,
    context_path: Annotated[Path, typer.Option("--context")],
    input_path: Annotated[Path, typer.Option("--input")]) -> None:
    """仅提交同一候选的结构观察，保留原Rubric；不签真实独立审稿。"""
    from knowledge_product_workflow.production_context import (
        CandidateEvaluationContext,
        record_candidate_structure_review,
    )
    opened = _open_workspace(workspace)
    try:
        context = CandidateEvaluationContext.model_validate_json(
            _workspace_input_path(opened, context_path, label="候选上下文").read_text(encoding="utf-8"))
        if Path(context.canonical_root).resolve() != opened.root.resolve():
            raise ValueError("候选上下文不属于当前书稿")
        result = record_candidate_structure_review(context, json.loads(
            _workspace_input_path(opened, input_path, label="结构观察").read_text(encoding="utf-8")))
    except (ValueError, FileNotFoundError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    from knowledge_product_workflow.production_tasks import (
        production_lock,
        resume_production_run,
        save_run,
    )
    run_id=Path(context.snapshot_root).parents[3].name
    with production_lock(opened):
        run_path=opened.root/"runs/production"/run_id/"run.json"
        if _v_is_file(run_path):
            run=resume_production_run(opened,run_id)
            writer=run.task(context.task_id)
            for task in run.tasks:
                if task.role=="semantic" and task.chapter_number==writer.chapter_number and task.candidate_version==writer.candidate_version and task.state=="reviewing":
                    run.complete_review(task.task_id,passed=not result.required_missing,
                        issues=result.required_missing,input_manifest_sha256=task.input_manifest_sha256)
            if writer.state=="local_check" and not result.required_missing:
                run.local_check_complete(writer.task_id,passed=True)
                for task in run.tasks:
                    if task.role=="reviewer" and task.chapter_number==writer.chapter_number and task.candidate_version==writer.candidate_version:
                        task.candidate_context_path=str(Path(context.snapshot_root).parent/"context.json")
                        task.candidate_source_sha256=result.source_sha256
            save_run(opened,run)
    typer.echo(result.model_dump_json(indent=2))


@zh_command("production-review-result")
def production_review_result_command(workspace: WorkspacePath,
    run_id: Annotated[str, typer.Option("--run")],
    task_id: Annotated[str, typer.Option("--task")],
    context_path: Annotated[Path, typer.Option("--context")],
    input_path: Annotated[Path, typer.Option("--input")]) -> None:
    """整章候选的独立审稿内容接收；诊断仍不能满足生产／发布门。"""
    from knowledge_product_workflow.production_context import CandidateEvaluationContext
    from knowledge_product_workflow.production_tasks import (
        production_lock,
        resume_production_run,
        save_run,
    )
    from knowledge_product_workflow.review_batches import ReviewResultInput, record_candidate_review
    opened = _open_workspace(workspace)
    try:
        context = CandidateEvaluationContext.model_validate_json(
            _workspace_input_path(opened, context_path, label="候选上下文").read_text(encoding="utf-8"))
        if Path(context.canonical_root).resolve() != opened.root.resolve():
            raise ValueError("候选上下文不属于当前书稿")
        result = ReviewResultInput.model_validate_json(
            _workspace_input_path(opened, input_path, label="独立审稿内容").read_text(encoding="utf-8"))
        with production_lock(opened):
            run = resume_production_run(opened, run_id)
            writer = run.task(context.task_id)
            task = run.task(task_id)
            record = record_candidate_review(context, author_identity=writer.task_id,
                author_session_id=writer.host_task_id or writer.task_id, reviewer_task=task,
                result=result, capabilities=run.capabilities, diagnostic_only=run.diagnostic_only)
            run.complete_review(task_id, passed=record["acceptance_ready"], issues=record["issues"],
                                input_manifest_sha256=task.input_manifest_sha256)
            save_run(opened, run)
    except (ValueError, FileNotFoundError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(record, ensure_ascii=False, indent=2))


@zh_command("production-commit")
def production_commit_command(workspace: WorkspacePath,
    run_id: Annotated[str, typer.Option("--run")], task_id: Annotated[str, typer.Option("--task")],
    context_path: Annotated[Path, typer.Option("--context")],
    compliance_path: Annotated[Path, typer.Option("--compliance")]) -> None:
    """父控制面正式接收；真实资格与整章证明缺一不可，诊断拒绝。"""
    from knowledge_product_workflow.production_context import CandidateEvaluationContext
    from knowledge_product_workflow.production_intake import commit_production_candidate
    opened = _open_workspace(workspace)
    try:
        context = CandidateEvaluationContext.model_validate_json(
            _workspace_input_path(opened, context_path, label="候选上下文").read_text(encoding="utf-8"))
        if Path(context.canonical_root).resolve() != opened.root.resolve():
            raise ValueError("候选上下文不属于当前书稿")
        result = commit_production_candidate(opened, task_id, run_id=run_id, context=context,
            compliance_input=_workspace_input_path(opened, compliance_path, label="候选合规映射"))
    except (ValueError, FileNotFoundError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@zh_command("production-recover")
def production_recover_command(workspace: WorkspacePath,
    commit_id: Annotated[str, typer.Option("--commit")]) -> None:
    """仅恢复控制面正式提交日志；诊断／手填日志不能获正式资格。"""
    from knowledge_product_workflow.production_intake import recover_production_commit
    from knowledge_product_workflow.production_tasks import resume_production_run
    opened = _open_workspace(workspace)
    try:
        if not commit_id.replace("-", "").isalnum():
            raise ValueError("提交ID越界")
        record = json.loads((opened.root / "runs/production/commits" / f"{commit_id}.json").read_text(encoding="utf-8"))
        if record.get("diagnostic_only") or not record.get("run_id"):
            raise ValueError("诊断／非受控提交日志不允许正式恢复")
        run = resume_production_run(opened, record["run_id"])
        if not run.capabilities.admits_live_parallelism():
            raise ValueError("真实宿主恢复资格未通过；保留待恢复日志，不重发任务")
        result = recover_production_commit(opened, commit_id)
    except (ValueError, FileNotFoundError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@zh_command("production-shared-request")
def production_shared_request_command(workspace: WorkspacePath,
    run_id: Annotated[str, typer.Option("--run")], task_id: Annotated[str, typer.Option("--task")],
    input_path: Annotated[Path, typer.Option("--input")]) -> None:
    """子任务只返回结构化共享请求，父控制面排队；不直接追加台账。"""
    from knowledge_product_workflow.production_tasks import (
        production_lock,
        resume_production_run,
        save_run,
    )
    opened = _open_workspace(workspace)
    try:
        payload=json.loads(_workspace_input_path(opened,input_path,label="共享请求").read_text(encoding="utf-8"))
        with production_lock(opened):
            run=resume_production_run(opened,run_id)
            request=run.queue_shared_request(task_id,payload["kind"],payload["payload"])
            save_run(opened,run)
    except (ValueError,FileNotFoundError,KeyError) as error:
        typer.echo(str(error),err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(request,ensure_ascii=False,indent=2))


@zh_command("production-shared-next")
def production_shared_next_command(workspace: WorkspacePath,
    run_id: Annotated[str, typer.Option("--run")]) -> None:
    """只读返回待执行原领域命令与请求；不授予来源／假设／图／审批权限。"""
    from knowledge_product_workflow.production_intake import shared_request_handoffs
    from knowledge_product_workflow.production_tasks import resume_production_run
    opened=_open_workspace(workspace)
    typer.echo(json.dumps(shared_request_handoffs(resume_production_run(opened,run_id)),ensure_ascii=False,indent=2))


@zh_command("production-shared-complete")
def production_shared_complete_command(workspace: WorkspacePath,
    run_id: Annotated[str, typer.Option("--run")], request_id: Annotated[str, typer.Option("--request")]) -> None:
    """核实际领域工件后消费共享请求；调用者的passed字段不作为回执。"""
    from knowledge_product_workflow.production_intake import complete_shared_request
    opened=_open_workspace(workspace)
    try:
        result=complete_shared_request(opened,run_id,request_id)
    except (ValueError,FileNotFoundError) as error:
        typer.echo(str(error),err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(result,ensure_ascii=False,indent=2))


@zh_command("production-recover-lock")
def production_recover_lock_command(workspace: WorkspacePath) -> None:
    """核实际PID及进程创建身份，只释放确认死亡的控制锁；活动／未知不释放。"""
    from knowledge_product_workflow.production_tasks import recover_production_lock
    try:
        result=recover_production_lock(_open_workspace(workspace))
    except ValueError as error:
        typer.echo(str(error),err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(result,ensure_ascii=False,indent=2))


@zh_command("production-revise")
def production_revise_command(workspace: WorkspacePath,
    run_id: Annotated[str, typer.Option("--run")], chapter_number: Annotated[int, typer.Option("--chapter", min=1)],
    reason: Annotated[str, typer.Option("--reason")]) -> None:
    """为受影响章创建新候选版本；保留旧稿／证明，未知宿主任务不猜测释放。"""
    from knowledge_product_workflow.production_dependencies import build_task_context
    from knowledge_product_workflow.production_tasks import (
        production_lock,
        resume_production_run,
        save_run,
    )
    opened=_open_workspace(workspace)
    try:
        if not reason.strip():
            raise ValueError("修订须明确原因")
        with production_lock(opened):
            run=resume_production_run(opened,run_id)
            if not run.diagnostic_only and not run.capabilities.admits_live_parallelism():
                raise ValueError("真实宿主资格未通过，禁止派新原生候选")
            context=build_task_context(opened,chapter_number,"writer")
            if context["blocked_by"]:
                raise ValueError("先解决写作前依赖："+", ".join(context["blocked_by"]))
            version=run.supersede_candidate(chapter_number)
            task=run.add_writer(chapter_number,context["live_bindings"],version=version)
            task.revision_reason=reason
            save_run(opened,run)
    except (ValueError,FileNotFoundError) as error:
        typer.echo(str(error),err=True)
        raise typer.Exit(1) from error
    typer.echo(task.model_dump_json(indent=2))


def _workspace_input_path(workspace: Workspace, path: Path, *, label: str) -> Path:
    """Accept a structured mutation input only from the current book workspace."""
    resolved = path.expanduser().resolve()
    if not _v_is_file(resolved):
        raise FileNotFoundError(f"找不到{label}：{path}")
    if not resolved.is_relative_to(workspace.root.resolve()):
        raise ValueError(f"{label}必须位于当前书稿工作区内")
    return resolved


def _interactive_stdin_isatty() -> bool:
    """Small seam for terminal-only Brief tests; production delegates to stdin."""
    return sys.stdin.isatty()


def _render_workflow_status(status) -> str:
    """Render a user-safe status summary; ``--json`` retains control-plane detail."""
    macro_labels = {
        "definition": "产品定位",
        "architecture": "课程设计",
        "production": "内容生产",
        "assurance": "质量检查",
        "delivery": "文档交付",
    }
    lines = [
        f"当前进度：{macro_labels.get(status.macro_phase.value, '处理中')}",
        f"状态：{status.user_summary}",
    ]
    if status.stage == "brief_missing":
        # This is a user-comprehensible absence, not a control-plane stage identifier.
        lines.append("当前阶段：缺少产品 Brief")
    if status.required_human_decision:
        lines.append(f"需要你确认：{status.required_human_decision}")
    if status.blocked_by and not status.required_human_decision:
        lines.append("当前步骤需要处理后才能继续；工作流会在本会话中给出下一步。")
    return "\n".join(lines)


def _doctor_checks() -> dict[str, dict[str, bool | str | None | list[str]]]:
    """Inspect local prerequisites without performing downloads or network requests."""
    yahei_candidates = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyh.ttf"),
    )
    yahei_path = next((path for path in yahei_candidates if _v_is_file(path)), None)
    quarto = resolve_quarto()
    d2 = resolve_d2()
    diagram_browser = resolve_diagram_browser()
    checks = {
        "kp": {"available": shutil.which("kp") is not None, "path": shutil.which("kp")},
        "quarto": {
            "available": quarto.available,
            "path": str(quarto.path) if quarto.path else None,
            "source": quarto.source,
            "version": quarto.version,
            "failed_candidates": list(quarto.failed_candidates),
        },
        "xelatex": {
            "available": shutil.which("xelatex") is not None,
            "path": shutil.which("xelatex"),
        },
        "microsoft_yahei": {
            "available": yahei_path is not None,
            "path": str(yahei_path) if yahei_path else None,
        },
        "d2": {
            "available": d2.available,
            "path": str(d2.path) if d2.path else None,
            "source": d2.source,
            "version": d2.version,
            "failed_candidates": list(d2.failed_candidates),
        },
        "diagram_browser": {
            "available": diagram_browser.available,
            "path": str(diagram_browser.path) if diagram_browser.path else None,
            "source": diagram_browser.source,
            "version": diagram_browser.version,
            "failed_candidates": list(diagram_browser.failed_candidates),
        },
        "pdftoppm": {
            "available": resolve_pdftoppm() is not None,
            "path": str(resolve_pdftoppm()) if resolve_pdftoppm() else None,
        },
    }
    from knowledge_product_workflow.pdf_fonts import probe_preferred_fonts
    checks.update({key: {**value, "path": None} for key, value in probe_preferred_fonts(Path.cwd()).items()})
    return checks


def _build_constraints(
    *,
    target_pages: int | str | None,
    target_words: int | str | None,
    language: str = "中文",
    layout_profile: str = "A4 标准版式",
    audience_level: str = "进阶",
    delivery_format: str = "Markdown",
    review_policy: str = "deterministic_only",
) -> DetailConstraints:
    """Normalize CLI length inputs into the persisted Brief constraint contract."""
    page_budget = parse_page_budget(target_pages) if target_pages is not None else None
    word_budget = parse_word_budget(target_words) if target_words is not None else None
    return DetailConstraints(
        page_constraint=page_budget,
        target_words=word_budget.target if word_budget else None,
        word_min=word_budget.minimum if word_budget else None,
        word_target=word_budget.target if word_budget else None,
        word_max=word_budget.maximum if word_budget else None,
        language=language,
        layout_profile=layout_profile,
        audience_level=audience_level,
        delivery_format=delivery_format,
        review_policy=review_policy,
    )


def _build_presentation_overrides(
    *,
    subtitle: str | None,
    edition: str | None,
    publication_date: str | date | None,
    publisher_label: str | None,
    copyright_line: str | None,
    brand_color_intent: str | None,
) -> PresentationOverrides | None:
    """Normalize optional public publication values without making them Brief requirements."""
    _brand_palette(brand_color_intent or "橙色基调")
    overrides = PresentationOverrides(
        subtitle=subtitle,
        edition=edition,
        publication_date=publication_date,
        publisher_label=publisher_label,
        copyright_line=copyright_line,
        brand_color_intent=brand_color_intent or "橙色基调",
    )
    return overrides if overrides.has_values() else None


class StructuredBriefInput(BriefSemanticFields):
    """Strict non-interactive contract; no positional stdin protocol is accepted."""

    model_config = ConfigDict(extra="forbid")

    topic: str | None = None
    target_audience: str
    audience_level: str
    target_pages: int | str | None = None
    target_words: int | str | None = None
    language: str = "中文"
    layout_profile: str = "A4 标准版式"
    delivery_format: str = "Markdown"
    review_policy: str = "deterministic_only"
    subtitle: str | None = None
    edition: str | None = None
    publication_date: date | None = None
    publisher_label: str | None = None
    copyright_line: str | None = None
    brand_color_intent: str | None = None
    requirement_sources: dict[str, str] = Field(default_factory=dict)
    presentation: PresentationOverrides | None = None

    @field_validator("target_audience")
    @classmethod
    def require_target_audience(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("目标受众不能为空")
        return cleaned


class BriefAmendmentInput(BriefSemanticFields):
    """A partial, explicit Brief revision request supplied by the host."""

    model_config = ConfigDict(extra="forbid")

    topic: str | None = None
    target_audience: str | None = None
    target_pages: int | str | None = None
    target_words: int | str | None = None
    language: str | None = None
    layout_profile: str | None = None
    audience_level: str | None = None
    delivery_format: str | None = None
    review_policy: str | None = None
    subtitle: str | None = None
    edition: str | None = None
    publication_date: date | None = None
    publisher_label: str | None = None
    copyright_line: str | None = None
    brand_color_intent: str | None = None
    change_reason: str | None = None

    @model_validator(mode="after")
    def require_one_change(self) -> "BriefAmendmentInput":
        if not (self.model_fields_set - {"change_reason"}):
            raise ValueError("Brief 修订至少需要提供一个可修改字段")
        return self


class ControlledExceptionSubmissionInput(BaseModel):
    """One semantic-only controlled-exception submission file."""

    model_config = ConfigDict(extra="forbid")

    exceptions: list[ControlledExceptionRequest] = Field(min_length=1)


_BRIEF_FIELDS = (
    "target_audience",
    "audience_level",
)
_BRIEF_LABELS = {
    "target_audience": "目标受众（必填）",
    "audience_level": "受众级别（入门/进阶/专家）",
}
_BRIEF_DEFAULTS: dict[str, str] = {}


def _brief_session_path(workspace: Workspace) -> Path:
    return workspace.root / "runs" / "brief-session.json"


def _load_brief_session(workspace: Workspace, topic: str) -> dict[str, object]:
    path = _brief_session_path(workspace)
    if not _v_is_file(path):
        return {"topic": topic, "current_field": _BRIEF_FIELDS[0], "values": {}}
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Brief 会话状态格式无效，请删除当前工作区的 runs/brief-session.json 后重试。") from error
    if not isinstance(session, dict) or session.get("topic") != topic:
        raise ValueError("已有未完成的 Brief 会话属于其他主题，请先完成或取消该会话。")
    if not isinstance(session.get("values"), dict):
        raise TypeError("Brief 会话状态不完整，无法安全恢复。")
    if session.get("current_field") not in _BRIEF_FIELDS:
        # Delivery variables from legacy sessions are optional under the minimal-input contract.
        return {"topic": topic, "current_field": _BRIEF_FIELDS[0], "values": {}}
    return session


def _save_brief_session(workspace: Workspace, session: dict[str, object]) -> None:
    _brief_session_path(workspace).write_text(
        json.dumps(session, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )


def _cancel_brief_session(workspace: Workspace, reason: str) -> None:
    path = _brief_session_path(workspace)
    if _v_exists(path):
        path.unlink()
    workspace.append_event("product_brief_cancelled", {"reason": reason})


def _render_constraints_summary(target_audience: str, constraints: DetailConstraints) -> str:
    language = "中文" if constraints.language == "zh-CN" else "英文"
    level = {"beginner": "入门", "intermediate": "进阶", "expert": "专家"}[constraints.audience_level.value]
    delivery = {"markdown": "Markdown", "pdf": "PDF", "both": "Markdown + PDF"}[constraints.delivery_format.value]
    word_budget = constraints.word_budget
    word_value = "未设置（不设字数硬门）" if word_budget is None else f"{word_budget.minimum}-{word_budget.maximum} 字"
    page_budget = constraints.page_budget
    if page_budget is None:
        page_value = "未设置（由课程设计建议篇幅）"
    elif page_budget.mode.value == "unbounded":
        page_value = "页数无上限（由课程设计建议篇幅）"
    elif page_budget.mode.value == "minimum":
        page_value = f"至少 {page_budget.minimum} 页"
    elif page_budget.mode.value == "exact":
        page_value = f"{page_budget.target} 页"
    elif page_budget.mode.value == "preferred":
        page_value = f"首选 {page_budget.preferred_minimum}-{page_budget.preferred_maximum} 页"
        if page_budget.maximum is not None:
            page_value += f"；硬上限 {page_budget.maximum} 页"
    else:
        page_value = f"{page_budget.minimum}-{page_budget.maximum} 页"
    if page_budget is not None and page_budget.scope.value == "body_only":
        page_value = "正文 " + page_value
    review = (
        "仅确定性质量门（不要求独立审稿）"
        if constraints.review_policy.value == "deterministic_only"
        else "必须有真实宿主子任务回执的独立审稿"
    )
    return "\n".join((
        "Brief 预览：",
        "用户确认的产品定位：",
        f"- 目标受众：{target_audience}",
        f"- 受众级别：{level}",
        "模型建议或用户覆盖的交付参数：",
        f"- 目标页数：{page_value}",
        f"- 目标字数：{word_value}",
        f"- 书稿语言：{language}",
        "- 版式：A4 标准版式",
        f"- 交付格式：{delivery}",
        f"- 审稿策略：{review}",
    ))


def _interactive_brief_input(workspace: Workspace, topic: str) -> tuple[str, DetailConstraints]:
    """Collect only the user-owned positioning inputs in a resumable terminal session."""
    session = _load_brief_session(workspace, topic)
    values = dict(session["values"])
    index = _BRIEF_FIELDS.index(str(session["current_field"]))
    while index < len(_BRIEF_FIELDS):
        field = _BRIEF_FIELDS[index]
        default_value = values.get(field, _BRIEF_DEFAULTS.get(field, ""))
        default = "" if default_value is None else str(default_value)
        answer = typer.prompt(_BRIEF_LABELS[field], default=default, show_default=bool(default)).strip()
        if answer == "取消":
            _cancel_brief_session(workspace, "user_cancelled")
            raise typer.Abort()
        if answer == "返回":
            if index:
                index -= 1
            session["current_field"] = _BRIEF_FIELDS[index]
            _save_brief_session(workspace, session)
            continue
        if field == "target_audience":
            value = answer
            if not value:
                typer.echo("目标受众不能为空；请说明读者是谁、已有基础或使用场景。")
                continue
        else:
            value = normalize_localized_constraints({field: answer}).get(field)
            allowed = {"beginner", "intermediate", "expert"}
            if value not in allowed:
                typer.echo(f"{_BRIEF_LABELS[field]} 的值无效；请只回答当前问题、返回或取消。")
                continue
        values[field] = value
        session["values"] = values
        session["current_field"] = _BRIEF_FIELDS[min(index + 1, len(_BRIEF_FIELDS) - 1)]
        _save_brief_session(workspace, session)
        index += 1
    try:
        target_audience = str(values["target_audience"])
        constraints = _build_constraints(
            target_pages=None,
            target_words=None,
            audience_level=str(values["audience_level"]),
        )
    except (ValidationError, ValueError) as error:
        raise ValueError(f"Brief 约束无效：{error}") from error
    typer.echo(_render_constraints_summary(target_audience, constraints))
    confirmation = typer.prompt("确认创建产品 Brief？[是/否]", default="否").strip().lower()
    if confirmation not in {"是", "确认", "yes", "y"}:
        _cancel_brief_session(workspace, "confirmation_rejected")
        raise typer.Abort()
    return target_audience, constraints


def _load_structured_brief(path: Path) -> StructuredBriefInput:
    try:
        source = path.read_text(encoding="utf-8")
        raw = json.loads(source) if path.suffix.lower() == ".json" else yaml.safe_load(source)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise ValueError(f"无法读取 Brief 输入文件：{error}") from error
    if not isinstance(raw, dict):
        raise TypeError("Brief 输入文件必须是 JSON 或 YAML 对象。")
    try:
        return StructuredBriefInput.model_validate(raw)
    except ValidationError as error:
        raise ValueError(f"Brief 输入文件字段无效：{error}") from error


def _load_brief_amendment(path: Path) -> BriefAmendmentInput:
    """Load one partial Brief revision without accepting unknown control fields."""
    try:
        source = path.read_text(encoding="utf-8")
        raw = json.loads(source) if path.suffix.lower() == ".json" else yaml.safe_load(source)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise ValueError(f"无法读取 Brief 修订输入文件：{error}") from error
    if not isinstance(raw, dict):
        raise TypeError("Brief 修订输入文件必须是 JSON 或 YAML 对象。")
    try:
        return BriefAmendmentInput.model_validate(raw)
    except ValidationError as error:
        raise ValueError(f"Brief 修订输入字段无效：{error}") from error


def _load_publication_update(path: Path) -> PublicationUpdateInput:
    try:
        source = path.read_text(encoding="utf-8")
        raw = json.loads(source) if path.suffix.lower() == ".json" else yaml.safe_load(source)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise ValueError(f"无法读取出版信息更新文件：{error}") from error
    if not isinstance(raw, dict):
        raise TypeError("出版信息更新文件必须是 JSON 或 YAML 对象。")
    try:
        return PublicationUpdateInput.model_validate(raw)
    except ValidationError as error:
        raise ValueError(f"出版信息更新字段无效：{error}") from error




def _load_controlled_exception_submission(path: Path) -> ControlledExceptionSubmissionInput:
    try:
        source = path.read_text(encoding="utf-8")
        raw = json.loads(source) if path.suffix.lower() == ".json" else yaml.safe_load(source)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise ValueError(f"无法读取受控例外输入文件：{error}") from error
    if not isinstance(raw, dict):
        raise TypeError("受控例外输入文件必须是 JSON 或 YAML 对象。")
    try:
        return ControlledExceptionSubmissionInput.model_validate(raw)
    except ValidationError as error:
        raise ValueError(f"受控例外输入字段无效：{error}") from error


def _publication_context(workspace: Workspace) -> tuple[str, PresentationOverrides | None]:
    """Read the language and legacy seed values from the current Brief only."""
    brief_path = workspace.root / "planning" / "product-brief.md"
    if not _v_is_file(brief_path):
        raise ValueError("缺少产品 Brief，不能设置出版信息。")
    _, body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    constraints = parse_detail_constraints(body)
    if constraints is None:
        raise ValueError("当前 Brief 缺少交付约束，不能安全设置出版信息。")
    return constraints.language, parse_presentation_overrides(body)


def _budget_argument(target: int | None, minimum: int | None, maximum: int | None) -> int | str | None:
    """Reconstruct a CLI-compatible range from a persisted normalized budget."""
    if target is None:
        return None
    if minimum == target == maximum:
        return target
    if minimum is None or maximum is None:
        raise ValueError("已批准 Brief 的预算字段不完整")
    return f"{minimum}-{maximum}"


def _page_constraint_argument(constraints: DetailConstraints) -> int | str | None:
    """Round-trip every supported page mode through the public Brief input."""
    constraint = constraints.page_constraint
    if constraint is None:
        return None
    prefix = "正文 " if constraint.scope.value == "body_only" else ""
    if constraint.mode.value == "unbounded":
        return prefix + "页数无上限"
    if constraint.mode.value == "minimum":
        return prefix + f"至少{constraint.minimum}页"
    if constraint.mode.value == "exact":
        return prefix + str(constraint.target)
    if constraint.mode.value == "preferred":
        value = prefix + f"目标{constraint.preferred_minimum}-{constraint.preferred_maximum}页"
        if constraint.maximum is not None:
            value += f"，最多{constraint.maximum}页"
        return value
    return prefix + f"{constraint.minimum}-{constraint.maximum}"


def _build_brief_amendment_candidate(workspace: Workspace, amendment: BriefAmendmentInput) -> BriefRevisionCandidate:
    """Merge a partial change into the current approved Brief without writing it."""
    brief_path = workspace.root / "planning" / "product-brief.md"
    if not _v_is_file(brief_path):
        raise ValueError("缺少当前产品 Brief，不能创建修订")
    metadata, body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
    if metadata.status is not ArtifactStatus.APPROVED:
        raise ValueError("当前产品 Brief 尚未批准，不能创建修订")
    constraints = parse_detail_constraints(body)
    if constraints is None:
        raise ValueError("旧版 Brief 缺少交付约束，不能安全修订；请先迁移该工作区")
    fields = amendment.model_fields_set
    current_pages = _page_constraint_argument(constraints)
    current_words = _budget_argument(constraints.target_words, constraints.word_min, constraints.word_max)
    revised_constraints = _build_constraints(
        target_pages=amendment.target_pages if "target_pages" in fields else current_pages,
        target_words=amendment.target_words if "target_words" in fields else current_words,
        language=amendment.language if "language" in fields else constraints.language,
        layout_profile=amendment.layout_profile if "layout_profile" in fields else constraints.layout_profile,
        audience_level=amendment.audience_level if "audience_level" in fields else constraints.audience_level.value,
        delivery_format=amendment.delivery_format if "delivery_format" in fields else constraints.delivery_format.value,
        review_policy=amendment.review_policy if "review_policy" in fields else constraints.review_policy.value,
    )
    current_presentation = parse_presentation_overrides(body)
    presentation_values = {
        "subtitle": None,
        "edition": None,
        "publication_date": None,
        "publisher_label": None,
        "copyright_line": None,
        "brand_color_intent": None,
    }
    if current_presentation is not None:
        presentation_values.update(current_presentation.model_dump(mode="python"))
    for field in ("subtitle", "edition", "publication_date", "publisher_label", "copyright_line", "brand_color_intent"):
        if field in fields:
            presentation_values[field] = getattr(amendment, field)
    presentation = PresentationOverrides.model_validate(presentation_values)
    topic = amendment.topic.strip() if amendment.topic is not None else brief_topic(body)
    target_audience = (
        amendment.target_audience.strip()
        if amendment.target_audience is not None
        else brief_target_audience(body)
    )
    if not topic or not target_audience:
        raise ValueError("Brief 修订后的主题和目标受众不能为空")
    current_fingerprints = brief_contract_fingerprints(body)
    semantic_values = parse_brief_semantics(body).model_dump()
    for field in BriefSemanticFields.model_fields:
        if field in fields:
            semantic_values[field] = getattr(amendment, field)
    revised_body = render_product_brief_body(
        topic, target_audience, revised_constraints, presentation, BriefSemanticFields(**semantic_values)
    )
    revised_fingerprints = brief_contract_fingerprints(revised_body)
    changed_fields = sorted(field for field in fields if field != "change_reason")
    revision_kind = (
        "content"
        if current_fingerprints["content_contract_sha256"]
        != revised_fingerprints["content_contract_sha256"]
        else "delivery"
    )
    return BriefRevisionCandidate(
        prior_brief_sha256=sha256(brief_path.read_bytes()).hexdigest(),
        prior_version=metadata.version,
        topic=topic,
        target_audience=target_audience,
        detail_constraints=revised_constraints,
        presentation=presentation,
        revision_kind=revision_kind,
        changed_fields=changed_fields,
        change_reason=amendment.change_reason,
        **semantic_values,
    )


def _write_brief_preview(
    workspace: Workspace,
    topic: str,
    target_audience: str,
    constraints: DetailConstraints,
    presentation: PresentationOverrides | None,
    requirement_sources: dict[str, str],
    semantics: BriefSemanticFields | None = None,
) -> Path:
    require_original_reader_request(workspace, semantics)
    update_snapshot = capture_orchestration_update(workspace)
    output = workspace.root / "runs" / "brief-preview.json"
    design_preview = create_publication_design_preview(workspace, presentation.brand_color_intent or "橙色基调" if presentation else "橙色基调")
    output.write_text(
        json.dumps(
            {
                "topic": topic,
                "semantics": semantics.model_dump() if semantics else None,
                "target_audience": target_audience,
                "detail_constraints": constraints.model_dump(mode="json"),
                "presentation": presentation.model_dump(mode="json", exclude_none=True)
                if presentation
                else None,
                "brand_palette": _brand_palette(presentation.brand_color_intent or "橙色基调").model_dump()
                if presentation else _brand_palette("橙色基调").model_dump(),
                "design_sample": "设计示意：目录编号、章节标题、提示框、表头和插图将使用同一品牌色；此时尚非真实成书预览。",
                "design_preview_path": design_preview.relative_to(workspace.root).as_posix(),
                "requirement_sources": requirement_sources,
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    workspace.append_event(
        "product_brief_previewed",
        {
            "topic": topic,
            "target_audience": target_audience,
            "artifact": str(output.relative_to(workspace.root)).replace("\\", "/"),
        },
    )
    write_request_contract(
        workspace, topic, target_audience, constraints, requirement_sources,
        brand_color_intent=presentation.brand_color_intent if presentation else None,
        semantics=semantics,
    )
    refresh_orchestration_after_controlled_update(workspace, 'brief-preview', snapshot=update_snapshot,
        output_paths=[output, design_preview, workspace.root / 'runs/request-contract.yaml'])
    return output


def _brief_requirement_sources(
    payload: StructuredBriefInput | None,
    constraints: DetailConstraints,
    *,
    interactive: bool,
    brand_color_explicit: bool = False,
) -> dict[str, str]:
    """Classify persisted values so defaults cannot masquerade as user requirements."""
    fields = payload.model_fields_set if payload is not None else set()
    sources: dict[str, str] = {}
    for field in (
        "target_pages",
        "target_words",
        "language",
        "layout_profile",
        "audience_level",
        "delivery_format",
        "review_policy",
        "brand_color_intent",
        *BriefSemanticFields.model_fields,
    ):
        if payload is not None and field in payload.requirement_sources:
            sources[field] = payload.requirement_sources[field]
        elif (field in fields or (interactive and field == "audience_level")
              or (field == "brand_color_intent" and (brand_color_explicit or
                  (payload is not None and payload.presentation is not None and payload.presentation.brand_color_intent is not None)))):
            sources[field] = "user_explicit"
        elif field in {"delivery_format", "language", "layout_profile", "review_policy", "brand_color_intent"}:
            sources[field] = "default"
        else:
            sources[field] = "model_recommendation"
    if constraints.delivery_format.value == "both" and sources["delivery_format"] == "default":
        sources["delivery_format"] = "model_recommendation"
    if (payload is not None and payload.reader_request
            and not re.search(r'橙|紫|品牌色|主色|#[0-9A-Fa-f]{6}', payload.reader_request)):
        sources['brand_color_intent'] = 'default'
    return sources


def _status_effective_constraints(workspace: Workspace) -> dict[str, object] | None:
    """Expose the actual persisted delivery contract for host recovery decisions."""
    brief_path = workspace.root / "planning" / "product-brief.md"
    if not _v_is_file(brief_path):
        return None
    try:
        _, body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
        constraints = parse_detail_constraints(body)
    except (OSError, ValueError):
        return None
    if constraints is None:
        return None
    payload: dict[str, object] = constraints.model_dump(mode="json")
    payload["fingerprints"] = brief_contract_fingerprints(body)
    request_contract = read_request_contract(workspace)
    payload["requirement_sources"] = (
        request_contract.get("requirements", {}) if request_contract is not None else {}
    )
    return payload


def _ensure_approval_identity(workspace: Workspace, interactive: bool) -> str:
    """Persist the audit identity without exposing it as an end-user workflow step."""
    stored = workspace.approval_identity()
    if stored is not None:
        return stored
    del interactive
    return workspace.configure_approval_identity("用户")


def _approval_identity_for_action(workspace: Workspace, override: str | None) -> str:
    """Use an explicit one-off identity or the workspace profile with a legacy fallback."""
    if override and override.strip():
        return override.strip()
    return workspace.approval_identity() or workspace.configure_approval_identity("用户")


def _capture_orchestration_action_or_exit(workspace: Workspace, action: str, *, chapter_number: int | None = None) -> str | None:
    """Keep controller handoff capture internal to one state-changing CLI command."""
    try:
        return capture_orchestration_action(workspace, action, chapter_number=chapter_number)
    except OrchestrationWorkOrderError as error:
        typer.echo(f"当前操作无法继续：{error}", err=True)
        raise typer.Exit(code=1) from error


@zh_command(user_visible=True)
def brief(
    workspace: WorkspacePath,
    topic: Annotated[str | None, typer.Argument(help="知识产品主题")] = None,
    interactive: Annotated[
        bool, typer.Option("--interactive/--no-interactive", help="是否使用终端逐项问答", show_default=False)
    ] = True,
    target_pages: Annotated[
        str | None, typer.Option(help="目标 PDF 页数或页数区间，例如 10-20")
    ] = None,
    target_words: Annotated[
        str | None, typer.Option(help="目标总字数或字数区间，例如 30000-50000")
    ] = None,
    target_audience: Annotated[str | None, typer.Option(help="目标受众（必填）")] = None,
    language: Annotated[str, typer.Option(help="书稿语言：中文/英文")] = "中文",
    layout_profile: Annotated[str, typer.Option(help="固定版式：A4 标准版式")] = "A4 标准版式",
    audience_level: Annotated[
        str | None, typer.Option(help="受众级别（必填）：入门/进阶/专家")
    ] = None,
    delivery_format: Annotated[str, typer.Option(help="交付格式：Markdown/PDF/两者")] = "Markdown",
    review_policy: Annotated[
        str,
        typer.Option(help="审稿策略：deterministic_only/independent_review_required"),
    ] = "deterministic_only",
    subtitle: Annotated[str | None, typer.Option(help="封面副标题（可选）")] = None,
    edition: Annotated[str | None, typer.Option(help="出版版本（可选）")] = None,
    publication_date: Annotated[str | None, typer.Option(help="发布日期，YYYY-MM-DD（可选）")] = None,
    publisher_label: Annotated[str | None, typer.Option(help="发布者标签（可选）")] = None,
    copyright_line: Annotated[str | None, typer.Option(help="版权行（可选）")] = None,
    brand_color_intent: Annotated[str | None, typer.Option("--brand-color", help="品牌色：橙色基调、紫色基调或 #RRGGBB；默认橙色")]= None,
    from_file: Annotated[Path | None, typer.Option("--from", help="结构化 Brief JSON 或 YAML 输入文件")] = None,
    confirm: Annotated[
        bool, typer.Option(help="确认使用已预览的结构化 Brief 创建工件", show_default=False)
    ] = False,
) -> None:
    """真实终端逐项创建 Brief；自动化请使用具名参数或 --from。"""
    opened_workspace = _open_workspace(workspace)
    presentation: PresentationOverrides | None = None
    structured_payload: StructuredBriefInput | None = None
    if interactive and from_file is None:
        if not _interactive_stdin_isatty():
            typer.echo("交互式 Brief 只能在真实终端运行；请使用 --no-interactive 的具名参数或 --from JSON/YAML。", err=True)
            raise typer.Exit(code=2)
        _ensure_approval_identity(opened_workspace, True)
        try:
            if not topic:
                typer.echo("交互式 Brief 必须提供主题。", err=True)
                raise typer.Exit(code=2)
            target_audience, constraints = _interactive_brief_input(opened_workspace, topic)
            presentation = _build_presentation_overrides(
                subtitle=subtitle,
                edition=edition,
                publication_date=publication_date,
                publisher_label=publisher_label,
                copyright_line=copyright_line,
                brand_color_intent=brand_color_intent,
            )
            if presentation is not None:
                validate_cover_contract(presentation, str(topic))
        except (TypeError, ValidationError, ValueError) as error:
            if isinstance(error, ValidationError) and "audience_level" in str(error):
                typer.echo(
                    "Brief 输入无效：受众级别不支持该写法；支持：入门/初学者/初级/初中级"
                    "（beginner）、进阶/中级/中高级（intermediate）、专家/高级（expert）。",
                    err=True,
                )
            else:
                typer.echo(f"Brief 输入无效：{error}", err=True)
            raise typer.Exit(code=2) from error
        except typer.Abort:
            raise
    else:
        try:
            if from_file is not None:
                payload = _load_structured_brief(from_file.expanduser().resolve())
                structured_payload = payload
                if topic and payload.topic and topic != payload.topic:
                    raise ValueError("命令主题与输入文件 topic 不一致。")
                topic = topic or payload.topic
                target_audience = payload.target_audience
                target_pages = payload.target_pages
                target_words = payload.target_words
                language = payload.language
                layout_profile = payload.layout_profile
                audience_level = payload.audience_level
                delivery_format = payload.delivery_format
                review_policy = payload.review_policy
                subtitle = payload.subtitle
                edition = payload.edition
                publication_date = payload.publication_date
                publisher_label = payload.publisher_label
                copyright_line = payload.copyright_line
                brand_color_intent = payload.brand_color_intent
                if payload.presentation is not None:
                    presentation = payload.presentation
            if not topic:
                raise ValueError("结构化 Brief 必须通过命令主题或输入文件 topic 提供主题。")
            if not target_audience or not target_audience.strip():
                raise ValueError("Brief 必须提供目标受众。")
            if not audience_level or not audience_level.strip():
                raise ValueError("Brief 必须提供受众级别（入门/进阶/专家）。")
            constraints = _build_constraints(
                target_pages=target_pages,
                target_words=target_words,
                language=language,
                layout_profile=layout_profile,
                audience_level=audience_level,
                delivery_format=delivery_format,
                review_policy=review_policy,
            )
            flat_presentation = _build_presentation_overrides(
                subtitle=subtitle,
                edition=edition,
                publication_date=publication_date,
                publisher_label=publisher_label,
                copyright_line=copyright_line,
                brand_color_intent=brand_color_intent,
            )
            if presentation is not None:
                values = flat_presentation.model_dump(exclude_none=True) if flat_presentation else {}
                values.update(presentation.model_dump(exclude_unset=True))
                presentation = PresentationOverrides.model_validate(values)
            else:
                presentation = flat_presentation
            if presentation is not None:
                validate_cover_contract(presentation, str(topic))
            require_original_reader_request(opened_workspace, structured_payload)
        except (TypeError, ValidationError, ValueError) as error:
            if isinstance(error, ValidationError) and "audience_level" in str(error):
                typer.echo(
                    "Brief 输入无效：受众级别不支持该写法；支持：入门/初学者/初级/初中级"
                    "（beginner）、进阶/中级/中高级（intermediate）、专家/高级（expert）。",
                    err=True,
                )
            else:
                typer.echo(f"Brief 输入无效：{error}", err=True)
            raise typer.Exit(code=2) from error
        _ensure_approval_identity(opened_workspace, False)
        requirement_sources = _brief_requirement_sources(
            structured_payload, constraints, interactive=False, brand_color_explicit=brand_color_intent is not None
        )
        preview = _write_brief_preview(
            opened_workspace,
            topic,
            target_audience.strip(),
            constraints,
            presentation,
            requirement_sources,
            (
                BriefSemanticFields(**{key: getattr(structured_payload, key) for key in BriefSemanticFields.model_fields})
                if structured_payload else None
            ),
        )
        typer.echo(_render_constraints_summary(target_audience.strip(), constraints))
        if not confirm:
            typer.echo(f"已生成待确认 Brief 预览：{preview}。确认后请以相同具名参数或 --from 加 --confirm 重新执行。")
            return
    brief_path = opened_workspace.root / "planning" / "product-brief.md"
    replacing_draft = _v_is_file(brief_path)
    action_work_order_id = (
        None
        if replacing_draft
        else _capture_orchestration_action_or_exit(opened_workspace, "brief")
    )
    create_or_replace = replace_unapproved_brief_draft if replacing_draft else create_product_brief
    try:
        output = create_or_replace(
            opened_workspace,
            str(topic),
            constraints,
            target_audience=target_audience.strip(),
            presentation=presentation,
            semantics=(BriefSemanticFields(**{key: getattr(structured_payload, key) for key in BriefSemanticFields.model_fields})
                       if structured_payload else None),
        )
    except (FileExistsError, ValueError) as error:
        fail_orchestration_action(opened_workspace, "brief", str(error))
        typer.echo(f"无法更新产品 Brief：{error}", err=True)
        raise typer.Exit(code=1) from error
    load_or_create_publication_profile(
        opened_workspace, language=constraints.language, overrides=presentation
    )
    write_request_contract(
        opened_workspace,
        str(topic),
        target_audience.strip(),
        constraints,
        _brief_requirement_sources(structured_payload, constraints, interactive=interactive, brand_color_explicit=brand_color_intent is not None),
        brand_color_intent=presentation.brand_color_intent if presentation else None,
        semantics=(BriefSemanticFields(**{key: getattr(structured_payload, key) for key in BriefSemanticFields.model_fields})
                   if structured_payload else None),
    )
    session_path = _brief_session_path(opened_workspace)
    if _v_exists(session_path):
        session_path.unlink()
    if replacing_draft:
        supersede_orchestration_work_orders(opened_workspace, "初始 Brief 草案已更新")
        create_orchestration_work_order(opened_workspace)
    else:
        complete_orchestration_action(
            opened_workspace,
            "brief",
            proof="已物化待确认产品 Brief",
            output_paths=[output, request_contract_path(opened_workspace)],
            work_order_id=action_work_order_id,
        )
    message = "已更新待确认产品 Brief" if replacing_draft else "已创建产品 Brief"
    typer.echo(f"{message}：{output}")


@zh_command(user_visible=True)
def status(
    workspace: WorkspacePath,
    json_output: Annotated[bool, typer.Option("--json", help="输出机器可读 JSON")] = False,
) -> None:
    """读取真实工件并报告当前阶段、阻断项和下一步。"""
    opened_workspace = _open_workspace(workspace)
    snapshot = inspect_workflow(opened_workspace)
    if json_output:
        payload = snapshot.model_dump(mode="json")
        payload["effective_constraints"] = _status_effective_constraints(opened_workspace)
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        # 宿主在 workspace 内操作，绝对路径只是噪音 token；统一替换为占位符。
        # 注意 JSON 转义：路径中的反斜杠在输出里是双反斜杠，需先转义再替换。
        escaped_root = str(opened_workspace.root).replace("\\", "\\\\")
        typer.echo(rendered.replace(escaped_root, "<workspace>"))
        return
    typer.echo(_render_workflow_status(snapshot))


@zh_command()
def route(
    workspace: WorkspacePath,
    write_work_order: Annotated[
        bool, typer.Option("--write-work-order", help="在 runs/work-orders 中写入本阶段工单")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="输出机器可读 JSON")] = False,
) -> None:
    """确定当前唯一允许的 Skill，并可选写入受限工作工单。"""
    opened_workspace = _open_workspace(workspace)
    try:
        decision = route_workflow(opened_workspace)
    except ValueError as error:
        typer.echo(f"暂不可路由：{error}", err=True)
        raise typer.Exit(1) from error
    if write_work_order:
        work_order = create_work_order(opened_workspace, decision)
        decision = decision.model_copy(
            update={"work_order_path": str(work_order.relative_to(opened_workspace.root)).replace("\\", "/")}
        )
    if json_output:
        typer.echo(decision.model_dump_json(indent=2))
        return
    typer.echo(_render_workflow_status(decision.status))
    if decision.work_order_path:
        typer.echo(f"已写入工作工单：{decision.work_order_path}")


@zh_command("orchestration-plan")
def orchestration_plan(
    workspace: WorkspacePath,
    adopt_legacy: Annotated[
        bool,
        typer.Option("--adopt-legacy", help="仅在用户确认后将旧工作区纳入 v1.1 编排控制"),
    ] = False,
    refresh: Annotated[bool, typer.Option("--refresh", help="检查失效工单并受控重路由")] = False,
    reason: Annotated[str | None, typer.Option("--reason", help="重路由原因；与 --refresh 配合使用")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="输出机器可读 JSON")] = False,
) -> None:
    """为当前宏阶段创建或复用版本绑定的 v1.1 编排工单。"""
    opened_workspace = _open_workspace(workspace)
    if refresh and not (reason or "").strip():
        typer.echo("无法刷新编排工单：--refresh 必须同时填写 --reason。", err=True)
        raise typer.Exit(code=1)
    try:
        output = create_orchestration_work_order(
            opened_workspace,
            adopt_legacy=adopt_legacy,
            refresh_reason=reason.strip() if refresh and reason else None,
        )
    except OrchestrationWorkOrderError as error:
        typer.echo(f"无法创建编排工单：{error}", err=True)
        raise typer.Exit(code=1) from error
    payload = {
        "work_order_path": str(output.relative_to(opened_workspace.root)).replace("\\", "/"),
        "refresh_reason": reason.strip() if refresh and reason else None,
        "status": inspect_workflow(opened_workspace).model_dump(mode="json"),
    }
    if json_output:
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        escaped_root = str(opened_workspace.root).replace("\\", "\\\\")
        typer.echo(rendered.replace(escaped_root, "<workspace>"))
        return
    typer.echo(f"已创建或复用编排工单：{payload['work_order_path']}")
    typer.echo(_render_workflow_status(inspect_workflow(opened_workspace)))


@zh_command("orchestration-migration-preview")
def orchestration_migration_preview_command(
    workspace: WorkspacePath,
    json_output: Annotated[bool, typer.Option("--json", help="输出机器可读 JSON")] = False,
) -> None:
    """只读预览旧工作区进入 v1.1 编排时会新增的控制记录。"""
    payload = orchestration_migration_preview(_open_workspace(workspace))
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(str(payload["message"]))
    typer.echo(f"需要用户确认：{'是' if payload['requires_confirmation'] else '否'}")
    if payload["existing_artifacts"]:
        typer.echo("现有工件：" + "、".join(payload["existing_artifacts"]))


@zh_command("start-run-intent")
def start_run_intent_command(workspace: WorkspacePath) -> None:
    """内部：持久化“继续完成直到交付”的用户运行意图。"""
    try:
        intent = start_run_intent(_open_workspace(workspace))
    except ValueError as error:
        typer.echo(f"持续运行意图未登记：{error}。先查 recovery-view 和原 operation-status；draft可调用不代表已恢复自动生产。", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已登记持续运行意图：{intent.state.value}")


@zh_command("pause-run-intent")
def pause_run_intent_command(
    workspace: WorkspacePath,
    reason: Annotated[str, typer.Option(help="暂停原因")]= "用户暂停",
) -> None:
    """内部：暂停持续运行意图，不删除任何进度或审批记录。"""
    intent = pause_run_intent(_open_workspace(workspace), reason)
    if intent is None:
        typer.echo("当前没有持续运行意图。")
        return
    typer.echo(f"已暂停持续运行意图：{intent.state.value}")


@zh_command("record-pdf-visual-review")
def record_pdf_visual_review_command(
    workspace: WorkspacePath,
    by: EditorIdentity = None,
    confirm_style_anchor: Annotated[bool, typer.Option(help="仅在用户明确认可已展示的候选母版后使用")] = False,
    confirm_manual_coverage: Annotated[bool, typer.Option(help="实际用户已直接核对当前全部核验页；不可覆盖失败或blocker")] = False,
    confirm_content: Annotated[bool, typer.Option(help="实际用户同时确认当前内容；旧视觉回执不自动扩展")] = False,
    user_response: Annotated[str | None, typer.Option("--response", help="本次真实用户确认原话，不用测试审批代填")] = None,
) -> None:
    """内部：将人工图表视觉复核绑定到当前 PDF 版本。"""
    opened_workspace = _open_workspace(workspace)
    try:
        from knowledge_product_workflow.pdf_visual import uses_v6_profile
        from knowledge_product_workflow.runtime import _read_orchestration_state
        from knowledge_product_workflow.user_decisions import precheck_pdf_scope
        state = _read_orchestration_state(opened_workspace)
        if uses_v6_profile(opened_workspace) or (state is not None and state.request_capture_revision >= 1):
            scopes = {'visual'} | ({'manual_full_coverage'} if confirm_manual_coverage else set())
            scopes |= {'content'} if confirm_content else set()
            scopes |= {'style_anchor'} if confirm_style_anchor else set()
            precheck_pdf_scope(opened_workspace, scopes, user_response)
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"无法记录 PDF 视觉复核：{error}", err=True)
        raise typer.Exit(code=1) from error
    action_work_order_id = (
        _capture_orchestration_action_or_exit(opened_workspace, "record-pdf-visual-review")
        if inspect_workflow(opened_workspace).stage == "pdf_manual_review_pending" else None
    )
    identity = _approval_identity_for_action(opened_workspace, by)
    try:
        from knowledge_product_workflow.delivery_preflight import require_delivery_preflight
        require_delivery_preflight(opened_workspace)
        output = record_manual_pdf_visual_review(opened_workspace, identity,confirm_style_anchor=confirm_style_anchor,
            confirm_manual_coverage=confirm_manual_coverage, confirm_content=confirm_content, user_response=user_response)
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"无法记录 PDF 视觉复核：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已记录当前 PDF 的视觉复核：{output}")
    if action_work_order_id is not None:
        complete_orchestration_action(
            opened_workspace, "record-pdf-visual-review", proof="用户已确认当前 PDF 的人工视觉复核",
            output_paths=[output], work_order_id=action_work_order_id,
        )


@zh_command("audit-pdf-visual")
def audit_pdf_visual_command(workspace: WorkspacePath,
    sample_limit: Annotated[int | None, typer.Option("--sample-limit", min=1, help="初查清晰页预算；不传则覆盖当前PDF全页，必选异常页不截断")] = None,
) -> None:
    """量测当前 v6 PDF 全页，默认全页清晰核验；显式页预算供初查。"""
    opened_workspace = _open_workspace(workspace)
    try:
        authorize_command(opened_workspace, "audit-pdf-visual")
        order_id = _capture_orchestration_action_or_exit(opened_workspace, "audit-pdf-visual")
        from pypdf import PdfReader
        limit = sample_limit if sample_limit is not None else len(PdfReader(opened_workspace.root / "delivery/book.pdf").pages)
        report = audit_pdf_visual(opened_workspace, sample_limit=limit)
    except (OSError, RuntimeError, ValueError) as error:
        typer.echo(f"无法完成逐页视觉量测：{error}", err=True)
        raise typer.Exit(code=1) from error
    complete_orchestration_action(
        opened_workspace, "audit-pdf-visual", proof="已完成全页量测及清晰页图",
        output_paths=[audit_path(opened_workspace)], work_order_id=order_id,
    )
    from knowledge_product_workflow.visual_audit import visual_observation_worklist
    worklist = visual_observation_worklist(report)
    typer.echo(
        f"已量测 {report.page_count} 页，清晰覆盖 {len(worklist['observable_pages'])} 页；"
        f"已有效 {len(worklist['reusable_pages'])}，未观察 {len(worklist['pending_pages'])}，"
        f"失败 {len(worklist['failed_pages'])}，待判断 {len(worklist['uncertain_pages'])}，"
        f"缺清晰图 {len(worklist['unavailable_pages'])}；全页观察齐备={worklist['full_coverage_ready']}。"
        "自动指标与本人最终视觉审批仍按原门核验。"
    )


@zh_command("pdf-observation-schema")
def pdf_observation_schema_command() -> None:
    """只读输出视觉观察 JSON 数组 Schema；记录不证明实际看过页面。"""
    schema = VisualObservation.model_json_schema()
    schema['required'] = sorted(set(schema.get('required', [])) | {'observation_input_sha256', 'page_image_sha256'})
    for field in ('observation_input_sha256', 'page_image_sha256'):
        schema['properties'][field] = next(item for item in schema['properties'][field]['anyOf'] if item.get('type') != 'null')
    typer.echo(json.dumps({"type": "array", "minItems": 1,
                          "items": schema}, ensure_ascii=False, indent=2))


@zh_command("pdf-observation-template")
def pdf_observation_template_command(workspace: WorkspacePath) -> None:
    """只读列出当前清晰页图和待填观察，不生成通过证明。"""
    from hashlib import sha256

    from knowledge_product_workflow.visual_audit import (
        current_visual_audit,
        visual_observation_worklist,
    )
    opened = _open_workspace(workspace)
    report = current_visual_audit(opened)
    if report is None:
        typer.echo("当前视觉量测缺失或已过期；请先运行 kp audit-pdf-visual。", err=True)
        raise typer.Exit(1)
    worklist = visual_observation_worklist(report)
    pages = []
    for metric in report.pages:
        if metric.page_number not in worklist["observable_pages"]:
            continue
        path = (opened.root / metric.high_resolution_image).resolve()
        if (path.is_relative_to(opened.root.resolve()) and _v_is_file(path)
                and sha256(_v_read_bytes(path)).hexdigest() == metric.high_resolution_image_sha256):
            pages.append({"page_number": metric.page_number, "image": metric.high_resolution_image,
                          "image_sha256": metric.high_resolution_image_sha256,
                          "context_required_pages": metric.layout_context_required_pages,
                          "bottom_gap_fraction": metric.bottom_gap_fraction,
                          "horizontal_text_candidate_count": metric.horizontal_text_candidate_count,
                          "horizontal_text_candidates": [c.model_dump() for c in metric.horizontal_text_candidates]})
    typer.echo(json.dumps({
        **worklist, "pages": pages,
        "observations": [{"page_number": p["page_number"], "assessment": "uncertain", "note": "",
                          "observation_input_sha256": report.observation_input_sha256,
                          "page_image_sha256": report.pages[p['page_number']-1].high_resolution_image_sha256,
                          "responsible_skill": "kp-visual-reviewer", "scope":"page",
                          **({"view_kind":"overview", "evidence_anchor":"", "checked_dimensions":[],
                              "context_page_sha256":{}} if report.evidence_revision else {})}
                         for p in pages if p["page_number"] in worklist["pending_pages"]],
        "note": "scope=page仅限该页可见字号/裁切/布局；跨页或内容判断保持context。增量模板仅填pending_pages：实际看对应清晰图后填写依据，只提交非空observations数组。"
                "reusable_pages保留；failed先修上游，uncertain交判断。needs_clear_images保留manual_required，"
                "无图／空输入不算通过；全页观察齐备也不代替自动指标及本人最终审批。",
    }, ensure_ascii=False, indent=2))


@zh_command("record-pdf-visual-observations")
def record_pdf_visual_observations_command(
    workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--from", help="已查看页面的观察记录 JSON 数组")],
) -> None:
    """记录视觉 Skill 的页码与具体观察；不能替代人工出版确认。"""
    opened_workspace = _open_workspace(workspace)
    try:
        path = _workspace_input_path(opened_workspace, input_path, label="视觉观察输入")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise TypeError("视觉观察输入须为 JSON 数组")
        observations = [VisualObservation.model_validate(item) for item in raw]
        if any(not o.observation_input_sha256 or not o.page_image_sha256 for o in observations):
            raise ValueError('新观察须使用当前pdf-observation-template的输入与PNG身份；旧记录只读，不手工移绑')
        report = record_visual_observations(opened_workspace, observations)
        create_orchestration_work_order(opened_workspace)
    except (OSError, ValueError, TypeError) as error:
        typer.echo(f"无法记录视觉观察：{error}", err=True)
        raise typer.Exit(code=1) from error
    from knowledge_product_workflow.visual_audit import visual_observation_worklist
    work = visual_observation_worklist(report)
    next_action = ('先修失败页' if work['failed_pages'] else '先解决待判断页' if work['uncertain_pages']
        else '继续观察当前pending_pages；未全页观察时仅可按明确本人完整覆盖路径处理' if work['pending_pages']
        else '先交付预检，再按明确范围请求本人确认')
    typer.echo(f"已绑定 {len(report.observations)} 条视觉观察；失败={work['failed_pages']}；待观察={work['pending_pages']}；{next_action}。")


@zh_command("complete-orchestration-work-order")
def complete_orchestration_work_order_command(
    workspace: WorkspacePath,
    work_order_id: Annotated[str, typer.Option(help="当前根编排 Skill 提供的工单标识")],
    proof: Annotated[str, typer.Option(help="本次受控动作的简短完成证明")],
) -> None:
    """由专业 Skill 完成其已验证的单一编排工单。"""
    try:
        output = complete_orchestration_work_order(_open_workspace(workspace), work_order_id, proof)
    except (FileNotFoundError, OrchestrationWorkOrderError) as error:
        typer.echo(f"无法完成编排工单：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已记录编排工单完成证明：{output}")


@zh_command("cancel-orchestration-work-order")
def cancel_orchestration_work_order_command(
    workspace: WorkspacePath,
    work_order_id: Annotated[str, typer.Option(help="要取消的编排工单标识")],
    reason: Annotated[str, typer.Option(help="取消原因")],
) -> None:
    """取消当前受控动作并释放其正文租约；不会删除书稿工件。"""
    try:
        output = cancel_orchestration_work_order(_open_workspace(workspace), work_order_id, reason)
    except (FileNotFoundError, OrchestrationWorkOrderError) as error:
        typer.echo(f"无法取消编排工单：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已取消编排工单：{output}")


@zh_command("record-research-impact")
def record_research_impact_command(
    workspace: WorkspacePath,
    impact: Annotated[
        str,
        typer.Option(help="no_product_change/packaging_change/content_change/unverified"),
    ],
    summary: Annotated[str, typer.Option(help="面向编排器的简短研究结论，不进入读者正文")],
) -> None:
    """记录研究结果对课程范围的影响，并给根编排器提供确定性恢复路径。"""
    opened_workspace = _open_workspace(workspace)
    try:
        normalized_impact = ResearchImpact(impact)
        action_work_order_id = _capture_orchestration_action_or_exit(
            opened_workspace, "record-research-impact"
        )
        output = record_research_impact(opened_workspace, normalized_impact, summary)
    except (ValueError, OSError) as error:
        typer.echo(f"无法记录研究影响：{error}", err=True)
        raise typer.Exit(code=1) from error
    complete_orchestration_action(
        opened_workspace,
        "record-research-impact",
        proof=f"已登记真实研究影响决策：{normalized_impact.value}",
        output_paths=[output],
        work_order_id=action_work_order_id,
    )
    typer.echo(f"已记录研究影响决策：{output}")


@zh_command("preflight-independent-review")
def preflight_independent_review_command(
    workspace: WorkspacePath,
    platform: Annotated[str, typer.Option(help="当前宿主平台名称")] = "当前宿主",
) -> None:
    """记录独立审稿能力预检；无真实宿主适配器时会诚实地阻断。"""
    try:
        snapshot = preflight_independent_review(_open_workspace(workspace), platform=platform)
    except HostCapabilityPreflightError as error:
        typer.echo(f"独立审稿预检未通过：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"独立审稿预检通过：策略={snapshot.review_policy}；宿主={snapshot.capabilities.platform}")


@zh_command(user_visible=True)
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="输出机器可读 JSON")] = False,
) -> None:
    """检查本机 KP、Quarto、XeLaTeX 和中文字体，不下载或修改任何组件。"""
    checks = _doctor_checks()
    if json_output:
        typer.echo(json.dumps(checks, ensure_ascii=False, indent=2))
        return
    labels = {
        "kp": "KP 命令",
        "quarto": "Quarto",
        "xelatex": "XeLaTeX",
        "microsoft_yahei": "微软雅黑字体",
        "pingfang_sc": "默认中文正文：PingFang SC",
        "pingfang_sc_semibold": "默认中文标题/强调：PingFang SC Semibold",
        "inter": "默认英文正文：Inter",
        "jetbrains_mono": "默认代码/等宽标识：JetBrains Mono",
        "d2": "D2 图表工具",
        "diagram_browser": "图表离线转换浏览器",
        "pdftoppm": "PDF 截图工具",
    }
    for key, check in checks.items():
        state = "已检测到" if check["available"] else "未检测到"
        if check.get("status") in {"timeout", "compile_failed", "probe_unavailable"}:
            state = "未能确认"
        detail = f"：{check['path']}" if check["path"] else ""
        if key == "quarto" and check.get("source"):
            detail += f"（来源：{check['source']}；版本：{check['version']}）"
        if check.get("diagnostic"):
            detail += f"（{check['diagnostic']}）"
        typer.echo(f"{labels[key]}：{state}{detail}")


@zh_command("record-network-timeout")
def record_network_timeout(
    workspace: WorkspacePath,
    endpoint_category: Annotated[str, typer.Option(help="超时端点类别，不填写 URL 或请求内容")],
    timeout_seconds: Annotated[int, typer.Option(min=1, max=8, help="已设置的超时秒数，最长 8 秒")],
) -> None:
    """记录一次网络超时，不保存 URL、提示词或其他敏感请求内容。"""
    opened_workspace = _open_workspace(workspace)
    opened_workspace.append_event(
        "network_timeout",
        {"endpoint_category": endpoint_category, "timeout_seconds": timeout_seconds},
    )
    typer.echo(f"已记录网络超时：{endpoint_category}，{timeout_seconds} 秒")


@zh_command()
def approve(
    artifact: Path,
    workspace: WorkspacePath,
    by: EditorIdentity = None,
) -> None:
    """使用已保存的审批身份批准 Markdown 工件。"""
    opened_workspace = _open_workspace(workspace)
    try:
        authorize_workflow_transition(opened_workspace, "approve")
    except HostCapabilityPreflightError as error:
        typer.echo(f"当前操作无法继续：{error}", err=True)
        raise typer.Exit(code=1) from error
    resolved_artifact = artifact.resolve()
    if not resolved_artifact.is_relative_to(opened_workspace.root.resolve()):
        raise typer.BadParameter("工件必须位于指定工作区内")
    decision_target = target_for_artifact(opened_workspace, resolved_artifact)
    if decision_target is UserDecisionTarget.BRIEF:
        try:
            require_original_reader_request(opened_workspace, persisted=True)
        except ValueError as error:
            typer.echo(f'无法确认当前产物：{error}', err=True)
            raise typer.Exit(code=1) from error
    decision_record = None
    if decision_target is not None and managed_user_decisions(opened_workspace):
        try:
            decision_record = require_approved_user_decision(opened_workspace, decision_target)
        except ValueError as error:
            typer.echo(f"无法确认当前产物：{error}", err=True)
            raise typer.Exit(code=1) from error
    action_work_order_id = _capture_orchestration_action_or_exit(opened_workspace, "approve")
    metadata, body = parse_markdown_artifact(resolved_artifact.read_text(encoding="utf-8"))
    identity = _approval_identity_for_action(opened_workspace, by)
    approved = transition(metadata, ArtifactStatus.APPROVED, approved_by=identity)
    resolved_artifact.write_text(render_markdown_artifact(approved, body), encoding="utf-8")
    if decision_target is not None and decision_record is not None:
        consume_approved_user_decision(
            opened_workspace,
            decision_target,
            record=decision_record,
        )
    opened_workspace.append_event(
        "artifact_approved", {"artifact": str(resolved_artifact), "approved_by": identity}
    )
    complete_orchestration_action(
        opened_workspace,
        "approve",
        proof="已批准当前用户确认的工件",
        output_paths=[resolved_artifact],
        work_order_id=action_work_order_id,
    )
    typer.echo(f"已确认当前产物：{resolved_artifact}")


@zh_command("request-user-decision")
def request_user_decision_command(
    workspace: WorkspacePath,
    target: Annotated[UserDecisionTarget, typer.Argument(help="待确认产物类型")],
    work_order_id: Annotated[str | None, typer.Option(help="当前编排工单 ID（可选）")] = None,
    scope: Annotated[list[str] | None, typer.Option("--scope", help="PDF请求范围，可重复；完整覆盖须单独请求manual_full_coverage")] = None,
) -> None:
    """内部：登记当前版本已展示，等待宿主转交用户自然语言决定。"""
    opened_workspace = _open_workspace(workspace)
    try:
        if target is UserDecisionTarget.PDF:
            from knowledge_product_workflow.user_decisions import pdf_requested_scopes
            pdf_requested_scopes(scope)
        update_snapshot = capture_orchestration_update(opened_workspace) if target is UserDecisionTarget.PDF else None
        record = request_user_decision(opened_workspace, target, work_order_id=work_order_id, scopes=scope)
        if target is UserDecisionTarget.PDF:
            refresh_orchestration_after_controlled_update(opened_workspace,'request-user-decision',
                snapshot=update_snapshot,output_paths=[opened_workspace.root/'runs/interaction/pdf.json'])
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"无法登记用户决定：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已登记当前版本的用户决定：{record.target.value}")
    if record.question:
        typer.echo(record.question)


@zh_command("record-user-decision")
def record_user_decision_command(
    workspace: WorkspacePath,
    target: Annotated[UserDecisionTarget, typer.Argument(help="待确认产物类型")],
    decision: Annotated[UserResponseDecision, typer.Option(help="宿主解析后的用户决定")],
    response: Annotated[str, typer.Option(help="用户原始回复，仅保存 SHA-256")],
    feedback: Annotated[str | None, typer.Option(help="修订反馈摘要，可选")] = None,
    by: EditorIdentity = None,
    scope: Annotated[list[str] | None, typer.Option("--scope", help="PDF实际确认范围；不能扩大已展示问题")] = None,
) -> None:
    """内部：保存用户自然语言决定，不持久化完整对话。"""
    opened_workspace = _open_workspace(workspace)
    identity = _approval_identity_for_action(opened_workspace, by)
    try:
        if target is UserDecisionTarget.PDF:
            from knowledge_product_workflow.user_decisions import precheck_pdf_reply
            precheck_pdf_reply(opened_workspace,scope,response)
        update_snapshot = capture_orchestration_update(opened_workspace) if target is UserDecisionTarget.PDF else None
        record = persist_user_decision(
            opened_workspace,
            target,
            decision,
            approved_by=identity,
            response=response,
            feedback_summary=feedback,
            confirmed_scope=scope,
        )
        if target is UserDecisionTarget.PDF:
            refresh_orchestration_after_controlled_update(opened_workspace,'record-user-decision',
                snapshot=update_snapshot,output_paths=[opened_workspace.root/'runs/interaction/pdf.json'])
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"无法记录用户决定：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已记录用户决定：{record.target.value}/{record.state.value}")


@zh_command("amend-brief")
def amend_brief(
    workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--from", help="部分 Brief 修订 JSON 或 YAML 文件")],
) -> None:
    """生成受控 Brief 修订预览，不会修改当前已批准 Brief。"""
    opened_workspace = _open_workspace(workspace)
    try:
        amendment = _load_brief_amendment(input_path.expanduser().resolve())
        candidate = _build_brief_amendment_candidate(opened_workspace, amendment)
        preview = create_brief_amendment_preview(opened_workspace, candidate)
    except (OSError, ValueError) as error:
        typer.echo(f"无法生成 Brief 修订预览：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        "已生成 Brief 修订预览："
        f"{preview}；变更类型：{'内容约束' if candidate.revision_kind == 'content' else '交付包装'}；"
        "请等待用户批准后运行 kp approve-brief-amendment。"
    )


@zh_command("approve-brief-amendment")
def approve_brief_amendment_command(
    workspace: WorkspacePath,
    by: EditorIdentity = None,
) -> None:
    """批准待确认的 Brief 修订并归档上一个已批准版本。"""
    opened_workspace = _open_workspace(workspace)
    try:
        output = approve_brief_amendment(opened_workspace, _approval_identity_for_action(opened_workspace, by))
    except (OSError, ValueError) as error:
        typer.echo(f"无法批准 Brief 修订：{error}", err=True)
        raise typer.Exit(code=1) from error
    supersede_orchestration_work_orders(opened_workspace, "已批准 Brief 修订")
    create_orchestration_work_order(opened_workspace)
    typer.echo(f"已确认 Brief 修改：{output}")


@zh_command("brief-schema")
def brief_schema_command() -> None:
    """输出具体学习成果、来源与同次出版预览的公开输入契约。"""
    typer.echo(json.dumps({"input": StructuredBriefInput.model_json_schema(),
                          "amendment": BriefAmendmentInput.model_json_schema(),
                          "rules": ["未限定页数/字数时省略字段，不传空字符串", "推断要求使用requirement_sources标明model_recommendation",
                                    "presentation承载同次确认的封面/品牌/后端，不替代用户批准"]}, ensure_ascii=False, indent=2))


@zh_command("publication-schema")
def publication_schema_command() -> None:
    """输出出版更新的完整公开 JSON Schema。"""
    typer.echo(json.dumps(PublicationUpdateInput.model_json_schema(), ensure_ascii=False, indent=2))




@zh_command("update-publication")
def update_publication(
    workspace: WorkspacePath,
    input_path: Annotated[
        Path,
        typer.Option(
            "--from",
            help="出版信息更新 JSON/YAML（字段：cover_label、footer_label、版权行、版本递增等）",
        ),
    ],
    confirm: Annotated[
        bool, typer.Option(help="确认应用已预览的出版信息更新并自动刷新派生交付物", show_default=False)
    ] = False,
    by: EditorIdentity = None,
) -> None:
    """预览或确认读者可见出版信息；不会修订 Brief、蓝图或章节。"""
    opened_workspace = _open_workspace(workspace)
    try:
        resolved_input = _workspace_input_path(
            opened_workspace, input_path, label="出版信息更新文件"
        )
        language, legacy_overrides = _publication_context(opened_workspace)
        if not confirm:
            update = _load_publication_update(resolved_input)
            preview, candidate = preview_publication_update(
                opened_workspace, update, language=language, overrides=legacy_overrides
            )
            typer.echo(
                f"已生成出版信息预览：{preview}；变更字段：{', '.join(candidate.changed_fields)}；"
                f"版本：{candidate.profile.edition_label}。请等待用户确认后以相同参数加 --confirm 运行。"
            )
            return
        from knowledge_product_workflow.presentation import publication_update_preview_path
        changed_fields = json.loads(publication_update_preview_path(opened_workspace).read_text(encoding='utf8'))['changed_fields']
        profile = apply_publication_update(
            opened_workspace, _approval_identity_for_action(opened_workspace, by)
        )
    except (OSError, ValueError, TypeError, FileExistsError) as error:
        typer.echo(f"无法更新出版信息：{error}", err=True)
        raise typer.Exit(code=1) from error

    try:
        refreshed = refresh_publication_delivery(opened_workspace, changed_fields=changed_fields)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        create_orchestration_work_order(opened_workspace, refresh_reason="出版配置已更新，派生物需恢复")
        typer.echo(
            f"出版信息已更新为 {profile.edition_label}，但派生交付物刷新失败：{error}。"
            "请运行 kp status --json 读取当前恢复动作。",
            err=True,
        )
        raise typer.Exit(code=1) from error
    if inspect_workflow(opened_workspace).stage != "completed":
        create_orchestration_work_order(opened_workspace, refresh_reason="已受控更新出版配置与派生物")
    if not refreshed["refreshed"]:
        typer.echo(f"出版信息已更新为 {profile.edition_label}；尚未汇编书稿，后续发布阶段会自动使用该配置。")
        return
    manifest = refreshed["manifest"]
    if refreshed.get('pending'):
        typer.echo(f'出版信息已更新为{profile.edition_label}，PDF已生成；下一步全页观察与delivery-preflight，再按当前范围请求本人确认。尚未最终校验或发布。')
        return
    if manifest is None:
        typer.echo(
            f"出版信息已更新为 {profile.edition_label}，已重建 Markdown/PDF 派生物，"
            f"但交付质量门未通过：{refreshed['report']}。未生成交付清单。",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(
        f"出版信息已更新为 {profile.edition_label}；已自动重建派生交付物与交付清单：{manifest}"
    )


















@zh_command("controlled-exception-schema")
def controlled_exception_schema_command(json_output: Annotated[bool, typer.Option("--json", help="输出JSON，默认仍为YAML")] = False) -> None:
    """输出受控例外的语义输入 Schema；不要求填写 SHA-256。"""
    typer.echo(json.dumps(controlled_exception_schema(), ensure_ascii=False, indent=2) if json_output else yaml.safe_dump(controlled_exception_schema(), allow_unicode=True, sort_keys=False))


@zh_command("submit-controlled-exceptions")
def submit_controlled_exceptions_command(
    workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--from", help="受控例外 JSON 或 YAML 输入文件")],
    by: EditorIdentity = None,
) -> None:
    """提交已人工确认的、版本绑定的质量门例外。"""
    opened_workspace = _open_workspace(workspace)
    try:
        payload = _load_controlled_exception_submission(
            _workspace_input_path(opened_workspace, input_path, label="受控例外输入文件")
        )
        output = submit_controlled_exceptions(
            opened_workspace,
            payload.exceptions,
            _approval_identity_for_action(opened_workspace, by),
        )
    except (OSError, ValueError, TypeError) as error:
        typer.echo(f"无法提交受控例外：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已提交受控例外：{output}；请运行 kp validate 重新生成质量报告。")


@zh_command()
def outline(workspace: WorkspacePath) -> None:
    """生成空白蓝图提交模板；不生成任何通用课程内容。"""
    opened_workspace = _open_workspace(workspace)
    try:
        output = create_curriculum_blueprint_template(
            opened_workspace, opened_workspace.root / "planning" / "product-brief.md"
        )
    except (CurriculumPrerequisiteError, FileExistsError) as error:
        typer.echo(f"无法生成蓝图模板：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"已生成空白蓝图模板：{output}。请由 kp-curriculum-architect 填写主题化内容后，运行 kp submit-outline --from \"{output}\"。"
    )


@zh_command("outline-schema")
def outline_schema(json_output: Annotated[bool, typer.Option("--json", help="输出JSON，默认仍为YAML")] = False) -> None:
    """输出课程蓝图可填写的公开 Schema，不读取工作区。"""
    typer.echo(json.dumps(curriculum_outline_schema(), ensure_ascii=False, indent=2) if json_output else yaml.safe_dump(curriculum_outline_schema(), allow_unicode=True, sort_keys=False))


@zh_command("outline-view")
def outline_view_command(
    workspace: WorkspacePath,
    input_path: Annotated[Path | None, typer.Option("--from", help="当前工作区内的候选蓝图；省略时读当前蓝图")] = None,
    view: Annotated[OutlineViewKind, typer.Option("--view", help="toc纯目录、design设计说明、review覆盖/修订对照")] = OutlineViewKind.TOC,
    json_output: Annotated[bool, typer.Option("--json", help="输出同一快照的结构化视图")] = False,
) -> None:
    """只读查看纯目录、设计说明或覆盖对照，不批准或改写蓝图。"""
    try:
        opened = _open_workspace(workspace)
        if input_path is not None:
            _workspace_input_path(opened, input_path, label="蓝图候选")
        value = inspect_curriculum_outline(opened, input_path=input_path, view=view.value)
    except (OSError, ValueError, TypeError) as error:
        typer.echo(f"无法查看课程蓝图：{error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2) if json_output else value["markdown"])


@zh_command("claim-classification-schema")
def claim_classification_schema_command() -> None:
    """输出事实、作者建议和情境案例的受控分类输入契约。"""
    typer.echo(json.dumps(classification_schema(), ensure_ascii=False, indent=2))


@zh_command("classify-claim")
def classify_claim_command(
    workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--from", help="工作区内的 Claim 分类 JSON/YAML 文件")],
) -> None:
    """将当前 Claim 分类以合约哈希绑定并追加到审计台账。"""
    opened_workspace = _open_workspace(workspace)
    resolved = input_path.expanduser().resolve()
    if not _v_is_file(resolved) or not resolved.is_relative_to(opened_workspace.root.resolve()):
        typer.echo("Claim 分类输入必须位于当前书稿工作区内。", err=True)
        raise typer.Exit(code=1)
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8")) if resolved.suffix.lower() == ".json" else yaml.safe_load(resolved.read_text(encoding="utf-8"))
        record = append_claim_classification(opened_workspace, ClaimClassificationInput.model_validate(raw))
        create_orchestration_work_order(opened_workspace, refresh_reason=f"Claim {record.claim_id} 分类已变更")
    except (OSError, ValueError, TypeError, yaml.YAMLError) as error:
        typer.echo(f"无法分类 Claim：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已记录 Claim 分类：{record.claim_id} → {record.kind.value}")


@zh_command("submit-outline")
def submit_outline(
    workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--from", help="宿主生成的课程蓝图 YAML 或 JSON 文件")],
) -> None:
    """受控接收主题化课程蓝图，不由 CLI 生成领域章节。"""
    opened_workspace = _open_workspace(workspace)
    action_work_order_id = _capture_orchestration_action_or_exit(opened_workspace, "submit-outline")
    try:
        output = submit_curriculum_blueprint(opened_workspace, input_path)
    except (BlueprintSubmissionError, CurriculumPrerequisiteError, FileNotFoundError, OSError) as error:
        fail_orchestration_action(opened_workspace, "submit-outline", str(error))
        typer.echo(f"无法提交课程蓝图：{error}", err=True)
        raise typer.Exit(code=1) from error
    complete_orchestration_action(
        opened_workspace,
        "submit-outline",
        proof="已提交主题化课程蓝图",
        output_paths=[output],
        work_order_id=action_work_order_id,
    )
    typer.echo(f"已提交课程蓝图：{output}；等待用户审批。")
    _echo_estimated_budget_warning(opened_workspace)


@zh_command("amend-outline")
def amend_outline(
    workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--from", help="修订后的课程蓝图 YAML 或 JSON 文件")],
    titles_only: Annotated[bool, typer.Option("--titles-only", help="只改正式标题，任何非标题字段变化均在写入前拒绝")] = False,
) -> None:
    """受控归档旧蓝图和章节合约后，写入待审批的新蓝图版本。"""
    try:
        output = amend_curriculum_blueprint(_open_workspace(workspace), input_path, titles_only=titles_only)
    except (BlueprintSubmissionError, CurriculumPrerequisiteError, FileNotFoundError, OSError) as error:
        typer.echo(f"无法修订课程蓝图：{error}", err=True)
        raise typer.Exit(code=1) from error
    opened_workspace = _open_workspace(workspace)
    supersede_orchestration_work_orders(opened_workspace, "已提交课程蓝图修订")
    create_orchestration_work_order(opened_workspace)
    typer.echo(f"已提交课程蓝图修订：{output}；等待用户审批后重新派生章节合约。")
    _echo_estimated_budget_warning(opened_workspace)


def _echo_estimated_budget_warning(workspace: Workspace) -> None:
    """Brief 无字数额门但已按页数目标估算章节预算时，提示宿主对照预算写作。"""
    brief_path = workspace.root / "planning" / "product-brief.md"
    blueprint_path = workspace.root / "planning" / "curriculum-blueprint.md"
    if not (_v_is_file(brief_path) and _v_is_file(blueprint_path)):
        return
    try:
        _, brief_body = parse_markdown_artifact(brief_path.read_text(encoding="utf-8"))
        _, blueprint_body = parse_markdown_artifact(blueprint_path.read_text(encoding="utf-8"))
        constraints = parse_detail_constraints(brief_body)
        if constraints is None or constraints.word_min is not None:
            return
        budgets = parse_chapter_budgets(blueprint_body)
    except (TypeError, ValueError, OSError):
        return
    total_min = sum(budget.word_min or 0 for budget in budgets.values())
    if total_min > 0:
        typer.echo(
            f"提示：Brief 未设置字数额门，已按页数目标估算章节预算（全书约 {total_min} 字下限）。"
            "写作时请对照章节合约的 word 预算，避免交付阶段页数不达标。",
        )


@zh_command("outline-migration-draft")
def outline_migration_draft(workspace: WorkspacePath) -> None:
    """从旧正文提取只读小节树候选，不修改或批准课程蓝图。"""
    try:
        output = create_section_outline_migration_draft(_open_workspace(workspace))
    except (CurriculumPrerequisiteError, FileExistsError, OSError, ValueError) as error:
        typer.echo(f"无法生成小节结构迁移候选：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"已生成只读小节结构候选：{output}。请审阅后填入 v2 蓝图输入，再运行 kp amend-outline；"
        "本候选不能直接提交或批准。"
    )


@zh_command()
def research(
    source_id: str,
    url: str,
    workspace: WorkspacePath,
    claim: ClaimId,
    excerpt: EvidenceExcerpt,
    license_note: LicenseNote,
) -> None:
    """追加映射到事实声明的来源台账条目。"""
    opened_workspace = _open_workspace(workspace)
    action_work_order_id = _capture_orchestration_action_or_exit(opened_workspace, "research")
    try:
        bindings = current_claim_contract_bindings(opened_workspace)
    except ValueError as error:
        typer.echo(f"无法登记来源台账：{error}", err=True)
        raise typer.Exit(code=1) from error
    normalized_claims = [claim_id.casefold() for claim_id in claim]
    unknown = sorted(set(normalized_claims) - set(bindings))
    if unknown:
        typer.echo(
            f"无法登记来源台账：Claim 未在当前章节合约中声明：{', '.join(unknown)}", err=True
        )
        raise typer.Exit(code=1)
    entry = SourceLedgerEntry(
        source_id=source_id,
        url=url,
        accessed_at=datetime.now(UTC).isoformat(),
        excerpt=excerpt,
        claim_ids=normalized_claims,
        claim_contract_sha256={claim_id: bindings[claim_id] for claim_id in normalized_claims},
        license_note=license_note,
    )
    ledger_path = opened_workspace.root / "research" / "source-ledger.jsonl"
    append_source_entry(ledger_path, entry)
    opened_workspace.append_event(
        "source_registered", {"source_id": source_id, "claim_ids": normalized_claims}
    )
    complete_orchestration_action(
        opened_workspace,
        "research",
        proof=f"已追加来源 {source_id}，绑定当前合约 Claim：{', '.join(normalized_claims)}",
        output_paths=[ledger_path],
        work_order_id=action_work_order_id,
    )
    typer.echo(f"已登记来源台账：{ledger_path}")


@zh_command()
def assumption(
    assumption_id: Annotated[str, typer.Argument(help="稳定假设标识，例如 interview-weight")],
    workspace: WorkspacePath,
    statement: Annotated[str, typer.Option(help="会影响产品决策的假设陈述")],
    impact: Annotated[
        AssumptionImpact,
        typer.Option(help="影响范围：audience/positioning/curriculum_weight/scope/conclusion"),
    ],
    disposition: Annotated[
        AssumptionDisposition,
        typer.Option(help="处理方式：evidence_needed/verified/reframed_as_case"),
    ],
    rationale: Annotated[str, typer.Option(help="记录该假设的理由或降级说明")],
    claim: Annotated[list[str] | None, typer.Option(help="verified 时关联的事实声明 ID")] = None,
) -> None:
    """追加产品假设的处理决定；同一标识以最后一条记录为当前状态。"""
    opened_workspace = _open_workspace(workspace)
    try:
        entry = AssumptionLedgerEntry(
            assumption_id=assumption_id,
            statement=statement,
            impact=impact,
            disposition=disposition,
            rationale=rationale,
            evidence_claim_ids=[claim_id.casefold() for claim_id in claim or []],
        )
        if disposition is AssumptionDisposition.VERIFIED:
            if not entry.evidence_claim_ids:
                raise ValueError("已验证假设必须关联至少一个事实声明 ID。")
            bindings = current_claim_contract_bindings(opened_workspace)
            unknown = sorted(set(entry.evidence_claim_ids) - set(bindings))
            if unknown:
                raise ValueError(f"Claim 未在当前章节合约中声明：{', '.join(unknown)}")
            findings = validate_claims(
                set(entry.evidence_claim_ids),
                opened_workspace.root / "research" / "source-ledger.jsonl",
                bindings,
            )
            if findings:
                raise ValueError(findings[0].message)
        update_snapshot = capture_orchestration_update(opened_workspace)
        ledger_path = opened_workspace.root / "research" / "assumption-ledger.jsonl"
        append_assumption_entry(ledger_path, entry)
        opened_workspace.append_event(
            "assumption_recorded",
            {"assumption_id": assumption_id, "disposition": disposition.value},
        )
        refresh_orchestration_after_controlled_update(
            opened_workspace, "assumption", snapshot=update_snapshot, output_paths=[ledger_path]
        )
    except (OSError, ValueError) as error:
        typer.echo(f"无法记录产品假设：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已记录产品假设：{assumption_id}")


@zh_command("compile-contracts")
def compile_contracts(
    workspace: WorkspacePath,
    all_chapters: Annotated[bool, typer.Option("--all", help="从当前批准蓝图派生全部章节合约")] = False,
) -> None:
    """从当前批准的结构化蓝图批量派生章节合约。"""
    if not all_chapters:
        typer.echo("请显式传入 --all；章节合约只能整批从当前蓝图派生。", err=True)
        raise typer.Exit(code=2)
    opened_workspace = _open_workspace(workspace)
    action_work_order_id = _capture_orchestration_action_or_exit(opened_workspace, "compile-contracts")
    try:
        outputs = compile_chapter_contracts(opened_workspace)
    except (CurriculumPrerequisiteError, FileExistsError, ValueError) as error:
        fail_orchestration_action(opened_workspace, "compile-contracts", str(error))
        typer.echo(f"无法派生章节合约：{error}", err=True)
        raise typer.Exit(code=1) from error
    complete_orchestration_action(
        opened_workspace,
        "compile-contracts",
        proof="已从当前批准蓝图派生全部章节合约",
        output_paths=outputs,
        work_order_id=action_work_order_id,
    )
    typer.echo(f"已派生 {len(outputs)} 份当前章节合约。")


@zh_command("length-recommendation-template")
def length_recommendation_template(workspace: WorkspacePath) -> None:
    """内部：生成课程架构 Agent 的教学篇幅评估模板和上下文。"""
    try:
        template, context = create_teaching_length_recommendation_template(_open_workspace(workspace))
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"无法生成教学篇幅评估模板：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已生成教学篇幅评估模板：{template}；上下文：{context}")


@zh_command("submit-length-recommendation")
def submit_length_recommendation(
    workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--from", help="课程架构 Agent 填写的篇幅评估 YAML")],
) -> None:
    """内部：冻结当前 Brief 对应的主题化教学篇幅建议。"""
    try:
        output = submit_teaching_length_recommendation(_open_workspace(workspace), input_path)
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"无法提交教学篇幅评估：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已提交教学篇幅评估：{output}")


@zh_command()
def contract(workspace: WorkspacePath) -> None:
    """已废弃：章节合约必须从整份已批准蓝图派生。"""
    del workspace
    typer.echo("kp contract 已废弃；请运行 kp compile-contracts --all --workspace <工作区>。", err=True)
    raise typer.Exit(code=2)


@zh_command()
def draft(chapter_number: int, workspace: WorkspacePath) -> None:
    """创建绑定当前章节合约的空白正文输入。"""
    opened_workspace = _open_workspace(workspace)
    action_work_order_id = _capture_orchestration_action_or_exit(opened_workspace, "draft", chapter_number=chapter_number)
    try:
        output = create_chapter_draft(opened_workspace, chapter_number)
    except (FileNotFoundError, FileExistsError, ValueError) as error:
        fail_orchestration_action(opened_workspace, "draft", str(error))
        typer.echo(f"无法创建章节草稿：{error}", err=True)
        raise typer.Exit(code=1) from error
    if action_work_order_id is not None:
        opened_workspace.append_event(
            "chapter_draft_prepared",
            {
                "chapter_number": chapter_number,
                "work_order_id": action_work_order_id,
                "draft_sha256": sha256(output.read_bytes()).hexdigest(),
            },
        )
    typer.echo(f"已创建章节草稿：{output}")


@zh_command("amend-chapter")
def amend_chapter(
    chapter_number: int,
    workspace: WorkspacePath,
    reason: Annotated[str, typer.Option(help="本次正文修订的简要原因")],
) -> None:
    """受控重开已提交章节，并保留旧版本与审批审计记录。"""
    opened_workspace = _open_workspace(workspace)
    try:
        output = amend_chapter_draft(opened_workspace, chapter_number, reason)
        supersede_orchestration_work_orders(opened_workspace, f"第 {chapter_number} 章已进入受控修订")
        create_orchestration_work_order(opened_workspace)
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"修订命令未完成：{error}；可能已重开并保留原归档，先核当前工件/工单终态，不重复重开或提前编辑。", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"修订命令已完成，可开始编辑正文版本：{output}")


@zh_command("humanize-check")
def humanize_check(
    chapter_number: int,
    workspace: WorkspacePath,
    source_path: Annotated[Path, typer.Option("--from", help="中文编辑后的章节 Markdown 文件")],
    before_path: Annotated[Path | None, typer.Option("--before", help="工作区内保留的编辑前稿；对比受保护技术内容")] = None,
) -> None:
    """运行中文终稿编辑规则并写入当前正文绑定的内部报告。"""
    opened = _open_workspace(workspace)
    _preflight_semantic_input(opened, source_path, "中文编辑源稿")
    if before_path is not None:
        _preflight_semantic_input(opened, before_path, "编辑前稿")
    try:
        snapshot = capture_orchestration_update(opened)
        output, report = record_humanize_check(opened, chapter_number, source_path, before_path=before_path)
        refresh_orchestration_after_controlled_update(opened, "humanize-check", snapshot=snapshot, output_paths=[output])
    except (HumanizeReportError, FileNotFoundError, ValueError) as error:
        typer.echo(f"无法完成中文终稿编辑校验：{error}", err=True)
        raise typer.Exit(code=1) from error
    blockers = sum(finding.severity is Severity.BLOCKER for finding in report.findings)
    warnings = sum(finding.severity is Severity.WARNING for finding in report.findings)
    typer.echo(f"中文终稿编辑报告：{output}；阻断项={blockers}；警告项={warnings}")
    if blockers:
        raise typer.Exit(code=1)


@zh_command("content-plan-schema")
def content_plan_schema_command() -> None:
    """输出章节内容计划的公开字段和跨字段约束。"""
    typer.echo(json.dumps(content_plan_schema(), ensure_ascii=False, indent=2))


@zh_command("content-plan-template")
def content_plan_template_command(chapter_number: int, workspace: WorkspacePath,
    as_json: Annotated[bool, typer.Option('--json', help='一次返回本章模板、上下文与真实提交Schema')] = False) -> None:
    """生成当前章节的内容块计划模板与只读小节上下文。"""
    try:
        template, context = create_content_plan_template(_open_workspace(workspace), chapter_number)
    except (ContentPlanError, OSError, ValueError) as error:
        typer.echo(f"无法生成章节内容计划模板：{error}", err=True)
        raise typer.Exit(code=1) from error
    if as_json:
        from knowledge_product_workflow.content_plan import content_plan_schema
        typer.echo(json.dumps({'template':str(template),'context':str(context),
            'submission_schema':content_plan_schema(),
            'context_data':json.loads(context.read_text(encoding='utf8'))},ensure_ascii=False))
    else:
        typer.echo(f"已生成章节内容计划模板：{template}；当前上下文：{context}；首次填写优先使用 --json 同时读取实际提交Schema。")


@zh_command("submit-content-plan")
def submit_content_plan_command(
    chapter_number: int,
    workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--from", help="章节内容计划 YAML 输入")],
    replace_draft: bool = False,
) -> None:
    """提交绑定当前章节合约的内容块计划，不接受内部哈希字段。"""
    opened_workspace = _open_workspace(workspace)
    try:
        request_path = _workspace_input_path(opened_workspace, input_path, label="章节内容计划输入")
        prepared = preflight_content_plan_input(opened_workspace, request_path)
        update_snapshot = capture_orchestration_update(opened_workspace)
        output = submit_content_plan(
            opened_workspace,
            chapter_number,
            request_path,
            replace_draft=replace_draft,
            prepared_input=prepared,
        )
        refresh_orchestration_after_controlled_update(opened_workspace, "submit-content-plan",
            snapshot=update_snapshot, output_paths=[output])
    except (ContentPlanError, OSError, ValueError) as error:
        typer.echo(f"无法提交章节内容计划：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已提交章节内容计划：{output}")


@zh_command("developmental-review-template")
def developmental_review_template_command(
    chapter_number: int,
    workspace: WorkspacePath,
    source_path: Annotated[Path, typer.Option("--from", help="当前章节源稿")],
) -> None:
    """输出当前结构审核单元、语义观察输入及预算绑定。"""
    try:
        template = create_structure_review_template(_open_workspace(workspace), chapter_number, source_path)
    except (OSError, ValueError) as error:
        typer.echo(f"无法生成结构审核模板：{error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(template, ensure_ascii=False, indent=2))


@zh_command("developmental-review-schema")
def developmental_review_schema_command() -> None:
    """输出当前结构观察及六项教学检查Schema，旧输入兼容读取。"""
    typer.echo(json.dumps(StructureReviewInput.model_json_schema(), ensure_ascii=False))


@zh_command("example-check-template")
def example_check_template_command(workspace: WorkspacePath,
    source_path: Annotated[Path, typer.Option("--from", help="当前工作区内源稿")]) -> None:
    """列出当前Python例子与实际代码，查看不执行。"""
    from knowledge_product_workflow.example_checks import example_template
    try:
        typer.echo(json.dumps(example_template(_open_workspace(workspace), source_path), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as error:
        typer.echo(f"无法读取例子：{error}", err=True)
        raise typer.Exit(1) from error


@zh_command("run-example-check")
def run_example_check_command(chapter_number: int, workspace: WorkspacePath,
    source_path: Annotated[Path, typer.Option("--from", help="已审阅且获授权的当前源稿")],
    example_index: Annotated[int | None, typer.Option("--example-index", min=1)] = None,
    resource_path: Annotated[str | None, typer.Option("--resource", help="当前计划声明的完整Python资源；不执行正文片段")] = None,
    approve_execution: Annotated[bool, typer.Option("--approve-execution", help="明确授权本段代码执行；不是安全沙箱")] = False) -> None:
    """在10秒上限子进程实际运行获授权例子，保留失败和原始输出。"""
    from knowledge_product_workflow.example_checks import run_python_example, run_python_resource
    try:
        if resource_path is not None and example_index is not None:
            raise ValueError("--resource与显式--example-index互斥")
        opened = _open_workspace(workspace)
        _illustration_authorization(opened, chapter_number, source_path)
        output = (run_python_resource(opened, source_path, resource_path, approved=approve_execution)
                  if resource_path is not None else
                  run_python_example(opened, source_path, example_index or 1, approved=approve_execution))
    except (OSError, ValueError) as error:
        typer.echo(f"无法运行例子：{error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(str(output))


@zh_command("adopt-reading-policy")
def adopt_reading_policy_command(
    workspace: WorkspacePath,
    confirm: Annotated[bool, typer.Option("--confirm", help="已获得用户明确采用新版阅读政策的决定")] = False,
) -> None:
    """预览或受控采用阅读结构政策2；保留旧正文与交付物。"""
    from knowledge_product_workflow.developmental import (
        adopt_reading_policy,
        reading_policy_preview,
    )
    try:
        opened = _open_workspace(workspace)
        result = adopt_reading_policy(opened) if confirm else reading_policy_preview(opened)
    except (OSError, ValueError) as error:
        typer.echo(f"无法采用阅读政策：{error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@zh_command("record-developmental-review")
def record_developmental_review_command(
    chapter_number: int,
    workspace: WorkspacePath,
    source_path: Annotated[Path, typer.Option("--chapter-source", help="当前章节源稿")],
    input_path: Annotated[Path, typer.Option("--from", help="填好的结构观察JSON")],
) -> None:
    """提交实际语义观察；不自动将检测指标或模板签为通过。"""
    opened = _open_workspace(workspace)
    _preflight_semantic_input(opened, source_path, "当前章节源稿")
    try:
        _path, payload = _preflight_semantic_input(opened, input_path, "结构观察", StructureReviewInput)
        update_snapshot = capture_orchestration_update(opened)
        output = record_structure_review(opened, chapter_number, source_path, payload)
        refresh_orchestration_after_controlled_update(opened, "record-developmental-review",
            snapshot=update_snapshot, output_paths=[output])
    except (OSError, ValueError) as error:
        typer.echo(f"无法提交结构观察：{error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"已提交结构观察：{output}")


@zh_command("developmental-check")
def developmental_check_command(
    chapter_number: int,
    workspace: WorkspacePath,
    source_path: Annotated[Path, typer.Option("--from", help="章节初稿 Markdown 文件")],
) -> None:
    """运行版本绑定的发展性编辑检查；它不直接改写正文。"""
    opened_workspace = _open_workspace(workspace)
    try:
        from knowledge_product_workflow.content_plan import _requires_content_plan
        if _requires_content_plan(opened_workspace):
            _preflight_semantic_input(opened_workspace, source_path, "发展性编辑源稿")
        update_snapshot = capture_orchestration_update(opened_workspace)
        output, report = record_developmental_check(
            opened_workspace, chapter_number, source_path
        )
    except (DevelopmentalEditError, FileNotFoundError, ValueError) as error:
        typer.echo(f"无法完成发展性编辑检查：{error}", err=True)
        raise typer.Exit(code=1) from error
    if output is None or report is None:
        typer.echo("当前旧版出版配置不要求发展性编辑报告。")
        return
    refresh_orchestration_after_controlled_update(opened_workspace, "developmental-check",
        snapshot=update_snapshot, output_paths=[output])
    blockers = sum(finding.severity.value == "blocker" for finding in report.findings)
    warnings = sum(finding.severity.value == "warning" for finding in report.findings)
    typer.echo(f"已写入发展性编辑报告：{output}；阻断项={blockers}；警告项={warnings}")
    if blockers:
        raise typer.Exit(code=1)


@zh_command("submit-diagram")
def submit_diagram_command(
    chapter_number: int,
    workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--from", help="kp-diagram-designer 填写的语义图表 JSON")],
    chapter_source: Annotated[Path, typer.Option("--chapter-source", help="当前工作区内已包含受控 PDF 引用的章节 Markdown 源稿")],
) -> None:
    """离线渲染语义图表输入为当前正文绑定的 SVG、PDF 和 PNG 资产。"""
    try:
        asset, manifest = submit_diagram(_open_workspace(workspace), chapter_number, chapter_source, input_path)
    except (DiagramSubmissionError, FileNotFoundError, ValueError) as error:
        typer.echo(f"无法提交图表：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已生成图表资产：{asset}；内部清单：{manifest.diagram_id}")


@zh_command("diagram-schema")
def diagram_schema_command() -> None:
    """输出图表语义输入的公开 Schema，不读取工作区。"""
    typer.echo(json.dumps(diagram_schema(), ensure_ascii=False, indent=2))


@zh_command("illustration-schema")
def illustration_schema_command() -> None:
    """查看插图准入、资产风格检查及正式风格角色。"""
    from knowledge_product_workflow.illustration_budget import ImageCall
    from knowledge_product_workflow.illustration_palette import DiagramPresentationInput
    from knowledge_product_workflow.illustrations import IllustrationAdmission
    typer.echo(json.dumps({"presentation": DiagramPresentationInput.model_json_schema(), "style_v2": style_resources(2), "admission": IllustrationAdmission.model_json_schema(),
                           "style_check": AssetStyleInput.model_json_schema(), "call_receipt": ImageCall.model_json_schema(),
                           "style": style_resources()}, ensure_ascii=False, indent=2))


@zh_command("freeze-illustration-budget")
def freeze_illustration_budget_command(
    workspace: WorkspacePath,
    approved_by: Annotated[str, typer.Option("--by", help="同次预算确认身份")],
    mode: str = "lean",
    chapter_slots: Annotated[int, typer.Option(help="仅政策1兼容；政策2来自批准图任务")] = 1,
    book_slots: Annotated[int, typer.Option(help="仅政策1兼容；政策2不设固定图数配额")] = 6,
    calls_per_slot: int = 2,
) -> None:
    """从批准图任务冻结需求预算；旧政策兼容，修改保留消费。"""
    try:
        budget = freeze_image_budget(_open_workspace(workspace), approved_by=approved_by,
                                    mode=mode, chapter_slots=chapter_slots, book_slots=book_slots,
                                    calls_per_slot=calls_per_slot)
    except (OSError, ValueError) as error:
        typer.echo(f"无法冻结生图预算：{error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(budget.model_dump_json(indent=2))


@zh_command("update-diagram-presentation")
def update_diagram_presentation_command(
    chapter_number: int, diagram_id: str, workspace: WorkspacePath,
    chapter_source: Annotated[Path, typer.Option()],
    input_path: Annotated[Path | None, typer.Option("--from")] = None,
) -> None:
    """冻结图级展示候选；不改正文或释放作者租约。"""
    from knowledge_product_workflow.diagrams import _require_workspace_file, _validate_diagram_id
    from knowledge_product_workflow.illustration_palette import DiagramPresentationInput
    from knowledge_product_workflow.illustration_updates import propose_presentation
    try:
        opened = _open_workspace(workspace)
        _validate_diagram_id(diagram_id)
        source = _require_workspace_file(opened, chapter_source, label="章节源稿")
        _illustration_authorization(opened, chapter_number, source)
        if input_path is None:
            from knowledge_product_workflow.diagrams import DiagramInput
            from knowledge_product_workflow.illustration_palette import load_category_colors
            current = DiagramInput.model_validate_json((opened.root/"runs/diagrams"/f"{diagram_id}.input.json").read_text(encoding="utf-8"))
            if current.presentation is None:
                raise ValueError("旧图升级须提供完整展示输入")
            values=current.presentation.model_dump()
            colors=load_category_colors(opened)
            for concept_key,color_key in (("concept_nodes","node_colors"),("concept_groups","group_colors")):
                for reference,concept in values[concept_key].items():
                    if concept in colors:
                        values[color_key][reference]=colors[concept]
            payload=DiagramPresentationInput.model_validate(values)
        else:
            payload = DiagramPresentationInput.model_validate_json(_require_workspace_file(opened,input_path,label="展示输入").read_text(encoding="utf-8"))
        path = propose_presentation(opened,chapter_number,diagram_id,payload)
        typer.echo(json.dumps({"diagram_input":str(path),"next_action":"illustration-preflight","chapter_number":chapter_number,"same_slot":True},ensure_ascii=False))
    except (OSError, ValueError) as error:
        typer.echo(str(error),err=True)
        raise typer.Exit(1) from error


@zh_command("activate-illustration-candidate")
def activate_illustration_candidate_command(
    chapter_number: int, diagram_id: str, call_id: str, workspace: WorkspacePath,
    chapter_source: Annotated[Path, typer.Option()],
    confirm_mixed_candidate: Annotated[bool, typer.Option(help="仅当前真实本人响应认可混合样品及原来源回执后使用")] = False,
    approved_by: Annotated[str | None, typer.Option("--by", help="当前本人认可身份；不由Agent代签")] = None,
    user_response: Annotated[str | None, typer.Option("--response", help="当前真实本人响应；仅落盘其SHA")] = None,
    review_input: Annotated[str | None, typer.Option(help="已展示当前候选的review_input；由illustration-style-template返回")] = None,
) -> None:
    """候选风格检查后原子切换；failed不能覆盖当前图。"""
    from knowledge_product_workflow.diagrams import _require_workspace_file, _validate_diagram_id
    from knowledge_product_workflow.illustration_updates import activate_candidate
    try:
        opened = _open_workspace(workspace)
        _validate_diagram_id(diagram_id)
        source = _require_workspace_file(opened,chapter_source,label="章节源稿")
        _illustration_authorization(opened,chapter_number,source)
        typer.echo(str(activate_candidate(opened,diagram_id,call_id,chapter_number=chapter_number,
                                          confirm_mixed_candidate=confirm_mixed_candidate,
                                          approved_by=approved_by, user_response=user_response,
                                          review_input=review_input)))
    except (OSError,ValueError) as error:
        typer.echo(str(error),err=True)
        raise typer.Exit(1) from error


@zh_command("illustration-budget-status")
def illustration_budget_status_command(workspace: WorkspacePath) -> None:
    """查看稳定图槽位、调用、费用来源；不将未知费用当零。"""
    try:
        typer.echo(load_image_budget(_open_workspace(workspace)).model_dump_json(indent=2))
    except (OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


@zh_command("illustration-preflight")
def illustration_preflight_command(
    chapter_number: int, workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--from")],
    chapter_source: Annotated[Path, typer.Option()],
    supports_reference: Annotated[bool, typer.Option("--supports-reference/--no-supports-reference")] = False,
    requested_model: str | None = None, actual_model: str | None = None,
    repair_reason: str | None = None,
    reference_capacity: int | None = None,
) -> None:
    """先准入/预约/落盘最小提示词；不调用任何模型。"""
    from knowledge_product_workflow.diagrams import _load_diagram_input, _require_workspace_file
    try:
        opened = _open_workspace(workspace)
        source = _require_workspace_file(opened, chapter_source, label="章节源稿")
        _illustration_authorization(opened, chapter_number, source)
        semantic = _load_diagram_input(opened, chapter_number,
                                       _require_workspace_file(opened, input_path, label="语义输入"))
        result = preflight_illustration(opened, chapter_number, semantic,
                                       supports_references=supports_reference,
                                       requested_model=requested_model, actual_model=actual_model,
                                       repair_reason=repair_reason, reference_capacity=reference_capacity)
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as error:
        typer.echo(f"插图准入失败：{error}", err=True)
        raise typer.Exit(1) from error


@zh_command("record-illustration-call")
def record_illustration_call_command(
    call_id: str, workspace: WorkspacePath,
    state: Annotated[str, typer.Option(help="dispatched/completed/failed/unknown/cancelled")],
    image: Path | None = None, actual_cost: str | None = None, usage_source: str = "unavailable",
    material_origin: Annotated[str | None, typer.Option(help="仅声明host-image/synthetic/unknown，不能证明实际AI来源")] = None,
    source_evidence: Annotated[Path | None, typer.Option(help="completed原件的工作区内原工具／会话回执；保留并绑定SHA")] = None,
) -> None:
    """发送前一次消费凭证；返回后记录实际图片及已知/未知费用。"""
    try:
        result = record_image_call(_open_workspace(workspace), call_id, state=state,
                                   image_path=image, actual_cost=Decimal(actual_cost) if actual_cost else None,
                                   usage_source=usage_source, material_origin=material_origin,
                                   source_evidence=source_evidence)
        typer.echo(result.model_dump_json(indent=2))
    except (OSError, ValueError) as error:
        typer.echo(f"调用记录失败：{error}", err=True)
        raise typer.Exit(1) from error


@zh_command("submit-illustration")
def submit_illustration_command(
    chapter_number: int, workspace: WorkspacePath,
    input_path: Annotated[Path, typer.Option("--from")],
    chapter_source: Annotated[Path, typer.Option()], image: Annotated[Path, typer.Option()],
    call_id: Annotated[str, typer.Option()],
) -> None:
    """受控提交宿主PNG并派生PDF/清单；不会额外生图或冒充矢量。"""
    try:
        opened = _open_workspace(workspace)
        from knowledge_product_workflow.diagrams import _require_workspace_file
        source = _require_workspace_file(opened, chapter_source, label="章节源稿")
        _illustration_authorization(opened, chapter_number, source)
        asset, manifest = submit_diagram(opened, chapter_number, source, input_path,
                                         host_image=image, image_call_id=call_id)
        typer.echo(f"已提交宿主位图资产：{asset}；renderer={manifest.renderer}；仍需资产风格检查")
    except (OSError, ValueError) as error:
        typer.echo(f"插图提交失败：{error}", err=True)
        raise typer.Exit(1) from error


@zh_command("record-illustration-style-check")
def record_illustration_style_command(
    workspace: WorkspacePath, input_path: Annotated[Path, typer.Option("--from")],
) -> None:
    """绑定当前资产逐项风格观察，失败/过期证明阻断章节及发布。"""
    from knowledge_product_workflow.diagrams import _require_workspace_file
    try:
        opened = _open_workspace(workspace)
        path = _require_workspace_file(opened, input_path, label="风格观察")
        output = record_asset_style(opened, AssetStyleInput.model_validate_json(path.read_text(encoding="utf-8")))
        typer.echo(f"已记录资产风格证明：{output}；此记录不代表用户成页批准")
    except (OSError, ValueError) as error:
        typer.echo(f"风格检查失败：{error}", err=True)
        raise typer.Exit(1) from error


@zh_command("illustration-style-template")
def illustration_style_template_command(diagram_id: str, workspace: WorkspacePath, candidate_call_id: str | None = None) -> None:
    """生成语义风格观察模板与只读预算指纹，不让宿主手算hash。"""
    from knowledge_product_workflow.diagrams import _validate_diagram_id
    from knowledge_product_workflow.illustrations import create_style_review_template
    try:
        _validate_diagram_id(diagram_id)
        typer.echo(json.dumps(create_style_review_template(_open_workspace(workspace), diagram_id,candidate_call_id), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


@zh_command("insert-diagram-reference")
def insert_diagram_reference_command(
    chapter_number: int,
    diagram_id: str,
    workspace: WorkspacePath,
    source_path: Annotated[Path, typer.Option("--from", help="当前工作区内的章节 Markdown 源稿")],
    diagram_input_path: Annotated[
        Path | None,
        typer.Option("--diagram-input", help="已填写的语义图表 JSON；用于写入最终图注"),
    ] = None,
    rebind_position: Annotated[bool, typer.Option("--rebind-position", help="正文改变后受控重解析语义位置，不重绘资产")] = False,
) -> None:
    """在当前章节源稿中幂等插入一个受控 D2 PDF 图表引用。"""
    try:
        output = insert_diagram_reference(
            _open_workspace(workspace), chapter_number, source_path, diagram_id, diagram_input_path,
            rebind_position=rebind_position,
        )
    except (DiagramSubmissionError, FileNotFoundError, ValueError) as error:
        typer.echo(f"无法插入图表引用：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已确认受控图表引用：{output}")


@zh_command("diagram-template")
def diagram_template_command(
    chapter_number: int,
    workspace: WorkspacePath,
    chapter_source: Annotated[Path, typer.Option("--chapter-source", help="当前工作区内的章节 Markdown 源稿")],
    diagram_id: Annotated[str, typer.Option("--diagram-id", help="小写图表 ID")],
    task_id: Annotated[str | None, typer.Option("--task-id", help="政策2批准的图任务，缺省与图表ID相同")] = None,
) -> None:
    """生成当前章节的语义图表填写模板与上下文，不暴露 SHA 或内部清单。"""
    try:
        template, context = create_diagram_template(
            _open_workspace(workspace), chapter_number, chapter_source, diagram_id, task_id
        )
    except (DiagramSubmissionError, FileNotFoundError, ValueError) as error:
        typer.echo(f"无法生成图表模板：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已生成图表语义模板：{template}；当前上下文：{context}")


@zh_command("chapter-compliance-schema")
def chapter_compliance_schema_command() -> None:
    """输出章节合规语义映射的公开 Schema，不读取工作区。"""
    typer.echo(json.dumps(chapter_compliance_schema(), ensure_ascii=False, indent=2))


@zh_command("check-chapter")
def check_chapter_command(
    chapter_number: int,
    workspace: WorkspacePath,
    source_path: Annotated[Path, typer.Option("--from", help="当前工作区内的章节 Markdown 源稿")],
    json_output: Annotated[bool, typer.Option("--json", help="输出机器可读诊断")] = False,
) -> None:
    """只读查看章节结构、两份终稿报告及合规映射的刷新顺序。"""
    try:
        report = check_chapter(_open_workspace(workspace), chapter_number, source_path)
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"无法检查章节：{error}", err=True)
        raise typer.Exit(code=1) from error
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    for name, item in report["checks"].items():
        typer.echo(f"{name}: {item['state']}")
    for action in report["refresh_actions"]:
        typer.echo(f"下一步：{action}")


@zh_command("check-chapters")
def check_chapters_command(workspace: WorkspacePath,
    chapters: Annotated[list[int], typer.Option("--chapter", min=1, help="可重复；只检查已保存canonical章节")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """单进程一致视图批量只读检查；exit0不等于全项current或最终QA通过。"""
    from knowledge_product_workflow.chapter_compliance import check_chapters
    try:
        result = check_chapters(_open_workspace(workspace), chapters)
    except (OSError, ValueError) as error:
        typer.echo(f"批量检查未完成：{error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2) if json_output else
               f"一致快照={result['coherent_snapshot']}；全部current={result['all_current']}；仍需最终QA。")


@zh_command("chapter-compliance-template")
def chapter_compliance_template_command(
    chapter_number: int,
    workspace: WorkspacePath,
    source_path: Annotated[Path, typer.Option("--from", help="当前工作区内的章节 Markdown 源稿")],
) -> None:
    """生成当前章节的语义合规映射模板与标题候选，不暴露 SHA 或内部证明。"""
    try:
        template, context = create_chapter_compliance_template(
            _open_workspace(workspace), chapter_number, source_path
        )
    except (ChapterSubmissionError, FileNotFoundError, ValueError) as error:
        typer.echo(f"无法生成章节合规模板：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已生成章节合规语义模板：{template}；当前标题候选：{context}")


@zh_command("render-review-preview")
def render_review_preview_command(workspace: WorkspacePath) -> None:
    """渲染全书诊断预览，不覆盖正式交付或创建发布清单。"""
    from knowledge_product_workflow.publishing import render_review_preview
    try:
        output = render_review_preview(_open_workspace(workspace))
    except (OSError, ValueError, RuntimeError) as error:
        typer.echo(f"诊断预览未完成：{error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"仅供诊断、未发布：{output}")


@zh_command("checkpoint-chapter-content")
def checkpoint_chapter_content_command(
    chapter_number: int,
    workspace: WorkspacePath,
    source_path: Annotated[Path, typer.Option("--from", help="当前正文源稿")],
    compliance_path: Annotated[Path, typer.Option("--compliance", help="当前合规映射")],
) -> None:
    """记录正文就绪与待办图；释放正文租约，不伪造章节完整提交。"""
    from knowledge_product_workflow.content_checkpoint import checkpoint_chapter_content
    opened = _open_workspace(workspace)
    _preflight_semantic_input(opened, source_path, "当前正文源稿")
    _preflight_semantic_input(opened, compliance_path, "当前合规映射", ChapterComplianceMappingInput)
    order = _capture_orchestration_action_or_exit(opened, "checkpoint-chapter-content", chapter_number=chapter_number)
    try:
        output = checkpoint_chapter_content(opened, chapter_number, source_path, compliance_path)
    except (OSError, ValueError) as error:
        fail_orchestration_action(opened, "checkpoint-chapter-content", str(error))
        typer.echo(f"无法建立正文检查点：{error}", err=True)
        raise typer.Exit(1) from error
    complete_orchestration_action(opened, "checkpoint-chapter-content", proof="chapter_content_checkpointed：正文就绪、资产待办，非完整提交",
                                  output_paths=[output], work_order_id=order)
    typer.echo(f"正文检查点已记录，待办图仍阻断正式交付：{output}")


@zh_command("submit-chapter")
def submit_chapter_command(
    chapter_number: int,
    workspace: WorkspacePath,
    source_path: Annotated[Path, typer.Option("--from", help="宿主生成的章节 Markdown 文件")],
    compliance_path: Annotated[Path, typer.Option("--compliance", help="kp-quality-editor 填写的语义章节合规映射 JSON")],
    operation_id: Annotated[str | None, typer.Option('--operation-id', help='可预先指定原操作ID；同ID禁止重复执行正文')] = None,
    operation_events: Annotated[bool, typer.Option('--operation-events', help='向stderr输出早期ID及阶段JSONL；原stdout保持兼容')] = False,
) -> None:
    """受控提交一章正文及其合规映射，不接受手工跳过体裁契约。"""
    opened_workspace = _open_workspace(workspace)
    _preflight_semantic_input(opened_workspace, source_path, "章节源稿")
    _preflight_semantic_input(opened_workspace, compliance_path, "章节合规映射", ChapterComplianceMappingInput)
    from knowledge_product_workflow.operation_receipts import (
        finish_operation,
        operation_scope,
        receipt_path,
    )
    if operation_id is not None and _v_exists(receipt_path(opened_workspace, operation_id)):
        typer.echo(f'已有原操作，请先 operation-status／recover-operation：{operation_id}；禁止重发正文', err=True)
        raise typer.Exit(1)
    action_work_order_id = _capture_orchestration_action_or_exit(opened_workspace, "submit-chapter", chapter_number=chapter_number)
    receipt = None
    try:
        with operation_scope(opened_workspace, action='submit-chapter', chapter_number=chapter_number,
            work_order_id=action_work_order_id, operation_id=operation_id,
            emit=(lambda record: typer.echo(json.dumps(record, ensure_ascii=False), err=True)) if operation_events else None) as receipt:
            chapter, compliance = submit_chapter(opened_workspace, chapter_number, source_path, compliance_path)
            finish_operation(opened_workspace, receipt.operation_id)
    except (ChapterSubmissionError, FileNotFoundError, ValueError) as error:
        if receipt is not None:
            # Domain status is determined by the persisted receipt, never by exit code alone.
            from knowledge_product_workflow.operation_receipts import operation_status
            committed = operation_status(opened_workspace, receipt.operation_id).get('transaction_id')
            if not committed:
                fail_orchestration_action(opened_workspace, "submit-chapter", str(error))
        typer.echo(f"无法提交章节正文：{error}", err=True)
        if receipt is not None:
            typer.echo(f'先查原操作，禁止重复提交：kp operation-status --operation-id {receipt.operation_id} --json', err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已提交章节正文：{chapter}；合规映射：{compliance}")


@zh_command("approve-sample")
def approve_sample(workspace: WorkspacePath, by: EditorIdentity = None) -> None:
    """使用已保存的审批身份批准第一章样章。"""
    opened_workspace = _open_workspace(workspace)
    identity = _approval_identity_for_action(opened_workspace, by)
    action_work_order_id = _capture_orchestration_action_or_exit(opened_workspace, "approve-sample")
    try:
        output = approve_sample_chapter(opened_workspace, identity)
    except (FileNotFoundError, ValueError) as error:
        fail_orchestration_action(opened_workspace, "approve-sample", str(error))
        typer.echo(f"无法确认样章：{error}", err=True)
        raise typer.Exit(code=1) from error
    complete_orchestration_action(
        opened_workspace,
        "approve-sample",
        proof="已确认样章写作方向",
        output_paths=[output],
        work_order_id=action_work_order_id,
    )
    typer.echo(f"已确认样章：{output}")


@zh_command()
def validate(workspace: WorkspacePath) -> None:
    """从当前章节合约自动收集事实声明并写入质量报告。"""
    opened_workspace = _open_workspace(workspace)
    try:
        action_work_order_id = _prepare_delivery_action(opened_workspace, "validate")
    except (WorkflowAuthorizationError, OrchestrationWorkOrderError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    output, report = run_validation(opened_workspace)
    complete_orchestration_action(
        opened_workspace,
        "validate",
        proof="已完成全书确定性质量校验",
        output_paths=[output],
        work_order_id=action_work_order_id,
    )
    typer.echo(f"已写入质量报告：{output}；可发布={report.publishable}")


@zh_command("review-check")
def review_check(workspace: WorkspacePath) -> None:
    """可选审稿诊断；其结果不会阻断交付。"""
    opened_workspace = _open_workspace(workspace)
    output = opened_workspace.root / "runs" / "review-report.json"
    report = QualityReport(findings=validate_review_batches(opened_workspace))
    output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    opened_workspace.append_event(
        "review_check_completed",
        {"finding_count": len(report.findings), "publishable": report.publishable},
    )
    typer.echo(f"已写入可选审稿报告：{output}；审稿结果不影响交付。")


@zh_command("submit-review-batch")
def submit_review_batch_command(
    workspace: WorkspacePath,
    all_chapters: Annotated[bool, typer.Option("--all", help="一次提交全部尚未通过的章节")] = False,
) -> None:
    """原子冻结全部待审章节并准备宿主 Skill 审稿任务。"""
    if not all_chapters:
        typer.echo("为避免逐章错位，送审必须使用 --all。", err=True)
        raise typer.Exit(code=2)
    try:
        batch = submit_review_batch(_open_workspace(workspace))
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        typer.echo(f"无法提交审稿批次：{error}", err=True)
        raise typer.Exit(code=1) from error
    if batch is None:
        typer.echo("所有当前章节都已有匹配的通过审稿结果；无需创建新批次。")
        return
    typer.echo(
        f"已创建审稿批次：{batch.batch_id}；章节数：{len(batch.tasks)}；当前状态：{_STAGE_LABELS.get(batch.state.value, batch.state.value)}"
    )
def _load_review_result_input(path: Path) -> ReviewResultInput:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"审稿结果输入必须是有效 JSON：{error}") from error
    if not isinstance(raw, dict):
        raise TypeError("审稿结果输入必须是 JSON 对象。")
    forbidden = {
        "batch_id",
        "chapter_number",
        "task_id",
        "parent_task_id",
        "task_input_manifest_sha256",
        "target",
        "review_schema_version",
    }
    leaked = sorted(forbidden & set(raw))
    if leaked:
        raise ValueError(f"审稿结果不得手填控制面字段：{', '.join(leaked)}")
    try:
        return ReviewResultInput.model_validate(raw)
    except ValidationError as error:
        raise ValueError(f"审稿结果字段无效：{error}") from error


@zh_command("submit-review-result")
def submit_review_result_command(
    workspace: WorkspacePath,
    batch: Annotated[str, typer.Option(help="审稿批次标识")],
    task_token: Annotated[str, typer.Option(help="宿主只读工单提供的任务令牌")],
    input_path: Annotated[Path, typer.Option("--input", help="审稿结果 JSON 文件")],
) -> None:
    """由宿主审稿 Skill 受控提交结果；作者会话不得代审。"""
    try:
        output = submit_review_result(
            _open_workspace(workspace), batch, task_token, _load_review_result_input(input_path)
        )
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        typer.echo(f"无法接收审稿结果：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已接收受控审稿结果：{output}")


@zh_command("checkpoint-chapter")
def checkpoint_chapter_command(
    chapter_number: int,
    workspace: WorkspacePath,
    author_id: AuthorIdentity,
    author_session_id: AuthorSessionIdentity,
) -> None:
    """历史诊断兼容命令；新书稿请使用 submit-review-batch --all。"""
    opened_workspace = _open_workspace(workspace)
    try:
        target = checkpoint_chapter(opened_workspace, chapter_number, author_id, author_session_id)
    except (FileNotFoundError, OSError, ValueError) as error:
        typer.echo(f"无法登记章节 checkpoint：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"已登记第 {chapter_number} 章 checkpoint：正文哈希={target.chapter_sha256}；"
        f"合约哈希={target.contract_sha256}；版本={target.chapter_version}"
    )


@zh_command("create-review-work-order")
def create_review_work_order_command(chapter_number: int, workspace: WorkspacePath) -> None:
    """历史诊断兼容命令；新书稿请使用 submit-review-batch --all。"""
    opened_workspace = _open_workspace(workspace)
    try:
        work_order = create_review_work_order(opened_workspace, chapter_number)
    except ReviewCheckpointRequiredError as error:
        typer.echo(
            f"无法创建审稿工单：{error}。请先运行 kp checkpoint-chapter {chapter_number}。",
            err=True,
        )
        raise typer.Exit(code=1) from error
    except (FileNotFoundError, OSError, ValueError) as error:
        typer.echo(f"无法创建审稿工单：{error}", err=True)
        raise typer.Exit(code=1) from error
    path = opened_workspace.root / "runs" / "work-orders" / f"{work_order.work_order_id}.json"
    typer.echo(f"已创建审稿工单：{path}；唯一输出：{work_order.output_path}")


@zh_command("reader-resource-preflight")
def reader_resource_preflight_command(workspace: WorkspacePath,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """只读资源前检，一次列全缺项；不核执行历史，不授予出版权限。同工作区仍须串行查询。"""
    from knowledge_product_workflow.publishing import resource_preflight_for_chapters
    try:
        result = resource_preflight_for_chapters(_open_workspace(workspace))
    except (OSError, ValueError) as error:
        typer.echo(f"资源前检未完成：{error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2) if json_output else
               f"资源前检ready={result['ready']}；问题={result['findings']}；不代表已批准或可发布。")
    if not result["ready"]:
        raise typer.Exit(1)


def _resource_preflight_or_exit(workspace):
    from knowledge_product_workflow.publishing import require_reader_resource_preflight
    try:
        require_reader_resource_preflight(workspace)
    except (OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


def _assembly_failure_hint(error, operation_id):
    winerror = getattr(error, "winerror", None)
    if winerror in {32, 33}:
        hint = "文件共享/锁冲突，先释放实际占用"
    elif isinstance(error, PermissionError) or winerror == 5:
        hint = "权限拒绝，核目标目录与文件权限"
    elif isinstance(error, FileNotFoundError):
        hint = "缺少输入文件；先运行reader-resource-preflight并补齐具体缺项"
    elif isinstance(error, ValueError):
        hint = "输入或契约无效；按原错误定位，不自动重试/结案"
    else:
        hint = f"保留原异常类别{type(error).__name__}，先核实际失败阶段"
    if operation_id:
        hint += f"；原操作={operation_id}，先operation-status/recover-operation核终态，禁止重发"
    else:
        hint += "；未创建本次操作回执，不臆造恢复ID"
    return f"汇编未完成：{error}；{hint}"


@zh_command()
def assemble(workspace: WorkspacePath,
    operation_id: Annotated[str | None, typer.Option("--operation-id", help="本次原操作标识；未知/未恢复时不重发")] = None,
) -> None:
    """将章节工件汇编为规范 Markdown。"""
    opened_workspace = _open_workspace(workspace)
    if operation_id is not None:
        from knowledge_product_workflow.operation_receipts import operation_status, receipt_path
        try:
            original_path = receipt_path(opened_workspace, operation_id)
            if original_path.is_file():
                original = operation_status(opened_workspace, operation_id)
                typer.echo(f"原操作={operation_id}，阶段={original.get('phase', 'unknown')}；"
                           "先operation-status/recover-operation核原终态，不重发汇编或创建新回执。", err=True)
                raise typer.Exit(1)
        except (OSError, ValueError) as error:
            typer.echo(f"无法安全核对原操作：{error}；停止，不重发。", err=True)
            raise typer.Exit(1) from error
    _resource_preflight_or_exit(opened_workspace)
    try:
        action_work_order_id = _prepare_delivery_action(opened_workspace, "assemble")
    except (WorkflowAuthorizationError, OrchestrationWorkOrderError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    from knowledge_product_workflow.operation_receipts import finish_operation, operation_scope
    current_operation = None
    try:
        with operation_scope(opened_workspace, action="assemble", chapter_number=0,
                work_order_id=action_work_order_id, operation_id=operation_id) as receipt:
            current_operation = receipt.operation_id
            output = assemble_book(opened_workspace)
            finish_operation(opened_workspace, receipt.operation_id)
    except (OSError, ValueError) as error:
        typer.echo(_assembly_failure_hint(error, current_operation or operation_id), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已汇编 Markdown：{output}")


@zh_command("render-pdf")
def render_pdf_command(workspace: WorkspacePath,
    compile_timeout: Annotated[int, typer.Option("--compile-timeout", min=1, max=600,
        help="本地编译预算秒数；不改变网络超时或自动重试")] = 300) -> None:
    """将汇编后的 Quarto 源稿渲染为 PDF 交付物。"""
    opened_workspace = _open_workspace(workspace)
    _resource_preflight_or_exit(opened_workspace)
    try:
        authorize_command(opened_workspace, "render-pdf")
    except WorkflowAuthorizationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    action_work_order_id = _capture_orchestration_action_or_exit(opened_workspace, "render-pdf")
    qmd_path = opened_workspace.root / "delivery" / "book.qmd"
    try:
        output = render_pdf(qmd_path, opened_workspace.root / "delivery", opened_workspace,
                            compile_timeout_seconds=compile_timeout)
    except (FileNotFoundError, RuntimeError) as error:
        fail_orchestration_action(opened_workspace, "render-pdf", str(error))
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    complete_orchestration_action(
        opened_workspace,
        "render-pdf",
        proof="已渲染受控 PDF 交付物",
        output_paths=[output],
        work_order_id=action_work_order_id,
    )
    typer.echo(f"已渲染 PDF：{output}")


@zh_command("repair-pdf-preview")
def repair_pdf_preview_command(workspace: WorkspacePath) -> None:
    """Rebuild the page-preview derivative of an already rendered current PDF."""
    opened_workspace = _open_workspace(workspace)
    try:
        authorize_command(opened_workspace, "repair-pdf-preview")
    except WorkflowAuthorizationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    action_work_order_id = _capture_orchestration_action_or_exit(
        opened_workspace, "repair-pdf-preview"
    )
    try:
        output, preview = render_pdf_preview_set(
            opened_workspace.root / "delivery" / "book.pdf",
            opened_workspace.root / "runs" / "pdf-preview",
        )
    except (OSError, RuntimeError, ValueError) as error:
        fail_orchestration_action(opened_workspace, "repair-pdf-preview", str(error))
        typer.echo(f"PDF 预览修复失败：{error}", err=True)
        raise typer.Exit(code=1) from error
    opened_workspace.append_event(
        "pdf_preview_repaired",
        {"page_count": len(preview.pages), "preview_manifest": str(output.relative_to(opened_workspace.root))},
    )
    complete_orchestration_action(
        opened_workspace,
        "repair-pdf-preview",
        proof="已修复当前 PDF 的页面预览",
        output_paths=[output],
        work_order_id=action_work_order_id,
    )
    typer.echo(f"已修复 PDF 页面预览：{output}")


@zh_command("validate-delivery")
def validate_delivery(workspace: WorkspacePath) -> None:
    """校验选定交付格式、页数目标和已批准版式。"""
    opened_workspace = _open_workspace(workspace)
    try:
        action_work_order_id = _prepare_delivery_action(opened_workspace, "validate-delivery")
    except (WorkflowAuthorizationError, OrchestrationWorkOrderError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    output, report = run_delivery_validation(opened_workspace)
    complete_orchestration_action(
        opened_workspace,
        "validate-delivery",
        proof="已完成交付格式、页数与版式校验",
        output_paths=[output],
        work_order_id=action_work_order_id,
    )
    typer.echo(f"已写入交付质量报告：{output}；可发布={report.publishable}")


@zh_command("delivery-preflight")
def delivery_preflight_command(
    workspace: WorkspacePath,
    as_json: Annotated[bool, typer.Option("--json", help="输出准备状态，不授予发布权限")] = False,
) -> None:
    """在请求当前PDF人审前检查可自动发现的交付问题。"""
    from knowledge_product_workflow.delivery_preflight import run_delivery_preflight
    opened = _open_workspace(workspace)
    try:
        authorize_command(opened, "delivery-preflight")
        order = _capture_orchestration_action_or_exit(opened, "delivery-preflight")
        report = run_delivery_preflight(opened)
        complete_orchestration_action(opened, "delivery-preflight", proof="已完成当前交付预检，未代签人审",
            output_paths=[opened.root / "runs/delivery-preflight.json"], work_order_id=order)
    except (ValueError, OSError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(report.model_dump_json(indent=2) if as_json else
        f"交付预检：可请求人审={report.ready_for_human_review}；问题={len(report.findings)}；待人工={len(report.pending_human_items)}")
    if not report.ready_for_human_review:
        raise typer.Exit(code=1)


@zh_command()
def publish(workspace: WorkspacePath) -> None:
    """为现有 Markdown/PDF 输出创建交付清单。"""
    opened_workspace = _open_workspace(workspace)
    try:
        action_work_order_id = _prepare_delivery_action(opened_workspace, "publish")
    except (WorkflowAuthorizationError, OrchestrationWorkOrderError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    output = create_delivery_manifest(
        opened_workspace,
        opened_workspace.root / "delivery" / "book.md",
        opened_workspace.root / "delivery" / "book.pdf",
    )
    complete_orchestration_action(
        opened_workspace,
        "publish",
        proof="已生成最终交付清单",
        output_paths=[output],
        work_order_id=action_work_order_id,
    )
    typer.echo(f"已创建交付清单：{output}")
