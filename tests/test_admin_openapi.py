"""Tests for the standardized admin-API OpenAPI schema (XMGPLAT-10417, T6).

Verifies that ``/openapi.json`` advertises:
- every admin endpoint (path + verb)
- the correct success-response schema
- the standardized ``ErrorResponse`` envelope for 400 / 401 / 403 / 404
  / 409 (PATCH-feedback only)
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_bedrock_chat_fastapi.admin_auth import AdminAuthorizer, AdminIdentity
from auto_bedrock_chat_fastapi.config import ChatConfig
from auto_bedrock_chat_fastapi.db.feedback_sqlite import SQLiteFeedbackStore
from auto_bedrock_chat_fastapi.db.kb_sqlite import SQLiteKBStore
from auto_bedrock_chat_fastapi.plugin import BedrockChatPlugin
from auto_bedrock_chat_fastapi.sso_session_store import SSOSessionStore


class _AllowAuthorizer(AdminAuthorizer):
    async def is_admin(self, identity: AdminIdentity) -> bool:
        return True


def _make_admin_config() -> ChatConfig:
    return ChatConfig(
        BEDROCK_MODEL_ID="anthropic.claude-sonnet-4-5-20250929-v1:0",
        AWS_REGION="us-east-1",
        SSO_ENABLED="true",
        SSO_CLIENT_ID="cid",
        SSO_CLIENT_SECRET="csec",
        SSO_AUTHORIZATION_URL="https://idp.example.com/authorize",
        SSO_TOKEN_URL="https://idp.example.com/token",
        SSO_USERINFO_URL="https://idp.example.com/userinfo",
        SSO_REDIRECT_URI="https://app.example.com/auth/sso/callback",
        SSO_SESSION_SECRET="x" * 32,
        ADMIN_ENABLED="true",
        # Keep admin auth strict: ``require_tool_auth=False`` (the default)
        # would let unauthenticated callers in as anonymous admin, which
        # would defeat the 401 / 403 assertions further down.
        BEDROCK_REQUIRE_TOOL_AUTH="true",
    )


@pytest.fixture
async def admin_app() -> Tuple[FastAPI, str]:
    """Build a plugin with feedback + KB stores wired so every admin route registers."""
    fb_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    kb_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        fb_store = SQLiteFeedbackStore(fb_path)
        await fb_store.open()
        kb_store = SQLiteKBStore(kb_path)

        app = FastAPI()
        plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)
        plugin.app = app
        plugin.config = _make_admin_config()
        plugin.sso_session_store = SSOSessionStore(session_ttl=3600)
        plugin._admin_authorizer = _AllowAuthorizer()
        plugin._feedback_store = fb_store
        plugin._kb_store = kb_store
        plugin.bedrock_client = None  # Re-embed callback not invoked from OpenAPI inspection
        plugin.app_base_url = "https://app.example.com"
        plugin._setup_admin_routes()

        prefix = f"{plugin.config.chat_endpoint}/admin"
        yield app, prefix

        await fb_store.close()
        kb_store.close()
    finally:
        for p in (fb_path, kb_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _schema_ref(resp: Dict[str, Any]) -> str:
    """Extract the $ref from the application/json content of a response object."""
    return resp["content"]["application/json"]["schema"].get("$ref", "")


# ---------------------------------------------------------------------------
# Path / verb coverage
# ---------------------------------------------------------------------------


class TestOpenAPIPathCoverage:
    def test_feedback_paths_present(self, admin_app):
        app, prefix = admin_app
        client = TestClient(app)
        schema = client.get("/openapi.json").json()

        paths = schema["paths"]
        assert f"{prefix}/feedback" in paths
        assert "get" in paths[f"{prefix}/feedback"]
        assert f"{prefix}/feedback/stats" in paths
        assert "get" in paths[f"{prefix}/feedback/stats"]
        assert f"{prefix}/feedback/{{feedback_id}}" in paths
        assert {"get", "patch"} <= paths[f"{prefix}/feedback/{{feedback_id}}"].keys()

    def test_kb_paths_present(self, admin_app):
        app, prefix = admin_app
        client = TestClient(app)
        schema = client.get("/openapi.json").json()

        paths = schema["paths"]
        assert f"{prefix}/kb/documents" in paths
        assert "get" in paths[f"{prefix}/kb/documents"]
        assert f"{prefix}/kb/documents/{{doc_id}}" in paths
        ops = paths[f"{prefix}/kb/documents/{{doc_id}}"]
        assert {"get", "patch", "delete"} <= ops.keys()


# ---------------------------------------------------------------------------
# Success-schema coverage
# ---------------------------------------------------------------------------


class TestOpenAPISuccessSchemas:
    def test_list_feedback_returns_feedback_list_response(self, admin_app):
        app, prefix = admin_app
        client = TestClient(app)
        schema = client.get("/openapi.json").json()

        op = schema["paths"][f"{prefix}/feedback"]["get"]
        assert "FeedbackListResponse" in _schema_ref(op["responses"]["200"])

    def test_get_feedback_returns_feedback_entry(self, admin_app):
        app, prefix = admin_app
        client = TestClient(app)
        schema = client.get("/openapi.json").json()

        op = schema["paths"][f"{prefix}/feedback/{{feedback_id}}"]["get"]
        assert "FeedbackEntry" in _schema_ref(op["responses"]["200"])

    def test_list_kb_documents_returns_envelope(self, admin_app):
        app, prefix = admin_app
        client = TestClient(app)
        schema = client.get("/openapi.json").json()

        op = schema["paths"][f"{prefix}/kb/documents"]["get"]
        assert "KBDocumentListResponse" in _schema_ref(op["responses"]["200"])

    def test_delete_kb_document_has_204(self, admin_app):
        app, prefix = admin_app
        client = TestClient(app)
        schema = client.get("/openapi.json").json()

        op = schema["paths"][f"{prefix}/kb/documents/{{doc_id}}"]["delete"]
        assert "204" in op["responses"]


# ---------------------------------------------------------------------------
# Error envelope coverage
# ---------------------------------------------------------------------------


class TestOpenAPIErrorEnvelopes:
    @pytest.mark.parametrize(
        "path_suffix,verb",
        [
            ("/feedback", "get"),
            ("/feedback/stats", "get"),
            ("/feedback/{feedback_id}", "get"),
            ("/feedback/{feedback_id}", "patch"),
            ("/kb/documents", "get"),
            ("/kb/documents/{doc_id}", "get"),
            ("/kb/documents/{doc_id}", "patch"),
            ("/kb/documents/{doc_id}", "delete"),
        ],
    )
    def test_route_advertises_error_envelopes(self, admin_app, path_suffix, verb):
        app, prefix = admin_app
        client = TestClient(app)
        schema = client.get("/openapi.json").json()

        op = schema["paths"][f"{prefix}{path_suffix}"][verb]
        for code in ("400", "401", "403", "404"):
            assert code in op["responses"], f"{verb.upper()} {path_suffix} missing {code} response"
            assert "ErrorResponse" in _schema_ref(
                op["responses"][code]
            ), f"{verb.upper()} {path_suffix} {code} response is not ErrorResponse"

    def test_feedback_patch_advertises_409(self, admin_app):
        app, prefix = admin_app
        client = TestClient(app)
        schema = client.get("/openapi.json").json()

        op = schema["paths"][f"{prefix}/feedback/{{feedback_id}}"]["patch"]
        assert "409" in op["responses"]
        assert "ErrorResponse" in _schema_ref(op["responses"]["409"])

    def test_kb_patch_does_not_advertise_409(self, admin_app):
        """KB documents have no status-transition concept → no 409."""
        app, prefix = admin_app
        client = TestClient(app)
        schema = client.get("/openapi.json").json()

        op = schema["paths"][f"{prefix}/kb/documents/{{doc_id}}"]["patch"]
        assert "409" not in op["responses"]

    def test_error_response_schema_shape(self, admin_app):
        app, _prefix = admin_app
        client = TestClient(app)
        schema = client.get("/openapi.json").json()

        comp = schema["components"]["schemas"]["ErrorResponse"]
        props = comp["properties"]
        assert "code" in props
        assert "detail" in props
        assert "errors" in props
        assert set(comp.get("required", [])) >= {"code", "detail"}


# ---------------------------------------------------------------------------
# Live error response shape (end-to-end smoke)
# ---------------------------------------------------------------------------


class TestLiveErrorEnvelope:
    def test_unauthenticated_401_envelope(self, admin_app):
        """Hit a real route without auth and verify the wire format matches the schema."""
        app, prefix = admin_app
        client = TestClient(app)
        resp = client.get(f"{prefix}/feedback")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "not_authenticated"
        assert "detail" in body
        # The flat envelope must NOT nest a dict under "detail".
        assert isinstance(body["detail"], str)
