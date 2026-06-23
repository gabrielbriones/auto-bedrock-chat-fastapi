from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ._autolangchat_imports import load_module

exceptions_mod = load_module("autolangchat.exceptions", "exceptions.py")
models_mod = load_module("autolangchat.models", "models.py")
admin_errors_mod = load_module(
    "autolangchat.admin.admin_errors",
    "admin/admin_errors.py",
    extra_modules={"autolangchat.exceptions": exceptions_mod, "autolangchat.models": models_mod},
)
synthed_mod = load_module(
    "autolangchat.admin.synthesizer",
    "admin/synthesizer.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.admin.admin_errors": admin_errors_mod,
    },
)
synthesis_routes_mod = load_module(
    "autolangchat.admin.admin_synthesis_routes",
    "admin/admin_synthesis_routes.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.admin.admin_errors": admin_errors_mod,
        "autolangchat.admin.synthesizer": synthed_mod,
    },
)

FeedbackEntry = models_mod.FeedbackEntry
Rating = models_mod.Rating
ReviewStatus = models_mod.ReviewStatus
FeedbackSynthesizer = synthed_mod.FeedbackSynthesizer
SynthesisAction = synthed_mod.SynthesisAction
SynthesisRunResult = synthed_mod.SynthesisRunResult
TagGroupResult = synthed_mod.TagGroupResult
RunPhase = synthesis_routes_mod.RunPhase
_RunState = synthesis_routes_mod._RunState
register_admin_error_handlers = admin_errors_mod.register_admin_error_handlers
register_admin_synthesis_routes = synthesis_routes_mod.register_admin_synthesis_routes


class _Identity(SimpleNamespace):
    user_id: str = "admin"


class _FakeFeedbackStore:
    def __init__(self, entries=None):
        self.entries = list(entries or [])
        self.marked = []

    async def list_entries(self, filters, limit=1000, offset=0):
        items = [
            e for e in self.entries if e.review_status == ReviewStatus.APPROVED and e.integrated_into_kb_id is None
        ]
        return items[offset : offset + limit]

    async def get(self, feedback_id):
        for entry in self.entries:
            if entry.id == feedback_id:
                return entry
        return None

    async def mark_integrated(self, feedback_id, kb_doc_id, when):
        self.marked.append((feedback_id, kb_doc_id, when))


class _FakeKBStore:
    def __init__(self):
        self.docs = []

    async def list_documents(self, filters, limit=50, offset=0):
        return []

    async def get_document(self, doc_id):
        return None

    async def add_document(self, *args, **kwargs):
        return None

    async def update_document(self, *args, **kwargs):
        return None


def _make_entry(tags=None, status=ReviewStatus.APPROVED):
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
        review_status=status,
        model_id="anthropic.claude-test",
        created_at=datetime.now(timezone.utc),
    )


def _make_synthesizer(action="create"):
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


def _build_app(feedback_store, kb_store, synth):
    app = FastAPI()
    register_admin_error_handlers(app)

    async def require_admin():
        return _Identity(user_id="admin")

    register_admin_synthesis_routes(
        app,
        prefix="/bedrock-chat/admin",
        feedback_store=feedback_store,
        kb_store=kb_store,
        require_admin=require_admin,
        synthesizer=synth,
        embedding_client=MagicMock(),
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_run_state_transitions():
    state = _RunState()
    assert state.status.phase == RunPhase.IDLE
    assert await state.try_claim_run() is True
    assert state.status.phase == RunPhase.RUNNING


@pytest.mark.asyncio
async def test_trigger_batch_reports_running_and_status():
    feedback_store = _FakeFeedbackStore([_make_entry()])
    kb_store = _FakeKBStore()
    synth, _ = _make_synthesizer()
    client = _build_app(feedback_store, kb_store, synth)
    resp = client.post("/bedrock-chat/admin/synthesis/trigger")
    assert resp.status_code == 202
    assert resp.json()["phase"] in ("running", "completed")


def test_status_endpoint_returns_idle_initially():
    feedback_store = _FakeFeedbackStore([])
    kb_store = _FakeKBStore()
    synth, _ = _make_synthesizer()
    client = _build_app(feedback_store, kb_store, synth)
    resp = client.get("/bedrock-chat/admin/synthesis/status")
    assert resp.status_code == 200
    assert resp.json()["phase"] == "idle"


def test_single_entry_trigger_returns_result():
    feedback_store = _FakeFeedbackStore([_make_entry()])
    kb_store = _FakeKBStore()
    synth, entry_id = _make_synthesizer(action="create")
    client = _build_app(feedback_store, kb_store, synth)
    resp = client.post(f"/bedrock-chat/admin/synthesis/trigger/{entry_id}")
    assert resp.status_code == 200
    assert resp.json()["tag"] == "perf"


def test_single_entry_trigger_missing_returns_404():
    feedback_store = _FakeFeedbackStore([])
    kb_store = _FakeKBStore()
    synth, _ = _make_synthesizer()
    synth.synthesize_entry = AsyncMock(side_effect=exceptions_mod.FeedbackNotFoundError("feedback not found"))
    client = _build_app(feedback_store, kb_store, synth)
    resp = client.post(f"/bedrock-chat/admin/synthesis/trigger/{uuid4()}")
    assert resp.status_code == 404
