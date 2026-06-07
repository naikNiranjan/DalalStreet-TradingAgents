"""NSE/BSE market calendar — trading days, holidays, and market hours (IST).

Used to gate the agent so it only acts when the market is open and to reason
about settlement/holiday context. Weekend + market-hours logic is exact. The
holiday list is **deliberately conservative**: only the fixed-date national
holidays whose 2026 Gregorian dates are certain are built in. The **movable**
holidays (Holi, Good Friday, Eid, Diwali / Muhurat trading, etc.) change every
year and MUST be added from the official NSE calendar before live trading — see
``nse_holidays`` in default_config and the warning below.

Market hours (equity): 09:15–15:30 IST. Pre-open 09:00–09:15 (not counted as
regular trading here).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional, Union

import pytz

from .config import get_config

IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

# ⚠️ INCOMPLETE BY DESIGN — fixed-date national holidays only (certain Gregorian
# dates). Movable holidays (Holi, Good Friday, Mahashivratri, Eid, Ganesh
# Chaturthi, Dussehra, Diwali/Laxmi Pujan + Muhurat, Guru Nanak Jayanti, etc.)
# are NOT here because their 2026 dates must be confirmed against the official
# NSE holiday list. Populate config["nse_holidays"] before relying on this for
# live trading. Source to verify: https://www.nseindia.com (Trading Holidays).
_DEFAULT_NSE_HOLIDAYS = {
    "2026-01-26",  # Republic Day
    "2026-10-02",  # Gandhi Jayanti
    "2026-12-25",  # Christmas
}

DateLike = Union[str, date, datetime]


def _to_date(d: DateLike) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(d, "%Y-%m-%d").date()


def _holiday_set(holidays: Optional[Iterable[str]] = None) -> set[str]:
    if holidays is not None:
        return set(holidays)
    cfg = get_config().get("nse_holidays")
    return set(cfg) if cfg else set(_DEFAULT_NSE_HOLIDAYS)


def is_weekend(d: DateLike) -> bool:
    return _to_date(d).weekday() >= 5  # Sat=5, Sun=6


def is_trading_holiday(d: DateLike, holidays: Optional[Iterable[str]] = None) -> bool:
    return _to_date(d).strftime("%Y-%m-%d") in _holiday_set(holidays)


def is_trading_day(d: DateLike, holidays: Optional[Iterable[str]] = None) -> bool:
    """True when ``d`` is a weekday and not a configured NSE holiday."""
    return not is_weekend(d) and not is_trading_holiday(d, holidays)


def is_market_open(
    now_ist: Optional[datetime] = None,
    holidays: Optional[Iterable[str]] = None,
) -> bool:
    """True when the NSE equity market is open at ``now_ist`` (defaults to now).

    Naive datetimes are treated as IST; aware datetimes are converted to IST.
    """
    if now_ist is None:
        now_ist = datetime.now(IST)
    elif now_ist.tzinfo is not None:
        now_ist = now_ist.astimezone(IST)
    if not is_trading_day(now_ist.date(), holidays):
        return False
    return MARKET_OPEN <= now_ist.time() <= MARKET_CLOSE


def next_trading_day(d: DateLike, holidays: Optional[Iterable[str]] = None) -> date:
    """First trading day strictly after ``d``."""
    cur = _to_date(d) + timedelta(days=1)
    while not is_trading_day(cur, holidays):
        cur += timedelta(days=1)
    return cur


def previous_trading_day(d: DateLike, holidays: Optional[Iterable[str]] = None) -> date:
    """Most recent trading day strictly before ``d``."""
    cur = _to_date(d) - timedelta(days=1)
    while not is_trading_day(cur, holidays):
        cur -= timedelta(days=1)
    return cur
