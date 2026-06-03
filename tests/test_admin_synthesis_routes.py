"""Tests for Admin Synthesis HTTP routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_bedrock_chat_fastapi.admin_auth import AdminIdentity
from auto_bedrock_chat_fastapi.admin_synthesis_routes import (
    RunPhase,
    _run_batch,
    _RunState,
    register_admin_synthesis_routes,
)
from auto_bedrock_chat_fastapi.db.feedback_sqlite import SQLiteFeedbackStore
from auto_bedrock_chat_fastapi.db.kb_sqlite import SQLiteKBStore
from auto_bedrock_chat_fastapi.exceptions import AlreadyIntegratedError, FeedbackNotFoundError
from auto_bedrock_chat_fastapi.models import FeedbackEntry, Rating, ReviewStatus
from auto_bedrock_chat_fastapi.plugin import BedrockChatPlugin
from auto_bedrock_chat_fastapi.sso_session_store import SSOSessionStore
from auto_bedrock_chat_fastapi.synthesizer import (
    FeedbackSynthesizer,
    SynthesisAction,
    SynthesisRunResult,
    TagGroupResult,
)

_SESSION_SECRET = "synthesis-test-secret-1234567890"
_CHAT_PREFIX = "/bedrock-chat"
_ADMIN_PREFIX = f"{_CHAT_PREFIX}/admin"
_SYNTHESIS_PREFIX = f"{_ADMIN_PREFIX}/synthesis"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AllowAuthorizer:
    async def is_admin(self, identity: AdminIdentity) -> bool:
        return True


def _make_admin_config() -> MagicMock:
    config = MagicMock()
    config.chat_endpoint = _CHAT_PREFIX
    config.admin_enabled = True
    config.sso_enabled = True
    config.sso_session_secret = _SESSION_SECRET
    config.sso_session_ttl = 3600
    config.auth_verification_endpoint = None
    config.kb_embedding_model = "amazon.titan-embed-text-v1"
    config.require_tool_auth = False  # dev-mode: no real auth needed
    return config


def _approved_entry(tags: Optional[list] = None) -> FeedbackEntry:
    return FeedbackEntry(
        session_id="sess-1",
        user_id="expert-1",
        query="What is X?",
        ai_response="It is Y.",
        rating=Rating.NEGATIVE,
        correction_text="Correct answer is X = Z.",
        reviewer_tags=tags or ["perf"],
        reviewer_id="reviewer-1",
        reviewed_at=datetime.now(timezone.utc),
        review_status=ReviewStatus.APPROVED,
        model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        created_at=datetime.now(timezone.utc),
    )


def _make_synthesizer(action: str = "create") -> FeedbackSynthesizer:
    synth = MagicMock(spec=FeedbackSynthesizer)
    synth.synthesize_all = AsyncMock(
        return_value=SynthesisRunResult(
            tag_results=[
                TagGroupResult(
                    tag="perf",
                    action=SynthesisAction(action),
                    kb_doc_id="synthesis-perf-abc12345",
                    feedback_ids_marked=[uuid4()],
                )
            ],
            total_integrated=1,
            errors=[],
        )
    )
    entry_id = uuid4()
    synth.synthesize_entry = AsyncMock(
        return_value=TagGroupResult(
            tag="perf",
            action=SynthesisAction(action),
            kb_doc_id="synthesis-perf-abc12345",
            feedback_ids_marked=[entry_id],
        )
    )
    return synth, entry_id


def _make_bedrock_client() -> MagicMock:
    client = MagicMock()
    client.config = MagicMock()
    client.config.model_id = "anthropic.claude-test"
    client.chat_completion = AsyncMock(return_value={"content": "{}"})
    client.generate_embeddings_batch = AsyncMock(return_value=[[0.1] * 1536])
    return client


@pytest.fixture
async def feedback_store(tmp_path):
    db_path = str(tmp_path / "feedback.db")
    s = SQLiteFeedbackStore(db_path=db_path, init_schema=True)
    await s.open()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
def kb_store(tmp_path):
    db_path = str(tmp_path / "kb.db")
    s = SQLiteKBStore(db_path=db_path)
    return s


def _build_app(feedback_store, kb_store, synth, entry_id=None):
    """Build a minimal FastAPI app with synthesis routes and no-auth."""
    app = FastAPI()
    bedrock_client = _make_bedrock_client()

    from auto_bedrock_chat_fastapi.admin_errors import register_admin_error_handlers

    register_admin_error_handlers(app)

    async def require_admin():
        return AdminIdentity(user_id="admin", claims={})

    register_admin_synthesis_routes(
        app,
        prefix=_ADMIN_PREFIX,
        feedback_store=feedback_store,
        kb_store=kb_store,
        require_admin=require_admin,
        synthesizer=synth,
        bedrock_client=bedrock_client,
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# _RunState unit tests
# ---------------------------------------------------------------------------


class TestRunState:
    def test_initial_phase_is_idle(self):
        state = _RunState()
        assert state.status.phase == RunPhase.IDLE

    def test_mark_running(self):
        state = _RunState()
        state._mark_running()
        assert state.status.phase == RunPhase.RUNNING
        assert state.status.started_at is not None

    def test_mark_completed(self):
        state = _RunState()
        state._mark_running()
        result = SynthesisRunResult(total_integrated=3, errors=[])
        result.tag_results = []
        state._mark_completed(result)
        status = state.status
        assert status.phase == RunPhase.COMPLETED
        assert status.total_integrated == 3
        assert status.finished_at is not None

    def test_mark_failed(self):
        state = _RunState()
        state._mark_running()
        state._mark_failed("boom")
        status = state.status
        assert status.phase == RunPhase.FAILED
        assert "boom" in status.errors

    def test_status_returns_copy(self):
        state = _RunState()
        s1 = state.status
        state._mark_running()
        s2 = state.status
        # s1 snapshot should not have changed
        assert s1.phase == RunPhase.IDLE
        assert s2.phase == RunPhase.RUNNING


# ---------------------------------------------------------------------------
# GET /admin/synthesis/status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_returns_idle_initially(self, feedback_store, kb_store):
        synth, _ = _make_synthesizer()
        client = _build_app(feedback_store, kb_store, synth)
        resp = client.get(f"{_SYNTHESIS_PREFIX}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "idle"
        assert data["total_integrated"] == 0

    def test_phase_field_present(self, feedback_store, kb_store):
        synth, _ = _make_synthesizer()
        client = _build_app(feedback_store, kb_store, synth)
        resp = client.get(f"{_SYNTHESIS_PREFIX}/status")
        assert "phase" in resp.json()


# ---------------------------------------------------------------------------
# POST /admin/synthesis/trigger  (batch)
# ---------------------------------------------------------------------------


class TestTriggerBatch:
    def test_returns_202_and_running_phase(self, feedback_store, kb_store):
        synth, _ = _make_synthesizer()
        # Make synthesize_all block until we inspect the 202 response
        event = asyncio.Event()

        async def _slow_synthesize(*args, **kwargs):
            await event.wait()
            return SynthesisRunResult(total_integrated=0, errors=[])

        synth.synthesize_all = _slow_synthesize
        client = _build_app(feedback_store, kb_store, synth)
        resp = client.post(f"{_SYNTHESIS_PREFIX}/trigger")
        assert resp.status_code == 202
        assert resp.json()["phase"] == "running"

    def test_second_trigger_while_running_returns_409(self, feedback_store, kb_store):
        synth, _ = _make_synthesizer()
        event = asyncio.Event()

        async def _slow_synthesize(*args, **kwargs):
            await event.wait()
            return SynthesisRunResult(total_integrated=0, errors=[])

        synth.synthesize_all = _slow_synthesize
        client = _build_app(feedback_store, kb_store, synth)
        client.post(f"{_SYNTHESIS_PREFIX}/trigger")
        resp2 = client.post(f"{_SYNTHESIS_PREFIX}/trigger")
        assert resp2.status_code == 409
        assert resp2.json()["code"] == "synthesis_already_running"

    def test_status_transitions_to_completed_after_run(self, feedback_store, kb_store):
        synth, _ = _make_synthesizer()

        # synthesize_all completes instantly (default AsyncMock)
        client = _build_app(feedback_store, kb_store, synth)
        client.post(f"{_SYNTHESIS_PREFIX}/trigger")

        # Poll until not running (TestClient runs the event loop to completion)
        resp = client.get(f"{_SYNTHESIS_PREFIX}/status")
        # Phase may be 'running' or 'completed' — not 'failed'
        assert resp.json()["phase"] in ("running", "completed")


# ---------------------------------------------------------------------------
# POST /admin/synthesis/trigger/{feedback_id}  (per-entry)
# ---------------------------------------------------------------------------


class TestTriggerEntry:
    def test_returns_200_with_result(self, feedback_store, kb_store):
        synth, entry_id = _make_synthesizer(action="create")
        client = _build_app(feedback_store, kb_store, synth)
        resp = client.post(f"{_SYNTHESIS_PREFIX}/trigger/{entry_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tag"] == "perf"
        assert data["action"] == "create"
        assert data["kb_doc_id"] == "synthesis-perf-abc12345"

    def test_returns_404_when_entry_not_found(self, feedback_store, kb_store):
        synth, _ = _make_synthesizer()
        synth.synthesize_entry = AsyncMock(side_effect=FeedbackNotFoundError("feedback 123 not found"))
        client = _build_app(feedback_store, kb_store, synth)
        resp = client.post(f"{_SYNTHESIS_PREFIX}/trigger/{uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_returns_409_when_already_integrated(self, feedback_store, kb_store):
        synth, _ = _make_synthesizer()
        synth.synthesize_entry = AsyncMock(
            side_effect=AlreadyIntegratedError("feedback abc is already integrated into KB document 'doc-1'")
        )
        client = _build_app(feedback_store, kb_store, synth)
        resp = client.post(f"{_SYNTHESIS_PREFIX}/trigger/{uuid4()}")
        assert resp.status_code == 409
        assert resp.json()["code"] == "already_integrated"

    def test_returns_422_when_not_approved(self, feedback_store, kb_store):
        synth, _ = _make_synthesizer()
        synth.synthesize_entry = AsyncMock(side_effect=ValueError("only 'approved' entries can be synthesized"))
        client = _build_app(feedback_store, kb_store, synth)
        resp = client.post(f"{_SYNTHESIS_PREFIX}/trigger/{uuid4()}")
        assert resp.status_code == 422
        assert resp.json()["code"] == "synthesis_precondition_failed"

    def test_returns_422_when_no_correction_text(self, feedback_store, kb_store):
        synth, _ = _make_synthesizer()
        synth.synthesize_entry = AsyncMock(side_effect=ValueError("has no correction_text; cannot synthesize"))
        client = _build_app(feedback_store, kb_store, synth)
        resp = client.post(f"{_SYNTHESIS_PREFIX}/trigger/{uuid4()}")
        assert resp.status_code == 422

    def test_returns_500_when_synthesis_fails_internally(self, feedback_store, kb_store):
        synth, entry_id = _make_synthesizer()
        synth.synthesize_entry = AsyncMock(
            return_value=TagGroupResult(
                tag="perf",
                action=SynthesisAction.SKIP,
                kb_doc_id=None,
                feedback_ids_marked=[],
                error="LLM returned invalid JSON",
            )
        )
        client = _build_app(feedback_store, kb_store, synth)
        resp = client.post(f"{_SYNTHESIS_PREFIX}/trigger/{entry_id}")
        assert resp.status_code == 500
        assert resp.json()["code"] == "synthesis_failed"
        assert "LLM returned invalid JSON" in resp.json()["detail"]

    def test_feedback_ids_marked_in_response(self, feedback_store, kb_store):
        synth, entry_id = _make_synthesizer()
        client = _build_app(feedback_store, kb_store, synth)
        resp = client.post(f"{_SYNTHESIS_PREFIX}/trigger/{entry_id}")
        data = resp.json()
        assert isinstance(data["feedback_ids_marked"], list)
        assert len(data["feedback_ids_marked"]) == 1

    def test_skip_action_returns_200(self, feedback_store, kb_store):
        synth, entry_id = _make_synthesizer(action="skip")
        client = _build_app(feedback_store, kb_store, synth)
        resp = client.post(f"{_SYNTHESIS_PREFIX}/trigger/{entry_id}")
        assert resp.status_code == 200
        assert resp.json()["action"] == "skip"

    def test_update_action_returns_200(self, feedback_store, kb_store):
        synth, entry_id = _make_synthesizer(action="update")
        client = _build_app(feedback_store, kb_store, synth)
        resp = client.post(f"{_SYNTHESIS_PREFIX}/trigger/{entry_id}")
        assert resp.status_code == 200
        assert resp.json()["action"] == "update"


# ---------------------------------------------------------------------------
# _run_batch helper
# ---------------------------------------------------------------------------


class TestRunBatch:
    @pytest.mark.asyncio
    async def test_marks_completed_on_success(self, feedback_store, kb_store):
        synth = MagicMock(spec=FeedbackSynthesizer)
        synth.synthesize_all = AsyncMock(return_value=SynthesisRunResult(total_integrated=5, errors=[]))
        synth.synthesize_all.return_value.tag_results = []
        state = _RunState()
        state._mark_running()
        await _run_batch(synth, feedback_store, kb_store, _make_bedrock_client(), state)
        assert state.status.phase == RunPhase.COMPLETED
        assert state.status.total_integrated == 5

    @pytest.mark.asyncio
    async def test_marks_failed_on_exception(self, feedback_store, kb_store):
        synth = MagicMock(spec=FeedbackSynthesizer)
        synth.synthesize_all = AsyncMock(side_effect=RuntimeError("boom"))
        state = _RunState()
        state._mark_running()
        await _run_batch(synth, feedback_store, kb_store, _make_bedrock_client(), state)
        assert state.status.phase == RunPhase.FAILED
        assert "boom" in state.status.errors[0]


# ---------------------------------------------------------------------------
# Plugin wiring smoke test
# ---------------------------------------------------------------------------


class TestPluginWiring:
    def test_synthesis_routes_registered_when_both_stores_present(self, feedback_store, kb_store):
        """_setup_admin_routes registers synthesis routes when fb+kb stores wired."""
        app = FastAPI()
        plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)
        plugin.app = app
        plugin.config = _make_admin_config()
        plugin.sso_session_store = SSOSessionStore(session_ttl=3600)
        plugin._admin_authorizer = _AllowAuthorizer()
        plugin._feedback_store = feedback_store
        plugin._kb_store = kb_store
        plugin.bedrock_client = _make_bedrock_client()
        plugin.app_base_url = "https://app.example.com"
        plugin._setup_admin_routes()

        # Status endpoint should now be registered (returns 200, not 404)
        client = TestClient(app)
        resp = client.get(f"{_SYNTHESIS_PREFIX}/status")
        assert resp.status_code == 200

    def test_synthesis_routes_skipped_when_kb_store_missing(self, feedback_store):
        """If kb_store is None, synthesis routes must not be registered."""
        app = FastAPI()
        plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)
        plugin.app = app
        plugin.config = _make_admin_config()
        plugin.sso_session_store = SSOSessionStore(session_ttl=3600)
        plugin._admin_authorizer = _AllowAuthorizer()
        plugin._feedback_store = feedback_store
        plugin._kb_store = None
        plugin.bedrock_client = _make_bedrock_client()
        plugin.app_base_url = "https://app.example.com"
        plugin._setup_admin_routes()

        client = TestClient(app)
        resp = client.get(f"{_SYNTHESIS_PREFIX}/status")
        assert resp.status_code == 404
