from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
feedback_routes_mod = load_module(
    "autolangchat.admin.admin_feedback_routes",
    "admin/admin_feedback_routes.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.admin.admin_errors": admin_errors_mod,
    },
)
kb_routes_mod = load_module(
    "autolangchat.admin.admin_kb_routes",
    "admin/admin_kb_routes.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.admin.admin_errors": admin_errors_mod,
    },
)
synth_mod = load_module(
    "autolangchat.admin.synthesizer",
    "admin/synthesizer.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
    },
)
synthesis_routes_mod = load_module(
    "autolangchat.admin.admin_synthesis_routes",
    "admin/admin_synthesis_routes.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.admin.admin_errors": admin_errors_mod,
        "autolangchat.admin.synthesizer": synth_mod,
    },
)

AdminAPIError = exceptions_mod.AdminAPIError
register_admin_error_handlers = admin_errors_mod.register_admin_error_handlers
register_admin_feedback_routes = feedback_routes_mod.register_admin_feedback_routes
register_admin_kb_routes = kb_routes_mod.register_admin_kb_routes
register_admin_synthesis_routes = synthesis_routes_mod.register_admin_synthesis_routes


class _FakeFeedbackStore:
    async def list_entries(self, *args, **kwargs):
        return []

    async def count_entries(self, *args, **kwargs):
        return 0

    async def stats(self):
        return models_mod.FeedbackStats()

    async def get(self, *args, **kwargs):
        return None

    async def update_review(self, *args, **kwargs):
        raise exceptions_mod.FeedbackNotFoundError("feedback not found")

    async def mark_integrated(self, *args, **kwargs):
        return None


class _FakeKBStore:
    def list_documents(self, *args, **kwargs):
        return []

    def count_documents(self, *args, **kwargs):
        return 0

    def get_document(self, *args, **kwargs):
        return None

    def update_document(self, *args, **kwargs):
        raise exceptions_mod.KBDocumentNotFoundError("kb document not found")

    def delete_document(self, *args, **kwargs):
        raise exceptions_mod.KBDocumentNotFoundError("kb document not found")


def _schema_ref(resp):
    return resp["content"]["application/json"]["schema"].get("$ref", "")


def _build_admin_app(*, authenticated=True):
    app = FastAPI()
    register_admin_error_handlers(app)

    if authenticated:

        async def require_admin():
            return SimpleNamespace(user_id="admin")

    else:

        async def require_admin():
            raise AdminAPIError(status_code=401, code="not_authenticated", detail="not authenticated")

    register_admin_feedback_routes(
        app,
        prefix="/bedrock-chat/admin",
        feedback_store=_FakeFeedbackStore(),
        require_admin=require_admin,
    )
    register_admin_kb_routes(
        app,
        prefix="/bedrock-chat/admin",
        kb_store=_FakeKBStore(),
        require_admin=require_admin,
        re_embed_document=AsyncMock(return_value=1),
    )
    register_admin_synthesis_routes(
        app,
        prefix="/bedrock-chat/admin",
        feedback_store=_FakeFeedbackStore(),
        kb_store=_FakeKBStore(),
        require_admin=require_admin,
        synthesizer=MagicMock(),
        embedding_client=MagicMock(),
    )
    return app


class TestOpenAPIPathCoverage:
    def test_feedback_kb_and_synthesis_paths_present(self):
        client = TestClient(_build_admin_app())
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]

        assert "/bedrock-chat/admin/feedback" in paths
        assert "/bedrock-chat/admin/feedback/stats" in paths
        assert "/bedrock-chat/admin/feedback/{feedback_id}" in paths
        assert "/bedrock-chat/admin/kb/documents" in paths
        assert "/bedrock-chat/admin/kb/documents/{doc_id}" in paths
        assert "/bedrock-chat/admin/synthesis/status" in paths
        assert "/bedrock-chat/admin/synthesis/trigger" in paths
        assert "/bedrock-chat/admin/synthesis/trigger/{feedback_id}" in paths
        assert "/bedrock-chat/admin/synthesis/rollback/{article_id}" in paths


class TestOpenAPISuccessSchemas:
    def test_feedback_and_kb_success_schemas_present(self):
        client = TestClient(_build_admin_app())
        schema = client.get("/openapi.json").json()

        assert "FeedbackListResponse" in _schema_ref(
            schema["paths"]["/bedrock-chat/admin/feedback"]["get"]["responses"]["200"]
        )
        assert "FeedbackEntry" in _schema_ref(
            schema["paths"]["/bedrock-chat/admin/feedback/{feedback_id}"]["get"]["responses"]["200"]
        )
        assert "KBDocumentListResponse" in _schema_ref(
            schema["paths"]["/bedrock-chat/admin/kb/documents"]["get"]["responses"]["200"]
        )
        assert "204" in schema["paths"]["/bedrock-chat/admin/kb/documents/{doc_id}"]["delete"]["responses"]

    def test_synthesis_success_schemas_present(self):
        client = TestClient(_build_admin_app())
        schema = client.get("/openapi.json").json()

        assert "SynthesisStatus" in _schema_ref(
            schema["paths"]["/bedrock-chat/admin/synthesis/status"]["get"]["responses"]["200"]
        )
        assert "SynthesisStatus" in _schema_ref(
            schema["paths"]["/bedrock-chat/admin/synthesis/trigger"]["post"]["responses"]["202"]
        )
        assert "SingleEntrySynthesisResponse" in _schema_ref(
            schema["paths"]["/bedrock-chat/admin/synthesis/trigger/{feedback_id}"]["post"]["responses"]["200"]
        )
        assert "RollbackResponse" in _schema_ref(
            schema["paths"]["/bedrock-chat/admin/synthesis/rollback/{article_id}"]["post"]["responses"]["200"]
        )


class TestOpenAPIErrorEnvelopes:
    @pytest.mark.parametrize(
        "path,verb,codes",
        [
            ("/bedrock-chat/admin/feedback", "get", ("400", "401", "403", "404")),
            ("/bedrock-chat/admin/feedback/{feedback_id}", "patch", ("400", "401", "403", "404", "409")),
            ("/bedrock-chat/admin/kb/documents/{doc_id}", "patch", ("400", "401", "403", "404")),
            ("/bedrock-chat/admin/synthesis/trigger", "post", ("400", "401", "403", "404", "409")),
            (
                "/bedrock-chat/admin/synthesis/trigger/{feedback_id}",
                "post",
                ("400", "401", "403", "404", "409", "422", "500"),
            ),
        ],
    )
    def test_route_advertises_error_envelopes(self, path, verb, codes):
        client = TestClient(_build_admin_app())
        schema = client.get("/openapi.json").json()
        op = schema["paths"][path][verb]

        for code in codes:
            assert code in op["responses"]
            if code != "204":
                assert "ErrorResponse" in _schema_ref(op["responses"][code])

    def test_error_response_schema_shape(self):
        client = TestClient(_build_admin_app())
        schema = client.get("/openapi.json").json()
        comp = schema["components"]["schemas"]["ErrorResponse"]
        props = comp["properties"]
        assert "code" in props
        assert "detail" in props
        assert "errors" in props


class TestLiveErrorEnvelope:
    def test_unauthenticated_401_envelope_is_flat(self):
        client = TestClient(_build_admin_app(authenticated=False))
        resp = client.get("/bedrock-chat/admin/feedback")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "not_authenticated"
        assert isinstance(body["detail"], str)
