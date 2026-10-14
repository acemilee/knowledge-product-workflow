"""移除文本额度门，同时保留内容与成页审核。"""

from pathlib import Path

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.quality import quality_report_inputs
from knowledge_product_workflow.workspace import Workspace


def test_new_workspace_does_not_create_text_budget(tmp_path):
    workspace = Workspace.create(tmp_path, "no-text-budget")
    assert not (workspace.root / "runs/token-budget.json").exists()
    assert (workspace.root / "runs/orchestration").is_dir()


def test_text_budget_commands_are_removed():
    retired = {"token-action-schema", "budget-amendment-schema", "freeze-token-budget",
               "amend-token-budget", "token-preflight", "complete-token-action",
               "cancel-token-action", "fail-token-action", "token-budget-status"}
    assert not retired.intersection(command.name for command in app.registered_commands)


def test_legacy_text_budget_is_not_a_quality_dependency(tmp_path):
    workspace = Workspace.create(tmp_path, "legacy-text-budget")
    path = workspace.root / "runs/token-budget.json"
    path.write_text("invalid historical budget", encoding="utf-8")
    before = path.read_bytes()
    inputs = quality_report_inputs(workspace, "delivery")
    assert not any(item.path.startswith("runs/token-budget.json") for item in inputs)
    assert path.read_bytes() == before


def test_skill_contract_does_not_restore_text_budget_gate():
    package = Path(__file__).parents[1] / "integrations/cc-switch"
    for path in [package / "references/orchestration-contract.md", *package.glob("skills/*/SKILL.md")]:
        instructions = path.read_text(encoding="utf-8")
        assert "amend-token-budget" not in instructions
        assert "freeze-token-budget" not in instructions
        assert "总额硬门" not in instructions
        assert "文本观察独立预约记账" not in instructions
        assert "另计文本预算" not in instructions
