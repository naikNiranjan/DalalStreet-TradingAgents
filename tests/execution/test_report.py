"""Task 8 — daily report: per-book metrics, cost-drag, audit coverage, dual-book output."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from execution.audit import AuditLog
from execution.brokers.paper import PaperBroker
from execution.contracts import Action, Quote, SignalDecision
from execution.portfolio import Portfolio
from execution.report import SessionReport, build_book_report
from execution.router import Router
from execution.security_master import SecurityMaster
from tradingagents.default_config import DEFAULT_CONFIG

NOW = datetime(2026, 6, 9, 11, 0)
NSE_2026 = DEFAULT_CONFIG["nse_holidays"]
SM = SecurityMaster.from_records([
    {"symbol": "RELIANCE.NS", "exchange": "NSE", "angel_token": "2885", "tick_size": 0.05},
])


def _signal(action=Action.STRONG_BUY, confidence=0.85):
    fresh = {"daily OHLCV": (NOW - timedelta(hours=1)).isoformat(),
             "security master": (NOW - timedelta(hours=2)).isoformat()}
    return SignalDecision("RELIANCE.NS", action, confidence, NOW, "Buy", "d", fresh)


def _good_quote():
    return Quote("RELIANCE.NS", ltp=1300.45, bid=1300.40, ask=1300.50, ts=NOW, bid_qty=500, ask_qty=400)


def _run(tmp_path, *, kill_switch=False):
    broker = PaperBroker(SM, clock=lambda: NOW)
    broker.prime_quotes({"RELIANCE.NS": _good_quote()})
    pf = Portfolio(1_000_000.0, holidays=[])
    audit = AuditLog(str(tmp_path / "audit.jsonl"), run_id="r", clock=lambda: NOW)
    router = Router(broker=broker, security_master=SM, portfolio=pf, audit=audit,
                   holidays=NSE_2026, kill_switch=kill_switch, clock=lambda: NOW)
    outcomes = router.run_execution_pass([_signal()], now=NOW)
    return outcomes, pf, audit


# --- a filled session ----------------------------------------------------------

def test_report_of_a_filled_session(tmp_path):
    outcomes, pf, audit = _run(tmp_path)
    rep = build_book_report(book="signal-quality ₹10L", session_date="2026-06-09",
                           starting_capital=1_000_000.0, outcomes=outcomes, portfolio=pf, audit=audit)
    assert rep.decisions == 1 and rep.filled == 1
    assert rep.turnover > 0 and rep.total_charges > 0 and rep.cost_drag_pct > 0
    assert rep.deployed_capital > 0
    assert rep.audit_coverage_pct == 100.0 and rep.audit_chain_ok
    md = rep.to_markdown()
    assert "signal-quality" in md and "Cost drag" in md
    assert json.loads(rep.to_json_line())["filled"] == 1


# --- a blocked session ---------------------------------------------------------

def test_report_counts_blocks_with_reasons(tmp_path):
    outcomes, pf, audit = _run(tmp_path, kill_switch=True)
    rep = build_book_report(book="shadow ₹25k", session_date="2026-06-09",
                           starting_capital=25_000.0, outcomes=outcomes, portfolio=pf, audit=audit)
    assert rep.blocked == 1 and rep.filled == 0
    assert rep.block_reasons.get("kill_switch") == 1
    assert rep.audit_chain_ok and rep.audit_coverage_pct == 100.0


# --- audit tamper is surfaced --------------------------------------------------

def test_report_flags_tampered_audit(tmp_path):
    outcomes, pf, audit = _run(tmp_path)
    lines = [json.loads(l) for l in open(audit.path)]
    lines[0]["payload"] = {"tampered": True}
    with open(audit.path, "w") as fh:
        fh.write("\n".join(json.dumps(l) for l in lines) + "\n")
    rep = build_book_report(book="b", session_date="2026-06-09", starting_capital=1_000_000.0,
                           outcomes=outcomes, portfolio=pf, audit=audit)
    assert rep.audit_chain_ok is False
    assert "TAMPER" in rep.to_markdown()


# --- daily-loss breach is surfaced ---------------------------------------------

def test_report_surfaces_daily_loss_breach(tmp_path):
    outcomes, pf, audit = _run(tmp_path)
    rep = build_book_report(book="b", session_date="2026-06-09", starting_capital=1_000_000.0,
                           outcomes=outcomes, portfolio=pf, audit=audit, daily_loss_breached=True)
    assert rep.daily_loss_breached is True
    assert "YES" in rep.to_markdown()


# --- dual books ----------------------------------------------------------------

def test_dual_book_session_report_writes_md_and_jsonl(tmp_path):
    outcomes, pf, audit = _run(tmp_path)
    signal_book = build_book_report(book="signal-quality ₹10L", session_date="2026-06-09",
                                   starting_capital=1_000_000.0, outcomes=outcomes, portfolio=pf, audit=audit)
    shadow_book = build_book_report(book="shadow ₹25k", session_date="2026-06-09",
                                   starting_capital=25_000.0, outcomes=outcomes, portfolio=pf, audit=audit)
    session = SessionReport("2026-06-09", [signal_book, shadow_book])

    md_path = tmp_path / "report.md"
    jsonl_path = tmp_path / "report.jsonl"
    session.write_markdown(str(md_path))
    session.write_jsonl(str(jsonl_path))

    md = md_path.read_text()
    assert "signal-quality ₹10L" in md and "shadow ₹25k" in md
    json_lines = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]
    assert len(json_lines) == 2
    assert {j["book"] for j in json_lines} == {"signal-quality ₹10L", "shadow ₹25k"}
