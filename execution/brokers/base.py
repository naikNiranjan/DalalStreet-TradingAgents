"""Broker Protocol (spine Contract 3) — the seam between the spine and any venue.

The router holds a ``Broker``, never a concrete class, so **mode switch = which
implementation is injected**: ``PaperBroker`` now; ``AngelOneBroker`` / ``DhanBroker``
(Phase 6) later implement the same Protocol.

Quote-fetch convention (locked, v2): ``get_quotes`` issues ONE batched
``getMarketData("FULL")`` call for the whole universe and returns a mapping. A symbol
that could not be fetched (None/error/unfetched) is **omitted** from the mapping — the
caller treats "missing" as ``quote_fetch_failed``. A symbol that was fetched but has no
two-sided book is returned with ``Quote.has_book == False`` (``no_book``). There is
**no LTP fallback** anywhere.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..contracts import Order, OrderState, Position, Quote


@runtime_checkable
class Broker(Protocol):
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """ONE batched FULL call. Missing symbol == quote_fetch_failed (never an LTP guess)."""
        ...

    def get_quote(self, symbol: str) -> Quote | None:
        """Single-symbol convenience. None == quote_fetch_failed. Never a per-symbol loop."""
        ...

    def place_order(self, order: Order) -> str:
        """Submit an order; return the broker order_id (the order's client_oid in paper)."""
        ...

    def get_order_status(self, order_id: str) -> OrderState:
        ...

    def cancel_order(self, order_id: str) -> None:
        ...

    def get_positions(self) -> list[Position]:
        ...

    def is_market_open(self) -> bool:
        ...
