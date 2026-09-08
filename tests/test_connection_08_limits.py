"""Tests for redirect and response-size request limits."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import aiohttp
import pytest

from asusrouter.connection import MAX_RESPONSE_SIZE
from asusrouter.error import AsusRouterDataError
from asusrouter.modules.endpoint import Endpoint
from tests.helpers import ConnectionFactory


class ChunkedContent:
    """A response stream that records how many chunks were consumed."""

    def __init__(self, chunks: list[bytes]) -> None:
        """Initialize a chunk stream."""

        self.chunks = chunks
        self.chunks_yielded = 0
        self.iter_chunked_calls = 0

    def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        """Yield response chunks asynchronously."""

        self.iter_chunked_calls += 1

        async def _iterate() -> AsyncIterator[bytes]:
            for chunk in self.chunks:
                self.chunks_yielded += 1
                yield chunk

        return _iterate()


def _mock_session(
    status: int,
    headers: dict[str, str],
    chunks: list[bytes],
) -> tuple[MagicMock, ChunkedContent]:
    """Create a session returning a streamed mock response."""

    content = ChunkedContent(chunks)
    response = MagicMock()
    response.status = status
    response.headers = headers
    response.content = content
    response.charset = "utf-8"
    context = MagicMock()
    context.__aenter__ = MagicMock()
    context.__aexit__ = MagicMock()

    async def _enter() -> MagicMock:
        return response

    async def _exit(*_args: object) -> None:
        return None

    context.__aenter__.side_effect = _enter
    context.__aexit__.side_effect = _exit
    session = MagicMock(spec=aiohttp.ClientSession)
    session.closed = False
    session.request.return_value = context
    return session, content


@pytest.mark.parametrize("status", [302, 307])
async def test_redirect_is_rejected_without_followup_request(
    connection_factory: ConnectionFactory,
    status: int,
) -> None:
    """Redirects fail before their body is read or another request is sent."""

    location = "http://169.254.169.254/latest/meta-data/secret"
    session, content = _mock_session(
        status,
        {"Location": location},
        [b"sensitive redirect body"],
    )
    connection = connection_factory(session=session)

    with pytest.raises(AsusRouterDataError) as exc_info:
        await connection._make_request(Endpoint.DEVICEMAP)

    assert Endpoint.DEVICEMAP in str(exc_info.value)
    assert "/latest/meta-data/secret" not in str(exc_info.value)
    assert session.request.call_count == 1
    assert session.request.call_args.kwargs["allow_redirects"] is False
    assert content.iter_chunked_calls == 0


async def test_content_length_over_cap_is_rejected_before_reading(
    connection_factory: ConnectionFactory,
) -> None:
    """An oversized declared body fails before stream consumption."""

    session, content = _mock_session(
        200,
        {"Content-Length": str(MAX_RESPONSE_SIZE + 1)},
        [b"unread"],
    )
    connection = connection_factory(session=session)

    with pytest.raises(AsusRouterDataError, match=Endpoint.DEVICEMAP):
        await connection._make_request(Endpoint.DEVICEMAP)

    assert content.iter_chunked_calls == 0


async def test_decompressed_body_over_cap_stops_stream_early(
    connection_factory: ConnectionFactory,
) -> None:
    """The running cap counts auto-decompressed bytes and aborts early."""

    half_cap = b"a" * (MAX_RESPONSE_SIZE // 2)
    chunks = [half_cap, half_cap, b"overflow", b"must remain unread"]
    session, content = _mock_session(
        200,
        {
            "Content-Encoding": "gzip",
            "Content-Length": "1024",
        },
        chunks,
    )
    connection = connection_factory(session=session)

    with pytest.raises(AsusRouterDataError, match=Endpoint.UPDATE_CLIENTS):
        await connection._make_request(Endpoint.UPDATE_CLIENTS)

    assert content.chunks_yielded == 3
    assert content.chunks_yielded < len(chunks)


async def test_body_just_under_cap_passes_unchanged(
    connection_factory: ConnectionFactory,
) -> None:
    """A valid body immediately under the cap is returned unchanged."""

    body = "a" * (MAX_RESPONSE_SIZE - 1)
    session, content = _mock_session(
        200,
        {"Content-Length": str(len(body))},
        [body[:123].encode(), body[123:].encode()],
    )
    connection = connection_factory(session=session)

    status, headers, result = await connection._make_request(
        Endpoint.UPDATE_CLIENTS
    )

    assert status == 200
    assert headers["Content-Length"] == str(len(body))
    assert result == body
    assert content.chunks_yielded == 2
