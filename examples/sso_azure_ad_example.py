"""Example FastAPI application with Azure AD SSO and auto-bedrock-chat-fastapi

This example demonstrates:
1. SSO authentication via Microsoft Entra ID (formerly Azure AD)
2. Users authenticate through Microsoft's login page
3. Access tokens are automatically forwarded to API tool calls
4. Tenant-specific OIDC discovery

Prerequisites:
- An Azure subscription with Entra ID (Azure AD)
- An App Registration in the Azure portal

Azure AD Setup:
1. Azure Portal → Entra ID → App registrations → New registration
2. Name: "My Chat App"
3. Supported account types: "Accounts in this organizational directory only" (single tenant)
4. Redirect URI (Web): http://localhost:8000/chat/auth/callback
5. Under "Certificates & secrets" → New client secret → copy the value
6. Under "API permissions" → Add: Microsoft Graph → openid, profile, email
7. Copy Application (client) ID and Directory (tenant) ID

Environment variables (.env):
    BEDROCK_SSO_ENABLED=true
    BEDROCK_SSO_PROVIDER=azure_ad
    BEDROCK_SSO_CLIENT_ID=<your-application-client-id>
    BEDROCK_SSO_CLIENT_SECRET=<your-client-secret>
    BEDROCK_SSO_DISCOVERY_URL=https://login.microsoftonline.com/<your-tenant-id>/v2.0/.well-known/openid-configuration
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
    title="SSO Example API (Azure AD)",
    description="Example API with Microsoft Entra ID SSO authentication and AI chat",
    version="1.0.0",
)

security = HTTPBearer(auto_error=True)


# ============================================================================
# Models
# ============================================================================


class Task(BaseModel):
    id: int = Field(..., description="Task ID")
    title: str = Field(..., description="Task title")
    assignee: str = Field(..., description="Assigned user")
    status: str = Field(..., description="Task status: open, in_progress, done")
    created_at: str = Field(..., description="Creation timestamp")


class TaskCreate(BaseModel):
    title: str = Field(..., description="Task title")
    assignee: str = Field(..., description="Assigned user email")


# ============================================================================
# In-memory storage
# ============================================================================

tasks_db: Dict[int, dict] = {
    1: {
        "id": 1,
        "title": "Set up CI/CD pipeline",
        "assignee": "alice@contoso.com",
        "status": "done",
        "created_at": "2026-01-10T09:00:00Z",
    },
    2: {
        "id": 2,
        "title": "Write integration tests",
        "assignee": "bob@contoso.com",
        "status": "in_progress",
        "created_at": "2026-03-05T11:00:00Z",
    },
}
next_id = 3


# ============================================================================
# API Endpoints (protected by bearer token from SSO)
# ============================================================================


@app.get("/tasks", response_model=List[Task], tags=["Tasks"])
async def list_tasks(credentials: HTTPAuthorizationCredentials = Depends(security)):  # noqa: B008
    """List all tasks. Requires authentication via SSO."""
    return list(tasks_db.values())


@app.get("/tasks/{task_id}", response_model=Task, tags=["Tasks"])
async def get_task(task_id: int, credentials: HTTPAuthorizationCredentials = Depends(security)):  # noqa: B008
    """Get a task by ID. Requires authentication via SSO."""
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks_db[task_id]


@app.post("/tasks", response_model=Task, status_code=201, tags=["Tasks"])
async def create_task(task: TaskCreate, credentials: HTTPAuthorizationCredentials = Depends(security)):  # noqa: B008
    """Create a new task. Requires authentication via SSO."""
    global next_id
    new_task = {
        "id": next_id,
        "title": task.title,
        "assignee": task.assignee,
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    tasks_db[next_id] = new_task
    next_id += 1
    return new_task


@app.get("/health", tags=["System"])
async def health_check():
    """Public health check endpoint (no auth required)."""
    return {"status": "healthy"}


# ============================================================================
# Plugin Setup — SSO via Azure AD
# ============================================================================

bedrock_chat = add_bedrock_chat(
    app,
    # SSO Configuration
    sso_enabled=True,
    sso_provider="azure_ad",
    # These come from .env — shown here for clarity:
    # sso_client_id="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    # sso_client_secret="your-client-secret-value",
    # sso_discovery_url="https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration",
    # sso_session_secret="change-me-to-a-random-secret",
    sso_scopes="openid profile email",
    sso_callback_path="/chat/auth/callback",
    # Auth settings — forward SSO token to tool calls
    enable_tool_auth=True,
    require_tool_auth=True,
    supported_auth_types=["sso"],
    # Endpoint filtering
    allowed_paths=["/tasks"],
    excluded_paths=["/docs", "/redoc", "/openapi.json", "/chat", "/ws", "/health"],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
