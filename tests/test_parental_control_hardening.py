"""Regression tests for authoritative parental-control reads and writes."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from asusrouter.asusrouter import AsusRouter
from asusrouter.error import (
    AsusRouterConnectionError,
    AsusRouterDataError,
    AsusRouterError,
)
from asusrouter.modules.aura import AsusAura
from asusrouter.modules.data import AsusData, AsusDataState
from asusrouter.modules.endpoint.hook import process, process_parental_control
from asusrouter.modules.identity import AsusDevice
from asusrouter.modules.parental_control import (
    DEFAULT_PC_TIMEMAP,
    KEY_PC_BLOCK_ALL,
    KEY_PC_MAC,
    KEY_PC_NAME,
    KEY_PC_STATE,
    KEY_PC_TIMEMAP,
    KEY_PC_TYPE,
    PC_RULE_MAP,
    AsusBlockAll,
    AsusParentalControl,
    ParentalControlRule,
    PCRuleType,
    check_rule,
    read_pc_rules,
    set_rule,
    write_pc_rules,
)
from asusrouter.modules.vpnc import AsusVPNC
from tests.test_data.rt_ax88u_merlin_388.hook_003 import expected_result


@pytest.fixture
def pc_router(router: AsusRouter) -> AsusRouter:
    """Provide a router with an expired, populated parental-control cache."""

    router._identity = AsusDevice()
    router._state[AsusData.PARENTAL_CONTROL] = AsusDataState(
        data=expected_result[AsusData.PARENTAL_CONTROL].copy(),
        timestamp=datetime.now(UTC) - timedelta(days=1),
        inactive_event=asyncio.Event(),
    )
    return router


@pytest.fixture
def pc_response() -> dict[str, str]:
    """Load the existing eight-rule Merlin fixture."""

    fixture = (
        Path(__file__).parent
        / "test_data/rt_ax88u_merlin_388/hook_003.content"
    )
    return json.loads(fixture.read_text(encoding="utf-8"))


@pytest.mark.parametrize("force", [True, False])
@pytest.mark.parametrize(
    "error_type", [AsusRouterConnectionError, AsusRouterDataError]
)
async def test_get_data_fetch_failure(
    pc_router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    force: bool,
    error_type: type[AsusRouterError],
) -> None:
    """Forced fetch failures raise; ordinary reads retain cached fallback."""

    state = pc_router._state[AsusData.PARENTAL_CONTROL]
    previous = state.data
    timestamp = state.timestamp
    error = error_type("fetch failed")
    fetch = AsyncMock(side_effect=error)
    monkeypatch.setattr(pc_router, "async_api_load", fetch)

    if force:
        with pytest.raises(AsusRouterError) as raised:
            await pc_router.async_get_data(
                AsusData.PARENTAL_CONTROL, force=True
            )
        assert raised.value is error
    else:
        assert (
            await pc_router.async_get_data(AsusData.PARENTAL_CONTROL)
            is previous
        )

    fetch.assert_awaited_once()
    assert state.data is previous
    assert state.timestamp == timestamp
    assert state.active is False
    assert state.inactive_event.is_set()


@pytest.mark.parametrize(
    "error_type", [AsusRouterConnectionError, AsusRouterDataError]
)
async def test_failed_forced_fetch_does_not_block_next_reader(
    pc_router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[AsusRouterError],
) -> None:
    """A failed refresh releases waiters without needing a real timeout."""

    state = pc_router._state[AsusData.PARENTAL_CONTROL]
    state.timestamp = datetime.now(UTC)
    monkeypatch.setattr(
        pc_router, "async_api_load", AsyncMock(side_effect=error_type())
    )
    wait = AsyncMock()
    monkeypatch.setattr(state.inactive_event, "wait", wait)

    with suppress(AsusRouterError):
        await pc_router.async_get_data(AsusData.PARENTAL_CONTROL, force=True)

    assert (
        await pc_router.async_get_data(AsusData.PARENTAL_CONTROL) is state.data
    )
    wait.assert_not_awaited()
    assert state.active is False
    assert state.inactive_event.is_set()


@pytest.mark.parametrize("force", [True, False])
async def test_get_data_omitted_update(
    pc_router: AsusRouter, monkeypatch: pytest.MonkeyPatch, force: bool
) -> None:
    """An omitted datatype cannot masquerade as a successful forced read."""

    state = pc_router._state[AsusData.PARENTAL_CONTROL]
    monkeypatch.setattr(
        pc_router, "async_api_load", AsyncMock(return_value={})
    )
    if force:
        with pytest.raises(AsusRouterDataError):
            await pc_router.async_get_data(
                AsusData.PARENTAL_CONTROL, force=True
            )
    else:
        assert (
            await pc_router.async_get_data(AsusData.PARENTAL_CONTROL)
            is state.data
        )
    assert state.active is False
    assert state.inactive_event.is_set()


async def test_get_data_without_finder_stops_update(
    pc_router: AsusRouter,
) -> None:
    """The early return for an unavailable finder must also release waiters."""

    pc_router._identity = None
    assert await pc_router.async_get_data(AsusData.PARENTAL_CONTROL) == {}
    assert pc_router._state[AsusData.PARENTAL_CONTROL].active is False


async def test_cancelled_fetch_stops_update(
    pc_router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation must not leave an update in progress."""

    monkeypatch.setattr(
        pc_router,
        "async_api_load",
        AsyncMock(side_effect=asyncio.CancelledError),
    )
    with pytest.raises(asyncio.CancelledError):
        await pc_router.async_get_data(AsusData.PARENTAL_CONTROL, force=True)
    assert pc_router._state[AsusData.PARENTAL_CONTROL].active is False


@pytest.mark.parametrize(
    "error_type", [AsusRouterConnectionError, AsusRouterDataError]
)
async def test_firmware_notes_failure_is_optional(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[AsusRouterError],
) -> None:
    """Optional release notes must not break a firmware status refresh."""

    firmware = {"state": True, "version": "new"}
    router._state[AsusData.FIRMWARE] = AsusDataState(data=firmware)
    fetch = AsyncMock(side_effect=error_type())
    monkeypatch.setattr(router, "async_get_data", fetch)

    await router._check_postrequisites(AsusData.FIRMWARE)

    fetch.assert_awaited_once_with(AsusData.FIRMWARE_NOTE, force=True)
    assert firmware == {"state": True, "version": "new"}


@pytest.mark.parametrize("state", [AsusVPNC.ON, AsusAura.ON])
async def test_state_write_requires_successful_dependency_refresh(
    pc_router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    state: AsusVPNC | AsusAura,
) -> None:
    """VPNC and AURA writes require a successful forced dependency fetch."""

    monkeypatch.setattr(
        pc_router, "async_api_load", AsyncMock(side_effect=AsusRouterDataError)
    )
    callback = AsyncMock()
    monkeypatch.setattr(pc_router, "async_run_service", callback)
    monkeypatch.setattr(pc_router, "async_api_command", callback)
    with pytest.raises(AsusRouterError):
        await pc_router.async_set_state(state)
    callback.assert_not_awaited()


@pytest.mark.parametrize("state", [AsusVPNC.ON, AsusAura.ON])
async def test_state_write_survives_failed_post_write_refresh(
    pc_router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    state: AsusVPNC | AsusAura,
) -> None:
    """A successful write is not failed by the refresh that follows it."""

    dependency = AsyncMock(side_effect=[None, AsusRouterDataError()])
    monkeypatch.setattr(pc_router, "_async_check_state_dependency", dependency)
    monkeypatch.setattr(
        "asusrouter.asusrouter.set_state", AsyncMock(return_value=True)
    )
    monkeypatch.setattr("asusrouter.asusrouter.asyncio.sleep", AsyncMock())

    assert await pc_router.async_set_state(state) is True
    assert dependency.await_count == 2


@pytest.mark.parametrize(
    "data",
    [{KEY_PC_STATE: "1"}, {KEY_PC_BLOCK_ALL: "1"}],
)
def test_process_parental_control_partial(
    data: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """State-only responses preserve available switches but omit rules."""

    with caplog.at_level(logging.WARNING):
        result = process_parental_control(data)
        assert process(data)[AsusData.PARENTAL_CONTROL] == result

    assert "rules" not in result
    if KEY_PC_STATE in data:
        assert result["state"] == AsusParentalControl.ON
    if KEY_PC_BLOCK_ALL in data:
        assert result["block_all"] == AsusBlockAll.ON
    assert caplog.records
    assert all(record.levelno == logging.WARNING for record in caplog.records)
    assert all(key in caplog.text for key in PC_RULE_MAP)


def test_process_parental_control_empty() -> None:
    """Four explicitly empty vectors mean a genuinely empty table."""

    data = {**dict.fromkeys(PC_RULE_MAP, ""), KEY_PC_STATE: "1"}
    assert process_parental_control(data)["rules"] == {}
    assert read_pc_rules(data) == {}


@pytest.mark.parametrize("key", PC_RULE_MAP)
@pytest.mark.parametrize("invalid", [None, 1, []])
def test_process_parental_control_non_string_vector(
    pc_response: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    key: str,
    invalid: Any,
) -> None:
    """Non-string vectors are unknown, even alongside otherwise valid data."""

    pc_response[key] = invalid
    assert "rules" not in process_parental_control(pc_response)
    assert key in caplog.text
    assert caplog.records[0].levelno == logging.WARNING


@pytest.mark.parametrize("key", PC_RULE_MAP)
def test_read_pc_rules_missing_vector(
    pc_response: dict[str, str],
    key: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Direct readers reject missing vectors instead of fabricating fields."""

    pc_response.pop(key)
    with pytest.raises(AsusRouterDataError, match=key):
        read_pc_rules(pc_response)
    assert "rules" not in process_parental_control(pc_response)
    assert f"{key}=missing" in caplog.text
    assert caplog.records[0].levelno == logging.WARNING


def test_read_pc_rules_absent_is_unknown() -> None:
    """Two absent keys are not an empty table."""

    with pytest.raises(AsusRouterDataError):
        read_pc_rules({})


def test_process_parental_control_mismatched_vectors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unequal cardinalities are logged and never silently truncated."""

    data = {
        KEY_PC_STATE: "1",
        KEY_PC_MAC: (
            "00:00:00:00:00:01&#6200:00:00:00:00:02&#6200:00:00:00:00:03"
        ),
        KEY_PC_NAME: "one&#62two",
        KEY_PC_TIMEMAP: "&#62&#62",
        KEY_PC_TYPE: "2&#622&#622",
    }
    assert "rules" not in process_parental_control(data)
    assert caplog.records[0].levelno == logging.WARNING
    for key, count in (
        (KEY_PC_MAC, 3),
        (KEY_PC_NAME, 2),
        (KEY_PC_TIMEMAP, 3),
        (KEY_PC_TYPE, 3),
    ):
        assert f"{key}={count}" in caplog.text
    with pytest.raises(AsusRouterDataError):
        read_pc_rules(data)


def test_process_parental_control_aligned_fixture(
    pc_response: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """The complete existing Merlin fixture retains its original meaning."""

    assert (
        process_parental_control(pc_response)
        == expected_result[AsusData.PARENTAL_CONTROL]
    )
    assert not caplog.records


def test_empty_rule_fields_round_trip() -> None:
    """Empty names and schedules must survive reading and writing unchanged."""

    data = {
        KEY_PC_MAC: "00:00:00:00:00:01",
        KEY_PC_NAME: "",
        KEY_PC_TIMEMAP: "",
        KEY_PC_TYPE: "0",
    }
    rules = read_pc_rules(data)
    rule = rules[data[KEY_PC_MAC]]
    assert rule.name == ""
    assert rule.timemap == ""
    assert write_pc_rules(rules) == data


def test_write_pc_rules_none_fields() -> None:
    """Defensive serialization renders None as empty without losing type 0."""

    mac = "00:00:00:00:00:01"
    rule = ParentalControlRule(
        mac=mac, name=None, timemap=None, type=PCRuleType.DISABLE
    )
    assert write_pc_rules({mac: rule}) == {
        KEY_PC_MAC: mac,
        KEY_PC_NAME: "",
        KEY_PC_TIMEMAP: "",
        KEY_PC_TYPE: "0",
    }


@pytest.mark.parametrize(
    ("name", "timemap"), [(None, "valid"), ("valid", None), (None, None)]
)
def test_check_rule_none_fields(name: str | None, timemap: str | None) -> None:
    """None names and schedules receive the existing defaults."""

    rule = ParentalControlRule(
        mac="00:00:00:00:00:01",
        name=name,
        timemap=timemap,
        type=PCRuleType.BLOCK,
    )
    assert check_rule(rule) is rule
    assert rule.name == (name or rule.mac)
    assert rule.timemap == (timemap or DEFAULT_PC_TIMEMAP)


@pytest.mark.parametrize(
    "router_state",
    [
        {},
        {AsusData.PARENTAL_CONTROL: AsusDataState(data=None)},
        {AsusData.PARENTAL_CONTROL: AsusDataState(data=[])},
        {AsusData.PARENTAL_CONTROL: AsusDataState(data={"state": True})},
        {AsusData.PARENTAL_CONTROL: AsusDataState(data={"rules": None})},
        {AsusData.PARENTAL_CONTROL: AsusDataState(data={"rules": []})},
    ],
    ids=[
        "absent",
        "none-data",
        "list-data",
        "absent-rules",
        "none-rules",
        "list-rules",
    ],
)
@pytest.mark.parametrize("rule_type", [PCRuleType.BLOCK, PCRuleType.REMOVE])
async def test_set_rule_refuses_unknown_rules(
    router_state: dict[AsusData, AsusDataState],
    rule_type: PCRuleType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Neither adding nor removing can write from an unknown rule table."""

    callback = AsyncMock(return_value=True)
    rule = ParentalControlRule(mac="00:00:00:00:00:01", type=rule_type)
    assert await set_rule(callback, rule, router_state=router_state) is False
    callback.assert_not_called()
    assert caplog.records[0].levelno == logging.ERROR


async def test_set_rule_allows_known_empty_rules() -> None:
    """An explicitly empty rule dict permits writing the first rule."""

    callback = AsyncMock(return_value=True)
    rule = ParentalControlRule(
        mac="00:00:00:00:00:01", name="new", type=PCRuleType.BLOCK
    )
    assert (
        await set_rule(
            callback,
            rule,
            router_state={
                AsusData.PARENTAL_CONTROL: AsusDataState(data={"rules": {}})
            },
        )
        is True
    )
    callback.assert_awaited_once_with(
        service="restart_firewall",
        arguments={
            KEY_PC_MAC: rule.mac,
            KEY_PC_NAME: "new",
            KEY_PC_TIMEMAP: DEFAULT_PC_TIMEMAP,
            KEY_PC_TYPE: "2",
        },
        apply=True,
    )


async def test_partial_refresh_invalidates_cached_rules_before_write(
    pc_router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial refresh invalidates stale rules and prevents public writes."""

    monkeypatch.setattr(
        pc_router,
        "async_api_load",
        AsyncMock(return_value={KEY_PC_STATE: "1"}),
    )
    callback = AsyncMock(return_value=True)
    monkeypatch.setattr(pc_router, "async_run_service", callback)
    snapshot = await pc_router.async_get_data(
        AsusData.PARENTAL_CONTROL, force=True
    )
    assert "rules" not in snapshot
    assert (
        await pc_router.async_set_state(
            ParentalControlRule(mac="00:00:00:00:00:09", type=PCRuleType.BLOCK)
        )
        is False
    )
    callback.assert_not_called()


@pytest.mark.parametrize("rule_type", [PCRuleType.BLOCK, PCRuleType.REMOVE])
async def test_rule_write_preserves_remaining_table(
    pc_router: AsusRouter,
    pc_response: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    rule_type: PCRuleType,
) -> None:
    """Rule additions and removals preserve other rules and their encoding."""

    monkeypatch.setattr(
        pc_router, "async_api_load", AsyncMock(return_value=pc_response)
    )
    callback = AsyncMock(return_value=True)
    monkeypatch.setattr(pc_router, "async_run_service", callback)
    await pc_router.async_get_data(AsusData.PARENTAL_CONTROL, force=True)

    if rule_type == PCRuleType.BLOCK:
        mac = "00:00:00:00:00:09"
        additions = {
            KEY_PC_MAC: mac,
            KEY_PC_NAME: "new",
            KEY_PC_TIMEMAP: DEFAULT_PC_TIMEMAP,
            KEY_PC_TYPE: "2",
        }
        expected = {
            key: pc_response[key].replace("&#62", ">").replace("&#60", "<")
            + ">"
            + value
            for key, value in additions.items()
        }
    else:
        mac = "00:00:00:00:00:01"
        expected = {
            key: ">".join(pc_response[key].split("&#62")[1:]).replace(
                "&#60", "<"
            )
            for key in PC_RULE_MAP
        }

    assert (
        await pc_router.async_set_state(
            ParentalControlRule(mac=mac, name="new", type=rule_type)
        )
        is True
    )
    callback.assert_awaited_once_with(
        service="restart_firewall", arguments=expected, apply=True
    )
