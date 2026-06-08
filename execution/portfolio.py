"""Portfolio (spine §portfolio.py) — cash, positions, T+1 settlement, MTM equity.

CNC delivery has **no leverage** and **T+1 settlement**, both of which the risk gates
depend on:

  * **Settled vs unsettled cash.** A BUY debits *settled* cash at trade time. SELL
    proceeds are *unsettled* until T+1 and do NOT count toward buying power until then
    (the conservative, BTST-free model). ``buying_power()`` == settled cash only.
  * **Settled vs unsettled quantity.** Bought shares are unsettled until T+1 and cannot
    be sold before then (``allow_btst`` default False). ``settled_qty(symbol)`` is what
    the ``no_BTST`` gate checks; only settled shares are sellable.

Settlement advances by **trading days** (via :mod:`india_calendar`), not calendar days.
Accounting is average-cost, **net of India charges** (buy charges fold into cost basis;
sell charges reduce proceeds), so realized + unrealized P&L are honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from tradingagents.dataflows.india_calendar import _to_date, next_trading_day

from .contracts import Fill, Position, Side
from .costs import Charges

__all__ = ["Portfolio"]


@dataclass
class _Lot:
    qty: int
    settle_date: date


@dataclass
class _CashLot:
    amount: float
    settle_date: date


@dataclass
class _PositionState:
    symbol: str
    cost_basis: float = 0.0          # total ₹ paid for currently-held shares (incl. buy charges)
    settled_qty: int = 0             # shares free to sell now
    realized_pnl: float = 0.0
    ltp: float = 0.0
    unsettled_lots: list[_Lot] = field(default_factory=list)

    @property
    def unsettled_qty(self) -> int:
        return sum(l.qty for l in self.unsettled_lots)

    @property
    def total_qty(self) -> int:
        return self.settled_qty + self.unsettled_qty

    @property
    def avg_price(self) -> float:
        q = self.total_qty
        return (self.cost_basis / q) if q > 0 else 0.0

    @property
    def unrealized_pnl(self) -> float:
        return self.ltp * self.total_qty - self.cost_basis if self.total_qty > 0 else 0.0


class Portfolio:
    """Single-book paper portfolio. The router runs one per book (₹10L and ₹25k)."""

    def __init__(
        self,
        cash: float,
        *,
        settlement_days: int = 1,
        holidays: Optional[Iterable[str]] = None,
    ):
        self._settled_cash = float(cash)
        self._unsettled_cash: list[_CashLot] = []
        self._settlement_days = settlement_days
        self._holidays = list(holidays) if holidays is not None else None
        self._positions: dict[str, _PositionState] = {}

    # --- settlement date math (trading days) -------------------------------

    def _settle_date(self, trade_date: date) -> date:
        d = trade_date
        for _ in range(max(0, self._settlement_days)):
            d = next_trading_day(d, self._holidays)
        return d

    def _pos(self, symbol: str) -> _PositionState:
        return self._positions.setdefault(symbol, _PositionState(symbol=symbol))

    # --- apply a fill ------------------------------------------------------

    def apply_fill(self, fill: Fill, charges: Charges, *, trade_date) -> None:
        """Update cash + position state for one (possibly partial) fill, net of charges."""
        td = _to_date(trade_date)
        pos = self._pos(fill.symbol)
        if fill.qty <= 0:
            return

        if fill.side is Side.BUY:
            # settled cash leaves now; shares are unsettled until T+1.
            self._settled_cash += charges.net_cash_impact          # negative for a buy
            pos.cost_basis += -charges.net_cash_impact             # turnover + charges
            pos.unsettled_lots.append(_Lot(qty=fill.qty, settle_date=self._settle_date(td)))
        else:
            # SELL: only settled shares are sellable (gate enforces; assert defensively).
            if fill.qty > pos.settled_qty:
                raise ValueError(
                    f"cannot sell {fill.qty} of {fill.symbol}: only {pos.settled_qty} settled "
                    "(no_BTST gate should have blocked this)"
                )
            avg = pos.avg_price
            cost_removed = avg * fill.qty
            proceeds = charges.net_cash_impact                     # positive: turnover - charges
            pos.realized_pnl += proceeds - cost_removed
            pos.cost_basis -= cost_removed
            pos.settled_qty -= fill.qty
            self._unsettled_cash.append(_CashLot(amount=proceeds, settle_date=self._settle_date(td)))

    # --- settlement promotion ----------------------------------------------

    def settle(self, as_of) -> None:
        """Promote share lots and cash whose settle_date has arrived (<= ``as_of``)."""
        ad = _to_date(as_of)
        for pos in self._positions.values():
            ready = [l for l in pos.unsettled_lots if l.settle_date <= ad]
            for lot in ready:
                pos.settled_qty += lot.qty
            pos.unsettled_lots = [l for l in pos.unsettled_lots if l.settle_date > ad]
        matured = [c for c in self._unsettled_cash if c.settle_date <= ad]
        for c in matured:
            self._settled_cash += c.amount
        self._unsettled_cash = [c for c in self._unsettled_cash if c.settle_date > ad]

    # --- marks -------------------------------------------------------------

    def mark_to_market(self, prices: dict[str, float]) -> None:
        """Set each held position's last price from ``{symbol: price}`` (e.g. quote.ltp)."""
        for sym, px in prices.items():
            if sym in self._positions:
                self._positions[sym].ltp = float(px)

    # --- cash / buying power ----------------------------------------------

    @property
    def settled_cash(self) -> float:
        return round(self._settled_cash, 2)

    def unsettled_cash(self) -> float:
        return round(sum(c.amount for c in self._unsettled_cash), 2)

    def buying_power(self) -> float:
        """Settled cash only — unsettled SELL proceeds don't fund new buys (no leverage)."""
        return self.settled_cash

    # --- position queries --------------------------------------------------

    def qty(self, symbol: str) -> int:
        return self._positions[symbol].total_qty if symbol in self._positions else 0

    def settled_qty(self, symbol: str) -> int:
        return self._positions[symbol].settled_qty if symbol in self._positions else 0

    def avg_price(self, symbol: str) -> float:
        return self._positions[symbol].avg_price if symbol in self._positions else 0.0

    def position(self, symbol: str) -> Optional[Position]:
        pos = self._positions.get(symbol)
        if pos is None or pos.total_qty <= 0:
            return None
        return Position(
            symbol=symbol, qty=pos.total_qty, avg_price=round(pos.avg_price, 4),
            ltp=pos.ltp, realized_pnl=round(pos.realized_pnl, 2),
            unrealized_pnl=round(pos.unrealized_pnl, 2),
        )

    def positions(self) -> list[Position]:
        return [p for p in (self.position(s) for s in self._positions) if p is not None]

    def held_symbols(self) -> list[str]:
        return [s for s, p in self._positions.items() if p.total_qty > 0]

    def open_position_count(self) -> int:
        return len(self.held_symbols())

    def position_value(self, symbol: str) -> float:
        pos = self._positions.get(symbol)
        return round(pos.ltp * pos.total_qty, 2) if pos else 0.0

    # --- aggregate P&L / equity -------------------------------------------

    @property
    def realized_pnl(self) -> float:
        return round(sum(p.realized_pnl for p in self._positions.values()), 2)

    @property
    def unrealized_pnl(self) -> float:
        return round(sum(p.unrealized_pnl for p in self._positions.values()), 2)

    def holdings_value(self) -> float:
        return round(sum(p.ltp * p.total_qty for p in self._positions.values()), 2)

    def equity(self) -> float:
        """Total mark-to-market value = settled + unsettled cash + holdings at last marks."""
        return round(self._settled_cash + sum(c.amount for c in self._unsettled_cash)
                     + self.holdings_value(), 2)
