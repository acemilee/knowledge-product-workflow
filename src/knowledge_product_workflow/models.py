"""Shared contracts for workflow artifacts."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ArtifactStatus(StrEnum):
    """Lifecycle states allowed for a workflow artifact."""

    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    IN_PRODUCTION = "in_production"
    IN_VALIDATION = "in_validation"
    PUBLISHABLE = "publishable"
    DELIVERED = "delivered"


class ArtifactMetadata(BaseModel):
    """Frontmatter persisted with every Markdown workflow artifact."""

    book_id: str
    artifact_type: str
    status: ArtifactStatus
    version: int = Field(ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    depends_on: list[str] = Field(default_factory=list)
    approved_by: str | None = None
