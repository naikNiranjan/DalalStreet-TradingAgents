"""India cost model (spine Contract 6) — itemized CNC equity-delivery charges.

Paper P&L is only honest if it is *net of real costs*. The ₹25k shadow book exists
precisely to prove the strategy survives these — at small capital, fixed per-trade
costs (DP charge, the rupee-floor of STT/GST) dominate.

All rates are table-driven in :class:`execution.config.CostConfig` (overridable). The
math here is pure, side-aware, and rounded to paise so the worked-example tests pin
every component to ₹0.01. Components are rounded to 2dp first; GST is then charged on
the rounded (brokerage + exchange-txn + SEBI + DP) base — matching how broker contract
notes itemize.

Default schedule tracks Angel One delivery (2026): brokerage = lower of ₹20 or 0.1%
(min ₹5/order); STT 0.1% both sides; stamp 0.015% buy-only; exchange txn 0.0030699%
(NSE); SEBI ₹10/crore; GST 18% on (brokerage + txn + SEBI + DP); DP ₹20/scrip on SELL.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import CostConfig, DEFAULT_EXECUTION_CONFIG
from ..contracts import Side

__all__ = ["Charges", "compute_charges"]


def _r2(x: float) -> float:
    return round(x, 2)


@dataclass(frozen=True)
class Charges:
    """Itemized charges for one fill. Every field is in ₹, rounded to paise."""

    side: Side
    exchange: str
    turnover: float
    brokerage: float
    stt: float
    stamp_duty: float
    exchange_txn: float
    sebi_fee: float
    gst: float
    dp_charge: float
    total: float

    @property
    def net_cash_impact(self) -> float:
        """Signed cash effect of the fill INCLUDING charges.

        BUY → negative (turnover + charges leave the account).
        SELL → positive (turnover arrives, charges deducted).
        """
        if self.side is Side.BUY:
            return _r2(-(self.turnover + self.total))
        return _r2(self.turnover - self.total)

    @property
    def cost_drag_frac(self) -> float:
        """Total charges as a fraction of turnover (feeds the daily report's cost-drag %)."""
        return self.total / self.turnover if self.turnover > 0 else 0.0


def compute_charges(
    side: Side,
    qty: int,
    price: float,
    *,
    exchange: str = "NSE",
    cfg: CostConfig = DEFAULT_EXECUTION_CONFIG.costs,
) -> Charges:
    """Compute itemized India delivery charges for a single ``side`` fill of ``qty`` @ ``price``."""
    if qty <= 0 or price <= 0:
        raise ValueError(f"qty and price must be positive (got qty={qty}, price={price})")

    turnover = _r2(qty * price)
    is_buy = side is Side.BUY

    # Brokerage: lower of (rate × turnover) and the cap, floored at the per-order min
    # when any brokerage applies (Angel: lower of ₹20 or 0.1%, minimum ₹5).
    brokerage = turnover * cfg.brokerage_rate
    if cfg.brokerage_max > 0:
        brokerage = min(brokerage, cfg.brokerage_max)
    if brokerage > 0 and cfg.brokerage_min > 0:
        brokerage = max(brokerage, cfg.brokerage_min)
    brokerage = _r2(brokerage)

    stt = _r2(turnover * (cfg.stt_buy if is_buy else cfg.stt_sell))
    stamp_duty = _r2(turnover * cfg.stamp_buy) if is_buy else 0.0

    txn_rate = cfg.exchange_txn_nse if exchange.upper() == "NSE" else cfg.exchange_txn_bse
    exchange_txn = _r2(turnover * txn_rate)
    sebi_fee = _r2(turnover * cfg.sebi_turnover)

    dp_charge = 0.0 if is_buy else _r2(cfg.dp_charge_per_sell_scrip)

    # GST is charged on (brokerage + exchange txn + SEBI fee + DP charge); never on
    # STT or stamp duty.
    gst = _r2(cfg.gst_rate * (brokerage + exchange_txn + sebi_fee + dp_charge))

    total = _r2(brokerage + stt + stamp_duty + exchange_txn + sebi_fee + gst + dp_charge)

    return Charges(
        side=side,
        exchange=exchange.upper(),
        turnover=turnover,
        brokerage=brokerage,
        stt=stt,
        stamp_duty=stamp_duty,
        exchange_txn=exchange_txn,
        sebi_fee=sebi_fee,
        gst=gst,
        dp_charge=dp_charge,
        total=total,
    )
