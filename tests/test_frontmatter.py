from knowledge_product_workflow.frontmatter import (
    parse_markdown_artifact,
    render_markdown_artifact,
)
from knowledge_product_workflow.models import ArtifactMetadata, ArtifactStatus


def test_frontmatter_round_trip_preserves_metadata_and_body() -> None:
    metadata = ArtifactMetadata(
        book_id="ai-agent-foundations",
        artifact_type="product_brief",
        status=ArtifactStatus.DRAFT,
        version=1,
    )

    rendered = render_markdown_artifact(metadata, "# AI Agent Foundations\n")
    parsed, body = parse_markdown_artifact(rendered)

    assert parsed.book_id == "ai-agent-foundations"
    assert parsed.status is ArtifactStatus.DRAFT
    assert body == "# AI Agent Foundations\n"
