"""YAML frontmatter serialization for Markdown workflow artifacts."""

from __future__ import annotations

import yaml

from knowledge_product_workflow.models import ArtifactMetadata
from knowledge_product_workflow.verification_context import (
    load_yaml as _v_load_yaml,
)


def render_markdown_artifact(metadata: ArtifactMetadata, body: str) -> str:
    """Render metadata and Markdown content into one portable artifact."""
    data = metadata.model_dump(mode="json")
    return f"---\n{yaml.safe_dump(data, sort_keys=False)}---\n{body}"


def parse_markdown_artifact(text: str) -> tuple[ArtifactMetadata, str]:
    """Parse one artifact and reject Markdown without valid frontmatter."""
    if not text.startswith("---\n"):
        raise ValueError("Markdown artifact must start with YAML frontmatter")
    parts = text.split("---\n", maxsplit=2)
    if len(parts) != 3:
        raise ValueError("Markdown artifact frontmatter is not closed")
    _, raw_metadata, body = parts
    return ArtifactMetadata.model_validate(_v_load_yaml(raw_metadata)), body
