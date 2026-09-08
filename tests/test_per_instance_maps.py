"""Regression tests for router-specific data finders and state dispatch."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from asusrouter.asusrouter import AsusRouter
from asusrouter.modules import data_finder, state as state_module
from asusrouter.modules.data import AsusData, AsusDataState
from asusrouter.modules.data_finder import AsusDataFinder
from asusrouter.modules.endpoint import Endpoint
from asusrouter.modules.firmware import Firmware
from asusrouter.modules.identity import AsusDevice
from asusrouter.modules.led import AsusLED
from asusrouter.modules.openvpn import AsusOVPNClient
from asusrouter.modules.state import AsusState
from asusrouter.modules.vpnc import AsusVPNC, AsusVPNType
from asusrouter.modules.wireguard import AsusWireGuardClient


@pytest.fixture(autouse=True)
def isolate_global_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep regression failures and legacy helper calls from leaking maps."""

    monkeypatch.setattr(
        data_finder, "ASUSDATA_MAP", deepcopy(data_finder.ASUSDATA_MAP)
    )
    monkeypatch.setattr(
        "asusrouter.asusrouter.ASUSDATA_MAP", data_finder.ASUSDATA_MAP
    )
    monkeypatch.setattr(
        state_module, "AsusStateMap", deepcopy(state_module.AsusStateMap)
    )
    monkeypatch.setattr(
        "asusrouter.asusrouter.AsusStateMap",
        state_module.AsusStateMap,
        raising=False,
    )


def _data_defaults() -> dict:
    """Snapshot finder contents, including nested mutable configuration."""

    return {
        datatype: deepcopy(vars(finder))
        if isinstance(finder, AsusDataFinder)
        else finder
        for datatype, finder in data_finder.ASUSDATA_MAP.items()
    }


def _identity(*, modern: bool, merlin: bool = False) -> AsusDevice:
    """Create identities on opposite sides of the VPN Fusion boundary."""

    return AsusDevice(
        firmware=Firmware(
            major="3.0.0.4", minor=388 if modern else 386, build=1
        ),
        merlin=merlin,
        dsl=modern,
        ookla=modern,
        endpoints=dict.fromkeys(Endpoint, True),
    )


@pytest.mark.parametrize("modern_first", [False, True])
@pytest.mark.parametrize("merlin", [False, True])
async def test_identity_maps_are_isolated(
    monkeypatch: pytest.MonkeyPatch, modern_first: bool, merlin: bool
) -> None:
    """Identifying old and new firmware in either order preserves defaults."""

    legacy = AsusRouter("legacy", "user", "password")
    modern = AsusRouter("modern", "user", "password")
    identities = {
        legacy: _identity(modern=False),
        modern: _identity(modern=True, merlin=merlin),
    }
    defaults = _data_defaults()
    state_defaults = state_module.AsusStateMap.copy()
    order = (modern, legacy) if modern_first else (legacy, modern)
    collect = AsyncMock(side_effect=[identities[item] for item in order])
    monkeypatch.setattr("asusrouter.asusrouter.collect_identity", collect)

    for item in order:
        assert await item.async_get_identity() is identities[item]

    for datatype in (
        AsusData.VPNC,
        AsusData.VPNC_CLIENTLIST,
        AsusData.WIREGUARD,
        AsusData.WIREGUARD_CLIENT,
        AsusData.WIREGUARD_SERVER,
        AsusData.DSL,
        AsusData.SPEEDTEST,
        AsusData.SPEEDTEST_RESULT,
    ):
        assert legacy._where_to_get_data(datatype) is None
        finder = modern._where_to_get_data(datatype)
        assert isinstance(finder, AsusDataFinder)
        assert finder.endpoint == [Endpoint.HOOK]

    assert legacy._state_map == state_defaults
    assert modern._state_map[AsusState.OPENVPN_CLIENT] == (
        AsusData.OPENVPN_CLIENT if merlin else AsusData.VPNC
    )
    assert modern._state_map[AsusState.WIREGUARD_CLIENT] == (
        AsusData.WIREGUARD_CLIENT if merlin else AsusData.VPNC
    )
    assert legacy._data_map[AsusData.OPENVPN_CLIENT] == AsusData.OPENVPN
    assert modern._data_map[AsusData.OPENVPN_CLIENT] == (
        AsusData.OPENVPN if merlin else AsusData.VPNC
    )
    server = modern._where_to_get_data(AsusData.OPENVPN_SERVER)
    assert server.endpoint == (
        [Endpoint.VPN, Endpoint.DEVICEMAP] if merlin else [Endpoint.HOOK]
    )
    assert bool(server.request) is not merlin
    vpnc = modern._where_to_get_data(AsusData.VPNC)
    assert (("get_vpnc_status", "") in vpnc.request) is not merlin
    assert _data_defaults() == defaults
    assert state_module.AsusStateMap == state_defaults


def test_instance_maps_are_deep_copies(router: AsusRouter) -> None:
    """Nested finder mutations leave peers and defaults intact."""

    other = AsusRouter("other", "user", "password")
    defaults = _data_defaults()
    state_defaults = state_module.AsusStateMap.copy()
    router._data_map[AsusData.CPU].request.append(("router_only", ""))
    router._data_map[AsusData.CPU].endpoint.clear()
    router._state_map[AsusState.OPENVPN_CLIENT] = AsusData.VPNC

    assert vars(other._data_map[AsusData.CPU]) == defaults[AsusData.CPU]
    assert other._state_map == state_defaults
    assert _data_defaults() == defaults
    assert state_module.AsusStateMap == state_defaults


@pytest.mark.parametrize(
    "endpoints",
    [
        {
            Endpoint.FIRMWARE_NOTE: False,
            Endpoint.FIRMWARE_NOTE_AIMESH: False,
        },
        {Endpoint.FIRMWARE_NOTE: None, Endpoint.FIRMWARE_NOTE_AIMESH: False},
        {Endpoint.HOOK: True},
    ],
)
def test_all_unavailable_endpoints_are_filtered_without_mutation(
    router: AsusRouter, endpoints: dict
) -> None:
    """Filter both unavailable notes without pruning stored or peer finders."""

    other = AsusRouter("other", "user", "password")
    other._identity = AsusDevice()
    router._identity = AsusDevice(endpoints=endpoints)
    original = [Endpoint.FIRMWARE_NOTE, Endpoint.FIRMWARE_NOTE_AIMESH]

    assert router._where_to_get_data(AsusData.FIRMWARE_NOTE).endpoint == []
    assert (
        other._where_to_get_data(AsusData.FIRMWARE_NOTE).endpoint == original
    )
    assert (
        data_finder.ASUSDATA_MAP[AsusData.FIRMWARE_NOTE].endpoint == original
    )
    assert router._data_map[AsusData.FIRMWARE_NOTE].endpoint == original
    router._identity.endpoints = dict.fromkeys(original, True)
    assert (
        router._where_to_get_data(AsusData.FIRMWARE_NOTE).endpoint == original
    )


@pytest.mark.parametrize(
    ("datatype", "origin"),
    [
        (AsusData.NETWORK, AsusData.CPU),
        (AsusData.WLAN, AsusData.WLAN),
        (AsusData.CLIENTS, AsusData.CLIENTS),
    ],
)
def test_filtered_finders_preserve_configuration(
    router: AsusRouter, datatype: AsusData, origin: AsusData
) -> None:
    """Filtering aliases preserves request configuration and merge mode."""

    router._identity = AsusDevice(
        endpoints={Endpoint.HOOK: True, Endpoint.UPDATE_CLIENTS: True}
    )
    finder = router._where_to_get_data(datatype)
    defaults = _data_defaults()
    expected = deepcopy(defaults[origin])
    expected["endpoint"] = (
        [Endpoint.UPDATE_CLIENTS]
        if datatype == AsusData.CLIENTS
        else [Endpoint.HOOK]
    )
    assert vars(finder) == expected
    finder.endpoint.clear()
    finder.request.append(("local_request", ""))

    assert vars(router._where_to_get_data(datatype)) == expected
    assert _data_defaults() == defaults


@pytest.mark.parametrize("modern_first", [False, True])
@pytest.mark.parametrize(
    "vpn_state", [AsusVPNC.ON, AsusOVPNClient.ON, AsusWireGuardClient.ON]
)
async def test_vpn_writes_use_each_identity_map(
    monkeypatch: pytest.MonkeyPatch, modern_first: bool, vpn_state: AsusState
) -> None:
    """A legacy router cannot disable VPN Fusion reads or state dispatch."""

    legacy = AsusRouter("legacy", "user", "password")
    modern = AsusRouter("modern", "user", "password")
    order = (modern, legacy) if modern_first else (legacy, modern)
    monkeypatch.setattr(
        "asusrouter.asusrouter.collect_identity",
        AsyncMock(
            side_effect=[_identity(modern=item is modern) for item in order]
        ),
    )
    for item in order:
        await item.async_get_identity()

    clientlist = "<profile>OpenVPN>1>username>password>0"
    processed = {
        AsusData.VPNC: {
            AsusVPNType.OPENVPN: {1: {"vpnc_unit": 1}},
            AsusVPNType.WIREGUARD: {1: {"vpnc_unit": 1}},
        },
        AsusData.VPNC_CLIENTLIST: clientlist,
    }
    load = AsyncMock(return_value={})
    service = AsyncMock(return_value=True)
    sleep = AsyncMock()
    monkeypatch.setattr(modern, "async_api_load", load)
    monkeypatch.setattr(modern, "async_run_service", service)
    monkeypatch.setattr(
        "asusrouter.asusrouter.process", lambda *args: processed
    )
    monkeypatch.setattr("asusrouter.asusrouter.asyncio.sleep", sleep)

    assert await modern.async_set_state(vpn_state, id=1, vpnc_unit=1)
    assert load.await_count == 2
    assert all(call.args[0] == Endpoint.HOOK for call in load.await_args_list)
    assert "get_vpnc_status()" in load.await_args.args[1]
    service.assert_awaited_once_with(
        service="restart_vpnc",
        arguments={
            "vpnc_unit": 1,
            "vpnc_clientlist": "<profile>OpenVPN>1>username>password>1",
        },
        apply=True,
        expect_modify=False,
    )
    sleep.assert_awaited_once_with(1)

    legacy_service = AsyncMock(return_value=True)
    legacy_load = AsyncMock()
    monkeypatch.setattr(legacy, "async_run_service", legacy_service)
    monkeypatch.setattr(legacy, "async_api_load", legacy_load)
    assert await legacy.async_set_state(AsusOVPNClient.ON, id=1)
    legacy_service.assert_awaited_once_with(
        service="start_vpnclient1",
        arguments={"id": 1},
        apply=True,
        expect_modify=False,
    )
    legacy_load.assert_not_awaited()
    assert legacy._state[AsusData.OPENVPN_CLIENT].data == {"state": True}


async def test_state_callback_uses_instance_map(router: AsusRouter) -> None:
    """Select command callbacks using the router's state mapping."""

    router._state_map[AsusState.LED] = AsusData.AURA
    assert (
        await router._async_get_state_callback(AsusLED.ON)
        == router.async_api_command
    )


async def test_state_save_uses_instance_map(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful writes update the datatype selected by the instance."""

    router._identity = _identity(modern=False)
    router._state_map[AsusState.OPENVPN_CLIENT] = AsusData.OPENVPN_SERVER
    monkeypatch.setattr(
        router, "async_run_service", AsyncMock(return_value=True)
    )

    assert await router.async_set_state(AsusOVPNClient.ON, id=1)
    assert router._state[AsusData.OPENVPN_SERVER].data == {"state": True}
    assert AsusData.OPENVPN_CLIENT not in router._state


async def test_reboot_restoration_uses_instance_map(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reboot restoration dispatches only states enabled for this router."""

    router._identity = AsusDevice(endpoints={Endpoint.SYSINFO: True})
    router._state[AsusData.LED] = AsusDataState(data={"state": AsusLED.OFF})
    router._state_map[AsusState.LED] = None
    service = AsyncMock(return_value=True)
    monkeypatch.setattr(router, "async_run_service", service)

    await router._async_handle_reboot()
    service.assert_not_awaited()

    router._state_map[AsusState.LED] = AsusData.LED
    await router._async_handle_reboot()
    assert service.await_count == 2


@pytest.mark.parametrize("explicit", [False, True])
def test_data_finder_helpers_support_optional_maps(explicit: bool) -> None:
    """Adding, aliasing and removing rules supports old and explicit calls."""

    defaults = _data_defaults()
    target = {} if explicit else data_finder.ASUSDATA_MAP
    kwargs = {"data_map": target} if explicit else {}
    finder = AsusDataFinder(Endpoint.HOOK)
    data_finder.add_conditional_data_rule(AsusData.VPNC, finder, **kwargs)
    data_finder.add_conditional_data_alias(
        AsusData.OPENVPN_CLIENT, AsusData.VPNC, **kwargs
    )
    assert target[AsusData.VPNC] is finder
    assert target[AsusData.OPENVPN_CLIENT] == AsusData.VPNC
    data_finder.remove_data_rule(AsusData.VPNC, **kwargs)
    data_finder.remove_data_rule(AsusData.VPNC, **kwargs)
    assert AsusData.VPNC not in target
    if explicit:
        assert _data_defaults() == defaults


@pytest.mark.parametrize("explicit", [False, True])
async def test_state_helpers_support_optional_maps(explicit: bool) -> None:
    """State lookup, dispatch, caching and restoration accept explicit maps."""

    defaults = state_module.AsusStateMap.copy()
    target = {} if explicit else state_module.AsusStateMap
    kwargs = {"state_map": target} if explicit else {}
    if explicit:
        assert state_module.get_datatype(AsusLED.ON, **kwargs) is None
        callback = AsyncMock()
        assert not await state_module.set_state(callback, AsusLED.ON, **kwargs)
        await state_module.keep_state(callback, AsusLED.OFF, **kwargs)
        library = {AsusData.LED: AsusDataState()}
        state_module.save_state(AsusLED.ON, library, **kwargs)
        assert library[AsusData.LED].data is None
        callback.assert_not_awaited()

    state_module.add_conditional_state(AsusState.LED, AsusData.LED, **kwargs)
    assert target[AsusState.LED] == AsusData.LED
    assert state_module.get_datatype(AsusLED.ON, **kwargs) == AsusData.LED
    callback = AsyncMock(return_value=True)
    assert await state_module.set_state(callback, AsusLED.ON, **kwargs)
    callback.assert_awaited_once()
    library = {AsusData.LED: AsusDataState()}
    state_module.save_state(AsusLED.ON, library, 5, 2, **kwargs)
    assert library[AsusData.LED].data == {2: {"state": True}}
    await state_module.keep_state(
        callback,
        AsusLED.OFF,
        identity=AsusDevice(endpoints={Endpoint.SYSINFO: True}),
        **kwargs,
    )
    assert callback.await_count == 3
    if explicit:
        assert state_module.AsusStateMap == defaults
