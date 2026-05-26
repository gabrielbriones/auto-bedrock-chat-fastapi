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
    """User-supplied rating for an AI response.

    Binary sentiment. Whether the user proposed a fix is an orthogonal
    signal carried by the optional ``correction_text`` field on
    :class:`FeedbackEntry` (only valid when ``rating == NEGATIVE``).
    """

    POSITIVE = "positive"
    NEGATIVE = "negative"


class ReviewStatus(str, Enum):
    """Expert-review state for a feedback entry."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


# Allowed status transitions for ``FeedbackStore.update_review``.
#
# Decided entries (approved / rejected) may be updated to *either* decided
# state — including staying in the same state — so that admins can correct
# mistakes such as a wrong decision, tags, or comment.
#
# The only permanently forbidden target is ``pending_review``: once a
# decision has been recorded the entry cannot be reset to the review queue.
ALLOWED_REVIEW_TRANSITIONS: Dict[ReviewStatus, frozenset[ReviewStatus]] = {
    ReviewStatus.PENDING_REVIEW: frozenset({ReviewStatus.APPROVED, ReviewStatus.REJECTED}),
    ReviewStatus.APPROVED: frozenset({ReviewStatus.APPROVED, ReviewStatus.REJECTED}),
    ReviewStatus.REJECTED: frozenset({ReviewStatus.APPROVED, ReviewStatus.REJECTED}),
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
    # Required, non-empty. Anonymous submissions (when the authorizer is
    # configured with ``allow_anonymous=True``) are stamped with the
    # ``"anonymous"`` sentinel by the WebSocket handler so audit queries
    # can distinguish "we have no identity" from a real user_id.
    user_id: str = Field(min_length=1)

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

    @field_validator("rating", mode="before")
    @classmethod
    def _coerce_legacy_correction_rating(cls, v: Any) -> Any:
        # Backwards-compat: the ``Rating`` enum used to include a third
        # value ``"correction"`` that was retired in favor of the
        # orthogonal ``correction_text`` field. Pre-existing rows in
        # long-lived dev databases may still carry that string value.
        # Coerce on read so hydration doesn't explode; the
        # ``_migrate_legacy_correction_rows`` step in both store
        # backends rewrites the rows in place on startup.
        if isinstance(v, str) and v == "correction":
            return Rating.NEGATIVE.value
        return v

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
        # A correction is a proposed fix to the AI's answer — only
        # meaningful for negative feedback. Anything else carrying
        # ``correction_text`` indicates a malformed payload.
        if self.correction_text and self.rating != Rating.NEGATIVE:
            raise ValueError("correction_text is only allowed when rating is 'negative'")
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


class TagCount(BaseModel):
    """A single ``(tag, count)`` pair used in :class:`FeedbackStats.top_tags`."""

    model_config = ConfigDict(validate_assignment=True)

    tag: str
    count: int = Field(ge=0)


class FeedbackStats(BaseModel):
    """Aggregate counts for the feedback table.

    ``top_tags``, ``oldest_pending_hours``, and ``with_correction`` are
    extended fields populated by the admin review API (XMGPLAT-10417,
    T2.2). They default to safe empty values so existing callers that
    only inspect ``total`` / ``by_status`` / ``by_rating`` continue to
    work.

    ``with_correction`` is the count of entries that include a
    user-proposed fix (``correction_text`` non-NULL). It's an orthogonal
    signal to ``by_rating`` — by construction those entries are also
    counted under ``by_rating[NEGATIVE]``.
    """

    model_config = ConfigDict(validate_assignment=True)

    total: int = 0
    by_status: Dict[ReviewStatus, int] = Field(default_factory=dict)
    by_rating: Dict[Rating, int] = Field(default_factory=dict)
    with_correction: int = 0
    top_tags: List[TagCount] = Field(default_factory=list)
    oldest_pending_hours: Optional[float] = None


class FeedbackListFilters(BaseModel):
    """Optional filter set for :meth:`BaseFeedbackStore.list_entries`.

    Every field is optional; ``None`` means "no constraint". ``tags`` uses
    overlap semantics (matches entries that have *any* of the listed tags
    in ``reviewer_tags``). ``date_from`` is inclusive, ``date_to`` is
    exclusive — mirroring :meth:`BaseFeedbackStore.list_by_date_range`.
    """

    model_config = ConfigDict(validate_assignment=True)

    status: Optional[ReviewStatus] = None
    rating: Optional[Rating] = None
    has_correction: Optional[bool] = None
    tags: Optional[List[str]] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    user_id: Optional[str] = None

    @field_validator("tags")
    @classmethod
    def _strip_tags(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        normalized = [t.strip() for t in v if t and t.strip()]
        # Treat an all-blank list the same as "no constraint" — callers
        # passing ``tags=["", "  "]`` clearly didn't intend a filter that
        # can never match.
        return normalized or None

    @field_validator("user_id")
    @classmethod
    def _strip_user_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @model_validator(mode="after")
    def _validate_date_window(self) -> "FeedbackListFilters":
        if self.date_from is not None and self.date_to is not None and self.date_to <= self.date_from:
            raise ValueError("date_to must be after date_from")
        return self


class FeedbackListResponse(BaseModel):
    """Paginated envelope returned by the admin feedback list endpoint."""

    model_config = ConfigDict(validate_assignment=True)

    items: List[FeedbackEntry]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ReviewUpdateRequest(BaseModel):
    """Request body for ``PATCH /admin/feedback/{id}``.

    Only the decision fields a reviewer can directly set are accepted.
    ``reviewer_id`` and ``reviewed_at`` are derived server-side from the
    authenticated admin identity and the current time, so any attempt to
    set them in the body is rejected via ``model_config = extra='forbid'``
    (returns 422 from FastAPI's validator).
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    review_status: ReviewStatus
    reviewer_tags: List[str] = Field(default_factory=list)
    reviewer_comment: Optional[str] = None

    @field_validator("review_status")
    @classmethod
    def _reject_pending(cls, v: ReviewStatus) -> ReviewStatus:
        # The reviewer can move an entry to ``approved`` or ``rejected``;
        # transitioning back into ``pending_review`` is not a valid
        # outcome of a review action (and the store would reject it too,
        # but failing fast here gives a 422 from Pydantic instead of a
        # 409 from the DB layer).
        if v == ReviewStatus.PENDING_REVIEW:
            raise ValueError("review_status must be 'approved' or 'rejected'")
        return v

    @field_validator("reviewer_tags")
    @classmethod
    def _strip_reviewer_tags(cls, v: List[str]) -> List[str]:
        return [t.strip() for t in v if t and t.strip()]

    @field_validator("reviewer_comment")
    @classmethod
    def _strip_reviewer_comment(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


# ---------------------------------------------------------------------------
# Knowledge-base admin models (XMGPLAT-10417 — Phase 2 KB admin extensions)
# ---------------------------------------------------------------------------


class KBDocument(BaseModel):
    """A knowledge-base document as exposed by the admin API.

    Mirrors the ``documents`` table shape used by both the SQLite and
    pgvector KB backends. ``tags`` is a convenience projection of
    ``metadata['tags']`` — the storage layer keeps tags inside the
    JSON ``metadata`` column so no schema migration is required. The
    store guarantees that this field and ``metadata['tags']`` stay in
    sync on read and on write.

    ``chunk_count`` is populated by :meth:`BaseKBStore.list_documents`
    and :meth:`BaseKBStore.update_document` via a JOIN; ``None`` means
    "not populated" (callers that don't need the count avoid the JOIN
    cost).
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(min_length=1)
    content: str
    title: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    topic: Optional[str] = None
    date_published: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    chunk_count: Optional[int] = None
    created_at: Optional[datetime] = None

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, v: List[str]) -> List[str]:
        # Match feedback-tag hygiene: strip + drop blanks + dedupe (case-sensitive,
        # preserving first-seen order so callers see what they wrote).
        seen: set[str] = set()
        out: List[str] = []
        for t in v or []:
            if not t:
                continue
            t = t.strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(t)
        return out


class KBDocumentListFilters(BaseModel):
    """Optional filter set for :meth:`BaseKBStore.list_documents`.

    All fields optional; ``None`` means "no constraint". ``tags`` uses
    overlap semantics (matches documents whose ``metadata['tags']``
    contains *any* of the listed tags). ``date_from`` / ``date_to``
    filter on the ``date_published`` column; ``date_from`` inclusive,
    ``date_to`` exclusive — mirroring :class:`FeedbackListFilters`.
    """

    model_config = ConfigDict(validate_assignment=True)

    source: Optional[str] = None
    topic: Optional[str] = None
    tags: Optional[List[str]] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None

    @field_validator("source", "topic")
    @classmethod
    def _strip_str(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("tags")
    @classmethod
    def _strip_tags(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        normalized = [t.strip() for t in v if t and t.strip()]
        return normalized or None

    @model_validator(mode="after")
    def _validate_date_window(self) -> "KBDocumentListFilters":
        if self.date_from is not None and self.date_to is not None and self.date_to <= self.date_from:
            raise ValueError("date_to must be after date_from")
        return self


class KBDocumentListResponse(BaseModel):
    """Paginated envelope returned by the admin KB list endpoint."""

    model_config = ConfigDict(validate_assignment=True)

    items: List[KBDocument]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Admin API error envelope (XMGPLAT-10417 — Phase 2, T6.2)
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Standardized error envelope for every admin endpoint.

    Surfaced in OpenAPI as the response schema for 400 / 404 / 409 /
    401 / 403. ``code`` is a stable machine-readable identifier
    (snake_case, never localized) and ``detail`` is a human-readable
    message. ``errors`` is populated only on validation failures and
    mirrors the structure FastAPI emits for 422, so client libraries
    can render field-level diagnostics uniformly.
    """

    model_config = ConfigDict(validate_assignment=True)

    code: str = Field(description="Stable machine-readable error code (snake_case)")
    detail: str = Field(description="Human-readable error message")
    errors: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Field-level diagnostics (validation failures only)",
    )
