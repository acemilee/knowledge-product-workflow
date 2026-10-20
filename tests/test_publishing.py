from pathlib import Path

import pytest
import yaml

from knowledge_product_workflow.publishing import (
    create_delivery_manifest,
    delivery_manifest_issues,
    workspace_relative_path,
)
from knowledge_product_workflow.workflows.validation import run_delivery_validation
from knowledge_product_workflow.workspace import Workspace


def test_manifest_records_sha256_for_markdown_and_pdf(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "ai-agent-foundations")
    markdown_path = workspace.root / "delivery" / "book.md"
    pdf_path = workspace.root / "delivery" / "book.pdf"
    markdown_path.write_text("# Book\n", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF-test")
    run_delivery_validation(workspace)

    manifest_path = create_delivery_manifest(workspace, markdown_path, pdf_path)

    manifest = manifest_path.read_text(encoding="utf-8")
    payload = yaml.safe_load(manifest)
    assert payload["schema_version"] == 2
    assert {item["path"] for item in payload["artifacts"]} == {"delivery/book.md", "delivery/book.pdf"}
    assert "sha256" in manifest
    assert "review_proofs: []" in manifest
    assert delivery_manifest_issues(workspace) == []

    markdown_path.write_text("# Changed Book\n", encoding="utf-8")

    assert delivery_manifest_issues(workspace) == ["交付清单输入已过期：delivery/book.md"]


def test_manifest_rejects_legacy_schema_and_workspace_path_escape(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    manifest_path = workspace.root / "delivery" / "delivery-manifest.yaml"
    manifest_path.write_text("schema_version: 1\nartifacts: []\n", encoding="utf-8")

    assert delivery_manifest_issues(workspace) == ["交付清单版本过旧；请运行 kp publish 受控重建"]
    with pytest.raises(ValueError, match="工作区之外"):
        workspace_relative_path(tmp_path / "outside.md", workspace)


def test_manifest_refuses_a_blocking_quality_report(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "ai-agent-foundations")
    markdown_path = workspace.root / "delivery" / "book.md"
    pdf_path = workspace.root / "delivery" / "book.pdf"
    markdown_path.write_text("# Book\n\n{{TODO}}\n", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF-test")
    run_delivery_validation(workspace)

    with pytest.raises(ValueError, match="质量报告未通过"):
        create_delivery_manifest(workspace, markdown_path, pdf_path)
