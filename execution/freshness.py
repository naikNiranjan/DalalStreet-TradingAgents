"""Freshness capture (spine clock-1) — build ``data_freshness`` from REAL reads.

The execution layer's ``data_fresh`` gate keys on:

  * critical (block entries AND exits): ``daily OHLCV``, ``security master``
  * degradable (warn only): ``news``, ``social``, ``fundamentals``

The graph returns *reports*, not source timestamps, so the session runner stamps
freshness from things it can actually verify (integration slice doc 14 §2):

  * ``daily OHLCV`` — the **last bar** an explicit per-symbol OHLCV read returned,
    via the existing vendor dispatch (Angel -> yfinance). This is *the* freshness
    source; the runner never fabricates a "fresh now" stamp.
  * ``security master`` — the ``refresh_from_angel`` timestamp (known at setup).
  * ``news`` / ``social`` / ``fundamentals`` — the analysis **run time** (these are
    fetched in-graph this run).

A source not actually read is **omitted** so the gate surfaces it (a missing
critical source reads as infinitely stale -> block). Fail-closed by construction.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Callable, Optional

__all__ = ["probe_ohlcv_last_bar", "build_data_freshness"]

logger = logging.getLogger(__name__)

# A data row in the get_stock_data CSV begins with an ISO calendar date.
_DATE_ROW = re.compile(r"^(\d{4}-\d{2}-\d{2})\b")

# How far back to ask for daily bars when probing for the latest one.
_PROBE_LOOKBACK_DAYS = 12

# Type of the injectable reader: (symbol, start_date, end_date) -> CSV string.
OhlcvReader = Callable[[str, str, str], str]


def _default_reader(symbol: str, start: str, end: str) -> str:
    """Live reader: route through the vendor dispatch (Angel primary, yfinance fallback)."""
    from tradingagents.dataflows.interface import route_to_vendor

    return route_to_vendor("get_stock_data", symbol, start, end)


def probe_ohlcv_last_bar(
    symbol: str,
    *,
    as_of_date: date,
    lookback_days: int = _PROBE_LOOKBACK_DAYS,
    reader: Optional[OhlcvReader] = None,
) -> Optional[str]:
    """Return the ISO timestamp (midnight) of the **last** daily bar, or ``None``.

    Reads a short window of daily OHLCV via ``reader`` (default: the live vendor
    dispatch) and returns the most recent bar's calendar date as an ISO datetime
    at midnight — the conservative, auditable freshness stamp. Returns ``None``
    (so the caller omits the source and the gate blocks) when the read yields the
    NO_DATA sentinel, no rows, or raises. Never fabricates a timestamp.
    """
    reader = reader or _default_reader
    start = (as_of_date - timedelta(days=max(1, lookback_days))).isoformat()
    end = (as_of_date + timedelta(days=1)).isoformat()  # vendor end is exclusive
    try:
        csv = reader(symbol, start, end)
    except Exception as exc:  # noqa: BLE001 — any failure == "not read" -> omit -> gate blocks
        logger.warning("OHLCV freshness probe failed for %s: %s", symbol, exc)
        return None

    if not csv or "NO_DATA_AVAILABLE" in csv:
        return None

    last_date: Optional[str] = None
    for line in csv.splitlines():
        m = _DATE_ROW.match(line.strip())
        if m:
            last_date = m.group(1)
    if last_date is None:
        return None
    return f"{last_date}T00:00:00"


def build_data_freshness(
    symbol: str,
    *,
    as_of: datetime,
    sm_refreshed_at: Optional[datetime],
    ohlcv_reader: Optional[OhlcvReader] = None,
) -> dict:
    """Assemble the ``data_freshness`` map for one symbol from verifiable reads.

    ``as_of`` is the analysis run time (IST). ``sm_refreshed_at`` is when the
    security master was last refreshed from Angel (``None`` if never -> omitted so
    the gate blocks). ``ohlcv_reader`` is injectable for offline tests.
    """
    fresh: dict = {}

    last_bar = probe_ohlcv_last_bar(symbol, as_of_date=as_of.date(), reader=ohlcv_reader)
    if last_bar is not None:
        fresh["daily OHLCV"] = last_bar

    if sm_refreshed_at is not None:
        fresh["security master"] = sm_refreshed_at.isoformat()

    # Degradable sources are fetched in-graph this run, so the run time is their
    # honest freshness. (They only ever warn, never block.)
    run_iso = as_of.isoformat()
    fresh["news"] = run_iso
    fresh["social"] = run_iso
    fresh["fundamentals"] = run_iso

    return fresh
