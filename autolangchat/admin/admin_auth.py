"""Admin authorization for the Expert Review Admin API.

This module is intentionally narrow: it answers the question "is this caller
allowed to use the ``/admin/*`` endpoints?" and **nothing else**.

The design rejects a static env-var allowlist of admin identifiers because
that approach is not runtime-mutable: rotating an admin requires a redeploy
and host operators have no way to integrate with their existing identity
systems. Instead we expose an :class:`AdminAuthorizer` protocol and ship
three built-in implementations:

* :class:`RemoteAdminAuthorizer` (recommended default) mirrors the existing
  ``auth_verification_endpoint`` pattern: it POSTs the caller's identity to
  a host-app-owned endpoint and reads ``{"is_admin": bool}`` back. Every
  admin request calls the endpoint fresh so revocations propagate
  immediately \u2014 admin traffic is human-paced and the load is negligible.
* :class:`SSOGroupAdminAuthorizer` checks whether the user's SSO group
  claim includes any of a configured set of "admin" groups. Zero-
  infrastructure path when the IdP already returns groups.
* :class:`DenyAllAdminAuthorizer` is the safe-by-default fallback when
  nothing is configured. Misconfiguration manifests as 403, not as an
  open admin surface.

The future Access Control task is expected to swap in a richer
implementation by constructor injection \u2014 the protocol is the seam.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Identity carrier
# ---------------------------------------------------------------------------


class AdminIdentity:
    """Minimal carrier for admin authorization decisions.

    Built from the SSO session and forwarded to authorizers / audit logs.
    Intentionally a plain attribute container (not a Pydantic model) so it
    can be constructed cheaply on the request hot path without pulling in
    validation overhead.
    """

    __slots__ = ("user_id", "email", "groups", "claims")

    def __init__(
        self,
        user_id: str,
        email: Optional[str] = None,
        groups: Optional[List[str]] = None,
        claims: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.user_id = user_id
        self.email = email
        self.groups = list(groups or [])
        self.claims = dict(claims or {})

    def to_dict(self) -> Dict[str, Any]:
        """Serializable view used by :class:`RemoteAdminAuthorizer`."""
        return {
            "user_id": self.user_id,
            "email": self.email,
            "groups": self.groups,
            "claims": self.claims,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"AdminIdentity(user_id={self.user_id!r}, email={self.email!r}, groups={self.groups!r})"


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class AdminAuthorizer(Protocol):
    """Decide whether ``identity`` may access the admin endpoints.

    Async to accommodate I/O-bound implementations (HTTP, DB). Sync
    implementations simply ``return True``/``False`` from an async method.
    """

    async def is_admin(self, identity: AdminIdentity) -> bool: ...


# ---------------------------------------------------------------------------
# Built-in implementations
# ---------------------------------------------------------------------------


class DenyAllAdminAuthorizer:
    """Default authorizer when nothing is configured.

    Returns ``False`` unconditionally. Ensures admin routes are safe-by-
    default; if the plugin is misconfigured the endpoints simply reject
    every caller with 403 rather than silently opening up.
    """

    async def is_admin(self, identity: AdminIdentity) -> bool:  # noqa: D401
        return False


class SSOGroupAdminAuthorizer:
    """Grant admin if the user's SSO group claim overlaps a required set.

    Zero infrastructure when the IdP already returns groups (Okta /
    Azure AD / Cognito all support this). ``required_groups`` is matched
    case-sensitively against ``identity.groups``.
    """

    def __init__(self, required_groups: List[str]) -> None:
        # Filter out empties; preserve order is irrelevant since we
        # compare via set intersection.
        self.required_groups = {g for g in required_groups if g}

    async def is_admin(self, identity: AdminIdentity) -> bool:
        if not self.required_groups:
            return False
        return bool(self.required_groups.intersection(identity.groups))


class RemoteAdminAuthorizer:
    """POST identity to a host-owned endpoint and read ``{"is_admin": bool}``.

    Mirrors :func:`AuthenticationHandler.verify_credentials_remote` so host
    apps that already operate an auth-verification endpoint can extend it
    with an admin path with minimal effort. The endpoint is expected to
    return a 2xx response with a JSON body containing an ``is_admin``
    boolean; anything else is treated as "not admin".

    No caching: every admin request triggers a fresh call to the
    verification endpoint. Admin traffic is human-paced (a person
    clicking through a dashboard), so the load on the host endpoint is
    negligible and the gain \u2014 instant revocation propagation \u2014 is worth
    far more than a few cached ms.
    """

    def __init__(
        self,
        endpoint_url: str,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        request_timeout: float = 10.0,
    ) -> None:
        if not endpoint_url:
            raise ValueError("endpoint_url is required")
        self.endpoint_url = endpoint_url
        self._http_client = http_client
        self.request_timeout = request_timeout

    async def is_admin(self, identity: AdminIdentity) -> bool:
        return await self._call_endpoint(identity)

    async def _call_endpoint(self, identity: AdminIdentity) -> bool:
        client = self._http_client
        owns_client = client is None
        try:
            if client is None:
                client = httpx.AsyncClient(timeout=self.request_timeout)
            response = await client.post(
                self.endpoint_url,
                json=identity.to_dict(),
                timeout=self.request_timeout,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "admin verification endpoint call failed user_id=%s error=%s",
                identity.user_id,
                exc,
            )
            return False
        finally:
            if owns_client and client is not None:
                await client.aclose()

        if not (200 <= response.status_code < 300):
            logger.info(
                "admin verification endpoint returned non-2xx user_id=%s status=%s",
                identity.user_id,
                response.status_code,
            )
            return False
        try:
            body = response.json()
        except ValueError:
            logger.warning(
                "admin verification endpoint returned non-JSON body user_id=%s",
                identity.user_id,
            )
            return False
        if not isinstance(body, dict):
            return False
        return bool(body.get("is_admin"))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_admin_authorizer(
    config: Any,
    *,
    http_client: Optional[httpx.AsyncClient] = None,
    app_base_url: Optional[str] = None,
) -> AdminAuthorizer:
    """Pick a built-in authorizer based on ``config``.

    Resolution order (highest first):

    1. ``admin_verification_endpoint`` set \u2192 :class:`RemoteAdminAuthorizer`.
    2. ``admin_required_groups`` non-empty \u2192 :class:`SSOGroupAdminAuthorizer`.
    3. Fallback \u2192 :class:`DenyAllAdminAuthorizer`.

    The plugin construction path may bypass this by supplying its own
    ``admin_authorizer`` (e.g. a test stub or the future Access Control
    implementation).
    """
    endpoint = getattr(config, "admin_verification_endpoint", None)
    if endpoint:
        # Resolve relative paths against app base URL, mirroring how
        # auth_verification_endpoint is consumed in the WS handler.
        if endpoint.startswith("/") and app_base_url:
            endpoint = f"{app_base_url}{endpoint}"
        logger.info("admin authorizer: RemoteAdminAuthorizer endpoint=%s", endpoint)
        return RemoteAdminAuthorizer(
            endpoint_url=endpoint,
            http_client=http_client,
        )
    required_groups = list(getattr(config, "admin_required_groups", []) or [])
    if required_groups:
        logger.info("admin authorizer: SSOGroupAdminAuthorizer groups=%s", required_groups)
        return SSOGroupAdminAuthorizer(required_groups=required_groups)
    logger.info("admin authorizer: DenyAllAdminAuthorizer (no admin configuration)")
    return DenyAllAdminAuthorizer()


# ---------------------------------------------------------------------------
# Identity resolution from the SSO cookie
# ---------------------------------------------------------------------------


# Standard claim names where an IdP may carry groups. Most providers
# follow OIDC conventions but a few use bespoke names (e.g. Cognito's
# ``cognito:groups``). The list is intentionally short \u2014 host apps
# whose IdP doesn't fit can swap in a custom authorizer.
_GROUP_CLAIM_KEYS = ("groups", "cognito:groups", "roles")


def _extract_groups(*sources: Dict[str, Any]) -> List[str]:
    for source in sources:
        if not source:
            continue
        for key in _GROUP_CLAIM_KEYS:
            value = source.get(key)
            if value is None:
                continue
            if isinstance(value, list):
                return [str(g) for g in value if g]
            if isinstance(value, str):
                # Some IdPs emit a single string; keep it as a one-element list.
                return [value]
    return []


def resolve_admin_identity_from_sso_session(sso_session: Dict[str, Any]) -> Optional[AdminIdentity]:
    """Build an :class:`AdminIdentity` from an SSO session payload.

    Returns ``None`` when the session lacks a usable user identifier \u2014
    the caller should treat that as 401 (not authenticated).
    """
    if not sso_session:
        return None

    from ..sso.sso_session_store import extract_user_id_from_sso_session

    user_info = sso_session.get("user_info") or {}
    id_token_claims = sso_session.get("id_token_claims") or {}

    user_id = extract_user_id_from_sso_session(user_info, id_token_claims)
    if not user_id:
        return None

    email = user_info.get("email") or id_token_claims.get("email")
    groups = _extract_groups(user_info, id_token_claims)

    # Merge claims for forwarding to remote verification endpoints. We
    # avoid leaking the access token \u2014 callers only need identity
    # claims to make an authorization decision.
    claims: Dict[str, Any] = {}
    claims.update(id_token_claims)
    # user_info values take precedence (they reflect the userinfo endpoint).
    claims.update({k: v for k, v in user_info.items() if k not in ("access_token", "refresh_token", "id_token")})

    return AdminIdentity(user_id=str(user_id), email=email, groups=groups, claims=claims)


# ---------------------------------------------------------------------------
# Identity resolution from tool-auth verification endpoint
# ---------------------------------------------------------------------------


# Auth-related request headers forwarded to the verification endpoint when
# the caller does not present an SSO cookie. Covers the schemes that
# ``AuthenticationHandler.apply_auth_to_headers`` emits on the WebSocket
# auth path (bearer_token / basic_auth / oauth2 / sso land in
# ``Authorization``; api_key defaults to ``X-API-Key``). Host apps that
# rely on a non-standard api_key header name should configure their tool
# auth to use ``X-API-Key`` for the admin HTTP path, or wire a custom
# :class:`AdminAuthorizer` that ignores the resolved identity.
_FORWARDED_AUTH_HEADERS = ("authorization", "x-api-key")


async def resolve_admin_identity_from_auth_endpoint(
    request: Any,
    endpoint_url: str,
    *,
    http_client: Optional[httpx.AsyncClient] = None,
    request_timeout: float = 10.0,
) -> Optional[AdminIdentity]:
    """Resolve identity by forwarding the caller's auth headers to ``endpoint_url``.

    This is the HTTP-admin analogue of the WebSocket tool-auth path that
    runs through :func:`AuthenticationHandler.verify_credentials_remote`:
    the host-owned endpoint authenticates the caller (Bearer / API key /
    Basic / OAuth2 / SSO bearer) and returns a JSON body describing the
    authenticated user. The admin dependency uses that body to build an
    :class:`AdminIdentity` and then delegates the authorization decision
    to the configured :class:`AdminAuthorizer`.

    The expected response body is a JSON object containing at minimum a
    ``user_id`` (or one of ``email`` / ``sub`` / ``username`` as a
    fallback). ``groups`` / ``roles`` / ``cognito:groups`` are read from
    the same body if present.

    Returns ``None`` (i.e. unauthenticated) when:

    * no recognised auth header is present on the request;
    * the endpoint returns a non-2xx status;
    * the endpoint returns a non-JSON or non-dict body;
    * the body lacks any usable identifier.
    """
    headers: Dict[str, str] = {}
    for header_name in _FORWARDED_AUTH_HEADERS:
        value = request.headers.get(header_name)
        if value:
            headers[header_name] = value
    if not headers:
        return None

    client = http_client
    owns_client = client is None
    try:
        if client is None:
            client = httpx.AsyncClient(timeout=request_timeout)
        response = await client.get(endpoint_url, headers=headers, timeout=request_timeout)
    except httpx.HTTPError as exc:
        logger.warning("admin identity verification call failed error=%s", exc)
        return None
    finally:
        if owns_client and client is not None:
            await client.aclose()

    if not (200 <= response.status_code < 300):
        logger.info("admin identity verification non-2xx status=%s", response.status_code)
        return None
    try:
        body = response.json()
    except ValueError:
        logger.warning("admin identity verification returned non-JSON body")
        return None
    if not isinstance(body, dict):
        logger.warning("admin identity verification returned non-dict body type=%s", type(body).__name__)
        return None

    user_id = body.get("user_id") or body.get("email") or body.get("sub") or body.get("username")
    if not user_id:
        return None
    return AdminIdentity(
        user_id=str(user_id),
        email=body.get("email"),
        groups=_extract_groups(body),
        claims=dict(body),
    )
