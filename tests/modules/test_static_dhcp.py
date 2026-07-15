"""Tests for the static DHCP module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from asusrouter.asusrouter import AsusRouter
from asusrouter.error import AsusRouterDataError
from asusrouter.modules.static_dhcp import (
    KEY_STATIC_DHCP_LIST,
    KEY_STATIC_DHCP_STATE,
    LAYOUT_LEGACY,
    SERVICE_STATIC_DHCP_APPLY,
    StaticDHCPLease,
    compile_static_dhcp_leases,
    normalize_static_dhcp_mac,
    parse_static_dhcp_leases,
)


def test_parse_static_dhcp_leases() -> None:
    """Test parse_static_dhcp_leases."""

    result = parse_static_dhcp_leases(
        "<AA:BB:CC:DD:EE:FF>192.168.1.2>1.1.1.1>printer"
        "<11:22:33:44:55:66>192.168.1.3"
    )

    assert result == [
        StaticDHCPLease(
            mac="AA:BB:CC:DD:EE:FF",
            ip="192.168.1.2",
            dns="1.1.1.1",
            hostname="printer",
        ),
        StaticDHCPLease(mac="11:22:33:44:55:66", ip="192.168.1.3"),
    ]


def test_parse_static_dhcp_leases_rejects_malformed_row() -> None:
    """Reject the complete response rather than silently dropping a row."""

    with pytest.raises(ValueError, match="Unrecognized"):
        parse_static_dhcp_leases("<AA:BB:CC:DD:EE:FF>192.168.1.2<missing-ip>")


def test_parse_static_dhcp_leases_preserves_legacy_hostname() -> None:
    """Treat a non-IP third column as the stock legacy hostname layout."""

    result = parse_static_dhcp_leases("<AA:BB:CC:DD:EE:FF>192.168.1.2>printer")

    assert result == [
        StaticDHCPLease(
            mac="AA:BB:CC:DD:EE:FF",
            ip="192.168.1.2",
            hostname="printer",
            layout=LAYOUT_LEGACY,
        )
    ]
    assert compile_static_dhcp_leases(result)[KEY_STATIC_DHCP_LIST] == (
        "<AA:BB:CC:DD:EE:FF>192.168.1.2>printer"
    )


@pytest.mark.parametrize("content", [None, ""])
def test_parse_static_dhcp_leases_empty(content: str | None) -> None:
    """Test parse_static_dhcp_leases with empty data."""

    assert parse_static_dhcp_leases(content) == []


def test_parse_static_dhcp_leases_escaped() -> None:
    """Test parse_static_dhcp_leases with escaped ASUS delimiters."""

    result = parse_static_dhcp_leases(
        "&#60AA:BB:CC:DD:EE:FF&#62192.168.1.2&#621.1.1.1&#62printer"
    )

    assert result == [
        StaticDHCPLease(
            mac="AA:BB:CC:DD:EE:FF",
            ip="192.168.1.2",
            dns="1.1.1.1",
            hostname="printer",
        )
    ]


def test_compile_static_dhcp_leases() -> None:
    """Test compile_static_dhcp_leases."""

    result = compile_static_dhcp_leases(
        [
            StaticDHCPLease(
                mac="aabbccddeeff",
                ip="192.168.1.2",
                dns="1.1.1.1",
                hostname="printer",
            ),
            StaticDHCPLease(mac="11:22:33:44:55:66", ip="192.168.1.3"),
        ]
    )

    assert result == {
        KEY_STATIC_DHCP_LIST: (
            "<AA:BB:CC:DD:EE:FF>192.168.1.2>1.1.1.1>printer"
            "<11:22:33:44:55:66>192.168.1.3"
        ),
        KEY_STATIC_DHCP_STATE: 1,
    }


def test_compile_static_dhcp_leases_empty() -> None:
    """Test compile_static_dhcp_leases with an empty list."""

    assert compile_static_dhcp_leases([]) == {
        KEY_STATIC_DHCP_LIST: "",
        KEY_STATIC_DHCP_STATE: 0,
    }


def test_compile_static_dhcp_leases_accepts_iterable() -> None:
    """Compile any iterable accepted by the public type annotation."""

    assert compile_static_dhcp_leases(()) == {
        KEY_STATIC_DHCP_LIST: "",
        KEY_STATIC_DHCP_STATE: 0,
    }


def test_compile_static_dhcp_leases_with_hostname_without_dns() -> None:
    """Test compile_static_dhcp_leases preserves empty DNS before hostname."""

    result = compile_static_dhcp_leases(
        [
            StaticDHCPLease(
                mac="AA:BB:CC:DD:EE:FF",
                ip="192.168.1.2",
                hostname="printer",
            )
        ]
    )

    assert result == {
        KEY_STATIC_DHCP_LIST: "<AA:BB:CC:DD:EE:FF>192.168.1.2>>printer",
        KEY_STATIC_DHCP_STATE: 1,
    }


def test_compile_static_dhcp_leases_preserves_literal_none_hostname() -> None:
    """Preserve a hostname that happens to contain the text None."""

    result = compile_static_dhcp_leases(
        [
            StaticDHCPLease(
                mac="00:11:22:33:44:55",
                ip="192.168.1.2",
                hostname="None",
            )
        ]
    )

    assert result[KEY_STATIC_DHCP_LIST] == (
        "<00:11:22:33:44:55>192.168.1.2>>None"
    )


@pytest.mark.parametrize(
    "leases",
    [
        [
            StaticDHCPLease(mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.2"),
            StaticDHCPLease(mac="aabbccddeeff", ip="192.168.1.3"),
        ],
        [
            StaticDHCPLease(mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.2"),
            StaticDHCPLease(mac="11:22:33:44:55:66", ip="192.168.1.2"),
        ],
    ],
)
def test_compile_static_dhcp_leases_duplicate(
    leases: list[StaticDHCPLease],
) -> None:
    """Test compile_static_dhcp_leases rejects duplicates."""

    with pytest.raises(ValueError, match="Duplicate static DHCP lease"):
        compile_static_dhcp_leases(leases)


@pytest.mark.parametrize(
    "lease",
    [
        StaticDHCPLease(mac="AA:BB:CC:DD:EE:FF", ip="2001:db8::1"),
        StaticDHCPLease(mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.2", dns="x"),
        StaticDHCPLease(
            mac="AA:BB:CC:DD:EE:FF",
            ip="192.168.1.2",
            hostname="bad>host",
        ),
    ],
)
def test_compile_static_dhcp_leases_invalid(
    lease: StaticDHCPLease,
) -> None:
    """Test compile_static_dhcp_leases rejects invalid fields."""

    with pytest.raises(ValueError, match="Static DHCP|Invalid IP address"):
        compile_static_dhcp_leases([lease])


def test_normalize_static_dhcp_mac() -> None:
    """Test normalize_static_dhcp_mac."""

    assert normalize_static_dhcp_mac("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_static_dhcp_mac("001122334455") == "00:11:22:33:44:55"


@pytest.mark.asyncio
async def test_async_get_static_dhcp_leases(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test async_get_static_dhcp_leases."""

    async_api_hook = AsyncMock(
        return_value={
            KEY_STATIC_DHCP_STATE: "0",
            KEY_STATIC_DHCP_LIST: (
                "<AA:BB:CC:DD:EE:FF>192.168.1.2>1.1.1.1>printer"
            ),
        }
    )
    monkeypatch.setattr(router, "async_api_hook", async_api_hook)

    result = await router.async_get_static_dhcp_leases()

    async_api_hook.assert_awaited_once_with(
        "nvram_get(dhcp_static_x);nvram_get(dhcp_staticlist);"
    )
    assert result == [
        StaticDHCPLease(
            mac="AA:BB:CC:DD:EE:FF",
            ip="192.168.1.2",
            dns="1.1.1.1",
            hostname="printer",
        )
    ]


@pytest.mark.asyncio
async def test_async_get_static_dhcp_leases_rejects_incomplete_response(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not reinterpret a missing list as an authoritative empty list."""

    monkeypatch.setattr(
        router,
        "async_api_hook",
        AsyncMock(return_value={KEY_STATIC_DHCP_STATE: "1"}),
    )

    with pytest.raises(AsusRouterDataError, match="Incomplete"):
        await router.async_get_static_dhcp_leases()


@pytest.mark.asyncio
async def test_async_apply_static_dhcp_leases(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test async_apply_static_dhcp_leases."""

    async_run_service = AsyncMock(return_value=True)
    monkeypatch.setattr(router, "async_run_service", async_run_service)

    result = await router.async_apply_static_dhcp_leases(
        [StaticDHCPLease(mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.2")]
    )

    assert result is True
    async_run_service.assert_awaited_once_with(
        service=SERVICE_STATIC_DHCP_APPLY,
        arguments={
            KEY_STATIC_DHCP_LIST: "<AA:BB:CC:DD:EE:FF>192.168.1.2",
            KEY_STATIC_DHCP_STATE: 1,
        },
        apply=True,
    )


@pytest.mark.asyncio
async def test_async_set_static_dhcp_lease(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test async_set_static_dhcp_lease."""

    async_get_static_dhcp_snapshot = AsyncMock(
        return_value=(
            True,
            [StaticDHCPLease(mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.2")],
        )
    )
    async_apply_static_dhcp_leases = AsyncMock(return_value=True)
    monkeypatch.setattr(
        router,
        "_async_get_static_dhcp_snapshot",
        async_get_static_dhcp_snapshot,
    )
    monkeypatch.setattr(
        router,
        "async_apply_static_dhcp_leases",
        async_apply_static_dhcp_leases,
    )

    result = await router.async_set_static_dhcp_lease(
        "aabbccddeeff",
        "192.168.1.10",
        hostname="printer",
        dns="1.1.1.1",
    )

    assert result is True
    async_apply_static_dhcp_leases.assert_awaited_once_with(
        [
            StaticDHCPLease(
                mac="AA:BB:CC:DD:EE:FF",
                ip="192.168.1.10",
                dns="1.1.1.1",
                hostname="printer",
            )
        ],
        enabled=True,
    )


@pytest.mark.asyncio
async def test_async_remove_static_dhcp_lease(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test async_remove_static_dhcp_lease."""

    leases = [
        StaticDHCPLease(mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.2"),
        StaticDHCPLease(mac="11:22:33:44:55:66", ip="192.168.1.3"),
    ]
    async_get_static_dhcp_snapshot = AsyncMock(return_value=(True, leases))
    async_apply_static_dhcp_leases = AsyncMock(return_value=True)
    monkeypatch.setattr(
        router,
        "_async_get_static_dhcp_snapshot",
        async_get_static_dhcp_snapshot,
    )
    monkeypatch.setattr(
        router,
        "async_apply_static_dhcp_leases",
        async_apply_static_dhcp_leases,
    )

    result = await router.async_remove_static_dhcp_lease("aabbccddeeff")

    assert result == [
        StaticDHCPLease(mac="11:22:33:44:55:66", ip="192.168.1.3")
    ]
    async_apply_static_dhcp_leases.assert_awaited_once_with(
        result,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_async_remove_static_dhcp_lease_reports_apply_failure(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise when dnsmasq does not accept a removal."""

    leases = [StaticDHCPLease(mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.2")]
    monkeypatch.setattr(
        router,
        "_async_get_static_dhcp_snapshot",
        AsyncMock(return_value=(True, leases)),
    )
    monkeypatch.setattr(
        router,
        "async_apply_static_dhcp_leases",
        AsyncMock(return_value=False),
    )

    with pytest.raises(AsusRouterDataError, match="removal"):
        await router.async_remove_static_dhcp_lease("aabbccddeeff")


@pytest.mark.asyncio
async def test_static_dhcp_updates_are_serialized(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent whole-list updates must not overwrite each other."""

    saved: list[StaticDHCPLease] = []

    async def get_snapshot() -> tuple[bool, list[StaticDHCPLease]]:
        await asyncio.sleep(0)
        return True, list(saved)

    async def apply(
        leases: list[StaticDHCPLease],
        *,
        enabled: bool | None = None,
    ) -> bool:
        del enabled
        await asyncio.sleep(0)
        saved[:] = leases
        return True

    monkeypatch.setattr(
        router, "_async_get_static_dhcp_snapshot", get_snapshot
    )
    monkeypatch.setattr(router, "async_apply_static_dhcp_leases", apply)

    await asyncio.gather(
        router.async_set_static_dhcp_lease("00:11:22:33:44:55", "192.168.1.2"),
        router.async_set_static_dhcp_lease("AA:BB:CC:DD:EE:FF", "192.168.1.3"),
    )

    assert {lease.ip for lease in saved} == {"192.168.1.2", "192.168.1.3"}
