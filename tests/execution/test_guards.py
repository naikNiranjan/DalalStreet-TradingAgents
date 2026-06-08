"""Task 5 — the fail-closed gate matrix + the exit-asymmetry invariant.

The crux: each gate vetoes when its precondition fails; entry-quality gates NEVER veto
an exit; the only universal gate that can stop an exit is no_BTST (unsettled stock).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from execution.contracts import (
    Action,
    GateResult,
    Order,
    Quote,
    SignalDecision,
    Side,
)
from execution.risk import guards
from execution.risk.guards import GateContext, blocking_results, evaluate, is_allowed
from execution.security_master import Instrument

NOW = datetime(2026, 6, 9, 11, 0)          # Tuesday, 11:00 IST — market open
INST = Instrument("RELIANCE.NS", "NSE", "2885", "", lot_size=1, tick_size=0.05)


class FakePortfolio:
    def __init__(self, *, qty=0, settled=0, buying_power=10_000_000.0, open_count=0, held=None):
        self._qty, self._settled, self._bp = qty, settled, buying_power
        self._open, self._held = open_count, held or []

    def qty(self, _s):
        return self._qty

    def settled_qty(self, _s):
        return self._settled

    def buying_power(self):
        return self._bp

    def open_position_count(self):
        return self._open

    def held_symbols(self):
        return self._held


def _fresh():
    return {
        "daily OHLCV": (NOW - timedelta(hours=1)).isoformat(),
        "security master": (NOW - timedelta(hours=2)).isoformat(),
        "news": (NOW - timedelta(hours=3)).isoformat(),
        "social": (NOW - timedelta(hours=3)).isoformat(),
        "fundamentals": (NOW - timedelta(hours=12)).isoformat(),
    }


def _quote(bid=1300.40, ask=1300.50, bid_qty=500, ask_qty=400, ts=NOW):
    return Quote("RELIANCE.NS", ltp=1300.45, bid=bid, ask=ask, ts=ts, bid_qty=bid_qty, ask_qty=ask_qty)


def _signal(action=Action.STRONG_BUY, confidence=0.85):
    return SignalDecision("RELIANCE.NS", action, confidence, NOW, "Buy", "d", _fresh())


def _entry_ctx(**ov):
    """A clean BUY context where every gate passes; override fields per test."""
    base = dict(
        order=Order("RELIANCE.NS", Side.BUY, 10),
        signal=_signal(),
        instrument=INST,
        portfolio=FakePortfolio(),
        equity=1_000_000.0,
        now=NOW,
        ref_price=1300.45,
        quote=_quote(),
        holidays=[],          # deterministic: only weekends are non-trading
    )
    base.update(ov)
    return GateContext(**base)


def _exit_ctx(**ov):
    """A clean EXIT context (SELL, settled qty available)."""
    base = dict(
        order=Order("RELIANCE.NS", Side.SELL, 10),
        signal=_signal(Action.EXIT, confidence=0.0),
        instrument=INST,
        portfolio=FakePortfolio(qty=10, settled=10, held=["RELIANCE.NS"]),
        equity=1_000_000.0,
        now=NOW,
        ref_price=1300.45,
        quote=_quote(),
        holidays=[],
    )
    base.update(ov)
    return GateContext(**base)


def _blocked_gate(results: list[GateResult]) -> str | None:
    b = blocking_results(results)
    return b[0].gate if b else None


# --- the clean baselines pass --------------------------------------------------

def test_clean_entry_passes_all_gates():
    results = evaluate(_entry_ctx())
    assert is_allowed(results), [r for r in results if r.blocked]


def test_clean_exit_passes_all_gates():
    assert is_allowed(evaluate(_exit_ctx()))


# --- universal gates block entries AND exits -----------------------------------

@pytest.mark.parametrize("ctx_fn", [_entry_ctx, _exit_ctx])
def test_kill_switch_blocks_both_sides(ctx_fn):
    assert _blocked_gate(evaluate(ctx_fn(kill_switch=True))) == "kill_switch"


@pytest.mark.parametrize("ctx_fn", [_entry_ctx, _exit_ctx])
def test_auth_invalid_blocks_both_sides(ctx_fn):
    assert "auth_valid" in {r.gate for r in blocking_results(evaluate(ctx_fn(auth_valid=False)))}


@pytest.mark.parametrize("ctx_fn", [_entry_ctx, _exit_ctx])
def test_market_closed_blocks_both_sides(ctx_fn):
    off_hours = datetime(2026, 6, 9, 16, 30)  # after 15:30
    assert "market_open" in {r.gate for r in blocking_results(evaluate(ctx_fn(now=off_hours)))}


@pytest.mark.parametrize("ctx_fn", [_entry_ctx, _exit_ctx])
def test_instrument_not_tradable_blocks_both_sides(ctx_fn):
    suspended = Instrument("RELIANCE.NS", "NSE", "2885", "", 1, 0.05, tradable=False)
    assert "instrument_tradable" in {r.gate for r in blocking_results(evaluate(ctx_fn(instrument=suspended)))}


@pytest.mark.parametrize("ctx_fn", [_entry_ctx, _exit_ctx])
def test_audit_not_writable_blocks_both_sides(ctx_fn):
    assert "audit_writable" in {r.gate for r in blocking_results(evaluate(ctx_fn(audit_writable=False)))}


# --- data_fresh (clock-1 critical) blocks both sides; degradable only warns -----

def test_missing_critical_freshness_blocks():
    sig = SignalDecision("RELIANCE.NS", Action.STRONG_BUY, 0.85, NOW, "Buy", "d",
                         {"security master": (NOW - timedelta(hours=1)).isoformat()})  # OHLCV missing
    assert "data_fresh" in {r.gate for r in blocking_results(evaluate(_entry_ctx(signal=sig)))}


def test_stale_critical_freshness_blocks():
    stale = {"daily OHLCV": (NOW - timedelta(hours=72)).isoformat(),
             "security master": (NOW - timedelta(hours=1)).isoformat()}
    sig = SignalDecision("RELIANCE.NS", Action.STRONG_BUY, 0.85, NOW, "Buy", "d", stale)
    assert "data_fresh" in {r.gate for r in blocking_results(evaluate(_entry_ctx(signal=sig)))}


def test_critical_freshness_also_blocks_an_exit():
    stale = {"security master": (NOW - timedelta(hours=1)).isoformat()}  # OHLCV missing
    sig = SignalDecision("RELIANCE.NS", Action.EXIT, 0.0, NOW, "Sell", "d", stale)
    assert not is_allowed(evaluate(_exit_ctx(signal=sig)))  # universal-class: stops exits too


def test_clean_context_data_fresh_is_ok():
    df = next(r for r in evaluate(_entry_ctx()) if r.gate == "data_fresh")
    assert df.severity == "ok"   # all five sources present and fresh


def test_degradable_freshness_only_warns():
    fr = _fresh()
    fr["news"] = (NOW - timedelta(hours=200)).isoformat()  # very stale, but degradable
    sig = SignalDecision("RELIANCE.NS", Action.STRONG_BUY, 0.85, NOW, "Buy", "d", fr)
    results = evaluate(_entry_ctx(signal=sig))
    assert is_allowed(results)  # not blocked
    df = next(r for r in results if r.gate == "data_fresh")
    assert df.severity == "warn" and "news" in df.reason


def test_missing_degradable_source_warns_visibly():
    fr = _fresh()
    del fr["fundamentals"]            # context gap must be surfaced, not silent
    sig = SignalDecision("RELIANCE.NS", Action.STRONG_BUY, 0.85, NOW, "Buy", "d", fr)
    results = evaluate(_entry_ctx(signal=sig))
    assert is_allowed(results)
    df = next(r for r in results if r.gate == "data_fresh")
    assert df.severity == "warn" and "fundamentals:missing" in df.reason


# --- entry-quality gates: block entries, NEVER veto exits ----------------------

def test_confidence_floor_blocks_low_conviction_entry():
    assert "confidence_floor" in {r.gate for r in blocking_results(
        evaluate(_entry_ctx(signal=_signal(Action.BUY, confidence=0.35))))}


def test_confidence_floor_never_blocks_exit():
    # an EXIT with confidence 0.0 must NOT be vetoed by the floor
    results = evaluate(_exit_ctx())
    assert is_allowed(results)
    cf = next(r for r in results if r.gate == "confidence_floor")
    assert not cf.blocked


@pytest.mark.parametrize(
    "bad_quote_kw",
    [dict(quote=None),                                    # quote_fetch_failed
     dict(quote=_quote(bid=0, ask=0, bid_qty=0, ask_qty=0)),  # no_book
     dict(quote=_quote(bid=1290.0, ask=1310.0)),         # very wide spread (>0.05%)
     dict(quote=_quote(ts=NOW - timedelta(seconds=120)))],   # stale_quote (>60s)
)
def test_bad_market_microstructure_blocks_entry(bad_quote_kw):
    assert not is_allowed(evaluate(_entry_ctx(**bad_quote_kw)))


@pytest.mark.parametrize(
    "bad_quote_kw",
    [dict(quote=None),
     dict(quote=_quote(bid=0, ask=0, bid_qty=0, ask_qty=0)),
     dict(quote=_quote(bid=1290.0, ask=1310.0)),
     dict(quote=_quote(ts=NOW - timedelta(seconds=120)))],
)
def test_bad_market_microstructure_never_blocks_exit(bad_quote_kw):
    # THE INVARIANT: no entry-quality condition may trap an exit.
    assert is_allowed(evaluate(_exit_ctx(**bad_quote_kw)))


def test_spread_above_hard_blocks_entry_but_only_warns_exit():
    # Fail-closed: a 0.115% spread is above the 0.05% hard threshold -> blocks an ENTRY,
    # but on an EXIT it downgrades to a (non-blocking) warn.
    q = _quote(bid=1299.0, ask=1300.5)  # spread 1.5 / mid ~1299.75 = 0.115%
    assert not is_allowed(evaluate(_entry_ctx(quote=q)))      # entry blocked
    exit_results = evaluate(_exit_ctx(quote=q))
    assert is_allowed(exit_results)                          # exit allowed
    sl = next(r for r in exit_results if r.gate == "spread_liquidity")
    assert sl.severity == "warn"


# --- size/exposure gates: entries only -----------------------------------------

def test_daily_loss_limit_blocks_new_buys_only():
    loss = -0.03 * 1_000_000.0 - 1
    assert "daily_loss_limit" in {r.gate for r in blocking_results(evaluate(_entry_ctx(day_pnl=loss)))}
    assert is_allowed(evaluate(_exit_ctx(day_pnl=loss)))  # exits exempt


def test_position_size_cap_blocks_oversized_entry():
    big = Order("RELIANCE.NS", Side.BUY, 1000)  # 1000*1300 = 13L > 15% of 10L
    assert "position_size_cap" in {r.gate for r in blocking_results(evaluate(_entry_ctx(order=big)))}


def test_max_open_positions_blocks_new_name_at_cap():
    pf = FakePortfolio(qty=0, open_count=8)  # new name, already at hard cap 8
    assert "max_open_positions" in {r.gate for r in blocking_results(evaluate(_entry_ctx(portfolio=pf)))}


def test_max_open_positions_allows_adding_to_existing():
    pf = FakePortfolio(qty=10, open_count=8)  # already holding -> adding is fine
    results = evaluate(_entry_ctx(portfolio=pf))
    assert not any(r.gate == "max_open_positions" and r.blocked for r in results)


def test_buying_power_blocks_unaffordable_entry():
    pf = FakePortfolio(buying_power=1000.0)  # can't afford 10*1300
    assert "buying_power" in {r.gate for r in blocking_results(evaluate(_entry_ctx(portfolio=pf)))}


def test_sector_cap_binds_at_two_financials():
    pf = FakePortfolio(qty=0, held=["HDFCBANK.NS", "ICICIBANK.NS"])  # 2 financials already
    sbin = GateContext(
        order=Order("SBIN.NS", Side.BUY, 10), signal=_signal(), instrument=INST,
        portfolio=pf, equity=1_000_000.0, now=NOW, ref_price=600.0, quote=_quote(), holidays=[],
    )
    assert "sector_cap" in {r.gate for r in blocking_results(evaluate(sbin))}


# --- no_BTST is the ONE universal gate that can stop an exit --------------------

def test_no_btst_blocks_selling_unsettled_even_on_exit():
    pf = FakePortfolio(qty=10, settled=0, held=["RELIANCE.NS"])  # bought < T+1 ago
    assert "no_BTST" in {r.gate for r in blocking_results(evaluate(_exit_ctx(portfolio=pf)))}


def test_no_btst_allows_selling_settled():
    assert is_allowed(evaluate(_exit_ctx()))  # settled=10 in the clean exit ctx


# --- all gates always run (full audit list) ------------------------------------

def test_all_gates_run_and_are_collected():
    results = evaluate(_entry_ctx(kill_switch=True, auth_valid=False))
    assert len(results) == 15                       # every gate produced a result
    blocked = {r.gate for r in blocking_results(results)}
    assert {"kill_switch", "auth_valid"} <= blocked  # not just the first failure


def test_property_entry_quality_block_flips_to_allowed_on_exit():
    # For every entry-quality failure, the same condition on an exit is allowed.
    for kw in (dict(quote=None),
               dict(quote=_quote(bid=0, ask=0, bid_qty=0, ask_qty=0)),
               dict(quote=_quote(bid=1290.0, ask=1310.0))):
        assert not is_allowed(evaluate(_entry_ctx(**kw)))   # entry blocked
        assert is_allowed(evaluate(_exit_ctx(**kw)))        # exit allowed
