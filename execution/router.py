"""Router (spine §router.py) — wire the spine end-to-end, fail-closed.

Two explicit phases (spine §Execution timing):

  1. **Analysis (anytime):** turn a PM ``PortfolioDecision`` into a typed
     ``SignalDecision`` and persist it — decoupling decision from execution timing.
  2. **Execution pass (09:20-15:25 IST, trading day only):** assert the calendar is
     ready; fetch the universe's depth in ONE FULL call; per symbol size -> evaluate
     gates -> (if allowed) place a paper order -> apply the fill to the portfolio ->
     audit **every** stage. Off-hours -> ``no_live_depth_outside_hours``; an exit with
     no fillable market -> ``intended_exit_unfilled`` (the position stays visible).

The router holds a ``Broker`` (paper now, live later): **mode == which broker is
injected.** Nothing here can place an order that did not pass the gate chain and get
audited first — gate ``audit_writable`` makes the audit trail a precondition of trading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Optional

from tradingagents.dataflows.india_calendar import IST, is_trading_day
from tradingagents.default_config import DEFAULT_CONFIG

from .audit import AuditLog, _jsonable
from .calendar_guard import assert_calendar_ready
from .config import SECTOR_TAGS, DEFAULT_EXECUTION_CONFIG, ExecutionConfig
from .contracts import ENTRY_ACTIONS, Action, FillStatus, Order, SignalDecision, Side
from .costs import compute_charges
from .risk import GateContext, evaluate, is_allowed
from .risk.guards import blocking_results
from .risk.sizing import size
from .security_master import SecurityMaster, UnknownInstrument

__all__ = ["Router", "ExecutionOutcome", "persist_signals", "load_signals"]


@dataclass(frozen=True)
class ExecutionOutcome:
    symbol: str
    action: Action
    kind: str            # filled|partial|rejected|intended_exit_unfilled|blocked|no_trade|unknown_instrument|outside_hours
    reason: str
    order_id: Optional[str] = None
    filled_qty: int = 0
    gate_block: Optional[str] = None   # the first blocking gate, when kind == "blocked"


# ---------------------------------------------------------------------------
# Signal persistence (analysis phase -> execution phase hand-off)
# ---------------------------------------------------------------------------


def persist_signals(signals: Iterable[SignalDecision], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for s in signals:
            fh.write(json.dumps(_jsonable(s), ensure_ascii=True) + "\n")


def load_signals(path: str) -> list[SignalDecision]:
    out: list[SignalDecision] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            out.append(SignalDecision(
                symbol=d["symbol"], action=Action(d["action"]), confidence=d["confidence"],
                as_of=datetime.fromisoformat(d["as_of"]), rating_raw=d["rating_raw"],
                rationale_digest=d["rationale_digest"], data_freshness=d["data_freshness"],
                horizon_days=d.get("horizon_days", 1), schema_version=d.get("schema_version", 1),
            ))
    return out


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class Router:
    def __init__(
        self,
        *,
        broker,
        security_master: SecurityMaster,
        portfolio,
        audit: AuditLog,
        config: ExecutionConfig = DEFAULT_EXECUTION_CONFIG,
        sector_tags: Optional[dict] = None,
        holidays: Optional[list] = None,
        kill_switch: bool = False,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.broker = broker
        self.sm = security_master
        self.portfolio = portfolio
        self.audit = audit
        self.config = config
        self.sector_tags = sector_tags if sector_tags is not None else dict(SECTOR_TAGS)
        self.holidays = holidays if holidays is not None else DEFAULT_CONFIG.get("nse_holidays")
        self.kill_switch = kill_switch
        self._clock = clock or (lambda: datetime.now(IST))

    # --- execution window --------------------------------------------------

    def _in_execution_window(self, now: datetime) -> bool:
        t = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
        return (
            is_trading_day(now.date(), self.holidays)
            and self.config.exec_window_start <= t <= self.config.exec_window_end
        )

    # --- the pass ----------------------------------------------------------

    def run_execution_pass(self, signals: list[SignalDecision], *, now: Optional[datetime] = None):
        now = now or self._clock()
        # Build-step-0 blocker: refuse to trade against an unconfigured calendar.
        assert_calendar_ready({"nse_holidays": self.holidays}, now.year)

        if not self._in_execution_window(now):
            outcomes = []
            for s in signals:
                self.audit.append("block", s.symbol,
                                  {"gate": "execution_window", "reason": "no_live_depth_outside_hours"})
                outcomes.append(ExecutionOutcome(
                    s.symbol, s.action, "outside_hours", "no_live_depth_outside_hours"))
            return outcomes

        symbols = [s.symbol for s in signals]
        quotes = self.broker.get_quotes(symbols)          # ONE batched FULL call

        # settle T+1 lots, mark to market, then snapshot equity once for the whole pass.
        self.portfolio.settle(now.date())
        self.portfolio.mark_to_market({sym: q.ltp for sym, q in quotes.items() if q.ltp > 0})
        equity = self.portfolio.equity()
        day_pnl = self.portfolio.realized_pnl + self.portfolio.unrealized_pnl

        outcomes = []
        for signal in signals:
            outcomes.append(self._process_one(signal, quotes.get(signal.symbol), now, equity, day_pnl))

        # Re-mark after the pass so equity/holdings reflect newly-filled positions.
        # (The pre-trade `equity` snapshot above is what sizing/gates used — unchanged.)
        self.portfolio.mark_to_market({sym: q.ltp for sym, q in quotes.items() if q.ltp > 0})
        return outcomes

    @staticmethod
    def _ref_price(quote) -> float:
        """Robust reference price: mid of a two-sided book, else LTP, else 0 (no price)."""
        if quote is None:
            return 0.0
        return quote.mid or quote.ltp or 0.0

    def _gate(self, order, signal, instrument, quote, now, equity, day_pnl):
        ctx = GateContext(
            order=order, signal=signal, instrument=instrument, portfolio=self.portfolio,
            equity=equity, now=now, ref_price=self._ref_price(quote), quote=quote,
            kill_switch=self.kill_switch, auth_valid=True, audit_writable=self.audit.is_writable(),
            day_pnl=day_pnl, sector_tags=self.sector_tags, holidays=self.holidays, config=self.config,
        )
        results = evaluate(ctx)
        self.audit.append("gate", signal.symbol, results)
        return results

    def _blocked(self, signal, results) -> ExecutionOutcome:
        first = blocking_results(results)[0]
        self.audit.append("block", signal.symbol, {"gate": first.gate, "reason": first.reason})
        return ExecutionOutcome(signal.symbol, signal.action, "blocked", first.reason, gate_block=first.gate)

    def _process_one(self, signal, quote, now, equity, day_pnl) -> ExecutionOutcome:
        self.audit.append("signal", signal.symbol, signal)

        try:
            instrument = self.sm.lookup(signal.symbol)
        except UnknownInstrument as exc:
            self.audit.append("block", signal.symbol, {"gate": "unknown_instrument", "reason": str(exc)})
            return ExecutionOutcome(signal.symbol, signal.action, "unknown_instrument", "unknown_instrument")

        ref_price = self._ref_price(quote)
        current_qty = self.portfolio.qty(signal.symbol)
        sizing = size(signal, equity=equity, current_qty=current_qty,
                     ref_price=ref_price, instrument=instrument, config=self.config)

        if not sizing.trades:
            # An ENTRY that can't be priced because the quote is missing/unusable is a
            # quote-failure BLOCK (entry-quality), not a benign skip. Run the gate chain
            # on a nominal order so an entry-quality quote gate vetoes AND audits it —
            # preserving the "quote failure blocks entries" invariant. The nominal order
            # is always vetoed and never reaches the broker.
            if sizing.reason == "no_price" and signal.action in ENTRY_ACTIONS:
                nominal = Order(symbol=signal.symbol, side=Side.BUY, qty=1)
                results = self._gate(nominal, signal, instrument, quote, now, equity, day_pnl)
                if blocking_results(results):
                    return self._blocked(signal, results)
                # Defensive: no gate blocked but there is genuinely no price to act on.
                reason = "quote_fetch_failed" if quote is None else "no_book"
                self.audit.append("block", signal.symbol, {"gate": "quote_quality", "reason": reason})
                return ExecutionOutcome(signal.symbol, signal.action, "blocked", reason, gate_block="quote_quality")
            self.audit.append("skip", signal.symbol, {"reason": sizing.reason})
            return ExecutionOutcome(signal.symbol, signal.action, "no_trade", sizing.reason)

        order = Order(symbol=signal.symbol, side=sizing.side, qty=abs(sizing.delta_qty))
        results = self._gate(order, signal, instrument, quote, now, equity, day_pnl)
        if not is_allowed(results):
            return self._blocked(signal, results)

        # passed the gate chain -> place the (paper) order
        order_id = self.broker.place_order(order)
        report = self.broker.report_for(order_id)

        if report.is_filled:
            fill = report.fill
            charges = compute_charges(fill.side, fill.qty, fill.price,
                                     exchange=instrument.exchange, cfg=self.config.costs)
            self.portfolio.apply_fill(fill, charges, trade_date=now.date())
            self.audit.append("fill", signal.symbol,
                             {"fill": _jsonable(fill), "charges": _jsonable(charges), "status": report.status.value})
            kind = "partial" if report.status is FillStatus.PARTIAL else "filled"
            return ExecutionOutcome(signal.symbol, signal.action, kind, report.reason,
                                   order_id=order_id, filled_qty=report.filled_qty)

        if report.status is FillStatus.INTENDED_EXIT_UNFILLED:
            self.audit.append("unfilled_exit", signal.symbol, {"reason": report.reason})
            return ExecutionOutcome(signal.symbol, signal.action, "intended_exit_unfilled", report.reason,
                                   order_id=order_id)

        self.audit.append("reject", signal.symbol, {"reason": report.reason})
        return ExecutionOutcome(signal.symbol, signal.action, "rejected", report.reason, order_id=order_id)
