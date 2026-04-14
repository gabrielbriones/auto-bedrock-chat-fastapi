"""SSO (OAuth2 Authorization Code + PKCE) provider service"""

import base64
import hashlib
import logging
import os
from typing import TYPE_CHECKING, Any, Dict, Optional
from urllib.parse import urlencode

import httpx
from jose import ExpiredSignatureError, JWTError, jwk, jwt

if TYPE_CHECKING:
    from .config import ChatConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class SSODiscoveryError(Exception):
    """Raised when OIDC discovery fails."""


class SSOTokenError(Exception):
    """Raised when token exchange or refresh fails."""


class SSOValidationError(Exception):
    """Raised when ID token validation fails."""


# ---------------------------------------------------------------------------
# SSOProvider
# ---------------------------------------------------------------------------


class SSOProvider:
    """Encapsulates OAuth2 Authorization Code + PKCE / OIDC discovery logic.

    One instance is created per application startup (gated behind
    ``config.sso_enabled``).  It is safe to share across requests because
    all mutable state is either cached (OIDC discovery doc) or passed as
    arguments.
    """

    def __init__(self, config: "ChatConfig") -> None:
        self._config = config
        # Cached OIDC discovery document (populated by discover())
        self._oidc_config: Optional[Dict[str, Any]] = None
        # Resolved endpoints (merged discovery + manual overrides)
        self._authorization_endpoint: Optional[str] = None
        self._token_endpoint: Optional[str] = None
        self._userinfo_endpoint: Optional[str] = None
        self._jwks_uri: Optional[str] = None
        self._issuer: Optional[str] = None

    @property
    def has_userinfo_endpoint(self) -> bool:
        """Whether a userinfo endpoint is configured."""
        return bool(self._userinfo_endpoint)

    # ------------------------------------------------------------------
    # OIDC Discovery
    # ------------------------------------------------------------------

    async def discover(self) -> None:
        """Fetch and cache the OIDC discovery document.

        Populates ``_oidc_config`` with the raw document and then resolves
        effective endpoints (discovery values merged with manual overrides).
        Short-circuits if endpoints have already been resolved.

        Raises:
            SSODiscoveryError: If the HTTP request fails or the response is
                not valid JSON containing the expected keys.
        """
        # Short-circuit if already resolved
        if self._authorization_endpoint:
            return

        if not self._config.sso_discovery_url:
            # No discovery URL; endpoints must come exclusively from manual config.
            self._resolve_endpoints(discovered={})
            return

        timeout = httpx.Timeout(10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.get(self._config.sso_discovery_url)
                response.raise_for_status()
                self._oidc_config = response.json()
            except httpx.HTTPStatusError as exc:
                raise SSODiscoveryError(
                    f"OIDC discovery endpoint returned HTTP {exc.response.status_code}: "
                    f"{self._config.sso_discovery_url}"
                ) from exc
            except (httpx.RequestError, ValueError) as exc:
                raise SSODiscoveryError(
                    f"Failed to fetch OIDC discovery document from " f"{self._config.sso_discovery_url}: {exc}"
                ) from exc

        self._resolve_endpoints(discovered=self._oidc_config or {})
        logger.debug("OIDC discovery successful from %s", self._config.sso_discovery_url)

    def _resolve_endpoints(self, discovered: Dict[str, Any]) -> None:
        """Merge discovered endpoints with manual config overrides.

        Manual overrides (``sso_authorization_url``, ``sso_token_url``, etc.)
        always take precedence over values from the discovery document.
        """
        self._authorization_endpoint = self._config.sso_authorization_url or discovered.get("authorization_endpoint")
        self._token_endpoint = self._config.sso_token_url or discovered.get("token_endpoint")
        self._userinfo_endpoint = self._config.sso_userinfo_url or discovered.get("userinfo_endpoint")
        self._jwks_uri = self._config.sso_jwks_url or discovered.get("jwks_uri")
        self._issuer = discovered.get("issuer")

        logger.debug(
            "Resolved SSO endpoints — auth: %s, token: %s, userinfo: %s, jwks: %s",
            self._authorization_endpoint,
            self._token_endpoint,
            self._userinfo_endpoint,
            self._jwks_uri,
        )

    # ------------------------------------------------------------------
    # PKCE helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_code_verifier() -> str:
        """Generate a cryptographically random PKCE code verifier (RFC 7636).

        Returns a URL-safe base64-encoded random string between 43 and 128
        characters (we use 64 bytes of randomness → 86-char output, always
        within range).
        """
        # 64 random bytes → 86 base64url characters (no padding)
        verifier = base64.urlsafe_b64encode(os.urandom(64)).rstrip(b"=").decode("ascii")
        return verifier

    @staticmethod
    def _generate_code_challenge(code_verifier: str) -> str:
        """Derive the S256 PKCE code challenge from *code_verifier*."""
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return challenge

    # ------------------------------------------------------------------
    # Authorization URL construction
    # ------------------------------------------------------------------

    def build_authorization_url(self, state: str, code_verifier: Optional[str] = None) -> tuple[str, str]:
        """Build the IdP authorization URL and return ``(url, code_verifier)``.

        A new code verifier is generated if one is not supplied (the normal
        case).  The caller must store the returned ``code_verifier`` alongside
        ``state`` so it can be sent in the token exchange later.

        Args:
            state: Opaque CSRF-protection state parameter.
            code_verifier: Optional pre-generated verifier (useful in tests).

        Returns:
            Tuple of (authorization_url, code_verifier).
        """
        if not self._authorization_endpoint:
            raise SSODiscoveryError(
                "Authorization endpoint is not configured. " "Call discover() first or set sso_authorization_url."
            )

        if code_verifier is None:
            code_verifier = self._generate_code_verifier()

        code_challenge = self._generate_code_challenge(code_verifier)
        redirect_uri = self._build_redirect_uri()

        params = {
            "response_type": "code",
            "client_id": self._config.sso_client_id,
            "redirect_uri": redirect_uri,
            "scope": self._config.sso_scopes,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        url = f"{self._authorization_endpoint}?{urlencode(params)}"
        return url, code_verifier

    # ------------------------------------------------------------------
    # Token exchange
    # ------------------------------------------------------------------

    async def exchange_code(self, code: str, code_verifier: str) -> Dict[str, Any]:
        """Exchange an authorization code for tokens.

        Args:
            code: The authorization code returned by the IdP.
            code_verifier: The PKCE verifier generated during the auth request.

        Returns:
            Dict with keys: ``access_token``, ``refresh_token``, ``id_token``,
            ``expires_in`` (and any extra fields the IdP returns).

        Raises:
            SSOTokenError: On HTTP errors or IdP-returned error responses.
        """
        if not self._token_endpoint:
            raise SSODiscoveryError("Token endpoint is not configured. " "Call discover() first or set sso_token_url.")

        redirect_uri = self._build_redirect_uri()
        data: Dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._config.sso_client_id,
            "code_verifier": code_verifier,
        }
        if self._config.sso_client_secret:
            data["client_secret"] = self._config.sso_client_secret

        tokens = await self._post_token_endpoint(data, "code exchange")
        return tokens

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Exchange a refresh token for a new token set.

        Args:
            refresh_token: The refresh token stored in the SSO session.

        Returns:
            Dict with the new token set (same structure as ``exchange_code``).

        Raises:
            SSOTokenError: On HTTP errors or IdP-returned error responses.
        """
        if not self._token_endpoint:
            raise SSODiscoveryError("Token endpoint is not configured. " "Call discover() first or set sso_token_url.")

        data: Dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._config.sso_client_id,
        }
        if self._config.sso_client_secret:
            data["client_secret"] = self._config.sso_client_secret

        tokens = await self._post_token_endpoint(data, "token refresh")
        return tokens

    async def _post_token_endpoint(self, data: Dict[str, str], operation: str) -> Dict[str, Any]:
        """POST form data to the token endpoint and return the parsed response.

        Raises:
            SSOTokenError: On HTTP or JSON parsing errors.
        """
        timeout = httpx.Timeout(10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(
                    self._token_endpoint,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.RequestError as exc:
                raise SSOTokenError(f"Network error during {operation}: {exc}") from exc

        if response.status_code >= 400:
            try:
                body = response.json()
                error = body.get("error", "unknown_error")
                description = body.get("error_description", "")
            except ValueError:
                error = "http_error"
                description = response.text
            raise SSOTokenError(
                f"IdP returned HTTP {response.status_code} during {operation}: " f"{error} — {description}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise SSOTokenError(f"Invalid JSON in token endpoint response during {operation}") from exc

    # ------------------------------------------------------------------
    # ID Token validation
    # ------------------------------------------------------------------

    # Allowlist of accepted JWT signing algorithms.  Symmetric algorithms
    # (HS*) are excluded to prevent the "HMAC with public key" attack where
    # an attacker sets ``alg: HS256`` and signs with the public RSA key.
    _ALLOWED_ALGORITHMS = frozenset(
        {
            "RS256",
            "RS384",
            "RS512",
            "ES256",
            "ES384",
            "ES512",
            "PS256",
            "PS384",
            "PS512",
        }
    )

    async def validate_id_token(self, id_token: str, access_token: Optional[str] = None) -> Dict[str, Any]:
        """Validate the ID token JWT signature and standard claims.

        Fetches the JWKS from the IdP, finds the matching key, verifies the
        signature, and validates ``aud``, ``iss``, ``exp``.

        Args:
            id_token: Raw JWT string from the token endpoint.
            access_token: The access token from the same token response.  When
                provided it is used to verify the ``at_hash`` claim that some
                IdPs (e.g. AWS Cognito) embed in the ID token.

        Returns:
            Decoded and verified claims dict.

        Raises:
            SSOValidationError: If the token is invalid, expired, or cannot
                be validated with the available keys.
        """
        if not self._jwks_uri:
            raise SSOValidationError("JWKS URI is not configured. " "Call discover() first or set sso_jwks_url.")

        # Fetch JWKS
        jwks_data = await self._fetch_jwks()

        # Decode header to find the key ID
        try:
            unverified_header = jwt.get_unverified_header(id_token)
        except JWTError as exc:
            raise SSOValidationError(f"Cannot read ID token header: {exc}") from exc

        kid = unverified_header.get("kid")
        alg = unverified_header.get("alg", "RS256")

        # Find the matching key in JWKS
        matching_key = self._find_jwks_key(jwks_data, kid)
        if matching_key is None:
            raise SSOValidationError(
                f"No matching key found in JWKS for kid={kid!r}. "
                f"Available kids: {[k.get('kid') for k in jwks_data.get('keys', [])]}"
            )

        # Prefer the algorithm declared in the JWKS key entry over the
        # unverified token header to prevent algorithm confusion attacks.
        alg = matching_key.get("alg") or alg

        # Validate algorithm against allowlist
        if alg not in self._ALLOWED_ALGORITHMS:
            raise SSOValidationError(
                f"Unsupported JWT algorithm {alg!r}. " f"Allowed: {sorted(self._ALLOWED_ALGORITHMS)}"
            )

        # Build the public key object
        try:
            public_key = jwk.construct(matching_key, algorithm=alg)
        except Exception as exc:
            raise SSOValidationError(f"Failed to construct public key from JWKS: {exc}") from exc

        # Verify and decode the JWT
        decode_kwargs: Dict[str, Any] = {
            "algorithms": [alg],
            "audience": self._config.sso_client_id,
        }
        if self._issuer:
            decode_kwargs["issuer"] = self._issuer
        if access_token:
            decode_kwargs["access_token"] = access_token
        try:
            claims = jwt.decode(id_token, public_key, **decode_kwargs)
        except ExpiredSignatureError as exc:
            raise SSOValidationError("ID token has expired.") from exc
        except JWTError as exc:
            raise SSOValidationError(f"ID token validation failed: {exc}") from exc

        return claims

    async def _fetch_jwks(self) -> Dict[str, Any]:
        """Fetch the JWKS document from the IdP.

        Raises:
            SSOValidationError: On HTTP/network errors.
        """
        timeout = httpx.Timeout(10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.get(self._jwks_uri)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                raise SSOValidationError(
                    f"JWKS endpoint returned HTTP {exc.response.status_code}: {self._jwks_uri}"
                ) from exc
            except (httpx.RequestError, ValueError) as exc:
                raise SSOValidationError(f"Failed to fetch JWKS from {self._jwks_uri}: {exc}") from exc

    @staticmethod
    def _find_jwks_key(jwks: Dict[str, Any], kid: Optional[str]) -> Optional[Dict[str, Any]]:
        """Find a key in the JWKS document.

        If *kid* is provided, match by key ID.  If not, return the first key.
        """
        keys = jwks.get("keys", [])
        if not keys:
            return None
        if kid:
            for key in keys:
                if key.get("kid") == kid:
                    return key
            # Fall back to first key if kid not found (some IdPs omit kid)
            return keys[0]
        return keys[0]

    # ------------------------------------------------------------------
    # UserInfo
    # ------------------------------------------------------------------

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Fetch the user profile from the OIDC userinfo endpoint.

        Args:
            access_token: Bearer token from the token endpoint.

        Returns:
            User profile dict (typically contains ``sub``, ``name``,
            ``email``, ``groups`` — exact fields depend on IdP / scopes).

        Raises:
            SSODiscoveryError: If the userinfo endpoint is not configured.
            SSOTokenError: On HTTP errors.
        """
        if not self._userinfo_endpoint:
            raise SSODiscoveryError(
                "Userinfo endpoint is not configured. " "Call discover() first or set sso_userinfo_url."
            )

        timeout = httpx.Timeout(10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.get(
                    self._userinfo_endpoint,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                raise SSOTokenError(f"Userinfo endpoint returned HTTP {exc.response.status_code}") from exc
            except (httpx.RequestError, ValueError) as exc:
                raise SSOTokenError(f"Failed to fetch user info: {exc}") from exc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_redirect_uri(self) -> str:
        """Construct the full callback redirect URI from config.

        Uses sso_public_base_url when configured — this is the browser-visible URL
        sent to the IdP and must be registered as a valid callback there.  Falls back
        to api_base_url (which is typically localhost for same-process plugins and is
        suitable when tool-call and public URLs are identical).
        """
        base = (self._config.sso_public_base_url or self._config.api_base_url or "").rstrip("/")
        path = self._config.sso_callback_path.lstrip("/")
        if base:
            return f"{base}/{path}"
        return f"/{path}"

    # ------------------------------------------------------------------
    # Provider-specific quirk documentation
    # ------------------------------------------------------------------
    # The methods below are no-ops; they exist to document provider quirks
    # and serve as extension points.  Callers can check config.sso_provider
    # and apply adjustments as needed.

    def get_provider_notes(self) -> str:
        """Return human-readable notes about the configured provider."""
        provider = (self._config.sso_provider or "").lower()
        notes = {
            "azure_ad": (
                "Azure AD: Use a tenant-specific discovery URL such as "
                "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration. "
                "The 'groups' claim requires the 'GroupMember.Read.All' permission."
            ),
            "okta": (
                "Okta: If you use a custom authorization server, include its ID in the discovery URL: "
                "https://{domain}/oauth2/{authServerId}/.well-known/openid-configuration. "
                "Group membership is available via the 'groups' scope."
            ),
            "keycloak": (
                "Keycloak: Use the realm-specific discovery URL: "
                "https://{host}/auth/realms/{realm}/.well-known/openid-configuration. "
                "Group membership is available via the 'groups' client scope."
            ),
            "auth0": (
                "Auth0: Discovery URL is https://{domain}/.well-known/openid-configuration. "
                "Use the 'openid profile email' scopes."
            ),
            "cognito": (
                "AWS Cognito: Discovery URL is "
                "https://cognito-idp.{region}.amazonaws.com/{userPoolId}/.well-known/openid-configuration. "
                "Use scopes allowed by the app client (e.g. 'openid email profile phone aws.cognito.signin.user.admin')."
            ),
        }
        return notes.get(provider, "No provider-specific notes available.")
