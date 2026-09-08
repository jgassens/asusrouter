"""Tests for secure router dumps."""

from __future__ import annotations

import json
from pathlib import Path
import stat
import zipfile

import pytest

from asusrouter.modules.endpoint import Endpoint, EndpointService
from asusrouter.tools.dump import AsusRouterDump, _redact_content


def _only_dump_directory(output: Path) -> Path:
    """Return the only generated dump directory."""

    directories = [path for path in output.iterdir() if path.is_dir()]
    assert len(directories) == 1
    return directories[0]


def _only_archive(output: Path) -> Path:
    """Return the only generated dump archive."""

    archives = list(output.glob("*.zip"))
    assert len(archives) == 1
    return archives[0]


@pytest.mark.asyncio
async def test_dump_redacts_login_and_nested_router_secrets(
    tmp_path: Path,
) -> None:
    """Redact credentials, tokens, headers, and nested NVRAM values."""

    secrets = {
        "login": "SENTINEL-LOGIN-PAYLOAD",
        "authorization": "SENTINEL-AUTHORIZATION",
        "cookie": "SENTINEL-COOKIE",
        "token": "SENTINEL-TOKEN",
        "password": "SENTINEL-PASSWORD",
        "passwd": "SENTINEL-PASSWD",
        "psk": "SENTINEL-PSK",
        "wpa_psk": "SENTINEL-WPA-PSK",
        "radius_key": "SENTINEL-RADIUS-KEY",
        "key": "SENTINEL-KEY",
        "secret": "SENTINEL-SECRET",
        "vpnc_clientlist": "SENTINEL-VPN-CLIENTS",
        "http_passwd": "SENTINEL-HTTP-PASSWD",
    }
    with AsusRouterDump(str(tmp_path), full_dump=True, archive=False) as dump:
        await dump.dump(
            EndpointService.LOGIN,
            {
                "login_authorization": secrets["login"],
                "safe_request_value": "login-safe",
            },
            200,
            {
                "Authorization": secrets["authorization"],
                "Set-Cookie": secrets["cookie"],
                "X-Api-Token": secrets["token"],
                "Server": "router-safe",
            },
            json.dumps(
                {
                    "asus_token": secrets["token"],
                    "model": "RT-AX88U",
                }
            ).encode(),
        )
        await dump.dump(
            Endpoint.HOOK,
            {
                "Password": secrets["password"],
                "mode": "read-safe",
            },
            200,
            {
                "Cookie": secrets["cookie"],
                "Content-Type": "application/json",
            },
            json.dumps(
                {
                    "wireless": [
                        {
                            "wl0_wpa_psk": secrets["wpa_psk"],
                            "ssid": "Home-safe",
                        },
                        {"wl0_radius_key": secrets["radius_key"]},
                        {"wgc5_psk": secrets["psk"]},
                    ],
                    "nested": {
                        "PASSWD": secrets["passwd"],
                        "key": secrets["key"],
                        "secret": secrets["secret"],
                        "http_passwd": secrets["http_passwd"],
                    },
                    "vpnc_clientlist": secrets["vpnc_clientlist"],
                    "uptime": 123,
                }
            ).encode(),
        )

    dump_directory = _only_dump_directory(tmp_path)
    dumped_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in dump_directory.iterdir()
        if path.is_file()
    )
    for secret in secrets.values():
        assert secret not in dumped_text

    login_metadata_path = next(
        dump_directory.glob(f"*-{EndpointService.LOGIN}.json")
    )
    login_metadata = json.loads(login_metadata_path.read_text())
    assert login_metadata["payload"] is None
    assert login_metadata["resp_headers"]["Server"] == "router-safe"
    assert login_metadata["resp_headers"]["Authorization"] == "[REDACTED]"
    assert login_metadata["resp_headers"]["Set-Cookie"] == "[REDACTED]"
    assert login_metadata["resp_headers"]["X-Api-Token"] == "[REDACTED]"

    login_content_path = next(
        dump_directory.glob(f"*-{EndpointService.LOGIN}.content")
    )
    login_content = json.loads(login_content_path.read_text())
    assert login_content == {
        "asus_token": "[REDACTED]",
        "model": "RT-AX88U",
    }

    hook_metadata_path = next(dump_directory.glob(f"*-{Endpoint.HOOK}.json"))
    hook_metadata = json.loads(hook_metadata_path.read_text())
    assert hook_metadata["payload"]["mode"] == "read-safe"
    assert hook_metadata["payload"]["Password"] == "[REDACTED]"
    assert hook_metadata["resp_headers"]["Cookie"] == "[REDACTED]"
    assert hook_metadata["resp_headers"]["Content-Type"] == (
        "application/json"
    )

    hook_content_path = next(dump_directory.glob(f"*-{Endpoint.HOOK}.content"))
    hook_content = json.loads(hook_content_path.read_text())
    assert hook_content["wireless"][0]["ssid"] == "Home-safe"
    assert hook_content["wireless"][0]["wl0_wpa_psk"] == "[REDACTED]"
    assert hook_content["wireless"][1]["wl0_radius_key"] == "[REDACTED]"
    assert hook_content["wireless"][2]["wgc5_psk"] == "[REDACTED]"
    assert set(hook_content["nested"]) == {
        "PASSWD",
        "key",
        "secret",
        "http_passwd",
    }
    assert set(hook_content["nested"].values()) == {"[REDACTED]"}
    assert hook_content["vpnc_clientlist"] == "[REDACTED]"
    assert hook_content["uptime"] == 123

    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in dump_directory.iterdir()
        if path.is_file()
    )


@pytest.mark.asyncio
async def test_dump_archive_is_private_and_redacted(tmp_path: Path) -> None:
    """Create a private ZIP and redact sensitive values in its members."""

    token = "SENTINEL-ARCHIVE-TOKEN"
    with AsusRouterDump(str(tmp_path), full_dump=True, archive=True) as dump:
        await dump.dump(
            Endpoint.HOOK,
            {"safe": "kept"},
            200,
            {"X-Token": token},
            json.dumps({"token": token, "safe": "kept"}).encode(),
        )

    archive = _only_archive(tmp_path)
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    with zipfile.ZipFile(archive) as dump_zip:
        dumped = b"\n".join(
            dump_zip.read(name) for name in dump_zip.namelist()
        )
    assert token.encode() not in dumped
    assert b'"safe": "kept"' in dumped


def test_loose_text_redacts_only_sensitive_values() -> None:
    """Router text that is not JSON keeps its shape; only secrets go."""

    body = (
        "wl0_ssid=Home-safe;wl0_wpa_psk=SENTINEL-PSK;"
        "http_passwd=SENTINEL-HTTP;"
        "{'asus_token': 'SENTINEL-TOKEN', 'model': 'RT-AX88U'}"
        " the keyboard token_bucket note stays"
    )
    redacted = _redact_content(body)

    assert "SENTINEL" not in redacted
    assert "wl0_ssid=Home-safe" in redacted
    assert "'model': 'RT-AX88U'" in redacted
    assert "keyboard token_bucket note stays" in redacted
