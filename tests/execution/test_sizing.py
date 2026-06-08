"""Task 5 — deterministic sizing: absolute target, tier × conf × cap, deadband, EXIT bypass."""

from __future__ import annotations

from datetime import datetime

import pytest

from execution.contracts import Action, Side, SignalDecision
from execution.risk.sizing import size
from execution.security_master import Instrument

AS_OF = datetime(2026, 6, 9, 11, 0)
INST = Instrument("RELIANCE.NS", "NSE", "2885", "", lot_size=1, tick_size=0.05)
EQUITY = 1_000_000.0


def _sig(action, confidence=0.85):
    return SignalDecision(
        symbol="RELIANCE.NS", action=action, confidence=confidence, as_of=AS_OF,
        rating_raw="x", rationale_digest="d", data_freshness={},
    )


def _size(action, *, confidence=0.85, current_qty=0, ref_price=1300.0, equity=EQUITY):
    return size(_sig(action, confidence), equity=equity, current_qty=current_qty,
               ref_price=ref_price, instrument=INST)


# --- entries: tier × confidence × cap ------------------------------------------

def test_strong_buy_target_notional():
    r = _size(Action.STRONG_BUY, confidence=0.85)        # 1e6 * 0.10 * 1.0 * 0.85 = 85,000
    assert r.target_notional == pytest.approx(85_000.0)
    assert r.target_qty == 65 and r.delta_qty == 65      # round(85000/1300)=65
    assert r.side is Side.BUY and r.reason == "ok" and r.trades


def test_buy_tier_multiplier_halves_exposure():
    strong = _size(Action.STRONG_BUY, confidence=0.60)
    buy = _size(Action.BUY, confidence=0.60)             # tier 0.6 vs 1.0
    assert buy.target_notional == pytest.approx(strong.target_notional * 0.6)


def test_absolute_target_does_not_double_add():
    # already holding the target -> no delta (absolute, not incremental).
    r = _size(Action.STRONG_BUY, confidence=0.85, current_qty=65)
    assert r.delta_qty == 0 and r.reason == "no_change" and not r.trades


# --- exits ---------------------------------------------------------------------

def test_exit_targets_full_close():
    r = _size(Action.EXIT, current_qty=50)
    assert r.target_qty == 0 and r.delta_qty == -50 and r.side is Side.SELL and r.trades


def test_exit_bypasses_deadband_even_for_tiny_position():
    # 1 share * 1300 = ₹1,300 < ₹5,000 deadband, but EXIT always fully closes.
    r = _size(Action.EXIT, current_qty=1)
    assert r.delta_qty == -1 and r.trades and r.reason == "ok"


def test_reduce_targets_half_and_respects_deadband():
    big = _size(Action.REDUCE, current_qty=50)
    assert big.target_qty == 25 and big.delta_qty == -25 and big.trades
    small = _size(Action.REDUCE, current_qty=2)           # target 1, delta notional 1300 < 5000
    assert not small.trades and small.reason == "deadband"


# --- HOLD / deadband / sub-economic --------------------------------------------

def test_hold_is_no_trade():
    r = _size(Action.HOLD, current_qty=10)
    assert not r.trades and r.reason == "hold"


def test_entry_below_deadband_is_skipped():
    # small equity -> target notional below the ₹5,000 / 2%-equity deadband.
    r = _size(Action.STRONG_BUY, confidence=0.85, equity=50_000.0)  # target ~4,250
    assert not r.trades and r.reason == "deadband"


def test_entry_rounding_to_zero_is_sub_economic_skipped():
    r = _size(Action.STRONG_BUY, confidence=0.35, ref_price=1_000_000.0)  # <1 share
    assert r.target_qty == 0 and not r.trades and r.reason == "sub_economic_skipped"


def test_entry_with_no_price_fails_closed():
    r = _size(Action.STRONG_BUY, ref_price=0.0)
    assert not r.trades and r.reason == "no_price"
