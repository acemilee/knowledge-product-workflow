import json

from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.runtime import _read_orchestration_state, inspect_workflow
from knowledge_product_workflow.workspace import Workspace


def test_first_status_exposes_actual_fallback_and_legacy_mode(tmp_path):
    current = Workspace.create(tmp_path, "new", current_policy=True)
    old = Workspace.create(tmp_path, "old")
    assert _read_orchestration_state(current).production_mode == "adaptive"
    assert _read_orchestration_state(old).production_mode == "serial"
    status = inspect_workflow(current)
    assert status.production_overview["qualification"] == "pending"
    assert status.production_overview["effective_mode"] == "serial"
    assert status.production_overview["effective_parallelism"] == 1


def test_first_command_discovery_explains_controls_and_keeps_repeatable_claims():
    runner = CliRunner()
    for command in ("production-host-connect", "production-host-replay", "production-host-session", "production-plan", "production-start", "production-freeze", "production-evaluate", "production-structure-result", "production-commit", "production-recover", "production-recover-lock", "production-revise", "production-shared-next", "production-shared-complete"):
        result = runner.invoke(app, ["commands", "--all", "--json", "--command", command])
        assert result.exit_code == 0
        entry = json.loads(result.output)["commands"][0]
        assert entry["command"] == command and entry["required_role"].startswith("kp-")
        assert entry["purpose"]
    result = runner.invoke(app, ["commands", "--all", "--json", "--command", "research"])
    params = json.loads(result.output)["commands"][0]["parameters"]
    assert next(p for p in params if p["name"] == "claim")["multiple"] is True


def test_native_start_fails_before_shared_write_and_plan_is_readonly(tmp_path):
    from tests.test_chapter_content_plan import _approved_workspace
    workspace = _approved_workspace(tmp_path)
    before = {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    runner = CliRunner()
    result = runner.invoke(app, ["production-plan", "--workspace", str(workspace.root), "--json"])
    assert result.exit_code == 0 and json.loads(result.output)["native_parallel_enabled"] is False
    result = runner.invoke(app, ["production-start", "--workspace", str(workspace.root), "--parallelism", "6"])
    assert result.exit_code != 0 and "资格未通过" in result.output
    assert before == {p: p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}


def test_repository_skill_first_guidance_contains_dependencies_and_scope():
    from tests.test_cc_switch_package import PACKAGE_ROOT
    contract = (PACKAGE_ROOT / "references/orchestration-contract.md").read_text(encoding="utf-8")
    assert "production-plan" in contract and "result" in contract and "interface" in contract
    assert "pending" in contract and "串行" in contract and "全部同时" in contract
    assert "--claim" in contract and "refresh_actions" in contract


def test_host_session_discovery_exposes_safe_auth_without_changing_review_default():
    result=CliRunner().invoke(app,["commands","--all","--json","--command","production-host-session"])
    assert result.exit_code==0
    entry=json.loads(result.output)["commands"][0]
    assert "auth-start" in entry["purpose"] and "内存认证" in entry["purpose"]
