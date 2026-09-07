"""Tests for parental-control rule-table capacity validation."""

from __future__ import annotations

from copy import deepcopy

import pytest

from asusrouter.modules.parental_control import (
    DEFAULT_PC_TIMEMAP,
    ParentalControlCapabilities,
    ParentalControlCapacityError,
    ParentalControlRule,
    PCRuleType,
    count_pc_rule_entries,
    validate_pc_capacity,
)


def _rules(
    row_count: int, timemap: str | None = "window"
) -> dict[str, ParentalControlRule]:
    """Build a rule table of the requested size."""

    return {
        f"row-{index}": ParentalControlRule(
            mac=f"row-{index}",
            timemap=timemap,
            type=PCRuleType.TIME,
        )
        for index in range(row_count)
    }


def _one_rule_with_windows(
    window_count: int,
) -> dict[str, ParentalControlRule]:
    """Build one row containing the requested number of raw windows."""

    timemap = "<".join(f"window-{index}" for index in range(window_count))
    return _rules(1, timemap)


def test_count_pc_rule_entries_counts_all_raw_segments_without_mutation() -> (
    None
):
    """Count every row and every non-empty raw schedule segment."""

    rules = {
        "default": ParentalControlRule(timemap=DEFAULT_PC_TIMEMAP),
        "mixed": ParentalControlRule(
            timemap="W03E021000700&#60W04122000800<opaque-malformed"
        ),
        "none": ParentalControlRule(timemap=None),
        "empty": ParentalControlRule(timemap=""),
    }
    original = deepcopy(rules)

    assert count_pc_rule_entries(rules) == (4, 5)
    assert rules == original


@pytest.mark.parametrize("timemap", [None, ""])
def test_count_pc_rule_entries_empty_timemap(timemap: str | None) -> None:
    """Count missing and empty timemaps as zero windows."""

    assert count_pc_rule_entries(_rules(1, timemap)) == (1, 0)


def test_count_pc_rule_entries_default_map() -> None:
    """Count the generated default timemap as two windows."""

    assert count_pc_rule_entries({"row": ParentalControlRule()}) == (1, 2)


def test_validate_pc_capacity_exactly_at_limits() -> None:
    """Allow growth up to both advertised limits."""

    validate_pc_capacity(
        _rules(1, "window"),
        _one_rule_with_windows(2),
        ParentalControlCapabilities(max_rules=1, max_entries=2),
    )


def test_validate_pc_capacity_rule_limit_one_over() -> None:
    """Reject row growth beyond an advertised rule limit."""

    with pytest.raises(ParentalControlCapacityError) as raised:
        validate_pc_capacity(
            _rules(16, ""),
            _rules(17, ""),
            ParentalControlCapabilities(max_rules=16, max_entries=128),
        )

    assert str(raised.value) == (
        "Parental-control update would increase rules from 16 to 17; "
        "router limit is 16 (MaxRule_parentctrl)."
    )


def test_validate_pc_capacity_window_limit_one_over() -> None:
    """Reject window growth beyond an advertised schedule limit."""

    with pytest.raises(ParentalControlCapacityError) as raised:
        validate_pc_capacity(
            _one_rule_with_windows(2),
            _one_rule_with_windows(3),
            ParentalControlCapabilities(max_rules=16, max_entries=2),
        )

    assert str(raised.value) == (
        "Parental-control update would increase schedule windows from 2 to 3; "
        "router limit is 2 (MaxRule_PC_DAYTIME)."
    )


def test_validate_pc_capacity_assumed_limit_wording() -> None:
    """Identify an unreported fallback limit as assumed."""

    with pytest.raises(ParentalControlCapacityError) as raised:
        validate_pc_capacity(
            _rules(16, ""),
            _rules(17, ""),
            ParentalControlCapabilities(),
        )

    assert str(raised.value) == (
        "Parental-control update would increase rules from 16 to 17; "
        "router limit is 16 "
        "(assumed because the router did not report it)."
    )


def test_validate_pc_capacity_shrinking_over_limit_table() -> None:
    """Allow an over-limit legacy table to shrink but remain over fallback."""

    validate_pc_capacity(
        _rules(18, ""),
        _rules(17, ""),
        ParentalControlCapabilities(),
    )


def test_validate_pc_capacity_growing_over_limit_table() -> None:
    """Reject additional growth of a table that is already over its limit."""

    with pytest.raises(ParentalControlCapacityError) as raised:
        validate_pc_capacity(
            _rules(17, ""),
            _rules(18, ""),
            ParentalControlCapabilities(),
        )

    assert str(raised.value) == (
        "Parental-control update would increase rules from 17 to 18; "
        "router limit is 16 "
        "(assumed because the router did not report it)."
    )
