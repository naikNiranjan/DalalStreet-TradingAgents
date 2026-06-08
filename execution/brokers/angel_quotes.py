"""Angel One FULL-mode quote adapter — the only call that returns bid/ask depth.

``getMarketData("FULL", {exchange: [tokens]})`` is the sole source of two-sided depth;
``ltpData`` is LTP-only and **forbidden** for fills. The execution pass fetches the
whole universe in ONE batched FULL call (tokens grouped by exchange), never a
per-symbol loop.

Failure semantics (no LTP fallback, ever):
  * symbol not returned by the API (unfetched / error / no token) -> omitted from the
    result dict -> caller reads it as ``quote_fetch_failed``;
  * symbol returned but with an empty/zero-qty touch -> ``Quote.has_book == False``
    (``no_book``).

The live call reuses the cached SmartAPI session from ``dataflows.angel_one``. The
parser :func:`parse_full_response` is pure and is what the unit tests exercise against
synthetic responses; no live call is made in CI.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

from tradingagents.dataflows.india_calendar import IST

from ..contracts import Quote
from ..security_master import SecurityMaster

logger = logging.getLogger(__name__)

__all__ = ["AngelQuoteAdapter", "parse_full_response"]


def _touch(levels: list, idx: int = 0) -> tuple[float, int]:
    """Best (price, qty) from a depth side, or (0.0, 0) when absent/malformed."""
    try:
        lvl = levels[idx]
        return float(lvl.get("price", 0) or 0), int(lvl.get("quantity", 0) or 0)
    except (IndexError, TypeError, ValueError, AttributeError):
        return 0.0, 0


def parse_full_response(
    resp: dict,
    token_to_symbol: dict[str, str],
    fetched_at: datetime,
) -> dict[str, Quote]:
    """Convert a getMarketData(FULL) response into ``{symbol: Quote}``.

    Quotes are stamped ``fetched_at`` (when we observed them) — the stale_quote gate
    measures age from this. Symbols absent from ``data.fetched`` are simply not in the
    returned dict (quote_fetch_failed). A present symbol with an empty book yields a
    ``Quote`` whose ``has_book`` is False.
    """
    out: dict[str, Quote] = {}
    if not resp or not resp.get("status"):
        return out
    fetched = ((resp.get("data") or {}).get("fetched")) or []
    for item in fetched:
        token = str(item.get("symbolToken", "") or "")
        symbol = token_to_symbol.get(token)
        if not symbol:
            continue
        depth = item.get("depth") or {}
        bid, bid_qty = _touch(depth.get("buy") or [])
        ask, ask_qty = _touch(depth.get("sell") or [])
        ltp = float(item.get("ltp", 0) or 0)
        out[symbol] = Quote(
            symbol=symbol, ltp=ltp, bid=bid, ask=ask,
            ts=fetched_at, bid_qty=bid_qty, ask_qty=ask_qty,
        )
    return out


class AngelQuoteAdapter:
    """Builds the batched FULL request from the security master and parses the response."""

    def __init__(
        self,
        security_master: SecurityMaster,
        *,
        client_factory: Optional[Callable] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self._sm = security_master
        self._client_factory = client_factory
        self._clock = clock or (lambda: datetime.now(IST))

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory()
        from tradingagents.dataflows.angel_one import _get_client

        return _get_client()

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """ONE FULL call for ``symbols``. Returns ``{symbol: Quote}`` (missing == failed)."""
        exchange_tokens: dict[str, list[str]] = {}
        token_to_symbol: dict[str, str] = {}
        for sym in symbols:
            try:
                inst = self._sm.lookup(sym)
            except KeyError:
                continue  # unknown instrument -> can't quote -> quote_fetch_failed
            if not inst.angel_token:
                continue  # no token -> fail-closed -> quote_fetch_failed
            exchange_tokens.setdefault(inst.exchange, []).append(inst.angel_token)
            token_to_symbol[inst.angel_token] = sym

        if not token_to_symbol:
            return {}

        try:
            resp = self._client().getMarketData("FULL", exchange_tokens)
        except Exception as exc:  # noqa: BLE001 — any failure == quote_fetch_failed, no fallback
            logger.warning("Angel FULL quote fetch failed: %s", exc)
            return {}

        return parse_full_response(resp, token_to_symbol, self._clock())

    def get_quote(self, symbol: str) -> Quote | None:
        return self.get_quotes([symbol]).get(symbol)
