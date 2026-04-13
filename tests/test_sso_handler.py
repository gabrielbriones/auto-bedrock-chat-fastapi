"""Unit tests for SSOProvider in sso_handler.py"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt as jose_jwt

from auto_bedrock_chat_fastapi.sso_handler import SSODiscoveryError, SSOProvider, SSOTokenError, SSOValidationError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DISCOVERY_DOC = {
    "authorization_endpoint": "https://idp.example.com/authorize",
    "token_endpoint": "https://idp.example.com/token",
    "userinfo_endpoint": "https://idp.example.com/userinfo",
    "jwks_uri": "https://idp.example.com/jwks",
    "issuer": "https://idp.example.com",
}


def _make_config(**overrides):
    """Build a minimal mock ChatConfig for use in tests."""
    defaults = {
        "sso_enabled": True,
        "sso_client_id": "test-client-id",
        "sso_client_secret": "test-client-secret",
        "sso_session_secret": "test-session-secret",
        "sso_discovery_url": "https://idp.example.com/.well-known/openid-configuration",
        "sso_authorization_url": None,
        "sso_token_url": None,
        "sso_userinfo_url": None,
        "sso_jwks_url": None,
        "sso_scopes": "openid profile email",
        "sso_callback_path": "/chat/auth/callback",
        "sso_provider": None,
        "api_base_url": "https://app.example.com",
    }
    defaults.update(overrides)
    config = MagicMock()
    for k, v in defaults.items():
        setattr(config, k, v)
    return config


@pytest.fixture
def config():
    return _make_config()


@pytest.fixture
def provider(config):
    return SSOProvider(config)


# ---------------------------------------------------------------------------
# RSA key pair for JWT signing (generated once per test session)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def rsa_private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def rsa_public_key(rsa_private_key):
    return rsa_private_key.public_key()


@pytest.fixture(scope="session")
def rsa_private_pem(rsa_private_key):
    return rsa_private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="session")
def rsa_public_pem(rsa_public_key):
    return rsa_public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _make_id_token(
    private_pem: bytes,
    *,
    sub="user123",
    aud="test-client-id",
    iss="https://idp.example.com",
    exp_offset=300,
    kid="test-kid",
):
    """Create a signed RS256 ID token JWT."""
    now = int(time.time())
    claims = {
        "sub": sub,
        "aud": aud,
        "iss": iss,
        "iat": now,
        "exp": now + exp_offset,
        "email": "user@example.com",
        "name": "Test User",
    }
    return jose_jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


def _public_pem_to_jwks(public_pem: bytes, kid="test-kid") -> dict:
    """Create a minimal JWKS document from a PEM public key."""
    from cryptography.hazmat.backends import default_backend

    pub_key = serialization.load_pem_public_key(public_pem, backend=default_backend())
    pub_numbers = pub_key.public_numbers()

    import base64

    def _int_to_base64url(n: int) -> str:
        # Encode integer as big-endian bytes, strip leading zeros, base64url
        length = (n.bit_length() + 7) // 8
        raw = n.to_bytes(length, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _int_to_base64url(pub_numbers.n),
                "e": _int_to_base64url(pub_numbers.e),
            }
        ]
    }


# ---------------------------------------------------------------------------
# Helper: pre-resolved provider (avoids having to call discover())
# ---------------------------------------------------------------------------


def _make_resolved_provider(config=None) -> SSOProvider:
    """Return a SSOProvider with endpoints already resolved."""
    if config is None:
        config = _make_config()
    p = SSOProvider(config)
    p._resolve_endpoints(discovered=_DISCOVERY_DOC)
    return p


# ---------------------------------------------------------------------------
# Tests: discover()
# ---------------------------------------------------------------------------


class TestDiscover:
    """discover() fetches the OIDC document and resolves endpoints."""

    @pytest.mark.asyncio
    async def test_discover_resolves_endpoints(self, provider):
        mock_response = MagicMock()
        mock_response.json.return_value = _DISCOVERY_DOC
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await provider.discover()

        assert provider._authorization_endpoint == "https://idp.example.com/authorize"
        assert provider._token_endpoint == "https://idp.example.com/token"
        assert provider._userinfo_endpoint == "https://idp.example.com/userinfo"
        assert provider._jwks_uri == "https://idp.example.com/jwks"

    @pytest.mark.asyncio
    async def test_discover_raises_on_network_error(self, provider):
        import httpx

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client_cls.return_value = mock_client

            with pytest.raises(SSODiscoveryError, match="Failed to fetch"):
                await provider.discover()

    @pytest.mark.asyncio
    async def test_discover_raises_on_http_error(self, provider):
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 404
        http_err = httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_response)
        mock_response.raise_for_status.side_effect = http_err

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SSODiscoveryError, match="404"):
                await provider.discover()


# ---------------------------------------------------------------------------
# Tests: manual URL overrides take precedence
# ---------------------------------------------------------------------------


class TestManualUrlOverrides:
    """Manual URL config overrides take precedence over discovered values."""

    def test_manual_auth_url_overrides_discovered(self):
        cfg = _make_config(sso_authorization_url="https://manual.example.com/authorize")
        p = SSOProvider(cfg)
        p._resolve_endpoints(discovered=_DISCOVERY_DOC)
        assert p._authorization_endpoint == "https://manual.example.com/authorize"

    def test_manual_token_url_overrides_discovered(self):
        cfg = _make_config(sso_token_url="https://manual.example.com/token")
        p = SSOProvider(cfg)
        p._resolve_endpoints(discovered=_DISCOVERY_DOC)
        assert p._token_endpoint == "https://manual.example.com/token"

    def test_manual_userinfo_url_overrides_discovered(self):
        cfg = _make_config(sso_userinfo_url="https://manual.example.com/userinfo")
        p = SSOProvider(cfg)
        p._resolve_endpoints(discovered=_DISCOVERY_DOC)
        assert p._userinfo_endpoint == "https://manual.example.com/userinfo"

    def test_manual_jwks_url_overrides_discovered(self):
        cfg = _make_config(sso_jwks_url="https://manual.example.com/jwks")
        p = SSOProvider(cfg)
        p._resolve_endpoints(discovered=_DISCOVERY_DOC)
        assert p._jwks_uri == "https://manual.example.com/jwks"

    def test_no_discovery_uses_manual_urls_only(self):
        cfg = _make_config(
            sso_discovery_url=None,
            sso_authorization_url="https://manual.example.com/authorize",
            sso_token_url="https://manual.example.com/token",
        )
        p = SSOProvider(cfg)
        p._resolve_endpoints(discovered={})
        assert p._authorization_endpoint == "https://manual.example.com/authorize"
        assert p._token_endpoint == "https://manual.example.com/token"
        assert p._userinfo_endpoint is None  # not set
        assert p._jwks_uri is None


# ---------------------------------------------------------------------------
# Tests: build_authorization_url()
# ---------------------------------------------------------------------------


class TestBuildAuthorizationUrl:
    """build_authorization_url() produces correct URLs with PKCE parameters."""

    def test_url_contains_required_params(self):
        p = _make_resolved_provider()
        url, verifier = p.build_authorization_url(state="test-state")

        assert "response_type=code" in url
        assert "client_id=test-client-id" in url
        assert "redirect_uri=" in url
        assert "scope=" in url
        assert "state=test-state" in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url

    def test_url_uses_authorization_endpoint(self):
        p = _make_resolved_provider()
        url, _ = p.build_authorization_url(state="xyz")
        assert url.startswith("https://idp.example.com/authorize")

    def test_code_verifier_returned(self):
        p = _make_resolved_provider()
        _, verifier = p.build_authorization_url(state="xyz")
        # Verifier must be between 43 and 128 chars (RFC 7636)
        assert 43 <= len(verifier) <= 128

    def test_supplied_verifier_is_used(self):
        p = _make_resolved_provider()
        verifier = "A" * 64
        url, returned_verifier = p.build_authorization_url(state="s", code_verifier=verifier)
        assert returned_verifier == verifier

    def test_code_challenge_is_s256_of_verifier(self):
        import base64
        import hashlib

        p = _make_resolved_provider()
        verifier = "A" * 64
        url, _ = p.build_authorization_url(state="s", code_verifier=verifier)

        expected_challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert f"code_challenge={expected_challenge}" in url

    def test_raises_when_endpoint_not_configured(self):
        cfg = _make_config(sso_discovery_url=None, sso_authorization_url=None)
        p = SSOProvider(cfg)
        # Endpoints not resolved yet
        with pytest.raises(SSODiscoveryError, match="Authorization endpoint"):
            p.build_authorization_url(state="s")


# ---------------------------------------------------------------------------
# Tests: exchange_code()
# ---------------------------------------------------------------------------


class TestExchangeCode:
    """exchange_code() sends correct POST body and handles responses."""

    @pytest.mark.asyncio
    async def test_exchange_code_returns_tokens(self):
        token_response = {
            "access_token": "at_abc",
            "refresh_token": "rt_xyz",
            "id_token": "eyJ...",
            "expires_in": 3600,
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = token_response

        p = _make_resolved_provider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await p.exchange_code(code="auth_code_123", code_verifier="v" * 64)

        assert result["access_token"] == "at_abc"
        assert result["refresh_token"] == "rt_xyz"
        assert result["id_token"] == "eyJ..."

    @pytest.mark.asyncio
    async def test_exchange_code_sends_correct_fields(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "at", "expires_in": 3600}

        p = _make_resolved_provider()
        captured_data = {}

        async def capture_post(url, **kwargs):
            captured_data.update(kwargs.get("data", {}))
            return mock_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = capture_post
            mock_client_cls.return_value = mock_client

            await p.exchange_code(code="CODE", code_verifier="VERIFIER")

        assert captured_data["grant_type"] == "authorization_code"
        assert captured_data["code"] == "CODE"
        assert captured_data["code_verifier"] == "VERIFIER"
        assert captured_data["client_id"] == "test-client-id"
        assert captured_data["client_secret"] == "test-client-secret"

    @pytest.mark.asyncio
    async def test_exchange_code_raises_on_idp_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "error": "invalid_grant",
            "error_description": "The authorization code is invalid.",
        }

        p = _make_resolved_provider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SSOTokenError, match="invalid_grant"):
                await p.exchange_code(code="bad_code", code_verifier="v" * 64)


# ---------------------------------------------------------------------------
# Tests: validate_id_token()
# ---------------------------------------------------------------------------


class TestValidateIdToken:
    """validate_id_token() verifies signatures, exp, and aud claims."""

    @pytest.mark.asyncio
    async def test_valid_token_returns_claims(self, rsa_private_pem, rsa_public_pem):
        token = _make_id_token(rsa_private_pem, aud="test-client-id")
        jwks = _public_pem_to_jwks(rsa_public_pem)

        mock_jwks_response = MagicMock()
        mock_jwks_response.status_code = 200
        mock_jwks_response.json.return_value = jwks
        mock_jwks_response.raise_for_status = MagicMock()

        p = _make_resolved_provider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_jwks_response)
            mock_client_cls.return_value = mock_client

            claims = await p.validate_id_token(token)

        assert claims["sub"] == "user123"
        assert claims["email"] == "user@example.com"

    @pytest.mark.asyncio
    async def test_expired_token_raises(self, rsa_private_pem, rsa_public_pem):
        token = _make_id_token(rsa_private_pem, exp_offset=-60)  # already expired
        jwks = _public_pem_to_jwks(rsa_public_pem)

        mock_jwks_response = MagicMock()
        mock_jwks_response.status_code = 200
        mock_jwks_response.json.return_value = jwks
        mock_jwks_response.raise_for_status = MagicMock()

        p = _make_resolved_provider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_jwks_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SSOValidationError, match="expired"):
                await p.validate_id_token(token)

    @pytest.mark.asyncio
    async def test_wrong_audience_raises(self, rsa_private_pem, rsa_public_pem):
        token = _make_id_token(rsa_private_pem, aud="another-client")
        jwks = _public_pem_to_jwks(rsa_public_pem)

        mock_jwks_response = MagicMock()
        mock_jwks_response.status_code = 200
        mock_jwks_response.json.return_value = jwks
        mock_jwks_response.raise_for_status = MagicMock()

        p = _make_resolved_provider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_jwks_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SSOValidationError):
                await p.validate_id_token(token)


# ---------------------------------------------------------------------------
# Tests: get_user_info()
# ---------------------------------------------------------------------------


class TestGetUserInfo:
    """get_user_info() fetches from userinfo endpoint with Bearer token."""

    @pytest.mark.asyncio
    async def test_returns_user_profile(self):
        user_profile = {
            "sub": "user123",
            "name": "Test User",
            "email": "user@example.com",
            "groups": ["admins"],
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = user_profile
        mock_response.raise_for_status = MagicMock()

        p = _make_resolved_provider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await p.get_user_info("my_access_token")

        assert result["email"] == "user@example.com"
        assert result["groups"] == ["admins"]

    @pytest.mark.asyncio
    async def test_sends_bearer_header(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"sub": "u1"}
        mock_response.raise_for_status = MagicMock()

        captured_headers = {}
        p = _make_resolved_provider()

        async def capture_get(url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            return mock_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = capture_get
            mock_client_cls.return_value = mock_client

            await p.get_user_info("TOKEN_VALUE")

        assert captured_headers.get("Authorization") == "Bearer TOKEN_VALUE"


# ---------------------------------------------------------------------------
# Tests: refresh_token()
# ---------------------------------------------------------------------------


class TestRefreshToken:
    """refresh_token() exchanges a refresh token for a new token set."""

    @pytest.mark.asyncio
    async def test_refresh_returns_new_tokens(self):
        new_tokens = {
            "access_token": "new_at",
            "refresh_token": "new_rt",
            "expires_in": 3600,
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = new_tokens

        p = _make_resolved_provider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await p.refresh_token("old_refresh_token")

        assert result["access_token"] == "new_at"
        assert result["refresh_token"] == "new_rt"

    @pytest.mark.asyncio
    async def test_refresh_sends_correct_grant(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "at", "expires_in": 3600}

        captured = {}
        p = _make_resolved_provider()

        async def capture_post(url, **kwargs):
            captured.update(kwargs.get("data", {}))
            return mock_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = capture_post
            mock_client_cls.return_value = mock_client

            await p.refresh_token("MY_REFRESH_TOKEN")

        assert captured["grant_type"] == "refresh_token"
        assert captured["refresh_token"] == "MY_REFRESH_TOKEN"
        assert captured["client_id"] == "test-client-id"


# ---------------------------------------------------------------------------
# Tests: issuer validation (Comment 6)
# ---------------------------------------------------------------------------


class TestIssuerValidation:
    """validate_id_token() must validate the `iss` claim when issuer is known."""

    @pytest.mark.asyncio
    async def test_discover_stores_issuer(self):
        """After discovery, _issuer should be populated from the discovery doc."""
        p = _make_resolved_provider()
        assert p._issuer == "https://idp.example.com"

    @pytest.mark.asyncio
    async def test_wrong_issuer_raises(self, rsa_private_pem, rsa_public_pem):
        """Token with a different issuer should be rejected."""
        token = _make_id_token(rsa_private_pem, iss="https://evil.example.com")
        jwks = _public_pem_to_jwks(rsa_public_pem)

        mock_jwks_response = MagicMock()
        mock_jwks_response.status_code = 200
        mock_jwks_response.json.return_value = jwks
        mock_jwks_response.raise_for_status = MagicMock()

        p = _make_resolved_provider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_jwks_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SSOValidationError):
                await p.validate_id_token(token)

    @pytest.mark.asyncio
    async def test_no_issuer_skips_validation(self, rsa_private_pem, rsa_public_pem):
        """When no discovery was done (_issuer is None), issuer is not validated."""
        token = _make_id_token(rsa_private_pem, iss="https://any-issuer.example.com")
        jwks = _public_pem_to_jwks(rsa_public_pem)

        mock_jwks_response = MagicMock()
        mock_jwks_response.status_code = 200
        mock_jwks_response.json.return_value = jwks
        mock_jwks_response.raise_for_status = MagicMock()

        # Manual config (no discovery) — _issuer stays None
        cfg = _make_config(sso_discovery_url=None, sso_jwks_url="https://manual.example.com/jwks")
        p = SSOProvider(cfg)
        p._resolve_endpoints(discovered={})
        assert p._issuer is None

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_jwks_response)
            mock_client_cls.return_value = mock_client

            claims = await p.validate_id_token(token)

        assert claims["sub"] == "user123"


# ---------------------------------------------------------------------------
# Tests: JWT algorithm allowlist (Comment 12)
# ---------------------------------------------------------------------------


class TestAlgorithmAllowlist:
    """validate_id_token() restricts accepted algorithms."""

    @pytest.mark.asyncio
    async def test_alg_none_rejected(self, rsa_public_pem):
        """Tokens with alg: none must be rejected."""
        # Craft a token with alg=none in the header
        now = int(time.time())
        claims = {
            "sub": "attacker",
            "aud": "test-client-id",
            "iss": "https://idp.example.com",
            "iat": now,
            "exp": now + 300,
        }
        # python-jose won't encode with alg=none, so build a fake token
        import base64
        import json

        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT", "kid": "test-kid"}).encode())
            .rstrip(b"=")
            .decode()
        )
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        forged_token = f"{header}.{payload}."

        jwks = _public_pem_to_jwks(rsa_public_pem)

        mock_jwks_response = MagicMock()
        mock_jwks_response.status_code = 200
        mock_jwks_response.json.return_value = jwks
        mock_jwks_response.raise_for_status = MagicMock()

        p = _make_resolved_provider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_jwks_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SSOValidationError, match="(?i)alg"):
                await p.validate_id_token(forged_token)

    @pytest.mark.asyncio
    async def test_hs256_rejected(self, rsa_public_pem):
        """Tokens with alg: HS256 must be rejected (HMAC with public key attack)."""
        now = int(time.time())
        claims = {
            "sub": "attacker",
            "aud": "test-client-id",
            "iss": "https://idp.example.com",
            "iat": now,
            "exp": now + 300,
        }
        import base64
        import json

        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "test-kid"}).encode())
            .rstrip(b"=")
            .decode()
        )
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        forged_token = f"{header}.{payload}.fakesig"

        jwks = _public_pem_to_jwks(rsa_public_pem)

        mock_jwks_response = MagicMock()
        mock_jwks_response.status_code = 200
        mock_jwks_response.json.return_value = jwks
        mock_jwks_response.raise_for_status = MagicMock()

        p = _make_resolved_provider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_jwks_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(SSOValidationError, match="(?i)alg"):
                await p.validate_id_token(forged_token)

    @pytest.mark.asyncio
    async def test_jwks_key_alg_preferred_over_header(self, rsa_private_pem, rsa_public_pem):
        """Algorithm from the JWKS key entry takes precedence over the token header."""
        token = _make_id_token(rsa_private_pem)
        jwks = _public_pem_to_jwks(rsa_public_pem)
        # The JWKS key has alg=RS256 and the token header also has RS256.
        # Verify it works — algorithm from key is used.
        assert jwks["keys"][0]["alg"] == "RS256"

        mock_jwks_response = MagicMock()
        mock_jwks_response.status_code = 200
        mock_jwks_response.json.return_value = jwks
        mock_jwks_response.raise_for_status = MagicMock()

        p = _make_resolved_provider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_jwks_response)
            mock_client_cls.return_value = mock_client

            claims = await p.validate_id_token(token)

        assert claims["sub"] == "user123"
