"""Tests for call-local service results."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from asusrouter.asusrouter import AsusRouter
from asusrouter.modules.service import ServiceResult, async_call_service


@pytest.mark.asyncio
async def test_async_run_service_result_returns_call_metadata(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return timing and ID metadata for the service call."""

    command = AsyncMock(
        return_value={
            "run_service": "restart_firewall",
            "modify": "1",
            "restart_needed_time": "12",
            "id": "41",
        }
    )
    monkeypatch.setattr(router, "async_api_command", command)

    result = await router.async_run_service_result(
        "restart_firewall", {"foo": "bar"}
    )

    assert result == ServiceResult(success=True, needed_time=12, last_id=41)
    assert "__bool__" not in ServiceResult.__dict__
    command.assert_awaited_once_with(
        {"rc_service": "restart_firewall", "foo": "bar"}
    )


@pytest.mark.asyncio
async def test_interleaved_service_results_keep_call_local_metadata(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep metadata paired with its call when replies arrive out of order."""

    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()

    async def command(arguments: dict[str, Any]) -> dict[str, Any]:
        service = arguments["rc_service"]
        if service == "restart_firewall":
            first_started.set()
            await release_first.wait()
            return {
                "run_service": service,
                "modify": "1",
                "restart_needed_time": "31",
                "id": "101",
            }

        second_started.set()
        await release_second.wait()
        return {
            "run_service": service,
            "modify": "1",
            "restart_needed_time": "47",
            "id": "202",
        }

    monkeypatch.setattr(router, "async_api_command", command)

    first_task = asyncio.create_task(
        router.async_run_service_result("restart_firewall")
    )
    await first_started.wait()
    second_task = asyncio.create_task(
        router.async_run_service_result("restart_wireless")
    )
    await second_started.wait()

    release_second.set()
    second_result = await second_task
    release_first.set()
    first_result = await first_task

    assert first_result == ServiceResult(True, 31, 101)
    assert second_result == ServiceResult(True, 47, 202)


@pytest.mark.asyncio
async def test_result_method_preserves_legacy_fields_and_wrapper_sets_them(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the compatibility wrapper writes the legacy metadata fields."""

    router._needed_time = 77
    router._last_id = 88
    command = AsyncMock(
        side_effect=[
            {
                "run_service": "restart_firewall",
                "modify": "1",
                "restart_needed_time": "12",
                "id": "41",
            },
            {
                "run_service": "restart_firewall",
                "modify": "1",
                "restart_needed_time": "19",
                "id": "42",
            },
        ]
    )
    monkeypatch.setattr(router, "async_api_command", command)

    result = await router.async_run_service_result("restart_firewall")

    assert result == ServiceResult(True, 12, 41)
    assert router._needed_time == 77
    assert router._last_id == 88

    wrapper_result = await router.async_run_service("restart_firewall")

    assert wrapper_result is True
    assert router._needed_time == 19
    assert router._last_id == 42


@pytest.mark.asyncio
async def test_async_run_service_returns_bare_bools(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserve exact boolean results from the compatibility method."""

    command = AsyncMock(
        side_effect=[
            {"run_service": "restart_firewall", "modify": "1"},
            {"run_service": "restart_firewall", "modify": "0"},
        ]
    )
    monkeypatch.setattr(router, "async_api_command", command)

    successful = await router.async_run_service("restart_firewall")
    failed = await router.async_run_service("restart_firewall")

    assert successful is True
    assert failed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name", ["async_run_service_result", "async_run_service"]
)
async def test_service_methods_submit_one_unchanged_wire_request_when_dropping(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
) -> None:
    """Send one request with legacy arguments even when dropping afterward."""

    command = AsyncMock(
        return_value={"run_service": "restart_firewall", "modify": "1"}
    )
    drop_connection = AsyncMock()
    monkeypatch.setattr(router, "async_api_command", command)
    monkeypatch.setattr(router, "_async_drop_connection", drop_connection)

    method = getattr(router, method_name)
    await method(
        "restart_firewall",
        {"foo": "bar"},
        apply=True,
        expect_modify=False,
        drop_connection=True,
    )

    command.assert_awaited_once_with(
        {
            "rc_service": "restart_firewall",
            "foo": "bar",
            "action_mode": "apply",
        }
    )
    drop_connection.assert_awaited_once_with()


_MISSING = object()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_time", "last_id", "expected_time"),
    [
        pytest.param(_MISSING, "4", 5, id="missing-with-id"),
        pytest.param(_MISSING, None, None, id="missing-without-id"),
        pytest.param(True, None, None, id="boolean"),
        pytest.param(-3, None, None, id="negative"),
        pytest.param(-0.5, None, None, id="negative-fraction"),
        pytest.param(float("inf"), None, None, id="infinity"),
        pytest.param(float("nan"), None, None, id="nan"),
        pytest.param("abc", None, None, id="invalid-string"),
        pytest.param("1e309", None, None, id="overflow"),
        pytest.param(12, None, 12, id="positive-integer"),
    ],
)
async def test_service_timing_conversion(
    raw_time: object,
    last_id: str | None,
    expected_time: int | None,
) -> None:
    """Accept valid delays and reject unsafe router timing values."""

    async def command(arguments: dict[str, Any]) -> dict[str, Any]:
        response: dict[str, Any] = {
            "run_service": arguments["rc_service"],
            "modify": "1",
        }
        if raw_time is not _MISSING:
            response["restart_needed_time"] = raw_time
        if last_id is not None:
            response["id"] = last_id
        return response

    success, needed_time, parsed_last_id = await async_call_service(
        command, "restart_firewall"
    )

    assert success is True
    assert needed_time == expected_time
    assert parsed_last_id == (int(last_id) if last_id is not None else None)
