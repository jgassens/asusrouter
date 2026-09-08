"""Regression tests for malformed router response handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asusrouter.asusrouter import AsusRouter
from asusrouter.error import AsusRouterDataError
from asusrouter.modules.data import AsusData, AsusDataState
from asusrouter.modules.endpoint import Endpoint, process
from asusrouter.modules.identity import AsusDevice
from asusrouter.modules.parental_control import (
    KEY_PC_BLOCK_ALL,
    KEY_PC_MAC,
    KEY_PC_NAME,
    KEY_PC_STATE,
    KEY_PC_TIMEMAP,
    KEY_PC_TYPE,
    PCRuleType,
)
from asusrouter.modules.port_forwarding import (
    KEY_PORT_FORWARDING_LIST,
    KEY_PORT_FORWARDING_STATE,
)
from asusrouter.tools.readers import read_json_content


@pytest.mark.parametrize(
    "error_type", [KeyError, IndexError, TypeError, RecursionError]
)
def test_endpoint_process_contains_parser_exceptions(
    error_type: type[Exception],
) -> None:
    """Structural parser failures are converted to an omitted datatype."""

    module = MagicMock()
    module.process.side_effect = error_type("malformed")
    with patch("asusrouter.modules.endpoint._get_module", return_value=module):
        assert process(Endpoint.HOOK, {}) == {}


@pytest.mark.parametrize(
    ("datatype", "payload"),
    [
        (
            AsusData.VPNC,
            {"vpnc_clientlist": "", "get_vpnc_status": "2>0>1"},
        ),
        (
            AsusData.WAN,
            {"get_wan_unit": "", "link_internet": "2"},
        ),
        (
            AsusData.PORT_FORWARDING,
            {
                KEY_PORT_FORWARDING_STATE: "1",
                KEY_PORT_FORWARDING_LIST: "&#60name&#628080",
            },
        ),
        (
            AsusData.PARENTAL_CONTROL,
            {
                KEY_PC_STATE: "1",
                KEY_PC_BLOCK_ALL: "0",
                KEY_PC_MAC: "00:00:00:00:00:01",
                KEY_PC_NAME: "device",
                KEY_PC_TIMEMAP: "valid",
                KEY_PC_TYPE: "7",
            },
        ),
    ],
    ids=["unknown-vpnc-id", "missing-wan-unit", "short-vts-row", "pc-type"],
)
async def test_malformed_hook_payload_returns_controlled_partial_result(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    datatype: AsusData,
    payload: dict[str, str],
) -> None:
    """Known malformed hook rows never escape through async_get_data."""

    router._identity = AsusDevice()
    monkeypatch.setattr(
        router, "async_api_load", AsyncMock(return_value=payload.copy())
    )

    result = await router.async_get_data(datatype, force=True)

    assert result is not None
    if datatype == AsusData.PARENTAL_CONTROL:
        rule = result["rules"][payload[KEY_PC_MAC]]
        assert rule.type == PCRuleType.UNKNOWN
    elif datatype == AsusData.PORT_FORWARDING:
        assert result["rules"] == []


def test_short_sysinfo_arrays_are_skipped() -> None:
    """Every fixed-width sysinfo array is length-checked before indexing."""

    result = process(
        Endpoint.SYSINFO,
        {
            "wlc_0_arr": ["1"],
            "conn_stats_arr": ["1"],
            "mem_stats_arr": ["1", "2"],
            "cpu_stats_arr": ["1"],
        },
    )

    assert result[AsusData.SYSINFO] == {
        "wlan": {},
        "connections": {},
        "memory": {},
        "load_avg": {},
    }


def test_short_port_status_row_is_skipped() -> None:
    """Malformed port identifiers and values do not discard valid groups."""

    result = process(
        Endpoint.PORT_STATUS,
        {"port_info": {"00:00:00:00:00:01": {"": []}}},
    )

    assert result[AsusData.PORTS] == {"00:00:00:00:00:01": {}}


def _deep_json(depth: int = 2000) -> str:
    """Build valid JSON beyond CPython's safe recursive decoder depth."""

    return '{"nested":' * depth + "0" + "}" * depth


def test_deep_json_raises_controlled_data_error() -> None:
    """Excessive JSON nesting is reported as a library data error."""

    with pytest.raises(AsusRouterDataError, match="nested too deeply"):
        read_json_content(_deep_json())


async def test_deep_json_ordinary_read_returns_cached_data(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parser data error reaches the ordinary-read cache fallback."""

    own_mac = "00:00:00:00:00:01"
    previous: dict[str, Any] = {own_mac: {"cached": True}}
    router._identity = AsusDevice(
        mac=own_mac,
        endpoints={
            Endpoint.PORT_STATUS: True,
            Endpoint.ETHERNET_PORTS: False,
        },
    )
    router._state[AsusData.PORTS] = AsusDataState(
        data=previous,
        timestamp=datetime.now(UTC) - timedelta(days=1),
    )
    monkeypatch.setattr(
        router,
        "async_api_query",
        AsyncMock(return_value=(200, {}, _deep_json())),
    )

    assert (
        await router.async_get_data(AsusData.PORTS, device="all") is previous
    )
    with pytest.raises(AsusRouterDataError, match="nested too deeply"):
        await router.async_get_data(AsusData.PORTS, force=True, device="all")
