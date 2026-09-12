"""AsusRouter module.

This module contains the main class for interacting with an Asus device.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any, cast

import aiohttp

from asusrouter.config import ARConfigKey as ARConfKey, ARInstanceConfig
from asusrouter.connection import Connection
from asusrouter.connection_config import ARConnectionConfigKey as ARCCKey
from asusrouter.const import (
    AR_CALL_GET_STATE,
    AR_CALL_TRANSLATE_STATE,
    DEFAULT_CACHE_TIME,
    DEFAULT_PORT_HTTP,
    DEFAULT_PORT_HTTPS,
    DEFAULT_RESULT_SUCCESS,
    DEFAULT_TIMEOUT,
)
from asusrouter.error import (
    AsusRouter404Error,
    AsusRouterAccessError,
    AsusRouterConnectionError,
    AsusRouterDataError,
    AsusRouterError,
)
from asusrouter.modules.attributes import AsusRouterAttribute
from asusrouter.modules.data import AsusData, AsusDataState
from asusrouter.modules.data_finder import (
    ASUSDATA_ENDPOINT_APPEND,
    ASUSDATA_MAP,
    ASUSDATA_NVRAM,
    AsusDataFinder,
    AsusDataMerge,
    add_conditional_data_alias,
    add_conditional_data_rule,
    remove_data_rule,
)
from asusrouter.modules.data_transform import (
    transform_clients,
    transform_cpu,
    transform_ethernet_ports,
    transform_network,
    transform_wan,
)
from asusrouter.modules.endpoint import (
    Endpoint,
    EndpointControl,
    EndpointType,
    get_request_type,
    process,
    read,
)
from asusrouter.modules.endpoint.error import AccessError
from asusrouter.modules.firmware import Firmware
from asusrouter.modules.flags import Flag
from asusrouter.modules.identity import AsusDevice, collect_identity
from asusrouter.modules.parental_control import (
    HOOK_PC,
    ParentalControlCapabilities,
    read_pc_capabilities,
)
from asusrouter.modules.service import ServiceResult, async_call_service
from asusrouter.modules.source import (
    ARDataCollection,
    ARDataSource,
    ARDataState,
    ARDataStateDynamic,
    ARDataStateStatic,
    ARDataType,
)
from asusrouter.modules.state import (
    AsusState,
    AsusStateMap,
    add_conditional_state,
    get_datatype,
    keep_state,
    save_state,
    set_state,
)
from asusrouter.modules.static_dhcp import (
    KEY_STATIC_DHCP_LIST,
    KEY_STATIC_DHCP_STATE,
    LAYOUT_LEGACY,
    SERVICE_STATIC_DHCP_APPLY,
    StaticDHCPLease,
    compile_static_dhcp_leases,
    normalize_static_dhcp_lease,
    normalize_static_dhcp_mac,
    parse_static_dhcp_leases,
)
from asusrouter.registry import ARCallableRegistry as ARCallReg
from asusrouter.tools.converters import get_enum_key_by_value, safe_bool
from asusrouter.tools.readers import merge_dicts
from asusrouter.tools.types import ARCallableType, ARCallbackType
from asusrouter.tools.writers import nvram

_LOGGER = logging.getLogger(__name__)

ARDataRequest = ARDataSource | ARDataType | Iterable[ARDataSource | ARDataType]


class AsusRouter:
    """The interface class."""

    def __init__(  # noqa: PLR0913
        self,
        hostname: str,
        username: str,
        password: str,
        port: int | None = None,
        use_ssl: bool = False,
        cache_time: float | None = None,
        session: aiohttp.ClientSession | None = None,
        dumpback: Callable[..., Awaitable[None]] | None = None,
        config: dict[ARConfKey, Any] | None = None,
        connection_config: dict[ARCCKey, Any] | None = None,
    ):
        """Initialize the interface."""

        _LOGGER.debug("Initializing a new interface to `%s`", hostname)

        # Initialize configs
        _LOGGER.debug("Setting up AR instance config: %s", config)
        self._config = ARInstanceConfig(defaults=config)

        # Set the cache time
        self._cache_time = cache_time or DEFAULT_CACHE_TIME
        self._cache_threshold = timedelta(seconds=self._cache_time)

        # Set the host
        self._hostname: str = hostname

        # Set the device identity
        self._identity: AsusDevice | None = None
        self._data_map = deepcopy(ASUSDATA_MAP)
        self._state_map = deepcopy(AsusStateMap)
        self._state: dict[AsusData, AsusDataState] = {}
        self._data_states: dict[ARDataSource | ARDataType, ARDataState] = {}
        self._parental_control_lock = asyncio.Lock()
        self._static_dhcp_lock = asyncio.Lock()

        # Set the flags
        self._flags: Flag = Flag()
        # Time for change to take effect before available to fetch
        self._needed_time: int | None = None
        # ID from the last called service
        self._last_id: int | None = None

        # Create an empty connection and save the credentials
        self._connection: Connection | None = None
        self._username = username
        self._password = password
        self._port = port
        self._use_ssl = use_ssl
        self._session = session
        self._dumpback = dumpback
        self._connection_config = connection_config

    # ---------------------------
    # Connection-related methods -->
    # ---------------------------

    async def async_init_connection(self) -> None:
        """Initialize the connection."""

        _LOGGER.debug("Triggered method async_init_connection")

        self._connection = await Connection.create(
            hostname=self._hostname,
            username=self._username,
            password=self._password,
            port=self._port,
            use_ssl=self._use_ssl,
            session=self._session,
            timeout=DEFAULT_TIMEOUT,
            dumpback=self._dumpback,
            config=self._connection_config,
        )

    async def async_del_connection(self) -> None:
        """Delete the connection."""

        _LOGGER.debug("Triggered method async_del_connection")

        # Disconnect from the device if connected
        await self.async_disconnect()

        # Close the connection
        if self._connection:
            await self._connection.async_close()
            self._connection = None

    async def async_connect(self) -> bool:
        """Connect to the device and get its identity."""

        _LOGGER.debug("Triggered method async_connect")

        # Connect to the device
        # Make sure the connection is initialized
        if self._connection is None:
            await self.async_init_connection()

        # Connect to the device
        result = (
            await self._connection.async_connect()
            if self._connection
            else False
        )
        if result is False:
            return False

        # Get the device identity
        return await self.async_get_identity() is not None

    async def async_disconnect(self) -> bool:
        """Disconnect from the device."""

        _LOGGER.debug("Triggered method async_disconnect")

        # Disconnect from the device
        if self._connection:
            await self._connection.async_disconnect()

        return True

    async def _async_drop_connection(self) -> None:
        """Drop the connection.

        In case we know it cannot reply due to our last actions.
        """

        _LOGGER.debug("Triggered method _async_drop_connection")

        if self._connection:
            self._connection.reset_connection()

    async def _async_handle_reboot(self) -> None:
        """Handle reboot."""

        _LOGGER.debug("Triggered method _async_handle_reboot")

        led_state = self._state.get(AsusData.LED)
        if led_state and led_state.data:
            _LOGGER.debug("Restoring LED state")
            await keep_state(
                callback=self.async_run_service,
                states=led_state.data["state"],
                state_map=self._state_map,
                identity=self._identity,
            )

        # Reset the reboot flag
        self._reset_flag("reboot")

    def _reset_flag(self, flag: str) -> None:
        """Reset a flag."""

        _LOGGER.debug("Triggered method _reset_flag")

        # Check that AsusData.FLAGS is available
        if AsusData.FLAGS not in self._state:
            return

        # Get the data from the state
        data = self._state[AsusData.FLAGS].data

        # Check that data is a dict
        if not isinstance(data, dict):
            return

        # Reset the flag
        data.pop(flag, None)

        _LOGGER.debug("Flag `%s` reset", flag)

    # ---------------------------
    # <-- Connection-related methods
    # ---------------------------

    # ---------------------------
    # Identity-related methods -->
    # ---------------------------

    async def async_get_identity(self, force: bool = False) -> AsusDevice:
        """Get the device identity."""

        _LOGGER.debug("Triggered method async_get_identity")

        # Check whether we already have the identity and not forcing a refresh
        if self._identity and not force:
            return self._identity

        # Collect the identity
        self._identity = await collect_identity(
            api_hook=self.async_api_hook,
            api_query=self.async_api_query,
        )

        # Add conditional data rules
        if self._identity:
            firmware = self._identity.firmware
            merlin = self._identity.merlin
            fw_388 = Firmware(major="3.0.0.4", minor=388, build=0)
            # Stock
            if not merlin:
                _LOGGER.debug("Adding conditional rules for stock firmware")
                if fw_388 < firmware:
                    add_conditional_state(
                        AsusState.OPENVPN_CLIENT,
                        AsusData.VPNC,
                        state_map=self._state_map,
                    )
                    add_conditional_state(
                        AsusState.WIREGUARD_CLIENT,
                        AsusData.VPNC,
                        state_map=self._state_map,
                    )
                    add_conditional_data_alias(
                        AsusData.OPENVPN_CLIENT,
                        AsusData.VPNC,
                        data_map=self._data_map,
                    )
                    add_conditional_data_alias(
                        AsusData.WIREGUARD_CLIENT,
                        AsusData.VPNC,
                        data_map=self._data_map,
                    )
                    add_conditional_data_rule(
                        AsusData.OPENVPN_SERVER,
                        AsusDataFinder(
                            Endpoint.HOOK,
                            nvram=ASUSDATA_NVRAM["openvpn_server_388"],
                        ),
                        data_map=self._data_map,
                    )
            # Merlin
            else:
                _LOGGER.debug("Adding conditional rules for Merlin firmware")
                if fw_388 < firmware:
                    add_conditional_data_rule(
                        AsusData.VPNC,
                        AsusDataFinder(
                            Endpoint.HOOK,
                            nvram=ASUSDATA_NVRAM["vpnc"],
                        ),
                        data_map=self._data_map,
                    )
            # Before 388
            if firmware < fw_388:
                # Remove VPNC rules
                remove_data_rule(AsusData.VPNC, data_map=self._data_map)
                remove_data_rule(
                    AsusData.VPNC_CLIENTLIST, data_map=self._data_map
                )
                # Remove WireGuard rules
                remove_data_rule(AsusData.WIREGUARD, data_map=self._data_map)
                remove_data_rule(
                    AsusData.WIREGUARD_CLIENT, data_map=self._data_map
                )
                remove_data_rule(
                    AsusData.WIREGUARD_SERVER, data_map=self._data_map
                )

            # DSL connection
            if self._identity.dsl is False:
                remove_data_rule(AsusData.DSL, data_map=self._data_map)

            # Ookla Speedtest
            if self._identity.ookla is False:
                remove_data_rule(AsusData.SPEEDTEST, data_map=self._data_map)
                # remove_data_rule(AsusData.SPEEDTEST_HISTORY)
                remove_data_rule(
                    AsusData.SPEEDTEST_RESULT, data_map=self._data_map
                )
                # remove_data_rule(AsusData.SPEEDTEST_SERVERS)

        # Return new identity
        return self._identity

    # ---------------------------
    # <-- Identity-related methods
    # ---------------------------

    # ---------------------------
    # Request-related methods -->
    # ---------------------------

    def _get_attribute(
        self, attribute: AsusRouterAttribute | None
    ) -> Any | None:
        """Get an attribute value."""

        if not attribute:
            return None

        match attribute:
            case AsusRouterAttribute.MAC:
                if self._identity:
                    return self._identity.mac
            case AsusRouterAttribute.WLAN_LIST:
                if self._identity:
                    return self._identity.wlan

        return None

    async def _check_flags(self) -> None:
        """Check flags."""

        _LOGGER.debug("Triggered method _check_flags")

        flags: dict[str, bool] = {}

        state_flags = self._state.get(AsusData.FLAGS)
        if isinstance(state_flags, AsusDataState) and isinstance(
            state_flags.data, dict
        ):
            flags = state_flags.data

        if flags.get("reboot", False) is True:
            _LOGGER.debug("Reboot flag is set")
            await self._async_handle_reboot()

    async def async_api_query(
        self, endpoint: EndpointType, payload: str | None = None
    ) -> tuple[int, Mapping[str, str], str]:
        """Query the API endpoint."""

        if endpoint in ASUSDATA_ENDPOINT_APPEND:
            payload = payload or ""
            for key, attribute in ASUSDATA_ENDPOINT_APPEND[endpoint].items():
                if isinstance(attribute, AsusRouterAttribute):
                    value = self._get_attribute(attribute)
                else:
                    value = attribute
                if value:
                    payload += f"{key}={value};"
            # Remove trailing semicolon
            payload = payload[:-1]

        _LOGGER.debug(
            "Querying endpoint `%s` with payload length %d",
            endpoint,
            len(payload) if payload else 0,
        )

        return await self._connection.async_query(
            endpoint, payload, request_type=get_request_type(endpoint)
        )

    async def async_api_load(
        self,
        endpoint: EndpointType,
        request: str = "",
        retry: int = 0,
    ) -> dict[str, Any]:
        """Load API endpoint with optional request."""

        _LOGGER.debug("Triggered method async_api_load: %s", endpoint)

        attempt = retry
        while True:
            # Load the endpoint
            try:
                status, _, content = await self.async_api_query(
                    endpoint, request
                )
            except AsusRouter404Error:
                _LOGGER.debug("Endpoint %s not found", endpoint)
                return {}
            except AsusRouterAccessError as ex:
                is_authorization_error = (
                    len(ex.args) > 1
                    and ex.args[1] == AccessError.AUTHORIZATION
                )
                if not is_authorization_error or attempt >= 1:
                    raise

                # Mark the connection as dropped so the next query logs in.
                await self._async_drop_connection()
                await asyncio.sleep(1 + attempt * 3)
                attempt += 1
                continue

            # Log status
            _LOGGER.debug("Response %s received from %s", status, endpoint)

            # Try to read the content
            try:
                result = read(endpoint, content, config=self.config)
            except json.JSONDecodeError as ex:
                # Not like this is supposed to happen, but just in case
                _LOGGER.debug(
                    "Failed to decode response from endpoint `%s` with "
                    "JSONDecodeError; body length: %d",
                    endpoint,
                    len(content),
                )
                if attempt < 1:
                    attempt += 1
                    continue
                raise AsusRouterDataError(
                    "Something went wrong while reading the content"
                ) from ex

            # Check if we need to drop the connection
            run_service = result.get("run_service", None)
            if run_service in ("restart_httpd", "reboot"):
                await self._async_drop_connection()

            return result

    async def async_api_hook(self, request: str) -> dict[str, Any]:
        """Perform a hook to the device API.

        Hooks are used to fetch data from the device.
        """

        _LOGGER.debug(
            "Querying endpoint `%s` via async_api_hook with request length %d",
            Endpoint.HOOK,
            len(request),
        )

        return await self.async_api_load(
            endpoint=Endpoint.HOOK,
            request=f"hook={request}",
        )

    async def async_api_command(
        self,
        commands: dict[str, str] | None,
        endpoint: EndpointType = EndpointControl.COMMAND,
    ) -> dict[str, Any]:
        """Send a command to the device."""

        _LOGGER.debug(
            "Sending async_api_command to endpoint `%s` with %d command "
            "field(s)",
            endpoint,
            len(commands) if commands else 0,
        )

        return await self.async_api_load(
            endpoint=endpoint,
            request=str(commands),
        )

    # ---------------------------
    # <-- Request-related methods
    # ---------------------------

    def _where_to_get_data(self, datatype: AsusData) -> AsusDataFinder | None:
        """Get the list of endpoints to get data from."""

        _LOGGER.debug("Triggered method _where_to_get_data")

        # Check that device identity is available
        if not self._identity:
            _LOGGER.debug("No device identity available")
            return None

        # Get the map
        data_map = self._data_map.get(datatype)
        # Consider aliases
        while isinstance(data_map, AsusData):
            data_map = self._data_map.get(data_map)
        # Check if we have a map
        if not isinstance(data_map, AsusDataFinder):
            _LOGGER.debug("No map found for %s", datatype)
            return None

        # Filter a copy so availability checks never prune the stored finder.
        finder = deepcopy(data_map)
        finder.endpoint = [
            endpoint
            for endpoint in data_map.endpoint
            if not self._identity.endpoints
            or self._identity.endpoints.get(endpoint) not in (False, None)
        ]

        _LOGGER.debug("Endpoints to check: %s", finder.endpoint)

        return finder

    def _transform_data(
        self, datatype: AsusData, data: Any, **kwargs: Any
    ) -> Any:
        """Transform data if needed."""

        _LOGGER.debug("Triggered method _transform_data for `%s`", datatype)

        if datatype == AsusData.CLIENTS:
            _LOGGER.debug("Transforming clients data")
            return transform_clients(
                data,
                self._state.get(AsusData.CLIENTS),
                aimesh=self._identity.aimesh if self._identity else False,
            )

        if datatype == AsusData.CPU:
            _LOGGER.debug("Transforming CPU data")
            return transform_cpu(data)

        if datatype == AsusData.NETWORK:
            _LOGGER.debug("Transforming network data")
            return transform_network(
                data,
                self._identity.services if self._identity else [],
                self._state.get(AsusData.NETWORK),
                model=self._identity.model if self._identity else None,
            )

        if datatype == AsusData.PORTS:
            _LOGGER.debug("Transforming port data")
            return transform_ethernet_ports(
                data,
                self._identity.mac if self._identity else None,
            )

        if datatype == AsusData.WAN:
            _LOGGER.debug("Transforming WAN data")
            return transform_wan(
                data,
                self._identity.services if self._identity else [],
            )

        return data

    def _drop_data(self, datatype: AsusData, endpoint: EndpointType) -> bool:
        """Check whether data should be dropped.

        This is required for some data obtained from multiple endpoints.
        """

        if not self._identity:
            return False

        if (
            datatype == AsusData.OPENVPN_CLIENT
            and self._identity.merlin is True
        ):
            return endpoint == Endpoint.HOOK

        return False

    async def _check_postrequisites(self, datatype: AsusData) -> None:
        """Check postrequisites after fetching data.

        This method is also used to fetch additional data.
        """

        _LOGGER.debug(
            "Triggered method _check_postrequisites for datatype `%s`",
            datatype,
        )

        # Firmware
        if datatype == AsusData.FIRMWARE:
            # Check if update is available
            firmware = self._state[AsusData.FIRMWARE].data
            if firmware and firmware["state"] is True:
                note_finder = self._where_to_get_data(AsusData.FIRMWARE_NOTE)
                if note_finder is not None and not note_finder.endpoint:
                    _LOGGER.debug(
                        "No firmware release note endpoints available"
                    )
                    return
                # Get release notes
                try:
                    release_note = await self.async_get_data(
                        AsusData.FIRMWARE_NOTE, force=True
                    )
                except (AsusRouterConnectionError, AsusRouterDataError) as ex:
                    _LOGGER.warning(
                        "Unable to fetch firmware release notes: %s", ex
                    )
                    return
                if release_note:
                    firmware.update(release_note)

    def _check_state(self, datatype: AsusData | None) -> None:
        """Make sure the state object is available."""

        _LOGGER.debug("Triggered method _check_state")

        if not datatype:
            return

        # Add state object but make sure it's marked expired
        if datatype not in self._state:
            self._state[datatype] = AsusDataState(
                timestamp=(
                    datetime.now(UTC) - timedelta(seconds=2 * self._cache_time)
                )
            )

    def _return_state(self, datatype: AsusData, **kwargs: Any) -> Any:
        """Return a proper state."""

        _LOGGER.debug("Triggered method _return_state")

        # Get the state
        state = self._state[datatype].data

        if datatype == AsusData.PORTS:
            own_mac = self._identity.mac if self._identity else None

            # Get the device selected
            device = kwargs.get("device")

            match device:
                case None:
                    if isinstance(state, dict):
                        return state.get(own_mac, {})
                    return state
                case "all":
                    return state
                # Case when substate is a MAC address
                case a if isinstance(a, str):
                    if isinstance(state, dict) and a in state:
                        return state[a]
                    return {}

        return state

    def _get_callback_for_state(
        self, source: ARDataSource | ARDataType
    ) -> ARCallbackType | None:
        """Get a callback function for the specified state."""

        return self.async_api_load

    def _create_data_state(self, cllctn: ARDataCollection) -> bool:
        """Create a new data state if does not exist."""

        if not isinstance(cllctn, ARDataCollection) or not cllctn:
            return False

        # Check which items we don't have states for yet
        not_set = [item for item in cllctn if item not in self._data_states]
        if not not_set:
            return True

        for item in not_set:
            # Create a correct state
            state: ARDataState = (
                ARDataStateDynamic(item)
                if isinstance(item, ARDataSource)
                else ARDataStateStatic(item)
            )

            # Find and assign callback and callables for this state
            state.callback = self._get_callback_for_state(item)
            state.state_caller = ARCallReg.get_callable(
                item, name=AR_CALL_GET_STATE
            )
            state.translate_caller = ARCallReg.get_callable(
                item, name=AR_CALL_TRANSLATE_STATE
            )

            self._data_states[item] = state

        return True

    def _get_call_matrix(
        self,
        states: list[ARDataState],
    ) -> dict[tuple[ARCallableType, ARCallbackType], list[ARDataState]]:
        """Get a call matrix for the specified states."""

        matrix: dict[
            tuple[ARCallableType, ARCallbackType], list[ARDataState]
        ] = defaultdict(list)

        for state in states:
            caller = state.state_caller
            callback = state.callback

            if not caller or not callback:
                continue

            matrix[(caller, callback)].append(state)

        return matrix

    def _save_data_state(
        self,
        state: ARDataState,
        data: dict[ARDataSource | ARDataType, Any],
    ) -> None:
        """Save the data state for the specified state."""

        if state.source in data:
            state.update(data[state.source])
            self._data_states[state.source] = state

    def _translate_multidata_raw(
        self,
        data: dict[ARDataSource | ARDataType, Any],
        states: list[ARDataState],
    ) -> None:
        """Save raw multicaller output for states without a translator."""

        for state in states:
            self._save_data_state(state, data)

    def _translate_multidata_batch(
        self,
        translator: ARCallableType,
        states: list[ARDataState],
        data: dict[ARDataSource | ARDataType, Any],
    ) -> None:
        """Translate a full multicaller result using a batch translator."""

        translated = translator(data)
        if not isinstance(translated, dict):
            _LOGGER.debug(
                "Translator %s returned %s instead of dict",
                getattr(translator, "__name__", repr(translator)),
                type(translated).__name__,
            )
            return

        for state in states:
            self._save_data_state(state, translated)

    def _translate_multidata_single(
        self,
        translator: ARCallableType,
        states: list[ARDataState],
        data: dict[ARDataSource | ARDataType, Any],
    ) -> None:
        """Translate individual state entries from multicaller output."""

        for state in states:
            state_source = state.source
            if state_source not in data:
                continue

            translated = translator(data[state_source])
            state.update(translated)
            self._data_states[state.source] = state

    def _translate_multidata(
        self,
        data: dict[ARDataSource | ARDataType, Any],
        states: list[ARDataState],
    ) -> None:
        """Translate data obtained from a multicaller."""

        if not isinstance(data, dict):
            _LOGGER.debug(  # type: ignore[unreachable]
                "Multicaller result must be a dict, got %s",
                type(data).__name__,
            )
            return

        translators: dict[ARCallableType | None, list[ARDataState]] = (
            defaultdict(list)
        )
        for state in states:
            translators[state.translate_caller].append(state)

        for translator, grouped_states in translators.items():
            if translator is None:
                self._translate_multidata_raw(data, grouped_states)
                continue

            if ARCallReg.get_callable_flag(translator):
                self._translate_multidata_batch(
                    translator,
                    grouped_states,
                    data,
                )
                continue

            self._translate_multidata_single(
                translator,
                grouped_states,
                data,
            )

    async def _async_refresh_data_state(
        self,
        cllctn: ARDataCollection,
        force: bool = False,
        **kwargs: Any,
    ) -> None:
        """Refresh the data state for the specified source."""

        if not isinstance(cllctn, ARDataCollection) or not cllctn:
            return

        # Get the states to work with
        data_states = self._data_states
        _states: list[ARDataState] = [
            data_states[item] for item in cllctn if item in data_states
        ]
        if not _states:
            return

        # Build call matrix
        matrix = self._get_call_matrix(_states)

        # Fetch the data
        for (caller, callback), states in matrix.items():
            sources = [state.source for state in states]

            multicaller = ARCallReg.get_callable_flag(caller)

            if multicaller is True:
                data = await caller(callback, sources, force=force, **kwargs)
                self._translate_multidata(data, states)

            else:
                for state in states:
                    data = await caller(
                        callback, state.source, force=force, **kwargs
                    )
                    translator = state.translate_caller
                    if translator:
                        data = translator(data)

                    state.update(data)
                    self._data_states[state.source] = state

    async def async_get_data_state(
        self,
        source: ARDataRequest,
        force: bool = False,
        **kwargs: Any,
    ) -> dict[ARDataSource | ARDataType, ARDataState]:
        """Get the full data state for the specified source."""

        # Convert source to a collection
        cllctn = ARDataCollection.from_value(source)
        if not cllctn:
            return {}

        # Create a state for the source if it doesn't exist
        self._create_data_state(cllctn)

        # Update the state
        await self._async_refresh_data_state(cllctn, force=force, **kwargs)

        # Return the state
        return {
            item: self._data_states[item]
            for item in cllctn
            if item in self._data_states
        }

    async def async_get_data_v2(
        self,
        source: ARDataRequest,
        force: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Get data from the specified source."""

        _LOGGER.debug("Querying data V2")

        # Allow recursive calls
        kwargs["get_data_callback"] = self.async_get_data_v2

        # Get the new data state
        data_state = await self.async_get_data_state(
            source, force=force, **kwargs
        )
        if not data_state:
            return None

        # Check if the data is fresh
        fresh_state = {
            key: state
            for key, state in data_state.items()
            if state.is_fresh(self._cache_threshold)
        }
        if not fresh_state:
            return None

        # Return the data
        return {key: state.content for key, state in fresh_state.items()}

    async def async_get_data(  # noqa: C901, PLR0912, PLR0915
        self, datatype: AsusData, force: bool = False, **kwargs: Any
    ) -> Any:
        """Get data from the device.

        Forced reads raise on connection or data errors instead of returning
        cached data. A response omitting the requested datatype is also an
        error for a forced read, as is invalidation during the fetch.
        """

        # --- V2 COMPATIBILITY ---
        # This small switcher will allow gradual switching from v1 to v2 logic
        if isinstance(datatype, ARDataSource | ARDataType):
            return await self.async_get_data_v2(
                source=datatype, force=force, **kwargs
            )

        # Check if we have a state object for this data
        self._check_state(datatype)

        # If state object is active, wait for it to finish and return the data
        if self._state[datatype].active:
            try:
                _LOGGER.debug(
                    "Already in progress. Waiting for data to be fetched"
                )
                await asyncio.wait_for(
                    self._state[datatype].inactive_event.wait(),
                    DEFAULT_TIMEOUT,
                )
            except TimeoutError:
                _LOGGER.debug(
                    "Timeout while waiting for data. Will try fetching again"
                )

        # Check if we have the data already and not forcing a refresh
        if (
            self._state[datatype].data
            and not force
            and not self._state[datatype].invalidated
        ):
            # Check if the data is younger than the cache time
            if datetime.now(UTC) - self._state[datatype].timestamp < timedelta(
                seconds=self._cache_time
            ):
                _LOGGER.debug(
                    "Using cached data for `%s` with object type `%s`",
                    datatype,
                    type(self._state[datatype].data).__name__,
                )
                # Return the cached data
                return self._state[datatype].data
            _LOGGER.debug("Data for %s is too old. Fetching", datatype)

        # Mark the data as active
        self._state[datatype].start()

        try:
            # Get the data finder
            data_finder = self._where_to_get_data(datatype)

            # Check if we have a data finder
            if not data_finder:
                _LOGGER.debug("No data finder for %s", datatype)
                return {}

            # The data we are looking for
            data = {}
            result: dict[AsusData, Any] = {}

            # Endpoints may return multiple datatypes. Keep one snapshot for
            # the entire fetch so no pre-invalidation response can be saved.
            generations = {
                key: state.generation for key, state in self._state.items()
            }

            for endpoint in data_finder.endpoint:
                # Get the data from the endpoint
                request = "hook=" if endpoint == Endpoint.HOOK else ""
                for item in data_finder.request:
                    key, value = item
                    request += f"{key}({value});"
                if data_finder.method:
                    argument = self._get_attribute(data_finder.arguments)
                    generated_request = (
                        data_finder.method(argument)
                        if argument
                        else data_finder.method()
                    )
                    request += cast(str, generated_request)
                # Check that we are not fetching this data already

                # Add the request from kwargs
                kw_request = kwargs.get("request", {})
                if isinstance(kw_request, dict):
                    for key, value in kw_request.items():
                        request += f"{key}={value};"
                    # Remove trailing symbol
                    request = request[:-1]

                # Fetch the data
                data = await self.async_api_load(endpoint, request)

                # Make sure, identity is available
                if not self._identity:
                    self._identity = await self.async_get_identity()

                processed = process(
                    endpoint,
                    data,
                    self._state,
                    self._identity.firmware,
                    self._identity.wlan,
                )

                # Check whether data should be dropped
                to_drop = []
                for key, value in processed.items():
                    if self._drop_data(key, endpoint):
                        to_drop.append(key)
                for key in to_drop:
                    processed.pop(key, None)

                result = merge_dicts(result, processed)

                # Check if we have data and data finder merge is ANY
                if result and data_finder.merge == AsusDataMerge.ANY:
                    break

            if self._state[datatype].generation != generations[datatype]:
                raise AsusRouterDataError(
                    f"Data invalidated during fetch: {datatype}"
                )

            if force and datatype not in result:
                raise AsusRouterDataError(
                    f"Response omitted requested data: {datatype}"
                )

            # Save the data state
            for key, value in result.items():
                if key in self._state and self._state[
                    key
                ].generation != generations.get(key, 0):
                    continue
                # Transform data if needed
                transformed_value = self._transform_data(key, value)
                # Save the data
                result[key] = transformed_value
                # Update the state
                if key not in self._state:
                    self._state[key] = AsusDataState()
                self._state[key].update(transformed_value)
        except (AsusRouterConnectionError, AsusRouterDataError):
            if force:
                raise
            return self._return_state(datatype, **kwargs)
        finally:
            # Failed, cancelled or omitted updates must also release waiters.
            self._state[datatype].stop()

        # Check flags
        await self._check_flags()

        # Check postrequisites
        await self._check_postrequisites(datatype)

        # Return the data we were looking for
        _LOGGER.debug(
            "Returning data for `%s` with object type `%s`",
            datatype,
            type(self._state[datatype].data),
        )
        return self._return_state(datatype, **kwargs)

    # ---------------------------
    # Service-related methods -->
    # ---------------------------

    async def async_run_service_result(
        self,
        service: str | None,
        arguments: dict[str, Any] | None = None,
        apply: bool = False,
        expect_modify: bool = True,
        drop_connection: bool = False,
    ) -> ServiceResult:
        """Run a service and return its call-local result metadata."""

        _LOGGER.debug("Triggered method async_run_service_result")

        success, needed_time, last_id = await async_call_service(
            self.async_api_command,
            service,
            arguments,
            apply,
            expect_modify,
        )

        result = ServiceResult(success, needed_time, last_id)
        if (
            result.success is True
            and arguments
            and any(key in arguments for key in HOOK_PC)
        ):
            # Invalidate before any further await, including connection drop.
            self._check_state(AsusData.PARENTAL_CONTROL)
            self._state[AsusData.PARENTAL_CONTROL].invalidate()

        if drop_connection:
            await self._async_drop_connection()

        return result

    async def async_run_service(
        self,
        service: str | None,
        arguments: dict[str, Any] | None = None,
        apply: bool = False,
        expect_modify: bool = True,
        drop_connection: bool = False,
    ) -> bool:
        """Run a service."""

        _LOGGER.debug("Triggered method async_run_service")

        result = await self.async_run_service_result(
            service,
            arguments,
            apply,
            expect_modify,
            drop_connection,
        )
        self._needed_time = result.needed_time
        self._last_id = result.last_id

        return result.success

    async def _async_check_state_dependency(self, state: AsusState) -> None:
        """Check and queue state dependencies. Required for some states."""

        _LOGGER.debug("Triggered method _async_check_state_dependency")

        dependency = get_datatype(state, state_map=self._state_map)

        if dependency == AsusData.VPNC:
            # VPNC state change requires the correct previous state
            await self.async_get_data(AsusData.VPNC, force=True)

        if dependency == AsusData.AURA:
            # Aura state change requires the correct previous state
            await self.async_get_data(AsusData.AURA, force=True)

    async def _async_get_state_callback(
        self, state: AsusState
    ) -> ARCallbackType:
        """Get the state callback."""

        _LOGGER.debug("Triggered method _async_get_state_callback")

        datatype = get_datatype(state, state_map=self._state_map)
        # If state is one of AsusState.AURA enum
        if datatype == AsusData.AURA:
            return self.async_api_command

        return self.async_run_service

    async def async_set_state(
        self,
        state: AsusState,
        expect_modify: bool = False,
        **kwargs: Any,
    ) -> bool:
        """Set the state."""

        _LOGGER.debug("Triggered method async_set_state")

        _LOGGER.debug(
            "Setting state for datatype `%s` using state type `%s` with %d "
            "argument(s). Expecting modify: `%s`",
            get_datatype(state, state_map=self._state_map),
            type(state).__name__,
            len(kwargs),
            expect_modify,
        )

        state_type = get_enum_key_by_value(
            AsusState, type(state), default=AsusState.NONE
        )

        # Get the state callback
        callback = await self._async_get_state_callback(state)

        if state_type == AsusState.PC_RULE:
            # Rule changes replace the whole router table. Serialize the
            # authoritative refresh and the write so concurrent callers
            # cannot build replacements from the same stale snapshot.
            async with self._parental_control_lock:
                await self.async_get_data(
                    AsusData.PARENTAL_CONTROL, force=True
                )
                result = await set_state(
                    callback=callback,
                    state=state,
                    state_map=self._state_map,
                    expect_modify=expect_modify,
                    router_state=self._state,
                    identity=self._identity,
                    **kwargs,
                )
        else:
            # Check dependencies
            await self._async_check_state_dependency(state)

            result = await set_state(
                callback=callback,
                state=state,
                state_map=self._state_map,
                expect_modify=expect_modify,
                router_state=self._state,
                identity=self._identity,
                **kwargs,
            )

        # Rewrite the result if it is the default one
        if result == DEFAULT_RESULT_SUCCESS:
            result = True

        if result is True:
            _datatype = get_datatype(state, state_map=self._state_map)

            if _datatype in (AsusData.VPNC, AsusData.AURA):
                # The only way to make it work with VPN Fusion
                await asyncio.sleep(1)
                try:
                    await self._async_check_state_dependency(state)
                except AsusRouterError as ex:
                    # The write itself succeeded. A failed refresh of
                    # the dependent state must not report it as failed.
                    _LOGGER.warning(
                        "State `%s` was set, but refreshing `%s` "
                        "afterwards failed: %s",
                        state,
                        _datatype,
                        ex,
                    )
            elif state_type == AsusState.PC_RULE:
                # We should not save this state, since it is saved differently
                pass
            else:
                # Check if we have a state object for this data
                self._check_state(_datatype)
                # Save the state
                _LOGGER.debug(
                    "Saving state `%s` for `%s` s with id=`%s`",
                    state,
                    self._needed_time,
                    self._last_id,
                )
                save_state(
                    state,
                    self._state,
                    self._needed_time,
                    self._last_id,
                    state_map=self._state_map,
                )
                # Reset the needed time and last id
                self._needed_time = None
                self._last_id = None

        return result

    # ---------------------------
    # <-- Service-related methods
    # ---------------------------

    async def async_get_parental_control_capabilities(
        self,
    ) -> ParentalControlCapabilities:
        """Get router-advertised parental-control capabilities."""

        response = await self.async_api_hook("get_ui_support()")
        ui_support = response.get("get_ui_support")
        return read_pc_capabilities(
            ui_support if isinstance(ui_support, dict) else {}
        )

    # ---------------------------
    # Static DHCP methods -->
    # ---------------------------

    async def _async_get_static_dhcp_snapshot(
        self,
    ) -> tuple[bool, list[StaticDHCPLease]]:
        """Get an authoritative static DHCP state and lease snapshot."""

        request = nvram([KEY_STATIC_DHCP_STATE, KEY_STATIC_DHCP_LIST])
        if not request:
            raise AsusRouterDataError("Unable to build static DHCP request")

        response = await self.async_api_hook(request)
        if not isinstance(response, dict):
            raise AsusRouterDataError("Incomplete static DHCP response")

        raw_list = response.get(KEY_STATIC_DHCP_LIST)
        state = safe_bool(response.get(KEY_STATIC_DHCP_STATE))
        if (
            KEY_STATIC_DHCP_STATE not in response
            or not isinstance(raw_list, str)
            or state is None
        ):
            raise AsusRouterDataError("Incomplete static DHCP response")

        try:
            leases = parse_static_dhcp_leases(raw_list)
        except (TypeError, ValueError) as ex:
            raise AsusRouterDataError(
                "Static DHCP response contains an unrecognized row"
            ) from ex
        return state, leases

    async def async_get_static_dhcp_leases(self) -> list[StaticDHCPLease]:
        """Get saved static DHCP leases, including when disabled."""

        _, leases = await self._async_get_static_dhcp_snapshot()
        return leases

    async def async_apply_static_dhcp_leases(
        self,
        leases: Iterable[StaticDHCPLease],
        *,
        enabled: bool | None = None,
    ) -> bool:
        """Apply static DHCP leases."""

        request = compile_static_dhcp_leases(leases, enabled=enabled)
        return await self.async_run_service(
            service=SERVICE_STATIC_DHCP_APPLY,
            arguments=request,
            apply=True,
        )

    async def async_set_static_dhcp_lease(
        self,
        mac: str,
        ip: str,
        hostname: str | None = None,
        dns: str | None = None,
    ) -> bool:
        """Set a static DHCP lease."""

        async with self._static_dhcp_lock:
            _, current_leases = await self._async_get_static_dhcp_snapshot()
            lease = normalize_static_dhcp_lease(
                StaticDHCPLease(
                    mac=mac,
                    ip=ip,
                    dns="" if dns is None else dns,
                    hostname="" if hostname is None else hostname,
                )
            )
            lease_mac = lease.mac
            existing = next(
                (
                    current
                    for current in current_leases
                    if normalize_static_dhcp_mac(current.mac) == lease_mac
                ),
                None,
            )
            layout = (
                existing.layout
                if existing is not None
                else (
                    LAYOUT_LEGACY
                    if any(
                        current.layout == LAYOUT_LEGACY
                        for current in current_leases
                    )
                    else lease.layout
                )
            )
            lease = normalize_static_dhcp_lease(
                StaticDHCPLease(
                    mac=lease.mac,
                    ip=lease.ip,
                    dns=lease.dns,
                    hostname=lease.hostname,
                    layout=layout,
                )
            )

            final = [
                current
                for current in current_leases
                if normalize_static_dhcp_mac(current.mac) != lease_mac
            ]
            final.append(lease)
            return await self.async_apply_static_dhcp_leases(
                final,
                enabled=True,
            )

    async def async_remove_static_dhcp_lease(
        self,
        mac: str,
        apply: bool = True,
    ) -> list[StaticDHCPLease]:
        """Remove a static DHCP lease."""

        async with self._static_dhcp_lock:
            (
                state,
                current_leases,
            ) = await self._async_get_static_dhcp_snapshot()
            lease_mac = normalize_static_dhcp_mac(mac)
            final = [
                lease
                for lease in current_leases
                if normalize_static_dhcp_mac(lease.mac) != lease_mac
            ]

            if apply and len(final) != len(current_leases):
                success = await self.async_apply_static_dhcp_leases(
                    final,
                    enabled=state and bool(final),
                )
                if not success:
                    raise AsusRouterDataError(
                        "Unable to apply static DHCP lease removal"
                    )

            return final

    # ---------------------------
    # <-- Static DHCP methods
    # ---------------------------

    # ---------------------------
    # Properties -->
    # ---------------------------

    @property
    def connected(self) -> bool:
        """Return connection status."""

        return self._connection.connected if self._connection else False

    @property
    def config(self) -> ARInstanceConfig:
        """Return connection config."""

        return self._config

    @property
    def webpanel(self) -> str:
        """Return the web panel URL."""

        if self._connection:
            return self._connection.webpanel

        return (
            f"https://{self._hostname}:{self._port or DEFAULT_PORT_HTTPS}"
            if self._use_ssl
            else f"http://{self._hostname}:{self._port or DEFAULT_PORT_HTTP}"
        )

    # ---------------------------
    # <-- Properties
    # ---------------------------

    # ---------------------------
    # Additional settings -->
    # ---------------------------

    # ---------------------------
    # <-- Additional settings
    # ---------------------------

    # ---------------------------
    # General management -->
    # ---------------------------

    async def async_cleanup(self) -> None:
        """Cleanup the connection."""

        if self._connection:
            self._connection.reset_connection()
            # await self._connection._async_close_session()

    # ---------------------------
    # <-- General management
    # ---------------------------
