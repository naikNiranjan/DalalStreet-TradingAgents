"""Step 2 (integration slice doc 14) — graph state -> SignalDecision bridge.

``build_signal`` is the SOLE caller of ``SignalDecision.from_portfolio_decision``
and must NEVER pass it ``None``:

  * typed ``portfolio_decision`` present -> typed path (derives confidence from conviction)
  * ``portfolio_decision`` is None (free-text fallback / unparseable) -> a safe **HOLD**
    SignalDecision (confidence 0.0), provenance ``pm_decision_unstructured``, with
    ``rating_raw`` still captured from the markdown for audit.

For this same-session first slice (no carried positions) HOLD is a no-op — the
safest fallback. Mapping markdown Underweight/Sell -> REDUCE/EXIT-with-position is
deferred (needs multi-session carry-over).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from execution.bridge import build_signal
from execution.contracts import Action
from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

AS_OF = datetime(2026, 6, 9, 11, 0)
FRESH = {"daily OHLCV": "2026-06-09T00:00:00", "security master": "2026-06-09T09:00:00"}


def _state(portfolio_decision=None, markdown="**Rating**: Hold\n\nNo action."):
    return {"final_trade_decision": markdown, "portfolio_decision": portfolio_decision}


def _pd(rating, conviction):
    return PortfolioDecision(
        rating=rating, conviction=conviction,
        executive_summary="x", investment_thesis="thesis text",
    )


# ---------------------------------------------------------------------------
# Typed path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTypedPath:
    def test_buy_high_conviction_derives_confidence(self):
        br = build_signal(_state(_pd(PortfolioRating.BUY, "high")),
                          "RELIANCE.NS", as_of=AS_OF, data_freshness=FRESH)
        assert br.provenance == "typed"
        assert br.signal.action is Action.STRONG_BUY     # rating "Buy" -> STRONG_BUY
        assert br.signal.confidence == 0.85              # high -> 0.85, derived in code
        assert br.signal.symbol == "RELIANCE.NS"
        assert br.signal.rating_raw == "Buy"

    def test_overweight_medium_maps_to_buy_action(self):
        br = build_signal(_state(_pd(PortfolioRating.OVERWEIGHT, "medium")),
                          "INFY.NS", as_of=AS_OF, data_freshness=FRESH)
        assert br.signal.action is Action.BUY
        assert br.signal.confidence == 0.60

    def test_low_conviction_entry_keeps_action_but_low_confidence(self):
        """from_portfolio_decision derives 0.35 (valid); the confidence_floor GATE,
        not the bridge, rejects it later — the bridge must not silently flip to HOLD."""
        br = build_signal(_state(_pd(PortfolioRating.BUY, "low")),
                          "RELIANCE.NS", as_of=AS_OF, data_freshness=FRESH)
        assert br.signal.action is Action.STRONG_BUY
        assert br.signal.confidence == 0.35

    def test_sell_maps_to_exit_confidence_irrelevant(self):
        br = build_signal(_state(_pd(PortfolioRating.SELL, "low")),
                          "ITC.NS", as_of=AS_OF, data_freshness=FRESH)
        assert br.signal.action is Action.EXIT
        assert br.provenance == "typed"

    def test_freshness_and_as_of_carried_through(self):
        br = build_signal(_state(_pd(PortfolioRating.BUY, "high")),
                          "RELIANCE.NS", as_of=AS_OF, data_freshness=FRESH)
        assert br.signal.as_of == AS_OF
        assert br.signal.data_freshness == FRESH
        assert br.signal.data_freshness is not FRESH  # defensive copy


# ---------------------------------------------------------------------------
# Free-text fallback path (portfolio_decision is None) -> HOLD
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFreeTextFallback:
    def test_none_decision_yields_hold_no_op(self):
        br = build_signal(_state(None, "**Rating**: Buy\n\nStrong."),
                          "RELIANCE.NS", as_of=AS_OF, data_freshness=FRESH)
        assert br.provenance == "pm_decision_unstructured"
        assert br.signal.action is Action.HOLD      # safe no-op, regardless of prose
        assert br.signal.confidence == 0.0

    def test_rating_raw_captured_from_markdown_for_audit(self):
        """A markdown Sell is recorded in rating_raw (audit) but NOT acted on as EXIT
        (exit-with-position needs multi-session carry-over, deferred)."""
        br = build_signal(_state(None, "**Rating**: Sell\n\nExit ahead of guidance."),
                          "ITC.NS", as_of=AS_OF, data_freshness=FRESH)
        assert br.signal.rating_raw == "Sell"
        assert br.signal.action is Action.HOLD

    def test_missing_portfolio_decision_key_is_treated_as_none(self):
        br = build_signal({"final_trade_decision": "**Rating**: Hold"},
                          "X.NS", as_of=AS_OF, data_freshness=FRESH)
        assert br.signal.action is Action.HOLD
        assert br.provenance == "pm_decision_unstructured"

    def test_missing_markdown_defaults_rating_to_hold(self):
        br = build_signal(_state(None, ""), "X.NS", as_of=AS_OF, data_freshness=FRESH)
        assert br.signal.rating_raw == "Hold"
        assert br.signal.action is Action.HOLD

    def test_freshness_carried_through_on_fallback(self):
        br = build_signal(_state(None), "X.NS", as_of=AS_OF, data_freshness=FRESH)
        assert br.signal.data_freshness == FRESH
        assert br.signal.as_of == AS_OF
