"""Tests for the AsusRouter command-line interface."""

from __future__ import annotations

from io import StringIO
import logging
from pathlib import Path
import sys
from unittest.mock import AsyncMock, Mock

import pytest

from asusrouter import __main__ as cli
from asusrouter.error import AsusRouterAccessError
from asusrouter.modules.endpoint import Endpoint, error as endpoint_error
from asusrouter.modules.endpoint.error import AccessError, handle_access_error


def _run_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *extra_args: str,
) -> AsyncMock:
    """Run the CLI with its network operation replaced."""

    connect = AsyncMock()
    monkeypatch.setattr(cli, "_connect_and_dump", connect)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "asusrouter",
            "--output",
            str(tmp_path),
            "--host",
            "router.test",
            *extra_args,
        ],
    )
    cli.main()
    return connect


def test_main_reads_password_from_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read a password from standard input when explicitly requested."""

    password = "SENTINEL-STDIN-PASSWORD"
    monkeypatch.setattr(sys, "stdin", StringIO(f"{password}\n"))
    prompt = Mock(side_effect=AssertionError("getpass should not be called"))
    monkeypatch.setattr(cli, "getpass", prompt)

    connect = _run_cli(monkeypatch, tmp_path, "--password-stdin")

    assert connect.await_args.args[0].password == password
    prompt.assert_not_called()


def test_main_prompts_for_password_when_not_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Prompt without echo when neither password option is supplied."""

    password = "SENTINEL-PROMPTED-PASSWORD"
    prompt = Mock(return_value=password)
    monkeypatch.setattr(cli, "getpass", prompt)

    connect = _run_cli(monkeypatch, tmp_path)

    assert connect.await_args.args[0].password == password
    prompt.assert_called_once()


def test_main_warns_when_password_is_on_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retain the password option while warning that argv is observable."""

    password = "SENTINEL-ARGV-PASSWORD"
    caplog.set_level(logging.WARNING, logger=cli.__name__)

    connect = _run_cli(monkeypatch, tmp_path, "--password", password)

    assert connect.await_args.args[0].password == password
    assert "--password" in caplog.text
    assert password not in caplog.text


def test_access_error_log_omits_response_object(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log only the access-error type and originating endpoint."""

    secret = "SENTINEL-ACCESS-ERROR-BODY"
    monkeypatch.setattr(
        endpoint_error,
        "read_json_content",
        Mock(
            return_value={
                "error_status": AccessError.CREDENTIALS,
                "http_passwd": secret,
            }
        ),
    )
    caplog.set_level(logging.DEBUG, logger=endpoint_error.__name__)

    with pytest.raises(AsusRouterAccessError):
        handle_access_error(None, endpoint=Endpoint.HOOK)

    assert secret not in caplog.text
    assert AccessError.CREDENTIALS.name in caplog.text
    assert Endpoint.HOOK in caplog.text
