"""Native usage summaries are observations, never account totals or authority."""
from types import SimpleNamespace

import pytest

from knowledge_product_workflow.production_controller import (
    NativeInlineBridge,
    NativeProductionSession,
)
from knowledge_product_workflow.production_delegated_host import DelegatedInlineBridge
from knowledge_product_workflow.production_host import NativeTaskLedger
from knowledge_product_workflow.workspace import Workspace


def usage(input_tokens=100, cached=80, output=20):
    return {"total": {"inputTokens": input_tokens, "cachedInputTokens": cached,
            "outputTokens": output, "reasoningOutputTokens": 5,
            "totalTokens": input_tokens + output},
            "last": {"inputTokens": 999999}, "irrelevant": "do not echo"}


def bound_session(tmp_path, kind):
    workspace = Workspace.create(tmp_path, "observed-usage")
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("status must not issue RPC or consume native events")

    client = SimpleNamespace(model_turns=2, request=forbidden, next_event=forbidden)
    if kind == "delegated":
        bridge = DelegatedInlineBridge(client, workspace=workspace, model="fixture", max_children=4)
        bridge.control_turn = "parent-turn"
        bridge._claims["task"] = {"thread": "child", "turn": "child-turn",
                                  "task": SimpleNamespace(role="writer")}
    else:
        bridge = NativeInlineBridge(client, model="fixture", max_children=4)
        bridge.ledger = NativeTaskLedger("parent", {"task": {"role": "writer"}})
        bridge.ledger.parent_turn_id = "parent-turn"
        bridge.ledger.children["child"] = {"task_id": "task", "released": False}
        bridge.ledger.running_turns["child"] = {"turn_id": "child-turn"}
    bridge.parent_id = "parent"
    return NativeProductionSession(workspace, bridge), calls


def consume(session, thread, turn, value):
    event = {"method": "thread/tokenUsage/updated", "params": {
        "threadId": thread, "turnId": turn, "tokenUsage": value}}
    if session.bridge.ledger is not None:
        session.bridge.ledger.consume(event)
    else:
        session.bridge._consume(event)


@pytest.mark.parametrize("kind", ["native", "delegated"])
def test_default_status_keeps_original_shape_and_does_not_build_usage(tmp_path, kind, monkeypatch):
    session, calls = bound_session(tmp_path, kind)

    def forbidden():
        pytest.fail("ordinary status must not build or print usage summaries")

    monkeypatch.setattr(session.bridge, "usage_observation", forbidden)
    result = session.apply({"op": "status"})
    assert set(result) == {"run_id", "model_turns_requested", "qualification", "active_children",
                           "execution_kind", "deployment"}
    assert not calls


@pytest.mark.parametrize("kind", ["native", "delegated"])
def test_status_reports_bound_native_totals_without_rpc_or_double_count(tmp_path, kind):
    session, calls = bound_session(tmp_path, kind)
    consume(session, "parent", "parent-turn", usage())
    consume(session, "child", "child-turn", usage(200, 150, 30))
    consume(session, "child", "child-turn", usage(200, 150, 30))
    consume(session, "foreign", "other", usage(900000, 0, 20))
    result = session.apply({"op": "status", "include_usage": True})["usage_observation"]
    assert result["all_bound_threads_observed"] is True
    assert result["roles"]["writer"]["observed_totals"]["input_tokens"] == 200
    assert result["roles"]["writer"]["observed_totals"]["uncached_input_tokens"] == 50
    assert result["roles"]["control"]["observed_totals"]["input_tokens"] == 100
    assert result["top_level_model_turn_requests"] == 2
    assert result["provider_request_count"] is None
    assert not calls
    result["roles"]["writer"]["observed_totals"]["input_tokens"] = -1
    assert session.apply({"op": "status", "include_usage": True})["usage_observation"]["roles"]["writer"]["observed_totals"]["input_tokens"] == 200


@pytest.mark.parametrize("kind", ["native", "delegated"])
def test_missing_stale_or_invalid_usage_is_unknown_not_zero(tmp_path, kind):
    session, calls = bound_session(tmp_path, kind)
    result = session.apply({"op": "status", "include_usage": True})["usage_observation"]
    assert result["all_bound_threads_observed"] is False
    assert result["roles"]["writer"]["observed_totals"]["input_tokens"] is None
    consume(session, "child", "old-turn", usage())
    result = session.apply({"op": "status", "include_usage": True})["usage_observation"]
    assert result["roles"]["writer"]["stale_turn_threads"] == 1
    assert result["roles"]["writer"]["observed_threads"] == 0
    consume(session, "child", "child-turn", usage(cached=101))
    result = session.apply({"op": "status", "include_usage": True})["usage_observation"]
    assert result["roles"]["writer"]["incompatible_threads"] == 1
    assert result["roles"]["writer"]["observed_totals"]["input_tokens"] is None
    assert not calls


@pytest.mark.parametrize("bad", [None, {}, {"total": None}, {"total": {"inputTokens": True}},
                                 usage(-1), usage(cached=101), usage(output=1),
                                 {"total": {**usage()["total"], "totalTokens": 2**63}},
                                 {"total": {**usage()["total"], "cacheWriteInputTokens": "0"}}])
def test_incompatible_payload_does_not_become_zero_or_raise(bad):
    from knowledge_product_workflow.production_host import summarize_native_usage

    result = summarize_native_usage({"child": bad}, {"child": "turn"},
        [("child", "writer", "turn")], top_level_model_turn_requests=1)
    assert not result["all_bound_threads_observed"]
    assert result["roles"]["writer"]["incompatible_threads"] == 1
    assert result["roles"]["writer"]["observed_totals"]["total_tokens"] is None


def test_partial_coverage_zeros_and_cache_write_are_separate():
    from knowledge_product_workflow.production_host import summarize_native_usage

    zero = {"total": dict.fromkeys(usage()["total"], 0)}
    result = summarize_native_usage({"first": zero}, {"first": "one"},
        [("first", "writer", "one"), ("second", "writer", "two")],
        top_level_model_turn_requests=None)
    role = result["roles"]["writer"]
    assert not result["all_bound_threads_observed"]
    assert role["expected_threads"] == 2 and role["observed_threads"] == 1
    assert role["missing_threads"] == 1
    assert role["observed_totals"]["input_tokens"] == 0  # A real reported zero, not missing data.
    assert role["observed_totals"]["cache_write_input_tokens"] is None
    assert result["top_level_model_turn_requests"] is None
    zero["total"]["cacheWriteInputTokens"] = 0
    result = summarize_native_usage({"first": zero}, {"first": "one"},
        [("first", "writer", "one")], top_level_model_turn_requests=0)
    assert result["roles"]["writer"]["observed_totals"]["cache_write_input_tokens"] == 0


def test_conflicting_scopes_do_not_double_count_or_claim_full_coverage():
    from knowledge_product_workflow.production_host import summarize_native_usage

    result = summarize_native_usage({"first": usage()}, {"first": "one"},
        [("first", "writer", "one"), ("first", "semantic", "one")],
        top_level_model_turn_requests=1)
    assert not result["all_bound_threads_observed"]
    assert result["roles"]["writer"]["incompatible_threads"] == 1
    assert result["roles"]["writer"]["observed_totals"]["input_tokens"] is None


def test_summary_size_is_bounded_and_never_echoes_raw_fields():
    import json

    from knowledge_product_workflow.production_host import summarize_native_usage

    raw = usage()
    raw["irrelevant"] = "PRIVATE-BODY-" * 20000
    raw["total"]["api_key"] = "PRIVATE-CREDENTIAL-MARKER"
    scopes = [(f"thread-{n}", "writer", "turn") for n in range(1000)]
    result = summarize_native_usage({identity: raw for identity, _, _ in scopes},
        {identity: "turn" for identity, _, _ in scopes}, scopes, top_level_model_turn_requests=1001)
    encoded = json.dumps(result)
    assert len(encoded.encode()) < 8192
    assert "PRIVATE" not in encoded and "thread-" not in encoded
    assert result["roles"]["writer"]["observed_totals"]["input_tokens"] == 100000


@pytest.mark.parametrize("kind", ["native", "delegated"])
def test_new_snapshot_replaces_old_and_status_does_not_change_lifecycle(tmp_path, kind):
    session, calls = bound_session(tmp_path, kind)
    if kind == "native":
        before = session.bridge.ledger.active_children.copy()
    else:
        before = session.bridge._claims.copy()
    consume(session, "child", "child-turn", usage())
    consume(session, "child", "child-turn", usage(200, 150, 30))
    for _ in range(3):
        result = session.apply({"op": "status", "include_usage": True})
        assert result["usage_observation"]["roles"]["writer"]["observed_totals"]["input_tokens"] == 200
        assert result["qualification"] == "pending"
    assert not calls and session.bridge.client.model_turns == 2
    if kind == "native":
        assert session.bridge.ledger.active_children == before
    else:
        assert session.bridge._claims == before
