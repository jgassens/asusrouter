"""Static DHCP lease parsing and serialization."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import re
from typing import Any, Final

from asusrouter.tools.identifiers import IpAddress, MacAddress

KEY_STATIC_DHCP_LIST = "dhcp_staticlist"
KEY_STATIC_DHCP_STATE = "dhcp_static_x"

SERVICE_STATIC_DHCP_APPLY = "restart_dnsmasq"

LAYOUT_LEGACY = "legacy"
LAYOUT_MODERN = "modern"

_LEASE_DELIMITER = "<"
_LEASE_DELIMITER_ESCAPED = "&#60"
_FIELD_DELIMITER = ">"
_FIELD_DELIMITER_ESCAPED = "&#62"
_IPV4_VERSION = 4
_LEGACY_FIELD_COUNT = 3
_MODERN_FIELD_COUNT = 4
_MAC_PLAIN_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class StaticDHCPLease:
    """One static DHCP reservation."""

    mac: str
    ip: str
    dns: str = ""
    hostname: str = ""
    layout: str = field(default=LAYOUT_MODERN, compare=False, repr=False)


def normalize_static_dhcp_mac(value: Any) -> str:
    """Normalize a lease MAC, treating 12 plain digits as hexadecimal."""

    if isinstance(value, str):
        compact = re.sub(r"[^0-9a-fA-F]", "", value)
        if _MAC_PLAIN_RE.fullmatch(compact):
            return bytes.fromhex(compact).hex(":").upper()
    return MacAddress.from_value(value).as_asus()


def _clean_optional(value: Any) -> str:
    """Convert an optional field without treating the text None as empty."""

    return "" if value is None or value == "" else str(value)


def _normalize_ipv4(value: Any, *, allow_empty: bool = False) -> str:
    """Normalize an IPv4 address."""

    if value is None or value == "":
        if allow_empty:
            return ""
        raise ValueError("Static DHCP lease IP address is required")

    ip_address = IpAddress.from_value(value)
    if ip_address.version != _IPV4_VERSION:
        raise ValueError("Static DHCP lease IP address must be IPv4")
    return str(ip_address)


def _check_text(value: str) -> None:
    """Reject delimiters that would change the NVRAM row shape."""

    if (
        _LEASE_DELIMITER in value
        or _FIELD_DELIMITER in value
        or _LEASE_DELIMITER_ESCAPED in value
        or _FIELD_DELIMITER_ESCAPED in value
    ):
        raise ValueError("Static DHCP lease fields cannot contain delimiters")


def normalize_static_dhcp_lease(lease: StaticDHCPLease) -> StaticDHCPLease:
    """Normalize and validate one lease."""

    if not isinstance(lease, StaticDHCPLease):
        raise TypeError("Static DHCP leases must be StaticDHCPLease objects")

    mac = normalize_static_dhcp_mac(lease.mac)
    ip = _normalize_ipv4(lease.ip)
    dns = _normalize_ipv4(lease.dns, allow_empty=True)
    hostname = _clean_optional(lease.hostname)

    if lease.layout not in (LAYOUT_LEGACY, LAYOUT_MODERN):
        raise ValueError("Unknown static DHCP lease layout")
    if lease.layout == LAYOUT_LEGACY and dns:
        raise ValueError(
            "Legacy static DHCP leases cannot contain a DNS field"
        )

    for value in (mac, ip, dns, hostname):
        _check_text(value)

    return StaticDHCPLease(
        mac=mac,
        ip=ip,
        dns=dns,
        hostname=hostname,
        layout=lease.layout,
    )


def _parse_row(row: str) -> StaticDHCPLease:
    """Parse one decoded NVRAM row without dropping unknown columns."""

    columns = row.split(_FIELD_DELIMITER)
    if (
        len(columns) not in (2, _LEGACY_FIELD_COUNT, _MODERN_FIELD_COUNT)
        or not columns[0]
        or not columns[1]
    ):
        raise ValueError("Unrecognized static DHCP lease row")

    dns = ""
    hostname = ""
    layout = LAYOUT_MODERN

    if len(columns) == _LEGACY_FIELD_COUNT:
        third = columns[2]
        if third:
            try:
                dns = _normalize_ipv4(third)
            except ValueError:
                hostname = third
                layout = LAYOUT_LEGACY
    elif len(columns) == _MODERN_FIELD_COUNT:
        dns, hostname = columns[2], columns[3]

    return normalize_static_dhcp_lease(
        StaticDHCPLease(
            mac=columns[0],
            ip=columns[1],
            dns=dns,
            hostname=hostname,
            layout=layout,
        )
    )


def parse_static_dhcp_leases(content: str | None) -> list[StaticDHCPLease]:
    """Parse a complete NVRAM list, raising on any unrecognized row."""

    if content is None or content == "":
        return []
    if not isinstance(content, str):
        raise ValueError("Static DHCP lease list must be text")

    decoded = content.replace(
        _LEASE_DELIMITER_ESCAPED,
        _LEASE_DELIMITER,
    ).replace(_FIELD_DELIMITER_ESCAPED, _FIELD_DELIMITER)
    return [_parse_row(row) for row in decoded.split(_LEASE_DELIMITER) if row]


def _serialize_lease(lease: StaticDHCPLease) -> str:
    """Serialize one normalized lease using its firmware layout."""

    fields = [lease.mac, lease.ip]
    if lease.layout == LAYOUT_LEGACY:
        if lease.hostname:
            fields.append(lease.hostname)
    else:
        if lease.dns or lease.hostname:
            fields.append(lease.dns)
        if lease.hostname:
            fields.append(lease.hostname)
    return f"<{'>'.join(fields)}"


def compile_static_dhcp_leases(
    leases: Iterable[StaticDHCPLease],
    *,
    enabled: bool | None = None,
) -> dict[str, str | int]:
    """Compile a complete lease iterable to ASUS request arguments."""

    normalized = [normalize_static_dhcp_lease(lease) for lease in leases]
    mac_addresses: set[str] = set()
    ip_addresses: set[str] = set()

    for lease in normalized:
        if lease.mac in mac_addresses:
            raise ValueError(f"Duplicate static DHCP lease MAC: {lease.mac}")
        if lease.ip in ip_addresses:
            raise ValueError(f"Duplicate static DHCP lease IP: {lease.ip}")
        mac_addresses.add(lease.mac)
        ip_addresses.add(lease.ip)

    state = bool(normalized) if enabled is None else enabled
    return {
        KEY_STATIC_DHCP_LIST: "".join(
            _serialize_lease(lease) for lease in normalized
        ),
        KEY_STATIC_DHCP_STATE: int(state),
    }


__all__ = [
    "KEY_STATIC_DHCP_LIST",
    "KEY_STATIC_DHCP_STATE",
    "LAYOUT_LEGACY",
    "LAYOUT_MODERN",
    "SERVICE_STATIC_DHCP_APPLY",
    "StaticDHCPLease",
    "compile_static_dhcp_leases",
    "normalize_static_dhcp_lease",
    "normalize_static_dhcp_mac",
    "parse_static_dhcp_leases",
]
