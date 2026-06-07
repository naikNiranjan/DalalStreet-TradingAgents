"""Tests for the NSE market calendar (Phase 2E). Deterministic, no network."""

from datetime import datetime

import pytest

from tradingagents.dataflows import india_calendar as cal


@pytest.mark.unit
class TestTradingDay:
    def test_weekend_detection(self):
        # 2026-06-06 is a Saturday, 2026-06-07 a Sunday.
        assert cal.is_weekend("2026-06-06") is True
        assert cal.is_weekend("2026-06-07") is True
        assert cal.is_weekend("2026-06-08") is False  # Monday

    def test_builtin_holidays(self):
        for h in ("2026-01-26", "2026-10-02", "2026-12-25"):
            assert cal.is_trading_holiday(h) is True
            assert cal.is_trading_day(h) is False

    def test_regular_weekday_is_trading_day(self):
        assert cal.is_trading_day("2026-06-08") is True  # Monday, not a holiday

    def test_explicit_holidays_override(self):
        assert cal.is_trading_day("2026-06-08", holidays=["2026-06-08"]) is False


@pytest.mark.unit
class TestMarketHours:
    def test_open_during_session(self):
        # Monday 2026-06-08 10:00 IST -> open.
        dt = datetime(2026, 6, 8, 10, 0)
        assert cal.is_market_open(dt) is True

    def test_closed_before_and_after(self):
        assert cal.is_market_open(datetime(2026, 6, 8, 9, 0)) is False    # pre-open
        assert cal.is_market_open(datetime(2026, 6, 8, 16, 0)) is False   # after close

    def test_boundaries_inclusive(self):
        assert cal.is_market_open(datetime(2026, 6, 8, 9, 15)) is True
        assert cal.is_market_open(datetime(2026, 6, 8, 15, 30)) is True

    def test_closed_on_weekend(self):
        assert cal.is_market_open(datetime(2026, 6, 6, 11, 0)) is False   # Saturday

    def test_aware_datetime_converted_to_ist(self):
        import pytz
        # 04:30 UTC == 10:00 IST on a trading Monday -> open.
        utc_dt = pytz.utc.localize(datetime(2026, 6, 8, 4, 30))
        assert cal.is_market_open(utc_dt) is True


@pytest.mark.unit
class TestNavigation:
    def test_next_trading_day_skips_weekend(self):
        # Friday 2026-06-05 -> Monday 2026-06-08.
        assert cal.next_trading_day("2026-06-05").isoformat() == "2026-06-08"

    def test_previous_trading_day_skips_weekend(self):
        assert cal.previous_trading_day("2026-06-08").isoformat() == "2026-06-05"

    def test_next_trading_day_skips_holiday(self):
        # Day after a Christmas holiday must not be the holiday itself.
        nxt = cal.next_trading_day("2026-12-24")
        assert nxt.isoformat() != "2026-12-25"
        assert cal.is_trading_day(nxt) is True
