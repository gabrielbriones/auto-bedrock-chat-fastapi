"""Shared data models for auto-bedrock-chat-fastapi"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


@dataclass
class ChatCompletionResult:
    """Result from ChatManager.chat_completion().

    Encapsulates everything that happened during a single chat_completion call,
    including the full updated message history, the final AI response, all tool
    results collected across recursive tool call rounds, and metadata/stats.

    Attributes:
        messages: Full updated conversation history (all messages including
            system, user, assistant, and tool messages after processing).
        response: The final assistant response dict (the last assistant message).
        tool_results: All tool results collected across all tool call rounds.
            Each entry is a dict with keys 'tool_call_id', 'name', and
            either 'result' (on success) or 'error' (on failure).
        metadata: Stats and diagnostics from the completion run, e.g.:
            - 'tool_call_rounds': number of recursive tool call rounds executed
            - 'total_tool_calls': total number of individual tool calls made
            - 'preprocessing_applied': whether message preprocessing was triggered
            - 'context_window_retries': number of context-window error recoveries
            - 'fallback_model_used': whether the fallback model was used
    """

    messages: List[Dict[str, Any]]
    response: Dict[str, Any]
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Feedback models (XMGPLAT-10417 — Phase 2 Feedback Storage Backend)
# ---------------------------------------------------------------------------


class Rating(str, Enum):
    """User-supplied rating for an AI response."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    CORRECTION = "correction"


class ReviewStatus(str, Enum):
    """Expert-review state for a feedback entry."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


# Allowed status transitions for ``FeedbackStore.update_review``.
ALLOWED_REVIEW_TRANSITIONS: Dict[ReviewStatus, frozenset[ReviewStatus]] = {
    ReviewStatus.PENDING_REVIEW: frozenset({ReviewStatus.APPROVED, ReviewStatus.REJECTED}),
    ReviewStatus.APPROVED: frozenset({ReviewStatus.REJECTED}),
    ReviewStatus.REJECTED: frozenset({ReviewStatus.APPROVED}),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FeedbackEntry(BaseModel):
    """A single user-feedback record for an AI response.

    Mirrors the ``feedback`` table schema. Created by the WebSocket handler
    when a client submits a ``feedback`` message and persisted via
    :class:`FeedbackStore`.
    """

    model_config = ConfigDict(use_enum_values=False, validate_assignment=True)

    id: UUID = Field(default_factory=uuid4)
    session_id: str
    user_id: str

    # Original AI response context
    query: str
    ai_response: str

    # User input
    rating: Rating
    score: Optional[int] = Field(default=None, ge=1, le=5)
    correction_text: Optional[str] = None
    user_comment: Optional[str] = None

    # Provenance
    kb_sources_used: List[Dict[str, Any]] = Field(default_factory=list)
    model_id: str

    # Review workflow
    review_status: ReviewStatus = ReviewStatus.PENDING_REVIEW
    reviewer_id: Optional[str] = None
    reviewer_tags: List[str] = Field(default_factory=list)
    reviewer_comment: Optional[str] = None
    reviewed_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("correction_text", "user_comment", "reviewer_comment", "reviewer_id")
    @classmethod
    def _strip_optional_text(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("reviewer_tags")
    @classmethod
    def _strip_reviewer_tags(cls, v: List[str]) -> List[str]:
        # Strip whitespace and drop empty tags so the persisted TEXT[] never
        # contains blanks (mirrors the spirit of the DB CHECK constraints).
        return [t.strip() for t in v if t and t.strip()]

    @model_validator(mode="after")
    def _validate_rating_payload(self) -> "FeedbackEntry":
        # ``correction`` rating requires non-empty correction_text
        if self.rating == Rating.CORRECTION and not self.correction_text:
            raise ValueError("correction_text is required when rating is 'correction'")
        # ``positive`` ratings should not carry a correction
        if self.rating == Rating.POSITIVE and self.correction_text:
            raise ValueError("correction_text is only allowed for 'negative' or 'correction' ratings")
        return self

    @model_validator(mode="after")
    def _validate_review_fields(self) -> "FeedbackEntry":
        # If a review decision has been recorded, reviewer_id and reviewed_at
        # must be present together.
        decided = self.review_status in (ReviewStatus.APPROVED, ReviewStatus.REJECTED)
        if decided and (self.reviewer_id is None or self.reviewed_at is None):
            raise ValueError(
                "reviewer_id and reviewed_at are required when review_status is " "'approved' or 'rejected'"
            )
        return self


class FeedbackStats(BaseModel):
    """Aggregate counts for the feedback table."""

    model_config = ConfigDict(validate_assignment=True)

    total: int = 0
    by_status: Dict[ReviewStatus, int] = Field(default_factory=dict)
    by_rating: Dict[Rating, int] = Field(default_factory=dict)
