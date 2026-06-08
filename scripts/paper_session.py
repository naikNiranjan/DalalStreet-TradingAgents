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
import json
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
from execution.security_master import SecurityMaster, default_universe_master
from execution.session import run_session
from tradingagents.dataflows.india_calendar import IST
from tradingagents.default_config import DEFAULT_CONFIG

# Daily security-master cache: resolved tokens don't change intraday, so we refresh
# from Angel ONCE per day and reuse — otherwise re-resolving 12 symbols on every run
# (analysis + drill + live) trips Angel's searchScrip rate limit. 20h < the gate's
# security_master_max_age_hours (24) so a served cache is never stale at the gate.
DEFAULT_SM_CACHE = os.path.join("runs", "security-master.json")
SM_CACHE_MAX_AGE_HOURS = 20


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


def _sm_meta_path(cache_path: str) -> str:
    return cache_path + ".meta.json"


def _save_security_master_cache(sm: SecurityMaster, cache_path: str, refreshed_at: datetime) -> None:
    """Persist the resolved master + its refresh time (sidecar) for same-day reuse."""
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    sm.save(cache_path)
    with open(_sm_meta_path(cache_path), "w", encoding="utf-8") as fh:
        json.dump({"refreshed_at": refreshed_at.isoformat()}, fh)


def _load_cached_security_master(cache_path: str, *, max_age_hours: float, now: datetime):
    """Return ``(sm, refreshed_at)`` from a fresh, fully-tokened cache, else ``None``."""
    meta = _sm_meta_path(cache_path)
    if not (os.path.exists(cache_path) and os.path.exists(meta)):
        return None
    try:
        with open(meta, encoding="utf-8") as fh:
            refreshed_at = datetime.fromisoformat(json.load(fh)["refreshed_at"])
    except Exception:  # noqa: BLE001 — corrupt meta -> treat as no cache
        return None
    if (now - refreshed_at).total_seconds() / 3600.0 > max_age_hours:
        return None  # too old -> would be stale at the freshness gate
    sm = SecurityMaster.from_file(cache_path)
    # A cache with any blank token is useless (quote fetch would fail closed) -> refuse it.
    if not all(s in sm and sm.lookup(s).has_token for s in UNIVERSE):
        return None
    return sm, refreshed_at


def _build_security_master(*, cache_path: str = DEFAULT_SM_CACHE,
                           max_age_hours: float = SM_CACHE_MAX_AGE_HOURS, now: datetime = None):
    """Build the universe security master, reusing a same-day cache when possible.

    Refresh happens in EVERY mode (analysis, exec, full): the execution pass needs the
    resolved tokens for the FULL quote read, and the analysis pass needs the refresh
    timestamp to stamp the critical ``security master`` freshness into each persisted
    signal (so a later ``--exec-only`` run doesn't block every trade on a missing
    source). To avoid re-hammering Angel's rate-limited ``searchScrip`` on every run,
    a fresh on-disk cache (default ``runs/security-master.json``) is reused if present.

    Returns ``(security_master, refreshed_at)``.
    """
    now = now or datetime.now(IST)
    cached = _load_cached_security_master(cache_path, max_age_hours=max_age_hours, now=now)
    if cached is not None:
        return cached

    sm = default_universe_master()
    sm.refresh_from_angel(UNIVERSE)            # LIVE — throttled + retried (rate-limit safe)
    refreshed_at = datetime.now(IST)
    _save_security_master_cache(sm, cache_path, refreshed_at)
    return sm, refreshed_at


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
    # Reuses a same-day on-disk cache to stay within Angel's searchScrip rate limit.
    security_master, sm_refreshed_at = _build_security_master(now=now_ist)
    print(f"  security master: {len(security_master)} instruments "
          f"(refreshed {sm_refreshed_at.isoformat()})")

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
