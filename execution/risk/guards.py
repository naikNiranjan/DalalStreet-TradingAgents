"""Fail-closed risk gates (spine Contract 4) — the safety core.

Every intended order runs the ordered gate chain. Default posture is **block**: if a
gate's precondition can't be *verified*, it vetoes. Gates are pure functions of a
``GateContext``. **All gates run** and the full list is returned (we audit *why*, every
gate, not just the first failure); the router vetoes if **any** result is ``blocked``.

Gate classes (the safety crux — exits bypass ONLY the entry-quality class):

  * **Universal safety/compliance** — block ENTRIES *and* EXITS: ``kill_switch``,
    ``auth_valid``, ``market_open``, ``data_fresh`` (clock-1 critical), ``instrument_tradable``,
    ``audit_writable``, and (for any SELL) ``no_BTST`` / ``settled_qty``.
  * **Entry-quality** — ENTRIES only, NEVER veto an exit: ``confidence_floor``,
    ``stale_quote`` (clock 2), ``spread_liquidity`` (``no_book`` / wide spread). On an exit
    these downgrade to ``warn``; the broker turns a no-fillable-market exit into
    ``intended_exit_unfilled`` so the trapped position stays visible.
  * **Size/exposure** — ENTRIES only (exits reduce risk and free cash): ``daily_loss_limit``,
    ``position_size_cap``, ``max_open_positions``, ``buying_power``, ``sector_cap``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from tradingagents.dataflows.india_calendar import is_market_open

from ..config import DEFAULT_EXECUTION_CONFIG, SECTOR_TAGS, ExecutionConfig
from ..contracts import ENTRY_ACTIONS, GateResult, Order, Quote, SignalDecision
from ..costs import compute_charges
from ..security_master import Instrument

__all__ = ["GateContext", "evaluate", "blocking_results", "is_allowed"]


# ---------------------------------------------------------------------------
# Context + result helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateContext:
    """Everything the gates read. The router assembles this once per intended order."""

    order: Order
    signal: SignalDecision
    instrument: Instrument
    portfolio: object                       # execution.portfolio.Portfolio (duck-typed)
    equity: float                           # snapshotted once per session
    now: datetime                           # IST; for market_open / freshness clocks
    ref_price: float                        # price used for sizing/exposure math
    quote: Optional[Quote] = None
    kill_switch: bool = False
    auth_valid: bool = True                 # paper: always True (structure ready for live)
    audit_writable: bool = True
    day_pnl: float = 0.0                    # realized + unrealized so far today (<=0 is a loss)
    sector_tags: dict = field(default_factory=lambda: dict(SECTOR_TAGS))
    holidays: Optional[list] = None
    config: ExecutionConfig = DEFAULT_EXECUTION_CONFIG


def _ok(name: str, reason: str = "ok") -> GateResult:
    return GateResult(True, name, reason, "ok")


def _warn(name: str, reason: str) -> GateResult:
    return GateResult(True, name, reason, "warn")


def _block(name: str, reason: str) -> GateResult:
    return GateResult(False, name, reason, "block")


def _entry_quality(name: str, reason: str, is_exit: bool) -> GateResult:
    """Block on an entry; on an exit downgrade to warn (never veto a de-risk)."""
    return _warn(name, f"exit-exempt: {reason}") if is_exit else _block(name, reason)


def _strip_tz(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _parse_ts(val) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return _strip_tz(val)
    try:
        return _strip_tz(datetime.fromisoformat(str(val)))
    except ValueError:
        return None


def _age_hours(now: datetime, val) -> Optional[float]:
    ts = _parse_ts(val)
    if ts is None:
        return None
    return (_strip_tz(now) - ts).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def g_kill_switch(ctx: GateContext) -> GateResult:
    return _block("kill_switch", "global kill flag set") if ctx.kill_switch else _ok("kill_switch")


def g_auth_valid(ctx: GateContext) -> GateResult:
    return _ok("auth_valid") if ctx.auth_valid else _block("auth_valid", "no/expired broker session")


def g_market_open(ctx: GateContext) -> GateResult:
    if is_market_open(ctx.now, ctx.holidays):
        return _ok("market_open")
    return _block("market_open", "outside NSE hours / holiday")


# clock-1 critical sources: stale-or-missing blocks ENTRIES and EXITS.
_CRITICAL_FRESH = ("daily OHLCV", "security master")
_DEGRADABLE_FRESH = ("news", "social", "fundamentals")


def g_data_fresh(ctx: GateContext) -> GateResult:
    fresh = ctx.signal.data_freshness or {}
    cfg = ctx.config
    limits = {
        "daily OHLCV": cfg.ohlcv_max_age_hours,
        "security master": cfg.security_master_max_age_hours,
    }
    for src in _CRITICAL_FRESH:
        if src not in fresh:
            return _block("data_fresh", f"missing critical source '{src}' (== infinitely stale)")
        age = _age_hours(ctx.now, fresh[src])
        if age is None:
            return _block("data_fresh", f"unparseable freshness for '{src}'")
        if age > limits[src]:
            return _block("data_fresh", f"'{src}' stale {age:.1f}h > {limits[src]}h")

    # Degradable sources never veto, but they must be *visible* in the report when
    # missing, unparseable, or stale (so paper metrics show degraded context, not a
    # silent gap). Fundamentals use a fetch-time threshold, not the news/social one.
    degradable_limits = {
        "news": cfg.news_social_max_age_hours,
        "social": cfg.news_social_max_age_hours,
        "fundamentals": cfg.fundamentals_max_age_hours,
    }
    degraded = []
    for src, max_h in degradable_limits.items():
        if src not in fresh:
            degraded.append(f"{src}:missing")
            continue
        age = _age_hours(ctx.now, fresh[src])
        if age is None:
            degraded.append(f"{src}:unparseable")
        elif age > max_h:
            degraded.append(f"{src}:stale({age:.0f}h>{max_h}h)")
    if degraded:
        return _warn("data_fresh", f"degraded context (non-blocking): {', '.join(degraded)}")
    return _ok("data_fresh")


def g_instrument_tradable(ctx: GateContext) -> GateResult:
    if ctx.instrument.tradable:
        return _ok("instrument_tradable")
    return _block("instrument_tradable", "ASM/GSM/suspended (tradable=False)")


def g_confidence_floor(ctx: GateContext) -> GateResult:
    # Entry-quality: only entries are floored; exits never suppressed by confidence.
    if ctx.signal.action not in ENTRY_ACTIONS:
        return _ok("confidence_floor", "not an entry")
    if ctx.signal.confidence < ctx.config.confidence_floor:
        return _block(
            "confidence_floor",
            f"confidence {ctx.signal.confidence:.2f} < floor {ctx.config.confidence_floor:.2f}",
        )
    return _ok("confidence_floor")


def g_stale_quote(ctx: GateContext) -> GateResult:
    # Clock 2, entry-quality. No usable/old quote -> entry block / exit warn.
    is_exit = ctx.order.is_exit
    if ctx.quote is None:
        return _entry_quality("stale_quote", "quote_fetch_failed", is_exit)
    age = (_strip_tz(ctx.now) - _strip_tz(ctx.quote.ts)).total_seconds()
    if age > ctx.config.stale_quote_seconds:
        return _entry_quality("stale_quote", f"quote age {age:.0f}s > {ctx.config.stale_quote_seconds}s", is_exit)
    return _ok("stale_quote")


def g_spread_liquidity(ctx: GateContext) -> GateResult:
    # Entry-quality. (None quote handled by stale_quote; avoid double-block here.)
    is_exit = ctx.order.is_exit
    q = ctx.quote
    if q is None:
        return _ok("spread_liquidity", "no quote (see stale_quote)")
    if not q.has_book:
        return _entry_quality("spread_liquidity", "no_book", is_exit)
    sf = q.spread_frac
    if sf is None:
        return _entry_quality("spread_liquidity", "no two-sided book", is_exit)
    # Fail-closed reading of the spine spread gate: any entry spread above the hard
    # threshold (0.05% for the large-cap tier) blocks the ENTRY. The spread_warn
    # threshold (0.20%) only flavors the reason ("wide" vs "severe") and, on the EXIT
    # side, the warn message — it never turns an entry block into a pass. Exits are
    # entry-quality-exempt: a wide spread downgrades to warn (accept the worse fill).
    if sf > ctx.config.spread_hard:
        band = "severe" if sf > ctx.config.spread_warn else "wide"
        reason = f"{band} spread {sf*100:.3f}% > hard {ctx.config.spread_hard*100:.3f}%"
        return _entry_quality("spread_liquidity", reason, is_exit)
    return _ok("spread_liquidity")


def g_daily_loss_limit(ctx: GateContext) -> GateResult:
    if ctx.order.is_exit:
        return _ok("daily_loss_limit", "exit-exempt")
    cap = -ctx.config.daily_loss_limit * ctx.equity
    if ctx.day_pnl <= cap:
        return _block("daily_loss_limit", f"day P&L {ctx.day_pnl:.0f} <= cap {cap:.0f}; no new buys")
    return _ok("daily_loss_limit")


def g_position_size_cap(ctx: GateContext) -> GateResult:
    if ctx.order.is_exit:
        return _ok("position_size_cap", "exit-exempt")
    current_qty = ctx.portfolio.qty(ctx.order.symbol)
    prospective = (current_qty + ctx.order.qty) * ctx.ref_price
    cap = ctx.config.position_cap_hard * ctx.equity
    if prospective > cap:
        return _block("position_size_cap", f"position {prospective:.0f} > cap {cap:.0f}")
    return _ok("position_size_cap")


def g_max_open_positions(ctx: GateContext) -> GateResult:
    if ctx.order.is_exit:
        return _ok("max_open_positions", "exit-exempt")
    is_new = ctx.portfolio.qty(ctx.order.symbol) == 0
    if is_new and ctx.portfolio.open_position_count() >= ctx.config.max_open_positions_hard:
        return _block("max_open_positions", f"at hard cap {ctx.config.max_open_positions_hard}")
    return _ok("max_open_positions")


def g_buying_power(ctx: GateContext) -> GateResult:
    if ctx.order.is_exit:
        return _ok("buying_power", "exit-exempt")
    price = ctx.quote.ask if (ctx.quote and ctx.quote.ask > 0) else ctx.ref_price
    if price <= 0:
        return _block("buying_power", "no price to estimate cost (fail-closed)")
    est = compute_charges(_buy_side(), ctx.order.qty, price, exchange=ctx.instrument.exchange,
                          cfg=ctx.config.costs)
    cost = ctx.order.qty * price + est.total
    bp = ctx.portfolio.buying_power()
    if cost > bp:
        return _block("buying_power", f"net cost {cost:.0f} > settled cash {bp:.0f}")
    return _ok("buying_power")


def g_no_btst(ctx: GateContext) -> GateResult:
    # Universal for SELL: you cannot sell unsettled stock (even to de-risk).
    if not ctx.order.is_exit:
        return _ok("no_BTST", "buy")
    if ctx.config.allow_btst:
        return _ok("no_BTST", "BTST allowed by config")
    settled = ctx.portfolio.settled_qty(ctx.order.symbol)
    if ctx.order.qty > settled:
        return _block("no_BTST", f"sell {ctx.order.qty} > settled {settled} (T+1 unsettled)")
    return _ok("no_BTST")


def g_sector_cap(ctx: GateContext) -> GateResult:
    if ctx.order.is_exit:
        return _ok("sector_cap", "exit-exempt")
    if ctx.portfolio.qty(ctx.order.symbol) > 0:
        return _ok("sector_cap", "adding to existing holding")
    sector = ctx.sector_tags.get(ctx.order.symbol)
    if sector is None:
        return _ok("sector_cap", "untagged sector")
    held_in_sector = sum(
        1 for s in ctx.portfolio.held_symbols()
        if s != ctx.order.symbol and ctx.sector_tags.get(s) == sector
    )
    if held_in_sector >= ctx.config.sector_cap:
        return _block("sector_cap", f"{held_in_sector} held in '{sector}' >= cap {ctx.config.sector_cap}")
    return _ok("sector_cap")


def g_audit_writable(ctx: GateContext) -> GateResult:
    if ctx.audit_writable:
        return _ok("audit_writable")
    return _block("audit_writable", "audit log not append-able (no order without a trail)")


def _buy_side():
    from ..contracts import Side
    return Side.BUY


# Ordered chain: universal safety/compliance, then entry-quality, then size/exposure,
# then the SELL-only settlement gate, then audit writeability.
_GATES: tuple[Callable[[GateContext], GateResult], ...] = (
    g_kill_switch,
    g_auth_valid,
    g_market_open,
    g_data_fresh,
    g_instrument_tradable,
    g_confidence_floor,
    g_stale_quote,
    g_spread_liquidity,
    g_daily_loss_limit,
    g_position_size_cap,
    g_max_open_positions,
    g_buying_power,
    g_no_btst,
    g_sector_cap,
    g_audit_writable,
)


def evaluate(ctx: GateContext) -> list[GateResult]:
    """Run the full gate chain (all gates) and return every result, in order."""
    return [gate(ctx) for gate in _GATES]


def blocking_results(results: list[GateResult]) -> list[GateResult]:
    return [r for r in results if r.blocked]


def is_allowed(results: list[GateResult]) -> bool:
    """True iff no gate produced a block."""
    return not any(r.blocked for r in results)
