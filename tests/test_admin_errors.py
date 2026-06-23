from fastapi import FastAPI
from fastapi.testclient import TestClient

from ._autolangchat_imports import load_module

exceptions_mod = load_module("autolangchat.exceptions", "exceptions.py")
admin_errors_mod = load_module(
    "autolangchat.admin.admin_errors",
    "admin/admin_errors.py",
    extra_modules={"autolangchat.exceptions": exceptions_mod},
)

AdminAPIError = exceptions_mod.AdminAPIError
FeedbackNotFoundError = exceptions_mod.FeedbackNotFoundError
InvalidStatusTransitionError = exceptions_mod.InvalidStatusTransitionError
KBDocumentNotFoundError = exceptions_mod.KBDocumentNotFoundError
register_admin_error_handlers = admin_errors_mod.register_admin_error_handlers


def _app_with_route(exc):
    app = FastAPI()
    register_admin_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise exc

    return TestClient(app)


def test_admin_api_error_maps_to_flat_envelope():
    client = _app_with_route(AdminAPIError(status_code=400, code="bad_input", detail="bad input"))
    resp = client.get("/boom")
    assert resp.status_code == 400
    assert resp.json() == {"code": "bad_input", "detail": "bad input"}


def test_feedback_not_found_maps_to_standard_envelope():
    client = _app_with_route(FeedbackNotFoundError("feedback 123 not found"))
    resp = client.get("/boom")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_invalid_status_transition_maps_to_409():
    client = _app_with_route(InvalidStatusTransitionError("cannot transition"))
    resp = client.get("/boom")
    assert resp.status_code == 409
    assert resp.json()["code"] == "invalid_status_transition"


def test_kb_document_not_found_maps_to_404():
    client = _app_with_route(KBDocumentNotFoundError("kb document missing"))
    resp = client.get("/boom")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
