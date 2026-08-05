from pathlib import Path

import yaml


def test_module_catalog_exposes_all_v1_workflow_stages() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((root / "module" / "module.yaml").read_text(encoding="utf-8"))
    catalog = (root / "module" / "module-help.csv").read_text(encoding="utf-8")

    assert manifest["code"] == "kp"
    assert {agent["code"] for agent in manifest["agents"]} >= {
        "kp-product-strategist",
        "kp-curriculum-architect",
        "kp-research-verifier",
        "kp-chapter-writer",
        "kp-quality-editor",
        "kp-publisher",
    }
    for workflow in ("kp-brief", "kp-outline", "kp-research", "kp-author", "kp-validate", "kp-publish"):
        assert workflow in catalog
