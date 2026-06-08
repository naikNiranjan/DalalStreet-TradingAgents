"""Task 3 — PaperBroker honest fill model + side-aware reject vs intended_exit_unfilled."""

from __future__ import annotations

from datetime import datetime

import pytest

from execution.brokers.paper import PaperBroker
from execution.contracts import FillStatus, Order, OrderState, Quote, Side
from execution.security_master import SecurityMaster

TS = datetime(2026, 6, 9, 10, 30, 0)
SM = SecurityMaster.from_records([
    {"symbol": "RELIANCE.NS", "exchange": "NSE", "angel_token": "2885", "tick_size": 0.05},
])


def _book(bid=1299.5, ask=1300.5, bid_qty=500, ask_qty=400):
    return Quote("RELIANCE.NS", ltp=1300.0, bid=bid, ask=ask, ts=TS, bid_qty=bid_qty, ask_qty=ask_qty)


def _broker(quote=None):
    b = PaperBroker(SM, clock=lambda: TS)
    if quote is not None:
        b.prime_quotes({"RELIANCE.NS": quote})
    return b


def _is_tick_multiple(price, tick=0.05):
    return abs(round(price / tick) - price / tick) < 1e-9


# --- spread crossing -----------------------------------------------------------

def test_buy_fills_near_ask_not_ltp():
    b = _broker(_book())
    oid = b.place_order(Order("RELIANCE.NS", Side.BUY, 100))
    r = b.report_for(oid)
    assert r.status is FillStatus.FILLED and r.filled_qty == 100
    assert r.fill.price > 1300.5          # crosses the spread + slippage, never ltp (1300.0)
    assert _is_tick_multiple(r.fill.price)
    assert b.get_order_status(oid) is OrderState.FILLED


def test_sell_fills_near_bid_not_ltp():
    b = _broker(_book())
    r = b.report_for(b.place_order(Order("RELIANCE.NS", Side.SELL, 100)))
    assert r.status is FillStatus.FILLED
    assert r.fill.price < 1299.5          # at/below bid after adverse slippage
    assert _is_tick_multiple(r.fill.price)


# --- slippage scales with size -------------------------------------------------

def test_slippage_increases_with_order_size():
    b1 = _broker(_book()); small = b1.report_for(b1.place_order(Order("RELIANCE.NS", Side.BUY, 1)))
    b2 = _broker(_book()); large = b2.report_for(b2.place_order(Order("RELIANCE.NS", Side.BUY, 400)))
    assert large.fill.price > small.fill.price   # consuming the whole touch costs more


# --- partial IOC ---------------------------------------------------------------

def test_partial_buy_remainder_expires():
    b = _broker(_book(ask_qty=400))
    r = b.report_for(b.place_order(Order("RELIANCE.NS", Side.BUY, 1000)))
    assert r.status is FillStatus.PARTIAL
    assert r.requested_qty == 1000 and r.filled_qty == 400
    assert r.fill.is_partial


def test_partial_exit_is_partial_not_unfilled():
    # Some of an exit fills -> PARTIAL (a portion got out); only a ZERO-fill exit is
    # intended_exit_unfilled.
    b = _broker(_book(bid_qty=500))
    r = b.report_for(b.place_order(Order("RELIANCE.NS", Side.SELL, 1000)))
    assert r.status is FillStatus.PARTIAL and r.filled_qty == 500


# --- side-aware no-fill (the safety crux) --------------------------------------

def test_entry_no_book_is_rejected():
    b = _broker(_book(bid=0.0, ask=0.0, bid_qty=0, ask_qty=0))  # has_book False
    r = b.report_for(b.place_order(Order("RELIANCE.NS", Side.BUY, 100)))
    assert r.status is FillStatus.REJECTED and r.reason == "no_book"
    assert b.get_order_status(b.place_order(Order("RELIANCE.NS", Side.BUY, 100))) is OrderState.REJECTED


def test_exit_no_book_is_intended_exit_unfilled():
    b = _broker(_book(bid=0.0, ask=0.0, bid_qty=0, ask_qty=0))
    r = b.report_for(b.place_order(Order("RELIANCE.NS", Side.SELL, 100)))
    assert r.status is FillStatus.INTENDED_EXIT_UNFILLED   # NOT a normal rejection
    assert r.filled_qty == 0


def test_quote_fetch_failed_is_side_aware():
    b = _broker(quote=None)  # nothing primed -> quote missing
    entry = b.report_for(b.place_order(Order("RELIANCE.NS", Side.BUY, 100)))
    exit_ = b.report_for(b.place_order(Order("RELIANCE.NS", Side.SELL, 100)))
    assert entry.status is FillStatus.REJECTED and entry.reason == "quote_fetch_failed"
    assert exit_.status is FillStatus.INTENDED_EXIT_UNFILLED and exit_.reason == "quote_fetch_failed"


# --- plumbing ------------------------------------------------------------------

def test_place_order_returns_client_oid_and_is_idempotently_keyed():
    b = _broker(_book())
    order = Order("RELIANCE.NS", Side.BUY, 10)
    oid = b.place_order(order)
    assert oid == order.client_oid
    assert b.report_for(oid).order is order


def test_get_positions_empty_in_paper_mode():
    assert _broker(_book()).get_positions() == []
