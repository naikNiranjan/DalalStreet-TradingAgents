"""PaperBroker (spine Contract 5) — honest fill simulator, not fill-at-LTP.

A fill-at-LTP simulator makes paper P&L a lie. This model fills against the live
two-sided ``Quote``:

  * **Spread crossing:** BUY fills near ``ask``, SELL near ``bid`` (never ``ltp``).
  * **Slippage:** ``base_slippage_bps`` plus a size-impact term scaled by order size vs
    the available touch quantity; adverse to the taker (BUY pays up, SELL receives less).
  * **Partial fills (IOC):** fill up to the available touch qty; the remainder expires
    (IOC never rests). A *zero-fill EXIT* is reported as ``intended_exit_unfilled``.
  * **Side-aware no-fill:** with no usable market (``quote_fetch_failed`` / ``no_book``),
    an ENTRY is ``REJECTED`` and an EXIT is ``intended_exit_unfilled`` (the de-risk
    couldn't execute and must stay visible — never a silent close).
  * **Tick/lot:** fill price rounded to the instrument tick.

The PaperBroker caches the quotes handed to it via :meth:`get_quotes` (or
:meth:`prime_quotes`) and fills :meth:`place_order` against the cached quote for that
symbol — matching the live flow (fetch the universe once, then place orders). The
Portfolio (Task 4) remains the source of truth for positions in paper mode, so
:meth:`get_positions` returns an empty list by design.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from tradingagents.dataflows.india_calendar import IST, is_market_open

from ..config import DEFAULT_EXECUTION_CONFIG, ExecutionConfig
from ..contracts import (
    Fill,
    FillStatus,
    Order,
    OrderReport,
    OrderState,
    Position,
    Quote,
    Side,
)
from ..security_master import SecurityMaster, round_to_tick

__all__ = ["PaperBroker"]

_STATUS_TO_STATE = {
    FillStatus.FILLED: OrderState.FILLED,
    FillStatus.PARTIAL: OrderState.PARTIAL,
    FillStatus.REJECTED: OrderState.REJECTED,
    FillStatus.INTENDED_EXIT_UNFILLED: OrderState.CANCELLED,  # IOC exit expired w/ 0 fill
}


class PaperBroker:
    """A :class:`execution.brokers.base.Broker` that simulates fills honestly."""

    def __init__(
        self,
        security_master: SecurityMaster,
        *,
        config: ExecutionConfig = DEFAULT_EXECUTION_CONFIG,
        quote_adapter=None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self._sm = security_master
        self._cfg = config
        self._adapter = quote_adapter
        self._clock = clock or (lambda: datetime.now(IST))
        self._quotes: dict[str, Quote] = {}
        self._reports: dict[str, OrderReport] = {}

    # --- quotes ------------------------------------------------------------

    def prime_quotes(self, quotes: dict[str, Quote]) -> None:
        """Inject quotes directly (used by the router after a FULL fetch, and by tests)."""
        self._quotes.update(quotes)

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if self._adapter is not None:
            fetched = self._adapter.get_quotes(symbols)
            self._quotes.update(fetched)
            return fetched
        return {s: self._quotes[s] for s in symbols if s in self._quotes}

    def get_quote(self, symbol: str) -> Quote | None:
        if self._adapter is not None:
            q = self._adapter.get_quote(symbol)
            if q is not None:
                self._quotes[symbol] = q
            return q
        return self._quotes.get(symbol)

    def is_market_open(self) -> bool:
        # Honor the injected clock so paper runs/tests are deterministic.
        return is_market_open(self._clock())

    # --- order placement ---------------------------------------------------

    def place_order(self, order: Order) -> str:
        """Simulate ``order`` against the cached quote; store and key the report by client_oid."""
        report = self._simulate(order)
        self._reports[order.client_oid] = report
        return order.client_oid

    def report_for(self, order_id: str) -> OrderReport:
        return self._reports[order_id]

    def get_order_status(self, order_id: str) -> OrderState:
        return _STATUS_TO_STATE[self._reports[order_id].status]

    def cancel_order(self, order_id: str) -> None:
        # IOC orders are already terminal after place_order; nothing to cancel.
        return None

    def get_positions(self) -> list[Position]:
        # Paper mode: the Portfolio is the source of truth, not the broker.
        return []

    # --- simulation core ---------------------------------------------------

    def _no_fill(self, order: Order, reason: str) -> OrderReport:
        status = (
            FillStatus.INTENDED_EXIT_UNFILLED if order.is_exit else FillStatus.REJECTED
        )
        return OrderReport(
            order=order, status=status, requested_qty=order.qty,
            filled_qty=0, reason=reason, fill=None,
        )

    def _simulate(self, order: Order) -> OrderReport:
        quote = self._quotes.get(order.symbol)
        if quote is None:
            return self._no_fill(order, "quote_fetch_failed")
        if not quote.has_book:
            return self._no_fill(order, "no_book")

        # tick from the security master; fall back to the configured cash-equity tick.
        try:
            tick = self._sm.lookup(order.symbol).tick_size
        except KeyError:
            tick = self._cfg.paper.tick_default

        if order.side is Side.BUY:
            base, available = quote.ask, quote.ask_qty
        else:
            base, available = quote.bid, quote.bid_qty

        fill_qty = min(order.qty, available)
        if fill_qty <= 0:
            return self._no_fill(order, "no_book")

        # size-scaled adverse slippage: more of the touch consumed -> worse price.
        p = self._cfg.paper
        size_frac = min(1.0, order.qty / available) if available > 0 else 1.0
        slip_frac = (p.base_slippage_bps + p.size_impact_bps * size_frac) / 10_000.0
        raw_price = base * (1 + slip_frac) if order.side is Side.BUY else base * (1 - slip_frac)
        price = round_to_tick(raw_price, tick)

        is_partial = fill_qty < order.qty
        fill = Fill(
            order_id=order.client_oid, symbol=order.symbol, side=order.side,
            qty=fill_qty, price=price, ts=self._clock(), is_partial=is_partial,
        )
        status = FillStatus.PARTIAL if is_partial else FillStatus.FILLED
        reason = "partial_ioc_remainder_expired" if is_partial else "filled"
        return OrderReport(
            order=order, status=status, requested_qty=order.qty,
            filled_qty=fill_qty, reason=reason, fill=fill,
        )
