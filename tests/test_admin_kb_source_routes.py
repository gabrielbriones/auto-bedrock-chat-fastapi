"""Tests for the ``/admin/kb/sources/{web,file}`` ingestion routes and status endpoint."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ._autolangchat_imports import load_module

exceptions_mod = load_module("autolangchat.exceptions", "exceptions.py")
models_mod = load_module("autolangchat.models", "models.py")
admin_errors_mod = load_module(
    "autolangchat.admin.admin_errors",
    "admin/admin_errors.py",
    extra_modules={"autolangchat.exceptions": exceptions_mod, "autolangchat.models": models_mod},
)
kb_routes_mod = load_module(
    "autolangchat.admin.admin_kb_routes",
    "admin/admin_kb_routes.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.admin.admin_errors": admin_errors_mod,
    },
)
content_crawler_mod = load_module("autolangchat.rag.content_crawler", "rag/content_crawler.py")

AdminAPIError = exceptions_mod.AdminAPIError
register_admin_error_handlers = admin_errors_mod.register_admin_error_handlers
register_admin_kb_routes = kb_routes_mod.register_admin_kb_routes
ContentCrawler = content_crawler_mod.ContentCrawler

# Content long enough to survive TextChunker's default min_chunk_size=50 words.
_LONG_TEXT = "hello world " * 60


class _Identity(SimpleNamespace):
    user_id: str = "admin"


class _FakeKBStore:
    """Records add_document/add_chunk calls; no real persistence."""

    def __init__(self):
        self.documents = {}
        self.chunks = []

    def add_document(self, *, doc_id, **kwargs):
        self.documents[doc_id] = kwargs

    def add_chunk(self, *, chunk_id, **kwargs):
        self.chunks.append({"chunk_id": chunk_id, **kwargs})


class _FailingKBStore(_FakeKBStore):
    """Like _FakeKBStore, but add_document raises for one doc_id substring."""

    def __init__(self, fail_doc_substring: str):
        super().__init__()
        self._fail_doc_substring = fail_doc_substring

    def add_document(self, *, doc_id, **kwargs):
        if self._fail_doc_substring in doc_id:
            raise RuntimeError("simulated store failure")
        super().add_document(doc_id=doc_id, **kwargs)


class _FakeHTMLResponse:
    """Minimal aiohttp response stand-in for a single HTML page."""

    def __init__(self, html: str, status: int = 200):
        self.status = status
        self.headers = {"Content-Type": "text/html"}
        self._html = html

    async def text(self):
        return self._html

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    """Minimal aiohttp.ClientSession stand-in; every GET returns the same page."""

    def __init__(self, html: str):
        self._html = html

    def get(self, url, **kwargs):
        return _FakeHTMLResponse(self._html)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _embedding_client():
    client = MagicMock()
    client.generate_embeddings_batch = AsyncMock(return_value=[[0.1, 0.2]] * 10)
    return client


def _slow_embedding_client(delay: float = 0.3):
    """Embedding client whose call takes real time — keeps a run 'running'
    long enough for a second, immediately-following request to observe it."""

    async def _generate(*, texts, model_id, batch_size=25):
        await asyncio.sleep(delay)
        return [[0.1, 0.2]] * len(texts)

    client = MagicMock()
    client.generate_embeddings_batch = AsyncMock(side_effect=_generate)
    return client


def _build_app(*, kb_store=None, embedding_client=None, embedding_model="fake-model", authenticated=True):
    app = FastAPI()
    register_admin_error_handlers(app)

    if authenticated:

        async def require_admin():
            return _Identity(user_id="admin")

    else:

        async def require_admin():
            raise AdminAPIError(status_code=401, code="not_authenticated", detail="not authenticated")

    register_admin_kb_routes(
        app,
        prefix="/bedrock-chat/admin",
        kb_store=kb_store or _FakeKBStore(),
        require_admin=require_admin,
        embedding_client=embedding_client,
        embedding_model=embedding_model,
    )
    return app


def _wait_until_not_running(client, timeout=5.0):
    """Poll GET /status in real time until the run leaves the 'running' phase.

    The background ingestion task runs on the TestClient's own event loop
    (only advanced between requests), so a real (short) sleep between polls
    is needed to give it a chance to progress and finish.
    """
    deadline = time.time() + timeout
    resp = None
    while time.time() < deadline:
        resp = client.get("/bedrock-chat/admin/kb/sources/status")
        if resp.json()["phase"] != "running":
            return resp
        time.sleep(0.05)
    return resp


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_web_source_requires_admin_auth():
    app = _build_app(authenticated=False, embedding_client=_embedding_client())
    client = TestClient(app)
    resp = client.post("/bedrock-chat/admin/kb/sources/web", json={"name": "s", "urls": ["https://example.com"]})
    assert resp.status_code == 401


def test_file_source_requires_admin_auth():
    app = _build_app(authenticated=False, embedding_client=_embedding_client())
    client = TestClient(app)
    resp = client.post(
        "/bedrock-chat/admin/kb/sources/file",
        data={"name": "s"},
        files=[("files", ("a.txt", b"hello", "text/plain"))],
    )
    assert resp.status_code == 401


def test_status_requires_admin_auth():
    app = _build_app(authenticated=False, embedding_client=_embedding_client())
    client = TestClient(app)
    resp = client.get("/bedrock-chat/admin/kb/sources/status")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_web_source_rejects_empty_urls():
    app = _build_app(embedding_client=_embedding_client())
    client = TestClient(app)
    resp = client.post("/bedrock-chat/admin/kb/sources/web", json={"name": "s", "urls": []})
    assert resp.status_code == 422


def test_file_source_rejects_no_files():
    app = _build_app(embedding_client=_embedding_client())
    client = TestClient(app)
    resp = client.post("/bedrock-chat/admin/kb/sources/file", data={"name": "s"}, files=[])
    assert resp.status_code == 422
    # `files` must be optional at the FastAPI-param level, or this request
    # never reaches our handler and gets FastAPI's own "field required"
    # envelope instead of our flat ErrorResponse.
    assert resp.json() == {"code": "no_files_uploaded", "detail": "at least one file must be uploaded"}


def test_file_source_rejects_non_utf8_file():
    app = _build_app(embedding_client=_embedding_client())
    client = TestClient(app)
    resp = client.post(
        "/bedrock-chat/admin/kb/sources/file",
        data={"name": "s"},
        files=[("files", ("bad.bin", b"\xff\xfe\xfd", "application/octet-stream"))],
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_file_encoding"


def test_ingestion_unavailable_without_embedding_client():
    app = _build_app(embedding_client=None)
    client = TestClient(app)
    resp = client.post("/bedrock-chat/admin/kb/sources/web", json={"name": "s", "urls": ["https://example.com"]})
    assert resp.status_code == 503
    assert resp.json()["code"] == "kb_source_ingestion_unavailable"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_is_idle_before_any_run():
    app = _build_app(embedding_client=_embedding_client())
    client = TestClient(app)
    resp = client.get("/bedrock-chat/admin/kb/sources/status")
    assert resp.status_code == 200
    assert resp.json()["phase"] == "idle"


# ---------------------------------------------------------------------------
# Mocked ingestion flows
# ---------------------------------------------------------------------------


def test_trigger_web_source_ingests_page_into_kb_store():
    kb_store = _FakeKBStore()
    app = _build_app(kb_store=kb_store, embedding_client=_embedding_client())
    html = f"<html><body>{_LONG_TEXT}</body></html>"

    with TestClient(app) as client, patch("aiohttp.ClientSession", return_value=_FakeSession(html)):
        resp = client.post(
            "/bedrock-chat/admin/kb/sources/web",
            json={"name": "docs", "urls": ["https://example.com/"]},
        )
        assert resp.status_code == 202
        assert resp.json()["phase"] == "running"
        assert resp.json()["run_id"]

        final = _wait_until_not_running(client)

    assert final.json()["phase"] == "completed"
    assert final.json()["chunks_written"] >= 1
    assert final.json()["errors"] == []
    assert kb_store.documents
    assert kb_store.chunks


def test_trigger_file_source_ingests_upload_into_kb_store():
    kb_store = _FakeKBStore()
    app = _build_app(kb_store=kb_store, embedding_client=_embedding_client())

    with TestClient(app) as client:
        resp = client.post(
            "/bedrock-chat/admin/kb/sources/file",
            data={"name": "uploads"},
            files=[("files", ("notes.md", _LONG_TEXT.encode("utf-8"), "text/markdown"))],
        )
        assert resp.status_code == 202

        final = _wait_until_not_running(client)

    assert final.json()["phase"] == "completed"
    assert final.json()["files_processed"] == 1
    assert final.json()["chunks_written"] >= 1
    assert kb_store.documents
    assert kb_store.chunks


def test_second_concurrent_run_is_rejected():
    app = _build_app(embedding_client=_slow_embedding_client())

    with TestClient(app) as client:
        first = client.post(
            "/bedrock-chat/admin/kb/sources/file",
            data={"name": "s1"},
            files=[("files", ("a.md", _LONG_TEXT.encode("utf-8"), "text/markdown"))],
        )
        assert first.status_code == 202

        second = client.post(
            "/bedrock-chat/admin/kb/sources/file",
            data={"name": "s2"},
            files=[("files", ("b.md", _LONG_TEXT.encode("utf-8"), "text/markdown"))],
        )
        assert second.status_code == 409
        assert second.json()["code"] == "kb_source_run_already_in_progress"

        _wait_until_not_running(client)


# ---------------------------------------------------------------------------
# max_pages cap (direct ContentCrawler test — fast/deterministic, no real
# network sleeps or background-task polling needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_pages_caps_number_of_pages_crawled():
    html_with_many_links = (
        "<html><body>" + "".join(f'<a href="/page{i}">p{i}</a>' for i in range(20)) + "</body></html>"
    )

    crawler = ContentCrawler(rate_limit_delay=0)
    with patch("aiohttp.ClientSession", return_value=_FakeSession(html_with_many_links)):
        docs = await crawler.crawl_url(
            "https://example.com/",
            recursive=True,
            max_depth=2,
            max_pages=3,
        )

    assert len(docs) == 3


@pytest.mark.asyncio
async def test_max_pages_caps_fetch_attempts_not_just_successful_documents():
    """A page that fails to fetch must still count against max_pages.

    Otherwise a site returning many 404s (or otherwise-failing pages) could
    make the crawler issue unbounded requests while len(documents) never
    reaches the cap.
    """
    root_html = "<html><body>" + "".join(f'<a href="/broken{i}">x</a>' for i in range(5)) + "</body></html>"
    fetch_urls = []

    class _RootThenAllBrokenSession:
        def get(self, url, **kwargs):
            fetch_urls.append(url)
            if "broken" in url:
                return _FakeHTMLResponse("", status=404)
            return _FakeHTMLResponse(root_html)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    crawler = ContentCrawler(rate_limit_delay=0)
    with patch("aiohttp.ClientSession", return_value=_RootThenAllBrokenSession()):
        docs = await crawler.crawl_url("https://example.com/", recursive=True, max_depth=2, max_pages=2)

    # Root (success) + exactly 1 failing link attempted before the cap trips —
    # not all 5 broken links.
    assert len(docs) == 1
    assert len(fetch_urls) == 2


# ---------------------------------------------------------------------------
# Per-document indexing failures must not abort the whole run
# ---------------------------------------------------------------------------


def test_web_source_indexing_failure_does_not_abort_whole_run():
    html_root = f'<html><body>{_LONG_TEXT}<a href="/page2">next</a></body></html>'
    html_page2 = f"<html><body>{_LONG_TEXT}</body></html>"

    class _TwoPageSession:
        def get(self, url, **kwargs):
            return _FakeHTMLResponse(html_page2 if "page2" in url else html_root)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    kb_store = _FailingKBStore(fail_doc_substring="page2")
    app = _build_app(kb_store=kb_store, embedding_client=_embedding_client())

    with TestClient(app) as client, patch("aiohttp.ClientSession", return_value=_TwoPageSession()):
        resp = client.post(
            "/bedrock-chat/admin/kb/sources/web",
            json={"name": "docs", "urls": ["https://example.com/"]},
        )
        assert resp.status_code == 202

        final = _wait_until_not_running(client)

    # The run completes (not "failed") with the good page indexed and the
    # bad one recorded in errors, instead of the whole run aborting.
    assert final.json()["phase"] == "completed"
    assert final.json()["pages_processed"] == 1
    assert len(final.json()["errors"]) == 1
    assert "page2" in final.json()["errors"][0]


# ---------------------------------------------------------------------------
# extra_headers / cookies reach the outgoing crawl request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crawler_sends_extra_headers_and_cookies():
    captured = {}

    class _CapturingSession:
        def get(self, url, headers=None, cookies=None, **kwargs):
            captured["headers"] = headers
            captured["cookies"] = cookies
            return _FakeHTMLResponse(f"<html><body>{_LONG_TEXT}</body></html>")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    crawler = ContentCrawler(
        extra_headers={"Authorization": "Bearer secret-token"},
        cookies={"session_id": "abc123"},
    )
    with patch("aiohttp.ClientSession", return_value=_CapturingSession()):
        docs = await crawler.crawl_url("https://example.com/", recursive=False)

    assert docs
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["cookies"] == {"session_id": "abc123"}


# ---------------------------------------------------------------------------
# allowed_domains: default-to-own-hostname + real hostname matching
# ---------------------------------------------------------------------------


def test_is_allowed_domain_matches_host_and_subdomain_not_substring_trick():
    crawler = ContentCrawler()
    assert crawler._is_allowed_domain("https://example.com/docs", ["example.com"])
    assert crawler._is_allowed_domain("https://sub.example.com/x", ["example.com"])
    assert not crawler._is_allowed_domain("https://evil.com/x", ["example.com"])
    # A link merely containing the domain name in its path/query shouldn't
    # count as "same site" (the old naive substring check would wrongly
    # allow this).
    assert not crawler._is_allowed_domain("https://evil.com/redirect?u=example.com", ["example.com"])


@pytest.mark.asyncio
async def test_crawl_url_default_allowed_domains_excludes_external_links():
    root_html = (
        f"<html><body>{_LONG_TEXT}"
        '<a href="/same-host-page">same host</a>'
        '<a href="https://external.example/other">external</a>'
        "</body></html>"
    )
    leaf_html = f"<html><body>{_LONG_TEXT}</body></html>"

    fetched = []

    class _RecordingSession:
        def get(self, url, **kwargs):
            fetched.append(url)
            return _FakeHTMLResponse(leaf_html if "same-host-page" in url else root_html)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    crawler = ContentCrawler(rate_limit_delay=0)
    with patch("aiohttp.ClientSession", return_value=_RecordingSession()):
        await crawler.crawl_url("https://example.com/", recursive=True, max_depth=2)

    assert any("example.com/same-host-page" in u for u in fetched)
    assert not any("external.example" in u for u in fetched)
