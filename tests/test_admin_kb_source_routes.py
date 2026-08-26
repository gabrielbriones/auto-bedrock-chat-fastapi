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

# kb_ingestion.py has no heavy (langgraph/boto3/etc.) dependencies of its own,
# so unlike the modules above it's imported normally rather than via
# load_module()'s file-path stub-loading.
from autolangchat.rag.kb_ingestion import ingest_uploaded_files  # noqa: E402

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
        self.documents[doc_id] = dict(kwargs)

    def get_document(self, doc_id):
        return self.documents.get(doc_id)

    def update_document(self, doc_id, *, content=None, **kwargs):
        doc = self.documents.setdefault(doc_id, {})
        if content is not None:
            doc["content"] = content
            # Real store transactionally clears existing chunks for this
            # document when content changes -- simulate that here so tests
            # can assert no stale chunks survive a re-ingest.
            self.chunks = [c for c in self.chunks if c.get("document_id") != doc_id]
        for key, value in kwargs.items():
            if value is not None:
                doc[key] = value
        return doc

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
    # Returns one embedding per requested text — a fixed-length mock would
    # mask the embedding-count-mismatch validation in kb_ingestion.py.
    async def _generate(*, texts, model_id, batch_size=25):
        return [[0.1, 0.2]] * len(texts)

    client = MagicMock()
    client.generate_embeddings_batch = AsyncMock(side_effect=_generate)
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


@pytest.mark.asyncio
async def test_crawl_url_explicit_empty_allowed_domains_disables_restriction():
    """An explicit `[]` must mean "no restriction", distinct from `None`
    (which defaults to the start URL's own hostname)."""
    root_html = f'<html><body>{_LONG_TEXT}<a href="https://external.example/other">external</a></body></html>'
    leaf_html = f"<html><body>{_LONG_TEXT}</body></html>"

    fetched = []

    class _RecordingSession:
        def get(self, url, **kwargs):
            fetched.append(url)
            return _FakeHTMLResponse(leaf_html if "external.example" in url else root_html)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    crawler = ContentCrawler(rate_limit_delay=0)
    # This test is about allowed_domains, not SSRF host validation -- the
    # reserved `.example` TLD used for the "external" link doesn't resolve
    # via real DNS, so the SSRF host check is bypassed here to isolate the
    # behavior under test (dedicated SSRF tests cover `_is_safe_host` itself).
    with (
        patch("aiohttp.ClientSession", return_value=_RecordingSession()),
        patch.object(ContentCrawler, "_is_safe_host", AsyncMock(return_value=True)),
    ):
        await crawler.crawl_url("https://example.com/", recursive=True, max_depth=2, allowed_domains=[])

    assert any("external.example" in u for u in fetched)


def test_crawl_url_malformed_start_url_does_not_block_all_links():
    """A start URL with no parseable hostname must not silently produce
    an unmatchable [""] domain filter that blocks every link."""
    crawler = ContentCrawler()
    # urlparse("not-a-url").hostname is None -- the default-domain fallback
    # must degrade to "no restriction" rather than [""].
    assert crawler._is_allowed_domain("https://example.com/x", [""]) is False


# ---------------------------------------------------------------------------
# Content-Type check is case-insensitive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_and_parse_accepts_mixed_case_content_type():
    class _MixedCaseResponse(_FakeHTMLResponse):
        def __init__(self, html):
            super().__init__(html)
            self.headers = {"Content-Type": "Text/HTML; charset=utf-8"}

    class _MixedCaseSession:
        def get(self, url, **kwargs):
            return _MixedCaseResponse(f"<html><body>{_LONG_TEXT}</body></html>")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    crawler = ContentCrawler()
    with patch("aiohttp.ClientSession", return_value=_MixedCaseSession()):
        doc = await crawler._fetch_and_parse("https://example.com/", "src", None)

    assert doc is not None


# ---------------------------------------------------------------------------
# Fast-path 409 before reading uploads
# ---------------------------------------------------------------------------


def test_file_source_rejects_second_run_without_reading_uploads():
    app = _build_app(embedding_client=_slow_embedding_client())

    with TestClient(app) as client:
        first = client.post(
            "/bedrock-chat/admin/kb/sources/file",
            data={"name": "s1"},
            files=[("files", ("a.md", _LONG_TEXT.encode("utf-8"), "text/markdown"))],
        )
        assert first.status_code == 202

        # A second upload while the first run is in flight should be
        # rejected by the fast-path check before its content is ever read;
        # a huge/slow file here must not add latency to the 409 response.
        second = client.post(
            "/bedrock-chat/admin/kb/sources/file",
            data={"name": "s2"},
            files=[("files", ("b.md", _LONG_TEXT.encode("utf-8"), "text/markdown"))],
        )
        assert second.status_code == 409
        assert second.json()["code"] == "kb_source_run_already_in_progress"

        _wait_until_not_running(client)


# ---------------------------------------------------------------------------
# Embedding count mismatch fails loudly instead of silently truncating
# ---------------------------------------------------------------------------


def test_web_source_embedding_count_mismatch_recorded_as_error():
    kb_store = _FakeKBStore()

    async def _too_few_embeddings(*, texts, model_id, batch_size=25):
        return [[0.1, 0.2]] * max(0, len(texts) - 1)

    embedding_client = MagicMock()
    embedding_client.generate_embeddings_batch = AsyncMock(side_effect=_too_few_embeddings)

    app = _build_app(kb_store=kb_store, embedding_client=embedding_client)
    html = f"<html><body>{_LONG_TEXT}</body></html>"

    with TestClient(app) as client, patch("aiohttp.ClientSession", return_value=_FakeSession(html)):
        resp = client.post(
            "/bedrock-chat/admin/kb/sources/web",
            json={"name": "docs", "urls": ["https://example.com/"]},
        )
        assert resp.status_code == 202

        final = _wait_until_not_running(client)

    assert final.json()["phase"] == "completed"
    assert final.json()["pages_processed"] == 0
    assert len(final.json()["errors"]) == 1
    assert "embedding count mismatch" in final.json()["errors"][0]


# ---------------------------------------------------------------------------
# max_pages / urls bounds
# ---------------------------------------------------------------------------


def test_web_source_rejects_max_pages_out_of_bounds():
    app = _build_app(embedding_client=_embedding_client())
    client = TestClient(app)

    too_low = client.post(
        "/bedrock-chat/admin/kb/sources/web",
        json={"name": "s", "urls": ["https://example.com"], "max_pages": 0},
    )
    assert too_low.status_code == 422

    too_high = client.post(
        "/bedrock-chat/admin/kb/sources/web",
        json={"name": "s", "urls": ["https://example.com"], "max_pages": 10_001},
    )
    assert too_high.status_code == 422


def test_web_source_rejects_too_many_urls():
    app = _build_app(embedding_client=_embedding_client())
    client = TestClient(app)
    resp = client.post(
        "/bedrock-chat/admin/kb/sources/web",
        json={"name": "s", "urls": [f"https://example.com/{i}" for i in range(21)]},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Upload size limits
# ---------------------------------------------------------------------------


def test_file_source_rejects_upload_exceeding_per_file_cap():
    app = _build_app(embedding_client=_embedding_client())
    client = TestClient(app)
    oversized = b"x" * (kb_routes_mod._MAX_FILE_BYTES + 1)
    resp = client.post(
        "/bedrock-chat/admin/kb/sources/file",
        data={"name": "s"},
        files=[("files", ("big.txt", oversized, "text/plain"))],
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "upload_too_large"


def test_file_source_rejects_uploads_exceeding_aggregate_cap(monkeypatch):
    # Use small caps so the test doesn't need to allocate real megabytes.
    monkeypatch.setattr(kb_routes_mod, "_MAX_FILE_BYTES", 100)
    monkeypatch.setattr(kb_routes_mod, "_MAX_TOTAL_UPLOAD_BYTES", 150)

    app = _build_app(embedding_client=_embedding_client())
    client = TestClient(app)
    resp = client.post(
        "/bedrock-chat/admin/kb/sources/file",
        data={"name": "s"},
        files=[
            ("files", ("a.txt", b"x" * 90, "text/plain")),
            ("files", ("b.txt", b"y" * 90, "text/plain")),
        ],
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "upload_too_large"


# ---------------------------------------------------------------------------
# Atomic document replace: re-ingest must not leave stale chunks behind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_uploaded_files_reingest_clears_stale_chunks():
    from autolangchat.rag.embedding_pipeline import TextChunker

    kb_store = _FakeKBStore()
    chunker = TextChunker()
    embedding_client = _embedding_client()

    # First ingest: long content -> multiple chunks.
    long_content = "hello world " * 700
    first = await ingest_uploaded_files(
        vector_db=kb_store,
        bedrock_client=embedding_client,
        chunker=chunker,
        embedding_model="fake-model",
        source_name="src",
        files=[("doc.txt", long_content)],
    )
    doc_id = "src/doc.txt"
    assert first["documents"] == 1
    first_chunk_count = len([c for c in kb_store.chunks if c["document_id"] == doc_id])
    assert first_chunk_count > 1

    # Re-ingest same doc_id with much shorter content -> fewer chunks. If the
    # store only ever upserts by id (add_document/add_chunk) without clearing
    # the old chunk set, the higher-index chunks from the first ingest would
    # survive as stale leftovers.
    short_content = "hello world " * 60
    second = await ingest_uploaded_files(
        vector_db=kb_store,
        bedrock_client=embedding_client,
        chunker=chunker,
        embedding_model="fake-model",
        source_name="src",
        files=[("doc.txt", short_content)],
    )
    assert second["documents"] == 1
    remaining_chunks = [c for c in kb_store.chunks if c["document_id"] == doc_id]
    assert len(remaining_chunks) == 1
    assert remaining_chunks[0]["content"] in short_content


# ---------------------------------------------------------------------------
# processed_urls marked only after a fully successful write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_web_source_failed_page_can_be_retried_by_a_later_source():
    from autolangchat.rag.embedding_pipeline import TextChunker
    from autolangchat.rag.kb_ingestion import ingest_web_source

    html = f"<html><body>{_LONG_TEXT}</body></html>"
    chunker = TextChunker()
    processed_urls = set()

    class _FailingStore(_FakeKBStore):
        def __init__(self):
            super().__init__()
            self.add_document_calls = 0

        def add_document(self, *, doc_id, **kwargs):
            self.add_document_calls += 1
            if self.add_document_calls == 1:
                raise RuntimeError("simulated transient failure")
            super().add_document(doc_id=doc_id, **kwargs)

    kb_store = _FailingStore()
    embedding_client = _embedding_client()

    with patch("aiohttp.ClientSession", return_value=_FakeSession(html)):
        first = await ingest_web_source(
            vector_db=kb_store,
            bedrock_client=embedding_client,
            chunker=chunker,
            embedding_model="fake-model",
            source_name="src1",
            urls=["https://example.com/"],
            processed_urls=processed_urls,
        )
        assert first["documents"] == 0
        assert len(first["errors"]) == 1
        # The failed page must NOT be marked processed -- otherwise a
        # second source sharing this set could never retry it.
        assert "https://example.com/" not in processed_urls

        second = await ingest_web_source(
            vector_db=kb_store,
            bedrock_client=embedding_client,
            chunker=chunker,
            embedding_model="fake-model",
            source_name="src2",
            urls=["https://example.com/"],
            processed_urls=processed_urls,
        )
        assert second["documents"] == 1
        assert "https://example.com/" in processed_urls


# ---------------------------------------------------------------------------
# SSRF: crawler must refuse internal/unsafe hosts, including via redirects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_and_parse_refuses_loopback_host():
    crawler = ContentCrawler()
    doc = await crawler._fetch_and_parse("http://127.0.0.1/secret", "src", None)
    assert doc is None
    assert any("internal/unsafe host" in e for e in crawler.errors)


@pytest.mark.asyncio
async def test_fetch_and_parse_refuses_link_local_metadata_host():
    crawler = ContentCrawler()
    # Cloud metadata endpoint (AWS/GCP/Azure) — a classic SSRF target.
    doc = await crawler._fetch_and_parse("http://169.254.169.254/latest/meta-data/", "src", None)
    assert doc is None
    assert any("internal/unsafe host" in e for e in crawler.errors)


@pytest.mark.asyncio
async def test_fetch_and_parse_follows_redirect_but_blocks_redirect_to_private_host():
    class _RedirectResponse:
        def __init__(self):
            self.status = 302
            self.headers = {"Location": "http://127.0.0.1/internal"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _RedirectingSession:
        def get(self, url, **kwargs):
            return _RedirectResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    crawler = ContentCrawler()
    with patch("aiohttp.ClientSession", return_value=_RedirectingSession()):
        doc = await crawler._fetch_and_parse("https://example.com/redirect", "src", None)

    assert doc is None
    assert any("internal/unsafe host" in e for e in crawler.errors)


@pytest.mark.asyncio
async def test_fetch_and_parse_tags_doc_with_final_url_after_redirect():
    """The indexed doc's URL/ID must reflect the final, post-redirect address
    -- not the originally requested one -- so link resolution and dedup are
    keyed by where the content actually came from."""

    class _RedirectThenOK:
        def __init__(self):
            self._calls = 0

        def get(self, url, **kwargs):
            self._calls += 1
            if self._calls == 1:
                return _FakeRedirectResponse("https://example.com/final")
            return _FakeHTMLResponse(f"<html><body>{_LONG_TEXT}</body></html>")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _FakeRedirectResponse:
        def __init__(self, location):
            self.status = 302
            self.headers = {"Location": location}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    session = _RedirectThenOK()
    crawler = ContentCrawler()
    with patch("aiohttp.ClientSession", return_value=session):
        doc = await crawler._fetch_and_parse("https://example.com/start", "src", None)

    assert doc is not None
    assert doc["url"] == "https://example.com/final"


# ---------------------------------------------------------------------------
# SSRF: sitemap fetching must be as protected as _fetch_and_parse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_sitemap_refuses_loopback_host():
    crawler = ContentCrawler()
    urls = await crawler._parse_sitemap("http://127.0.0.1/sitemap.xml")
    assert urls == []


@pytest.mark.asyncio
async def test_parse_sitemap_blocks_redirect_to_private_host():
    class _RedirectResponse:
        def __init__(self):
            self.status = 302
            self.headers = {"Location": "http://169.254.169.254/sitemap.xml"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _RedirectingSession:
        def get(self, url, **kwargs):
            return _RedirectResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    crawler = ContentCrawler()
    with patch("aiohttp.ClientSession", return_value=_RedirectingSession()):
        urls = await crawler._parse_sitemap("https://example.com/sitemap.xml")

    assert urls == []


@pytest.mark.asyncio
async def test_parse_sitemap_passes_configured_proxy():
    """_parse_sitemap must thread the crawler's proxy through like
    _fetch_and_parse does -- checked via the outgoing request kwargs rather
    than a full XML parse, since BeautifulSoup's "xml" feature depends on an
    optional lxml install not related to this fix."""
    captured = {}

    class _FakeSitemapResponse:
        def __init__(self):
            self.status = 200

        async def text(self):
            return "<urlset></urlset>"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _CapturingSession:
        def get(self, url, **kwargs):
            captured["proxy"] = kwargs.get("proxy")
            return _FakeSitemapResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    crawler = ContentCrawler(proxy="http://proxy.example.internal:8080")
    with patch("aiohttp.ClientSession", return_value=_CapturingSession()):
        await crawler._parse_sitemap("https://example.com/sitemap.xml")

    assert captured["proxy"] == "http://proxy.example.internal:8080"
