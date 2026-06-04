"""Unit tests for the FeedbackSynthesizer engine."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from auto_bedrock_chat_fastapi.exceptions import AlreadyIntegratedError, FeedbackNotFoundError
from auto_bedrock_chat_fastapi.models import FeedbackEntry, KBDocument, Rating, ReviewStatus
from auto_bedrock_chat_fastapi.synthesizer import (
    FeedbackSynthesizer,
    SynthesisAction,
    SynthesisRunResult,
    TagGroupResult,
    _article_to_document_content,
    _build_messages,
    _parse_article,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODEL_ID = "anthropic.claude-test"
_EMBED_MODEL = "amazon.titan-embed-text-v1"
_DUMMY_EMBEDDING = [0.1] * 1536


def _approved_entry(
    *,
    tags: Optional[List[str]] = None,
    correction_text: str = "The correct answer is X.",
    reviewer_comment: Optional[str] = None,
) -> FeedbackEntry:
    reviewer_tags = ["perf"] if tags is None else tags
    return FeedbackEntry(
        session_id="sess-test",
        user_id="expert-1",
        query="What is the workload type?",
        ai_response="It is Y.",
        rating=Rating.NEGATIVE,
        correction_text=correction_text,
        reviewer_tags=reviewer_tags,
        reviewer_id="reviewer-1",
        reviewer_comment=reviewer_comment,
        review_status=ReviewStatus.APPROVED,
        reviewed_at=datetime.now(timezone.utc),
        model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        created_at=datetime.now(timezone.utc),
    )


def _kb_doc(
    *,
    tag: str = "perf",
    doc_id: Optional[str] = None,
) -> KBDocument:
    return KBDocument(
        id=doc_id or f"synthesis-{tag}-abcd1234",
        content=f"# Existing article for {tag}\n\n## Problem\nOld problem.",
        title=f"Learned correction: {tag}",
        source="feedback",
        topic=tag,
        tags=[tag],
        metadata={"tags": [tag], "synthesized": True, "source_feedback_ids": []},
    )


def _article_json(action: str = "create", title: str = "Test Article") -> str:
    """Return a minimal valid JSON string that the LLM would produce."""
    return json.dumps(
        {
            "title": title,
            "problem": "AI was computing the wrong metric.",
            "correct_methodology": "Use formula X = Y / Z.",
            "key_terms": ["workload", "metric", "performance"],
            "examples": "Example: X = 10 / 2 = 5.",
            "source_feedback_ids": [],
            "action": action,
        }
    )


def _make_bedrock_client(llm_response_json: str) -> MagicMock:
    client = MagicMock()
    client.config = MagicMock()
    client.config.model_id = _MODEL_ID
    client.chat_completion = AsyncMock(return_value={"content": llm_response_json})
    client.generate_embeddings_batch = AsyncMock(return_value=[_DUMMY_EMBEDDING])
    return client


def _make_kb_store(existing_docs: Optional[List[KBDocument]] = None) -> MagicMock:
    store = MagicMock()
    store.list_documents = MagicMock(return_value=existing_docs or [])
    store.add_document = MagicMock()
    store.update_document = MagicMock(return_value=_kb_doc())
    store.add_chunk = MagicMock()
    return store


def _make_feedback_store(entries: Optional[List[FeedbackEntry]] = None) -> MagicMock:
    store = MagicMock()
    store.list_entries = AsyncMock(return_value=entries or [])
    store.get = AsyncMock(return_value=entries[0] if entries else None)
    store.mark_integrated = AsyncMock()
    return store


# ---------------------------------------------------------------------------
# _parse_article
# ---------------------------------------------------------------------------


class TestParseArticle:
    def test_parses_plain_json(self):
        payload = '{"action": "create", "title": "Foo"}'
        result = _parse_article(payload)
        assert result["action"] == "create"
        assert result["title"] == "Foo"

    def test_strips_markdown_json_fence(self):
        payload = '```json\n{"action": "update"}\n```'
        result = _parse_article(payload)
        assert result["action"] == "update"

    def test_strips_plain_code_fence(self):
        payload = '```\n{"action": "skip"}\n```'
        result = _parse_article(payload)
        assert result["action"] == "skip"

    def test_raises_on_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_article("not json at all")


# ---------------------------------------------------------------------------
# _article_to_document_content
# ---------------------------------------------------------------------------


class TestArticleToDocumentContent:
    def test_includes_all_sections(self):
        data = {
            "title": "My Article",
            "problem": "Bad output",
            "correct_methodology": "Use X",
            "key_terms": ["alpha", "beta"],
            "examples": "Example: alpha = 1",
        }
        content = _article_to_document_content(data)
        assert "# My Article" in content
        assert "## Problem" in content
        assert "## Correct Methodology" in content
        assert "## Key Terms" in content
        assert "alpha, beta" in content
        assert "## Examples" in content

    def test_missing_optional_sections_are_skipped(self):
        data = {"title": "Only Title"}
        content = _article_to_document_content(data)
        assert "# Only Title" in content
        assert "## Problem" not in content


# ---------------------------------------------------------------------------
# _build_messages
# ---------------------------------------------------------------------------


class TestBuildMessages:
    def test_includes_system_and_user_roles(self):
        entry = _approved_entry()
        messages = _build_messages("perf", [entry], existing_content=None)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_user_message_contains_tag(self):
        messages = _build_messages("my-tag", [_approved_entry()], existing_content=None)
        assert "my-tag" in messages[1]["content"]

    def test_user_message_mentions_no_existing_article(self):
        messages = _build_messages("perf", [_approved_entry()], existing_content=None)
        assert "No existing KB article" in messages[1]["content"]

    def test_user_message_includes_existing_content(self):
        messages = _build_messages("perf", [_approved_entry()], existing_content="Old content here.")
        assert "Old content here." in messages[1]["content"]

    def test_entry_fields_present_in_user_message(self):
        entry = _approved_entry(correction_text="Correct answer is X.")
        messages = _build_messages("perf", [entry], existing_content=None)
        user_content = messages[1]["content"]
        assert "Correct answer is X." in user_content
        assert entry.query in user_content
        assert entry.ai_response in user_content

    def test_reviewer_comment_included_when_present(self):
        entry = _approved_entry(reviewer_comment="Pay attention to edge case Y.")
        messages = _build_messages("perf", [entry], existing_content=None)
        assert "Pay attention to edge case Y." in messages[1]["content"]


# ---------------------------------------------------------------------------
# FeedbackSynthesizer._embed_and_add_chunks
# ---------------------------------------------------------------------------


class TestEmbedAndAddChunks:
    @pytest.mark.asyncio
    async def test_calls_generate_embeddings_batch(self):
        # Use default chunk_size=512 so chunks pass min_chunk_size=50
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)
        kb_store = _make_kb_store()
        bedrock_client = _make_bedrock_client(_article_json())
        bedrock_client.generate_embeddings_batch = AsyncMock(return_value=[_DUMMY_EMBEDDING])
        # 60 words — satisfies min_chunk_size=50 for a single chunk
        long_content = " ".join(f"word{i}" for i in range(60))

        await synth._embed_and_add_chunks("doc-1", long_content, kb_store, bedrock_client)

        bedrock_client.generate_embeddings_batch.assert_awaited_once()
        kb_store.add_chunk.assert_called()

    @pytest.mark.asyncio
    async def test_no_chunks_skips_embedding(self):
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)
        kb_store = _make_kb_store()
        bedrock_client = _make_bedrock_client(_article_json())

        await synth._embed_and_add_chunks("doc-1", "", kb_store, bedrock_client)

        bedrock_client.generate_embeddings_batch.assert_not_called()
        kb_store.add_chunk.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_embeddings_count_mismatches_chunks(self):
        """A partial embedding response must raise rather than silently drop chunks."""
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)
        kb_store = _make_kb_store()
        bedrock_client = _make_bedrock_client(_article_json())
        # Return fewer embeddings than there are chunks
        bedrock_client.generate_embeddings_batch = AsyncMock(return_value=[])
        long_content = " ".join(f"word{i}" for i in range(60))

        with pytest.raises(RuntimeError, match="expected.*embeddings.*got"):
            await synth._embed_and_add_chunks("doc-1", long_content, kb_store, bedrock_client)

        kb_store.add_chunk.assert_not_called()


# ---------------------------------------------------------------------------
# FeedbackSynthesizer.synthesize_all — no entries
# ---------------------------------------------------------------------------


class TestSynthesizeAllEmpty:
    @pytest.mark.asyncio
    async def test_returns_empty_result_when_no_pending(self):
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)
        fb_store = _make_feedback_store(entries=[])
        kb_store = _make_kb_store()
        bedrock_client = _make_bedrock_client(_article_json())

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        assert isinstance(result, SynthesisRunResult)
        assert result.total_integrated == 0
        assert result.tag_results == []
        bedrock_client.chat_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_synthesizes_entries_without_tags_under_untagged(self):
        entry = _approved_entry(tags=[])
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store()
        bedrock_client = _make_bedrock_client(_article_json())
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        assert result.total_integrated == 1
        assert result.tag_results[0].tag == "untagged"
        bedrock_client.chat_completion.assert_called_once()


# ---------------------------------------------------------------------------
# FeedbackSynthesizer.synthesize_all — CREATE branch
# ---------------------------------------------------------------------------


class TestSynthesizeAllCreate:
    @pytest.mark.asyncio
    async def test_creates_new_kb_document(self):
        entry = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[])  # no existing article
        bedrock_client = _make_bedrock_client(_article_json(action="create"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        assert result.total_integrated == 1
        assert result.errors == []
        kb_store.add_document.assert_called_once()
        _, call_args = kb_store.add_document.call_args[0][0], kb_store.add_document.call_args[0]
        doc_id = call_args[0]
        assert "perf" in doc_id

    @pytest.mark.asyncio
    async def test_marks_entry_integrated(self):
        entry = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client(_article_json(action="create"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        fb_store.mark_integrated.assert_awaited_once()
        call_args = fb_store.mark_integrated.call_args[0]
        assert call_args[0] == entry.id

    @pytest.mark.asyncio
    async def test_tag_result_has_correct_action(self):
        entry = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client(_article_json(action="create"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        assert len(result.tag_results) == 1
        assert result.tag_results[0].action == SynthesisAction.CREATE
        assert result.tag_results[0].tag == "perf"

    @pytest.mark.asyncio
    async def test_multiple_entries_same_tag_grouped(self):
        entry_a = _approved_entry(tags=["perf"])
        entry_b = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store(entries=[entry_a, entry_b])
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client(_article_json(action="create"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        # One LLM call for the single "perf" tag group
        bedrock_client.chat_completion.assert_awaited_once()
        assert result.total_integrated == 2
        assert len(result.tag_results) == 1

    @pytest.mark.asyncio
    async def test_entries_different_tags_yield_separate_groups(self):
        entry_perf = _approved_entry(tags=["perf"])
        entry_mem = _approved_entry(tags=["memory"])
        fb_store = _make_feedback_store(entries=[entry_perf, entry_mem])
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client(_article_json(action="create"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        assert bedrock_client.chat_completion.await_count == 2
        assert len(result.tag_results) == 2

    @pytest.mark.asyncio
    async def test_multi_tag_entry_not_double_marked(self):
        # Entry has two tags.  After the first tag group synthesizes and marks
        # the entry, the second tag group finds no fresh entries and is skipped.
        # The entry is marked exactly once.
        entry = _approved_entry(tags=["perf", "memory"])
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client(_article_json(action="create"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        # Entry is marked exactly once (first tag group consumed it).
        assert fb_store.mark_integrated.await_count == 1
        # Only the first tag group produced a result; the second was skipped.
        assert len(result.tag_results) == 1

    @pytest.mark.asyncio
    async def test_uses_configured_model_id(self):
        entry = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client(_article_json())
        synth = FeedbackSynthesizer(model_id="custom-model-id")

        await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        _, kwargs = bedrock_client.chat_completion.call_args
        assert kwargs.get("model_id") == "custom-model-id"

    @pytest.mark.asyncio
    async def test_falls_back_to_client_model_id(self):
        entry = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client(_article_json())
        synth = FeedbackSynthesizer(model_id=None)  # no explicit model

        await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        _, kwargs = bedrock_client.chat_completion.call_args
        assert kwargs.get("model_id") == _MODEL_ID  # from bedrock_client.config.model_id


# ---------------------------------------------------------------------------
# FeedbackSynthesizer.synthesize_all — UPDATE branch
# ---------------------------------------------------------------------------


class TestSynthesizeAllUpdate:
    @pytest.mark.asyncio
    async def test_updates_existing_kb_document(self):
        entry = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store(entries=[entry])
        existing = _kb_doc(tag="perf")
        kb_store = _make_kb_store(existing_docs=[existing])
        bedrock_client = _make_bedrock_client(_article_json(action="update"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        kb_store.update_document.assert_called_once()
        assert result.tag_results[0].action == SynthesisAction.UPDATE
        assert result.tag_results[0].kb_doc_id == existing.id

    @pytest.mark.asyncio
    async def test_update_merges_source_feedback_ids(self):
        entry = _approved_entry(tags=["perf"])
        prev_id = str(uuid4())
        existing = _kb_doc(tag="perf")
        existing.metadata["source_feedback_ids"] = [prev_id]
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[existing])
        bedrock_client = _make_bedrock_client(_article_json(action="update"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        _, kwargs = kb_store.update_document.call_args
        ids = kwargs["metadata"]["source_feedback_ids"]
        assert prev_id in ids
        assert str(entry.id) in ids

    @pytest.mark.asyncio
    async def test_update_triggers_re_embedding(self):
        entry = _approved_entry(tags=["perf"])
        existing = _kb_doc(tag="perf")
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[existing])
        bedrock_client = _make_bedrock_client(_article_json(action="update"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        # Patch _embed_and_add_chunks to verify it is called after update_document
        with patch.object(synth, "_embed_and_add_chunks", new_callable=AsyncMock) as mock_embed:
            await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        mock_embed.assert_awaited_once()
        kb_store.update_document.assert_called_once()


# ---------------------------------------------------------------------------
# FeedbackSynthesizer.synthesize_all — SKIP branch
# ---------------------------------------------------------------------------


class TestSynthesizeAllSkip:
    @pytest.mark.asyncio
    async def test_skip_does_not_modify_kb(self):
        entry = _approved_entry(tags=["perf"])
        existing = _kb_doc(tag="perf")
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[existing])
        bedrock_client = _make_bedrock_client(_article_json(action="skip"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        kb_store.add_document.assert_not_called()
        kb_store.update_document.assert_not_called()
        assert result.tag_results[0].action == SynthesisAction.SKIP

    @pytest.mark.asyncio
    async def test_skip_marks_entry_integrated_to_existing_doc(self):
        entry = _approved_entry(tags=["perf"])
        existing = _kb_doc(tag="perf")
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[existing])
        bedrock_client = _make_bedrock_client(_article_json(action="skip"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        fb_store.mark_integrated.assert_awaited_once()
        assert result.tag_results[0].kb_doc_id == existing.id

    @pytest.mark.asyncio
    async def test_skip_without_existing_doc_falls_back_to_create(self):
        entry = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[])
        # LLM says "skip" even though there's no existing doc — should fall
        # back to CREATE to avoid stranding the entry indefinitely.
        bedrock_client = _make_bedrock_client(_article_json(action="skip"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        assert result.tag_results[0].action == SynthesisAction.CREATE
        assert result.tag_results[0].kb_doc_id is not None
        fb_store.mark_integrated.assert_awaited_once()


# ---------------------------------------------------------------------------
# FeedbackSynthesizer.synthesize_all — unrecognised action falls back to CREATE
# ---------------------------------------------------------------------------


class TestSynthesizeAllUnknownAction:
    @pytest.mark.asyncio
    async def test_unknown_action_defaults_to_create(self):
        entry = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client(_article_json(action="invent-something"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        assert result.tag_results[0].action == SynthesisAction.CREATE
        kb_store.add_document.assert_called_once()


# ---------------------------------------------------------------------------
# FeedbackSynthesizer.synthesize_all — LLM / store errors
# ---------------------------------------------------------------------------


class TestSynthesizeAllErrors:
    @pytest.mark.asyncio
    async def test_llm_error_captured_in_result_not_raised(self):
        entry = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = MagicMock()
        bedrock_client.config.model_id = _MODEL_ID
        bedrock_client.chat_completion = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        bedrock_client.generate_embeddings_batch = AsyncMock(return_value=[_DUMMY_EMBEDDING])
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        assert len(result.errors) == 1
        assert "LLM unavailable" in result.errors[0]
        assert result.total_integrated == 0

    @pytest.mark.asyncio
    async def test_invalid_json_response_captured_as_error(self):
        entry = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store(entries=[entry])
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client("this is not json")
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        assert len(result.errors) == 1
        assert result.total_integrated == 0


# ---------------------------------------------------------------------------
# FeedbackSynthesizer.synthesize_entry — validation
# ---------------------------------------------------------------------------


class TestSynthesizeEntryValidation:
    @pytest.mark.asyncio
    async def test_raises_feedback_not_found(self):
        fb_store = _make_feedback_store()
        fb_store.get = AsyncMock(return_value=None)
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        with pytest.raises(FeedbackNotFoundError):
            await synth.synthesize_entry(uuid4(), fb_store, _make_kb_store(), _make_bedrock_client(_article_json()))

    @pytest.mark.asyncio
    async def test_raises_if_not_approved(self):
        entry = _approved_entry()
        entry.review_status = ReviewStatus.PENDING_REVIEW
        fb_store = _make_feedback_store()
        fb_store.get = AsyncMock(return_value=entry)
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        with pytest.raises(ValueError, match="only 'approved'"):
            await synth.synthesize_entry(entry.id, fb_store, _make_kb_store(), _make_bedrock_client(_article_json()))

    @pytest.mark.asyncio
    async def test_synthesizes_without_correction_text(self):
        # correction_text is optional; synthesis should succeed using
        # reviewer_comment (or just the AI response + rating) as context.
        entry = _approved_entry()
        object.__setattr__(entry, "correction_text", None)
        fb_store = _make_feedback_store()
        fb_store.get = AsyncMock(return_value=entry)
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_entry(
            entry.id, fb_store, _make_kb_store(), _make_bedrock_client(_article_json())
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_raises_if_already_integrated(self):
        entry = _approved_entry()
        kb_uuid = f"synthesis-test-{uuid4().hex[:8]}"
        object.__setattr__(entry, "integrated_into_kb_id", kb_uuid)
        object.__setattr__(entry, "integrated_at", datetime.now(timezone.utc))
        fb_store = _make_feedback_store()
        fb_store.get = AsyncMock(return_value=entry)
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        with pytest.raises(AlreadyIntegratedError):
            await synth.synthesize_entry(entry.id, fb_store, _make_kb_store(), _make_bedrock_client(_article_json()))


# ---------------------------------------------------------------------------
# FeedbackSynthesizer.synthesize_entry — happy paths
# ---------------------------------------------------------------------------


class TestSynthesizeEntryHappyPath:
    @pytest.mark.asyncio
    async def test_create_branch_returns_tag_result(self):
        entry = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store()
        fb_store.get = AsyncMock(return_value=entry)
        fb_store.mark_integrated = AsyncMock()
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client(_article_json(action="create"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_entry(entry.id, fb_store, kb_store, bedrock_client)

        assert isinstance(result, TagGroupResult)
        assert result.action == SynthesisAction.CREATE
        assert result.error is None
        assert entry.id in result.feedback_ids_marked

    @pytest.mark.asyncio
    async def test_uses_first_tag_as_canonical_tag(self):
        entry = _approved_entry(tags=["perf", "memory"])
        fb_store = _make_feedback_store()
        fb_store.get = AsyncMock(return_value=entry)
        fb_store.mark_integrated = AsyncMock()
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client(_article_json(action="create"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_entry(entry.id, fb_store, kb_store, bedrock_client)

        # Canonical tag should be "perf" (first in list)
        assert result.tag == "perf"

    @pytest.mark.asyncio
    async def test_no_tags_uses_untagged_tag(self):
        entry = _approved_entry(tags=[])
        fb_store = _make_feedback_store()
        fb_store.get = AsyncMock(return_value=entry)
        fb_store.mark_integrated = AsyncMock()
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client(_article_json(action="create"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        result = await synth.synthesize_entry(entry.id, fb_store, kb_store, bedrock_client)

        assert result.tag == "untagged"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_only_queries_entry_not_list_entries(self):
        """synthesize_entry must use feedback_store.get, not list_entries."""
        entry = _approved_entry(tags=["perf"])
        fb_store = _make_feedback_store()
        fb_store.get = AsyncMock(return_value=entry)
        fb_store.mark_integrated = AsyncMock()
        kb_store = _make_kb_store(existing_docs=[])
        bedrock_client = _make_bedrock_client(_article_json(action="create"))
        synth = FeedbackSynthesizer(model_id=_MODEL_ID)

        await synth.synthesize_entry(entry.id, fb_store, kb_store, bedrock_client)

        fb_store.list_entries.assert_not_called()
        fb_store.get.assert_awaited_once_with(entry.id)
