"""WiFi module for AsusRouter."""

from __future__ import annotations

from enum import IntEnum

from asusrouter.const import UNKNOWN_MEMBER
from asusrouter.tools.enum import FromIntMixin


class ARWiFiGeneration(FromIntMixin, IntEnum):
    """WiFi generation types."""

    UNKNOWN = UNKNOWN_MEMBER

    WIFI_5 = 5
    WIFI_6 = 6
    WIFI_7 = 7


class ARWiFiMultiBand(FromIntMixin, IntEnum):
    """WiFi multiband types."""

    UNKNOWN = UNKNOWN_MEMBER

    SINGLEBAND = 1
    DUALBAND = 2
    TRIBAND = 3
    QUADBAND = 4
