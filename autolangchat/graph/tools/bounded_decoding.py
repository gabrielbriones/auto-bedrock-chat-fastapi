"""Bounded-output HTTP content decoding for tool responses.

``httpx`` decompresses each network chunk in one call with no output limit, so a
tiny compressed body (especially with stacked ``Content-Encoding`` layers) can
allocate far more memory than any byte cap applied afterwards. The helpers here
decode ``gzip`` / ``deflate`` incrementally, producing at most
``DECODE_STEP_BYTES`` per ``zlib`` call, so the caller can stop reading as soon
as its cap is reached.
"""

import zlib
from typing import AsyncIterator, Iterator, List, Sequence

import httpx

from ...exceptions import ToolError

DECODE_STEP_BYTES = 1024 * 1024

# Advertised on requests so compliant servers only use encodings decoded here.
ACCEPT_ENCODING = "gzip, deflate"


class _ZlibLayer:
    """One ``gzip`` or ``deflate`` layer that yields decoded output in bounded steps."""

    def __init__(self, *, deflate: bool):
        self._deflate = deflate
        self._first_attempt = deflate
        self._decompressor = zlib.decompressobj() if deflate else zlib.decompressobj(zlib.MAX_WBITS | 16)

    def _decompress(self, data: bytes) -> bytes:
        was_first_attempt = self._first_attempt
        self._first_attempt = False
        try:
            return self._decompressor.decompress(data, DECODE_STEP_BYTES)
        except zlib.error as exc:
            if was_first_attempt:
                # Some servers send raw deflate without the zlib header.
                self._decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
                return self._decompress(data)
            raise httpx.DecodingError(str(exc)) from exc

    def feed(self, data: bytes) -> Iterator[bytes]:
        out = self._decompress(data)
        while True:
            if out:
                yield out
            tail = self._decompressor.unconsumed_tail
            if tail:
                out = self._decompress(tail)
            elif len(out) == DECODE_STEP_BYTES:
                # The step limit may have left output pending inside zlib.
                out = self._decompress(b"")
            else:
                return


def build_decoders(content_encodings: Sequence[str]) -> List[_ZlibLayer]:
    """Return the decode layers for a ``Content-Encoding`` header, outermost first.

    Raises:
        ToolError: For an encoding that cannot be decoded with a bounded output.
    """
    layers: List[_ZlibLayer] = []
    for token in content_encodings:
        name = token.strip().lower()
        if name in ("", "identity"):
            continue
        if name in ("gzip", "x-gzip"):
            layers.append(_ZlibLayer(deflate=False))
        elif name == "deflate":
            layers.append(_ZlibLayer(deflate=True))
        else:
            raise ToolError(f"Unsupported Content-Encoding in tool response: {token.strip()!r}")
    layers.reverse()
    return layers


def decode_chunk(layers: Sequence[_ZlibLayer], data: bytes) -> Iterator[bytes]:
    """Lazily decode one raw network chunk through every layer.

    Each piece is at most ``DECODE_STEP_BYTES``. Stopping iteration stops all
    further decompression, so memory stays bounded by the layer count.
    """
    if not layers:
        if data:
            yield data
        return
    head, rest = layers[0], layers[1:]
    for piece in head.feed(data):
        yield from decode_chunk(rest, piece)


async def iter_decoded_chunks(response: httpx.Response) -> AsyncIterator[bytes]:
    """Yield the decoded body of a streamed response in bounded pieces.

    A response that was already read into memory (for example one produced by an
    in-memory test transport) is yielded as-is: httpx has decoded it already and
    its raw stream can no longer be iterated.

    Raises:
        ToolError: For a ``Content-Encoding`` that cannot be decoded with a bounded output.
        httpx.DecodingError: For a corrupt compressed body.
    """
    try:
        preloaded = response.content
    except httpx.ResponseNotRead:
        preloaded = None
    if preloaded is not None:
        yield preloaded
        return

    layers = build_decoders(response.headers.get_list("content-encoding", split_commas=True))
    async for raw_chunk in response.aiter_raw():
        for chunk in decode_chunk(layers, raw_chunk):
            yield chunk
