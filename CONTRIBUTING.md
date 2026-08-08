# Contributing to AsusRouter HTTP Client

Issues and pull requests are welcome in
[jgassens/asusrouter](https://github.com/jgassens/asusrouter).

## Set Up

```sh
git clone https://github.com/jgassens/asusrouter.git
cd asusrouter
uv sync --all-groups
uv run prek install
```

Development targets **main**. Create a short-lived branch for each change.

## Validate Changes

```sh
uv run pytest
uv run prek run --all-files
```

Router-write changes must include focused parsing, validation, failure, and
round-trip tests. Do not make broader firmware compatibility claims without
source evidence or hardware validation.

## Pull Requests

- Explain the router or application behavior being changed.
- Include tests for new behavior and regressions.
- Preserve public API compatibility unless the change is explicitly versioned.
- Preserve Apache-2.0 attribution and existing Git history.

Questions, bugs, and feature requests belong in the
[project issue tracker](https://github.com/jgassens/asusrouter/issues).
