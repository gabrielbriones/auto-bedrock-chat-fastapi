"""Feedback synthesis engine.

Converts approved, not-yet-integrated feedback entries into structured KB
articles via LLM synthesis, then stores them in the KB and marks each
contributing :class:`FeedbackEntry` as integrated.

Typical usage::

    synthesizer = FeedbackSynthesizer(model_id=config.model_id)
    result = await synthesizer.synthesize_all(feedback_store, kb_store, bedrock_client)
    # Or, for per-review on-demand:
    tag_result = await synthesizer.synthesize_entry(feedback_id, feedback_store, kb_store, bedrock_client)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from .db.feedback_base import BaseFeedbackStore
from .db.kb_base import BaseKBStore
from .embedding_pipeline import TextChunker
from .models import FeedbackEntry, FeedbackListFilters, KBDocumentListFilters, ReviewStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Entries with no reviewer_tags are grouped under this fixed label so they
# are synthesized rather than silently skipped.
_UNTAGGED_TAG = "untagged"

# Page size used when fetching pending entries from the feedback store.
_PAGE_SIZE = 1_000


class SynthesisAction(str, Enum):
    """Action taken (or recommended by the LLM) for a tag group."""

    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"


@dataclass
class TagGroupResult:
    """Result of synthesizing one tag group."""

    tag: str
    action: SynthesisAction
    #: ``None`` on error or when the LLM chose ``skip`` without an existing article.
    kb_doc_id: Optional[str]
    feedback_ids_marked: List[UUID]
    error: Optional[str] = None


@dataclass
class SynthesisRunResult:
    """Aggregate result of a :meth:`FeedbackSynthesizer.synthesize_all` run."""

    tag_results: List[TagGroupResult] = field(default_factory=list)
    total_integrated: int = 0
    errors: List[str] = field(default_factory=list)

    def add(self, result: TagGroupResult) -> None:
        self.tag_results.append(result)
        if result.error:
            self.errors.append(f"[{result.tag}] {result.error}")
        else:
            self.total_integrated += len(result.feedback_ids_marked)


# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------

_SYNTHESIS_SYSTEM_PROMPT = """\
You are an expert knowledge-base curator.
Your task is to synthesize one or more expert-validated feedback items into a
concise, searchable knowledge-base article.

Each feedback item may contain:
  • reviewer_comment  – The reviewer's expert guidance (PRIMARY source — use this
                        above all else when present)
  • correction_text   – A specific correction the user proposed (SECONDARY —
                        supplement the reviewer comment when present)
  • ai_response       – The original AI answer (use to identify what was wrong)
  • user_query        – The user's original question (use for context)

Not all fields will be present in every item.  Base your article on whatever
expert guidance is available, using the AI response and user query for context.

You MUST respond with a single valid JSON object and nothing else. The JSON
must contain exactly these fields:

  "title"              – (string) Concise, searchable article title
  "problem"            – (string) What the AI was doing wrong or what gap
                          exists in its knowledge
  "correct_methodology"– (string) The validated correct approach, including
                          formulas or code fragments where helpful
  "key_terms"          – (array of strings) Terms likely to appear in user
                          queries about this topic
  "examples"           – (string) 1–2 concrete, correct worked examples
  "source_feedback_ids"– (array of strings) UUIDs of the feedback entries
                          you are synthesizing
  "action"             – (string) Exactly one of: "create", "update", "skip"

Rules for "action":
  • "create" – No existing article was provided; write a new one.
  • "update" – An existing article was provided and the new feedback adds,
               corrects, or meaningfully clarifies information in it.
  • "skip"   – An existing article was provided and the new feedback
               confirms it without changing anything.

Do NOT include any text outside the JSON object.\
"""


def _format_entry(entry: FeedbackEntry, index: int) -> str:
    lines = [
        f"--- Feedback Entry {index + 1} ---",
        f"ID: {entry.id}",
        f"User query: {entry.query}",
        f"Original AI response: {entry.ai_response}",
    ]
    # Reviewer comment is the primary expert signal — list it first.
    if entry.reviewer_comment:
        lines.append(f"Reviewer comment (PRIMARY): {entry.reviewer_comment}")
    # Correction text is a user-proposed fix — secondary context.
    if entry.correction_text:
        lines.append(f"User correction (secondary): {entry.correction_text}")
    elif not entry.reviewer_comment:
        # Neither is set; note the rating so the LLM has at least that.
        lines.append(f"User rating: {entry.rating.value}")
    return "\n".join(lines)


def _build_messages(
    tag: str,
    entries: List[FeedbackEntry],
    existing_content: Optional[str],
    system_prompt: str = _SYNTHESIS_SYSTEM_PROMPT,
) -> List[Dict[str, Any]]:
    """Build the message list to pass to ``bedrock_client.chat_completion``."""
    parts: List[str] = [
        f"Tag: {tag}",
        f"\nFeedback entries to synthesize ({len(entries)} total):\n",
    ]
    for i, entry in enumerate(entries):
        parts.append(_format_entry(entry, i))

    if existing_content:
        parts.append(f"\n--- Existing KB article for this tag ---\n{existing_content}")
    else:
        parts.append("\n(No existing KB article for this tag — please create one.)")

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(parts)},
    ]


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------


def _parse_article(content: str) -> Dict[str, Any]:
    """Extract the JSON object from the LLM response, stripping markdown fences."""
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return json.loads(stripped.strip())


def _article_to_document_content(data: Dict[str, Any]) -> str:
    """Render the synthesized article dict as formatted KB document text."""
    parts: List[str] = []
    if data.get("title"):
        parts.append(f"# {data['title']}")
    if data.get("problem"):
        parts.append(f"\n## Problem\n{data['problem']}")
    if data.get("correct_methodology"):
        parts.append(f"\n## Correct Methodology\n{data['correct_methodology']}")
    if data.get("examples"):
        parts.append(f"\n## Examples\n{data['examples']}")
    if data.get("key_terms"):
        terms = data["key_terms"]
        if isinstance(terms, list):
            parts.append(f"\n## Key Terms\n{', '.join(str(t) for t in terms)}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------


class FeedbackSynthesizer:
    """Synthesize expert-approved feedback entries into KB articles.

    Instantiate once and call :meth:`synthesize_all` for scheduled batch runs
    or :meth:`synthesize_entry` for per-review on-demand synthesis.

    Parameters
    ----------
    model_id:
        Bedrock model used for LLM synthesis calls. When ``None``, falls back
        to ``bedrock_client.config.model_id`` at call time.
    embedding_model_id:
        Bedrock embedding model. Defaults to ``"amazon.titan-embed-text-v1"``.
    chunk_size:
        Target words-per-chunk passed to :class:`~.embedding_pipeline.TextChunker`.
    chunk_overlap:
        Overlap words between consecutive chunks.
    """

    def __init__(
        self,
        model_id: Optional[str] = None,
        embedding_model_id: str = "amazon.titan-embed-text-v1",
        chunk_size: int = 512,
        chunk_overlap: int = 100,
        synthesis_system_prompt: Optional[str] = None,
    ) -> None:
        self.model_id = model_id
        self.embedding_model_id = embedding_model_id
        self._chunker = TextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self._system_prompt = synthesis_system_prompt or _SYNTHESIS_SYSTEM_PROMPT

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def synthesize_all(
        self,
        feedback_store: BaseFeedbackStore,
        kb_store: BaseKBStore,
        bedrock_client: Any,
    ) -> SynthesisRunResult:
        """Process all approved entries where ``integrated_into_kb_id IS NULL``.

        Entries are grouped by ``reviewer_tags``.  Each tag group is synthesized
        independently.  An entry with multiple tags participates in each group;
        it is marked integrated after the first successful group synthesis and
        skipped in subsequent groups within the same run to avoid double-marking.
        Entries with no tags are grouped under ``"untagged"`` and synthesized
        together.

        Returns a :class:`SynthesisRunResult` summarising what happened.
        """
        run_result = SynthesisRunResult()

        pending: List[FeedbackEntry] = []
        offset = 0
        while True:
            page = await feedback_store.list_entries(
                FeedbackListFilters(
                    status=ReviewStatus.APPROVED,
                    has_integrated=False,
                ),
                limit=_PAGE_SIZE,
                offset=offset,
            )
            if not page:
                break
            pending.extend(page)
            if len(page) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE

        if not pending:
            logger.info("synthesize_all: no pending entries — nothing to do")
            return run_result

        # Build tag → entry mapping.  Entries with no tags are grouped under
        # _UNTAGGED_TAG so they are synthesized rather than silently dropped.
        tag_groups: Dict[str, List[FeedbackEntry]] = {}
        for entry in pending:
            tags = entry.reviewer_tags if entry.reviewer_tags else [_UNTAGGED_TAG]
            for tag in tags:
                tag_groups.setdefault(tag, []).append(entry)

        logger.info(
            "synthesize_all: %d pending entries across %d tag group(s)",
            len(pending),
            len(tag_groups),
        )

        # Track entries integrated this run to avoid marking them twice when
        # they carry multiple tags (e.g. tags=["perf", "accuracy"]).  Each
        # entry contributes to only the first group that successfully
        # integrates it; later groups see a filtered list without it.
        already_marked: set[UUID] = set()

        for tag, entries in tag_groups.items():
            # Filter out entries already marked by a previous group in this run.
            fresh = [e for e in entries if e.id not in already_marked]
            if not fresh:
                continue
            tag_result = await self._synthesize_tag_group(
                tag=tag,
                entries=fresh,
                kb_store=kb_store,
                bedrock_client=bedrock_client,
                feedback_store=feedback_store,
            )
            run_result.add(tag_result)
            already_marked.update(tag_result.feedback_ids_marked)

        return run_result

    async def synthesize_entry(
        self,
        feedback_id: UUID,
        feedback_store: BaseFeedbackStore,
        kb_store: BaseKBStore,
        bedrock_client: Any,
    ) -> TagGroupResult:
        """Synthesize a single approved entry on demand (per-review trigger).

        Fetches the entry identified by ``feedback_id``, validates it is
        eligible, then synthesises it as a single-entry tag group.  The first
        tag in ``reviewer_tags`` is used as the canonical tag; if the entry has
        no tags, ``"untagged"`` is used.

        Returns
        -------
        TagGroupResult
            On success, ``kb_doc_id`` is set and ``feedback_ids_marked``
            contains the entry's UUID.

        Raises
        ------
        FeedbackNotFoundError
            If no entry with ``feedback_id`` exists.
        AlreadyIntegratedError
            If the entry is already linked to a KB document.
        ValueError
            If the entry is not ``approved``.
        """
        from .exceptions import FeedbackNotFoundError

        entry = await feedback_store.get(feedback_id)
        if entry is None:
            raise FeedbackNotFoundError(f"feedback {feedback_id} not found")

        if entry.review_status != ReviewStatus.APPROVED:
            raise ValueError(
                f"feedback {feedback_id} has status '{entry.review_status.value}'; "
                "only 'approved' entries can be synthesized"
            )
        if entry.integrated_into_kb_id is not None:
            from .exceptions import AlreadyIntegratedError

            raise AlreadyIntegratedError(
                f"feedback {feedback_id} is already integrated into KB document " f"'{entry.integrated_into_kb_id}'"
            )

        tag = entry.reviewer_tags[0] if entry.reviewer_tags else _UNTAGGED_TAG

        return await self._synthesize_tag_group(
            tag=tag,
            entries=[entry],
            kb_store=kb_store,
            bedrock_client=bedrock_client,
            feedback_store=feedback_store,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _synthesize_tag_group(
        self,
        tag: str,
        entries: List[FeedbackEntry],
        kb_store: BaseKBStore,
        bedrock_client: Any,
        feedback_store: BaseFeedbackStore,
    ) -> TagGroupResult:
        """Core synthesis logic for one tag group.

        Calls the LLM, parses the response, creates or updates the KB
        document, re-embeds if necessary, and marks all contributing entries
        as integrated.  Any unexpected exception is caught, logged, and
        returned as a :class:`TagGroupResult` with ``error`` set rather than
        propagating — so one failing tag group does not abort a batch run.
        """
        try:
            existing_docs = await asyncio.to_thread(
                kb_store.list_documents,
                KBDocumentListFilters(tags=[tag], source="feedback"),
            )
            existing_doc = existing_docs[0] if existing_docs else None

            messages = _build_messages(
                tag=tag,
                entries=entries,
                existing_content=existing_doc.content if existing_doc else None,
                system_prompt=self._system_prompt,
            )

            model_id = self.model_id or bedrock_client.config.model_id
            response = await bedrock_client.chat_completion(
                messages=messages,
                model_id=model_id,
                temperature=0.0,
            )

            raw_content = response.get("content", "")
            article_data = _parse_article(raw_content)

            action_str = article_data.get("action", "create").lower()
            try:
                action = SynthesisAction(action_str)
            except ValueError:
                logger.warning(
                    "tag='%s': unrecognised LLM action '%s'; defaulting to 'create'",
                    tag,
                    action_str,
                )
                action = SynthesisAction.CREATE

            doc_content = _article_to_document_content(article_data)
            title = str(article_data.get("title") or f"Learned correction: {tag}")
            doc_id: Optional[str] = None

            # Collect the actual reviewer_tags from the entries so the KB
            # article is tagged with domain labels, not the internal routing key.
            # Fall back to [_UNTAGGED_TAG] when all entries lack reviewer_tags so
            # that the stored metadata.tags matches the lookup filter used on the
            # next run (KBDocumentListFilters(tags=[tag])) and avoids creating
            # duplicate "untagged" documents on every synthesis pass.
            doc_tags: List[str] = list(dict.fromkeys(t for e in entries for t in (e.reviewer_tags or []))) or [
                _UNTAGGED_TAG
            ]

            if action == SynthesisAction.SKIP and existing_doc is not None:
                # LLM confirmed no new information; reuse the existing article.
                logger.info(
                    "tag='%s' action=skip — LLM found no new information; no KB change",
                    tag,
                )
                doc_id = existing_doc.id

            elif action == SynthesisAction.UPDATE and existing_doc is not None:
                doc_id = existing_doc.id
                # Merge source_feedback_ids from existing metadata
                prev_ids: List[str] = existing_doc.metadata.get("source_feedback_ids") or []
                new_ids = [str(e.id) for e in entries]
                merged_ids = list(dict.fromkeys(prev_ids + new_ids))  # deduplicated, stable order
                # Merge tags too
                prev_tags: List[str] = existing_doc.metadata.get("tags") or []
                merged_tags = list(dict.fromkeys(prev_tags + doc_tags))
                await asyncio.to_thread(
                    kb_store.update_document,
                    doc_id,
                    content=doc_content,
                    title=title,
                    metadata={
                        **(existing_doc.metadata or {}),
                        "tags": merged_tags,
                        "synthesized": True,
                        "source_feedback_ids": merged_ids,
                    },
                )
                logger.info("tag='%s' action=update doc_id='%s'", tag, doc_id)
                await self._embed_and_add_chunks(doc_id, doc_content, kb_store, bedrock_client)

            else:
                # CREATE (or UPDATE when there is no existing doc — treat as create).
                # Also handles SKIP with no existing doc: the prompt requires the LLM
                # to use "create" in that scenario; if it returns "skip" anyway we
                # fall back here to avoid stranding entries indefinitely.
                action = SynthesisAction.CREATE
                safe_tag = re.sub(r"[^a-z0-9_-]", "-", tag.lower())
                doc_id = f"synthesis-{safe_tag}-{uuid4().hex[:8]}"
                await asyncio.to_thread(
                    kb_store.add_document,
                    doc_id,
                    doc_content,
                    title,
                    "feedback",  # source
                    None,  # source_url
                    "Feedback review",  # topic — admin can refine later
                    None,  # date_published
                    {
                        "tags": doc_tags,
                        "synthesized": True,
                        "source_feedback_ids": [str(e.id) for e in entries],
                    },
                )
                logger.info("tag='%s' action=create doc_id='%s'", tag, doc_id)
                await self._embed_and_add_chunks(doc_id, doc_content, kb_store, bedrock_client)

            # Mark all contributing entries as integrated.
            now = datetime.now(timezone.utc)
            marked: List[UUID] = []
            if doc_id is not None:
                for entry in entries:
                    await feedback_store.mark_integrated(entry.id, doc_id, now)
                    marked.append(entry.id)

            return TagGroupResult(
                tag=tag,
                action=action,
                kb_doc_id=doc_id,
                feedback_ids_marked=marked,
            )

        except Exception as exc:
            # Intentionally broad: one failing tag group must not abort a batch
            # run.  The error is recorded in TagGroupResult and surfaced via
            # SynthesisRunResult.errors so callers can report it without losing
            # results from other tag groups.
            logger.exception("synthesize_tag_group: unhandled error for tag='%s': %s", tag, exc)
            return TagGroupResult(
                tag=tag,
                action=SynthesisAction.SKIP,
                kb_doc_id=None,
                feedback_ids_marked=[],
                error=str(exc),
            )

    async def _embed_and_add_chunks(
        self,
        doc_id: str,
        content: str,
        kb_store: BaseKBStore,
        bedrock_client: Any,
    ) -> None:
        """Chunk ``content``, generate embeddings, and store via ``kb_store.add_chunk``."""
        chunks = self._chunker.chunk_text(content)
        if not chunks:
            logger.warning("_embed_and_add_chunks: no chunks produced for doc_id='%s'", doc_id)
            return

        texts = [c["text"] for c in chunks]
        embeddings = await bedrock_client.generate_embeddings_batch(texts, model_id=self.embedding_model_id)

        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            await asyncio.to_thread(
                kb_store.add_chunk,
                f"{doc_id}__chunk_{i}",
                doc_id,
                chunk["text"],
                embedding,
                i,
                chunk.get("start_char"),
                chunk.get("end_char"),
            )
