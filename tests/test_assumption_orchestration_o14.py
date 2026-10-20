"""合法假设追加自动衔接真实工单，失败输入和未知变化不推进。"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from knowledge_product_workflow.runtime import (
    OrchestrationWorkOrderError,
    inspect_workflow,
    read_orchestration_work_order,
    validate_orchestration_work_order,
)
from knowledge_product_workflow.workspace import Workspace
from tests.test_research_orchestration_o10 import (
    _cli,
    _current_order,
    _impact,
    _prepared_workspace,
    _research,
)


def _assumption(workspace: Workspace, *, disposition: str = "reframed_as_case",
                claim: str | None = None, assumption_id: str = "decision-case",
                bootstrap: str = "", extra: tuple[str, ...] = ()):
    args = ["assumption", assumption_id, "--statement", "仅在给定案例条件下比较方案。",
            "--impact", "scope", "--disposition", disposition, "--rationale", "保留适用条件。"]
    if claim is not None:
        args.extend(["--claim", claim])
    return _cli(workspace, *args, *extra, bootstrap=bootstrap)


def _protected(workspace: Workspace) -> dict[str, bytes]:
    paths = [workspace.root / "planning/product-brief.md",
             workspace.root / "planning/curriculum-blueprint.md",
             *list((workspace.root / "chapters").glob("*.md")),
             *list((workspace.root / "runs/interaction").glob("*.json"))]
    return {path.relative_to(workspace.root).as_posix(): path.read_bytes() for path in paths}


def _events(workspace: Workspace):
    return [json.loads(line) for line in (workspace.root / "runs/events.jsonl").read_text(
        encoding="utf-8").splitlines()]


def _assert_refreshed(workspace: Workspace, old, expected_stage: str):
    current = _current_order(workspace)
    assert current.stage == expected_stage
    assert current.work_order_id != old.work_order_id
    validate_orchestration_work_order(workspace, current.work_order_id)
    retired = read_orchestration_work_order(workspace, old.work_order_id)
    assert retired.lifecycle.value == "superseded"
    assert retired.completion_proof is not None
    assert retired.completion_proof.transition == "superseded"
    assert retired.completion_proof.input_manifest_sha256 == old.input_manifest_sha256
    with pytest.raises(OrchestrationWorkOrderError, match="已过期、已完成或已取消"):
        validate_orchestration_work_order(workspace, old.work_order_id)
    updates = [event for event in _events(workspace)
               if event["event_type"] == "orchestration_controlled_update"]
    assert updates[-1]["payload"]["action"] == "assumption"
    assert updates[-1]["payload"]["changed_inputs"] == ["research/assumption-ledger.jsonl"]
    ledger = workspace.root / "research/assumption-ledger.jsonl"
    assert updates[-1]["payload"]["outputs"] == [{
        "path": "research/assumption-ledger.jsonl", "sha256": sha256(ledger.read_bytes()).hexdigest()
    }]
    print(json.dumps({"retired": retired.model_dump(mode="json"),
                      "current": current.model_dump(mode="json"),
                      "controlled_update": updates[-1]}, ensure_ascii=False))
    return current


@pytest.mark.parametrize("disposition,claim", [("reframed_as_case", None), ("verified", "claim-one")])
def test_research_assumption_impact_cli_flow_needs_no_manual_refresh(
    tmp_path: Path, disposition: str, claim: str | None,
) -> None:
    workspace = _prepared_workspace(tmp_path)
    assert _research(workspace, "claim-one").returncode == 0
    assert _research(workspace, "claim-two").returncode == 0
    old = _current_order(workspace)
    protected = _protected(workspace)
    result = _assumption(workspace, disposition=disposition, claim=claim)
    assert result.returncode == 0, result.stderr
    # Exercise the original failure before inspecting the successor.
    impacted = _impact(workspace)
    assert impacted.returncode == 0, impacted.stderr
    superseded = read_orchestration_work_order(workspace, old.work_order_id)
    assert superseded.lifecycle.value == "superseded"
    assert superseded.completion_proof.transition == "superseded"
    assert _current_order(workspace).stage == "sample_draft_missing"
    validate_orchestration_work_order(workspace, _current_order(workspace).work_order_id)
    with pytest.raises(OrchestrationWorkOrderError):
        validate_orchestration_work_order(workspace, old.work_order_id,
                                          command="record-research-impact")
    assert _protected(workspace) == protected
    assert len([event for event in _events(workspace)
                if event["event_type"] == "assumption_recorded"]) == 1


@pytest.mark.parametrize("phase", ["before-research", "between-claims", "before-impact"])
def test_supplementary_case_keeps_current_research_stage_and_fact_requirements(
    tmp_path: Path, phase: str,
) -> None:
    workspace = _prepared_workspace(tmp_path)
    if phase != "before-research":
        assert _research(workspace, "claim-one").returncode == 0
    if phase == "before-impact":
        assert _research(workspace, "claim-two").returncode == 0
    old = _current_order(workspace)
    protected = _protected(workspace)
    source = workspace.root / "research/source-ledger.jsonl"
    before_source = source.read_bytes() if source.is_file() else None
    assert _assumption(workspace).returncode == 0
    _assert_refreshed(workspace, old, old.stage)
    assert (source.read_bytes() if source.is_file() else None) == before_source
    assert _protected(workspace) == protected
    if phase != "before-impact":
        refused = _impact(workspace)
        assert refused.returncode == 1
        assert not list((workspace.root / "runs/orchestration/research-impacts").glob("*.json"))
    if phase == "before-research":
        assert _research(workspace, "claim-one").returncode == 0
    if phase != "before-impact":
        assert _research(workspace, "claim-two").returncode == 0
    assert _impact(workspace).returncode == 0


def test_unresolved_assumption_can_be_legally_revised_without_completing_research(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path)
    protected = _protected(workspace)
    old = _current_order(workspace)
    assert _assumption(workspace, disposition="evidence_needed").returncode == 0
    pending = _assert_refreshed(workspace, old, "assumptions_require_resolution")
    refused = _impact(workspace)
    assert refused.returncode == 1
    assert not list((workspace.root / "runs/orchestration/research-impacts").glob("*.json"))
    ledger = workspace.root / "research/assumption-ledger.jsonl"
    first = ledger.read_bytes()
    assert _assumption(workspace).returncode == 0
    _assert_refreshed(workspace, pending, "research_required")
    assert ledger.read_bytes().startswith(first)
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2
    assert _protected(workspace) == protected
    assert _research(workspace, "claim-one").returncode == 0
    assert _research(workspace, "claim-two").returncode == 0
    assert _impact(workspace).returncode == 0


def test_case_after_impact_keeps_the_existing_writer_authority(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path)
    assert _research(workspace, "claim-one").returncode == 0
    assert _research(workspace, "claim-two").returncode == 0
    assert _impact(workspace).returncode == 0
    old = _current_order(workspace)
    protected = _protected(workspace)
    assert _assumption(workspace).returncode == 0
    current = _current_order(workspace)
    assert current.stage == "sample_draft_missing"
    assert current.skill == "kp-chapter-writer"
    validate_orchestration_work_order(workspace, current.work_order_id, command="submit-chapter",
                                      chapter_number=1)
    with pytest.raises(OrchestrationWorkOrderError, match="不允许"):
        validate_orchestration_work_order(workspace, current.work_order_id, command="publish")
    assert current.allowed_commands == old.allowed_commands
    assert current.allowed_write_paths == old.allowed_write_paths
    assert _protected(workspace) == protected


@pytest.mark.parametrize("failure", ["invalid-id", "unsupported-disposition", "unsupported-option",
                                    "verified-no-claim", "verified-unknown-claim",
                                    "verified-uncovered-claim", "wrong-workspace"])
def test_rejected_assumption_input_keeps_ledger_event_and_order_unchanged(
    tmp_path: Path, failure: str,
) -> None:
    workspace = _prepared_workspace(tmp_path)
    old = _current_order(workspace)
    before = {path.relative_to(workspace.root).as_posix(): path.read_bytes()
              for path in workspace.root.rglob("*") if path.is_file()}
    kwargs = {}
    if failure == "invalid-id":
        kwargs["assumption_id"] = "Invalid_ID"
    elif failure == "unsupported-disposition":
        kwargs["disposition"] = "invented"
    elif failure == "unsupported-option":
        kwargs["extra"] = ("--from", str(workspace.root / "runs/unsupported.json"))
    elif failure.startswith("verified"):
        kwargs["disposition"] = "verified"
        if failure == "verified-unknown-claim":
            kwargs["claim"] = "not-declared"
        elif failure == "verified-uncovered-claim":
            kwargs["claim"] = "claim-one"
    target = Workspace(root=workspace.root / "not-a-book") if failure == "wrong-workspace" else workspace
    result = _assumption(target, **kwargs)
    assert result.returncode != 0
    after = {path.relative_to(workspace.root).as_posix(): path.read_bytes()
             for path in workspace.root.rglob("*") if path.is_file()}
    assert after == before
    assert _current_order(workspace).work_order_id == old.work_order_id
    assert read_orchestration_work_order(workspace, old.work_order_id).completion_proof is None


@pytest.mark.parametrize("drift", ["brief", "contract", "request"])
def test_assumption_does_not_silently_refresh_preexisting_uncontrolled_input_change(
    tmp_path: Path, drift: str,
) -> None:
    workspace = _prepared_workspace(tmp_path)
    old = _current_order(workspace)
    paths = {"brief": "planning/product-brief.md", "contract": "chapters/chapter-01-contract.md",
             "request": "runs/request-contract.yaml"}
    changed = workspace.root / paths[drift]
    changed.write_bytes(changed.read_bytes() + "\n未授权变化。\n".encode())
    events = (workspace.root / "runs/events.jsonl").read_bytes()
    result = _assumption(workspace)
    assert result.returncode == 1
    assert "输入工件已变化" in result.stderr
    assert not (workspace.root / "research/assumption-ledger.jsonl").exists()
    assert (workspace.root / "runs/events.jsonl").read_bytes() == events
    assert read_orchestration_work_order(workspace, old.work_order_id).completion_proof is None


def test_assumption_preserves_partial_append_and_refuses_unknown_change_during_write(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path)
    old = _current_order(workspace)
    bootstrap = """
import knowledge_product_workflow.cli as cli
original_append = cli.append_assumption_entry
def append_and_change(path, entry):
    original_append(path, entry)
    request = path.parent.parent / "runs/request-contract.yaml"
    request.write_bytes(request.read_bytes() + b"\\nunknown_drift: true\\n")
cli.append_assumption_entry = append_and_change
"""
    result = _assumption(workspace, bootstrap=bootstrap)
    assert result.returncode == 1
    assert "非输出输入变化" in result.stderr
    ledger = workspace.root / "research/assumption-ledger.jsonl"
    assert json.loads(ledger.read_text(encoding="utf-8"))["assumption_id"] == "decision-case"
    assert read_orchestration_work_order(workspace, old.work_order_id).completion_proof is None
    assert not any(event["event_type"] == "orchestration_controlled_update"
                   for event in _events(workspace))
    refused = _impact(workspace)
    assert refused.returncode == 1 and "输入工件已变化" in refused.stderr


@pytest.mark.parametrize("failure", ["stale-source", "malformed-source"])
def test_verified_assumption_refuses_noncurrent_or_invalid_source_without_writing(
    tmp_path: Path, failure: str,
) -> None:
    workspace = _prepared_workspace(tmp_path)
    assert _research(workspace, "claim-one").returncode == 0
    old = _current_order(workspace)
    if failure == "stale-source":
        contract = workspace.root / "chapters/chapter-01-contract.md"
        contract.write_bytes(contract.read_bytes() + "\n真实夹具合约内容已修订。\n".encode())
    else:
        source = workspace.root / "research/source-ledger.jsonl"
        source.write_bytes(source.read_bytes() + b"not-json\n")
    before = {path.relative_to(workspace.root).as_posix(): path.read_bytes()
              for path in workspace.root.rglob("*") if path.is_file()}
    result = _assumption(workspace, disposition="verified", claim="claim-one")
    assert result.returncode == 1
    assert "无法记录产品假设" in result.stderr
    if failure == "stale-source":
        assert "绑定的是旧章节合约" in result.stderr
    after = {path.relative_to(workspace.root).as_posix(): path.read_bytes()
             for path in workspace.root.rglob("*") if path.is_file()}
    assert after == before
    assert read_orchestration_work_order(workspace, old.work_order_id).completion_proof is None


def test_assumption_append_failure_does_not_record_success_or_supersede_order(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path)
    old = _current_order(workspace)
    ledger = workspace.root / "research/assumption-ledger.jsonl"
    ledger.mkdir()
    events = (workspace.root / "runs/events.jsonl").read_bytes()
    result = _assumption(workspace)
    assert result.returncode == 1
    assert ledger.is_dir()
    assert (workspace.root / "runs/events.jsonl").read_bytes() == events
    assert read_orchestration_work_order(workspace, old.work_order_id).completion_proof is None


def test_process_interruption_after_assumption_append_retains_ledger_without_completion(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path)
    old = _current_order(workspace)
    bootstrap = """
import os
import knowledge_product_workflow.cli as cli
original_append = cli.append_assumption_entry
def append_and_interrupt(path, entry):
    original_append(path, entry)
    os._exit(72)
cli.append_assumption_entry = append_and_interrupt
"""
    result = _assumption(workspace, bootstrap=bootstrap)
    assert result.returncode == 72
    ledger = workspace.root / "research/assumption-ledger.jsonl"
    original = ledger.read_bytes()
    assert json.loads(original.decode())["assumption_id"] == "decision-case"
    assert read_orchestration_work_order(workspace, old.work_order_id).completion_proof is None
    assert not any(event["event_type"] in {"assumption_recorded", "orchestration_controlled_update"}
                   for event in _events(workspace))
    refused = _research(workspace, "claim-one")
    assert refused.returncode == 1 and "输入工件已变化" in refused.stderr
    assert ledger.read_bytes() == original
    assert read_orchestration_work_order(workspace, old.work_order_id).completion_proof is None


def test_legacy_assumption_append_does_not_adopt_orchestration(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path)
    original = workspace.root / "runs/orchestration"
    original.rename(workspace.root / "runs/test-archived-orchestration")
    assert inspect_workflow(workspace).orchestration_mode == "legacy"
    assert _assumption(workspace).returncode == 0
    assert not original.exists()
    assert len((workspace.root / "research/assumption-ledger.jsonl").read_text(
        encoding="utf-8").splitlines()) == 1
