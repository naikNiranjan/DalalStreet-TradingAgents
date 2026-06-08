"""Task 0 — NSE 2026 holiday calendar blocker.

Asserts (a) the shipped DEFAULT_CONFIG carries a usable full-year 2026 list,
(b) movable holidays like Diwali are treated as closed, and (c) the startup
guard fails closed on an empty / partial / malformed list.
"""

from __future__ import annotations

import pytest

from execution.calendar_guard import (
    MIN_HOLIDAYS_PER_YEAR,
    CalendarNotConfigured,
    assert_calendar_ready,
)
from tradingagents.dataflows import india_calendar
from tradingagents.default_config import DEFAULT_CONFIG

NSE_2026 = DEFAULT_CONFIG["nse_holidays"]


# --- the shipped 2026 list is real & usable ------------------------------------

def test_default_config_2026_list_passes_guard():
    assert_calendar_ready(DEFAULT_CONFIG, 2026)  # must not raise


def test_default_config_has_plausible_2026_count():
    y2026 = [h for h in NSE_2026 if h.startswith("2026-")]
    assert len(y2026) >= MIN_HOLIDAYS_PER_YEAR
    assert len(y2026) == len(set(y2026)), "duplicate holiday dates"


# --- movable holidays are treated as closed ------------------------------------

@pytest.mark.parametrize(
    "movable",
    [
        "2026-03-03",  # Holi
        "2026-04-03",  # Good Friday
        "2026-05-28",  # Bakri Id
        "2026-10-20",  # Dussehra
        "2026-11-10",  # Diwali Balipratipada
        "2026-11-24",  # Guru Nanak Jayanti
    ],
)
def test_movable_holiday_is_not_a_trading_day(movable):
    assert not india_calendar.is_trading_day(movable, holidays=NSE_2026)
    assert india_calendar.is_trading_holiday(movable, holidays=NSE_2026)


def test_diwali_2026_market_closed_during_session_hours():
    # 2026-11-10 12:00 IST would otherwise be inside 09:15-15:30 trading hours.
    from datetime import datetime

    midday = datetime(2026, 11, 10, 12, 0)
    assert not india_calendar.is_market_open(midday, holidays=NSE_2026)


def test_ordinary_weekday_is_a_trading_day():
    # 2026-06-09 is a Tuesday and not in the holiday list.
    assert india_calendar.is_trading_day("2026-06-09", holidays=NSE_2026)


def test_muhurat_sunday_is_left_closed_by_weekend_logic():
    # Muhurat 2026-11-08 is a Sunday special session; Phase 3 conservatively does
    # not trade it — the weekend logic keeps it closed and it is NOT a holiday entry.
    assert india_calendar.is_weekend("2026-11-08")
    assert "2026-11-08" not in NSE_2026
    assert not india_calendar.is_trading_day("2026-11-08", holidays=NSE_2026)


# --- the guard fails closed ----------------------------------------------------

def test_empty_list_raises():
    with pytest.raises(CalendarNotConfigured):
        assert_calendar_ready({"nse_holidays": []}, 2026)


def test_none_raises():
    with pytest.raises(CalendarNotConfigured):
        assert_calendar_ready({}, 2026)


def test_partial_list_below_floor_raises():
    short = ["2026-01-26", "2026-10-02", "2026-12-25"]  # only the anchors
    with pytest.raises(CalendarNotConfigured):
        assert_calendar_ready({"nse_holidays": short}, 2026)


def test_missing_fixed_anchor_raises():
    # Plausible count but Republic Day removed -> not a real NSE calendar.
    no_anchor = [h for h in NSE_2026 if h != "2026-01-26"]
    with pytest.raises(CalendarNotConfigured) as exc:
        assert_calendar_ready({"nse_holidays": no_anchor}, 2026)
    assert "2026-01-26" in str(exc.value)


def test_malformed_entry_raises():
    bad = list(NSE_2026) + ["not-a-date"]
    with pytest.raises(CalendarNotConfigured):
        assert_calendar_ready({"nse_holidays": bad}, 2026)


def test_unconfigured_future_year_raises():
    # The shipped list only covers 2026; running a 2027 pass must refuse until the
    # 2027 list is added.
    with pytest.raises(CalendarNotConfigured):
        assert_calendar_ready(DEFAULT_CONFIG, 2027)


def test_holidays_override_takes_precedence_over_config():
    full = {"nse_holidays": []}  # empty config...
    assert_calendar_ready(full, 2026, holidays=NSE_2026)  # ...overridden, passes
