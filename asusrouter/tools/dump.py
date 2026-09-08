"""Dump tools for AsusRouter.

This module contains all needed to dump the raw data from the router
for the following analysis and testing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
from types import TracebackType
from typing import Any, BinaryIO, Self, TextIO
import zipfile

from asusrouter.modules.endpoint import EndpointService, EndpointType

_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "http_passwd",
        "key",
        "passwd",
        "password",
        "psk",
        "radius_key",
        "secret",
        "set_cookie",
        "token",
        "vpnc_clientlist",
        "wpa_psk",
    }
)
_WORD_PATTERN = re.compile(r"[a-zA-Z0-9_-]+")


def _is_sensitive_key(key: object) -> bool:
    """Return whether a key identifies secret data."""

    normalized = str(key).casefold().replace("-", "_")
    return any(
        normalized == sensitive or normalized.endswith(f"_{sensitive}")
        for sensitive in _SENSITIVE_KEYS
    )


def _contains_sensitive_key(value: str) -> bool:
    """Return whether unstructured text names a sensitive field."""

    return any(
        _is_sensitive_key(word) for word in _WORD_PATTERN.findall(value)
    )


def _redact(value: Any) -> Any:
    """Recursively replace values associated with sensitive keys."""

    if isinstance(value, Mapping):
        return {
            key: _REDACTED if _is_sensitive_key(key) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact(item) for item in value]
    if isinstance(value, str) and _contains_sensitive_key(value):
        return _REDACTED
    return value


def _redact_content(content: str | bytes) -> str:
    """Decode and structurally redact response content when possible."""

    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    else:
        text = content

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return _redact(text)
    return json.dumps(_redact(parsed), default=str)


def _open_private_text(path: Path) -> TextIO:
    """Exclusively create a private UTF-8 text file."""

    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    return os.fdopen(descriptor, "w", encoding="utf-8")


def _open_private_binary(path: Path) -> BinaryIO:
    """Exclusively create a private seekable binary file."""

    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    return os.fdopen(descriptor, "w+b")


class AsusRouterDump:
    """AsusRouter Dumper."""

    _endpoint: EndpointType
    _datetime: str

    def __init__(
        self, output_folder: str, full_dump: bool = False, archive: bool = True
    ) -> None:
        """Initialize."""

        self.log: dict[str, str] = {}

        self._output_folder = Path(output_folder)
        self.full_dump = full_dump
        self.zip = archive

        self._init_datetime = datetime.now(UTC)

        if self.zip:
            zip_name = (
                "AsusRouter-"
                f"{self._init_datetime.isoformat().replace(':', '-')}"
                ".zip"
            )
            self._zip_stream = _open_private_binary(
                self._output_folder / zip_name
            )
            try:
                self._zipfile = zipfile.ZipFile(self._zip_stream, "w")
            except Exception:
                self._zip_stream.close()
                raise
        else:
            # Create subfolder for the dump
            self._output_folder = self._output_folder / (
                "AsusRouter-"
                f"{self._init_datetime.isoformat().replace(':', '-')}"
            )
            self._output_folder.mkdir(mode=0o700, parents=True)

    def __enter__(self) -> Self:
        """Enter the runtime context related to this object."""

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Exit the runtime context and save the log."""

        if self.zip:
            # Write the log data to the zip file
            try:
                self._zipfile.writestr(
                    "log.json", json.dumps(_redact(self.log), default=str)
                )
            finally:
                try:
                    self._zipfile.close()
                finally:
                    self._zip_stream.close()
        else:
            with _open_private_text(self._output_folder / "log.json") as f:
                json.dump(_redact(self.log), f, default=str)

    async def dump(
        self,
        endpoint: EndpointType,
        payload: Any,
        resp_status: int,
        resp_headers: Mapping[str, Any],
        resp_content: str | bytes,
    ) -> None:
        """Dump the data."""

        self._endpoint = endpoint
        self._datetime = datetime.now(UTC).isoformat().replace(":", "-")

        self.log[self._datetime] = f"Endpoint.{endpoint.name}"

        # Write the response content to a file
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_content, resp_content)

        # Write the metadata to a file
        metadata = {
            "endpoint": endpoint,
            "payload": (
                None if endpoint == EndpointService.LOGIN else payload
            ),
            "resp_status": resp_status,
            "resp_headers": resp_headers,
        }
        await loop.run_in_executor(None, self._write_metadata, metadata)

    def _write_content(self, content: str | bytes) -> None:
        """Write the content to a file."""

        if self.full_dump:
            filename = f"{self._datetime}-{self._endpoint}.content"
            redacted = _redact_content(content)
            if self.zip:
                self._zipfile.writestr(filename, redacted)
            else:
                with _open_private_text(self._output_folder / filename) as f:
                    f.write(redacted)

    def _write_metadata(self, metadata: dict[str, Any]) -> None:
        """Write the metadata to a file."""

        if self.full_dump:
            filename = f"{self._datetime}-{self._endpoint}.json"
            redacted = _redact(metadata)
            if self.zip:
                self._zipfile.writestr(
                    filename, json.dumps(redacted, default=str)
                )
            else:
                with _open_private_text(self._output_folder / filename) as f:
                    json.dump(redacted, f, default=str)
