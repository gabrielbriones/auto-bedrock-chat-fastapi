"""Tests for the ``feedbackEnabled`` flag rendered into ``window.CONFIG``.

Covers task T1 of the Feedback Rating UI plan: server-side gating of the
feedback UI via the chat HTML template context.
"""

from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_bedrock_chat_fastapi import add_bedrock_chat
from auto_bedrock_chat_fastapi.db import AllowlistFeedbackAuthorizer
from auto_bedrock_chat_fastapi.sso_session_store import SSOSessionStore


def _mock_boto3():
    """Return a mock boto3 Session shape reused across plugin tests."""
    instance = Mock()
    instance.client.return_value = Mock()
    return instance


def _build_plugin_with_ui():
    app = FastAPI(title="Test App")
    plugin = add_bedrock_chat(app, enable_ui=True)
    return app, plugin


class TestFeedbackEnabledFlag:
    """T1: ``window.CONFIG.feedbackEnabled`` is server-rendered correctly."""

    @patch("boto3.Session")
    @patch("auto_bedrock_chat_fastapi.bedrock_client.boto3.Session")
    def test_flag_false_when_feedback_disabled_in_config(self, mock_bc, mock_b):
        mock_bc.return_value = _mock_boto3()
        mock_b.return_value = _mock_boto3()
        app, plugin = _build_plugin_with_ui()
        plugin.config.feedback_enabled = False
        plugin._feedback_store = None

        client = TestClient(app)
        response = client.get(plugin.config.ui_endpoint)

        assert response.status_code == 200
        assert "feedbackEnabled: false" in response.text

    @patch("boto3.Session")
    @patch("auto_bedrock_chat_fastapi.bedrock_client.boto3.Session")
    def test_flag_false_when_no_feedback_store(self, mock_bc, mock_b):
        """feedback_enabled=True but no FeedbackStore wired → flag is false."""
        mock_bc.return_value = _mock_boto3()
        mock_b.return_value = _mock_boto3()
        app, plugin = _build_plugin_with_ui()
        plugin.config.feedback_enabled = True
        plugin._feedback_store = None

        client = TestClient(app)
        response = client.get(plugin.config.ui_endpoint)

        assert response.status_code == 200
        assert "feedbackEnabled: false" in response.text

    @patch("boto3.Session")
    @patch("auto_bedrock_chat_fastapi.bedrock_client.boto3.Session")
    def test_flag_true_when_feature_enabled_and_store_present(self, mock_bc, mock_b):
        """HTTP gate is feature-only: store present + feedback_enabled → flag True.

        Per-user authorization is now deferred to the WebSocket handler;
        the authorizer is not consulted at HTTP-render time.
        """
        mock_bc.return_value = _mock_boto3()
        mock_b.return_value = _mock_boto3()
        app, plugin = _build_plugin_with_ui()
        plugin.config.feedback_enabled = True
        # auth_verification_endpoint is the signal that a user_id will
        # be populated in session.metadata at WS auth time.
        plugin.config.auth_verification_endpoint = "https://auth.example.com/verify"
        plugin._feedback_store = Mock()
        # Authorizer must NOT be consulted at HTTP time anymore.
        not_consulted = Mock()
        not_consulted.can_submit.return_value = False
        plugin.websocket_handler.feedback_authorizer = not_consulted

        client = TestClient(app)
        response = client.get(plugin.config.ui_endpoint)

        assert response.status_code == 200
        assert "feedbackEnabled: true" in response.text
        not_consulted.can_submit.assert_not_called()

    @patch("boto3.Session")
    @patch("auto_bedrock_chat_fastapi.bedrock_client.boto3.Session")
    def test_flag_false_when_no_auth_and_anonymous_disallowed(self, mock_bc, mock_b):
        """No auth mechanism + anonymous disallowed → UI is suppressed.

        Submits could never succeed in this configuration, so rendering
        the controls would be misleading.
        """
        mock_bc.return_value = _mock_boto3()
        mock_b.return_value = _mock_boto3()
        app, plugin = _build_plugin_with_ui()
        plugin.config.feedback_enabled = True
        plugin.config.sso_enabled = False
        plugin.config.auth_verification_endpoint = None
        plugin.config.feedback_allow_anonymous = False
        plugin._feedback_store = Mock()

        client = TestClient(app)
        response = client.get(plugin.config.ui_endpoint)

        assert response.status_code == 200
        assert "feedbackEnabled: false" in response.text

    @patch("boto3.Session")
    @patch("auto_bedrock_chat_fastapi.bedrock_client.boto3.Session")
    def test_flag_true_when_no_auth_but_anonymous_allowed(self, mock_bc, mock_b):
        """No auth mechanism but anonymous explicitly allowed → UI rendered."""
        mock_bc.return_value = _mock_boto3()
        mock_b.return_value = _mock_boto3()
        app, plugin = _build_plugin_with_ui()
        plugin.config.feedback_enabled = True
        plugin.config.sso_enabled = False
        plugin.config.auth_verification_endpoint = None
        plugin.config.feedback_allow_anonymous = True
        plugin._feedback_store = Mock()

        client = TestClient(app)
        response = client.get(plugin.config.ui_endpoint)

        assert response.status_code == 200
        assert "feedbackEnabled: true" in response.text


class TestFeedbackEnabledAllowlistSSO:
    """T3: UI gate correctly applies allowlist using canonical user_id (not display name).

    Regression guard for the identity mismatch bug: sso_user_display was
    previously passed to can_submit instead of the canonical sso_user_id.
    When the allowlist is configured with a ``sub`` value and the user session
    has no email, the display string would differ from the WS user_id.
    """

    def _make_sso_session(self, sso_session_store, session_secret, *, user_info, claims):
        """Store a fake SSO session and return a signed session token."""
        session_id = sso_session_store.create_session(
            tokens={"access_token": "fake-access-token"},
            user_info=user_info,
            id_token_claims=claims,
        )
        token = sso_session_store.generate_session_token(session_id, session_secret)
        return token

    @patch("boto3.Session")
    @patch("auto_bedrock_chat_fastapi.bedrock_client.boto3.Session")
    def test_flag_true_for_sub_allowlisted_user_without_email(self, mock_bc, mock_b):
        """feedbackEnabled=true when allowlist contains only a sub value and user has no email."""
        mock_bc.return_value = _mock_boto3()
        mock_b.return_value = _mock_boto3()
        app, plugin = _build_plugin_with_ui()
        plugin.config.feedback_enabled = True
        plugin.config.sso_enabled = True
        plugin.config.sso_session_secret = "test-secret-32-chars-padding-here"
        plugin.config.feedback_authorized_users = ["a1b2c3d4-sub-value"]
        plugin._feedback_store = Mock()
        plugin._feedback_authorizer = AllowlistFeedbackAuthorizer(authorized_users=["a1b2c3d4-sub-value"])
        # sso_session_store is None until SSO is enabled at init; create one directly.
        store = SSOSessionStore()
        plugin.sso_session_store = store

        # Session has sub but NO email — display would be empty/username,
        # but canonical user_id resolves to sub.
        token = self._make_sso_session(
            store,
            plugin.config.sso_session_secret,
            user_info={"sub": "a1b2c3d4-sub-value"},
            claims={"sub": "a1b2c3d4-sub-value"},
        )

        client = TestClient(app, cookies={"sso_session_token": token})
        response = client.get(plugin.config.ui_endpoint)

        assert response.status_code == 200
        assert "feedbackEnabled: true" in response.text

    @patch("boto3.Session")
    @patch("auto_bedrock_chat_fastapi.bedrock_client.boto3.Session")
    def test_flag_false_for_sub_user_not_in_allowlist(self, mock_bc, mock_b):
        """feedbackEnabled=false when authenticated sub is not in the allowlist."""
        mock_bc.return_value = _mock_boto3()
        mock_b.return_value = _mock_boto3()
        app, plugin = _build_plugin_with_ui()
        plugin.config.feedback_enabled = True
        plugin.config.sso_enabled = True
        plugin.config.sso_session_secret = "test-secret-32-chars-padding-here"
        plugin.config.feedback_authorized_users = ["other-sub-value"]
        plugin._feedback_store = Mock()
        plugin._feedback_authorizer = AllowlistFeedbackAuthorizer(authorized_users=["other-sub-value"])
        store = SSOSessionStore()
        plugin.sso_session_store = store

        token = self._make_sso_session(
            store,
            plugin.config.sso_session_secret,
            user_info={"sub": "a1b2c3d4-sub-value"},
            claims={"sub": "a1b2c3d4-sub-value"},
        )

        client = TestClient(app, cookies={"sso_session_token": token})
        response = client.get(plugin.config.ui_endpoint)

        assert response.status_code == 200
        assert "feedbackEnabled: false" in response.text
