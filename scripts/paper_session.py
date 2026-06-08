#!/usr/bin/env python
"""Runnable entry point for one dual-book PAPER trading session (integration slice doc 14 §4).

LIVE wiring only — all decision/sizing/gate/fill logic lives in the tested
``execution.session`` module. This script just assembles the live objects:

  * the analysis graph (``TradingAgentsGraph``) — live LLM calls,
  * the security master refreshed from Angel (``refresh_from_angel``) — live,
  * an ``AngelQuoteAdapter`` quote source (``getMarketData FULL``) — live read.

Order placement is **PaperBroker** throughout — there is NO live-order path in
this slice. "Do not jump to live."

Examples
--------
    # Full session (analysis + execution) for today, both books:
    python scripts/paper_session.py

    # Analysis off-window, then the execution pass later inside 09:20-15:25 IST:
    python scripts/paper_session.py --analysis-only --signals-path runs/signals.jsonl
    python scripts/paper_session.py --exec-only     --signals-path runs/signals.jsonl

    # Kill-switch drill (every order must be blocked + audited):
    python scripts/paper_session.py --kill-switch
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime

# Make the repo root importable whether invoked as `python scripts/paper_session.py`
# (script dir on sys.path) or `python -m scripts.paper_session`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from execution.brokers.angel_quotes import AngelQuoteAdapter
from execution.calendar_guard import assert_calendar_ready
from execution.config import UNIVERSE, default_books
from execution.security_master import default_universe_master
from execution.session import run_session
from tradingagents.dataflows.india_calendar import IST
from tradingagents.default_config import DEFAULT_CONFIG


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run one dual-book PAPER trading session (no live orders).")
    p.add_argument("--date", help="Session/trade date YYYY-MM-DD (default: today IST).")
    p.add_argument("--kill-switch", action="store_true", help="Drill: block + audit every order.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--analysis-only", action="store_true",
                      help="Run the graph and persist signals only (can run off-window).")
    mode.add_argument("--exec-only", action="store_true",
                      help="Skip the graph; load persisted signals and run the exec pass.")
    p.add_argument("--books", choices=["both", "signal", "shadow"], default="both",
                   help="Which paper book(s) to run (default: both).")
    p.add_argument("--out-dir", default="runs", help="Where to write reports/audit (default: runs/).")
    p.add_argument("--signals-path", help="Path for the persisted signals hand-off file.")
    p.add_argument("--llm-provider",
                   help="LLM provider override (default: TRADINGAGENTS_LLM_PROVIDER env, "
                        "else 'azure-foundry' — this project's native provider).")
    return p.parse_args(argv)


def _resolve_llm_provider(cli_value, env_value):
    """Pick the LLM provider for the run.

    Priority: explicit ``--llm-provider`` > ``TRADINGAGENTS_LLM_PROVIDER`` env >
    ``azure-foundry`` (this project's native, role-routed provider). The bare
    DEFAULT_CONFIG default is ``openai`` (api.openai.com), which would silently run
    the India stack on the wrong endpoint/models — so the paper session defaults to
    Foundry instead of inheriting that.
    """
    return cli_value or env_value or "azure-foundry"


def _select_books(which: str):
    books = default_books()
    if which == "both":
        return books
    return tuple(b for b in books if b.name == which)


def _build_security_master():
    """Build the universe security master and refresh tokens from Angel (LIVE).

    Done in EVERY mode (analysis, exec, full) — NOT gated on the run mode:
      * the execution pass needs the resolved tokens for the FULL quote read, and
      * the analysis pass needs the refresh timestamp to stamp the critical
        ``security master`` freshness into each persisted signal. If analysis
        skipped the refresh, ``--exec-only`` would later load signals whose
        critical ``security master`` source is missing and the freshness gate
        would block every trade — silently breaking the advertised split flow.

    Returns ``(security_master, refreshed_at)``.
    """
    security_master = default_universe_master()
    security_master.refresh_from_angel(UNIVERSE)
    return security_master, datetime.now(IST)


def main(argv=None) -> int:
    args = _parse_args(argv)
    # Copy so we can set the provider without mutating the shared module default,
    # then route the LLMs through Azure Foundry (this project's native provider)
    # unless explicitly overridden — never the bare-default OpenAI endpoint.
    config = dict(DEFAULT_CONFIG)
    config["llm_provider"] = _resolve_llm_provider(
        args.llm_provider, os.environ.get("TRADINGAGENTS_LLM_PROVIDER")
    )

    now_ist = datetime.now(IST)
    session_date = date.fromisoformat(args.date) if args.date else now_ist.date()
    # Keep a realistic time-of-day so the execution-window check is honest, but
    # label the session with the requested date.
    as_of = datetime.combine(session_date, now_ist.timetz())

    # Refuse to run against an unconfigured calendar (NSE holidays for this year).
    assert_calendar_ready(config, session_date.year)
    holidays = config.get("nse_holidays")

    os.makedirs(args.out_dir, exist_ok=True)
    signals_path = args.signals_path or os.path.join(args.out_dir, f"signals-{session_date}.jsonl")

    # --- security master: refresh tokens from Angel (LIVE), in EVERY mode ------
    # (analysis stamps the 'security master' freshness; exec needs the tokens).
    security_master, sm_refreshed_at = _build_security_master()

    # --- analysis graph (LIVE LLM) — only when we will analyze -----------------
    graph = None
    if not args.exec_only:
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        graph = TradingAgentsGraph(config=config)

    # --- live quote source (Angel FULL) ---------------------------------------
    quote_source = AngelQuoteAdapter(security_master)

    result = run_session(
        graph,
        universe=list(UNIVERSE),
        security_master=security_master,
        quote_source=quote_source,
        as_of=as_of,
        sm_refreshed_at=sm_refreshed_at,
        out_dir=args.out_dir,
        books=_select_books(args.books),
        holidays=holidays,
        kill_switch=args.kill_switch,
        analysis_only=args.analysis_only,
        exec_only=args.exec_only,
        signals_path=signals_path,
    )

    print(f"Session {result.session_date}  ·  LLM provider: {config['llm_provider']}")
    if result.coverage is not None:
        c = result.coverage
        print(f"  coverage: planned {c.universe_planned} · analyzed {c.analyzed} · "
              f"skipped {c.skipped} · unstructured {c.unstructured}")
        for sym, reason in sorted(c.skipped_detail.items()):
            print(f"    skipped {sym}: {reason}")
    if args.analysis_only:
        print(f"  signals persisted: {signals_path}")
    else:
        for r in result.books:
            rep = r.report
            print(f"  [{r.spec.name}] filled {rep.filled} · blocked {rep.blocked} · "
                  f"equity ₹{rep.equity:,.2f} · chain {'OK' if rep.audit_chain_ok else 'BROKEN'}")
        print(f"  report (md):    {result.md_path}")
        print(f"  report (jsonl): {result.jsonl_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
