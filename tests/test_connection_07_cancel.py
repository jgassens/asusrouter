"""Tests for caller cancellation and session recovery during login."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from asusrouter.modules.endpoint import EndpointService
from tests.helpers import (
    AsyncPatch,
    ConnectionFactory,
    SyncPatch,
    mock_response,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "connected"),
    [("query", True), ("query", False), ("connect", False)],
    ids=["query_request", "query_login", "connect"],
)
async def test_caller_cancellation_propagates(
    operation: str,
    connected: bool,
    connection_factory: ConnectionFactory,
    make_request: AsyncPatch,
) -> None:
    """Cancelling a caller must cancel it while preserving a shared login."""

    connection = connection_factory(session=Mock(closed=False))
    connection._connected = connected
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_request(*args: object) -> tuple[int, dict, str]:
        started.set()
        await release.wait()
        return 200, {}, '{"asus_token": "login-token"}'

    make_request(connection, side_effect=blocked_request)
    request = (
        connection.async_query(EndpointService.LOGIN)
        if operation == "query"
        else connection.async_connect()
    )
    caller = asyncio.create_task(request)
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        login_task = connection._connect_task
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert caller.cancelled()

        if login_task is not None:
            assert not login_task.done()
            assert login_task.cancelling() == 0
            release.set()
            assert await connection.async_connect() is True
            assert connection._token == "login-token"
    finally:
        release.set()
        await asyncio.gather(caller, return_exceptions=True)
        if connection._connect_task is not None:
            await asyncio.gather(
                connection._connect_task, return_exceptions=True
            )


@pytest.mark.asyncio
async def test_cancelled_login_returns_false_to_all_waiters(
    connection_factory: ConnectionFactory,
    make_request: AsyncPatch,
) -> None:
    """An inner login cancellation is a failed connection for every waiter."""

    connection = connection_factory(session=Mock(closed=False))
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_request(*args: object) -> tuple[int, dict, str]:
        started.set()
        await release.wait()
        return 200, {}, '{"asus_token": "login-token"}'

    make_request(connection, side_effect=blocked_request)
    callers = [
        asyncio.create_task(connection.async_connect()) for _ in range(2)
    ]
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        login_task = connection._connect_task
        assert login_task is not None
        login_task.cancel()
        assert await asyncio.gather(*callers) == [False, False]
        assert login_task.cancelled()
        assert not connection.connected
        assert all(caller.cancelling() == 0 for caller in callers)
    finally:
        release.set()
        await asyncio.gather(*callers, return_exceptions=True)


@pytest.mark.asyncio
async def test_closed_session_during_login_completes_before_timeout(
    connection_factory: ConnectionFactory,
    new_session: SyncPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A login recreates its session without waiting for its own timeout."""

    connection = connection_factory(session=Mock(closed=True), timeout=0.05)
    response = mock_response(200, {}, '{"asus_token": "recovered-token"}')
    context = AsyncMock()
    context.__aenter__.return_value = response
    session = MagicMock(closed=False)
    session.request.return_value = context
    create_session = new_session(connection, return_value=session)

    loop = asyncio.get_running_loop()
    start = loop.time()
    clock = Mock(return_value=start)
    monkeypatch.setattr(loop, "time", clock)
    caller = asyncio.create_task(connection.async_connect())
    try:
        # Advance a virtual clock within a 100 ms budget, without sleeping.
        for _ in range(100):
            if caller.done():
                break
            clock.return_value += 0.001
            await asyncio.sleep(0)

        assert caller.done(), "Login exceeded the virtual time budget"
        assert caller.result() is True
        assert clock.return_value - start < connection._timeout
        assert connection.connected
        assert connection._token == "recovered-token"
        create_session.assert_called_once_with()
        session.request.assert_called_once()
        assert session.request.call_args.args[1].endswith("/login.cgi")
    finally:
        caller.cancel()
        if connection._connect_task is not None:
            connection._connect_task.cancel()
            await asyncio.gather(
                connection._connect_task, return_exceptions=True
            )
        await asyncio.gather(caller, return_exceptions=True)
