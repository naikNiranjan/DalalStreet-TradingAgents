"""Session runner (integration slice doc 14 §3) — pure orchestration, no new trading logic.

Ties the spine to the analysis graph for one dual-book paper session:

  analysis phase (anytime): per symbol ``graph.propagate`` -> ``build_signal`` (the
    sole bridge; typed PM decision or safe HOLD fallback) -> persist + a coverage
    manifest (planned / analyzed / skipped / unstructured) so failures stay visible.
  execution phase (09:20-15:25 IST): ONE shared FULL quote fetch primes BOTH books;
    each book gets its own Portfolio + AuditLog + Router with its **own**
    ExecutionConfig (₹10L signal vs ₹25k shadow) and runs against the SAME snapshot.

Everything live (the graph, the quote source, ``refresh_from_angel``) is **injected**,
so this module is fully exercised offline with a fake graph + synthetic quotes. The
runnable entry point (``scripts/paper_session.py``) wires the live objects. Order
placement is PaperBroker throughout — no live orders in this slice.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from tradingagents.dataflows.india_calendar import IST, is_trading_day

from .audit import AuditLog
from .bridge import build_signal
from .brokers.paper import PaperBroker
from .config import BookSpec, default_books
from .freshness import OhlcvReader, build_data_freshness
from .portfolio import Portfolio
from .report import BookReport, CoverageManifest, SessionReport, build_book_report
from .router import Router, load_signals, persist_signals
from .security_master import SecurityMaster

__all__ = [
    "AnalysisResult",
    "BookRunResult",
    "SessionResult",
    "run_analysis_phase",
    "run_execution_phase",
    "run_session",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Coverage sidecar (split-run hand-off: analysis writes it, exec-only reads it)
# ---------------------------------------------------------------------------


def coverage_path_for(signals_path: Optional[str]) -> Optional[str]:
    """The coverage manifest lives beside the signals file (same hand-off)."""
    return signals_path + ".coverage.json" if signals_path else None


def _save_coverage(coverage: CoverageManifest, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(coverage.to_json_line() + "\n")


def _load_coverage(path: str) -> Optional[CoverageManifest]:
    with open(path, "r", encoding="utf-8") as fh:
        line = fh.readline().strip()
    return CoverageManifest.from_manifest_dict(json.loads(line)) if line else None


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass
class AnalysisResult:
    signals: list                  # list[SignalDecision]
    coverage: CoverageManifest


@dataclass
class BookRunResult:
    spec: BookSpec
    portfolio: Portfolio
    audit: AuditLog
    outcomes: list                 # list[ExecutionOutcome]
    report: BookReport


@dataclass
class SessionResult:
    session_date: str
    coverage: Optional[CoverageManifest]
    signals: list
    books: list = field(default_factory=list)   # list[BookRunResult]
    report: Optional[SessionReport] = None
    md_path: Optional[str] = None
    jsonl_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Analysis phase
# ---------------------------------------------------------------------------


def run_analysis_phase(
    graph,
    universe,
    *,
    as_of: datetime,
    sm_refreshed_at: Optional[datetime],
    ohlcv_reader: Optional[OhlcvReader] = None,
    signals_path: Optional[str] = None,
) -> AnalysisResult:
    """Run the graph per symbol and bridge each result into a typed SignalDecision.

    A symbol whose graph run errors is **skipped and recorded** in the coverage
    manifest (with its reason) — never guessed. Freshness comes from real reads
    (explicit OHLCV probe + security-master refresh time + run time), never invented.
    """
    universe = list(universe)
    signals = []
    skipped: dict = {}
    unstructured = 0
    trade_date = as_of.date().isoformat()

    for symbol in universe:
        try:
            final_state, _ = graph.propagate(symbol, trade_date)
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not sink the session
            logger.warning("analysis skipped %s: %s", symbol, exc)
            skipped[symbol] = f"graph_error: {exc}"
            continue

        freshness = build_data_freshness(
            symbol, as_of=as_of, sm_refreshed_at=sm_refreshed_at, ohlcv_reader=ohlcv_reader,
        )
        bridged = build_signal(final_state, symbol, as_of=as_of, data_freshness=freshness)
        signals.append(bridged.signal)
        if bridged.provenance == "pm_decision_unstructured":
            unstructured += 1

    coverage = CoverageManifest(
        universe_planned=len(universe),
        analyzed=len(signals),
        skipped_detail=skipped,
        unstructured=unstructured,
    )
    if signals_path:
        persist_signals(signals, signals_path)
        # Persist the coverage manifest beside the signals so a later --exec-only
        # run (separate process) still reports planned/analyzed/skipped — a split
        # run with skipped graph symbols can't masquerade as a clean smaller run.
        _save_coverage(coverage, coverage_path_for(signals_path))
    return AnalysisResult(signals=signals, coverage=coverage)


# ---------------------------------------------------------------------------
# Execution phase
# ---------------------------------------------------------------------------


def _in_execution_window(now: datetime, holidays, config) -> bool:
    t = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
    return (
        is_trading_day(now.date(), holidays)
        and config.exec_window_start <= t <= config.exec_window_end
    )


def run_execution_phase(
    signals,
    *,
    security_master: SecurityMaster,
    quote_source,
    books,
    session_date: str,
    holidays,
    audit_dir: str,
    kill_switch: bool = False,
    clock: Callable[[], datetime],
    now: Optional[datetime] = None,
    portfolio_factory: Optional[Callable[[BookSpec], Portfolio]] = None,
) -> list:
    """Run one execution pass per book against ONE shared FULL quote snapshot.

    The single ``quote_source.get_quotes`` call is shared across both books (not
    one-per-book) and only happens inside the execution window — off-window we
    pull no live depth at all (the router still returns ``outside_hours``).
    """
    now = now or clock()
    books = tuple(books)

    # One window decision (the window is identical across books); gate the live
    # FULL fetch on it so nothing is pulled outside 09:20-15:25 on a trading day.
    in_window = bool(signals) and _in_execution_window(now, holidays, books[0].config)
    symbols = [s.symbol for s in signals]
    quotes = quote_source.get_quotes(symbols) if in_window else {}

    results = []
    for spec in books:
        if portfolio_factory is not None:
            portfolio = portfolio_factory(spec)
        else:
            portfolio = Portfolio(
                spec.capital, settlement_days=spec.config.settlement_days, holidays=holidays,
            )
        audit = AuditLog(
            os.path.join(audit_dir, f"audit-{session_date}-{spec.name}.jsonl"),
            run_id=f"{session_date}-{spec.name}", clock=clock,
        )
        broker = PaperBroker(security_master, config=spec.config, clock=clock)
        broker.prime_quotes(quotes)            # the SAME snapshot for every book
        router = Router(
            broker=broker, security_master=security_master, portfolio=portfolio, audit=audit,
            config=spec.config, holidays=holidays, kill_switch=kill_switch, clock=clock,
        )
        outcomes = router.run_execution_pass(signals, now=now)
        report = build_book_report(
            book=spec.name, session_date=session_date, starting_capital=spec.capital,
            outcomes=outcomes, portfolio=portfolio, audit=audit,
        )
        results.append(BookRunResult(
            spec=spec, portfolio=portfolio, audit=audit, outcomes=outcomes, report=report,
        ))
    return results


# ---------------------------------------------------------------------------
# Full session
# ---------------------------------------------------------------------------


def run_session(
    graph,
    *,
    universe,
    security_master: SecurityMaster,
    quote_source,
    as_of: datetime,
    sm_refreshed_at: Optional[datetime],
    out_dir: str,
    books=None,
    holidays=None,
    ohlcv_reader: Optional[OhlcvReader] = None,
    kill_switch: bool = False,
    clock: Optional[Callable[[], datetime]] = None,
    now: Optional[datetime] = None,
    analysis_only: bool = False,
    exec_only: bool = False,
    signals_path: Optional[str] = None,
    portfolio_factory: Optional[Callable[[BookSpec], Portfolio]] = None,
) -> SessionResult:
    """Orchestrate analysis + execution for one session and write the dual-book report.

    ``analysis_only`` runs the graph and persists signals (no quote read — can run
    off-window). ``exec_only`` skips the graph and loads previously-persisted signals
    (so the exec pass can run later, inside the window). Default runs both.
    """
    clock = clock or (lambda: datetime.now(IST))
    now = now or clock()
    books = tuple(books) if books is not None else default_books()
    session_date = as_of.date().isoformat()

    coverage: Optional[CoverageManifest] = None
    if exec_only:
        if not signals_path:
            raise ValueError("exec_only requires signals_path to load persisted signals")
        signals = load_signals(signals_path)
        # Recover the analysis-phase coverage manifest (written beside the signals)
        # so the split-run report still carries planned/analyzed/skipped.
        cov_path = coverage_path_for(signals_path)
        if cov_path and os.path.exists(cov_path):
            coverage = _load_coverage(cov_path)
        else:
            logger.warning("exec_only: no coverage manifest beside %s; report omits coverage", signals_path)
    else:
        analysis = run_analysis_phase(
            graph, universe, as_of=as_of, sm_refreshed_at=sm_refreshed_at,
            ohlcv_reader=ohlcv_reader, signals_path=signals_path,
        )
        signals = analysis.signals
        coverage = analysis.coverage

    if analysis_only:
        return SessionResult(
            session_date=session_date, coverage=coverage, signals=signals,
        )

    book_results = run_execution_phase(
        signals, security_master=security_master, quote_source=quote_source, books=books,
        session_date=session_date, holidays=holidays, audit_dir=out_dir,
        kill_switch=kill_switch, clock=clock, now=now, portfolio_factory=portfolio_factory,
    )

    report = SessionReport(session_date, [r.report for r in book_results], coverage=coverage)
    md_path = os.path.join(out_dir, f"session-{session_date}.md")
    jsonl_path = os.path.join(out_dir, f"session-{session_date}.jsonl")
    report.write_markdown(md_path)
    report.write_jsonl(jsonl_path)

    return SessionResult(
        session_date=session_date, coverage=coverage, signals=signals, books=book_results,
        report=report, md_path=md_path, jsonl_path=jsonl_path,
    )
