"""前版引导缺陷的实际操作、只读复用和权限负例；夹具不代表生产验收。"""
import json
import os
from pathlib import Path

import pytest

from knowledge_product_workflow.claims import current_claim_contract_bindings
from knowledge_product_workflow.runtime import inspect_workflow
from knowledge_product_workflow.source_ledger import (
    SourceLedgerEntry,
    append_source_entry,
    validate_claims,
)
from tests.test_research_orchestration_o10 import (
    _assert_completion,
    _cli,
    _current_order,
    _prepared_workspace,
)
from tests.visual_support import bind_fixture_observations


def test_one_source_registers_two_current_claims_in_one_action(tmp_path):
    workspace = _prepared_workspace(tmp_path)
    assert inspect_workflow(workspace).pending_claim_ids == ["claim-one", "claim-two"]
    old = _current_order(workspace)
    assert any("--claim" in item and "分组" in item for item in old.preconditions)
    result = _cli(workspace, "research", "joint", "https://example.com/joint", "--claim", "claim-one",
                  "--claim", "claim-two", "--excerpt", "夹具两项声明的共同支持摘录。", "--license-note", "测试许可")
    assert result.returncode == 0, result.stderr
    ledger = workspace.root / "research/source-ledger.jsonl"
    rows = ledger.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    entry = SourceLedgerEntry.model_validate_json(rows[0])
    assert entry.claim_ids == ["claim-one", "claim-two"]
    assert entry.claim_contract_sha256 == current_claim_contract_bindings(workspace)
    assert len(set(entry.claim_contract_sha256.values())) == 2
    _assert_completion(workspace, old, "research", ledger)
    events = [json.loads(line) for line in (workspace.root/"runs/events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(item["event_type"] == "source_registered" for item in events) == 1
    assert inspect_workflow(workspace).pending_claim_ids == []


def test_research_twenty_claims_use_four_evidence_actions_and_keep_existing_rows(tmp_path):
    claims = tuple(f"claim-{number:02}" for number in range(1, 21))
    workspace = _prepared_workspace(tmp_path, claim_ids=(claims[:10], claims[10:]))
    ledger = workspace.root / "research/source-ledger.jsonl"
    for group in range(4):
        old = _current_order(workspace)
        assert inspect_workflow(workspace).pending_claim_ids == list(claims[group*5:])
        prior = ledger.read_bytes() if ledger.exists() else b""
        args = ["research", f"evidence-{group}", f"https://example.com/fixture/{group}"]
        for claim in claims[group*5:(group+1)*5]:
            args += ["--claim", claim]
        args += ["--excerpt", "已核验的夹具证据组："+", ".join(claims[group*5:(group+1)*5]), "--license-note", "组内同一测试许可"]
        result = _cli(workspace, *args)
        assert result.returncode == 0, result.stderr
        assert ledger.read_bytes().startswith(prior)
        _assert_completion(workspace, old, "research", ledger)
    rows = [SourceLedgerEntry.model_validate_json(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4 and sum(len(item.claim_ids) for item in rows) == 20
    assert validate_claims(set(claims), ledger, current_claim_contract_bindings(workspace)) == []
    assert inspect_workflow(workspace).pending_claim_ids == []
    assert inspect_workflow(workspace).stage == "research_impact_decision_pending"


@pytest.mark.parametrize("kind", ["scenario", "author_recommendation"])
def test_research_status_lists_only_uncovered_or_stale_claims(tmp_path, kind):
    from knowledge_product_workflow.claim_classification import (
        ClaimClassificationInput,
        append_claim_classification,
    )
    from knowledge_product_workflow.workflows.validation import _collect_validation_findings

    workspace = _prepared_workspace(tmp_path)
    ledger = workspace.root / "research/source-ledger.jsonl"
    append_source_entry(ledger, SourceLedgerEntry(source_id="historical", url="https://example.com/history",
        accessed_at="2026-10-14", excerpt="旧合约夹具证据", claim_ids=["claim-one", "claim-two"],
        claim_contract_sha256={"claim-one": "a"*64, "claim-two": "a"*64}, license_note="测试许可"))
    before = ledger.read_bytes()
    append_claim_classification(workspace, ClaimClassificationInput(claim_id="claim-two", kind=kind,
        statement="可以用此情境练习比较方案。", heading_anchor="2.1 判断", reason="教学构造"))
    status = inspect_workflow(workspace)
    assert status.stage == "research_required" and status.pending_claim_ids == ["claim-one"]
    findings = _collect_validation_findings(workspace)
    source = [item.location for item in findings if item.code in {"MISSING_CLAIM_SOURCE", "CLAIM_SOURCE_CONTRACT_MISMATCH"}]
    assert source == ["claim-one"]
    assert ledger.read_bytes() == before


def test_claim_findings_are_limited_to_requested_set(tmp_path):
    ledger = tmp_path / "sources.jsonl"
    append_source_entry(ledger, SourceLedgerEntry(source_id="old", url="https://example.com/old",
        accessed_at="2026-10-14", excerpt="旧版本", claim_ids=["claim-one", "claim-two"],
        claim_contract_sha256={"claim-one":"a"*64,"claim-two":"a"*64}, license_note="测试许可"))
    bindings = {"claim-one":"b"*64,"claim-two":"b"*64}
    assert [item.location for item in validate_claims({"claim-one"}, ledger, bindings)] == ["claim-one"]
    assert validate_claims(set(), ledger, bindings) == []
    ledger.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ValueError):
        validate_claims(set(), ledger, bindings)


@pytest.mark.parametrize("reason", ["verified_assumption", "stale_classification"])
def test_research_nonfactual_old_binding_still_blocks_when_evidence_is_required(tmp_path, reason):
    from knowledge_product_workflow.assumption_ledger import (
        AssumptionLedgerEntry,
        append_assumption_entry,
    )
    from knowledge_product_workflow.claim_classification import (
        ClaimClassificationInput,
        append_claim_classification,
    )
    from knowledge_product_workflow.workflows.validation import _collect_validation_findings

    workspace = _prepared_workspace(tmp_path)
    append_claim_classification(workspace, ClaimClassificationInput(claim_id="claim-two", kind="scenario",
        statement="课堂使用此情境练习。", heading_anchor="2.1 判断", reason="教学设定"))
    append_source_entry(workspace.root/"research/source-ledger.jsonl", SourceLedgerEntry(source_id="old",
        url="https://example.com/old", accessed_at="2026-10-14", excerpt="旧证据",
        claim_ids=["claim-one","claim-two"], claim_contract_sha256={"claim-one":"a"*64,"claim-two":"a"*64}, license_note="测试许可"))
    if reason == "verified_assumption":
        append_assumption_entry(workspace.root/"research/assumption-ledger.jsonl", AssumptionLedgerEntry(
            assumption_id="previously-verified", statement="之前已验证的夹具前提", impact="scope",
            disposition="verified", rationale="历史夹具", evidence_claim_ids=["claim-two"]))
    else:
        contract = workspace.root/"chapters/chapter-02-contract.md"
        contract.write_text(contract.read_text(encoding="utf-8")+"\n当前合约修订。\n", encoding="utf-8")
    assert inspect_workflow(workspace).pending_claim_ids == ["claim-one", "claim-two"]
    source = [item.location for item in _collect_validation_findings(workspace) if item.code == "CLAIM_SOURCE_CONTRACT_MISMATCH"]
    assert source == ["claim-one", "claim-two"]


def test_research_rejects_mixed_unknown_claim_before_any_write(tmp_path):
    workspace = _prepared_workspace(tmp_path)
    before = {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    result = _cli(workspace, "research", "mixed", "https://example.com/mixed", "--claim", "claim-one",
                  "--claim", "claim-unknown", "--excerpt", "无未知声明授权", "--license-note", "测试许可")
    assert result.returncode == 1
    assert before == {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    assert inspect_workflow(workspace).pending_claim_ids == ["claim-one", "claim-two"]


def test_chapter_handoff_surfaces_readonly_diagnostic_before_submission(tmp_path):
    from knowledge_product_workflow.chapter_compliance import check_chapter
    from knowledge_product_workflow.runtime import (
        create_orchestration_work_order,
        read_orchestration_work_order,
    )
    from tests.test_structure_review_v133 import _fixture

    workspace, source, _ = _fixture(tmp_path)
    path = create_orchestration_work_order(workspace, adopt_legacy=True)
    order = read_orchestration_work_order(workspace, path)
    status = inspect_workflow(workspace)
    assert status.readonly_diagnostics[0].command == "check-chapter"
    assert "--json" in status.readonly_diagnostics[0].argv
    assert "check-chapter" not in order.allowed_commands
    assert any("readonly_diagnostics" in item and "源稿" in item for item in order.preconditions)
    before = {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    check_chapter(workspace, 1, source)
    assert before == {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}


def test_chapter_diagnostic_reuses_current_proofs_and_only_refreshes_invalid_one(tmp_path):
    from knowledge_product_workflow.chapter_compliance import check_chapter
    from knowledge_product_workflow.developmental import record_developmental_check
    from knowledge_product_workflow.humanize import record_humanize_check
    from tests.test_structure_review_v133 import _fixture, simulated_review

    workspace, source, _ = _fixture(tmp_path)
    simulated_review(workspace, source)
    developmental, _ = record_developmental_check(workspace, 1, source)
    humanize, _ = record_humanize_check(workspace, 1, source)
    before = {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    report = check_chapter(workspace, 1, source)
    assert all(report["checks"][name]["state"] == "current" for name in ["developmental_report", "humanize_report"])
    assert before == {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    broken = json.loads(humanize.read_text(encoding="utf-8"))
    broken["contract_sha256"] = "a"*64
    humanize.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    after = check_chapter(workspace, 1, source)
    assert after["checks"]["developmental_report"]["state"] == "current"
    assert after["checks"]["humanize_report"]["state"] != "current"
    assert any("humanize-check" in action for action in after["refresh_actions"])
    assert not any("developmental-check" in action for action in after["refresh_actions"])
    assert developmental.read_bytes() == before[developmental]


def test_chapter_diagnostic_refuses_missing_and_outside_source_without_writes(tmp_path):
    from knowledge_product_workflow.chapter_compliance import check_chapter
    from tests.test_structure_review_v133 import _fixture

    workspace, _, _ = _fixture(tmp_path)
    outside = tmp_path/"outside.md"
    outside.write_text("其他夹具工作区。", encoding="utf-8")
    before = {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    for source in [outside, workspace.root/"runs/missing.md"]:
        with pytest.raises((FileNotFoundError, ValueError)):
            check_chapter(workspace, 1, source)
    assert before == {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}


def _visual_template(workspace):
    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app
    result = CliRunner().invoke(app, ["pdf-observation-template", "--workspace", str(workspace.root)])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


@pytest.mark.requires_poppler
def test_visual_template_preserves_valid_observations_and_only_lists_missing_input(tmp_path):
    from pypdf import PdfReader, PdfWriter

    from knowledge_product_workflow.pdf_preview import render_pdf_preview_set
    from knowledge_product_workflow.visual_audit import (
        VisualObservation,
        audit_pdf_visual,
        record_visual_observations,
    )
    from tests.test_visual_audit import _workspace

    workspace = _workspace(tmp_path)
    pdf = workspace.root/"delivery/book.pdf"
    original = PdfReader(pdf).pages
    writer = PdfWriter()
    for page in original:
        writer.add_page(page)
    for _ in range(77):
        writer.add_page(original[-1])
    writer.write(pdf)
    render_pdf_preview_set(pdf, workspace.root/"runs/pdf-preview")
    audit_pdf_visual(workspace, sample_limit=80)
    # Synthetic observations verify the protocol, not human review of a book.
    prior = [VisualObservation(page_number=n, assessment="passed", note=f"隔离夹具页{n}已观察") for n in range(1,80)]
    record_visual_observations(workspace, bind_fixture_observations(workspace, prior))
    before = {p:p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    template = _visual_template(workspace)
    assert template["reusable_pages"] == list(range(1,80))
    assert template["pending_pages"] == [80] and template["needs_submission"] is True
    assert [item["page_number"] for item in template["observations"]] == [80]
    assert template["full_coverage_ready"] is False
    assert before == {p:p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    final = record_visual_observations(workspace, bind_fixture_observations(workspace, [VisualObservation(page_number=80, assessment="passed", note="隔离夹具最后页")]))
    assert final.observations[:79] == prior
    assert _visual_template(workspace)["full_coverage_ready"] is True


@pytest.mark.requires_poppler
def test_visual_template_all_current_has_no_submission_and_empty_record_is_rejected(tmp_path):
    from knowledge_product_workflow.visual_audit import (
        VisualObservation,
        audit_pdf_visual,
        record_visual_observations,
    )
    from tests.test_visual_audit import _workspace

    workspace = _workspace(tmp_path)
    audit_pdf_visual(workspace)
    record_visual_observations(workspace, bind_fixture_observations(workspace, [VisualObservation(page_number=n, assessment="passed", note="隔离协议夹具") for n in [1,2,3]]))
    before = {p:p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    template = _visual_template(workspace)
    assert template["observations"] == [] and template["needs_submission"] is False
    assert template["reusable_pages"] == [1,2,3] and template["full_coverage_ready"] is True
    with pytest.raises(ValueError, match="空记录"):
        record_visual_observations(workspace, bind_fixture_observations(workspace, []))
    assert before == {p:p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}


@pytest.mark.requires_poppler
def test_visual_template_keeps_failed_uncertain_and_cached_audit_reports_real_counts(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app
    from knowledge_product_workflow.visual_audit import (
        VisualObservation,
        audit_pdf_visual,
        record_visual_observations,
    )
    from tests.test_visual_audit import _workspace

    workspace = _workspace(tmp_path)
    audit_pdf_visual(workspace)
    record_visual_observations(workspace, bind_fixture_observations(workspace, [VisualObservation(page_number=n, assessment=assessment, note="隔离状态夹具")
        for n,assessment in [(1,"passed"),(2,"failed"),(3,"uncertain")]]))
    template = _visual_template(workspace)
    assert template["observations"] == [] and template["needs_submission"] is False
    assert template["failed_pages"] == [2] and template["uncertain_pages"] == [3]
    assert template["full_coverage_ready"] is False
    result = CliRunner().invoke(app, ["audit-pdf-visual", "--workspace", str(workspace.root)])
    assert result.exit_code == 1 and "当前工作流阶段" in result.output
    # This fixture has an unapproved Brief. Isolate only the receipt presentation;
    # the real stage refusal above and existing authorization tests stay in place.
    from knowledge_product_workflow import cli
    monkeypatch.setattr(cli, "authorize_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_capture_orchestration_action_or_exit", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "complete_orchestration_action", lambda *args, **kwargs: None)
    result = CliRunner().invoke(app, ["audit-pdf-visual", "--workspace", str(workspace.root)])
    assert result.exit_code == 0, result.output
    assert "已有效 1" in result.output and "未观察 0" in result.output
    assert "失败 1" in result.output and "待判断 1" in result.output
    assert "观察项待看图或人工复核" not in result.output


def test_visual_template_no_clear_images_keeps_manual_boundary(tmp_path, monkeypatch):
    from knowledge_product_workflow import visual_audit
    from tests.test_visual_audit import _workspace

    workspace = _workspace(tmp_path)
    monkeypatch.setattr(visual_audit, "resolve_pdftoppm", lambda: None)
    visual_audit.audit_pdf_visual(workspace)
    template = _visual_template(workspace)
    assert template["unavailable_pages"] == [1,2,3] and template["needs_clear_images"] is True
    assert template["pending_pages"] == [] and template["observations"] == []
    assert template["full_coverage_ready"] is False
    assert "manual_required" in template["note"]


def _guidance_roots():
    if candidate := os.environ.get("KP_TEST_CANDIDATE_RECORD"):
        record = json.loads(Path(candidate).read_text(encoding="utf-8"))
        return Path(record["source_root"]), Path(record["skill_package"])
    root = Path(__file__).resolve().parents[1]
    return root, root/"integrations/cc-switch"


def test_guidance_documents_agree_on_grouping_readonly_and_automatic_completion():
    root, package = _guidance_roots()
    research = (package/"skills/kp-research-verifier/SKILL.md").read_text(encoding="utf-8")
    root_skill = (package/"skills/kp-create/SKILL.md").read_text(encoding="utf-8")
    writer = (package/"skills/kp-chapter-writer/SKILL.md").read_text(encoding="utf-8")
    contract = (package/"references/orchestration-contract.md").read_text(encoding="utf-8")
    runbook = (root/"docs/operator-runbook.md").read_text(encoding="utf-8")
    assert research.index("第一次登记前") < research.index("kp research")
    assert "同URL不是合并依据" in research and "有效覆盖不重登" in research
    assert all("readonly_diagnostics" in text for text in [root_skill, writer, contract, runbook])
    assert "根编排器写完成证明" not in writer
    assert "根 Skill 使用 `kp complete-orchestration-work-order` 写入证明" not in runbook
    assert "CLI自动完成" in writer and "CLI自动完成" in runbook
    visual = (package/"skills/kp-visual-reviewer/SKILL.md").read_text(encoding="utf-8")
    assert all(word in visual for word in ["pending_pages", "reusable_pages", "needs_submission=false", "manual_required"])


def test_guided_cli_action_requires_no_second_manual_completion(tmp_path):
    workspace = _prepared_workspace(tmp_path)
    old = _current_order(workspace)
    calls = []
    def host_call(*args):
        calls.append(args[0])
        return _cli(workspace, *args)
    result = host_call("research", "combined", "https://example.com/combined", "--claim", "claim-one",
        "--claim", "claim-two", "--excerpt", "隔离共同证据", "--license-note", "测试许可")
    assert result.returncode == 0, result.stderr
    successor = _assert_completion(workspace, old, "research", workspace.root/"research/source-ledger.jsonl")
    before = {p:p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    status = host_call("status", "--json")
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["current_work_order"].endswith(successor.work_order_id+".json")
    assert calls == ["research", "status"]
    assert before == {p:p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}


def test_candidate_cli_ignores_source_pythonpath_and_records_actual_module_identity(tmp_path, monkeypatch):
    candidate = os.environ.get("KP_TEST_CANDIDATE_RECORD")
    if candidate is None:
        pytest.skip("仅在最终候选模式执行父子进程身份负例")
    record = json.loads(Path(candidate).read_text(encoding="utf-8"))
    monkeypatch.setenv("PYTHONPATH", str(Path(record["project_root"])/"src"))
    workspace = _prepared_workspace(tmp_path)
    identities = Path(os.environ["TMP"])/"cli-identities"
    before = set(identities.glob("*.json"))
    result = _cli(workspace, "status", "--json")
    assert result.returncode == 0, result.stderr
    new = set(identities.glob("*.json"))-before
    assert len(new) == 1
    identity = json.loads(new.pop().read_text(encoding="utf-8"))
    assert identity["wheel_sha256"] == record["wheel_sha256"]
    for name, loaded in identity["loaded"].items():
        assert Path(loaded["path"]).is_relative_to(Path(record["site_root"]))
        assert loaded["sha256"] == record["package_files"][name]


@pytest.mark.parametrize("damage", ["hash", "site", "wheel"])
def test_candidate_cli_refuses_wrong_identity_before_workspace_write(tmp_path, monkeypatch, damage):
    candidate = os.environ.get("KP_TEST_CANDIDATE_RECORD")
    if candidate is None:
        pytest.skip("仅在最终候选模式执行身份拒绝负例")
    workspace = _prepared_workspace(tmp_path)
    before = {p:p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    record = json.loads(Path(candidate).read_text(encoding="utf-8"))
    if damage == "hash":
        record["package_files"]["knowledge_product_workflow/cli.py"] = "0"*64
    elif damage == "site":
        record["site_root"] = str(Path(record["project_root"])/"src")
    else:
        record["wheel_sha256"] = "0"*64
    altered = tmp_path/"wrong-candidate.json"
    altered.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv("KP_TEST_CANDIDATE_RECORD", str(altered))
    result = _cli(workspace, "research", "refused", "https://example.com/refused", "--claim", "claim-one",
                  "--excerpt", "不能写入的夹具", "--license-note", "测试许可")
    assert result.returncode != 0
    assert before == {p:p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
