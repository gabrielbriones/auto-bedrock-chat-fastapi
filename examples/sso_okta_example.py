"""Example FastAPI application with Okta SSO and auto-bedrock-chat-fastapi

This example demonstrates:
1. SSO authentication via Okta (OAuth2 Authorization Code + PKCE)
2. Users authenticate through Okta's hosted login page
3. Access tokens are automatically forwarded to API tool calls
4. Protected endpoints that require a valid bearer token

Prerequisites:
- An Okta developer account (https://developer.okta.com)
- An Okta application configured as a "Web" app with Authorization Code + PKCE
- Redirect URI set to: http://localhost:8000/chat/auth/callback

Okta Setup:
1. Go to Applications → Create App Integration → OIDC → Web Application
2. Grant type: Authorization Code
3. Sign-in redirect URI: http://localhost:8000/chat/auth/callback
4. Assignments: Allow everyone in your org (or specific groups)
5. Copy Client ID and Okta domain

Environment variables (.env):
    BEDROCK_SSO_ENABLED=true
    BEDROCK_SSO_PROVIDER=okta
    BEDROCK_SSO_CLIENT_ID=<your-okta-client-id>
    BEDROCK_SSO_DISCOVERY_URL=https://<your-okta-domain>/oauth2/default/.well-known/openid-configuration
    BEDROCK_SSO_SESSION_SECRET=<random-secret-at-least-32-chars>
    BEDROCK_SSO_SCOPES=openid profile email
    AWS_REGION=us-east-1
"""

from datetime import datetime, timezone
from typing import Dict, List

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI(
    title="SSO Example API (Okta)",
    description="Example API with Okta SSO authentication and AI chat",
    version="1.0.0",
)

security = HTTPBearer(auto_error=True)


# ============================================================================
# Models
# ============================================================================


class Item(BaseModel):
    id: int = Field(..., description="Item ID")
    name: str = Field(..., description="Item name")
    status: str = Field(..., description="Item status")
    created_at: str = Field(..., description="Creation timestamp")


class ItemCreate(BaseModel):
    name: str = Field(..., description="Item name")


# ============================================================================
# In-memory storage
# ============================================================================

items_db: Dict[int, dict] = {
    1: {"id": 1, "name": "Project Alpha", "status": "active", "created_at": "2026-01-15T10:00:00Z"},
    2: {"id": 2, "name": "Project Beta", "status": "completed", "created_at": "2026-02-20T14:30:00Z"},
}
next_id = 3


# ============================================================================
# API Endpoints (protected by bearer token from SSO)
# ============================================================================


@app.get("/items", response_model=List[Item], tags=["Items"])
async def list_items(credentials: HTTPAuthorizationCredentials = Depends(security)):  # noqa: B008
    """List all items. Requires authentication via SSO."""
    return list(items_db.values())


@app.get("/items/{item_id}", response_model=Item, tags=["Items"])
async def get_item(item_id: int, credentials: HTTPAuthorizationCredentials = Depends(security)):  # noqa: B008
    """Get an item by ID. Requires authentication via SSO."""
    if item_id not in items_db:
        raise HTTPException(status_code=404, detail="Item not found")
    return items_db[item_id]


@app.post("/items", response_model=Item, status_code=201, tags=["Items"])
async def create_item(item: ItemCreate, credentials: HTTPAuthorizationCredentials = Depends(security)):  # noqa: B008
    """Create a new item. Requires authentication via SSO."""
    global next_id
    new_item = {
        "id": next_id,
        "name": item.name,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    items_db[next_id] = new_item
    next_id += 1
    return new_item


@app.get("/health", tags=["System"])
async def health_check():
    """Public health check endpoint (no auth required)."""
    return {"status": "healthy"}


# ============================================================================
# Plugin Setup — SSO via Okta
# ============================================================================

bedrock_chat = add_bedrock_chat(
    app,
    # SSO Configuration
    sso_enabled=True,
    sso_provider="okta",
    # These come from .env — shown here for clarity:
    # sso_client_id="0oa...",
    # sso_discovery_url="https://dev-12345.okta.com/oauth2/default/.well-known/openid-configuration",
    # sso_session_secret="change-me-to-a-random-secret",
    sso_scopes="openid profile email",
    sso_callback_path="/chat/auth/callback",
    # Auth settings — forward SSO token to tool calls
    enable_tool_auth=True,
    require_tool_auth=True,
    supported_auth_types=["sso"],
    # Endpoint filtering
    allowed_paths=["/items"],
    excluded_paths=["/docs", "/redoc", "/openapi.json", "/chat", "/ws", "/health"],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
