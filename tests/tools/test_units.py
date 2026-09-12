"""Tests for the unit tools."""

from __future__ import annotations

from enum import StrEnum

import pytest

from asusrouter.error import AsusRouterError
from asusrouter.tools.units import UnitConverterBase


class MockUnits(StrEnum):
    """Mock units for testing."""

    BASE = "base"
    MEGABASE = "megabase"
    TERABASE = "terabase"


MOCK_UNIT_CLASS = "mock_units"

MOCK_UNIT_RATIOS: dict[StrEnum, float] = {
    MockUnits.BASE: 1,
    MockUnits.MEGABASE: 10**6,
    MockUnits.TERABASE: 10**12,
}


class TestUnitConverter(UnitConverterBase):
    """Test unit converter base."""

    UNIT_CLASS = MOCK_UNIT_CLASS
    _UNIT_RATIO = MOCK_UNIT_RATIOS

    @pytest.mark.parametrize(
        ("value", "unit", "expected"),
        [
            (1.0, MockUnits.BASE, 1.0),
            (1.0, MockUnits.MEGABASE, float(10**6)),
            (2.5, MockUnits.TERABASE, 2.5 * 10**12),
        ],
    )
    def test_convert_to_base(
        self, value: float, unit: MockUnits, expected: float
    ) -> None:
        """Test the convert_to_base method."""

        result = self.convert_to_base(value, unit)
        assert result == pytest.approx(expected)

    def test_convert_to_base_unknown_unit(self) -> None:
        """Test convert_to_base raises AsusRouterError for unknown units."""

        with pytest.raises(AsusRouterError, match="Unknown unit"):
            self.convert_to_base(1.0, "not_a_unit")  # type: ignore[arg-type]
