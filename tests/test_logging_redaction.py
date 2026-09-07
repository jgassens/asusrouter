"""Regression tests for redacting raw router data from logs."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime
import json
import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from asusrouter.asusrouter import AsusRouter
from asusrouter.config import ARConfig, ARConfigKey as ARConfKey
from asusrouter.const import RequestType
from asusrouter.error import AsusRouterDataError, AsusRouterServiceError
from asusrouter.modules import system as system_module
from asusrouter.modules.data import AsusData, AsusDataState
from asusrouter.modules.endpoint import Endpoint, EndpointControl
from asusrouter.modules.service import async_call_service
from asusrouter.modules.system import AsusSystem
from asusrouter.tools.readers import read_json_content
from asusrouter.tools.security import ARSecurityLevel

SECURE_LOGGING_POLICIES = (
    ARSecurityLevel.DEFAULT,
    ARSecurityLevel.STRICT,
    ARSecurityLevel.SANITIZED,
)


@pytest.fixture(params=SECURE_LOGGING_POLICIES, ids=lambda level: level.name)
def secure_logging_policy(
    request: pytest.FixtureRequest,
) -> Iterator[ARSecurityLevel]:
    """Select a safe logging policy and restore the global setting."""

    previous = ARConfig.get(ARConfKey.DEBUG_PAYLOAD)
    level = cast(ARSecurityLevel, request.param)
    ARConfig.set(ARConfKey.DEBUG_PAYLOAD, level)
    yield level
    ARConfig.set(ARConfKey.DEBUG_PAYLOAD, previous)


def _log_text(caplog: pytest.LogCaptureFixture) -> str:
    """Return all captured, rendered log messages."""

    return "\n".join(record.getMessage() for record in caplog.records)


def _assert_redacted(
    caplog: pytest.LogCaptureFixture, *sentinels: str
) -> None:
    """Assert none of the sentinel values appear in any log record."""

    logged = _log_text(caplog)
    for sentinel in sentinels:
        assert sentinel not in logged


@pytest.mark.asyncio
async def test_service_log_redacts_arguments_and_response(
    caplog: pytest.LogCaptureFixture,
    secure_logging_policy: ARSecurityLevel,
) -> None:
    """Keep service values out of logs without changing submitted commands."""

    del secure_logging_policy
    caplog.set_level(logging.DEBUG)
    argument_secret = "S3NT1NEL-SERVICE-PSK"
    radius_secret = "S3NT1NEL-SERVICE-RADIUS"
    response_secret = "S3NT1NEL-SERVICE-RESULT"
    arguments = {
        "wl0_wpa_psk": argument_secret,
        "wl0_radius_key": radius_secret,
    }
    callback = AsyncMock(
        return_value={
            "run_service": "restart_wireless",
            "modify": "1",
            "router_echo": response_secret,
        }
    )

    result = await async_call_service(callback, "restart_wireless", arguments)

    assert result == (True, None, None)
    callback.assert_awaited_once_with(
        {
            "rc_service": "restart_wireless",
            "wl0_wpa_psk": argument_secret,
            "wl0_radius_key": radius_secret,
        }
    )
    _assert_redacted(caplog, argument_secret, radius_secret, response_secret)
    assert "restart_wireless" in _log_text(caplog)
    assert "Success" in _log_text(caplog)


@pytest.mark.asyncio
async def test_api_command_logs_only_endpoint_and_field_count(
    router: AsusRouter,
    caplog: pytest.LogCaptureFixture,
    secure_logging_policy: ARSecurityLevel,
) -> None:
    """Preserve the command wire payload and returned data while redacting."""

    del secure_logging_policy
    caplog.set_level(logging.DEBUG)
    command_secret = "S3NT1NEL-COMMAND-PSK"
    device_secret = "S3NT1NEL-COMMAND-DEVICE"
    commands = {
        "wl0_wpa_psk": command_secret,
        "custom_client_name": device_secret,
    }
    response = {"echo": command_secret, "device": device_secret}
    transport = AsyncMock(return_value=(200, {}, json.dumps(response)))
    router._connection = cast(Any, SimpleNamespace(async_query=transport))

    result = await router.async_api_command(commands)

    payload = str(commands)
    transport.assert_awaited_once_with(
        EndpointControl.COMMAND,
        payload,
        request_type=RequestType.POST,
    )
    assert command_secret in payload
    assert device_secret in payload
    assert result == response
    _assert_redacted(caplog, command_secret, device_secret)
    logged = _log_text(caplog)
    assert "async_api_command" in logged
    assert EndpointControl.COMMAND in logged


@pytest.mark.asyncio
async def test_api_hook_logs_length_without_changing_request(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    secure_logging_policy: ARSecurityLevel,
) -> None:
    """Pass a hook request and response unchanged without logging values."""

    del secure_logging_policy
    caplog.set_level(logging.DEBUG)
    request_secret = "S3NT1NEL-HOOK-REQUEST"
    response = {"device_name": request_secret}
    loader = AsyncMock(return_value=response)
    monkeypatch.setattr(router, "async_api_load", loader)

    result = await router.async_api_hook(request_secret)

    loader.assert_awaited_once_with(
        endpoint=Endpoint.HOOK,
        request=f"hook={request_secret}",
    )
    assert result is response
    _assert_redacted(caplog, request_secret)
    logged = _log_text(caplog)
    assert "async_api_hook" in logged
    assert Endpoint.HOOK in logged


@pytest.mark.asyncio
async def test_cached_wlan_log_redacts_data_and_preserves_return(
    router: AsusRouter,
    caplog: pytest.LogCaptureFixture,
    secure_logging_policy: ARSecurityLevel,
) -> None:
    """Return cached WLAN data unchanged without rendering its values."""

    del secure_logging_policy
    caplog.set_level(logging.DEBUG)
    psk_secret = "S3NT1NEL-CACHED-PSK"
    radius_secret = "S3NT1NEL-CACHED-RADIUS"
    device_secret = "S3NT1NEL-CACHED-DEVICE"
    cached_wlan = {
        "2ghz": {
            "wpa_psk": psk_secret,
            "radius_key": radius_secret,
            "ssid": device_secret,
        }
    }
    router._state[AsusData.WLAN] = AsusDataState(
        data=cached_wlan,
        timestamp=datetime.now(UTC),
    )

    result = await router.async_get_data(AsusData.WLAN)

    assert result is cached_wlan
    assert result["2ghz"]["wpa_psk"] == psk_secret
    assert result["2ghz"]["radius_key"] == radius_secret
    assert result["2ghz"]["ssid"] == device_secret
    _assert_redacted(caplog, psk_secret, radius_secret, device_secret)
    logged = _log_text(caplog)
    assert "cached data" in logged
    assert AsusData.WLAN in logged


@pytest.mark.asyncio
async def test_set_state_log_redacts_kwargs_sent_to_transport(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    secure_logging_policy: ARSecurityLevel,
) -> None:
    """Keep state kwargs unchanged on the service call but out of logs."""

    del secure_logging_policy
    caplog.set_level(logging.DEBUG)
    mac_secret = "DE:AD:BE:EF:CA:FE"
    device_secret = "S3NT1NEL-STATE-DEVICE"
    response_secret = "S3NT1NEL-STATE-RESULT"
    monkeypatch.setattr(
        system_module, "STATE_MAP", deepcopy(system_module.STATE_MAP)
    )
    transport = AsyncMock(
        return_value={"modify": "1", "router_echo": response_secret}
    )
    monkeypatch.setattr(router, "async_api_command", transport)

    result = await router.async_set_state(
        AsusSystem.NODE_CONFIG_CHANGE,
        re_mac=mac_secret,
        config=device_secret,
    )

    assert result is True
    transport.assert_awaited_once_with(
        {
            "action_mode": AsusSystem.NODE_CONFIG_CHANGE.value,
            "re_mac": mac_secret,
            "config": device_secret,
        }
    )
    _assert_redacted(caplog, mac_secret, device_secret, response_secret)
    logged = _log_text(caplog)
    assert "Setting state" in logged
    assert AsusData.SYSTEM in logged


@pytest.mark.asyncio
async def test_service_mismatch_exception_omits_response_data(
    caplog: pytest.LogCaptureFixture,
    secure_logging_policy: ARSecurityLevel,
) -> None:
    """Report requested and returned services without the response dict."""

    del secure_logging_policy
    caplog.set_level(logging.DEBUG)
    response_secret = "S3NT1NEL-MISMATCH-RESPONSE"
    callback = AsyncMock(
        return_value={
            "run_service": "restart_wireless",
            "wpa_psk": response_secret,
        }
    )

    with pytest.raises(AsusRouterServiceError) as exc_info:
        await async_call_service(callback, "restart_firewall")

    exception_text = str(exc_info.value)
    assert "restart_firewall" in exception_text
    assert "restart_wireless" in exception_text
    assert response_secret not in exception_text
    callback.assert_awaited_once_with({"rc_service": "restart_firewall"})
    _assert_redacted(caplog, response_secret)


def test_read_json_content_logs_length_without_body(
    caplog: pytest.LogCaptureFixture,
    secure_logging_policy: ARSecurityLevel,
) -> None:
    """Log malformed JSON length and failure category without its body."""

    del secure_logging_policy
    caplog.set_level(logging.DEBUG)
    body_secret = "S3NT1NEL-MALFORMED-JSON"
    body = f'{{"wpa_psk": "{body_secret}", invalid}}'

    assert read_json_content(body) == {}

    _assert_redacted(caplog, body_secret)
    error_records = [
        record for record in caplog.records if record.levelno == logging.ERROR
    ]
    assert error_records
    error_text = "\n".join(record.getMessage() for record in error_records)
    assert "read_json_content" in error_text
    assert "JSONDecodeError" in error_text
    assert f"length {len(body)}" in error_text


@pytest.mark.asyncio
async def test_api_load_decode_error_logs_endpoint_without_body(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    secure_logging_policy: ARSecurityLevel,
) -> None:
    """Retain endpoint and decode category when endpoint parsing fails."""

    del secure_logging_policy
    caplog.set_level(logging.DEBUG)
    body_secret = "S3NT1NEL-ENDPOINT-BODY"
    body = f"malformed response containing {body_secret}"
    query = AsyncMock(return_value=(200, {}, body))
    monkeypatch.setattr(router, "async_api_query", query)
    decode_error = json.JSONDecodeError("invalid response", body, 0)
    monkeypatch.setattr(
        "asusrouter.asusrouter.read", Mock(side_effect=decode_error)
    )

    with pytest.raises(AsusRouterDataError) as exc_info:
        await router.async_api_load(Endpoint.DEVICEMAP)

    assert body_secret not in str(exc_info.value)
    assert query.await_count == 2
    _assert_redacted(caplog, body_secret)
    failure_records = [
        record
        for record in caplog.records
        if "JSONDecodeError" in record.getMessage()
    ]
    assert failure_records
    assert all(
        Endpoint.DEVICEMAP in record.getMessage() for record in failure_records
    )
