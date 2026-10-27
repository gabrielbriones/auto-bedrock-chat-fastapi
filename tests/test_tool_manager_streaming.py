"""Streaming, size-capped HTTP ingestion in ``ToolManager._execute_single_tool_call``.

The HTTP layer is an ``httpx.MockTransport`` fed by async byte generators, so
the cap is asserted by counting how many bytes were actually pulled from the
stream, never by timing.
"""

import asyncio
import gzip
import json
import tracemalloc
import zlib
from typing import AsyncIterator
from unittest.mock import patch

import httpx
import pytest

from autolangchat.config import ChatConfig
from autolangchat.exceptions import ToolError, ToolHTTPError
from autolangchat.graph.tools.generator import ToolsGenerator
from autolangchat.graph.tools.manager import ToolManager
from tests.http_stubs import stub_http, stub_http_stream

_CAP = 10_000
_CHUNK = 1024
_JOBS_TOOL = {"method": "GET", "path": "/jobs"}

_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/jobs": {"get": {"operationId": "list_jobs", "summary": "List all jobs", "responses": {"200": {}}}},
    },
}


def _make_manager(cap: int = _CAP) -> ToolManager:
    config = ChatConfig(model_id="test-model", excluded_paths=[], AUTOCHAT_TOOL_RESPONSE_MAX_BYTES=cap)
    generator = ToolsGenerator(openapi_spec=_SPEC, config=config)
    return ToolManager(generated_tools=generator._generated_tools, config=config, base_url="http://test-api")


class _EndlessBody(httpx.AsyncByteStream):
    """Endless byte stream that records pulled bytes and whether it was closed."""

    def __init__(self, chunk: int = _CHUNK):
        self.chunk = chunk
        self.pulled = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            self.pulled += self.chunk
            yield b"x" * self.chunk

    async def aclose(self) -> None:
        self.closed = True


class _StalledBody(httpx.AsyncByteStream):
    """Sends one byte, then stalls forever; records whether it was closed."""

    def __init__(self):
        self.started = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"x"
        self.started.set()
        await asyncio.sleep(60)

    async def aclose(self) -> None:
        self.closed = True


async def _fetch(manager: ToolManager):
    return await manager._execute_single_tool_call(_JOBS_TOOL, {})


class TestCap:
    @pytest.mark.asyncio
    async def test_endless_body_is_cut_off_at_the_cap(self):
        manager = _make_manager()
        body = _EndlessBody()
        stub_http_stream(manager, body, **{"content-type": "text/plain"})

        result = await _fetch(manager)

        assert result["truncated"] is True
        assert result["bytes_read"] == _CAP
        assert result["limit_bytes"] == _CAP
        assert result["content_type"] == "text/plain"
        assert result["text"] == "x" * _CAP
        assert _CAP <= body.pulled <= _CAP + _CHUNK

    @pytest.mark.asyncio
    async def test_response_is_closed_when_the_cap_is_hit(self):
        manager = _make_manager()
        body = _EndlessBody()
        stub_http_stream(manager, body)

        await _fetch(manager)

        assert body.closed is True

    @pytest.mark.asyncio
    async def test_response_is_closed_on_cancellation(self):
        manager = _make_manager()
        body = _StalledBody()
        stub_http_stream(manager, body)
        task = asyncio.ensure_future(_fetch(manager))
        await asyncio.wait_for(body.started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert body.closed is True

    @pytest.mark.asyncio
    async def test_body_of_exactly_the_cap_is_not_truncated(self):
        manager = _make_manager()
        payload = {"data": "a" * (_CAP - len('{"data": ""}'))}
        raw = json.dumps(payload).encode()
        assert len(raw) == _CAP
        stub_http_stream(manager, _once(raw))

        assert await _fetch(manager) == payload

    @pytest.mark.asyncio
    async def test_body_one_byte_over_the_cap_is_truncated(self):
        manager = _make_manager()
        stub_http_stream(manager, _once(b"y" * (_CAP + 1)))

        result = await _fetch(manager)

        assert result["truncated"] is True
        assert result["bytes_read"] == _CAP

    @pytest.mark.asyncio
    async def test_compressed_body_is_capped_on_decoded_size(self):
        manager = _make_manager()
        stub_http_stream(manager, _once(gzip.compress(b"z" * 100_000)), **{"content-encoding": "gzip"})

        result = await _fetch(manager)

        assert result["truncated"] is True
        assert result["text"] == "z" * _CAP

    @pytest.mark.asyncio
    async def test_over_cap_body_is_never_json_parsed(self):
        manager = _make_manager()
        stub_http_stream(manager, _once(b'{"a": "' + b"b" * _CAP + b'"}'), **{"content-type": "application/json"})

        with patch("autolangchat.graph.tools.manager.json.loads") as loads:
            result = await _fetch(manager)

        loads.assert_not_called()
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_truncated_result_is_json_serializable(self):
        manager = _make_manager()
        stub_http_stream(manager, _once(b"y" * (_CAP * 2)))

        result = await _fetch(manager)

        assert json.loads(json.dumps(result))["truncated"] is True


class TestErrorBodies:
    @pytest.mark.asyncio
    async def test_huge_error_body_is_read_in_a_bounded_way(self):
        manager = _make_manager(cap=1_000_000)
        body = _EndlessBody()
        stub_http_stream(manager, body, status_code=500)

        with pytest.raises(ToolHTTPError) as exc_info:
            await _fetch(manager)

        assert exc_info.value.status_code == 500
        assert exc_info.value.details == "x" * 500
        assert body.pulled <= 4096 + _CHUNK
        assert body.closed is True

    @pytest.mark.asyncio
    async def test_error_body_never_exceeds_a_smaller_configured_cap(self):
        manager = _make_manager(cap=100)
        stub_http_stream(manager, _once(b"e" * 5000), status_code=500)

        with pytest.raises(ToolHTTPError) as exc_info:
            await _fetch(manager)

        assert exc_info.value.details == "e" * 100


class TestLegacyShape:
    @pytest.mark.asyncio
    async def test_json_dict_and_list_are_parsed(self):
        manager = _make_manager()
        stub_http(manager, (200, {"jobs": [1, 2]}), (200, [1, 2, 3]))

        assert await _fetch(manager) == {"jobs": [1, 2]}
        assert await _fetch(manager) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_non_json_body_falls_back_to_text(self):
        manager = _make_manager()
        stub_http(manager, (200, "plain log line"))

        assert await _fetch(manager) == "plain log line"

    @pytest.mark.asyncio
    async def test_empty_body_falls_back_to_empty_text(self):
        manager = _make_manager()
        stub_http(manager, (204, ""))

        assert await _fetch(manager) == ""

    @pytest.mark.asyncio
    async def test_text_is_decoded_with_the_declared_charset(self):
        manager = _make_manager()
        stub_http_stream(manager, _once("café".encode("latin-1")), **{"content-type": "text/plain; charset=latin-1"})

        assert await _fetch(manager) == "café"

    @pytest.mark.asyncio
    async def test_execute_tool_calls_keeps_result_and_error_shapes(self):
        manager = _make_manager()
        stub_http(manager, (200, {"jobs": []}), (401, "Unauthorized"))

        # Run sequentially so each call maps deterministically to one canned response.
        ok = (await manager.execute_tool_calls([{"id": "c1", "name": "list_jobs", "arguments": {}}]))[0]
        unauthorized = (await manager.execute_tool_calls([{"id": "c2", "name": "list_jobs", "arguments": {}}]))[0]

        assert ok["result"] == {"jobs": []}
        assert "status_code" not in ok
        assert unauthorized["status_code"] == 401
        assert unauthorized["result"] == {"error": "HTTP 401", "details": "Unauthorized"}

    @pytest.mark.asyncio
    async def test_execute_tool_calls_returns_the_flagged_truncated_result(self):
        manager = _make_manager()
        stub_http_stream(manager, _once(b"y" * (_CAP * 2)))

        results = await manager.execute_tool_calls([{"id": "c1", "name": "list_jobs", "arguments": {}}])

        assert results[0]["result"]["truncated"] is True
        assert "error" not in results[0]


def _compress(data_chunks, wbits: int) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, wbits)
    return b"".join(compressor.compress(chunk) for chunk in data_chunks) + compressor.flush()


def _zeros(total_mib: int):
    return (b"\0" * (1024 * 1024) for _ in range(total_mib))


async def _tiny_chunks(data: bytes, size: int = 7) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


class TestBoundedDecompression:
    @pytest.mark.asyncio
    async def test_stacked_gzip_bomb_does_not_allocate_beyond_the_cap(self):
        manager = _make_manager(cap=1024)
        gzip_wbits = zlib.MAX_WBITS | 16
        bomb = _compress([_compress(_zeros(128), gzip_wbits)], gzip_wbits)
        assert len(bomb) < 2048
        stub_http_stream(manager, _once(bomb), **{"content-encoding": "gzip, gzip"})

        tracemalloc.start()
        try:
            result = await _fetch(manager)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert result["truncated"] is True
        assert result["bytes_read"] == 1024
        assert peak < 16 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_single_layer_gzip_bomb_does_not_allocate_beyond_the_cap(self):
        manager = _make_manager(cap=1024)
        bomb = _compress(_zeros(128), zlib.MAX_WBITS | 16)
        stub_http_stream(manager, _once(bomb), **{"content-encoding": "gzip"})

        tracemalloc.start()
        try:
            result = await _fetch(manager)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert result["truncated"] is True
        assert peak < 16 * 1024 * 1024

    @pytest.mark.asyncio
    @pytest.mark.parametrize("wbits", [zlib.MAX_WBITS, -zlib.MAX_WBITS], ids=["zlib-wrapped", "raw-deflate"])
    async def test_deflate_decodes_with_and_without_the_zlib_header(self, wbits):
        manager = _make_manager(cap=10 * 1024 * 1024)
        stub_http_stream(manager, _once(_compress([b"hello deflate"], wbits)), **{"content-encoding": "deflate"})

        assert await _fetch(manager) == "hello deflate"

    @pytest.mark.asyncio
    async def test_output_larger_than_one_decode_step_is_fully_decoded(self):
        manager = _make_manager(cap=10 * 1024 * 1024)
        text = "log line\n" * 350_000  # about 3 MiB, several decode steps from one tiny chunk
        stub_http_stream(manager, _once(gzip.compress(text.encode())), **{"content-encoding": "gzip"})

        assert await _fetch(manager) == text

    @pytest.mark.asyncio
    async def test_body_split_into_tiny_network_chunks_is_decoded(self):
        manager = _make_manager()
        stub_http_stream(
            manager, _tiny_chunks(gzip.compress(json.dumps({"ok": [1, 2, 3]}).encode())), **{"content-encoding": "gzip"}
        )

        assert await _fetch(manager) == {"ok": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_stacked_encodings_are_decoded_in_reverse_order(self):
        manager = _make_manager()
        payload = _compress([_compress([b"stacked body"], zlib.MAX_WBITS)], zlib.MAX_WBITS | 16)
        stub_http_stream(manager, _once(payload), **{"content-encoding": "deflate, gzip"})

        assert await _fetch(manager) == "stacked body"

    @pytest.mark.asyncio
    async def test_identity_encoding_is_passed_through(self):
        manager = _make_manager()
        stub_http_stream(manager, _once(b"plain"), **{"content-encoding": "identity"})

        assert await _fetch(manager) == "plain"

    @pytest.mark.asyncio
    async def test_unsupported_encoding_is_rejected_and_the_response_closed(self):
        manager = _make_manager()
        body = _EndlessBody()
        stub_http_stream(manager, body, **{"content-encoding": "br"})

        with pytest.raises(ToolError, match="Unsupported Content-Encoding"):
            await _fetch(manager)

        assert body.pulled == 0
        assert body.closed is True

    @pytest.mark.asyncio
    async def test_corrupt_compressed_body_raises_a_decoding_error(self):
        manager = _make_manager()
        stub_http_stream(manager, _once(b"this is not gzip data"), **{"content-encoding": "gzip"})

        with pytest.raises(httpx.DecodingError):
            await _fetch(manager)

    @pytest.mark.asyncio
    async def test_request_only_advertises_decodable_encodings(self):
        manager = _make_manager()
        requests = stub_http(manager, (200, {}))

        await _fetch(manager)

        assert requests[0].headers["accept-encoding"] == "gzip, deflate"


async def _once(data: bytes) -> AsyncIterator[bytes]:
    yield data
