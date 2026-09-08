"""Parental control module."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import IntEnum
import logging
from typing import Any

from asusrouter.error import AsusRouterDataError
from asusrouter.modules.data import AsusData
from asusrouter.tools.converters import safe_int, safe_return

_LOGGER = logging.getLogger(__name__)

KEY_PC_BLOCK_ALL = "MULTIFILTER_BLOCK_ALL"
KEY_PC_MAC = "MULTIFILTER_MAC"
KEY_PC_NAME = "MULTIFILTER_DEVICENAME"
KEY_PC_STATE = "MULTIFILTER_ALL"
KEY_PC_TIMEMAP = "MULTIFILTER_MACFILTER_DAYTIME_V2"
KEY_PC_TYPE = "MULTIFILTER_ENABLE"

KEY_PC_MAX_RULES = "MaxRule_parentctrl"
KEY_PC_MAX_ENTRIES = "MaxRule_PC_DAYTIME"
KEY_PC_SCHED_VERSION = "PC_SCHED_V3"

DEFAULT_PC_MAX_RULES = 16
DEFAULT_PC_MAX_ENTRIES = 128

PC_RULE_MAP = {
    KEY_PC_MAC: "mac",
    KEY_PC_NAME: "name",
    KEY_PC_TIMEMAP: "timemap",
    KEY_PC_TYPE: "type",
}

HOOK_PC = [
    KEY_PC_BLOCK_ALL,
    KEY_PC_MAC,
    KEY_PC_NAME,
    KEY_PC_STATE,
    KEY_PC_TIMEMAP,
    KEY_PC_TYPE,
]


DEFAULT_PC_TIMEMAP = "W03E21000700<W04122000800"
MAX_PC_NAME_LENGTH = 32

_PC_FIELD_DELIMITERS = (">", "<", "&#62", "&#60")
_PC_CONTROL_CHARACTER_LIMIT = 0x20


@dataclass(frozen=True)
class ParentalControlCapabilities:
    """Router-advertised parental-control limits and schedule version."""

    max_rules: int | None = None
    max_entries: int | None = None
    sched_version: int | None = None


class ParentalControlCapacityError(ValueError):
    """Raised when a parental-control update exceeds a capacity limit."""


class PCRuleType(IntEnum):
    """Parental control rule type."""

    UNKNOWN = -999
    REMOVE = -1  # pseudo type to remove a rule
    DISABLE = 0
    TIME = 1
    BLOCK = 2


@dataclass
class ParentalControlRule:
    """Parental control rule class."""

    mac: str | None = None
    name: str | None = ""
    timemap: str | None = DEFAULT_PC_TIMEMAP
    type: PCRuleType = PCRuleType.UNKNOWN


def _read_positive_int(value: Any) -> int | None:
    """Return a positive integer from a supported router value."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdecimal():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def read_pc_capabilities(data: dict[str, Any]) -> ParentalControlCapabilities:
    """Read parental-control capabilities from UI support data."""

    return ParentalControlCapabilities(
        max_rules=_read_positive_int(data.get(KEY_PC_MAX_RULES)),
        max_entries=_read_positive_int(data.get(KEY_PC_MAX_ENTRIES)),
        sched_version=_read_positive_int(data.get(KEY_PC_SCHED_VERSION)),
    )


def count_pc_rule_entries(
    rules: dict[str, ParentalControlRule],
) -> tuple[int, int]:
    """Count rule rows and raw non-empty schedule segments."""

    window_count = 0
    for rule in rules.values():
        if rule.timemap:
            timemap = rule.timemap.replace("&#60", "<")
            window_count += sum(
                bool(segment) for segment in timemap.split("<")
            )

    return len(rules), window_count


def validate_pc_capacity(
    current_rules: dict[str, ParentalControlRule],
    proposed_rules: dict[str, ParentalControlRule],
    capabilities: ParentalControlCapabilities,
) -> None:
    """Reject only updates that grow a table beyond a capacity limit."""

    current_counts = count_pc_rule_entries(current_rules)
    proposed_counts = count_pc_rule_entries(proposed_rules)
    dimensions = (
        (
            "rules",
            current_counts[0],
            proposed_counts[0],
            capabilities.max_rules,
            DEFAULT_PC_MAX_RULES,
            KEY_PC_MAX_RULES,
        ),
        (
            "schedule windows",
            current_counts[1],
            proposed_counts[1],
            capabilities.max_entries,
            DEFAULT_PC_MAX_ENTRIES,
            KEY_PC_MAX_ENTRIES,
        ),
    )

    for (
        dimension,
        current,
        proposed,
        advertised,
        fallback,
        router_key,
    ) in dimensions:
        limit = advertised if advertised is not None else fallback
        if proposed <= current or proposed <= limit:
            continue

        source = (
            router_key
            if advertised is not None
            else "assumed because the router did not report it"
        )
        raise ParentalControlCapacityError(
            "Parental-control update would increase "
            f"{dimension} from {current} to {proposed}; "
            f"router limit is {limit} ({source})."
        )


class AsusParentalControl(IntEnum):
    """Asus parental control state."""

    UNKNOWN = -999
    OFF = 0
    ON = 1


class AsusBlockAll(IntEnum):
    """Asus block all state."""

    UNKNOWN = -999
    OFF = 0
    ON = 1


async def set_state(
    callback: Callable[..., Awaitable[bool]],
    state: AsusParentalControl | AsusBlockAll | ParentalControlRule,
    **kwargs: Any,
) -> bool:
    """Set the parental control state."""

    # Check if we need to set a rule
    if isinstance(state, ParentalControlRule):
        return await set_rule(callback, state, **kwargs)

    # Check if state is available and valid
    if not isinstance(
        state, AsusParentalControl | AsusBlockAll
    ) or state.value not in (0, 1):
        return False

    service_arguments = {}

    match state:
        case a if isinstance(a, AsusParentalControl):
            service_arguments = {
                KEY_PC_STATE: 1 if state == AsusParentalControl.ON else 0
            }
        case a if isinstance(a, AsusBlockAll):
            service_arguments = {
                KEY_PC_BLOCK_ALL: 1 if state == AsusBlockAll.ON else 0
            }

    # Get the correct service call
    service = "restart_firewall"

    # Call the service
    return await callback(
        service=service,
        arguments=service_arguments,
        apply=True,
        expect_modify=kwargs.get("expect_modify", False),
    )


async def set_rule(
    callback: Callable[..., Awaitable[bool]],
    rule: ParentalControlRule,
    **kwargs: Any,
) -> bool:
    """Set the parental control rule."""

    # Check if rule is available
    if not isinstance(rule, ParentalControlRule):
        return False

    # Only a known rule table may seed a whole-table replacement.
    router_state = kwargs.get("router_state", {})
    parental_control = (
        router_state.get(AsusData.PARENTAL_CONTROL)
        if isinstance(router_state, dict)
        else None
    )
    data = getattr(parental_control, "data", None)
    if not isinstance(data, dict) or not isinstance(data.get("rules"), dict):
        _LOGGER.error(
            "Cannot set parental control rule without a known rules dict"
        )
        return False
    if getattr(parental_control, "invalidated", False):
        _LOGGER.error(
            "Cannot set parental control rule from a stale rule table; "
            "fetch the current rules first"
        )
        return False
    # Work on a copy: the cached table must only ever change through a
    # fetch, never through a write that the router may still reject.
    current_rules = dict(data["rules"])

    # Get rule action
    # If the rule is not available, we need to add it
    # update can also be handled as add
    action = add_rule
    if rule.type == PCRuleType.REMOVE:
        action = remove_rule
    elif check_rule(rule) is None:
        return False

    # Perform the action
    current_rules = action(current_rules, rule)

    # Convert the rules to service arguments
    service_arguments = write_pc_rules(current_rules)

    # Get the correct service call
    service = "restart_firewall"

    # Call the service
    return await callback(
        service=service,
        arguments=service_arguments,
        apply=True,
    )


def check_rule(  # noqa: C901, PLR0911
    rule: ParentalControlRule | None,
) -> ParentalControlRule | None:
    """Check the parental control rule."""

    # Check if rule is available
    if not isinstance(rule, ParentalControlRule):
        return None

    # Check that mac is available
    if rule.mac is None:
        return None

    # Check that type is available and valid
    if not isinstance(rule.type, PCRuleType) or rule.type not in (
        PCRuleType.DISABLE,
        PCRuleType.TIME,
        PCRuleType.BLOCK,
    ):
        return None

    if rule.name is not None and not isinstance(rule.name, str):
        _LOGGER.error("Invalid parental control rule name")
        return None
    if rule.timemap is not None and not isinstance(rule.timemap, str):
        _LOGGER.error("Invalid parental control rule timemap")
        return None

    for field_name, value in (("name", rule.name), ("timemap", rule.timemap)):
        if not value or (
            value == DEFAULT_PC_TIMEMAP and field_name == "timemap"
        ):
            continue
        if any(token in value for token in _PC_FIELD_DELIMITERS) or any(
            ord(character) < _PC_CONTROL_CHARACTER_LIMIT for character in value
        ):
            _LOGGER.error(
                "Parental control rule %s contains an unsafe delimiter or "
                "control character",
                field_name,
            )
            return None

    # Check that timemap is available and valid
    if not (rule.timemap or "").strip():
        rule.timemap = DEFAULT_PC_TIMEMAP

    # Check that name is available
    if not (rule.name or "").strip():
        rule.name = rule.mac

    if len(rule.name) > MAX_PC_NAME_LENGTH:
        _LOGGER.error(
            "Parental control rule name exceeds %d characters",
            MAX_PC_NAME_LENGTH,
        )
        return None

    # Return the rule
    return rule


def add_rule(
    current_rules: dict[str, ParentalControlRule],
    rule: ParentalControlRule | None = None,
) -> dict[str, ParentalControlRule]:
    """Add a rule."""

    # Check that the current rules are available
    if not isinstance(current_rules, dict):
        current_rules = {}

    # Check if rule is available and valid
    rule = check_rule(rule)
    if rule is None or rule.mac is None:
        return current_rules

    # Add the new rule
    # This will also overwrite (update) the old rule if it exists
    current_rules[rule.mac] = rule

    return current_rules


def remove_rule(
    current_rules: dict[str, ParentalControlRule],
    rule: ParentalControlRule | str | None = None,
) -> dict[str, ParentalControlRule]:
    """Remove a rule."""

    # Check that the current rules are available
    if not isinstance(current_rules, dict):
        current_rules = {}

    rule_mac = (
        rule.mac
        if isinstance(rule, ParentalControlRule)
        else rule
        if isinstance(rule, str)
        else None
    )

    # If mac is available, remove the rule
    if rule_mac is not None:
        current_rules.pop(rule_mac, None)

    return current_rules


def read_pc_string(key: str, data: dict[str, str]) -> list[str]:
    """Read the parental control string."""

    return data.get(key, "").split("&#62")


def read_pc_rules(data: dict[str, Any]) -> dict[str, ParentalControlRule]:
    """Read a complete rule table, raising on missing or unaligned vectors."""

    vectors = {}
    cardinalities: dict[str, int | str] = {}
    for key in PC_RULE_MAP:
        if isinstance(data.get(key), str):
            vectors[key] = read_pc_string(key, data)
            cardinalities[key] = len(vectors[key])
        else:
            cardinalities[key] = "missing" if key not in data else "non-string"

    if (
        len(vectors) != len(PC_RULE_MAP)
        or len(set(cardinalities.values())) != 1
    ):
        details = ", ".join(
            f"{key}={count}" for key, count in cardinalities.items()
        )
        raise AsusRouterDataError(
            "Incomplete parental control rule vectors (cardinalities): "
            f"{details}"
        )

    # Only four explicitly empty strings represent an empty table.
    if all(data[key] == "" for key in PC_RULE_MAP):
        return {}

    # The data is split in 4 strings. Each data value is split in the string
    # with a `&#62` separator. We need to map the data and make sure, that
    # each `ParentalControlRule` has all values

    # Map the values to a list of `ParentalControlRule`
    rules = {}
    for rule_mac, rule_name, rule_timemap, rule_type in zip(
        *vectors.values(), strict=True
    ):
        type_code = safe_int(rule_type, default=-999)
        # Map the values
        rule = ParentalControlRule(
            mac=safe_return(rule_mac),
            name=rule_name,
            timemap=rule_timemap,
            type=(
                PCRuleType(type_code)
                if type_code in PCRuleType._value2member_map_
                else PCRuleType.UNKNOWN
            ),
        )

        # Append the rule to the list
        rules[rule_mac] = rule

    return rules


def write_pc_rules(rules: dict[str, ParentalControlRule]) -> dict[str, str]:
    """Write the parental control data."""

    # If no rules are provided, return empty dict
    if not rules:
        return dict.fromkeys(PC_RULE_MAP, "")

    # Join the values together
    data = {}
    for key, attribute in PC_RULE_MAP.items():
        data[key] = ">".join(
            str(value)
            if (value := getattr(rule, attribute, "")) is not None
            else ""
            for rule in rules.values()
        )

    data[KEY_PC_TIMEMAP] = data[KEY_PC_TIMEMAP].replace("&#60", "<")

    return data
