"""Step 1 (integration slice doc 14) — data_freshness from a REAL, verifiable read.

The session runner must NOT invent freshness. ``daily OHLCV`` (critical clock-1)
comes from an explicit per-symbol OHLCV probe (the last bar the data layer
actually returned); ``security master`` from the refresh timestamp; degradable
sources (news/social/fundamentals) from the analysis run time. A source not
actually read is **omitted** so the gate surfaces it (critical -> block).

These tests inject a fake OHLCV reader (no network); the live default routes
through the existing vendor dispatch (Angel -> yfinance).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from execution.contracts import Action, SignalDecision
from execution.freshness import build_data_freshness, probe_ohlcv_last_bar
from execution.risk.guards import GateContext, g_data_fresh

RUN = datetime(2026, 6, 9, 11, 0)          # Tuesday 11:00 IST


def _csv(*dates: str) -> str:
    """A get_stock_data-style CSV (comment header + Date,OHLCV rows)."""
    header = (
        "# Stock data for RELIANCE.NS (Angel One / SmartAPI)\n"
        f"# Total records: {len(dates)}\n"
        "# Data retrieved on: 2026-06-09 11:00:00\n\n"
        "Date,Open,High,Low,Close,Volume\n"
    )
    rows = "".join(f"{d},1300.0,1310.0,1295.0,1305.0,1000000\n" for d in dates)
    return header + rows


def _reader(csv: str):
    return lambda symbol, start, end: csv


# ---------------------------------------------------------------------------
# probe_ohlcv_last_bar
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProbeOhlcvLastBar:
    def test_returns_last_bar_date_as_iso_midnight(self):
        ts = probe_ohlcv_last_bar(
            "RELIANCE.NS", as_of_date=RUN.date(),
            reader=_reader(_csv("2026-06-04", "2026-06-05", "2026-06-08")),
        )
        assert ts == "2026-06-08T00:00:00"  # the LAST bar, not the first

    def test_no_data_sentinel_returns_none(self):
        ts = probe_ohlcv_last_bar(
            "WIPRO.NS", as_of_date=RUN.date(),
            reader=lambda s, a, b: "NO_DATA_AVAILABLE: nothing here. Do not fabricate.",
        )
        assert ts is None

    def test_empty_or_headers_only_returns_none(self):
        ts = probe_ohlcv_last_bar(
            "X.NS", as_of_date=RUN.date(), reader=_reader(_csv()),  # header, zero rows
        )
        assert ts is None

    def test_reader_exception_returns_none_failclosed(self):
        def boom(s, a, b):
            raise RuntimeError("vendor down")

        assert probe_ohlcv_last_bar("X.NS", as_of_date=RUN.date(), reader=boom) is None


# ---------------------------------------------------------------------------
# build_data_freshness
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildDataFreshness:
    def test_critical_daily_ohlcv_from_probe(self):
        fresh = build_data_freshness(
            "RELIANCE.NS", as_of=RUN, sm_refreshed_at=datetime(2026, 6, 9, 9, 0),
            ohlcv_reader=_reader(_csv("2026-06-08")),
        )
        assert fresh["daily OHLCV"] == "2026-06-08T00:00:00"

    def test_security_master_from_refresh_time(self):
        sm_at = datetime(2026, 6, 9, 9, 0)
        fresh = build_data_freshness(
            "RELIANCE.NS", as_of=RUN, sm_refreshed_at=sm_at,
            ohlcv_reader=_reader(_csv("2026-06-08")),
        )
        assert fresh["security master"] == sm_at.isoformat()

    def test_degradable_sources_stamped_with_run_time(self):
        fresh = build_data_freshness(
            "RELIANCE.NS", as_of=RUN, sm_refreshed_at=datetime(2026, 6, 9, 9, 0),
            ohlcv_reader=_reader(_csv("2026-06-08")),
        )
        for src in ("news", "social", "fundamentals"):
            assert fresh[src] == RUN.isoformat()  # fetched in-graph this run

    def test_omits_daily_ohlcv_when_probe_fails_never_fabricates(self):
        fresh = build_data_freshness(
            "X.NS", as_of=RUN, sm_refreshed_at=datetime(2026, 6, 9, 9, 0),
            ohlcv_reader=lambda s, a, b: "NO_DATA_AVAILABLE: gone.",
        )
        assert "daily OHLCV" not in fresh  # omitted, not a fake "fresh now"
        assert "security master" in fresh  # the others still present

    def test_omits_security_master_when_no_refresh_time(self):
        fresh = build_data_freshness(
            "RELIANCE.NS", as_of=RUN, sm_refreshed_at=None,
            ohlcv_reader=_reader(_csv("2026-06-08")),
        )
        assert "security master" not in fresh


# ---------------------------------------------------------------------------
# Key-name contract: the built map must satisfy the freshness GATE
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFreshnessFeedsTheGate:
    def test_fresh_read_passes_the_critical_data_fresh_gate(self):
        """Locks the key names against drift: a fresh probe must not block entries."""
        fresh = build_data_freshness(
            "RELIANCE.NS", as_of=RUN, sm_refreshed_at=datetime(2026, 6, 9, 9, 0),
            ohlcv_reader=_reader(_csv("2026-06-09")),  # today's bar -> fresh
        )
        sig = SignalDecision("RELIANCE.NS", Action.STRONG_BUY, 0.85, RUN, "Buy", "d", fresh)
        ctx = GateContext(order=None, signal=sig, instrument=None, portfolio=None,
                          equity=1_000_000.0, now=RUN, ref_price=1300.0)
        res = g_data_fresh(ctx)
        assert not res.blocked  # critical sources present and fresh

    def test_missing_ohlcv_blocks_the_critical_gate(self):
        fresh = build_data_freshness(
            "X.NS", as_of=RUN, sm_refreshed_at=datetime(2026, 6, 9, 9, 0),
            ohlcv_reader=lambda s, a, b: "NO_DATA_AVAILABLE",
        )
        sig = SignalDecision("X.NS", Action.STRONG_BUY, 0.85, RUN, "Buy", "d", fresh)
        ctx = GateContext(order=None, signal=sig, instrument=None, portfolio=None,
                          equity=1_000_000.0, now=RUN, ref_price=1300.0)
        res = g_data_fresh(ctx)
        assert res.blocked  # missing critical OHLCV == infinitely stale -> block
