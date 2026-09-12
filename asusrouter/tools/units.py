"""Units tools."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from asusrouter.error import AsusRouterError


class UnitOfDataRate(StrEnum):
    """Units of data rate."""

    # Base unit
    BIT_PER_SECOND = "bps"
    BYTE_PER_SECOND = "Bps"
    # Base 10
    KILOBIT_PER_SECOND = "kbps"
    MEGABIT_PER_SECOND = "Mbps"
    GIGABIT_PER_SECOND = "Gbps"
    TERABIT_PER_SECOND = "Tbps"
    KILOBYTE_PER_SECOND = "KBps"
    MEGABYTE_PER_SECOND = "MBps"
    GIGABYTE_PER_SECOND = "GBps"
    TERABYTE_PER_SECOND = "TBps"
    # Base 2 (native Asus Type)
    KIBIBIT_PER_SECOND = "Kibps"
    MEBIBIT_PER_SECOND = "Mibps"
    GIBIBIT_PER_SECOND = "Gibps"
    TEBIBIT_PER_SECOND = "Tibps"
    KIBIBYTE_PER_SECOND = "KiBps"
    MEBIBYTE_PER_SECOND = "MiBps"
    GIBIBYTE_PER_SECOND = "GiBps"
    TEBIBYTE_PER_SECOND = "TiBps"


class UnitConverterBase:
    """AsusRouter Unit Converter."""

    UNIT_CLASS: ClassVar[str]
    _UNIT_RATIO: ClassVar[dict[StrEnum, float]]

    @classmethod
    def convert_to_base(cls, value: float, from_unit: StrEnum) -> float:
        """Convert a value to the base unit."""

        try:
            return value * cls._UNIT_RATIO[from_unit]
        except KeyError:
            raise AsusRouterError(
                f"Unknown unit `{from_unit}` encountered during "
                f"conversion of `{cls.UNIT_CLASS}`"
            )


class DataRateUnitConverter(UnitConverterBase):
    """Data Rate Unit Converter."""

    UNIT_CLASS = "data_rate"

    _UNIT_RATIO = {
        UnitOfDataRate.BIT_PER_SECOND: 1,
        UnitOfDataRate.BYTE_PER_SECOND: 8,
        UnitOfDataRate.KILOBIT_PER_SECOND: 1e3,
        UnitOfDataRate.MEGABIT_PER_SECOND: 1e6,
        UnitOfDataRate.GIGABIT_PER_SECOND: 1e9,
        UnitOfDataRate.TERABIT_PER_SECOND: 1e12,
        UnitOfDataRate.KIBIBIT_PER_SECOND: 2**10,
        UnitOfDataRate.MEBIBIT_PER_SECOND: 2**20,
        UnitOfDataRate.GIBIBIT_PER_SECOND: 2**30,
        UnitOfDataRate.TEBIBIT_PER_SECOND: 2**40,
        UnitOfDataRate.KILOBYTE_PER_SECOND: 8e3,
        UnitOfDataRate.MEGABYTE_PER_SECOND: 8e6,
        UnitOfDataRate.GIGABYTE_PER_SECOND: 8e9,
        UnitOfDataRate.TERABYTE_PER_SECOND: 8e12,
        UnitOfDataRate.KIBIBYTE_PER_SECOND: 2**13,
        UnitOfDataRate.MEBIBYTE_PER_SECOND: 2**23,
        UnitOfDataRate.GIBIBYTE_PER_SECOND: 2**33,
        UnitOfDataRate.TEBIBYTE_PER_SECOND: 2**43,
    }
