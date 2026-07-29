"""Per-request auth extraction for the MCP endpoint (XMGPLAT-11065, Phase 4).

Builds an ``AuthInfo`` (``Credentials`` + ``AuthenticationHandler``) from the
raw HTTP headers of an incoming MCP request. Mirrors the same auth types
already supported for tool-call execution via the WebSocket ``auth`` message
(``websocket_handler.py::_handle_auth_message``) — just sourced from HTTP
headers (leg 1: MCP client -> MCP endpoint) instead of a JSON message, then
forwarded unchanged to the target REST API (leg 2: MCP server -> target REST
API) via the same ``AuthenticationHandler.apply_auth_to_headers()`` used
everywhere else in the codebase.

Stateless by design (matches ``StreamableHTTPSessionManager(stateless=True)``
from ``mcp/server.py``): a fresh ``Credentials``/``AuthenticationHandler``
pair is built on every request, so an OAuth2 Client Credentials token is not
cached across requests — only the (no-op) cache within a single call.

SSO is the one exception to "stateless": an MCP client authenticates once
through the existing web-based SSO login flow (``/chat/auth/sso/login`` ->
IdP -> ``/chat/auth/sso/callback``, unchanged from the web UI) and is handed
back an opaque ``session_token`` JWT. That token is then presented on every
subsequent MCP request as a normal ``Authorization: Bearer <session_token>``
header. This module recognizes it (by successfully validating it against
``SSOSessionStore``) and swaps in the *IdP* access token stored server-side
for leg 2, mirroring ``websocket_handler.py::_validate_sso_token_and_extract_user``
exactly so both transports produce an identical ``Credentials`` shape for the
same session. Falls back to treating the header as a plain bearer token when
SSO is not enabled or the token doesn't validate as an SSO session.
"""

import base64
import binascii
import logging
from typing import Any, Mapping, Optional

from ..auth_handler import AuthenticationHandler, AuthType, Credentials
from ..graph.tools.manager import AuthInfo

logger = logging.getLogger(__name__)

DEFAULT_API_KEY_HEADER = "X-API-Key"
OAUTH2_CLIENT_ID_HEADER = "X-OAuth2-Client-Id"
OAUTH2_CLIENT_SECRET_HEADER = "X-OAuth2-Client-Secret"
OAUTH2_TOKEN_URL_HEADER = "X-OAuth2-Token-Url"
OAUTH2_SCOPE_HEADER = "X-OAuth2-Scope"
CUSTOM_HEADER_PREFIX = "X-Forward-"


def _get_header(headers: Mapping[str, str], name: str) -> Optional[str]:
    """Case-insensitive header lookup.

    Starlette's ``Headers`` is already case-insensitive, but this also works
    for plain ``dict`` headers (used in tests) regardless of the casing they
    were built with.
    """
    value = headers.get(name)
    if value is not None:
        return value
    name_lower = name.lower()
    for key, val in headers.items():
        if key.lower() == name_lower:
            return val
    return None


def _build_sso_credentials(
    session_token: str, sso_session_store: Any, sso_session_secret: str
) -> Optional[Credentials]:
    """Validate an SSO session token and build ``Credentials`` for it.

    Mirrors ``websocket_handler.py::_validate_sso_token_and_extract_user``'s
    credential-construction logic (minus the optional remote
    ``auth_verification_endpoint`` call, which Phase 4's static auth types
    don't perform either). Returns ``None`` if the token doesn't validate as
    an SSO session -- the caller falls back to treating it as a plain bearer
    token.
    """
    sso_session_id = sso_session_store.validate_session_token(session_token, sso_session_secret)
    if not sso_session_id:
        return None

    sso_session = sso_session_store.get_session(sso_session_id)
    if not sso_session:
        return None

    access_token = sso_session.get("access_token")
    if not access_token:
        logger.warning("SSO session missing access token: %s", sso_session_id)
        sso_session_store.delete_session(sso_session_id)
        return None

    user_info = sso_session.get("user_info", {})
    display_name = user_info.get("name") or user_info.get("email") or "SSO User"

    return Credentials(
        auth_type=AuthType.SSO,
        bearer_token=access_token,
        session_token=session_token,
        sso_user_info=user_info,
        metadata={"sso_session_id": sso_session_id, "display_name": display_name},
    )


def build_credentials_from_headers(
    headers: Mapping[str, str],
    sso_session_store: Optional[Any] = None,
    sso_session_secret: Optional[str] = None,
) -> Optional[Credentials]:
    """Build a ``Credentials`` object from an incoming MCP request's headers.

    Detection order (first match wins), mirroring how each scheme is
    conventionally presented over HTTP:

    1. ``Authorization: Bearer <session_token>`` where ``<session_token>``
       validates against ``SSOSessionStore`` -> SSO (only attempted when
       ``sso_session_store``/``sso_session_secret`` are provided)
    2. ``Authorization: Bearer <token>`` (any other value) -> bearer token
    3. ``Authorization: Basic <base64(user:pass)>`` -> basic auth
    4. ``X-OAuth2-Client-Id`` / ``X-OAuth2-Client-Secret`` / ``X-OAuth2-Token-Url``
       -> OAuth2 Client Credentials
    5. ``X-API-Key`` -> API key
    6. Any ``X-Forward-*`` headers -> custom headers (prefix stripped),
       forwarded verbatim to the target API

    Returns ``None`` if no recognized credentials are present — the call
    still executes unauthenticated, same as today's WebSocket flow when no
    ``auth`` message was ever sent.
    """
    authorization = _get_header(headers, "Authorization")
    if authorization:
        scheme, _, value = authorization.partition(" ")
        scheme = scheme.lower()
        if scheme == "bearer" and value:
            if sso_session_store is not None and sso_session_secret:
                sso_credentials = _build_sso_credentials(value, sso_session_store, sso_session_secret)
                if sso_credentials is not None:
                    return sso_credentials
            return Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token=value)
        if scheme == "basic" and value:
            try:
                decoded = base64.b64decode(value).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                logger.warning("Malformed Basic auth header on MCP request; ignoring")
                decoded = ""
            if ":" in decoded:
                username, _, password = decoded.partition(":")
                return Credentials(auth_type=AuthType.BASIC_AUTH, username=username, password=password)

    client_id = _get_header(headers, OAUTH2_CLIENT_ID_HEADER)
    client_secret = _get_header(headers, OAUTH2_CLIENT_SECRET_HEADER)
    token_url = _get_header(headers, OAUTH2_TOKEN_URL_HEADER)
    if client_id and client_secret and token_url:
        return Credentials(
            auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
            client_id=client_id,
            client_secret=client_secret,
            token_url=token_url,
            scope=_get_header(headers, OAUTH2_SCOPE_HEADER),
        )

    api_key = _get_header(headers, DEFAULT_API_KEY_HEADER)
    if api_key:
        return Credentials(auth_type=AuthType.API_KEY, api_key=api_key, api_key_header=DEFAULT_API_KEY_HEADER)

    custom_headers = {
        key[len(CUSTOM_HEADER_PREFIX) :]: value
        for key, value in headers.items()
        if key.lower().startswith(CUSTOM_HEADER_PREFIX.lower())
    }
    if custom_headers:
        return Credentials(auth_type=AuthType.CUSTOM, custom_headers=custom_headers)

    return None


def build_auth_info_from_headers(
    headers: Mapping[str, str],
    http_client: Optional[Any] = None,
    sso_session_store: Optional[Any] = None,
    sso_session_secret: Optional[str] = None,
) -> Optional[AuthInfo]:
    """Build a transport-agnostic ``AuthInfo`` from an incoming MCP request's headers.

    Returns ``None`` when no recognized credentials are present (so
    ``ToolManager.call_tool`` is invoked with ``auth_info=None``, i.e. leg 2
    is called unauthenticated).

    Args:
        headers: The incoming MCP request's headers (case-insensitive).
        http_client: Shared ``httpx.AsyncClient`` (e.g. ``ToolManager.http_client``)
            wired into the ``AuthenticationHandler`` for the OAuth2 Client
            Credentials token exchange. Other auth types don't need it.
        sso_session_store: ``SSOSessionStore`` instance (shared with the web
            UI), enabling SSO session-token recognition. ``None`` disables it.
        sso_session_secret: Signing secret for validating SSO session tokens
            (``config.sso_session_secret``).
    """
    credentials = build_credentials_from_headers(
        headers, sso_session_store=sso_session_store, sso_session_secret=sso_session_secret
    )
    if credentials is None:
        return None

    auth_handler = AuthenticationHandler(credentials)
    if credentials.auth_type == AuthType.OAUTH2_CLIENT_CREDENTIALS and http_client is not None:
        auth_handler.set_http_client(http_client)

    return AuthInfo(credentials=credentials, auth_handler=auth_handler)
