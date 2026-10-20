"""研究 CLI 成功输出和原工单、后继的真实绑定回归。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.runtime import (
    OrchestrationWorkOrderError,
    capture_orchestration_action,
    create_orchestration_work_order,
    inspect_workflow,
    read_orchestration_work_order,
    validate_orchestration_work_order,
)
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.product_brief import (
    BriefSemanticFields,
    brief_contract_fingerprints,
    create_product_brief,
)
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint


def _approve(path: Path) -> None:
    metadata, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    path.write_text(
        render_markdown_artifact(transition(metadata, ArtifactStatus.APPROVED, approved_by="用户"), body),
        encoding="utf-8",
    )


def _cli(workspace: Workspace, *args: str, bootstrap: str = "") -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    flags = ["-B"]
    binding = ""
    if candidate := env.get("KP_TEST_CANDIDATE_RECORD"):
        record = json.loads(Path(candidate).read_text(encoding="utf-8"))
        project = Path(record["project_root"])
        env.pop("PYTHONPATH", None)
        flags.append("-S")
        binding = (
            "import sys, json, os, hashlib, uuid\nfrom pathlib import Path\n"
            f"record = json.loads(Path({candidate!r}).read_text(encoding='utf-8'))\n"
            f"project = Path({str(project)!r}).resolve()\n"
            "site = Path(record['site_root']).resolve()\n"
            "assert record['version'] in {'1.3.5','1.4.0','1.4.1'} and site.is_relative_to(project/('work/v'+record['version']))\n"
            "wheel = Path(record['wheel']).resolve()\n"
            "assert wheel.is_relative_to(project/('work/v'+record['version']))\n"
            "assert hashlib.sha256(wheel.read_bytes()).hexdigest() == record['wheel_sha256']\n"
            "sys.path[:0] = [str(site), str(project/'.venv/Lib/site-packages')]\n"
            "import knowledge_product_workflow, knowledge_product_workflow.cli\n"
            "loaded = {}\n"
            "for module in [knowledge_product_workflow, knowledge_product_workflow.cli]:\n"
            " path = Path(module.__file__).resolve()\n"
            " assert path.is_relative_to(site/'knowledge_product_workflow')\n"
            " name = path.relative_to(site).as_posix()\n"
            " sha = hashlib.sha256(path.read_bytes()).hexdigest()\n"
            " assert sha == record['package_files'][name]\n"
            " loaded[name] = {'path': str(path), 'sha256': sha}\n"
            "temporary = Path(os.environ['TMP']).resolve()\n"
            "assert temporary.is_relative_to(project/('tmp/v'+record['version']))\n"
            "identities = temporary/'cli-identities'; identities.mkdir(exist_ok=True)\n"
            "(identities/(uuid.uuid4().hex+'.json')).write_text(json.dumps({'loaded':loaded,"
            "'wheel_sha256':record['wheel_sha256'],'pid':os.getpid()}),encoding='utf-8')\n"
        )
    else:
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    command = [sys.executable, *flags, "-c", binding + bootstrap + "\nfrom knowledge_product_workflow.cli import app; app()",
               *args, "--workspace", str(workspace.root)]
    result = subprocess.run(command, env=env, capture_output=True, text=True,
                            encoding="utf-8", timeout=120, check=False)
    print(json.dumps({"args": args, "workspace": str(workspace.root), "exit_code": result.returncode,
                      "stdout": result.stdout, "stderr": result.stderr}, ensure_ascii=False))
    return result


def _prepared_workspace(tmp_path: Path, *, claim_ids: tuple[tuple[str, ...], ...] | None = None) -> Workspace:
    workspace = Workspace.create(tmp_path, "research-guide", current_policy=True)
    brief = create_product_brief(
        workspace, "研究后继", DetailConstraints(target_words=1000),
        semantics=BriefSemanticFields(reader_request="生成中文研究后继教程。")
    )
    _approve(brief)
    blueprint = submit_test_blueprint(
        workspace, brief, titles=("基础", "案例"), claim_ids=claim_ids or (("claim-one",), ("claim-two",))
    )
    _approve(blueprint)
    # Library-only setup ends here. Compile and all research actions use the real CLI.
    create_orchestration_work_order(workspace)
    compiled = _cli(workspace, "compile-contracts", "--all")
    assert compiled.returncode == 0, compiled.stderr
    assert inspect_workflow(workspace).stage == "research_required"
    return workspace


def _current_order(workspace: Workspace):
    path = inspect_workflow(workspace).current_work_order
    assert path is not None
    return read_orchestration_work_order(workspace, workspace.root / path)


def _research(workspace: Workspace, claim: str, source: str | None = None, url: str = "https://example.com/evidence"):
    return _cli(workspace, "research", source or claim, url, "--claim", claim,
                "--excerpt", "真实 CLI 测试来源摘录。", "--license-note", "测试夹具")


def _impact(workspace: Workspace, impact: str = "no_product_change", summary: str = "研究不改变课程范围。"):
    return _cli(workspace, "record-research-impact", "--impact", impact, "--summary", summary)


def _assert_completion(workspace: Workspace, old, action: str, output: Path):
    completed = read_orchestration_work_order(workspace, old.work_order_id)
    assert completed.lifecycle.value == "completed"
    proof = completed.completion_proof
    assert proof is not None and proof.transition == action
    assert proof.input_manifest_sha256 == old.input_manifest_sha256
    assert proof.event_id
    relative = output.relative_to(workspace.root).as_posix()
    assert [(entry.path, entry.sha256) for entry in proof.output_manifest] == [
        (relative, sha256(output.read_bytes()).hexdigest())
    ]
    current = _current_order(workspace)
    assert proof.successor_work_order_id == current.work_order_id
    assert current.work_order_id != old.work_order_id
    validate_orchestration_work_order(workspace, current.work_order_id)
    print(json.dumps({"completed": completed.model_dump(mode="json"),
                      "successor": current.model_dump(mode="json")}, ensure_ascii=False))
    return current


def test_two_claim_cli_flow_reaches_content_plan_without_refresh(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path)
    contracts = {path.name: path.read_bytes() for path in (workspace.root / "chapters").glob("*-contract.md")}
    assert _research(workspace, "claim-one").returncode == 0
    assert _research(workspace, "claim-two").returncode == 0
    assert _impact(workspace).returncode == 0
    plan = workspace.root / "runs" / "content-plan-input.yaml"
    plan.write_text(yaml.safe_dump({"sections": [{
        "section_id": "1.1", "reader_job": "比较方案并写出取舍条件。", "opening_mode": "decision",
        "block_sequence": ["decision", "worksheet"], "artifact": "方案取舍工作表",
        "asset_decision": "worksheet", "asset_rationale": "记录方案、条件与后果。",
    }]}, allow_unicode=True), encoding="utf-8")
    submitted = _cli(workspace, "submit-content-plan", "1", "--from", str(plan))
    assert submitted.returncode == 0, submitted.stderr
    current = _current_order(workspace)
    validate_orchestration_work_order(workspace, current.work_order_id)
    assert contracts == {path.name: path.read_bytes() for path in (workspace.root / "chapters").glob("*-contract.md")}
    assert (workspace.root / "chapters/chapter-01-content-plan.yaml").is_file()


def test_each_claim_closes_its_original_order_and_keeps_uncovered_claims_pending(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path)
    first = _current_order(workspace)
    ledger = workspace.root / "research/source-ledger.jsonl"
    assert _research(workspace, "claim-one").returncode == 0
    first_bytes = ledger.read_bytes()
    second = _assert_completion(workspace, first, "research", ledger)
    assert second.stage == "research_required"
    refused = _impact(workspace)
    assert refused.returncode == 1
    assert not list((workspace.root / "runs/orchestration/research-impacts").glob("*.json"))
    assert read_orchestration_work_order(workspace, second.work_order_id).completion_proof is None
    assert _research(workspace, "claim-two").returncode == 0
    assert ledger.read_bytes().startswith(first_bytes)
    pending = _assert_completion(workspace, second, "research", ledger)
    assert pending.stage == "research_impact_decision_pending"
    assert _impact(workspace).returncode == 0
    decision = next((workspace.root / "runs/orchestration/research-impacts").glob("*.json"))
    successor = _assert_completion(workspace, pending, "record-research-impact", decision)
    assert successor.stage == "sample_draft_missing"
    assert successor.target_chapter_number == 1


def test_partial_research_failure_can_resume_from_the_uncovered_claim(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path)
    first = _current_order(workspace)
    ledger = workspace.root / "research/source-ledger.jsonl"
    assert _research(workspace, "claim-one").returncode == 0
    pending = _assert_completion(workspace, first, "research", ledger)
    original = ledger.read_bytes()
    failed = _research(workspace, "claim-two", url="not-a-url")
    assert failed.returncode != 0
    assert ledger.read_bytes() == original
    assert _current_order(workspace).work_order_id == pending.work_order_id
    assert read_orchestration_work_order(workspace, pending.work_order_id).completion_proof is None
    assert _research(workspace, "claim-two").returncode == 0
    successor = _assert_completion(workspace, pending, "research", ledger)
    assert successor.stage == "research_impact_decision_pending"
    assert ledger.read_bytes().startswith(original)
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2


def test_process_interruption_after_append_preserves_output_without_completion(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path)
    old = _current_order(workspace)
    bootstrap = """
import os
import knowledge_product_workflow.cli as cli
original_append = cli.append_source_entry
def append_and_interrupt(path, entry):
    original_append(path, entry)
    os._exit(71)
cli.append_source_entry = append_and_interrupt
"""
    interrupted = _cli(workspace, "research", "interrupted-source", "https://example.com/evidence",
                       "--claim", "claim-one", "--excerpt", "退出前保留的真实来源。",
                       "--license-note", "测试夹具", bootstrap=bootstrap)
    assert interrupted.returncode == 71
    ledger = workspace.root / "research/source-ledger.jsonl"
    preserved = ledger.read_bytes()
    assert json.loads(preserved.decode())["source_id"] == "interrupted-source"
    assert read_orchestration_work_order(workspace, old.work_order_id).completion_proof is None
    rejected = _research(workspace, "claim-two")
    assert rejected.returncode == 1 and "输入工件已变化" in rejected.stderr
    assert ledger.read_bytes() == preserved
    assert read_orchestration_work_order(workspace, old.work_order_id).completion_proof is None


@pytest.mark.parametrize("failure", ["unknown-claim", "invalid-url", "ledger-unwritable"])
def test_failed_research_does_not_complete_or_append_a_success(tmp_path: Path, failure: str) -> None:
    workspace = _prepared_workspace(tmp_path)
    old = _current_order(workspace)
    ledger = workspace.root / "research/source-ledger.jsonl"
    if failure == "ledger-unwritable":
        # The ledger is absent in a fresh fixture; a directory makes append fail before writing bytes.
        ledger.mkdir()
    result = _research(workspace, "not-declared" if failure == "unknown-claim" else "claim-one",
                       url="not-a-url" if failure == "invalid-url" else "https://example.com/evidence")
    assert result.returncode != 0
    assert read_orchestration_work_order(workspace, old.work_order_id).completion_proof is None
    assert not ledger.is_file()
    assert not list((workspace.root / "runs/orchestration/research-impacts").glob("*.json"))


@pytest.mark.parametrize("drift", ["brief", "contract", "request", "impact-before-command"])
def test_preexisting_scope_drift_is_rejected_before_research_write(tmp_path: Path, drift: str) -> None:
    workspace = _prepared_workspace(tmp_path)
    if drift == "impact-before-command":
        assert _research(workspace, "claim-one").returncode == 0
        assert _research(workspace, "claim-two").returncode == 0
    old = _current_order(workspace)
    paths = {"brief": "planning/product-brief.md", "contract": "chapters/chapter-01-contract.md",
             "request": "runs/request-contract.yaml", "impact-before-command": "planning/product-brief.md"}
    changed = workspace.root / paths[drift]
    changed.write_bytes(changed.read_bytes() + "\n未授权改变输入。\n".encode())
    ledger = workspace.root / "research/source-ledger.jsonl"
    before = ledger.read_bytes() if ledger.is_file() else None
    result = _impact(workspace) if drift == "impact-before-command" else _research(workspace, "claim-one")
    assert result.returncode == 1
    assert "输入工件已变化" in result.stderr
    assert (ledger.read_bytes() if ledger.is_file() else None) == before
    assert read_orchestration_work_order(workspace, old.work_order_id).completion_proof is None
    assert not list((workspace.root / "runs/orchestration/research-impacts").glob("*.json"))


def test_expired_order_is_retired_and_cannot_be_captured_again(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path)
    old = _current_order(workspace)
    path = workspace.root / "runs/orchestration/work-orders" / f"{old.work_order_id}.json"
    path.write_text(old.model_copy(update={"expires_at": old.created_at - timedelta(seconds=1)}).model_dump_json(), encoding="utf-8")
    assert _research(workspace, "claim-one").returncode == 0
    retired = read_orchestration_work_order(workspace, old.work_order_id)
    assert retired.lifecycle.value == "superseded"
    assert retired.completion_proof is not None
    assert retired.completion_proof.transition != "research"
    with pytest.raises(OrchestrationWorkOrderError, match="已过期"):
        validate_orchestration_work_order(workspace, old.work_order_id, command="research")
    current = _current_order(workspace)
    assert capture_orchestration_action(workspace, "research") == current.work_order_id


@pytest.mark.parametrize("impact,summary", [("invented", "非法分类"), ("no_product_change", "")])
def test_invalid_impact_keeps_original_pending_order(tmp_path: Path, impact: str, summary: str) -> None:
    workspace = _prepared_workspace(tmp_path)
    assert _research(workspace, "claim-one").returncode == 0
    assert _research(workspace, "claim-two").returncode == 0
    old = _current_order(workspace)
    result = _impact(workspace, impact, summary)
    assert result.returncode == 1
    assert read_orchestration_work_order(workspace, old.work_order_id).completion_proof is None
    assert not list((workspace.root / "runs/orchestration/research-impacts").glob("*.json"))


@pytest.mark.parametrize("impact,route", [
    ("packaging_change", "amend_brief_delivery_only"),
    ("content_change", "amend_brief_then_outline_then_compile_contracts"),
])
def test_changed_impact_preserves_approval_route_and_artifact_approvals(tmp_path: Path, impact: str, route: str) -> None:
    workspace = _prepared_workspace(tmp_path)
    assert _research(workspace, "claim-one").returncode == 0
    assert _research(workspace, "claim-two").returncode == 0
    original = {path: path.read_bytes() for path in [workspace.root / "planning/product-brief.md",
                                                     workspace.root / "planning/curriculum-blueprint.md",
                                                     *list((workspace.root / "chapters").glob("*-contract.md"))]}
    old = _current_order(workspace)
    assert _impact(workspace, impact, "研究要求相应变更并重新确认。").returncode == 0
    decision_path = next((workspace.root / "runs/orchestration/research-impacts").glob("*.json"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["requires_human_approval"] is True
    assert decision["route"] == route
    assert original == {path: path.read_bytes() for path in original}
    _assert_completion(workspace, old, "record-research-impact", decision_path)
    # The classification records a decision, while actual changes retain the existing approval gate.
    amendment = workspace.root / "runs/impact-amendment.json"
    changed_field = ({"subtitle": "研究后确认的新版包装"} if impact == "packaging_change"
                     else {"target_audience": "负责研究审核的高级工程师"})
    amendment.write_text(json.dumps({**changed_field, "change_reason": "按研究决策修订。"}, ensure_ascii=False), encoding="utf-8")
    assert _cli(workspace, "amend-brief", "--from", str(amendment)).returncode == 0
    blocked = _cli(workspace, "approve-brief-amendment")
    assert blocked.returncode == 1
    assert "尚未收到当前版本的用户确认" in blocked.stderr
    assert original == {path: path.read_bytes() for path in original}
    preview = json.loads((workspace.root / "runs/brief-amendment-preview.json").read_text(encoding="utf-8"))
    assert preview["revision_kind"] == ("delivery" if impact == "packaging_change" else "content")
    assert _cli(workspace, "request-user-decision", "brief_amendment").returncode == 0
    assert _cli(workspace, "record-user-decision", "brief_amendment", "--decision", "approve",
                "--response", "批准当前测试修订。").returncode == 0
    approved = _cli(workspace, "approve-brief-amendment")
    assert approved.returncode == 0, approved.stderr
    brief = workspace.root / "planning/product-brief.md"
    before_meta, before_body = parse_markdown_artifact(original[brief].decode().replace("\r\n", "\n"))
    after_meta, after_body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    assert after_meta.version == before_meta.version + 1
    same_content = (brief_contract_fingerprints(before_body)["content_contract_sha256"]
                    == brief_contract_fingerprints(after_body)["content_contract_sha256"])
    assert same_content is (impact == "packaging_change")
    assert (workspace.root / "planning/history/product-brief.v1.md").read_bytes() == original[brief]
    decision = json.loads((workspace.root / "runs/interaction/brief_amendment.json").read_text(encoding="utf-8"))
    assert decision["state"] == "consumed"
    if impact == "content_change":
        assert inspect_workflow(workspace).stage == "blueprint_invalid"


def test_legacy_without_control_records_still_appends_source(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path)
    original = workspace.root / "runs/orchestration"
    original.rename(workspace.root / "runs/test-archived-orchestration")
    assert inspect_workflow(workspace).orchestration_mode == "legacy"
    assert _research(workspace, "claim-one").returncode == 0
    assert not original.exists()
    assert _research(workspace, "claim-two").returncode == 0
    assert _impact(workspace).returncode == 0
    assert not list((original / "work-orders").glob("*.json"))
    assert len((workspace.root / "research/source-ledger.jsonl").read_text(encoding="utf-8").splitlines()) == 2
