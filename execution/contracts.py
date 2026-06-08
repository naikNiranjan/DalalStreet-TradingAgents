"""Typed contracts for the execution spine (Phase 3) — pure data + maps, no behavior.

Every object passed between the analysis engine, the risk layer, the (paper) broker,
the portfolio, and the audit log is defined here. Keeping them in one module makes the
hand-offs explicit and lets the rest of the package depend on shapes, not internals.

See niranjan_docs/12-phase3-execution-spine.md (Contracts 1/3/4) for the locked shapes.
The only translation logic that lives here is :meth:`SignalDecision.from_portfolio_decision`
(rating + categorical conviction -> typed signal with a derived, fail-closed confidence),
because it is the single boundary where the LLM's output becomes a typed decision.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

__all__ = [
    "Action",
    "Side",
    "OrderType",
    "TimeInForce",
    "OrderState",
    "FillStatus",
    "RATING_TO_ACTION",
    "CONVICTION_TO_CONFIDENCE",
    "TIER_MULT",
    "ENTRY_ACTIONS",
    "EXIT_ACTIONS",
    "SignalDecision",
    "Quote",
    "Order",
    "Fill",
    "Position",
    "GateResult",
    "OrderReport",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Action(str, Enum):
    """Execution action, mapped 1:1 from the existing 5-tier portfolio rating."""

    STRONG_BUY = "strong_buy"  # rating Buy
    BUY = "buy"                # rating Overweight
    HOLD = "hold"             # rating Hold
    REDUCE = "reduce"          # rating Underweight
    EXIT = "exit"             # rating Sell


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"  # LIMIT deferred to Phase 5 (v2)


class TimeInForce(str, Enum):
    IOC = "ioc"  # DAY / resting deferred to Phase 5 (v2)


class OrderState(str, Enum):
    NEW = "new"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class FillStatus(str, Enum):
    """Richer, side-aware outcome of a paper order (super-set of OrderState semantics).

    ``INTENDED_EXIT_UNFILLED`` is the safety-critical one: an EXIT/REDUCE that found no
    fillable market is **never** a silent close or a normal rejection — it is recorded
    so the trapped position stays visible (spine Contract 5).
    """

    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"                          # entries: no fillable market
    INTENDED_EXIT_UNFILLED = "intended_exit_unfilled"  # exits: no fillable market


# ---------------------------------------------------------------------------
# Maps (the only "business rules" allowed in a pure-data module)
# ---------------------------------------------------------------------------

# rating label (PortfolioRating.value) -> Action. Total over the 5-tier scale.
RATING_TO_ACTION: dict[str, Action] = {
    "Buy": Action.STRONG_BUY,
    "Overweight": Action.BUY,
    "Hold": Action.HOLD,
    "Underweight": Action.REDUCE,
    "Sell": Action.EXIT,
}

# categorical conviction -> confidence float. Uncalibrated proxy, recalibrated in Phase 5.
CONVICTION_TO_CONFIDENCE: dict[str, float] = {
    "high": 0.85,
    "medium": 0.60,
    "low": 0.35,
}

# per-action sizing multiplier. Only entries deploy capital; HOLD/REDUCE/EXIT
# don't multiply a fresh target (REDUCE/EXIT targets are computed from current qty).
TIER_MULT: dict[Action, float] = {
    Action.STRONG_BUY: 1.0,
    Action.BUY: 0.6,
    Action.HOLD: 0.0,
    Action.REDUCE: 0.0,
    Action.EXIT: 0.0,
}

ENTRY_ACTIONS = frozenset({Action.STRONG_BUY, Action.BUY})
EXIT_ACTIONS = frozenset({Action.REDUCE, Action.EXIT})


def _valid_confidence(x: Optional[float]) -> bool:
    return x is not None and isinstance(x, (int, float)) and not math.isnan(x) and 0.0 <= x <= 1.0


# ---------------------------------------------------------------------------
# Contract 1 — SignalDecision (analysis engine -> execution)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalDecision:
    """The single typed hand-off. Execution consumes ONLY this — never the LLM prose."""

    symbol: str                 # canonical, e.g. "RELIANCE.NS"
    action: Action
    confidence: float           # 0..1, DERIVED from categorical conviction (never LLM-emitted float)
    as_of: datetime             # IST; decision timestamp (look-ahead boundary)
    rating_raw: str             # original 5-tier label, for audit
    rationale_digest: str       # short hash/snippet of the report (audit only, not logic)
    data_freshness: dict        # {source: last_updated_ts} — feeds the stale-data gate (REQUIRED)
    horizon_days: int = 1       # once-daily cadence default
    schema_version: int = 1     # defaulted fields MUST come last

    @classmethod
    def from_portfolio_decision(
        cls,
        decision,                       # tradingagents.agents.schemas.PortfolioDecision
        *,
        symbol: str,
        as_of: datetime,
        data_freshness: dict,
        rationale_digest: Optional[str] = None,
    ) -> "SignalDecision":
        """Build a SignalDecision from the PM's typed output, deriving confidence.

        Fail-closed rule (spine v2.C): the PM emits a categorical ``conviction``
        (low/medium/high); code maps it to a float. If that float can't be derived
        and validated for an **entry** (Buy/Overweight), the action is forced to HOLD
        (confidence 0.0). REDUCE/EXIT are never suppressed by a missing confidence —
        confidence is irrelevant to exits — so they pass through with confidence 0.0
        when unparseable. This is the only place this translation exists.
        """
        rating_raw = decision.rating.value if hasattr(decision.rating, "value") else str(decision.rating)
        action = RATING_TO_ACTION.get(rating_raw, Action.HOLD)

        conviction = getattr(decision, "conviction", None)
        conviction_key = str(conviction).strip().lower() if conviction is not None else None
        derived = CONVICTION_TO_CONFIDENCE.get(conviction_key) if conviction_key else None

        if action in ENTRY_ACTIONS:
            if _valid_confidence(derived):
                confidence = float(derived)
            else:
                # Cannot validate conviction for an entry -> fail closed to HOLD.
                action = Action.HOLD
                confidence = 0.0
        else:
            # HOLD / REDUCE / EXIT — confidence is not used to gate these.
            confidence = float(derived) if _valid_confidence(derived) else 0.0

        if rationale_digest is None:
            thesis = getattr(decision, "investment_thesis", "") or ""
            rationale_digest = hashlib.sha256(thesis.encode("utf-8")).hexdigest()[:16]

        return cls(
            symbol=symbol,
            action=action,
            confidence=confidence,
            as_of=as_of,
            rating_raw=rating_raw,
            rationale_digest=rationale_digest,
            data_freshness=dict(data_freshness),
        )


# ---------------------------------------------------------------------------
# Contract 3 — broker data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    """Snapshot used by the fill simulator + spread/liquidity gates. From getMarketData(FULL)."""

    symbol: str
    ltp: float
    bid: float
    ask: float
    ts: datetime
    bid_qty: int
    ask_qty: int

    @property
    def mid(self) -> Optional[float]:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return None

    @property
    def spread(self) -> Optional[float]:
        if self.bid > 0 and self.ask > 0:
            return self.ask - self.bid
        return None

    @property
    def spread_frac(self) -> Optional[float]:
        """Spread as a fraction of mid (e.g. 0.0005 == 0.05%). None when no two-sided book."""
        mid = self.mid
        if mid and mid > 0 and self.spread is not None:
            return self.spread / mid
        return None

    @property
    def has_book(self) -> bool:
        """True only when both touches have a positive price AND a positive quantity."""
        return self.bid > 0 and self.ask > 0 and self.bid_qty > 0 and self.ask_qty > 0


@dataclass(frozen=True)
class Order:
    """Phase 3 = MARKET + IOC only. ``client_oid`` is the idempotency key."""

    symbol: str
    side: Side
    qty: int
    order_type: OrderType = OrderType.MARKET
    tif: TimeInForce = TimeInForce.IOC
    product: str = "CNC"
    client_oid: str = field(default_factory=lambda: uuid4().hex)

    @property
    def is_exit(self) -> bool:
        """Long-only CNC: a SELL is always an exit/reduce, a BUY always an entry/add.

        Drives the side-aware reject-vs-unfilled rule in the paper simulator and the
        entry-quality gate class.
        """
        return self.side == Side.SELL


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: Side
    qty: int
    price: float
    ts: datetime
    is_partial: bool


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: int
    avg_price: float
    ltp: float
    realized_pnl: float
    unrealized_pnl: float


# ---------------------------------------------------------------------------
# Contract 4 — gate result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    gate: str            # which gate decided
    reason: str          # human-readable, goes to audit
    severity: str        # "block" | "warn"

    @property
    def blocked(self) -> bool:
        return (not self.allowed) and self.severity == "block"


# ---------------------------------------------------------------------------
# Order outcome (paper broker -> router/audit) — side-aware
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderReport:
    """Result of submitting an Order to a (paper) broker.

    ``status`` carries the side-aware semantics: an entry that finds no market is
    ``REJECTED``; an exit that finds no market is ``INTENDED_EXIT_UNFILLED`` (the
    position is NOT assumed closed). ``fill`` is present for FILLED/PARTIAL.
    """

    order: Order
    status: FillStatus
    requested_qty: int
    filled_qty: int
    reason: str
    fill: Optional[Fill] = None

    @property
    def is_filled(self) -> bool:
        return self.status in (FillStatus.FILLED, FillStatus.PARTIAL) and self.fill is not None
