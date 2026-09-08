"""Regression tests for parental-control cache invalidation after writes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from asusrouter.asusrouter import AsusRouter
from asusrouter.error import AsusRouterConnectionError, AsusRouterDataError
from asusrouter.modules.data import AsusData, AsusDataState
from asusrouter.modules.endpoint.hook import process_parental_control
from asusrouter.modules.identity import AsusDevice
from asusrouter.modules.parental_control import (
    HOOK_PC,
    KEY_PC_BLOCK_ALL,
    KEY_PC_MAC,
    KEY_PC_NAME,
    KEY_PC_STATE,
    KEY_PC_TIMEMAP,
    KEY_PC_TYPE,
    AsusBlockAll,
    AsusParentalControl,
)

PC_RESPONSE = {
    KEY_PC_BLOCK_ALL: "0",
    KEY_PC_MAC: "00:00:00:00:00:01",
    KEY_PC_NAME: "last-known",
    KEY_PC_STATE: "0",
    KEY_PC_TIMEMAP: "",
    KEY_PC_TYPE: "0",
}
STALE_RESPONSE = {**PC_RESPONSE, KEY_PC_NAME: "in-flight"}
NEW_RESPONSE = {**PC_RESPONSE, KEY_PC_NAME: "after-write"}


@pytest.fixture
def cached_router(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> AsusRouter:
    """Seed a fresh table and acknowledge writes through mocked transport."""

    router._identity = AsusDevice()
    router._state[AsusData.PARENTAL_CONTROL] = AsusDataState(
        data=process_parental_control(PC_RESPONSE),
        timestamp=datetime.now(UTC),
    )
    monkeypatch.setattr(
        router,
        "async_api_command",
        AsyncMock(
            return_value={
                "run_service": "restart_firewall",
                "modify": "1",
                "restart_needed_time": "60",
            }
        ),
    )
    return router


@pytest.mark.parametrize("key", HOOK_PC)
@pytest.mark.parametrize("service", ["restart_firewall", None])
async def test_successful_pc_write_bypasses_fresh_cache(
    cached_router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    service: str | None,
) -> None:
    """Every PC key invalidates synchronously, even with a falsey value."""

    state = cached_router._state[AsusData.PARENTAL_CONTROL]
    previous, timestamp = state.data, state.timestamp
    fetch = AsyncMock(return_value=NEW_RESPONSE.copy())
    monkeypatch.setattr(cached_router, "async_api_load", fetch)

    assert (
        await cached_router.async_get_data(AsusData.PARENTAL_CONTROL)
        is previous
    )
    fetch.assert_not_awaited()
    result = await cached_router.async_run_service_result(service, {key: ""})

    assert result.success is True
    assert state.invalidated is True
    assert state.generation == 1
    assert state.data is previous
    assert state.timestamp == timestamp
    assert await cached_router.async_get_data(
        AsusData.PARENTAL_CONTROL
    ) == process_parental_control(NEW_RESPONSE)
    fetch.assert_awaited_once()


@pytest.mark.parametrize("force", [False, True], ids=["ordinary", "forced"])
async def test_inflight_pc_read_crossing_write(
    cached_router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    force: bool,
) -> None:
    """A pre-write response cannot replace or freshen the last-known table."""

    state = cached_router._state[AsusData.PARENTAL_CONTROL]
    state.timestamp -= timedelta(days=1)
    previous, timestamp = state.data, state.timestamp
    started, release = asyncio.Event(), asyncio.Event()

    async def load(*_: Any) -> dict[str, str]:
        if not started.is_set():
            started.set()
            await release.wait()
            return STALE_RESPONSE.copy()
        return NEW_RESPONSE.copy()

    fetch = AsyncMock(side_effect=load)
    monkeypatch.setattr(cached_router, "async_api_load", fetch)
    task = asyncio.create_task(
        cached_router.async_get_data(AsusData.PARENTAL_CONTROL, force=force)
    )
    await started.wait()
    try:
        assert state.active is True
        assert not state.inactive_event.is_set()
        await cached_router.async_run_service_result(
            "restart_firewall", {KEY_PC_STATE: "1"}
        )
    finally:
        release.set()

    if force:
        with pytest.raises(AsusRouterDataError, match="invalidat"):
            await task
    else:
        assert await task is previous
    assert state.data is previous
    assert state.timestamp == timestamp
    assert state.invalidated is True
    assert state.generation == 1
    assert state.active is False
    assert state.inactive_event.is_set()

    assert await cached_router.async_get_data(
        AsusData.PARENTAL_CONTROL
    ) == process_parental_control(NEW_RESPONSE)
    assert fetch.await_count == 2
    assert state.invalidated is False


@pytest.mark.parametrize(
    "outcome", ["failure", "exception", "unrelated", "no-arguments"]
)
async def test_failed_or_unrelated_write_preserves_cache(
    cached_router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Only an acknowledged write containing a PC key invalidates data."""

    state = cached_router._state[AsusData.PARENTAL_CONTROL]
    previous, timestamp = state.data, state.timestamp
    arguments = {KEY_PC_STATE: "1"}
    command = AsyncMock(
        return_value={"run_service": "restart_firewall", "modify": "1"}
    )
    if outcome == "failure":
        command.return_value["modify"] = "0"
    elif outcome == "exception":
        command.side_effect = AsusRouterConnectionError("write failed")
    elif outcome == "unrelated":
        arguments = {"unrelated": "1"}
    else:
        arguments = None
    monkeypatch.setattr(cached_router, "async_api_command", command)
    fetch = AsyncMock()
    monkeypatch.setattr(cached_router, "async_api_load", fetch)

    if outcome == "exception":
        with pytest.raises(AsusRouterConnectionError):
            await cached_router.async_run_service_result(
                "restart_firewall", arguments
            )
    else:
        result = await cached_router.async_run_service_result(
            "restart_firewall", arguments
        )
        assert result.success is (outcome != "failure")

    assert state.invalidated is False
    assert state.generation == 0
    assert state.timestamp == timestamp
    assert (
        await cached_router.async_get_data(AsusData.PARENTAL_CONTROL)
        is previous
    )
    fetch.assert_not_awaited()


@pytest.mark.parametrize("scalar", [AsusParentalControl.ON, AsusBlockAll.ON])
@pytest.mark.parametrize("needed_time", [None, 60])
async def test_scalar_save_does_not_freshen_invalidated_rules(
    cached_router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    scalar: AsusParentalControl | AsusBlockAll,
    needed_time: int | None,
) -> None:
    """Master/block-all saves keep the fallback without extending its TTL."""

    state = cached_router._state[AsusData.PARENTAL_CONTROL]
    previous, timestamp = state.data, state.timestamp
    rules = previous["rules"]
    monkeypatch.setattr(
        cached_router,
        "async_api_command",
        AsyncMock(
            return_value={
                "run_service": "restart_firewall",
                "restart_needed_time": needed_time,
            }
        ),
    )
    fetch = AsyncMock(return_value=NEW_RESPONSE.copy())
    monkeypatch.setattr(cached_router, "async_api_load", fetch)

    assert await cached_router.async_set_state(scalar) is True
    assert state.invalidated is True
    assert state.generation == 1
    assert state.timestamp == timestamp
    assert state.data is previous
    assert state.data["rules"] is rules
    key = "block_all" if isinstance(scalar, AsusBlockAll) else "state"
    assert state.data[key] is True
    other_key = "state" if key == "block_all" else "block_all"
    assert bool(state.data[other_key]) is False

    await cached_router.async_get_data(AsusData.PARENTAL_CONTROL)
    fetch.assert_awaited_once()


async def test_post_invalidation_fetch_restores_freshness(
    cached_router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writes advance the generation; a later valid fetch clears the flag."""

    state = cached_router._state[AsusData.PARENTAL_CONTROL]
    state.timestamp -= timedelta(days=1)
    timestamp = state.timestamp
    for generation in (1, 2):
        await cached_router.async_run_service_result(
            "restart_firewall", {KEY_PC_STATE: "1"}
        )
        assert state.invalidated is True
        assert state.generation == generation
    fetch = AsyncMock(return_value=NEW_RESPONSE.copy())
    monkeypatch.setattr(cached_router, "async_api_load", fetch)

    result = await cached_router.async_get_data(AsusData.PARENTAL_CONTROL)
    assert result == process_parental_control(NEW_RESPONSE)
    assert state.invalidated is False
    assert state.generation == 2
    assert state.timestamp > timestamp
    assert (
        await cached_router.async_get_data(AsusData.PARENTAL_CONTROL) is result
    )
    fetch.assert_awaited_once()


async def test_pc_write_leaves_other_cache_entries_untouched(
    cached_router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PC invalidation leaves other datatypes and their waiters alone."""

    other = AsusDataState(data={"state": True}, timestamp=datetime.now(UTC))
    other.stop()
    cached_router._state[AsusData.LED] = other
    previous, timestamp = other.data, other.timestamp
    keys = set(cached_router._state)
    fetch = AsyncMock(return_value=NEW_RESPONSE.copy())
    monkeypatch.setattr(cached_router, "async_api_load", fetch)

    await cached_router.async_run_service_result(
        "restart_firewall", {KEY_PC_STATE: "1"}
    )
    await cached_router.async_get_data(AsusData.PARENTAL_CONTROL)

    assert set(cached_router._state) == keys
    assert cached_router._state[AsusData.LED] is other
    assert other.data is previous
    assert other.timestamp == timestamp
    assert other.generation == 0
    assert other.invalidated is False
    assert other.active is False
    assert other.inactive_event.is_set()
    assert await cached_router.async_get_data(AsusData.LED) is previous
    fetch.assert_awaited_once()


async def test_cancelled_invalidated_fetch_releases_waiters(
    cached_router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling a post-write fetch releases waiters and keeps stale data."""

    state = cached_router._state[AsusData.PARENTAL_CONTROL]
    previous, timestamp = state.data, state.timestamp
    await cached_router.async_run_service_result(
        "restart_firewall", {KEY_PC_STATE: "1"}
    )
    started, release = asyncio.Event(), asyncio.Event()

    async def load(*_: Any) -> dict[str, str]:
        started.set()
        await release.wait()
        return NEW_RESPONSE.copy()

    monkeypatch.setattr(
        cached_router, "async_api_load", AsyncMock(side_effect=load)
    )
    task = asyncio.create_task(
        cached_router.async_get_data(AsusData.PARENTAL_CONTROL, force=True)
    )
    await started.wait()
    waiter = asyncio.create_task(state.inactive_event.wait())
    assert not state.inactive_event.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert state.active is False
    assert state.inactive_event.is_set()
    assert await waiter is True
    assert state.data is previous
    assert state.timestamp == timestamp
    assert state.invalidated is True
    assert state.generation == 1


@pytest.mark.parametrize("seed_pc", [False, True])
async def test_other_datatype_fetch_cannot_install_pre_write_pc_response(
    cached_router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    seed_pc: bool,
) -> None:
    """Generation checks also guard incidental PC data in another fetch."""

    if not seed_pc:
        cached_router._state.pop(AsusData.PARENTAL_CONTROL)
    started, release = asyncio.Event(), asyncio.Event()

    async def load(*_: Any) -> dict[str, str]:
        started.set()
        await release.wait()
        return {**STALE_RESPONSE, "led_val": "1"}

    monkeypatch.setattr(
        cached_router, "async_api_load", AsyncMock(side_effect=load)
    )
    task = asyncio.create_task(
        cached_router.async_get_data(AsusData.LED, force=True)
    )
    await started.wait()
    try:
        await cached_router.async_run_service_result(
            "restart_firewall", {KEY_PC_STATE: "1"}
        )
    finally:
        release.set()
    assert await task == {"state": 1}
    state = cached_router._state[AsusData.PARENTAL_CONTROL]
    assert state.invalidated is True
    assert state.generation == 1
    assert state.data == (
        process_parental_control(PC_RESPONSE) if seed_pc else None
    )


def test_data_states_have_independent_waiters() -> None:
    """One datatype's fetch must not clear another's completion event."""

    first, second = AsusDataState(), AsusDataState()
    first.stop()
    second.start()
    assert first.inactive_event.is_set()
    assert not second.inactive_event.is_set()
