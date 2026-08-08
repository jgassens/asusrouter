# AsusRouter HTTP Client

This repository is the independently maintained `asusrouter` Python library
used by [AsusRouter Fixed IP](https://github.com/jgassens/ha-asusrouter).
It communicates with ASUSWRT routers through their authenticated HTTP(S) WebUI
interface and includes guarded static-DHCP reservation support.

Current maintenance release: **v2.0.0+jgassens.1**.

## Purpose

The stable **main** branch is the compatibility line consumed by the Home
Assistant integration. The integration pins an exact Git commit so a router
control release cannot change underneath an installed Home Assistant version.

This package is maintained independently and is not published over the
source project's PyPI package. Install this maintenance line from GitHub.

## Static DHCP API

```python
from asusrouter import StaticDHCPLease

leases = await router.async_get_static_dhcp_leases()

await router.async_set_static_dhcp_lease(
    mac="AA:BB:CC:DD:EE:FF",
    ip="192.168.1.50",
    hostname="printer",
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

The set and remove helpers preserve unrelated reservations. The full-list
apply helper intentionally replaces the list and should be used with care.

Inputs are normalized and validated for malformed MAC addresses, invalid IPv4
values, duplicate MAC or IP assignments, and delimiter injection. Writes are
read back from the router by the Home Assistant integration.

## Install

```sh
pip install "asusrouter @ git+https://github.com/jgassens/asusrouter.git@v2.0.0+jgassens.1"
```

Applications that control real routers should pin a release tag or commit
rather than installing from a moving branch.

## Compatibility

- Python 3.11 through 3.14.
- Stock ASUSWRT 3.0.0.4.x and 3.0.0.6.x are the primary target.
- AsusWRT-Merlin is expected to work where it exposes the same WebUI state and
  apply actions.
- Router SSH is not required for static DHCP operations.

ASUS models and firmware revisions vary. Back up the router configuration and
verify the first write in the ASUS WebUI.

## Maintenance

Development targets **main**. Source-project changes may be evaluated and
ported when useful, but this repository does not automatically merge or rebase
them. Compatibility with the released Home Assistant integration takes
priority over adopting an incompatible API rewrite.

Report library or router-protocol problems in
[this repository's issue tracker](https://github.com/jgassens/asusrouter/issues).
Home Assistant UI and action problems belong in
[jgassens/ha-asusrouter](https://github.com/jgassens/ha-asusrouter/issues).

## Development

```sh
uv sync --all-groups
uv run pytest
uv run ruff check asusrouter tests
uv run ruff format --check asusrouter tests
```

## Attribution

This project is derived from
[Vaskivskyi/asusrouter](https://github.com/Vaskivskyi/asusrouter) and is
maintained independently. It is not affiliated with ASUS or endorsed by the
source project's maintainers.

The Apache-2.0 license, NOTICE file, original authorship, and Git history are
preserved.
