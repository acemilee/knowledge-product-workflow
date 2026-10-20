"""v1.0.2/v1.0.3 regressions for publication-only updates and controlled delivery."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from knowledge_product_workflow.chapter_compliance import submit_chapter
from knowledge_product_workflow.claim_quality import validate_undeclared_high_risk_claims
from knowledge_product_workflow.cli import app
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.humanize import record_humanize_check
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.presentation import (
    PublicationUpdateInput,
    apply_publication_update,
    load_or_create_publication_profile,
    preview_publication_update,
)
from knowledge_product_workflow.publishing import (
    assemble_book,
    create_delivery_manifest,
    create_quarto_source,
)
from knowledge_product_workflow.quality import Severity
from knowledge_product_workflow.quality_exceptions import (
    ControlledExceptionRequest,
    apply_controlled_exceptions,
    validate_controlled_exceptions,
)
from knowledge_product_workflow.source_ledger import SourceLedgerEntry, append_source_entry
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.authoring import (
    compile_chapter_contracts,
    create_chapter_draft,
    require_current_chapter_contract,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workflows.validation import run_delivery_validation, run_validation
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint, submit_test_chapter


def _approve(path: Path) -> None:
    metadata, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    path.write_text(
        render_markdown_artifact(transition(metadata, ArtifactStatus.APPROVED, approved_by="用户"), body),
        encoding="utf-8",
    )


def _assembled_workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path, "guide")
    brief = create_product_brief(
        workspace,
        "AI Agent 工程实践",
        DetailConstraints(target_words=None, delivery_format="markdown"),
        target_audience="希望建立稳定 Agent 工作流的工程师",
    )
    _approve(brief)
    blueprint = submit_test_blueprint(workspace, brief, titles=("工程基础",))
    _approve(blueprint)
    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    submit_test_chapter(workspace, tmp_path, 1)
    (workspace.root / "chapters" / "chapter-01-sample.approved").write_text(
        "approved_by: 用户\n", encoding="utf-8"
    )
    assemble_book(workspace)
    return workspace


@pytest.mark.parametrize("filename", ["knowledge-book-components.lua", "knowledge-book-style.tex"])
def test_stale_rendering_resources_can_be_repaired_without_metadata_or_text_changes(tmp_path: Path, filename: str) -> None:
    workspace = _assembled_workspace(tmp_path)
    markdown = workspace.root / "delivery/book.md"
    create_quarto_source(workspace, markdown)
    current = load_or_create_publication_profile(workspace, language="zh-CN")
    update = PublicationUpdateInput(body_font_size=current.body_font_size, version_bump="none")
    with pytest.raises(ValueError, match="没有产生任何可见变化"):
        preview_publication_update(workspace, update, language="zh-CN")
    component = workspace.root / "delivery" / filename
    canonical = component.read_bytes()
    bodies = {p.name: p.read_bytes() for p in (workspace.root / "chapters").glob("*.md")}
    component.write_text("-- stale rendering resources\n", encoding="utf-8")
    _, candidate = preview_publication_update(workspace, update, language="zh-CN")
    assert candidate.changed_fields == ["rendering_resources"]
    applied = apply_publication_update(workspace, "用户")
    assert applied.body_font_size == current.body_font_size
    assert applied.edition_label == current.edition_label
    create_quarto_source(workspace, markdown)
    assert component.read_bytes() == canonical
    assert bodies == {p.name: p.read_bytes() for p in (workspace.root / "chapters").glob("*.md")}
    with pytest.raises(ValueError, match="没有产生任何可见变化"):
        preview_publication_update(workspace, update, language="zh-CN")


def test_publication_update_separates_cover_footer_and_semver(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    from knowledge_product_workflow.workflows.product_brief import create_product_brief
    create_product_brief(workspace, "出版字段更新")
    profile = load_or_create_publication_profile(workspace, language="zh-CN", today=date(2020, 1, 1))
    assert profile.cover_label == "知识产品工作流"
    assert profile.footer_label == "知识产品工作流"
    assert profile.edition_label == "v1.0.0"

    preview, candidate = preview_publication_update(
        workspace,
        PublicationUpdateInput(footer_label="客户内部资料", version_bump="patch"),
        language="zh-CN",
    )
    assert preview.is_file()
    assert candidate.profile.cover_label == "知识产品工作流"
    assert candidate.profile.footer_label == "客户内部资料"
    assert candidate.profile.edition_label == "v1.0.1"
    assert candidate.profile.publication_date != profile.publication_date

    updated = apply_publication_update(workspace, "用户")
    assert updated.revision == 2
    assert updated.edition_label == "v1.0.1"
    assert (workspace.root / "planning" / "history" / "publication-profile.r1.yaml").is_file()


def test_publication_update_can_change_a_supported_body_font_size(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    from knowledge_product_workflow.workflows.product_brief import create_product_brief
    create_product_brief(workspace, "出版字段更新")
    profile = load_or_create_publication_profile(workspace, language="zh-CN", today=date(2026, 8, 3))
    assert profile.body_font_size == 12

    _, candidate = preview_publication_update(
        workspace,
        PublicationUpdateInput(body_font_size=14, version_bump="none"),
        language="zh-CN",
    )

    assert candidate.profile.body_font_size == 14
    assert candidate.profile.edition_label == profile.edition_label
    assert candidate.changed_fields == ["body_font_size"]


def test_publication_update_rejects_a_non_semver_edition_label() -> None:
    with pytest.raises(ValueError, match="vX.Y.Z"):
        PublicationUpdateInput(edition_label="第一版", version_bump="none")


def test_publication_update_cli_previews_then_applies_once(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(
        workspace,
        "Agent 教程",
        DetailConstraints(target_words=None),
        target_audience="希望上手 Agent 的工程师",
    )
    request = workspace.root / "runs" / "publication-update.yaml"
    request.write_text("cover_label: 客户品牌\nversion_bump: minor\n", encoding="utf-8")
    runner = CliRunner()

    preview = runner.invoke(
        app,
        ["update-publication", "--workspace", str(workspace.root), "--from", str(request)],
    )
    assert preview.exit_code == 0, preview.output
    assert "出版信息预览" in preview.output

    confirmed = runner.invoke(
        app,
        [
            "update-publication",
            "--workspace",
            str(workspace.root),
            "--from",
            str(request),
            "--confirm",
        ],
    )
    assert confirmed.exit_code == 0, confirmed.output
    assert "尚未汇编书稿" in confirmed.output
    profile = yaml.safe_load((workspace.root / "planning" / "publication-profile.yaml").read_text(encoding="utf-8"))
    assert profile["cover_label"] == "客户品牌"
    assert profile["edition_label"] == "v1.1.0"


def test_reassembly_archives_old_delivery_derivatives_when_profile_changes(tmp_path: Path) -> None:
    workspace = _assembled_workspace(tmp_path)
    delivery = workspace.root / "delivery"
    delivery.joinpath("book.pdf").write_bytes(b"%PDF-old")
    delivery.joinpath("book.tex").write_text("old tex", encoding="utf-8")
    delivery.joinpath("delivery-manifest.yaml").write_text("schema_version: 2\n", encoding="utf-8")

    preview_publication_update(
        workspace,
        PublicationUpdateInput(cover_label="新版封面", version_bump="minor"),
        language="zh-CN",
    )
    apply_publication_update(workspace, "用户")
    assemble_book(workspace)

    history = list((workspace.root / "runs" / "delivery-history").glob("*"))
    assert len(history) == 1
    archived = {path.name for path in history[0].iterdir()}
    assert {"book.pdf", "book.tex", "delivery-manifest.yaml", "book-input-manifest.json"} <= archived
    assert not delivery.joinpath("book.pdf").exists()
    current_inputs = json.loads(delivery.joinpath("book-input-manifest.json").read_text(encoding="utf-8"))
    assert "planning/publication-profile.yaml" in {item["path"] for item in current_inputs["inputs"]}


def test_reassembly_migrates_legacy_callouts_without_rewriting_the_chapter(tmp_path: Path) -> None:
    workspace = _assembled_workspace(tmp_path)
    chapter = workspace.root / "chapters" / "chapter-01.md"
    metadata, body = parse_markdown_artifact(chapter.read_text(encoding="utf-8"))
    legacy_body = body + "\n\n{.kp-note}\n旧工作区的提示框内容。\n"
    chapter.write_text(render_markdown_artifact(metadata, legacy_body), encoding="utf-8")

    book = assemble_book(workspace)
    qmd = workspace.root / "delivery" / "book.qmd"

    assert chapter.read_text(encoding="utf-8").endswith("{.kp-note}\n旧工作区的提示框内容。\n")
    assert ".kp-" not in book.read_text(encoding="utf-8")
    assert ":::" not in book.read_text(encoding="utf-8")
    assert "::: {.kp-note}" in qmd.read_text(encoding="utf-8")


def test_confirmed_publication_update_automatically_rebuilds_a_markdown_delivery(tmp_path: Path) -> None:
    workspace = _assembled_workspace(tmp_path)
    request = workspace.root / "runs" / "publication-update.yaml"
    request.write_text("footer_label: 仅供客户内部使用\nversion_bump: patch\n", encoding="utf-8")
    runner = CliRunner()

    assert runner.invoke(
        app,
        ["update-publication", "--workspace", str(workspace.root), "--from", str(request)],
    ).exit_code == 0
    confirmed = runner.invoke(
        app,
        [
            "update-publication",
            "--workspace",
            str(workspace.root),
            "--from",
            str(request),
            "--confirm",
        ],
    )

    assert confirmed.exit_code == 0, confirmed.output
    assert "自动重建派生交付物与交付清单" in confirmed.output
    assert (workspace.root / "delivery" / "delivery-manifest.yaml").is_file()
    cover = (workspace.root / "delivery" / "knowledge-book-cover.tex").read_text(encoding="utf-8")
    assert "\\providecommand{\\KnowledgeProductFooterLabel}{仅供客户内部使用}" in cover


def _high_risk_workspace(tmp_path: Path) -> tuple[Workspace, Path, str]:
    workspace = Workspace.create(tmp_path, "claim-guide")
    brief = create_product_brief(
        workspace,
        "面试教程",
        DetailConstraints(delivery_format="markdown"),
        target_audience="工程师",
    )
    _approve(brief)
    blueprint = submit_test_blueprint(
        workspace,
        brief,
        titles=("岗位判断",),
        claim_ids=(("claim-001",),),
        section_outlines=(
            [
                {"number": "1.1", "title": "决策框架", "children": []},
                {"number": "1.2", "title": "额外结论", "children": []},
            ],
        ),
    )
    _approve(blueprint)
    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    source = workspace.root / "runs" / "chapter-01-exception-source.md"
    source.write_text(
        "# 1. 岗位判断\n\n"
        "## 1.1 决策框架\n\n"
        "先区分事实、判断与待验证假设，再决定下一步行动。面对岗位信息时，"
        "先把可核验的职责、技术栈和协作方式逐项列出，再记录自己尚未掌握的部分。"
        "这样既能避免把猜测当结论，也能让后续准备围绕真实差距展开。\n\n"
        "## 1.2 额外结论\n\n"
        "大多数公司会将系统设计列为典型面试重点。\n",
        encoding="utf-8",
    )
    semantic_mapping = {
        "learning_objective_anchors": {"建立可执行的判断框架": "1.1 决策框架"},
        "unit_anchors": {"decision_framework": "1.1 决策框架"},
        "claim_locations": {"claim-001": "1.1 决策框架"},
    }
    mapping = workspace.root / "runs" / "chapter-01-exception-compliance.json"
    mapping.write_text(json.dumps(semantic_mapping, ensure_ascii=False), encoding="utf-8")
    record_humanize_check(workspace, 1, source)
    chapter_path, _ = submit_chapter(workspace, 1, source, mapping)
    contract_path, _ = require_current_chapter_contract(workspace, 1)
    append_source_entry(
        workspace.root / "research" / "source-ledger.jsonl",
        SourceLedgerEntry(
            source_id="test-source",
            url="https://example.com/source",
            accessed_at="2026-08-02T00:00:00Z",
            excerpt="测试用的可追溯依据。",
            claim_ids=["claim-001"],
            claim_contract_sha256={"claim-001": hashlib.sha256(contract_path.read_bytes()).hexdigest()},
            license_note="test fixture",
        ),
    )
    _, body = parse_markdown_artifact(chapter_path.read_text(encoding="utf-8"))
    finding = validate_undeclared_high_risk_claims(workspace, chapter_path, body)[0]
    return workspace, chapter_path, finding.location


def test_controlled_exception_only_consumes_one_current_exact_finding(tmp_path: Path) -> None:
    workspace, chapter_path, location = _high_risk_workspace(tmp_path)
    request = ControlledExceptionRequest(
        rule_id="UNDECLARED_HIGH_RISK_CLAIM",
        location=location,
        claim_id="claim-001",
        evidence_paths=["research/source-ledger.jsonl"],
        rationale="该句位于补充标题下，但同章 Claim 与来源均已完成当前版本绑定。",
    )
    payload = workspace.root / "runs" / "controlled-exceptions-input.json"
    payload.write_text(
        json.dumps({"exceptions": [request.model_dump()]}, ensure_ascii=False), encoding="utf-8"
    )
    submitted = CliRunner().invoke(
        app,
        ["submit-controlled-exceptions", "--workspace", str(workspace.root), "--from", str(payload)],
    )
    assert submitted.exit_code == 0, submitted.output
    _, body = parse_markdown_artifact(chapter_path.read_text(encoding="utf-8"))
    transformed = apply_controlled_exceptions(
        workspace, validate_undeclared_high_risk_claims(workspace, chapter_path, body)
    )
    assert [(item.code, item.severity) for item in transformed] == [
        ("CONTROLLED_EXCEPTION_APPLIED", Severity.INFO)
    ]

    chapter_path.write_text(chapter_path.read_text(encoding="utf-8") + "\n新增段落。\n", encoding="utf-8")
    assert [item.code for item in validate_controlled_exceptions(workspace)] == [
        "CONTROLLED_EXCEPTION_INVALID"
    ]


def test_valid_controlled_exception_is_disclosed_in_the_delivery_manifest(tmp_path: Path) -> None:
    workspace, _chapter_path, location = _high_risk_workspace(tmp_path)
    request = ControlledExceptionRequest(
        rule_id="UNDECLARED_HIGH_RISK_CLAIM",
        location=location,
        claim_id="claim-001",
        evidence_paths=["research/source-ledger.jsonl"],
        rationale="同章已经存在当前版本 Claim、来源与合规映射，检查器只误判了补充标题。",
    )
    payload = workspace.root / "runs" / "controlled-exceptions-input.json"
    payload.write_text(
        json.dumps({"exceptions": [request.model_dump()]}, ensure_ascii=False), encoding="utf-8"
    )
    assert CliRunner().invoke(
        app,
        ["submit-controlled-exceptions", "--workspace", str(workspace.root), "--from", str(payload)],
    ).exit_code == 0
    (workspace.root / "chapters" / "chapter-01-sample.approved").write_text(
        "approved_by: 用户\n", encoding="utf-8"
    )

    _, report = run_validation(workspace)
    assert report.publishable is True
    assert [finding.code for finding in report.findings] == ["CONTROLLED_EXCEPTION_APPLIED"]
    markdown = assemble_book(workspace)
    _, delivery_report = run_delivery_validation(workspace)
    assert delivery_report.publishable
    manifest = yaml.safe_load(create_delivery_manifest(workspace, markdown, None).read_text(encoding="utf-8"))
    assert len(manifest["controlled_exceptions"]) == 1
    exception = manifest["controlled_exceptions"][0]
    assert exception["rule_id"] == "UNDECLARED_HIGH_RISK_CLAIM"
    assert exception["location"] == location
    assert exception["claim_id"] == "claim-001"
    assert exception["evidence_paths"] == ["research/source-ledger.jsonl"]
    assert exception["approved_by"] == "用户"
    assert exception["chapter_body_sha256"]
    assert exception["contract_sha256"]
    assert exception["approved_at"]
