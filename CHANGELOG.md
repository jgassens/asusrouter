# Changelog

## 2.0.0+jgassens.3

- Removed the unused masking and legacy port-forwarding writer modules and the
  three legacy whole-table port-forwarding methods. Also removed unused data
  unit conversion, timestamp, list, reader, security, color, source and
  registry helpers, plus the obsolete `DeviceOperationMode` and `AccessPoint`
  types.
- Connection: a caller's own cancellation now propagates instead of being
  swallowed as a failed connect; the login fallback no longer cancels the
  task it runs inside; a login on a closed session no longer awaits itself.
  On the request path `asyncio.CancelledError` is no longer wrapped as
  `AsusRouterTimeoutError`; callers that caught that around shutdown now
  see the cancellation itself.
- Connection: redirects are no longer followed (`allow_redirects=False`;
  any 3xx raises `AsusRouterDataError`). A setup that relied on the router
  or a proxy redirecting an authenticated POST elsewhere must point the
  library at the final address. Response bodies are capped at 8 MiB,
  checked against both the declared length and the decompressed stream;
  an unknown declared charset falls back to UTF-8.
- Authorization retry is bounded to one re-login per request instead of
  recursing; a firmware update without release-note endpoints no longer logs
  a warning on every poll.
- Each `AsusRouter` instance owns its data and state lookup maps. Rules
  removed or added for one router (Merlin vs stock, DSL, VPN generation) no
  longer leak into other routers in the same process.
- Parental control: `async_set_state` for a rule now holds a lock and
  refetches the table before writing; a failed refetch aborts the write.
  Rule names are limited to 32 characters and names and schedules reject
  delimiter and control characters. Unknown rule type codes map to
  `PCRuleType.UNKNOWN`. Block-all is saved under `block_all`, not `state`.
- Parsers skip malformed port-forwarding, VPN status, dual-WAN, port-status
  and sysinfo rows instead of raising; deeply nested JSON raises
  `AsusRouterDataError` instead of a recursion error.
- OpenVPN and WireGuard client IDs are validated as integers 1 to 5 before
  they are placed into an `rc_service` command.
- Parental control: `check_rule` rejects a MAC that is not colon-separated
  hex, since the MAC is written into the delimited rule table and doubles
  as the default name.
- Every request carries the configured timeout (default 15 s) even on a
  caller-supplied session, so a dripping response cannot stay open
  indefinitely. This replaces, rather than merges with, a longer timeout
  set on the session; pass `timeout=` to `AsusRouter` for a slow router.
- Parsers: an oversized integer literal, a non-numeric `error_status` and
  an uptime beyond the datetime range no longer escape as raw
  `ValueError`/`OverflowError`.
- `async_call_service` no longer mutates the caller's arguments dict.
- Dump tool: files are created private (0600, exclusive), the login payload
  is not recorded, and tokens, cookies, passwords, PSKs, RADIUS keys and
  WireGuard private keys are redacted in metadata, logs and content. In
  loose text a secret value is redacted to the end of its line, so
  semicolons, `&` or escaped quotes inside it cannot leak a tail. The CLI reads the password from a
  prompt or `--password-stdin` and warns when it is given on the command line.
- Removed unused code: `AsusRouterSessionError`, `CLIENT_MAP`, `ARWiFiBand`,
  `ARWiFiFrequency`, `PORT_SUBTYPE`, the empty RGB endpoint module and
  several no-op helpers. `handle_access_error` now takes the content and an
  optional endpoint. `Connection.async_connect` no longer accepts `lock`.
- Version bumped so that pip and Home Assistant reinstall the library.

## 2.0.0+jgassens.2

- Parental control: forced reads now raise on fetch failure instead of
  returning cached data, and an incomplete rule response omits `rules`
  instead of reporting an empty table.
- Parental control: `set_rule` refuses to write without a known, current
  rule table and never mutates the cached table in place.
- Parental control: successful rule writes invalidate the cached table so the
  next read refetches; in-flight reads cannot re-freshen stale data.
- Parental control: empty rule names and schedules round-trip as empty strings
  instead of the literal `None`.
- Added `ServiceResult` and `async_run_service_result` exposing the router's
  reported `restart_needed_time` per call; `async_run_service` is unchanged.
- Added `async_get_parental_control_capabilities`, `count_pc_rule_entries`
  and `validate_pc_capacity` for pre-write capacity checks against
  `MaxRule_parentctrl` / `MaxRule_PC_DAYTIME`.
- A failed update no longer leaves readers blocked; each data state now has
  its own waiter event.
- Routine debug logs and service exceptions no longer include raw request
  arguments, cached data, response bodies or the VPNC client list;
  `applyapp.cgi` is treated as a sensitive endpoint.
- Version bumped so that pip and Home Assistant reinstall the library: pip
  decides by version, not by pinned commit.

## 2.0.0+jgassens.1

- Established an independently maintained stable branch for the Home Assistant
  AsusRouter Fixed IP integration.
- Added guarded static DHCP reservation parsing and HTTP(S) write helpers.
- Added duplicate-address, malformed-input, and write-readback protections.
- Removed inherited publication and bot workflows that depended on the source
  project's credentials.
- Preserved Apache-2.0 licensing, attribution, and repository history.
