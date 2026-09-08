# Changelog

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
