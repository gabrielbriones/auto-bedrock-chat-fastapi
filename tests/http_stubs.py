"""Shared helper to stub ``ToolManager``'s HTTP layer with ``httpx.MockTransport``.

``ToolManager._execute_single_tool_call`` streams the response through the
real ``httpx`` client, so tests replace the client's transport rather than
mocking individual client methods.
"""

from typing import Any, AsyncIterable, List, Tuple

import httpx

from autolangchat.graph.tools.manager import ToolManager


def stub_http(manager: ToolManager, *responses: Tuple[int, Any]) -> List[httpx.Request]:
    """Route ``manager``'s HTTP calls to canned responses and record the requests.

    Each response is a ``(status_code, body)`` tuple: a ``str`` body is sent as
    text, anything else as JSON. Responses are used in call order and the last
    one repeats once the list is exhausted.

    Returns:
        The list that every outbound ``httpx.Request`` is appended to.
    """
    requests: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status_code, body = responses[min(len(requests), len(responses)) - 1]
        if isinstance(body, str):
            return httpx.Response(status_code, text=body)
        return httpx.Response(status_code, json=body)

    manager._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return requests


def stub_http_stream(manager: ToolManager, chunks: AsyncIterable[bytes], status_code: int = 200, **headers: str):
    """Route ``manager``'s HTTP calls to a single streamed body.

    ``chunks`` is an async byte iterable (or an ``httpx.AsyncByteStream``, whose
    ``aclose`` a test can observe), so a test can emulate an endless or very
    large body and count how much of it was actually pulled.
    """
    requests: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if isinstance(chunks, httpx.AsyncByteStream):
            return httpx.Response(status_code, headers=headers, stream=chunks)
        return httpx.Response(status_code, headers=headers, content=chunks)

    manager._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return requests
