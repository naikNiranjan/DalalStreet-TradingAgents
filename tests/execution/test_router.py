"""Task 7 — router integration: signal -> size -> gates -> quote -> fill -> portfolio -> audit."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from execution.audit import AuditLog
from execution.brokers.paper import PaperBroker
from execution.contracts import Action, Fill, Quote, SignalDecision, Side
from execution.costs import compute_charges
from execution.portfolio import Portfolio
from execution.router import Router, load_signals, persist_signals
from execution.security_master import SecurityMaster
from tradingagents.default_config import DEFAULT_CONFIG

NOW = datetime(2026, 6, 9, 11, 0)          # Tuesday 11:00 IST — inside the execution window
OFF_HOURS = datetime(2026, 6, 9, 16, 30)   # after 15:25
NSE_2026 = DEFAULT_CONFIG["nse_holidays"]

SM = SecurityMaster.from_records([
    {"symbol": "RELIANCE.NS", "exchange": "NSE", "angel_token": "2885", "tick_size": 0.05},
    {"symbol": "INFY.NS", "exchange": "NSE", "angel_token": "9999", "tick_size": 0.05},
])


def _fresh():
    return {
        "daily OHLCV": (NOW - timedelta(hours=1)).isoformat(),
        "security master": (NOW - timedelta(hours=2)).isoformat(),
    }


def _signal(symbol="RELIANCE.NS", action=Action.STRONG_BUY, confidence=0.85):
    return SignalDecision(symbol, action, confidence, NOW, "Buy", "d", _fresh())


def _good_quote(symbol="RELIANCE.NS"):
    return Quote(symbol, ltp=1300.45, bid=1300.40, ask=1300.50, ts=NOW, bid_qty=500, ask_qty=400)


def _no_book_quote(symbol="RELIANCE.NS"):
    return Quote(symbol, ltp=1300.0, bid=0.0, ask=0.0, ts=NOW, bid_qty=0, ask_qty=0)


class CountingPaperBroker(PaperBroker):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.quote_calls = 0

    def get_quotes(self, symbols):
        self.quote_calls += 1
        return super().get_quotes(symbols)


def _router(tmp_path, *, portfolio=None, broker=None, kill_switch=False, quote=None):
    broker = broker or CountingPaperBroker(SM, clock=lambda: NOW)
    if quote is not None:
        broker.prime_quotes(quote)
    portfolio = portfolio or Portfolio(1_000_000.0, holidays=[])
    audit = AuditLog(str(tmp_path / "audit.jsonl"), run_id="run-1", clock=lambda: NOW)
    router = Router(
        broker=broker, security_master=SM, portfolio=portfolio, audit=audit,
        holidays=NSE_2026, kill_switch=kill_switch, clock=lambda: NOW,
    )
    return router, broker, portfolio, audit


# --- happy path ----------------------------------------------------------------

def test_buy_signal_flows_to_fill_and_audits(tmp_path):
    router, broker, pf, audit = _router(tmp_path, quote={"RELIANCE.NS": _good_quote()})
    out = router.run_execution_pass([_signal()], now=NOW)[0]
    assert out.kind == "filled" and out.filled_qty == 65   # 85,000 / 1300.45
    assert pf.qty("RELIANCE.NS") == 65
    assert broker.quote_calls == 1                          # ONE FULL call
    assert audit.verify()
    stages = [r["stage"] for r in audit.read_all()]
    assert stages == ["signal", "gate", "fill"]


def test_single_quote_call_for_multiple_symbols(tmp_path):
    quotes = {"RELIANCE.NS": _good_quote(), "INFY.NS": _good_quote("INFY.NS")}
    router, broker, pf, audit = _router(tmp_path, quote=quotes)
    router.run_execution_pass([_signal(), _signal("INFY.NS")], now=NOW)
    assert broker.quote_calls == 1                          # batched, not per-symbol


# --- blocked: no order, but audited -------------------------------------------

def test_kill_switch_blocks_order_but_records_it(tmp_path):
    router, broker, pf, audit = _router(tmp_path, quote={"RELIANCE.NS": _good_quote()}, kill_switch=True)
    out = router.run_execution_pass([_signal()], now=NOW)[0]
    assert out.kind == "blocked" and out.gate_block == "kill_switch"
    assert pf.qty("RELIANCE.NS") == 0                       # NO order placed
    assert "block" in [r["stage"] for r in audit.read_all()]
    assert audit.verify()


def test_audit_unwritable_blocks_order_gate_10(tmp_path):
    router, broker, pf, audit = _router(tmp_path, quote={"RELIANCE.NS": _good_quote()})
    audit.is_writable = lambda: False                      # simulate a broken trail
    out = router.run_execution_pass([_signal()], now=NOW)[0]
    assert out.kind == "blocked" and out.gate_block == "audit_writable"
    assert pf.qty("RELIANCE.NS") == 0                       # no order without a writable trail


# --- exit can't be trapped: intended_exit_unfilled -----------------------------

def _settled_holding():
    pf = Portfolio(1_000_000.0, holidays=[])
    pf.apply_fill(Fill("o", "RELIANCE.NS", Side.BUY, 10, 1300.0, NOW, False),
                  compute_charges(Side.BUY, 10, 1300.0), trade_date=date(2026, 6, 5))
    pf.settle(date(2026, 6, 9))                             # T+1 passed -> settled
    assert pf.settled_qty("RELIANCE.NS") == 10
    return pf


def test_exit_with_no_book_is_intended_exit_unfilled_and_position_stays(tmp_path):
    pf = _settled_holding()
    router, broker, pf, audit = _router(tmp_path, portfolio=pf, quote={"RELIANCE.NS": _no_book_quote()})
    out = router.run_execution_pass([_signal(action=Action.EXIT, confidence=0.0)], now=NOW)[0]
    assert out.kind == "intended_exit_unfilled"
    assert pf.qty("RELIANCE.NS") == 10                      # NOT silently closed
    assert "unfilled_exit" in [r["stage"] for r in audit.read_all()]
    assert audit.verify()


# --- missing quote must BLOCK an entry (not silently skip) ---------------------

def test_entry_with_no_quote_is_blocked_not_silently_skipped(tmp_path):
    router, broker, pf, audit = _router(tmp_path)   # NO quote primed -> get_quotes == {}
    out = router.run_execution_pass([_signal()], now=NOW)[0]
    assert out.kind == "blocked"                     # NOT no_trade/no_price
    assert out.gate_block in ("stale_quote", "quote_quality")
    assert pf.qty("RELIANCE.NS") == 0
    stages = [r["stage"] for r in audit.read_all()]
    assert "gate" in stages and "block" in stages    # ran the gate chain AND audited the block
    assert "skip" not in stages                       # the bug was a silent skip
    assert audit.verify()


# --- off-hours -----------------------------------------------------------------

def test_off_hours_is_no_live_depth_outside_hours(tmp_path):
    router, broker, pf, audit = _router(tmp_path, quote={"RELIANCE.NS": _good_quote()})
    out = router.run_execution_pass([_signal()], now=OFF_HOURS)[0]
    assert out.kind == "outside_hours" and out.reason == "no_live_depth_outside_hours"
    assert broker.quote_calls == 0                          # never reaches the FULL fetch
    assert pf.qty("RELIANCE.NS") == 0


# --- unknown instrument fails closed -------------------------------------------

def test_unknown_instrument_blocks(tmp_path):
    router, broker, pf, audit = _router(tmp_path)
    out = router.run_execution_pass([_signal("WIPRO.NS")], now=NOW)[0]
    assert out.kind == "unknown_instrument"
    assert audit.verify()


# --- signal persistence round-trip (analysis -> execution hand-off) ------------

def test_persist_and_load_signals(tmp_path):
    path = str(tmp_path / "signals.jsonl")
    sigs = [_signal(), _signal("INFY.NS", Action.EXIT, 0.0)]
    persist_signals(sigs, path)
    loaded = load_signals(path)
    assert [s.symbol for s in loaded] == ["RELIANCE.NS", "INFY.NS"]
    assert loaded[0].action is Action.STRONG_BUY and loaded[1].action is Action.EXIT
    assert loaded[0].as_of == NOW
