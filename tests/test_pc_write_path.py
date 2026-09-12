"""Tests for serialized parental-control rule writes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from asusrouter.asusrouter import AsusRouter
from asusrouter.error import AsusRouterDataError
from asusrouter.modules.data import AsusData, AsusDataState
from asusrouter.modules.identity import AsusDevice
from asusrouter.modules.parental_control import (
    KEY_PC_BLOCK_ALL,
    KEY_PC_MAC,
    KEY_PC_NAME,
    KEY_PC_STATE,
    KEY_PC_TIMEMAP,
    KEY_PC_TYPE,
    PC_RULE_MAP,
    ParentalControlRule,
    PCRuleType,
    check_rule,
    read_pc_rules,
    set_rule,
    write_pc_rules,
)


def _pc_response(
    rules: dict[str, ParentalControlRule],
) -> dict[str, str]:
    """Build a complete hook response from the simulated router table."""

    return {
        KEY_PC_STATE: "1",
        KEY_PC_BLOCK_ALL: "0",
        **write_pc_rules(rules),
    }


def _seed_router(router: AsusRouter) -> None:
    """Give a router identity and a known empty parental-control table."""

    router._identity = AsusDevice()
    router._state[AsusData.PARENTAL_CONTROL] = AsusDataState(
        data={"state": True, "block_all": False, "rules": {}},
        timestamp=datetime.now(UTC),
    )


def _rule(number: int) -> ParentalControlRule:
    """Create a valid rule with a stable MAC address."""

    return ParentalControlRule(
        mac=f"00:00:00:00:00:{number:02d}",
        name=f"device-{number}",
        timemap="valid",
        type=PCRuleType.BLOCK,
    )


def _read_written_rules(
    arguments: dict[str, str],
) -> dict[str, ParentalControlRule]:
    """Simulate the router's entity encoding on a subsequent hook read."""

    return read_pc_rules(
        {key: arguments[key].replace(">", "&#62") for key in PC_RULE_MAP}
    )


async def test_consecutive_rule_writes_refresh_authoritative_table(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second library write retains the rule written by the first."""

    _seed_router(router)
    router_rules: dict[str, ParentalControlRule] = {}
    writes: list[dict[str, str]] = []

    async def load(*_: Any) -> dict[str, str]:
        return _pc_response(router_rules)

    async def command(arguments: dict[str, str]) -> dict[str, str]:
        nonlocal router_rules
        writes.append(arguments.copy())
        router_rules = _read_written_rules(arguments)
        return {"run_service": "restart_firewall", "modify": "1"}

    monkeypatch.setattr(router, "async_api_load", AsyncMock(side_effect=load))
    monkeypatch.setattr(
        router, "async_api_command", AsyncMock(side_effect=command)
    )

    first, second = _rule(1), _rule(2)
    assert await router.async_set_state(first) is True
    assert await router.async_set_state(second) is True

    assert set(router_rules) == {first.mac, second.mac}
    assert writes[-1][KEY_PC_MAC] == f"{first.mac}>{second.mac}"


async def test_concurrent_rule_writes_are_serialized(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent callers cannot replace tables from the same snapshot."""

    _seed_router(router)
    router_rules: dict[str, ParentalControlRule] = {}
    writes: list[dict[str, str]] = []

    async def load(*_: Any) -> dict[str, str]:
        return _pc_response(router_rules)

    async def command(arguments: dict[str, str]) -> dict[str, str]:
        nonlocal router_rules
        # Give a competing task a chance to enter the write path.
        await asyncio.sleep(0)
        writes.append(arguments.copy())
        router_rules = _read_written_rules(arguments)
        return {"run_service": "restart_firewall", "modify": "1"}

    monkeypatch.setattr(router, "async_api_load", AsyncMock(side_effect=load))
    monkeypatch.setattr(
        router, "async_api_command", AsyncMock(side_effect=command)
    )

    first, second = _rule(1), _rule(2)
    results = await asyncio.gather(
        router.async_set_state(first), router.async_set_state(second)
    )

    assert results == [True, True]
    assert set(router_rules) == {first.mac, second.mac}
    assert len(writes) == 2
    assert set(writes[-1][KEY_PC_MAC].split(">")) == {
        first.mac,
        second.mac,
    }


async def test_failed_rule_refetch_sends_no_write(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed authoritative refresh aborts before the service call."""

    _seed_router(router)
    monkeypatch.setattr(
        router,
        "async_api_load",
        AsyncMock(side_effect=AsusRouterDataError("fetch failed")),
    )
    command = AsyncMock()
    monkeypatch.setattr(router, "async_api_command", command)

    with pytest.raises(AsusRouterDataError, match="fetch failed"):
        await router.async_set_state(_rule(1))

    command.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "bad>name"),
        ("name", "bad<name"),
        ("name", "bad&#62name"),
        ("name", "bad&#60name"),
        ("name", "bad\x1fname"),
        ("name", "\n"),
        ("name", "n" * 33),
        ("timemap", "bad>map"),
        ("timemap", "bad<map"),
        ("timemap", "bad&#62map"),
        ("timemap", "bad&#60map"),
        ("timemap", "bad\nmap"),
        ("timemap", "\t"),
    ],
)
async def test_unsafe_new_rule_field_sends_no_write(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    """Unsafe parallel-vector content is rejected before a write."""

    _seed_router(router)
    monkeypatch.setattr(
        router,
        "async_api_load",
        AsyncMock(return_value=_pc_response({})),
    )
    command = AsyncMock()
    monkeypatch.setattr(router, "async_api_command", command)
    values = {"name": "normal", "timemap": "valid", field: value}
    rule = ParentalControlRule(
        mac="00:00:00:00:00:01",
        name=values["name"],
        timemap=values["timemap"],
        type=PCRuleType.BLOCK,
    )

    assert await router.async_set_state(rule) is False
    command.assert_not_awaited()


async def test_normal_new_rule_field_is_written(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Safe names and timemaps still pass validation."""

    _seed_router(router)
    monkeypatch.setattr(
        router,
        "async_api_load",
        AsyncMock(return_value=_pc_response({})),
    )
    command = AsyncMock(
        return_value={"run_service": "restart_firewall", "modify": "1"}
    )
    monkeypatch.setattr(router, "async_api_command", command)

    assert await router.async_set_state(_rule(1)) is True
    assert command.await_args.args[0][KEY_PC_NAME] == "device-1"
    assert command.await_args.args[0][KEY_PC_TIMEMAP] == "valid"
    assert command.await_args.args[0][KEY_PC_TYPE] == "2"


async def test_retained_rule_fields_are_not_validated_or_altered() -> None:
    """Validation applies only to the proposed rule, not cached router rows."""

    retained = ParentalControlRule(
        mac="00:00:00:00:00:01",
        name="router>value",
        timemap="router<value",
        type=PCRuleType.BLOCK,
    )
    state = AsusDataState(data={"rules": {retained.mac: retained}})
    callback = AsyncMock(return_value=True)

    assert (
        await set_rule(
            callback,
            _rule(2),
            router_state={AsusData.PARENTAL_CONTROL: state},
        )
        is True
    )
    assert state.data["rules"][retained.mac] is retained
    assert callback.await_args.kwargs["arguments"][KEY_PC_NAME].startswith(
        "router>value>"
    )
    assert callback.await_args.kwargs["arguments"][KEY_PC_TIMEMAP].startswith(
        "router<value>"
    )


@pytest.mark.parametrize(
    "mac", ["AA>BB", "AA:BB:CC:DD:EE", "not a mac", "AABBCCDDEEFF", ""]
)
def test_check_rule_rejects_malformed_mac(mac: str) -> None:
    """A MAC that is not colon-separated hex cannot enter the table."""

    rule = ParentalControlRule(mac=mac, name="", type=PCRuleType.BLOCK)

    assert check_rule(rule) is None
