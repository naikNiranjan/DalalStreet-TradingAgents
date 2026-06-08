"""Deterministic position sizing (spine §Sizing) — absolute target, tier × confidence × cap.

The LLM proposes direction + conviction; **code decides quantity.** Sizing is an
*absolute target*: the router trades the delta from the current holding, so a repeated
signal doesn't keep adding.

  * **Entry (STRONG_BUY/BUY):** ``target_notional = equity × position_cap × tier_mult × confidence``.
  * **EXIT:** target 0 (full close). **REDUCE:** target = 50% of the current holding.
  * **HOLD:** no change.

Anti-churn deadband: act only if ``|delta_notional| ≥ 2% of equity`` **and** ``≥ ₹5,000``
(after tick/lot rounding). **EXIT bypasses the deadband** (always fully closes). A
target that rounds to zero qty is a no-trade, audited ``sub_economic_skipped``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..config import DEFAULT_EXECUTION_CONFIG, ExecutionConfig
from ..contracts import TIER_MULT, Action, SignalDecision, Side
from ..security_master import Instrument, floor_to_lot

__all__ = ["SizingResult", "size"]


@dataclass(frozen=True)
class SizingResult:
    symbol: str
    action: Action
    target_qty: int
    current_qty: int
    delta_qty: int                 # +buy / -sell
    side: Optional[Side]           # None when no trade
    reason: str                    # "ok" | "hold" | "no_change" | "deadband" | "sub_economic_skipped" | "no_price"
    target_notional: float

    @property
    def trades(self) -> bool:
        return self.side is not None and self.delta_qty != 0


def _no_trade(signal, current_qty, target_qty, reason, target_notional=0.0) -> SizingResult:
    return SizingResult(
        symbol=signal.symbol, action=signal.action, target_qty=target_qty,
        current_qty=current_qty, delta_qty=0, side=None, reason=reason,
        target_notional=target_notional,
    )


def size(
    signal: SignalDecision,
    *,
    equity: float,
    current_qty: int,
    ref_price: float,
    instrument: Instrument,
    config: ExecutionConfig = DEFAULT_EXECUTION_CONFIG,
) -> SizingResult:
    """Return the absolute-target sizing decision for ``signal`` given the current holding."""
    action = signal.action
    lot = instrument.lot_size

    if action is Action.HOLD:
        return _no_trade(signal, current_qty, current_qty, "hold")

    # --- absolute target quantity -----------------------------------------
    if action is Action.EXIT:
        target_qty = 0
        target_notional = 0.0
    elif action is Action.REDUCE:
        target_qty = floor_to_lot(int(current_qty * config.reduce_fraction), lot)
        target_notional = target_qty * ref_price
    else:  # entry (STRONG_BUY / BUY)
        if ref_price <= 0:
            return _no_trade(signal, current_qty, current_qty, "no_price")
        target_notional = equity * config.position_cap_init * TIER_MULT[action] * signal.confidence
        target_qty = floor_to_lot(round(target_notional / ref_price), lot)

    delta_qty = target_qty - current_qty
    if delta_qty == 0:
        # An entry that intended a position but rounded to zero is a sub-economic skip.
        reason = "sub_economic_skipped" if action in (Action.STRONG_BUY, Action.BUY) and target_qty == 0 else "no_change"
        return _no_trade(signal, current_qty, target_qty, reason, target_notional)

    side = Side.BUY if delta_qty > 0 else Side.SELL
    delta_notional = abs(delta_qty) * ref_price

    # --- deadband (EXIT bypasses) -----------------------------------------
    if action is not Action.EXIT:
        below_equity_frac = delta_notional < config.deadband_equity_frac * equity
        below_min_notional = delta_notional < config.deadband_min_notional
        if below_equity_frac or below_min_notional:
            return _no_trade(signal, current_qty, target_qty, "deadband", target_notional)

    return SizingResult(
        symbol=signal.symbol, action=action, target_qty=target_qty,
        current_qty=current_qty, delta_qty=delta_qty, side=side,
        reason="ok", target_notional=target_notional,
    )
