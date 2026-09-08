"""Regression tests for safe dead-code cleanup."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import importlib
import importlib.util
from typing import Any, get_type_hints
from unittest.mock import AsyncMock, Mock

import pytest

from asusrouter.asusrouter import AsusRouter
from asusrouter.connection import Connection
from asusrouter.const import RequestType
from asusrouter.modules import parental_control
from asusrouter.modules.data_finder import AsusDataFinder
from asusrouter.modules.endpoint import Endpoint, get_request_type
from asusrouter.tools.types import ARCallbackType


@pytest.mark.parametrize(
    ("module_name", "attribute"),
    [
        ("asusrouter.const", "AR_CALL_SET_STATE"),
        ("asusrouter.const", "DEFAULT_SLEEP_TIME"),
        ("asusrouter.error", "AsusRouterSessionError"),
        ("asusrouter.modules.client", "CLIENT_MAP"),
        ("asusrouter.modules.color", "DEFAULT_COLOR_SCALE"),
        ("asusrouter.modules.const", "MapReplaceType"),
        ("asusrouter.modules.endpoint.devicemap", "read_special"),
        (
            "asusrouter.modules.endpoint.devicemap_const",
            "DEVICEMAP_SPECIAL",
        ),
        ("asusrouter.modules.ports", "PORT_SUBTYPE"),
        ("asusrouter.modules.support", "ARSupportSourceUniversal"),
        ("asusrouter.modules.wifi", "ARWiFiBand"),
        ("asusrouter.modules.wifi", "ARWiFiFrequency"),
    ],
)
def test_unused_attribute_removed(module_name: str, attribute: str) -> None:
    """Unused module attributes stay removed."""

    module = importlib.import_module(module_name)

    assert not hasattr(module, attribute)


def test_unreachable_rgb_endpoint_module_removed() -> None:
    """The unreachable RGB endpoint has no importable module."""

    assert importlib.util.find_spec("asusrouter.modules.endpoint.rgb") is None


@pytest.mark.parametrize(
    "attribute", ["_async_handle_exception", "_check_prerequisites"]
)
def test_noop_router_helpers_removed(attribute: str) -> None:
    """No-op and re-raise-only router helpers stay removed."""

    assert not hasattr(AsusRouter, attribute)


def _traceback_function_names(exception: BaseException) -> list[str]:
    """Return function names from an exception traceback."""

    names = []
    traceback = exception.__traceback__
    while traceback is not None:
        names.append(traceback.tb_frame.f_code.co_name)
        traceback = traceback.tb_next
    return names


@pytest.mark.asyncio
async def test_router_connect_propagates_without_reraising(
    router: AsusRouter,
) -> None:
    """Connection errors keep a single AsusRouter.async_connect frame."""

    router._connection = Mock()
    router._connection.async_connect = AsyncMock(side_effect=RuntimeError)

    with pytest.raises(RuntimeError) as exc_info:
        await router.async_connect()

    names = _traceback_function_names(exc_info.value)
    assert names.count("async_connect") == 1


@pytest.mark.asyncio
async def test_router_disconnect_propagates_without_helper(
    router: AsusRouter,
) -> None:
    """Disconnect errors propagate without a re-raise-only helper frame."""

    router._connection = Mock()
    router._connection.async_disconnect = AsyncMock(side_effect=RuntimeError)

    with pytest.raises(RuntimeError) as exc_info:
        await router.async_disconnect()

    names = _traceback_function_names(exc_info.value)
    assert "_async_handle_exception" not in names


@pytest.mark.asyncio
async def test_api_query_uses_shared_request_type_helper(
    router: AsusRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API queries delegate request-type selection to the shared helper."""

    router._connection = Mock()
    router._connection.async_query = AsyncMock(return_value=(200, {}, "body"))
    request_type = Mock(return_value=RequestType.GET)
    monkeypatch.setattr("asusrouter.asusrouter.get_request_type", request_type)

    result = await router.async_api_query(Endpoint.DEVICEMAP)

    assert result == (200, {}, "body")
    request_type.assert_called_once_with(Endpoint.DEVICEMAP)
    router._connection.async_query.assert_awaited_once_with(
        Endpoint.DEVICEMAP,
        None,
        request_type=RequestType.GET,
    )


def test_capability_parser_reuses_safe_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capability parsing uses the shared safe integer converter."""

    safe_int = Mock(wraps=parental_control.safe_int)
    monkeypatch.setattr(parental_control, "safe_int", safe_int)

    parental_control.read_pc_capabilities(
        {
            parental_control.KEY_PC_MAX_RULES: "32",
            parental_control.KEY_PC_MAX_ENTRIES: "256",
            parental_control.KEY_PC_SCHED_VERSION: "3",
        }
    )

    assert safe_int.call_count == 3


def test_cleanup_type_hints_are_specific() -> None:
    """Cleaned-up callables expose concrete parameterized annotations."""

    assert (
        get_type_hints(Connection._make_request)["return"]
        == tuple[int, Mapping[str, str], str]
    )
    assert get_type_hints(AsusRouter.async_api_load)["retry"] is int
    assert (
        get_type_hints(AsusRouter._async_get_state_callback)["return"]
        == ARCallbackType
        == Callable[..., Awaitable[Any]]
    )
    assert get_type_hints(AsusDataFinder.__init__)["method"] == (
        Callable[..., str | None] | None
    )
    assert get_type_hints(get_request_type)["return"] is RequestType
