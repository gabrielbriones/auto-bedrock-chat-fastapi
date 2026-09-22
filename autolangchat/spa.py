"""Serve the React SPA build from FastAPI (CONTRACT-002 BC-002, Mechanism A).

The SPA under ``frontend/`` is built by Vite into ``frontend/dist`` and mounted
here at ``config.ui_endpoint`` — the same path the chat UI has always been
served from — so the API, the SSO cookie and the chat WebSocket all share one
origin (ADR-006). No standalone Vite server is involved in the served
application.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from .config import ChatConfig

logger = logging.getLogger(__name__)

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
NO_STORE = "no-store"
# Vite's default hashed-asset directory; everything else is treated as mutable.
HASHED_ASSET_PREFIX = "assets/"


def default_dist_dir() -> Path:
    """``<repo>/frontend/dist`` relative to the installed package."""
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"


def resolve_dist_dir(config: ChatConfig) -> Path:
    return Path(config.ui_dist_dir).expanduser() if config.ui_dist_dir else default_dist_dir()


class SPAStaticFiles(StaticFiles):
    """``StaticFiles`` with SPA fallback and cache headers.

    * Existing files are served as-is; hashed ``assets/*`` get an immutable
      cache header, ``index.html`` is ``no-store``.
    * Extension-less unmatched paths (client-side routes such as
      ``/chat/ui/admin/knowledge``) fall back to ``index.html`` so deep links
      survive a reload.
    * Unmatched paths that look like files (``/chat/ui/assets/missing.js``) stay
      404 so a broken asset URL fails loudly instead of loading HTML as JS.
    """

    def __init__(self, directory: os.PathLike) -> None:
        super().__init__(directory=str(directory), html=False, check_dir=True)

    async def get_response(self, path: str, scope: Scope) -> Response:
        if path in ("", "."):
            return await self._index(scope)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            response = None
        if response is None or response.status_code == 404:
            if _looks_like_file(path):
                return PlainTextResponse("Not Found", status_code=404)
            return await self._index(scope)
        response.headers["Cache-Control"] = IMMUTABLE_CACHE if path.startswith(HASHED_ASSET_PREFIX) else NO_STORE
        return response

    async def _index(self, scope: Scope) -> Response:
        response = await super().get_response("index.html", scope)
        response.headers["Cache-Control"] = NO_STORE
        return response


def _looks_like_file(path: str) -> bool:
    return "." in path.rsplit("/", 1)[-1]


def _shadowed_routes(app: FastAPI, mount_path: str) -> list:
    """Routes registered before the SPA mount that live under its prefix."""
    prefix = mount_path.rstrip("/")
    shadowed = []
    for route in app.router.routes:
        route_path: Optional[str] = getattr(route, "path", None)
        if not route_path or not isinstance(route, (Route, WebSocketRoute, Mount)):
            continue
        if route_path == prefix or route_path.startswith(f"{prefix}/"):
            shadowed.append(route_path)
    return shadowed


def mount_spa(app: FastAPI, config: ChatConfig) -> Optional[Path]:
    """Mount the built SPA at ``config.ui_endpoint``.

    Returns the served ``dist`` directory, or ``None`` when no build was found
    (in which case a 503 placeholder is registered so the path explains
    itself instead of 404-ing).
    """
    mount_path = config.ui_endpoint.rstrip("/") or "/"
    dist_dir = resolve_dist_dir(config)

    for path in _shadowed_routes(app, mount_path):
        # Starlette matches in registration order, so these keep winning; the
        # SPA fallback silently loses those paths, which is almost never intended.
        logger.warning(
            "Route %s is registered under the SPA mount %s and will not fall back to the SPA", path, mount_path
        )

    index_html = dist_dir / "index.html"
    if not index_html.is_file():
        logger.warning(
            "SPA build not found at %s (expected index.html). Run `npm run build` in frontend/ "
            "or set AUTOCHAT_UI_DIST_DIR. Serving a 503 placeholder at %s.",
            dist_dir,
            mount_path,
        )

        async def spa_missing(_request: Request) -> Response:
            return PlainTextResponse(
                f"SPA build not found at {dist_dir}. Run `npm run build` in frontend/ or set AUTOCHAT_UI_DIST_DIR.",
                status_code=503,
                headers={"Cache-Control": NO_STORE},
            )

        app.add_api_route(mount_path, spa_missing, methods=["GET"], include_in_schema=False)
        app.add_api_route(f"{mount_path}/{{path:path}}", spa_missing, methods=["GET"], include_in_schema=False)
        return None

    static = SPAStaticFiles(directory=dist_dir)

    # Serve the bare mount path directly rather than via Starlette's
    # trailing-slash redirect, so `GET {ui_endpoint}` is the SPA in one round trip.
    async def spa_root(request: Request) -> Response:
        return await static.get_response("", request.scope)

    app.add_api_route(mount_path, spa_root, methods=["GET", "HEAD"], include_in_schema=False)
    app.mount(mount_path, static, name="spa")
    logger.info("SPA mounted at %s from %s", mount_path, dist_dir)
    return dist_dir
