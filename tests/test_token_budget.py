from __future__ import annotations

import json
from hashlib import sha256

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.quality import Severity
from knowledge_product_workflow.token_budget import (
    TokenBudgetActionRequest,
    TokenBudgetExceededError,
    TokenMode,
    complete_model_action,
    freeze_token_budget,
    initialize_token_budget,
    load_token_budget,
    preflight_model_action,
    token_budget_findings,
)
from knowledge_product_workflow.workspace import Workspace


def _artifact_sha(value: str = "chapter-01") -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _request(
    *,
    artifact: str = "chapter-01",
    input_tokens: int = 700,
    optional: bool = False,
    output_tokens: int = 300,
) -> TokenBudgetActionRequest:
    return TokenBudgetActionRequest(
        skill="kp-chapter-writer",
        action="draft-chapter",
        artifact_sha256=_artifact_sha(artifact),
        chapter_number=1,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        optional=optional,
    )


def test_new_workspace_starts_with_a_frozen_lean_budget(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")

    budget = load_token_budget(workspace)

    assert budget.mode is TokenMode.LEAN
    assert budget.frozen_at is not None
    assert budget.entries == []
    assert (workspace.root / "runs" / "token-budget.json").is_file()


def test_preflight_reserves_usage_and_completed_action_is_reused_from_cache(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    request = _request()

    first = preflight_model_action(workspace, request)
    assert first.cache_hit is False
    assert first.action.state == "authorized"

    completed = complete_model_action(
        workspace,
        first.action.action_id,
        input_tokens=650,
        output_tokens=250,
        usage_source="actual",
    )
    assert completed.state == "completed"

    second = preflight_model_action(workspace, request)
    assert second.cache_hit is True
    assert second.action.action_id == first.action.action_id
    assert len(load_token_budget(workspace).entries) == 1


def test_near_budget_blocks_optional_actions_but_preserves_required_work(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    freeze_token_budget(
        workspace,
        mode=TokenMode.LEAN,
        book_limit_tokens=1_000,
        chapter_limit_tokens=1_000,
        action_limit_tokens=1_000,
    )
    first = preflight_model_action(workspace, _request(output_tokens=200))
    complete_model_action(
        workspace,
        first.action.action_id,
        input_tokens=700,
        output_tokens=200,
        usage_source="estimated",
    )

    optional = preflight_model_action(
        workspace,
        _request(artifact="optional-diagram", input_tokens=50, optional=True, output_tokens=10),
    )
    assert optional.optional_actions_allowed is False

    required = preflight_model_action(
        workspace,
        _request(artifact="required-revision", input_tokens=50, optional=False, output_tokens=10),
    )
    assert required.cache_hit is False


def test_budget_exhaustion_and_unfinished_reservation_block_delivery(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    freeze_token_budget(
        workspace,
        mode=TokenMode.LEAN,
        book_limit_tokens=900,
        chapter_limit_tokens=900,
        action_limit_tokens=900,
    )

    with pytest.raises(TokenBudgetExceededError):
        preflight_model_action(workspace, _request(output_tokens=300))

    initialize_token_budget(workspace, overwrite=True)
    pending = preflight_model_action(workspace, _request())
    findings = token_budget_findings(workspace, require_budget=True)

    assert pending.action.state == "authorized"
    assert {finding.code for finding in findings} == {"TOKEN_BUDGET_ACTION_UNFINISHED"}
    assert findings[0].severity is Severity.BLOCKER


def test_hidden_cli_records_usage_and_reuses_a_current_action(tmp_path) -> None:
    runner = CliRunner()
    workspace_path = tmp_path / "guide"
    assert runner.invoke(app, ["init", "guide", "--root", str(tmp_path)]).exit_code == 0
    request_path = workspace_path / "runs" / "token-action.json"
    request_path.write_text(_request().model_dump_json(), encoding="utf-8")

    first = runner.invoke(
        app,
        ["token-preflight", "--workspace", str(workspace_path), "--from", str(request_path)],
    )
    assert first.exit_code == 0, first.stdout
    first_payload = json.loads(first.stdout)
    assert first_payload["cache_hit"] is False

    completed = runner.invoke(
        app,
        [
            "complete-token-action",
            first_payload["action"]["action_id"],
            "--workspace",
            str(workspace_path),
            "--input-tokens",
            "600",
            "--output-tokens",
            "200",
            "--usage-source",
            "unavailable",
        ],
    )
    assert completed.exit_code == 0, completed.stdout

    cached = runner.invoke(
        app,
        ["token-preflight", "--workspace", str(workspace_path), "--from", str(request_path)],
    )
    assert cached.exit_code == 0
    assert json.loads(cached.stdout)["cache_hit"] is True
    assert "token-preflight" not in runner.invoke(app, ["--help"]).stdout
