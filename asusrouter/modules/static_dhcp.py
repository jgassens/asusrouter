"""Static DHCP lease module."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from asusrouter.tools.identifiers import IpAddress, MacAddress

KEY_STATIC_DHCP_LIST = "dhcp_staticlist"
KEY_STATIC_DHCP_STATE = "dhcp_static_x"

SERVICE_STATIC_DHCP_APPLY = "restart_dnsmasq"

LEASE_DELIMITER = "<"
LEASE_DELIMITER_ESCAPED = "&#60"
LEASE_FIELD_DELIMITER = ">"
LEASE_FIELD_DELIMITER_ESCAPED = "&#62"
LEASE_DNS_INDEX = 2
LEASE_HOSTNAME_INDEX = 3
LEASE_MIN_FIELDS = 2
IPV4_VERSION = 4


@dataclass(frozen=True)
class StaticDHCPLease:
    """Static DHCP lease entry."""

    mac: str
    ip: str
    dns: str = ""
    hostname: str = ""


def _empty_asus_value(value: Any) -> bool:
    """Return whether a value should be sent as an empty ASUS field."""

    return value is None or value in ("", "None")


def _clean_asus_value(value: Any) -> str:
    """Convert an optional ASUS field to string."""

    if _empty_asus_value(value):
        return ""

    return str(value)


def _check_field_delimiters(value: str) -> None:
    """Check that a lease field does not contain ASUS delimiters."""

    if (
        LEASE_DELIMITER in value
        or LEASE_DELIMITER_ESCAPED in value
        or LEASE_FIELD_DELIMITER in value
        or LEASE_FIELD_DELIMITER_ESCAPED in value
    ):
        raise ValueError("Static DHCP lease fields cannot contain delimiters")


def _normalize_mac(value: Any) -> str:
    """Normalize a MAC address to ASUS format."""

    return MacAddress.from_value(value).as_asus()


def normalize_static_dhcp_mac(value: Any) -> str:
    """Normalize a static DHCP lease MAC address."""

    return _normalize_mac(value)


def _normalize_ipv4(value: Any, *, allow_empty: bool = False) -> str:
    """Normalize an IPv4 address."""

    if _empty_asus_value(value):
        if allow_empty:
            return ""
        raise ValueError("Static DHCP lease IP address is required")

    ip_address = IpAddress.from_value(value)
    if ip_address.version != IPV4_VERSION:
        raise ValueError("Static DHCP lease IP address must be IPv4")

    return str(ip_address)


def normalize_static_dhcp_lease(lease: StaticDHCPLease) -> StaticDHCPLease:
    """Normalize and validate a static DHCP lease."""

    mac = _normalize_mac(lease.mac)
    ip = _normalize_ipv4(lease.ip)
    dns = _normalize_ipv4(lease.dns, allow_empty=True)
    hostname = _clean_asus_value(lease.hostname)

    _check_field_delimiters(mac)
    _check_field_delimiters(ip)
    _check_field_delimiters(dns)
    _check_field_delimiters(hostname)

    return StaticDHCPLease(mac=mac, ip=ip, dns=dns, hostname=hostname)


def parse_static_dhcp_leases(content: str | None) -> list[StaticDHCPLease]:
    """Parse an ASUS static DHCP lease list."""

    if not content:
        return []

    content = content.replace(
        LEASE_DELIMITER_ESCAPED,
        LEASE_DELIMITER,
    ).replace(LEASE_FIELD_DELIMITER_ESCAPED, LEASE_FIELD_DELIMITER)

    leases: list[StaticDHCPLease] = []

    for row in content.split(LEASE_DELIMITER):
        if not row:
            continue

        data = row.split(LEASE_FIELD_DELIMITER)
        if len(data) < LEASE_MIN_FIELDS or not data[0] or not data[1]:
            continue

        leases.append(
            StaticDHCPLease(
                mac=data[0],
                ip=data[1],
                dns=data[LEASE_DNS_INDEX]
                if len(data) > LEASE_DNS_INDEX
                else "",
                hostname=data[LEASE_HOSTNAME_INDEX]
                if len(data) > LEASE_HOSTNAME_INDEX
                else "",
            )
        )

    return leases


def compile_static_dhcp_leases(
    leases: Iterable[StaticDHCPLease],
) -> dict[str, str | int] | None:
    """Compile static DHCP leases to ASUS request arguments."""

    if not isinstance(leases, list):
        return None

    normalized_leases = [
        normalize_static_dhcp_lease(lease) for lease in leases
    ]

    mac_addresses: set[str] = set()
    ip_addresses: set[str] = set()

    result = ""
    for lease in normalized_leases:
        if lease.mac in mac_addresses:
            raise ValueError(f"Duplicate static DHCP lease MAC: {lease.mac}")
        if lease.ip in ip_addresses:
            raise ValueError(f"Duplicate static DHCP lease IP: {lease.ip}")

        mac_addresses.add(lease.mac)
        ip_addresses.add(lease.ip)

        result += (
            f"{LEASE_DELIMITER}{lease.mac}{LEASE_FIELD_DELIMITER}{lease.ip}"
            f"{LEASE_FIELD_DELIMITER}{lease.dns}"
            f"{LEASE_FIELD_DELIMITER}{lease.hostname}"
        )

    return {
        KEY_STATIC_DHCP_LIST: result,
        KEY_STATIC_DHCP_STATE: 1 if normalized_leases else 0,
    }
