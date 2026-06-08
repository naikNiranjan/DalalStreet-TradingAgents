"""Graph -> execution bridge (integration slice doc 14 §1).

``build_signal`` is the **single** point where the analysis graph's output becomes
a typed :class:`SignalDecision`. It is the only caller of
:meth:`SignalDecision.from_portfolio_decision` and it **never passes ``None``**:

  * ``state["portfolio_decision"]`` present (typed PM output) -> typed path; the
    confidence is derived from the categorical ``conviction`` in code.
  * absent / ``None`` (the PM fell back to free text, or conviction was
    unparseable) -> a safe **HOLD** SignalDecision (confidence 0.0). The 5-tier
    rating is still parsed from the markdown into ``rating_raw`` for the audit
    trail, but it is NOT acted on (mapping a markdown Underweight/Sell to
    REDUCE/EXIT requires an existing position and multi-session carry-over —
    deferred). For the first same-session run with no carried positions, HOLD is
    a no-op: the safest possible fallback.

The provenance ("typed" vs "pm_decision_unstructured") is returned alongside the
signal so the session runner can record it in the coverage / analysis manifest.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from tradingagents.agents.utils.rating import parse_rating

from .contracts import Action, SignalDecision

__all__ = ["build_signal", "BridgeResult"]


@dataclass(frozen=True)
class BridgeResult:
    """A bridged signal plus how it was derived (for the analysis manifest)."""

    signal: SignalDecision
    provenance: str  # "typed" | "pm_decision_unstructured"


def build_signal(
    state: dict,
    symbol: str,
    *,
    as_of: datetime,
    data_freshness: dict,
) -> BridgeResult:
    """Turn one graph ``final_state`` into a typed signal (see module docstring)."""
    decision = state.get("portfolio_decision")

    if decision is not None:
        signal = SignalDecision.from_portfolio_decision(
            decision, symbol=symbol, as_of=as_of, data_freshness=data_freshness,
        )
        return BridgeResult(signal=signal, provenance="typed")

    # Free-text fallback: emit a HOLD no-op. Capture the markdown rating for audit
    # only (rating_raw), never as an action this slice.
    markdown = state.get("final_trade_decision", "") or ""
    rating_raw = parse_rating(markdown)  # defaults to "Hold"
    digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]
    signal = SignalDecision(
        symbol=symbol,
        action=Action.HOLD,
        confidence=0.0,
        as_of=as_of,
        rating_raw=rating_raw,
        rationale_digest=digest,
        data_freshness=dict(data_freshness),
    )
    return BridgeResult(signal=signal, provenance="pm_decision_unstructured")
