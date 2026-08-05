from pathlib import Path

from knowledge_product_workflow.workspace import Workspace


def test_create_initializes_all_canonical_directories(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "ai-agent-foundations")

    assert workspace.root == (tmp_path / "ai-agent-foundations").resolve()
    assert (workspace.root / "planning").is_dir()
    assert (workspace.root / "research").is_dir()
    assert (workspace.root / "chapters").is_dir()
    assert (workspace.root / "delivery").is_dir()
    assert (workspace.root / "runs" / "events.jsonl").is_file()
    assert (workspace.root / "runs" / "orchestration" / "state.json").is_file()
    assert list((workspace.root / "runs" / "orchestration" / "work-orders").glob("*.json"))
    assert "workspace_initialized" in (workspace.root / "runs" / "events.jsonl").read_text()


def test_open_normalizes_a_relative_workspace_path_from_a_nested_delivery_directory(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = Workspace.create(tmp_path / "output", "guide")
    monkeypatch.chdir(workspace.root / "delivery")

    reopened = Workspace.open(Path("output") / "guide")

    assert reopened.root == workspace.root


def test_approval_identity_is_persisted_in_the_workspace_run_metadata(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "ai-agent-foundations")

    stored = workspace.configure_approval_identity("内容负责人")

    assert stored == "内容负责人"
    assert workspace.approval_identity() == "内容负责人"
    assert (workspace.root / "runs" / "approval-profile.json").is_file()
    assert "approval_identity_configured" in (workspace.root / "runs" / "events.jsonl").read_text(
        encoding="utf-8"
    )
