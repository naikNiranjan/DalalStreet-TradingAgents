"""Step 4 (integration slice doc 14) — session runner, OFFLINE.

A FAKE graph (canned PortfolioDecisions, no LLM) + a synthetic quote source (no
Angel) drive the full slice end-to-end:

  analysis phase: graph.propagate -> build_signal -> persist + coverage manifest
  execution phase: ONE shared FULL quote fetch primes BOTH books -> each book
    (own ExecutionConfig) sizes/gates/fills against the SAME snapshot -> reports.

Asserts the doc-14 step-4 contract: both books produce reports, one shared fetch
primes both, audit chains verify, gate outcomes are correct, exits are never
blocked by entry-quality gates, the ₹25k shadow book actually trades whole shares,
and skipped symbols are visible in coverage. No live calls.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

import pytest

from execution.config import BookSpec, default_books, shadow_book_config, signal_book_config
from execution.contracts import Action, Fill, Quote, SignalDecision, Side
from execution.costs import compute_charges
from execution.portfolio import Portfolio
from execution.session import (
    AnalysisResult,
    run_analysis_phase,
    run_execution_phase,
    run_session,
)
from execution.security_master import SecurityMaster
from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating
from tradingagents.default_config import DEFAULT_CONFIG

NOW = datetime(2026, 6, 9, 11, 0)          # Tuesday 11:00 IST — inside the window
OFF_HOURS = datetime(2026, 6, 9, 16, 30)
NSE_2026 = DEFAULT_CONFIG["nse_holidays"]
SM_AT = datetime(2026, 6, 9, 9, 0)

SM = SecurityMaster.from_records([
    {"symbol": "RELIANCE.NS", "exchange": "NSE", "angel_token": "2885", "tick_size": 0.05},
    {"symbol": "INFY.NS", "exchange": "NSE", "angel_token": "9999", "tick_size": 0.05},
    {"symbol": "TCS.NS", "exchange": "NSE", "angel_token": "1111", "tick_size": 0.05},
    {"symbol": "HDFCBANK.NS", "exchange": "NSE", "angel_token": "2222", "tick_size": 0.05},
])


# --- fakes ---------------------------------------------------------------------


def _pd(rating, conviction):
    return PortfolioDecision(rating=rating, conviction=conviction,
                            executive_summary="x", investment_thesis="thesis")


class FakeGraph:
    """propagate(symbol, date) -> (final_state, processed_signal). 'ERROR' raises."""

    def __init__(self, decisions: dict):
        self.decisions = decisions
        self.calls = []

    def propagate(self, company_name, trade_date, asset_type="stock"):
        self.calls.append((company_name, trade_date))
        spec = self.decisions[company_name]
        if spec == "ERROR":
            raise RuntimeError("graph blew up for " + company_name)
        markdown = "**Rating**: Hold\n\nprose"
        return {"final_trade_decision": markdown, "portfolio_decision": spec}, "Hold"


class FakeQuoteSource:
    """get_quotes(symbols) -> {symbol: Quote}; counts calls to prove ONE shared fetch."""

    def __init__(self, quotes: dict):
        self.quotes = quotes
        self.calls = 0

    def get_quotes(self, symbols):
        self.calls += 1
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


def _csv(last="2026-06-09"):
    return (
        "# header\n# Total records: 1\n# Data retrieved on: 2026-06-09\n\n"
        "Date,Open,High,Low,Close,Volume\n"
        f"{last},1300.0,1310.0,1295.0,1305.0,1000000\n"
    )


def _reader(symbol, start, end):
    return _csv()


def _good(symbol, mid):
    return Quote(symbol, ltp=mid, bid=mid - 0.05, ask=mid + 0.05, ts=NOW, bid_qty=10_000, ask_qty=10_000)


def _no_book(symbol, ltp=1300.0):
    return Quote(symbol, ltp=ltp, bid=0.0, ask=0.0, ts=NOW, bid_qty=0, ask_qty=0)


# ---------------------------------------------------------------------------
# Analysis phase
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnalysisPhase:
    def test_builds_signals_and_coverage_with_skips_and_unstructured(self, tmp_path):
        graph = FakeGraph({
            "RELIANCE.NS": _pd(PortfolioRating.BUY, "high"),     # typed STRONG_BUY
            "INFY.NS": "ERROR",                                   # skipped
            "TCS.NS": None,                                       # free-text fallback -> HOLD
            "HDFCBANK.NS": _pd(PortfolioRating.BUY, "high"),     # typed STRONG_BUY
        })
        res = run_analysis_phase(
            graph, ["RELIANCE.NS", "INFY.NS", "TCS.NS", "HDFCBANK.NS"],
            as_of=NOW, sm_refreshed_at=SM_AT, ohlcv_reader=_reader,
            signals_path=str(tmp_path / "signals.jsonl"),
        )
        assert isinstance(res, AnalysisResult)
        assert res.coverage.universe_planned == 4
        assert res.coverage.analyzed == 3          # RELIANCE, TCS, HDFCBANK
        assert res.coverage.skipped == 1
        assert "INFY.NS" in res.coverage.skipped_detail
        assert res.coverage.unstructured == 1      # TCS free-text fallback
        assert {s.symbol for s in res.signals} == {"RELIANCE.NS", "TCS.NS", "HDFCBANK.NS"}
        # TCS fell back to a safe HOLD
        tcs = next(s for s in res.signals if s.symbol == "TCS.NS")
        assert tcs.action is Action.HOLD
        # signals were persisted for the execution phase hand-off
        assert (tmp_path / "signals.jsonl").exists()

    def test_skipped_symbol_never_fabricates_a_signal(self, tmp_path):
        graph = FakeGraph({"INFY.NS": "ERROR"})
        res = run_analysis_phase(graph, ["INFY.NS"], as_of=NOW, sm_refreshed_at=SM_AT,
                                 ohlcv_reader=_reader)
        assert res.signals == []
        assert res.coverage.skipped == 1


# ---------------------------------------------------------------------------
# Execution phase — dual books, one shared fetch
# ---------------------------------------------------------------------------


def _entry_signal(symbol, conf=0.85):
    fresh = {"daily OHLCV": "2026-06-09T00:00:00", "security master": SM_AT.isoformat()}
    return SignalDecision(symbol, Action.STRONG_BUY, conf, NOW, "Buy", "d", fresh)


@pytest.mark.unit
class TestExecutionPhaseDualBooks:
    def _signals(self):
        return [
            _entry_signal("RELIANCE.NS"),
            _entry_signal("HDFCBANK.NS"),   # NO quote primed -> blocked
        ]

    def _quotes(self):
        return FakeQuoteSource({"RELIANCE.NS": _good("RELIANCE.NS", 1300.0)})  # HDFCBANK omitted

    def test_one_shared_fetch_primes_both_books(self, tmp_path):
        qs = self._quotes()
        results = run_execution_phase(
            self._signals(), security_master=SM, quote_source=qs, books=default_books(),
            session_date="2026-06-09", holidays=NSE_2026, clock=lambda: NOW, now=NOW,
            audit_dir=str(tmp_path),
        )
        assert qs.calls == 1                        # ONE FULL fetch, not one-per-book
        assert {r.spec.name for r in results} == {"signal", "shadow"}

    def test_both_books_produce_verifying_reports(self, tmp_path):
        results = run_execution_phase(
            self._signals(), security_master=SM, quote_source=self._quotes(),
            books=default_books(), session_date="2026-06-09", holidays=NSE_2026,
            clock=lambda: NOW, now=NOW, audit_dir=str(tmp_path),
        )
        for r in results:
            assert r.report.audit_chain_ok
            assert r.report.decisions == 2

    def test_shadow_book_actually_trades_whole_shares(self, tmp_path):
        results = run_execution_phase(
            self._signals(), security_master=SM, quote_source=self._quotes(),
            books=default_books(), session_date="2026-06-09", holidays=NSE_2026,
            clock=lambda: NOW, now=NOW, audit_dir=str(tmp_path),
        )
        by = {r.spec.name: r for r in results}
        sig_rel = next(o for o in by["signal"].outcomes if o.symbol == "RELIANCE.NS")
        sh_rel = next(o for o in by["shadow"].outcomes if o.symbol == "RELIANCE.NS")
        assert sig_rel.kind == "filled" and sig_rel.filled_qty == 65   # ₹10L book
        assert sh_rel.kind == "filled" and sh_rel.filled_qty == 6      # ₹25k book trades!
        assert by["shadow"].portfolio.qty("RELIANCE.NS") == 6

    def test_missing_quote_blocks_entry_in_both_books(self, tmp_path):
        results = run_execution_phase(
            self._signals(), security_master=SM, quote_source=self._quotes(),
            books=default_books(), session_date="2026-06-09", holidays=NSE_2026,
            clock=lambda: NOW, now=NOW, audit_dir=str(tmp_path),
        )
        for r in results:
            hdfc = next(o for o in r.outcomes if o.symbol == "HDFCBANK.NS")
            assert hdfc.kind == "blocked"
            assert hdfc.gate_block in ("stale_quote", "quote_quality")

    def test_off_hours_skips_the_shared_fetch_entirely(self, tmp_path):
        qs = self._quotes()
        results = run_execution_phase(
            self._signals(), security_master=SM, quote_source=qs, books=default_books(),
            session_date="2026-06-09", holidays=NSE_2026, clock=lambda: OFF_HOURS,
            now=OFF_HOURS, audit_dir=str(tmp_path),
        )
        assert qs.calls == 0                        # no live depth pulled outside the window
        for r in results:
            assert all(o.kind == "outside_hours" for o in r.outcomes)


# ---------------------------------------------------------------------------
# Exit invariant survives the session wiring
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExitNeverBlockedByEntryQuality:
    def test_held_exit_with_no_book_is_intended_exit_unfilled_not_blocked(self, tmp_path):
        fresh = {"daily OHLCV": "2026-06-09T00:00:00", "security master": SM_AT.isoformat()}
        exit_sig = SignalDecision("RELIANCE.NS", Action.EXIT, 0.0, NOW, "Sell", "d", fresh)

        def seed(spec: BookSpec) -> Portfolio:
            pf = Portfolio(spec.capital, holidays=[])
            pf.apply_fill(Fill("o", "RELIANCE.NS", Side.BUY, 5, 1300.0, NOW - timedelta(days=4), False),
                          compute_charges(Side.BUY, 5, 1300.0), trade_date=date(2026, 6, 5))
            pf.settle(date(2026, 6, 9))     # T+1 passed -> settled, sellable
            return pf

        qs = FakeQuoteSource({"RELIANCE.NS": _no_book("RELIANCE.NS")})  # no fillable market
        results = run_execution_phase(
            [exit_sig], security_master=SM, quote_source=qs, books=default_books(),
            session_date="2026-06-09", holidays=NSE_2026, clock=lambda: NOW, now=NOW,
            audit_dir=str(tmp_path), portfolio_factory=seed,
        )
        for r in results:
            out = r.outcomes[0]
            assert out.kind == "intended_exit_unfilled"   # NOT "blocked"
            assert r.portfolio.qty("RELIANCE.NS") == 5     # position stays visible
            assert r.report.audit_chain_ok


# ---------------------------------------------------------------------------
# run_session — analysis + execution glue + report files
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunSession:
    def test_full_session_writes_reports_with_coverage(self, tmp_path):
        graph = FakeGraph({
            "RELIANCE.NS": _pd(PortfolioRating.BUY, "high"),
            "INFY.NS": "ERROR",
        })
        qs = FakeQuoteSource({"RELIANCE.NS": _good("RELIANCE.NS", 1300.0)})
        result = run_session(
            graph, universe=["RELIANCE.NS", "INFY.NS"], security_master=SM, quote_source=qs,
            as_of=NOW, sm_refreshed_at=SM_AT, books=default_books(), holidays=NSE_2026,
            ohlcv_reader=_reader, out_dir=str(tmp_path), clock=lambda: NOW, now=NOW,
        )
        assert qs.calls == 1
        assert result.coverage.universe_planned == 2 and result.coverage.skipped == 1
        with open(result.md_path, encoding="utf-8") as fh:
            md = fh.read()
        assert "Coverage" in md and "signal" in md and "shadow" in md
        # JSONL has a coverage manifest line + 2 book lines
        import json
        with open(result.jsonl_path, encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh.read().splitlines() if l.strip()]
        assert lines[0]["record"] == "coverage"
        assert {l.get("book") for l in lines[1:]} == {"signal", "shadow"}

    def test_analysis_only_does_not_fetch_quotes(self, tmp_path):
        graph = FakeGraph({"RELIANCE.NS": _pd(PortfolioRating.BUY, "high")})
        qs = FakeQuoteSource({"RELIANCE.NS": _good("RELIANCE.NS", 1300.0)})
        result = run_session(
            graph, universe=["RELIANCE.NS"], security_master=SM, quote_source=qs,
            as_of=NOW, sm_refreshed_at=SM_AT, books=default_books(), holidays=NSE_2026,
            ohlcv_reader=_reader, out_dir=str(tmp_path), clock=lambda: NOW, now=NOW,
            analysis_only=True,
        )
        assert qs.calls == 0                  # analysis can run off-window; no quote read
        assert result.signals and result.books == []

    # --- split-run hand-off (reviewer round-3): freshness + coverage survive ----

    def _analysis_only(self, tmp_path, sig_path, *, sm_refreshed_at):
        graph = FakeGraph({
            "RELIANCE.NS": _pd(PortfolioRating.BUY, "high"),
            "INFY.NS": "ERROR",
        })
        return run_session(
            graph, universe=["RELIANCE.NS", "INFY.NS"], security_master=SM,
            quote_source=FakeQuoteSource({}), as_of=NOW, sm_refreshed_at=sm_refreshed_at,
            books=default_books(), holidays=NSE_2026, ohlcv_reader=_reader,
            out_dir=str(tmp_path), clock=lambda: NOW, now=NOW,
            analysis_only=True, signals_path=sig_path,
        )

    def test_analysis_writes_coverage_sidecar_beside_signals(self, tmp_path):
        from execution.session import coverage_path_for
        sig_path = str(tmp_path / "signals.jsonl")
        self._analysis_only(tmp_path, sig_path, sm_refreshed_at=SM_AT)
        assert os.path.exists(coverage_path_for(sig_path))

    def test_exec_only_recovers_coverage_for_the_report(self, tmp_path):
        sig_path = str(tmp_path / "signals.jsonl")
        self._analysis_only(tmp_path, sig_path, sm_refreshed_at=SM_AT)
        qs = FakeQuoteSource({"RELIANCE.NS": _good("RELIANCE.NS", 1300.0)})
        result = run_session(
            None, universe=["RELIANCE.NS"], security_master=SM, quote_source=qs,
            as_of=NOW, sm_refreshed_at=None, books=default_books(), holidays=NSE_2026,
            out_dir=str(tmp_path), clock=lambda: NOW, now=NOW,
            exec_only=True, signals_path=sig_path,
        )
        # Coverage is NOT lost across the split run.
        assert result.coverage is not None
        assert result.coverage.universe_planned == 2 and result.coverage.skipped == 1
        assert "INFY.NS" in result.coverage.skipped_detail
        assert "Coverage" in result.report.to_markdown()

    def test_split_run_freshness_round_trips_so_exec_actually_trades(self, tmp_path):
        """Finding #1: a real sm_refreshed_at at analysis -> 'security master' is
        baked into the persisted signal -> exec-only passes the critical gate."""
        sig_path = str(tmp_path / "signals.jsonl")
        self._analysis_only(tmp_path, sig_path, sm_refreshed_at=SM_AT)
        qs = FakeQuoteSource({"RELIANCE.NS": _good("RELIANCE.NS", 1300.0)})
        result = run_session(
            None, universe=["RELIANCE.NS"], security_master=SM, quote_source=qs,
            as_of=NOW, sm_refreshed_at=None, books=default_books(), holidays=NSE_2026,
            out_dir=str(tmp_path), clock=lambda: NOW, now=NOW,
            exec_only=True, signals_path=sig_path,
        )
        rel = next(o for o in result.books[0].outcomes if o.symbol == "RELIANCE.NS")
        assert rel.kind == "filled"   # NOT blocked on data_fresh

    def test_split_run_without_sm_refresh_blocks_at_exec_documents_the_bug(self, tmp_path):
        """If analysis skips the SM refresh (sm_refreshed_at=None), 'security master'
        is omitted and every trade is correctly blocked at exec — which is exactly
        why scripts/paper_session.py refreshes the SM in EVERY mode."""
        sig_path = str(tmp_path / "signals.jsonl")
        self._analysis_only(tmp_path, sig_path, sm_refreshed_at=None)
        qs = FakeQuoteSource({"RELIANCE.NS": _good("RELIANCE.NS", 1300.0)})
        result = run_session(
            None, universe=["RELIANCE.NS"], security_master=SM, quote_source=qs,
            as_of=NOW, sm_refreshed_at=None, books=default_books(), holidays=NSE_2026,
            out_dir=str(tmp_path), clock=lambda: NOW, now=NOW,
            exec_only=True, signals_path=sig_path,
        )
        rel = next(o for o in result.books[0].outcomes if o.symbol == "RELIANCE.NS")
        assert rel.kind == "blocked" and rel.gate_block == "data_fresh"

    def test_exec_only_loads_persisted_signals(self, tmp_path):
        # First, analysis-only persists signals.
        graph = FakeGraph({"RELIANCE.NS": _pd(PortfolioRating.BUY, "high")})
        qs0 = FakeQuoteSource({})
        sig_path = str(tmp_path / "signals.jsonl")
        run_session(graph, universe=["RELIANCE.NS"], security_master=SM, quote_source=qs0,
                    as_of=NOW, sm_refreshed_at=SM_AT, books=default_books(), holidays=NSE_2026,
                    ohlcv_reader=_reader, out_dir=str(tmp_path), clock=lambda: NOW, now=NOW,
                    analysis_only=True, signals_path=sig_path)
        # Then exec-only loads them and trades.
        qs = FakeQuoteSource({"RELIANCE.NS": _good("RELIANCE.NS", 1300.0)})
        result = run_session(
            None, universe=["RELIANCE.NS"], security_master=SM, quote_source=qs,
            as_of=NOW, sm_refreshed_at=SM_AT, books=default_books(), holidays=NSE_2026,
            out_dir=str(tmp_path), clock=lambda: NOW, now=NOW,
            exec_only=True, signals_path=sig_path,
        )
        assert qs.calls == 1
        by = {r.spec.name: r for r in result.books}
        assert by["signal"].outcomes[0].kind == "filled"
