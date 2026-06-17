# Static DHCP Reservations for ASUSWRT

This fork branch adds fixed-IP DHCP reservation helpers to the `asusrouter`
Python library used by the Home Assistant AsusRouter integration.

The original library can read a lot of router state and can already apply some
router settings, but it did not expose a clean way for an agent or integration
to manage ASUSWRT manual DHCP assignments. This branch adds that missing
surface without requiring SSH.

## Problem

ASUS routers let users assign fixed IP addresses to devices through the Web UI:

`LAN > DHCP Server > Manually Assigned IP around the DHCP list`

That state is useful for automation, but the existing Python library did not
offer a supported helper for:

- reading fixed-IP reservations
- adding or updating one device reservation
- removing one device reservation
- replacing the reservation list intentionally

For my use case, Codex Terminal Pro needs to manage router reservations through
the same authenticated HTTP(S) path that Home Assistant already uses, instead
of logging in by SSH and editing router internals directly.

## How This Branch Solves It

ASUSWRT stores manual DHCP reservations in NVRAM:

- `dhcp_staticlist`: serialized static DHCP rows
- `dhcp_static_x`: enable flag for manual DHCP assignment

This branch adds a `StaticDHCPLease` dataclass plus async helpers on
`AsusRouter`:

```python
from asusrouter import StaticDHCPLease

leases = await router.async_get_static_dhcp_leases()

await router.async_set_static_dhcp_lease(
    mac="AA:BB:CC:DD:EE:FF",
    ip="192.168.1.50",
    hostname="printer",
    dns="1.1.1.1",
)

await router.async_remove_static_dhcp_lease("AA:BB:CC:DD:EE:FF")

await router.async_apply_static_dhcp_leases(
    [
        StaticDHCPLease(
            mac="AA:BB:CC:DD:EE:FF",
            ip="192.168.1.50",
            hostname="printer",
        ),
    ]
)
```

`async_set_static_dhcp_lease()` and `async_remove_static_dhcp_lease()` preserve
the rest of the router's current reservation list. `async_apply_static_dhcp_leases()`
replaces the full list and should be used deliberately.

## Wire Format

ASUSWRT serializes `dhcp_staticlist` as rows separated by `<` with fields
separated by `>`:

```text
<MAC>IP
<MAC>IP>DNS
<MAC>IP>DNS>hostname
```

This branch parses both raw delimiters and the escaped delimiters returned by
some ASUS Web UI endpoints, such as `&#60` and `&#62`.

When writing, it normalizes and validates:

- MAC addresses
- IPv4 reservation addresses
- optional IPv4 DNS values
- duplicate MAC addresses
- duplicate IP addresses
- delimiter characters inside user-provided fields

The apply path updates `dhcp_staticlist` and `dhcp_static_x`, then applies the
change with `restart_net_and_phy`, matching stock ASUSWRT Web UI behavior seen
in current firmware pages.

## Expected Compatibility

Expected to work on both stock ASUSWRT and AsusWRT-Merlin.

Evidence checked while building this branch:

- Older stock ASUSWRT sources use `dhcp_staticlist` and `dhcp_static_x`, then
  write static leases into `/etc/ethers`.
- Stock Blue Cave ASUSWRT Web UI reads and writes `dhcp_staticlist` as
  `<MAC>IP>DNS`.
- Current extracted stock RT-AX88U Pro Web UI reads and writes
  `<MAC>IP>DNS>hostname` and applies the DHCP page with `restart_net_and_phy`.
- Merlin's DHCP page uses the same `dhcp_staticlist` / `dhcp_static_x` state
  and compatible row parsing.

The branch has not yet been confirmed against a live stock router by actually
creating a reservation on hardware. The source/Web UI evidence is strong, but a
real-device smoke test is still the last mile.

## Install This Branch

```bash
pip install "asusrouter @ git+https://github.com/jgassens/asusrouter.git@codex/static-dhcp-leases"
```

## Test Status

Validated locally on this branch:

```text
pytest tests/modules/test_static_dhcp.py  # 18 passed
ruff check                               # passed
ruff format --check asusrouter tests     # passed
pytest                                   # 1596 passed
```

## Branch

Upstream base: `Vaskivskyi/asusrouter` `dev`

Fork branch:

```text
jgassens/asusrouter@codex/static-dhcp-leases
```
