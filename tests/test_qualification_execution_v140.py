"""Qualification alone may use an owned executor; data cannot grant that scope."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from knowledge_product_workflow import production_host as host
from tests.test_production_native_replay_v140 import (
    activity,
    completed_state,
    control_bridge,
    ledger,
    release_bridge,
    turn,
)


def test_executor_bearer_token_is_redacted_at_every_nesting():
    secret = "owned-test-capability"
    value = {"params": {"authBearerToken": secret, "auth_bearer_token": secret}}
    assert secret not in json.dumps(host.redacted_protocol(value))


def test_generated_executor_secret_is_scrubbed_even_in_reflected_error():
    secret = "owned-test-capability"
    value = {"message": 'invalid value authBearerToken="' + secret + '"'}
    assert secret not in json.dumps(host.redacted_protocol(value, secret_values=(secret,)))


def test_missing_qualification_executor_stops_before_claim_or_model(tmp_path):
    bridge, calls = control_bridge(tmp_path)
    bridge.create_parent()
    with pytest.raises(host.NativeProtocolError, match="资格.*执行环境"):
        bridge.dispatch_qualification(count=1)
    assert not bridge._claimed_tasks
    assert all(method != "turn/start" for method, _ in calls)


def denial_state():
    state = ledger(1)
    state.expected_tasks["0"].update(role="qualification", permission_probe_commands={"read": "Get-Content fixed"})
    state.consume(activity("0"))
    state.consume(turn("0", "started"))
    return state


def wrapped_denial(**updates):
    item = {"type": "commandExecution", "id": "deny-read", "command": '"pwsh.exe" -NoProfile -Command "Get-Content fixed"',
            "commandActions": [{"type": "unknown", "command": "Get-Content fixed"}],
            "status": "failed", "exitCode": 1, "aggregatedOutput": "Permission denied"}
    item.update(updates)
    return {"method": "item/completed", "params": {"threadId": "0", "turnId": "turn-0", "item": item}}


def test_native_wrapped_command_uses_exact_action_binding():
    state = denial_state()
    state.consume(wrapped_denial())
    assert state.permission_denials["0"] == {"read": "deny-read"}


def test_native_windows_socket_access_denied_uses_error_code_not_message_language():
    state = denial_state()
    state.consume(wrapped_denial(aggregatedOutput="PermissionError: [WinError 10013] 本机拒绝该网络操作"))
    assert state.permission_denials["0"] == {"read": "deny-read"}


@pytest.mark.parametrize("change", [
    {"commandActions": [{"type": "unknown", "command": "echo Get-Content fixed"}]},
    {"commandActions": [{"type": "unknown", "command": "Get-Content fixed"}, {"type": "unknown", "command": "extra"}]},
    {"commandActions": [{"type": "unknown", "command": "Get-Content fixed"}], "status": "completed", "exitCode": 0},
    {"aggregatedOutput": "The file does not exist"},
    {"aggregatedOutput": "Connection refused"},
    {"aggregatedOutput": "ConnectionRefusedError: [WinError 10061] connection refused"},
])
def test_unbound_or_nonpermission_failure_never_grants_denial(change):
    state = denial_state()
    state.consume(wrapped_denial(**change))
    assert not state.permission_denials.get("0")


def test_only_real_owned_host_can_be_attached(tmp_path):
    bridge, _ = control_bridge(tmp_path)
    with pytest.raises(host.NativeProtocolError):
        bridge.bind_qualification_execution({"ready": True})


def test_owned_host_paths_must_be_inside_project(tmp_path):
    factory = getattr(host, "NativeQualificationHost", None)
    assert factory is not None
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"not-executed-fixture")
    shell = tmp_path / "runtime/pwsh.exe"
    shell.parent.mkdir()
    shell.write_bytes(b"not-executed-shell")
    with pytest.raises(host.NativeProtocolError, match="工作区"):
        factory(binary, cwd=tmp_path, private_home=tmp_path.parent / "outside-home",
                shell_executable=shell, native_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                model_turn_limit=0, max_children=6)


def test_ordinary_dispatch_explicitly_resets_environment_and_readonly_policy(tmp_path):
    bridge, calls = control_bridge(tmp_path)
    bridge.create_parent()
    bridge.dispatch([{"task_id": "writer", "role": "writer", "candidate_version": 1, "input_manifest_sha256": "a" * 64}])
    params = calls[-1][1]
    assert params["environments"] == []
    assert params["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}


def test_qualification_scope_never_steers_into_an_ordinary_running_turn(tmp_path):
    bridge, calls = control_bridge(tmp_path)
    bridge.create_parent()
    bridge.dispatch([{"task_id": "writer", "role": "writer", "candidate_version": 1, "input_manifest_sha256": "a" * 64}])
    bridge._qualification_execution = SimpleNamespace(parameters=lambda client: {"environments": [], "permissions": "fixture"})
    before = len(calls)
    with pytest.raises(host.NativeProtocolError):
        bridge.dispatch_qualification(count=1)
    assert len(calls) == before


def test_qualification_uses_only_controlled_scope_and_binds_positive_probe(tmp_path):
    bridge, calls = control_bridge(tmp_path, qualification=True)
    bridge.create_parent()
    assert "positive_probe_commands" in calls[0][1]["baseInstructions"]
    assert "positive_probe_commands" in calls[0][1]["developerInstructions"]
    bridge.dispatch_qualification(count=1)
    params = calls[-1][1]
    assert params["environments"][0]["environmentId"] == "fixture"
    assert params["permissions"] == "fixture" and "sandboxPolicy" not in params
    prompt = params["input"][0]["text"]
    task = json.loads(prompt.split("任务数据：\n", 1)[1])[0]
    assert task["positive_probe_commands"] == {"allowed_read": "fixture-allowed_read"}
    assert "tools.exec_command" in prompt
    assert task["qualification_control"]["execution_scope_sha256"]


def test_finished_parent_without_child_receipt_cannot_switch_scope(tmp_path):
    bridge, calls = control_bridge(tmp_path, qualification=True)
    bridge.create_parent()
    bridge.dispatch_qualification(count=1)
    bridge.ledger.parent_status = "completed"
    before = len(calls)
    with pytest.raises(host.NativeProtocolError, match="切换"):
        bridge.dispatch([{"task_id": "writer", "role": "writer", "candidate_version": 1,
                         "input_manifest_sha256": "a" * 64}])
    assert len(calls) == before and "writer" not in bridge._claimed_tasks


@pytest.mark.parametrize("exit_code,output", [(False, "fixture-marker"), (1, "fixture-marker"), (0, "other-marker")])
def test_positive_probe_requires_typed_zero_exit_and_exact_marker(exit_code, output):
    state = denial_state()
    state.expected_tasks["0"].update(positive_probe_commands={"allowed_read": "fixed-positive"},
                                     positive_probe_marker="fixture-marker")
    frame = wrapped_denial(commandActions=[{"type": "unknown", "command": "fixed-positive"}],
                           status="completed", exitCode=exit_code, aggregatedOutput=output)
    state.consume(frame)
    assert not state.permission_positive_controls.get("0")


def test_positive_probe_records_native_bound_success():
    state = denial_state()
    state.expected_tasks["0"].update(positive_probe_commands={"allowed_read": "fixed-positive"},
                                     positive_probe_marker="fixture-marker")
    state.consume(wrapped_denial(commandActions=[{"type": "unknown", "command": "fixed-positive"}],
                                 status="completed", exitCode=0, aggregatedOutput="owned warning\nfixture-marker\n"))
    assert state.permission_positive_controls["0"] == {"allowed_read": "deny-read"}


@pytest.mark.parametrize("flag", ["--qualification-shell", "--qualification-python"])
def test_cli_incomplete_qualification_pair_stops_before_process(tmp_path, flag):
    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app
    from knowledge_product_workflow.workspace import Workspace

    workspace = Workspace.create(tmp_path, "qualified-pair")
    result = CliRunner().invoke(app, ["production-host-session", "--workspace", str(workspace.root),
        "--binary", str(tmp_path / "must-not-start.exe"), "--expected-sha256", "a" * 64,
        "--model", "fixture", flag, str(tmp_path / "runtime.exe")])
    assert result.exit_code == 1
    assert "同时指定" in result.output


def test_qualification_release_uses_current_bound_scope_not_empty_environment():
    state = completed_state()
    scope = {"environments": [{"environmentId": "owned", "cwd": "owned", "runtimeWorkspaceRoots": []}],
             "permissions": "readonly", "runtimeWorkspaceRoots": []}
    state.expected_tasks["0"].update(role="qualification", qualification_control={
        "execution_scope_sha256": hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()})
    bridge, _ = release_bridge(state, thread={"environments": scope["environments"]})
    bridge._qualification_execution = SimpleNamespace(parameters=lambda client: scope)
    assert bridge.reconcile_execution_releases() == ["0"]


def test_plain_role_cannot_release_with_qualification_environment():
    state = completed_state()
    bridge, _ = release_bridge(state, thread={"environments": [{"environmentId": "owned"}]})
    with pytest.raises(host.NativeProtocolError, match="隔离归属"):
        bridge.reconcile_execution_releases()
    assert state.active_children == {"0"}


def test_verified_receipt_uses_scope_from_live_release_snapshot():
    state = completed_state()
    bridge, _ = release_bridge(state)
    assert state.thread_scopes.get("0") is None  # Native spawn need not emit thread/started.
    assert bridge.reconcile_execution_releases() == ["0"]
    assert bridge.verified_receipts()[0]["scope_verified"] is True


def test_completed_idle_child_missing_final_fails_without_wait_or_release():
    state = completed_state()
    state.children["0"]["message"] = None
    bridge, _ = release_bridge(state)
    with pytest.raises(host.NativeProtocolError, match="缺少.*final"):
        bridge.reconcile_execution_releases()
    assert state.active_children == {"0"}


def test_owned_executor_rpc_checks_connection_liveness_and_one_registration(tmp_path):
    owner = host.NativeQualificationHost.__new__(host.NativeQualificationHost)
    client = SimpleNamespace(native_origin=True, _blocked=False, model_turns=0,
                             command=["frozen"], process=SimpleNamespace(poll=lambda: None))
    owner.client, owner.executor = client, SimpleNamespace(poll=lambda: None)
    owner._closed, owner._registration_sent = False, False
    owner._command_identity, owner.profile = ("frozen",), "owned"
    owner._endpoint, owner._token = "ws://127.0.0.1:1", "fixture-secret"
    owner._identity_paths = (tmp_path.resolve(),)
    params = {"environmentId": "owned", "execServerUrl": owner._endpoint,
              "authBearerToken": owner._token, "connectTimeoutMs": 5000}
    assert not owner.admit_rpc(SimpleNamespace(), "environment/add", params)
    assert not owner.admit_rpc(client, "environment/add", {**params, "execServerUrl": "ws://external:1"})
    assert owner.admit_rpc(client, "environment/add", params)
    assert not owner.admit_rpc(client, "environment/add", params)
    assert not owner.admit_rpc(client, "environment/info", {"environmentId": "other"})
    assert owner.admit_rpc(client, "environment/info", {"environmentId": "owned"})
    owner.executor = SimpleNamespace(poll=lambda: 1)
    assert not owner.admit_rpc(client, "environment/status", {"environmentId": "owned"})


def owned_constructor(tmp_path, *, runtime_covers_home=False):
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"not-executed-original-native")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    shell, python = runtime / "pwsh.exe", runtime / "python.exe"
    shell.write_bytes(b"not-executed-shell")
    python.write_bytes(b"not-executed-python")
    return host.NativeQualificationHost(binary, cwd=tmp_path,
        private_home=(runtime if runtime_covers_home else tmp_path) / "private-home",
        shell_executable=shell, python_executable=python,
        native_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        model_turn_limit=0, max_children=6)


def test_runtime_read_root_cannot_include_protected_home(tmp_path):
    with pytest.raises(host.NativeProtocolError, match="覆盖.*保护区"):
        owned_constructor(tmp_path, runtime_covers_home=True)


def test_binary_changed_after_construction_is_rejected_before_any_process(tmp_path, monkeypatch):
    owner = owned_constructor(tmp_path)
    owner.binary.write_bytes(b"changed-native")
    monkeypatch.setattr(host.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not start native"))
    with pytest.raises(host.NativeProtocolError, match="二进制.*变化"):
        owner.__enter__()
    assert not owner.root.exists()


def test_path_rebinding_revokes_parameters_before_liveness_can_authorize(tmp_path, monkeypatch):
    owner = owned_constructor(tmp_path)
    owner._ready = True
    owner._closed = False
    client = SimpleNamespace(native_origin=True, _blocked=False, command=owner.command,
                             process=SimpleNamespace(poll=lambda: None))
    owner.client, owner.executor = client, SimpleNamespace(poll=lambda: None)
    original = host.Path.resolve

    def rebound(path, *args, **kwargs):
        return tmp_path / "redirected-home" if path == owner.home else original(path, *args, **kwargs)

    monkeypatch.setattr(host.Path, "resolve", rebound)
    with pytest.raises(host.NativeProtocolError, match="活连接"):
        owner.parameters(client)


def test_owned_cleanup_keeps_executor_and_secret_cleanup_when_client_close_fails():
    import threading

    owner = host.NativeQualificationHost.__new__(host.NativeQualificationHost)
    calls = []

    def fail():
        raise OSError("owned close failed")

    owner._stop, owner._threads = threading.Event(), []
    owner._token = "fixture-secret"
    owner.client = SimpleNamespace(_login_state="not_started", close=fail)
    owner.executor = SimpleNamespace(poll=lambda: None, terminate=lambda: calls.append("terminated"),
        wait=lambda **_: calls.append("waited"), stdin=None, stdout=None, stderr=None)
    owner._listener = SimpleNamespace(close=lambda: calls.append("listener_closed"))
    with pytest.raises(OSError, match="owned close failed"):
        owner.close()
    assert owner._token is None and owner._closed and not owner._ready
    assert calls == ["terminated", "waited", "listener_closed"]
