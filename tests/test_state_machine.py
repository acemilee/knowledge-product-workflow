import pytest

from knowledge_product_workflow.models import ArtifactMetadata, ArtifactStatus
from knowledge_product_workflow.state_machine import InvalidTransitionError, transition


def test_approved_transition_requires_a_human_identity() -> None:
    metadata = ArtifactMetadata(
        book_id="ai-agent-foundations",
        artifact_type="product_brief",
        status=ArtifactStatus.READY_FOR_REVIEW,
        version=1,
    )

    with pytest.raises(InvalidTransitionError, match="审批人"):
        transition(metadata, ArtifactStatus.APPROVED)


def test_approved_transition_records_the_approver() -> None:
    metadata = ArtifactMetadata(
        book_id="ai-agent-foundations",
        artifact_type="product_brief",
        status=ArtifactStatus.READY_FOR_REVIEW,
        version=1,
    )

    result = transition(metadata, ArtifactStatus.APPROVED, approved_by="editor@example.com")

    assert result.status is ArtifactStatus.APPROVED
    assert result.approved_by == "editor@example.com"
