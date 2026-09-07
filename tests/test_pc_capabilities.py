"""Tests for parental-control router capabilities."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from asusrouter.asusrouter import AsusRouter
from asusrouter.error import AsusRouterConnectionError, AsusRouterDataError
from asusrouter.modules.parental_control import (
    KEY_PC_MAX_ENTRIES,
    KEY_PC_MAX_RULES,
    KEY_PC_SCHED_VERSION,
    ParentalControlCapabilities,
    read_pc_capabilities,
)


def test_read_pc_capabilities() -> None:
    """Read each reported capability from positive integers."""

    assert read_pc_capabilities(
        {
            KEY_PC_MAX_RULES: 32,
            KEY_PC_MAX_ENTRIES: 256,
            KEY_PC_SCHED_VERSION: 3,
        }
    ) == ParentalControlCapabilities(
        max_rules=32,
        max_entries=256,
        sched_version=3,
    )


def test_read_pc_capabilities_numeric_strings() -> None:
    """Accept decimal-integer strings reported by router firmware."""

    assert read_pc_capabilities(
        {
            KEY_PC_MAX_RULES: "32",
            KEY_PC_MAX_ENTRIES: "256",
            KEY_PC_SCHED_VERSION: "3",
        }
    ) == ParentalControlCapabilities(
        max_rules=32,
        max_entries=256,
        sched_version=3,
    )


@pytest.mark.parametrize(
    "missing_key",
    [KEY_PC_MAX_RULES, KEY_PC_MAX_ENTRIES, KEY_PC_SCHED_VERSION],
)
def test_read_pc_capabilities_independently_absent(missing_key: str) -> None:
    """Treat an independently absent capability as unknown."""

    data = {
        KEY_PC_MAX_RULES: 32,
        KEY_PC_MAX_ENTRIES: 256,
        KEY_PC_SCHED_VERSION: 3,
    }
    del data[missing_key]
    result = read_pc_capabilities(data)

    expected = {
        KEY_PC_MAX_RULES: 32,
        KEY_PC_MAX_ENTRIES: 256,
        KEY_PC_SCHED_VERSION: 3,
    }
    expected[missing_key] = None
    assert result == ParentalControlCapabilities(
        max_rules=expected[KEY_PC_MAX_RULES],
        max_entries=expected[KEY_PC_MAX_ENTRIES],
        sched_version=expected[KEY_PC_SCHED_VERSION],
    )


@pytest.mark.parametrize("invalid", [True, False, 0, -1, "0", "-1", "abc"])
def test_read_pc_capabilities_rejects_invalid_values(invalid: object) -> None:
    """Reject booleans, non-positive values, and non-numeric strings."""

    assert (
        read_pc_capabilities(
            {
                KEY_PC_MAX_RULES: invalid,
                KEY_PC_MAX_ENTRIES: invalid,
                KEY_PC_SCHED_VERSION: invalid,
            }
        )
        == ParentalControlCapabilities()
    )


@pytest.mark.asyncio
async def test_async_get_parental_control_capabilities(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Send the unprefixed hook and read only its nested result object."""

    api_hook = AsyncMock(
        return_value={
            KEY_PC_MAX_RULES: 999,
            "get_ui_support": {
                KEY_PC_MAX_RULES: "32",
                KEY_PC_MAX_ENTRIES: "256",
                KEY_PC_SCHED_VERSION: "3",
            },
        }
    )
    monkeypatch.setattr(router, "async_api_hook", api_hook)

    result = await router.async_get_parental_control_capabilities()

    api_hook.assert_awaited_once_with("get_ui_support()")
    assert result == ParentalControlCapabilities(32, 256, 3)


@pytest.mark.asyncio
async def test_async_get_parental_control_capabilities_missing_keys(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return unknown capabilities when UI support omits all limit keys."""

    monkeypatch.setattr(
        router,
        "async_api_hook",
        AsyncMock(return_value={"get_ui_support": {"other": 1}}),
    )

    assert (
        await router.async_get_parental_control_capabilities()
        == ParentalControlCapabilities()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type", [AsusRouterConnectionError, AsusRouterDataError]
)
async def test_async_get_parental_control_capabilities_propagates_errors(
    router: AsusRouter,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    """Propagate transport and data errors unchanged."""

    error = error_type("failed")
    monkeypatch.setattr(router, "async_api_hook", AsyncMock(side_effect=error))

    with pytest.raises(error_type) as raised:
        await router.async_get_parental_control_capabilities()

    assert raised.value is error
