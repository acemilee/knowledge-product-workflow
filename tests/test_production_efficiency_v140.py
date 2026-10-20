"""Cheap rejection precedes whole-book state capture; no production fixtures."""

import hashlib

import pytest
import yaml
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from tests.test_chapter_content_plan import _approved_workspace


@pytest.mark.parametrize("invalid", ["missing", "enum", "outside"])
def test_invalid_plan_is_rejected_before_state_capture_and_without_side_effects(
    tmp_path, monkeypatch, invalid
):
    from knowledge_product_workflow import runtime

    w = _approved_workspace(tmp_path)
    path = w.root / "runs/plan-input.yaml"
    if invalid == "enum":
        path.write_text(
            yaml.safe_dump(
                {
                    "sections": [
                        {
                            "section_id": "1.1",
                            "opening_mode": "comparison",
                            "reader_job": "根据当前输入作判断",
                            "block_sequence": ["explanation"],
                            "asset_decision": "none",
                            "asset_rationale": "解释已足够",
                        }
                    ]
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
    elif invalid == "outside":
        path = tmp_path / "outside.yaml"
        path.write_text("sections: []", encoding="utf-8")
    before = {
        p.relative_to(w.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in w.root.rglob("*")
        if p.is_file()
    }
    calls = []
    original = runtime.inspect_workflow

    def inspect(workspace):
        calls.append(workspace.root)
        return original(workspace)

    monkeypatch.setattr(runtime, "inspect_workflow", inspect)
    result = CliRunner().invoke(
        app, ["submit-content-plan", "1", "--workspace", str(w.root), "--from", str(path)]
    )
    assert result.exit_code == 1, result.output
    assert not calls, "invalid input paid for deep workflow inspection"
    assert before == {
        p.relative_to(w.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in w.root.rglob("*")
        if p.is_file()
    }


def test_preflight_cannot_write_a_request_changed_during_state_capture(tmp_path, monkeypatch):
    from knowledge_product_workflow import cli
    from tests.test_chapter_content_plan import _valid_plan

    w = _approved_workspace(tmp_path)
    path = w.root / "runs/plan-input.yaml"
    raw = _valid_plan(w)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    original = cli.capture_orchestration_update

    def change(workspace):
        snapshot = original(workspace)
        raw["sections"][0]["reader_job"] += "，这是另一个输入版本"
        path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        return snapshot

    monkeypatch.setattr(cli, "capture_orchestration_update", change)
    result = CliRunner().invoke(
        app, ["submit-content-plan", "1", "--workspace", str(w.root), "--from", str(path)]
    )
    assert result.exit_code == 1
    assert "预检后改变" in result.output
    assert not (w.root / "chapters/chapter-01-content-plan.yaml").exists()


@pytest.mark.parametrize(
    "command",
    ["submit-chapter", "humanize-check", "developmental-check", "record-developmental-review"],
)
def test_other_missing_editor_inputs_are_also_cheap_and_readonly(tmp_path, monkeypatch, command):
    from knowledge_product_workflow import runtime

    w = _approved_workspace(tmp_path)
    missing = w.root / "runs/missing.md"
    argv = [command, "1", "--workspace", str(w.root)]
    if command == "record-developmental-review":
        argv += ["--chapter-source", str(missing), "--from", str(w.root / "runs/missing.json")]
    else:
        argv += ["--from", str(missing)]
        if command == "submit-chapter":
            argv += ["--compliance", str(w.root / "runs/missing.json")]
    before = {p: p.read_bytes() for p in w.root.rglob("*") if p.is_file()}
    calls = []
    original = runtime.inspect_workflow

    def inspect(workspace):
        calls.append(workspace.root)
        return original(workspace)

    monkeypatch.setattr(runtime, "inspect_workflow", inspect)
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 1
    assert calls == []
    assert before == {p: p.read_bytes() for p in w.root.rglob("*") if p.is_file()}
