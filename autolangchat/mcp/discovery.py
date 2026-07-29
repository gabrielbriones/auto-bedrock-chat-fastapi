"""OAuth discovery metadata endpoints for the MCP SSO auth path (Phase 5).

Publishes the two well-known metadata documents spec-compliant MCP clients
need to auto-discover the configured IdP and perform Authorization Code +
PKCE themselves, with no credentials embedded in static client config:

- ``/.well-known/oauth-protected-resource<mcp_endpoint>`` (RFC 9728): tells
  the client which authorization server(s) can issue tokens for this MCP
  endpoint. The well-known path is derived via the official ``mcp`` SDK's
  ``build_resource_metadata_url`` helper; the response body is served by
  this module as a ``ProtectedResourceMetadata`` document.
- ``/.well-known/oauth-authorization-server`` (RFC 8414): a minimal
  translation of whatever ``SSOProvider`` already resolved from the
  configured IdP's OIDC discovery document (or manual endpoint overrides),
  for MCP clients that only understand the RFC 8414 shape rather than OIDC
  discovery. This is net-new — ``SSOProvider`` only *consumes* an IdP's
  discovery doc today, it does not publish its own AS metadata.

Both are only mounted when ``config.mcp_enabled and config.sso_enabled``
(see ``plugin.py::_setup_mcp_routes``). The actual login/token-exchange flow
is unchanged — MCP clients use the existing ``/chat/auth/sso/login`` /
``/chat/auth/sso/callback`` web routes and present the resulting
``session_token`` as a Bearer token on MCP requests (see ``mcp/auth.py``).
"""

import logging
from typing import TYPE_CHECKING, List, Optional
from urllib.parse import urlparse

from mcp.server.auth.routes import build_resource_metadata_url
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from ..config import ChatConfig
    from ..sso.sso_handler import SSOProvider

logger = logging.getLogger(__name__)


def _resolve_issuer(sso_provider: "SSOProvider", config: "ChatConfig") -> Optional[str]:
    """Resolve an issuer URL for discovery metadata, falling back when the
    IdP's discovery document didn't include an explicit ``issuer`` claim.

    Falls back to the origin (scheme + host) of whichever manual SSO
    endpoint override is configured, since IdPs without a discovery
    document still need *some* stable issuer identifier to publish.
    """
    if sso_provider.issuer:
        return sso_provider.issuer

    for url in (config.sso_discovery_url, config.sso_authorization_url, config.sso_token_url):
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"

    return None


def build_protected_resource_routes(
    resource_url: str, sso_provider: "SSOProvider", config: "ChatConfig"
) -> List[Route]:
    """Build the RFC 9728 ``/.well-known/oauth-protected-resource`` route.

    The well-known *path* is static (derived from ``resource_url`` alone,
    via the official SDK's own ``build_resource_metadata_url`` helper — RFC
    9728 §3.1 inserts ``/.well-known/oauth-protected-resource`` between the
    host and the resource's own path). The response *body* (which
    authorization server issues tokens for this resource) is resolved
    lazily per-request via ``sso_provider.discover()``, since discovery is
    async and can't run synchronously during ``plugin.__init__``.

    Args:
        resource_url: The public URL of the MCP endpoint itself
            (e.g. ``https://example.com/chat/mcp``).
    """
    resource_any_url = AnyHttpUrl(resource_url)
    metadata_url = build_resource_metadata_url(resource_any_url)
    well_known_path = urlparse(str(metadata_url)).path

    async def _handle(request: Request) -> JSONResponse:
        try:
            await sso_provider.discover()
        except Exception as exc:  # SSODiscoveryError or any transport failure
            # This endpoint is intentionally public/unauthenticated (required by
            # RFC 9728), so the raw exception text is logged server-side only --
            # never returned to the caller, to avoid leaking internal config
            # (e.g. sso_discovery_url) or implementation details.
            logger.error("SSO discovery failed while serving protected resource metadata: %s", exc)
            return JSONResponse(
                {"error": "sso_discovery_failed", "detail": "SSO discovery is temporarily unavailable."},
                status_code=503,
            )

        issuer = _resolve_issuer(sso_provider, config)
        if not issuer:
            return JSONResponse(
                {"error": "sso_not_configured", "detail": "No authorization server issuer could be resolved."},
                status_code=503,
            )

        metadata = ProtectedResourceMetadata(resource=resource_any_url, authorization_servers=[AnyHttpUrl(issuer)])
        return JSONResponse(metadata.model_dump(mode="json", exclude_none=True, by_alias=True))

    return [Route(well_known_path, endpoint=_handle, methods=["GET"])]


def build_authorization_server_metadata_route(sso_provider: "SSOProvider", config: "ChatConfig") -> Route:
    """Build the RFC 8414 ``/.well-known/oauth-authorization-server`` route.

    The handler calls ``sso_provider.discover()`` on every request — a no-op
    after the first call, since discovery is cached and short-circuits once
    endpoints are resolved (see ``SSOProvider.discover()``) — so this works
    regardless of whether discovery has already run at app startup.

    Returns a 503 JSON error (rather than raising) if discovery hasn't
    resolved an authorization/token endpoint yet, e.g. because the IdP is
    unreachable or SSO endpoints aren't fully configured.
    """

    async def _handle(request: Request) -> JSONResponse:
        try:
            await sso_provider.discover()
        except Exception as exc:  # SSODiscoveryError or any transport failure
            # Public/unauthenticated endpoint (required by RFC 8414) -- log the
            # real exception server-side only, never return it to the caller.
            logger.error("SSO discovery failed while serving AS metadata: %s", exc)
            return JSONResponse(
                {"error": "sso_discovery_failed", "detail": "SSO discovery is temporarily unavailable."},
                status_code=503,
            )

        issuer = _resolve_issuer(sso_provider, config)
        if not issuer or not sso_provider.authorization_endpoint or not sso_provider.token_endpoint:
            return JSONResponse(
                {"error": "sso_not_configured", "detail": "Authorization/token endpoints are not resolved."},
                status_code=503,
            )

        metadata = OAuthMetadata(
            issuer=AnyHttpUrl(issuer),
            authorization_endpoint=AnyHttpUrl(sso_provider.authorization_endpoint),
            token_endpoint=AnyHttpUrl(sso_provider.token_endpoint),
            response_types_supported=["code"],
            grant_types_supported=["authorization_code", "refresh_token"],
            code_challenge_methods_supported=["S256"],
        )
        return JSONResponse(metadata.model_dump(mode="json", exclude_none=True, by_alias=True))

    return Route("/.well-known/oauth-authorization-server", endpoint=_handle, methods=["GET"])
