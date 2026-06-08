"""Task 3 — Angel FULL quote adapter: one batched call, depth parse, no LTP fallback."""

from __future__ import annotations

from datetime import datetime

import pytest

from execution.brokers.angel_quotes import AngelQuoteAdapter, parse_full_response
from execution.security_master import SecurityMaster

FIXED_TS = datetime(2026, 6, 9, 10, 30, 0)

SM = SecurityMaster.from_records([
    {"symbol": "RELIANCE.NS", "exchange": "NSE", "angel_token": "2885", "tick_size": 0.05},
    {"symbol": "TCS.NS", "exchange": "NSE", "angel_token": "11536", "tick_size": 0.05},
    {"symbol": "INFY.NS", "exchange": "NSE", "angel_token": "9999", "tick_size": 0.05},
    {"symbol": "SBIN.BO", "exchange": "BSE", "angel_token": "500112", "tick_size": 0.05},
])

# fetched: RELIANCE (good book), TCS (empty book -> no_book). INFY/SBIN omitted = unfetched.
RESP = {
    "status": True,
    "data": {
        "fetched": [
            {"exchange": "NSE", "symbolToken": "2885", "ltp": 1300.0,
             "depth": {"buy": [{"price": 1299.5, "quantity": 500}],
                       "sell": [{"price": 1300.5, "quantity": 400}]}},
            {"exchange": "NSE", "symbolToken": "11536", "ltp": 3850.0,
             "depth": {"buy": [], "sell": []}},
        ],
        "unfetched": [],
    },
}


class FakeClient:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def getMarketData(self, mode, exchange_tokens):
        self.calls.append((mode, exchange_tokens))
        return self.resp

    def ltpData(self, *a, **k):  # must never be touched
        raise AssertionError("ltpData is LTP-only and forbidden for fills")


def _adapter(client):
    return AngelQuoteAdapter(SM, client_factory=lambda: client, clock=lambda: FIXED_TS)


# --- batching ------------------------------------------------------------------

def test_single_full_call_for_whole_universe():
    client = FakeClient(RESP)
    _adapter(client).get_quotes(["RELIANCE.NS", "TCS.NS", "INFY.NS"])
    assert len(client.calls) == 1                 # ONE batched call, not per-symbol
    mode, ex_tokens = client.calls[0]
    assert mode == "FULL"                         # never LTP
    assert sorted(ex_tokens["NSE"]) == ["11536", "2885", "9999"]


def test_tokens_grouped_by_exchange():
    client = FakeClient(RESP)
    _adapter(client).get_quotes(["RELIANCE.NS", "SBIN.BO"])
    _, ex_tokens = client.calls[0]
    assert ex_tokens["NSE"] == ["2885"]
    assert ex_tokens["BSE"] == ["500112"]


# --- parsing -------------------------------------------------------------------

def test_parse_good_book():
    quotes = _adapter(FakeClient(RESP)).get_quotes(["RELIANCE.NS", "TCS.NS", "INFY.NS"])
    rel = quotes["RELIANCE.NS"]
    assert rel.bid == 1299.5 and rel.ask == 1300.5
    assert rel.bid_qty == 500 and rel.ask_qty == 400
    assert rel.ltp == 1300.0 and rel.ts == FIXED_TS
    assert rel.has_book


def test_present_but_empty_book_is_no_book_not_dropped():
    quotes = _adapter(FakeClient(RESP)).get_quotes(["RELIANCE.NS", "TCS.NS", "INFY.NS"])
    assert "TCS.NS" in quotes              # present...
    assert not quotes["TCS.NS"].has_book   # ...but no_book


def test_unfetched_symbol_is_omitted_quote_fetch_failed():
    quotes = _adapter(FakeClient(RESP)).get_quotes(["RELIANCE.NS", "TCS.NS", "INFY.NS"])
    assert "INFY.NS" not in quotes         # missing == quote_fetch_failed


# --- failure semantics (no fallback) -------------------------------------------

def test_client_exception_returns_empty_not_ltp():
    class Boom(FakeClient):
        def getMarketData(self, mode, exchange_tokens):
            raise RuntimeError("network")

    quotes = _adapter(Boom(RESP)).get_quotes(["RELIANCE.NS"])
    assert quotes == {}                    # all quote_fetch_failed, never an LTP guess


def test_no_token_symbol_makes_no_call():
    sm = SecurityMaster.from_records([
        {"symbol": "RELIANCE.NS", "exchange": "NSE", "angel_token": "", "tick_size": 0.05},
    ])
    client = FakeClient(RESP)
    quotes = AngelQuoteAdapter(sm, client_factory=lambda: client).get_quotes(["RELIANCE.NS"])
    assert quotes == {}
    assert client.calls == []              # nothing to fetch -> no network


def test_unstatused_response_yields_nothing():
    assert parse_full_response({"status": False}, {"2885": "RELIANCE.NS"}, FIXED_TS) == {}
    assert parse_full_response({}, {"2885": "RELIANCE.NS"}, FIXED_TS) == {}


def test_get_quote_single_convenience():
    a = _adapter(FakeClient(RESP))
    assert a.get_quote("RELIANCE.NS").symbol == "RELIANCE.NS"
    assert a.get_quote("INFY.NS") is None  # quote_fetch_failed
