"""Test configuration file"""

import pytest


@pytest.fixture
def mock_aws_credentials(monkeypatch):
    """Mock AWS credentials for testing"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def sample_fastapi_app():
    """Create a sample FastAPI app for testing"""
    from fastapi import FastAPI

    app = FastAPI(title="Test App")

    @app.get("/test")
    async def test_endpoint():
        return {"message": "test"}

    @app.post("/create")
    async def create_endpoint(data: dict):
        return {"created": data}

    return app
