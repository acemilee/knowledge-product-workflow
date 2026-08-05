"""Explicit lifecycle transitions for knowledge product artifacts."""

from datetime import UTC, datetime

from knowledge_product_workflow.models import ArtifactMetadata, ArtifactStatus


class InvalidTransitionError(ValueError):
    """Raised when an artifact attempts an illegal lifecycle transition."""


ALLOWED_TRANSITIONS: dict[ArtifactStatus, set[ArtifactStatus]] = {
    ArtifactStatus.DRAFT: {ArtifactStatus.READY_FOR_REVIEW},
    ArtifactStatus.READY_FOR_REVIEW: {ArtifactStatus.APPROVED, ArtifactStatus.DRAFT},
    ArtifactStatus.APPROVED: {ArtifactStatus.IN_PRODUCTION},
    ArtifactStatus.IN_PRODUCTION: {ArtifactStatus.IN_VALIDATION},
    ArtifactStatus.IN_VALIDATION: {ArtifactStatus.IN_PRODUCTION, ArtifactStatus.PUBLISHABLE},
    ArtifactStatus.PUBLISHABLE: {ArtifactStatus.DELIVERED},
    ArtifactStatus.DELIVERED: set(),
}


def transition(
    metadata: ArtifactMetadata,
    target: ArtifactStatus,
    approved_by: str | None = None,
) -> ArtifactMetadata:
    """Return a new metadata value after validating the requested transition."""
    if target not in ALLOWED_TRANSITIONS[metadata.status]:
        raise InvalidTransitionError(f"不允许将工件状态从 {metadata.status} 变更为 {target}")
    if target is ArtifactStatus.APPROVED and not approved_by:
        raise InvalidTransitionError("审批时必须提供审批人标识")
    return metadata.model_copy(
        update={
            "status": target,
            "approved_by": approved_by if target is ArtifactStatus.APPROVED else metadata.approved_by,
            "updated_at": datetime.now(UTC),
        }
    )
