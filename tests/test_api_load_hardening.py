"""Regression tests for API-load request hardening."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, Mock

import pytest

from asusrouter.asusrouter import AsusRouter
from asusrouter.error import AsusRouterAccessError, AsusRouterDataError
from asusrouter.modules.data import AsusData, AsusDataState
from asusrouter.modules.data_finder import ASUSDATA_MAP, AsusDataFinder
from asusrouter.modules.endpoint import Endpoint
from asusrouter.modules.endpoint.error import AccessError
from asusrouter.modules.identity import AsusDevice


def _authorization_error() -> AsusRouterAccessError:
    """Return the authorization failure emitted by the request layer."""

    return AsusRouterAccessError(
        "Access error",
        AccessError.AUTHORIZATION,
        {},
    )


async def test_authorization_retry_stops_after_one_relogin(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persistent authorization failure gets one re-login attempt."""

    query = AsyncMock(side_effect=_authorization_error())
    drop_connection = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr(router, "async_api_query", query)
    monkeypatch.setattr(router, "_async_drop_connection", drop_connection)
    monkeypatch.setattr("asusrouter.asusrouter.asyncio.sleep", sleep)

    with pytest.raises(AsusRouterAccessError) as exc_info:
        await router.async_api_load(Endpoint.DEVICEMAP)

    assert exc_info.value.args[1] == AccessError.AUTHORIZATION
    assert query.await_count == 2
    drop_connection.assert_awaited_once_with()
    sleep.assert_awaited_once_with(1)


async def test_authorization_retry_succeeds_after_one_relogin(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient authorization failure succeeds after one re-login."""

    expected = {"clients": {}}
    query = AsyncMock(
        side_effect=[
            _authorization_error(),
            (200, {}, "valid response"),
        ]
    )
    drop_connection = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr(router, "async_api_query", query)
    monkeypatch.setattr(router, "_async_drop_connection", drop_connection)
    monkeypatch.setattr("asusrouter.asusrouter.asyncio.sleep", sleep)
    monkeypatch.setattr(
        "asusrouter.asusrouter.read", Mock(return_value=expected)
    )

    assert await router.async_api_load(Endpoint.DEVICEMAP) == expected
    assert query.await_count == 2
    drop_connection.assert_awaited_once_with()
    sleep.assert_awaited_once_with(1)


async def test_firmware_note_without_endpoints_does_not_warn(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unavailable release-note endpoints are skipped without a warning."""

    router._state[AsusData.FIRMWARE] = AsusDataState(
        data={"state": True, "version": "new"}
    )
    router._identity = AsusDevice(
        endpoints={
            Endpoint.FIRMWARE_NOTE: False,
            Endpoint.FIRMWARE_NOTE_AIMESH: False,
        }
    )
    monkeypatch.setitem(
        ASUSDATA_MAP,
        AsusData.FIRMWARE_NOTE,
        AsusDataFinder(
            [Endpoint.FIRMWARE_NOTE, Endpoint.FIRMWARE_NOTE_AIMESH]
        ),
    )
    fetch = AsyncMock(side_effect=AsusRouterDataError("should not fetch"))
    monkeypatch.setattr(router, "async_get_data", fetch)
    caplog.set_level(logging.DEBUG, logger="asusrouter.asusrouter")

    await router._check_postrequisites(AsusData.FIRMWARE)

    fetch.assert_not_awaited()
    assert not [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    assert "No firmware release note endpoints" in caplog.text
