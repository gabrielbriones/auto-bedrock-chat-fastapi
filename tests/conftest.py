"""Test configuration file"""

import pytest


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
