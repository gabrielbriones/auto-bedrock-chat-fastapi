"""Unit tests for the KB credibility feedback signals (XMGPLAT-10940).

Covers:
- adjust_credibility on SQLiteKBStore (T5)
- document_id in kb_sources (T1)
- rated-feedback PATCH handler (T3)
- synthesizer cited-doc-ID path (T4 / Phase 5)
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ._autolangchat_imports import load_module

# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

_exceptions_mod = load_module("autolangchat.exceptions", "exceptions.py")
_models_mod = load_module("autolangchat.models", "models.py")
_admin_errors_mod = load_module(
    "autolangchat.admin.admin_errors",
    "admin/admin_errors.py",
    extra_modules={
        "autolangchat.exceptions": _exceptions_mod,
        "autolangchat.models": _models_mod,
    },
)
_kb_base_mod = load_module(
    "autolangchat.db.kb_base",
    "db/kb_base.py",
    extra_modules={
        "autolangchat.exceptions": _exceptions_mod,
        "autolangchat.models": _models_mod,
    },
)
_kb_sqlite_mod = load_module(
    "autolangchat.db.kb_sqlite",
    "db/kb_sqlite.py",
    extra_modules={
        "autolangchat.exceptions": _exceptions_mod,
        "autolangchat.models": _models_mod,
        "autolangchat.db.kb_base": _kb_base_mod,
    },
)
_feedback_routes_mod = load_module(
    "autolangchat.admin.admin_feedback_routes",
    "admin/admin_feedback_routes.py",
    extra_modules={
        "autolangchat.exceptions": _exceptions_mod,
        "autolangchat.models": _models_mod,
        "autolangchat.admin.admin_errors": _admin_errors_mod,
        "autolangchat.db.kb_base": _kb_base_mod,
        "autolangchat.db.feedback_base": load_module(
            "autolangchat.db.feedback_base",
            "db/feedback_base.py",
            extra_modules={
                "autolangchat.exceptions": _exceptions_mod,
                "autolangchat.models": _models_mod,
            },
        ),
    },
)

SQLiteKBStore = _kb_sqlite_mod.SQLiteKBStore
FeedbackEntry = _models_mod.FeedbackEntry
Rating = _models_mod.Rating
ReviewStatus = _models_mod.ReviewStatus
KBDocument = _models_mod.KBDocument
register_admin_feedback_routes = _feedback_routes_mod.register_admin_feedback_routes
register_admin_error_handlers = _admin_errors_mod.register_admin_error_handlers


# ---------------------------------------------------------------------------
# SQLiteKBStore helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path):
    return SQLiteKBStore(db_path=str(tmp_path / "test_kb.db"))


def _insert_doc(store, doc_id, source="feedback", score=1.0, flagged=False):
    store.conn.execute(
        "INSERT INTO documents (id, content, source, credibility_score, removal_flagged) " "VALUES (?, ?, ?, ?, ?)",
        (doc_id, f"content of {doc_id}", source, score, 1 if flagged else 0),
    )
    store.conn.commit()


def _get_row(store, doc_id):
    row = store.conn.execute(
        "SELECT credibility_score, removal_flagged FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()
    return {"credibility_score": row[0], "removal_flagged": bool(row[1])}


# ===========================================================================
# T5 — adjust_credibility (SQLiteKBStore)
# ===========================================================================


class TestAdjustCredibilitySQLite:
    def test_positive_delta_increases_score(self, tmp_path):
        store = _make_store(tmp_path)
        _insert_doc(store, "d1", score=0.6)
        store.adjust_credibility(["d1"], 0.2, threshold=0.3)
        assert abs(_get_row(store, "d1")["credibility_score"] - 0.8) < 1e-6

    def test_positive_delta_clamps_at_one(self, tmp_path):
        store = _make_store(tmp_path)
        _insert_doc(store, "d1", score=0.9)
        store.adjust_credibility(["d1"], 0.5, threshold=0.3)
        assert _get_row(store, "d1")["credibility_score"] == 1.0

    def test_negative_delta_decreases_score(self, tmp_path):
        store = _make_store(tmp_path)
        _insert_doc(store, "d1", score=0.8)
        store.adjust_credibility(["d1"], -0.2, threshold=0.3)
        assert abs(_get_row(store, "d1")["credibility_score"] - 0.6) < 1e-6

    def test_negative_delta_clamps_at_zero(self, tmp_path):
        store = _make_store(tmp_path)
        _insert_doc(store, "d1", score=0.1)
        store.adjust_credibility(["d1"], -0.5, threshold=0.3)
        assert _get_row(store, "d1")["credibility_score"] == 0.0

    def test_negative_delta_sets_removal_flagged_at_threshold(self, tmp_path):
        store = _make_store(tmp_path)
        _insert_doc(store, "d1", score=0.5)  # 0.5 - 0.3 = 0.2 <= 0.3
        store.adjust_credibility(["d1"], -0.3, threshold=0.3)
        row = _get_row(store, "d1")
        assert row["removal_flagged"] is True
        assert abs(row["credibility_score"] - 0.2) < 1e-6

    def test_negative_delta_does_not_flag_above_threshold(self, tmp_path):
        store = _make_store(tmp_path)
        _insert_doc(store, "d1", score=0.9)  # 0.9 - 0.1 = 0.8 > 0.3
        store.adjust_credibility(["d1"], -0.1, threshold=0.3)
        assert _get_row(store, "d1")["removal_flagged"] is False

    def test_skips_non_feedback_source(self, tmp_path):
        store = _make_store(tmp_path)
        _insert_doc(store, "d1", source="operator", score=0.8)
        _insert_doc(store, "d2", source="crawler", score=0.8)
        updated = store.adjust_credibility(["d1", "d2"], 0.2, threshold=0.3)
        assert updated == 0
        assert _get_row(store, "d1")["credibility_score"] == 0.8
        assert _get_row(store, "d2")["credibility_score"] == 0.8

    def test_multiple_doc_ids_updated(self, tmp_path):
        store = _make_store(tmp_path)
        _insert_doc(store, "d1", score=0.5)
        _insert_doc(store, "d2", score=0.6)
        updated = store.adjust_credibility(["d1", "d2"], 0.1, threshold=0.3)
        assert updated == 2

    def test_empty_doc_ids_returns_zero(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.adjust_credibility([], 0.1, threshold=0.3) == 0

    def test_returns_correct_row_count(self, tmp_path):
        store = _make_store(tmp_path)
        _insert_doc(store, "d1")
        _insert_doc(store, "d2")
        _insert_doc(store, "d3", source="operator")  # excluded by source filter
        updated = store.adjust_credibility(["d1", "d2", "d3"], 0.1, threshold=0.3)
        assert updated == 2


# ===========================================================================
# T1 — document_id in kb_sources (websocket_handler integration point)
# ===========================================================================


class TestDocumentIdInKbSources:
    """Verify FeedbackEntry.kb_sources_used field accepts document_id key."""

    def test_kb_sources_used_accepts_document_id(self):
        entry = FeedbackEntry(
            session_id="s1",
            user_id="user1",
            query="q",
            ai_response="a",
            rating=Rating.POSITIVE,
            model_id="anthropic.claude-test",
            kb_sources_used=[
                {
                    "document_id": "doc-abc",
                    "title": "Some Article",
                    "source": "feedback",
                    "url": None,
                    "score": 0.85,
                }
            ],
        )
        assert entry.kb_sources_used[0]["document_id"] == "doc-abc"

    def test_kb_sources_used_allows_missing_document_id(self):
        # Backward-compat: entries stored before T1 may lack document_id
        entry = FeedbackEntry(
            session_id="s1",
            user_id="user1",
            query="q",
            ai_response="a",
            rating=Rating.POSITIVE,
            model_id="anthropic.claude-test",
            kb_sources_used=[{"title": "Old entry", "score": 0.7}],
        )
        assert entry.kb_sources_used[0].get("document_id") is None


# ===========================================================================
# T3 — Rated-feedback PATCH handler
# ===========================================================================


class _FakeFeedbackStore:
    def __init__(self, entries=None):
        self.entries = list(entries or [])

    async def list_entries(self, filters, limit=50, offset=0):
        return self.entries[offset : offset + limit]

    async def count_entries(self, filters):
        return len(self.entries)

    async def stats(self):
        return _models_mod.FeedbackStats()

    async def get(self, entry_id):
        for e in self.entries:
            if e.id == entry_id:
                return e
        return None

    async def update_review(self, entry_id, review_status, reviewer_id=None, tags=None, comment=None):
        entry = await self.get(entry_id)
        if entry is None:
            raise _exceptions_mod.FeedbackNotFoundError("not found")
        data = entry.model_dump()
        data.update(
            {
                "review_status": review_status,
                "reviewer_id": reviewer_id or "admin",
                "reviewer_tags": list(tags or []),
                "reviewer_comment": comment,
                "reviewed_at": datetime.now(timezone.utc),
            }
        )
        updated = FeedbackEntry(**data)
        self.entries = [updated if e.id == entry_id else e for e in self.entries]
        return updated

    async def delete(self, entry_id, expected_status=None):
        for idx, e in enumerate(self.entries):
            if e.id == entry_id:
                del self.entries[idx]
                return True
        return False


def _make_entry(**kwargs):
    defaults = dict(
        session_id="sess-1",
        user_id="alice",
        query="what is the answer?",
        ai_response="42",
        rating=Rating.POSITIVE,
        model_id="anthropic.claude-test",
    )
    defaults.update(kwargs)
    return FeedbackEntry(**defaults)


class _FakeConfig:
    kb_credibility_feedback_signal_enabled = True
    kb_credibility_positive_delta = 0.5
    kb_credibility_negative_delta = 0.5
    kb_credibility_removal_threshold = 0.3


def _build_app(store, kb_store=None, chat_config=None):
    app = FastAPI()
    register_admin_error_handlers(app)

    async def require_admin():
        return SimpleNamespace(user_id="admin")

    register_admin_feedback_routes(
        app,
        prefix="/admin",
        feedback_store=store,
        require_admin=require_admin,
        kb_store=kb_store,
        chat_config=chat_config,
    )
    return TestClient(app)


class TestRatedFeedbackCredibilitySignal:
    def test_signal_fires_on_pending_to_approved(self):
        doc_id = "doc-xyz"
        entry = _make_entry(
            rating=Rating.POSITIVE,
            kb_sources_used=[{"document_id": doc_id, "score": 0.9}],
        )
        store = _FakeFeedbackStore([entry])
        kb_store = MagicMock()
        kb_store.adjust_credibility = MagicMock(return_value=1)
        client = _build_app(store, kb_store=kb_store, chat_config=_FakeConfig())

        resp = client.patch(
            f"/admin/feedback/{entry.id}",
            json={"review_status": "approved"},
        )
        assert resp.status_code == 200
        kb_store.adjust_credibility.assert_called_once()
        call_args = kb_store.adjust_credibility.call_args
        assert call_args[0][0] == [doc_id]
        assert call_args[0][1] > 0  # positive delta

    def test_signal_fires_on_pending_to_approved_negative_rating(self):
        """Negatively-rated entry APPROVED by admin → negative delta applied."""
        doc_id = "doc-xyz"
        entry = _make_entry(
            rating=Rating.NEGATIVE,
            correction_text="fix this",
            kb_sources_used=[{"document_id": doc_id, "score": 0.9}],
        )
        store = _FakeFeedbackStore([entry])
        kb_store = MagicMock()
        kb_store.adjust_credibility = MagicMock(return_value=1)
        client = _build_app(store, kb_store=kb_store, chat_config=_FakeConfig())

        resp = client.patch(
            f"/admin/feedback/{entry.id}",
            json={"review_status": "approved"},
        )
        assert resp.status_code == 200
        kb_store.adjust_credibility.assert_called_once()
        call_args = kb_store.adjust_credibility.call_args
        assert call_args[0][0] == [doc_id]
        assert call_args[0][1] < 0  # negative delta
        assert call_args[0][2] == _FakeConfig.kb_credibility_removal_threshold

    def test_signal_does_not_fire_on_pending_to_rejected(self):
        """Admin REJECTED means the feedback is invalid — no credibility effect."""
        doc_id = "doc-xyz"
        entry = _make_entry(
            rating=Rating.NEGATIVE,
            correction_text="fix this",
            kb_sources_used=[{"document_id": doc_id, "score": 0.9}],
        )
        store = _FakeFeedbackStore([entry])
        kb_store = MagicMock()
        kb_store.adjust_credibility = MagicMock(return_value=1)
        client = _build_app(store, kb_store=kb_store, chat_config=_FakeConfig())

        resp = client.patch(
            f"/admin/feedback/{entry.id}",
            json={"review_status": "rejected"},
        )
        assert resp.status_code == 200
        kb_store.adjust_credibility.assert_not_called()

    def test_signal_does_not_fire_on_re_review(self):
        """APPROVED → REJECTED re-review must not trigger the signal."""
        doc_id = "doc-xyz"
        entry = FeedbackEntry(
            **_make_entry(
                rating=Rating.POSITIVE,
                kb_sources_used=[{"document_id": doc_id, "score": 0.9}],
            ).model_dump()
            | {
                "review_status": ReviewStatus.APPROVED,
                "reviewer_id": "admin",
                "reviewed_at": datetime.now(timezone.utc),
            }
        )
        store = _FakeFeedbackStore([entry])
        kb_store = MagicMock()
        kb_store.adjust_credibility = MagicMock(return_value=1)
        client = _build_app(store, kb_store=kb_store, chat_config=_FakeConfig())

        resp = client.patch(
            f"/admin/feedback/{entry.id}",
            json={"review_status": "rejected"},
        )
        assert resp.status_code == 200
        kb_store.adjust_credibility.assert_not_called()

    def test_signal_does_not_fire_when_disabled(self):
        doc_id = "doc-xyz"
        entry = _make_entry(kb_sources_used=[{"document_id": doc_id, "score": 0.9}])
        store = _FakeFeedbackStore([entry])
        kb_store = MagicMock()

        class _DisabledConfig(_FakeConfig):
            kb_credibility_feedback_signal_enabled = False

        client = _build_app(store, kb_store=kb_store, chat_config=_DisabledConfig())
        resp = client.patch(f"/admin/feedback/{entry.id}", json={"review_status": "approved"})
        assert resp.status_code == 200
        kb_store.adjust_credibility.assert_not_called()

    def test_signal_does_not_fire_when_no_kb_store(self):
        doc_id = "doc-xyz"
        entry = _make_entry(kb_sources_used=[{"document_id": doc_id, "score": 0.9}])
        store = _FakeFeedbackStore([entry])
        client = _build_app(store, kb_store=None, chat_config=_FakeConfig())
        resp = client.patch(f"/admin/feedback/{entry.id}", json={"review_status": "approved"})
        assert resp.status_code == 200  # no error, signal silently skipped

    def test_signal_does_not_fire_when_no_kb_sources(self):
        entry = _make_entry(kb_sources_used=[])
        store = _FakeFeedbackStore([entry])
        kb_store = MagicMock()
        client = _build_app(store, kb_store=kb_store, chat_config=_FakeConfig())
        resp = client.patch(f"/admin/feedback/{entry.id}", json={"review_status": "approved"})
        assert resp.status_code == 200
        kb_store.adjust_credibility.assert_not_called()

    def test_audit_log_includes_credibility_docs_adjusted(self):
        doc_id = "doc-xyz"
        entry = _make_entry(kb_sources_used=[{"document_id": doc_id, "score": 0.9}])
        store = _FakeFeedbackStore([entry])
        kb_store = MagicMock()
        kb_store.adjust_credibility = MagicMock(return_value=1)
        client = _build_app(store, kb_store=kb_store, chat_config=_FakeConfig())

        import logging

        records = []

        class _Cap(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Cap()
        audit_log = logging.getLogger("bedrock.audit")
        old_level = audit_log.level
        audit_log.setLevel(logging.DEBUG)
        audit_log.addHandler(handler)
        try:
            client.patch(f"/admin/feedback/{entry.id}", json={"review_status": "approved"})
        finally:
            audit_log.removeHandler(handler)
            audit_log.setLevel(old_level)

        assert any(getattr(r, "credibility_docs_adjusted", None) == 1 for r in records)


# ===========================================================================
# T4 — Synthesizer cited-doc-ID path (Phase 5)
# ===========================================================================


def _load_synthesizer():
    rag_pkg = types.ModuleType("autolangchat.rag")
    rag_pkg.__path__ = []

    embedding_pipeline_mod = load_module("autolangchat.rag.embedding_pipeline", "rag/embedding_pipeline.py")
    feedback_base_mod = load_module(
        "autolangchat.db.feedback_base",
        "db/feedback_base.py",
        extra_modules={
            "autolangchat.exceptions": _exceptions_mod,
            "autolangchat.models": _models_mod,
        },
    )

    installed = {
        "autolangchat": types.ModuleType("autolangchat"),
        "autolangchat.admin": types.ModuleType("autolangchat.admin"),
        "autolangchat.db": types.ModuleType("autolangchat.db"),
        "autolangchat.rag": rag_pkg,
        "autolangchat.rag.embedding_pipeline": embedding_pipeline_mod,
        "autolangchat.models": _models_mod,
        "autolangchat.exceptions": _exceptions_mod,
        "autolangchat.db.feedback_base": feedback_base_mod,
        "autolangchat.db.kb_base": _kb_base_mod,
    }
    for pkg in ("autolangchat", "autolangchat.admin", "autolangchat.db"):
        installed[pkg].__path__ = []

    original = {name: sys.modules.get(name) for name in installed}
    try:
        sys.modules.update(installed)
        from importlib.util import module_from_spec, spec_from_file_location
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "autolangchat" / "admin" / "synthesizer.py"
        spec = spec_from_file_location("autolangchat.admin.synthesizer", path)
        mod = module_from_spec(spec)
        sys.modules["autolangchat.admin.synthesizer"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for name, previous in original.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


_synth_mod = _load_synthesizer()
FeedbackSynthesizer = _synth_mod.FeedbackSynthesizer
SynthesisAction = _synth_mod.SynthesisAction


def _approved_entry(tags=None, kb_sources_used=None):
    return FeedbackEntry(
        session_id="sess-test",
        user_id="expert-1",
        query="What is the workload type?",
        ai_response="It is Y.",
        rating=Rating.NEGATIVE,
        correction_text="The correct answer is X.",
        reviewer_tags=tags or ["perf"],
        reviewer_id="reviewer-1",
        review_status=ReviewStatus.APPROVED,
        reviewed_at=datetime.now(timezone.utc),
        model_id="anthropic.claude-test",
        kb_sources_used=kb_sources_used or [],
    )


def _make_kb_store_with_doc(doc_id, content="Existing content"):
    store = MagicMock()
    doc_dict = {
        "id": doc_id,
        "content": content,
        "title": "Existing article",
        "source": "feedback",
        "source_url": None,
        "topic": None,
        "date_published": None,
        "metadata": {"tags": ["perf"], "synthesized": True, "source_feedback_ids": []},
        "created_at": None,
        "credibility_score": 1.0,
        "removal_flagged": False,
    }
    store.list_documents = MagicMock(return_value=[])  # tag-based returns nothing
    store.get_document = MagicMock(return_value=doc_dict)  # cited ID resolves
    store.update_document = MagicMock()
    store.add_document = MagicMock()
    store.add_chunk = MagicMock()
    return store


def _make_kb_store_no_doc():
    store = MagicMock()
    tag_doc = KBDocument(
        id="synthesis-perf-tag",
        content="Tag-based existing content",
        title="Tag article",
        source="feedback",
        metadata={"tags": ["perf"], "synthesized": True, "source_feedback_ids": []},
    )
    store.list_documents = MagicMock(return_value=[tag_doc])
    store.get_document = MagicMock(return_value=None)  # cited ID deleted
    store.update_document = MagicMock()
    store.add_document = MagicMock()
    store.add_chunk = MagicMock()
    return store


def _llm_patch(action="update"):
    llm_json = json.dumps(
        {
            "title": "Updated Article",
            "problem": "Wrong info",
            "correct_methodology": "Use X",
            "key_terms": ["perf"],
            "examples": "Ex",
            "source_feedback_ids": [],
            "action": action,
        }
    )
    return patch.dict(
        sys.modules,
        {
            "langchain_aws": types.SimpleNamespace(
                ChatBedrockConverse=lambda **kw: MagicMock(ainvoke=AsyncMock(return_value=MagicMock(content=llm_json)))
            ),
            "langchain_core.messages": types.SimpleNamespace(SystemMessage=MagicMock, HumanMessage=MagicMock),
        },
    )


class TestSynthesizerCitedDocPath:
    @pytest.mark.asyncio
    async def test_cited_doc_id_overrides_tag_lookup(self):
        """When cited doc ID exists, synthesizer uses it instead of tag-based doc."""
        cited_id = "cited-doc-001"
        entry = _approved_entry(kb_sources_used=[{"document_id": cited_id, "score": 0.9}])
        kb_store = _make_kb_store_with_doc(cited_id)
        fb_store = MagicMock()
        fb_store.list_entries = AsyncMock(return_value=[entry])
        fb_store.mark_integrated = AsyncMock()

        bedrock_client = MagicMock()
        bedrock_client.generate_embeddings_batch = AsyncMock(return_value=[[0.1] * 1536])

        synth = FeedbackSynthesizer(model_id="anthropic.claude-test")
        with _llm_patch("update"):
            await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        # update_document called with the cited doc ID
        kb_store.get_document.assert_called_with(cited_id)
        update_call = kb_store.update_document.call_args
        assert update_call[0][0] == cited_id

    @pytest.mark.asyncio
    async def test_falls_back_to_tag_lookup_when_cited_doc_deleted(self):
        """When cited doc ID no longer exists, falls back to tag-based doc."""
        cited_id = "deleted-doc-999"
        entry = _approved_entry(kb_sources_used=[{"document_id": cited_id, "score": 0.9}])
        kb_store = _make_kb_store_no_doc()  # get_document returns None, list_documents returns tag doc
        fb_store = MagicMock()
        fb_store.list_entries = AsyncMock(return_value=[entry])
        fb_store.mark_integrated = AsyncMock()

        bedrock_client = MagicMock()
        bedrock_client.generate_embeddings_batch = AsyncMock(return_value=[[0.1] * 1536])

        synth = FeedbackSynthesizer(model_id="anthropic.claude-test")
        with _llm_patch("update"):
            await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        # Falls back to the tag-based doc
        update_call = kb_store.update_document.call_args
        assert update_call[0][0] == "synthesis-perf-tag"

    @pytest.mark.asyncio
    async def test_most_cited_doc_id_wins(self):
        """Most-cited document ID takes priority over the others."""
        doc_a = "doc-a"
        doc_b = "doc-b"
        entries = [
            _approved_entry(kb_sources_used=[{"document_id": doc_a, "score": 0.9}]),
            _approved_entry(kb_sources_used=[{"document_id": doc_b, "score": 0.8}]),
            _approved_entry(kb_sources_used=[{"document_id": doc_b, "score": 0.7}]),
        ]
        kb_store = _make_kb_store_with_doc(doc_b)

        # doc_a not found, doc_b found
        def _get_doc(doc_id):
            if doc_id == doc_b:
                return {
                    "id": doc_b,
                    "content": "b content",
                    "title": "B",
                    "source": "feedback",
                    "source_url": None,
                    "topic": None,
                    "date_published": None,
                    "metadata": {"tags": ["perf"], "synthesized": True, "source_feedback_ids": []},
                    "created_at": None,
                    "credibility_score": 1.0,
                    "removal_flagged": False,
                }
            return None

        kb_store.get_document = MagicMock(side_effect=_get_doc)

        fb_store = MagicMock()
        fb_store.list_entries = AsyncMock(return_value=entries)
        fb_store.mark_integrated = AsyncMock()
        bedrock_client = MagicMock()
        bedrock_client.generate_embeddings_batch = AsyncMock(return_value=[[0.1] * 1536])

        synth = FeedbackSynthesizer(model_id="anthropic.claude-test")
        with _llm_patch("update"):
            await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        update_call = kb_store.update_document.call_args
        assert update_call[0][0] == doc_b

    @pytest.mark.asyncio
    async def test_source_document_ids_stored_in_metadata_on_create(self):
        cited_id = "some-cited-doc"
        entry = _approved_entry(
            tags=["newtag"],
            kb_sources_used=[{"document_id": cited_id, "score": 0.9}],
        )
        kb_store = MagicMock()
        kb_store.list_documents = MagicMock(return_value=[])
        kb_store.get_document = MagicMock(return_value=None)  # cited doc deleted → CREATE path
        kb_store.add_document = MagicMock()
        kb_store.add_chunk = MagicMock()

        fb_store = MagicMock()
        fb_store.list_entries = AsyncMock(return_value=[entry])
        fb_store.mark_integrated = AsyncMock()
        bedrock_client = MagicMock()
        bedrock_client.generate_embeddings_batch = AsyncMock(return_value=[[0.1] * 1536])

        synth = FeedbackSynthesizer(model_id="anthropic.claude-test")
        with _llm_patch("create"):
            await synth.synthesize_all(fb_store, kb_store, bedrock_client)

        add_call = kb_store.add_document.call_args
        metadata_arg = add_call[0][7]  # 8th positional arg (0-based) is metadata
        assert cited_id in metadata_arg.get("source_document_ids", [])


# ===========================================================================
# Citation Boost Node
# ===========================================================================

from autolangchat.graph.nodes.citation_boost import citation_boost_node  # noqa: E402


class _BoostConfig:
    kb_credibility_citation_boost_enabled = True
    kb_credibility_citation_boost = 0.05
    kb_credibility_removal_threshold = 0.3


class TestCitationBoostNode:
    @pytest.mark.asyncio
    async def test_boost_fires_when_enabled(self):
        doc_id = "doc-boost-1"
        kb_store = MagicMock()
        kb_store.adjust_credibility = MagicMock(return_value=1)

        state = {"kb_results": [{"document_id": doc_id, "title": "Doc A"}]}
        config = {"configurable": {"chat_config": _BoostConfig(), "kb_store": kb_store}}

        await citation_boost_node(state, config)

        kb_store.adjust_credibility.assert_called_once_with(
            [doc_id],
            _BoostConfig.kb_credibility_citation_boost,
            _BoostConfig.kb_credibility_removal_threshold,
        )

    @pytest.mark.asyncio
    async def test_boost_deduplicates_doc_ids(self):
        doc_id = "doc-dup"
        kb_store = MagicMock()
        kb_store.adjust_credibility = MagicMock(return_value=1)

        state = {
            "kb_results": [
                {"document_id": doc_id},
                {"document_id": doc_id},  # duplicate
                {"document_id": "other-doc"},
            ]
        }
        config = {"configurable": {"chat_config": _BoostConfig(), "kb_store": kb_store}}

        await citation_boost_node(state, config)

        call_args = kb_store.adjust_credibility.call_args
        assert call_args[0][0] == [doc_id, "other-doc"]  # deduped, order preserved

    @pytest.mark.asyncio
    async def test_boost_does_not_fire_when_disabled(self):
        kb_store = MagicMock()

        class _Disabled(_BoostConfig):
            kb_credibility_citation_boost_enabled = False

        state = {"kb_results": [{"document_id": "doc-abc"}]}
        config = {"configurable": {"chat_config": _Disabled(), "kb_store": kb_store}}

        await citation_boost_node(state, config)

        kb_store.adjust_credibility.assert_not_called()

    @pytest.mark.asyncio
    async def test_boost_does_not_fire_when_no_kb_results(self):
        kb_store = MagicMock()

        state = {"kb_results": []}
        config = {"configurable": {"chat_config": _BoostConfig(), "kb_store": kb_store}}

        await citation_boost_node(state, config)

        kb_store.adjust_credibility.assert_not_called()

    @pytest.mark.asyncio
    async def test_boost_does_not_fire_when_no_kb_store(self):
        state = {"kb_results": [{"document_id": "doc-abc"}]}
        config = {"configurable": {"chat_config": _BoostConfig(), "kb_store": None}}

        result = await citation_boost_node(state, config)

        assert result == {}
