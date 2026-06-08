"""Task 1 — execution contracts + the rating/conviction translation boundary."""

from __future__ import annotations

import dataclasses
from datetime import datetime
from types import SimpleNamespace

import pytest

from execution.contracts import (
    CONVICTION_TO_CONFIDENCE,
    RATING_TO_ACTION,
    TIER_MULT,
    Action,
    Fill,
    FillStatus,
    GateResult,
    Order,
    OrderReport,
    OrderType,
    Quote,
    SignalDecision,
    Side,
    TimeInForce,
)
from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

AS_OF = datetime(2026, 6, 9, 10, 0)
FRESH = {"daily OHLCV": "2026-06-09T00:00:00", "security master": "2026-06-09T08:00:00"}


# --- maps ----------------------------------------------------------------------

def test_rating_to_action_is_total_over_the_5_tier_scale():
    for rating in PortfolioRating:
        assert rating.value in RATING_TO_ACTION
    assert RATING_TO_ACTION["Buy"] is Action.STRONG_BUY
    assert RATING_TO_ACTION["Overweight"] is Action.BUY
    assert RATING_TO_ACTION["Hold"] is Action.HOLD
    assert RATING_TO_ACTION["Underweight"] is Action.REDUCE
    assert RATING_TO_ACTION["Sell"] is Action.EXIT


def test_conviction_and_tier_maps():
    assert CONVICTION_TO_CONFIDENCE == {"high": 0.85, "medium": 0.60, "low": 0.35}
    assert TIER_MULT[Action.STRONG_BUY] == 1.0
    assert TIER_MULT[Action.BUY] == 0.6
    assert TIER_MULT[Action.HOLD] == 0.0


# --- dataclass shapes ----------------------------------------------------------

def test_signal_decision_required_fields_precede_defaults():
    fields = {f.name: f for f in dataclasses.fields(SignalDecision)}
    # required (no default) ...
    for name in ("symbol", "action", "confidence", "as_of", "rating_raw",
                 "rationale_digest", "data_freshness"):
        assert fields[name].default is dataclasses.MISSING
    # ... then defaulted, last
    assert fields["horizon_days"].default == 1
    assert fields["schema_version"].default == 1


def test_signal_decision_requires_data_freshness():
    with pytest.raises(TypeError):
        SignalDecision(  # missing data_freshness
            symbol="RELIANCE.NS", action=Action.HOLD, confidence=0.6,
            as_of=AS_OF, rating_raw="Hold", rationale_digest="abc",
        )


def test_order_defaults_and_unique_client_oid():
    o1 = Order(symbol="RELIANCE.NS", side=Side.BUY, qty=10)
    o2 = Order(symbol="RELIANCE.NS", side=Side.BUY, qty=10)
    assert o1.order_type is OrderType.MARKET
    assert o1.tif is TimeInForce.IOC
    assert o1.product == "CNC"
    assert o1.client_oid and o1.client_oid != o2.client_oid  # idempotency key, unique
    assert not o1.is_exit
    assert Order(symbol="X", side=Side.SELL, qty=1).is_exit  # long-only: SELL == exit


def test_quote_properties_with_book():
    q = Quote("RELIANCE.NS", ltp=1300.0, bid=1299.5, ask=1300.5, ts=AS_OF, bid_qty=500, ask_qty=400)
    assert q.mid == 1300.0
    assert q.spread == pytest.approx(1.0)
    assert q.spread_frac == pytest.approx(1.0 / 1300.0)
    assert q.has_book


@pytest.mark.parametrize(
    "bid,ask,bq,aq",
    [(0.0, 1300.5, 500, 400),   # no bid price
     (1299.5, 0.0, 500, 400),   # no ask price
     (1299.5, 1300.5, 0, 400),  # zero bid qty
     (1299.5, 1300.5, 500, 0)], # zero ask qty
)
def test_quote_has_no_book_when_touch_empty(bid, ask, bq, aq):
    q = Quote("X", ltp=1300.0, bid=bid, ask=ask, ts=AS_OF, bid_qty=bq, ask_qty=aq)
    assert not q.has_book


def test_gate_result_blocked_semantics():
    assert GateResult(False, "kill_switch", "set", "block").blocked
    assert not GateResult(False, "spread_sane", "wide on exit", "warn").blocked  # warn != block
    assert not GateResult(True, "market_open", "ok", "block").blocked


def test_order_report_is_filled():
    fill = Fill("oid", "X", Side.BUY, 10, 100.0, AS_OF, is_partial=False)
    assert OrderReport(Order("X", Side.BUY, 10), FillStatus.FILLED, 10, 10, "ok", fill).is_filled
    assert not OrderReport(Order("X", Side.BUY, 10), FillStatus.REJECTED, 10, 0, "no_book").is_filled


# --- the translation boundary (fail-closed) ------------------------------------

def _pm(rating, conviction):
    return PortfolioDecision(
        rating=rating, conviction=conviction,
        executive_summary="x", investment_thesis="thesis body",
    )


def test_from_pm_happy_path_entries():
    sig = SignalDecision.from_portfolio_decision(
        _pm(PortfolioRating.BUY, "high"), symbol="RELIANCE.NS", as_of=AS_OF, data_freshness=FRESH)
    assert sig.action is Action.STRONG_BUY and sig.confidence == 0.85
    assert sig.rating_raw == "Buy"

    sig = SignalDecision.from_portfolio_decision(
        _pm(PortfolioRating.OVERWEIGHT, "medium"), symbol="X", as_of=AS_OF, data_freshness=FRESH)
    assert sig.action is Action.BUY and sig.confidence == 0.60


def test_from_pm_exits_pass_through_regardless_of_confidence():
    # Underweight/Sell are never suppressed by confidence.
    reduce_sig = SignalDecision.from_portfolio_decision(
        _pm(PortfolioRating.UNDERWEIGHT, "low"), symbol="X", as_of=AS_OF, data_freshness=FRESH)
    assert reduce_sig.action is Action.REDUCE and reduce_sig.confidence == 0.35

    # Even a stub with an unparseable conviction must still EXIT (confidence -> 0.0).
    stub = SimpleNamespace(rating="Sell", conviction="garbage", investment_thesis="t")
    exit_sig = SignalDecision.from_portfolio_decision(
        stub, symbol="X", as_of=AS_OF, data_freshness=FRESH)
    assert exit_sig.action is Action.EXIT and exit_sig.confidence == 0.0


@pytest.mark.parametrize("bad", [None, "garbage", "", "HIGH-ish"])
def test_from_pm_entry_with_invalid_conviction_fails_closed_to_hold(bad):
    stub = SimpleNamespace(rating="Buy", conviction=bad, investment_thesis="t")
    sig = SignalDecision.from_portfolio_decision(
        stub, symbol="X", as_of=AS_OF, data_freshness=FRESH)
    assert sig.action is Action.HOLD       # entry forced to HOLD
    assert sig.confidence == 0.0
    assert sig.rating_raw == "Buy"         # original rating preserved for audit


def test_from_pm_case_insensitive_conviction():
    stub = SimpleNamespace(rating="Buy", conviction="HIGH", investment_thesis="t")
    sig = SignalDecision.from_portfolio_decision(stub, symbol="X", as_of=AS_OF, data_freshness=FRESH)
    assert sig.action is Action.STRONG_BUY and sig.confidence == 0.85


def test_from_pm_digest_and_freshness_copy():
    sig = SignalDecision.from_portfolio_decision(
        _pm(PortfolioRating.HOLD, "low"), symbol="X", as_of=AS_OF, data_freshness=FRESH)
    assert len(sig.rationale_digest) == 16          # sha256[:16]
    assert sig.data_freshness == FRESH
    assert sig.data_freshness is not FRESH          # defensive copy, not aliased
