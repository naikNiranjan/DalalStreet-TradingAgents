"""Step 0 (integration slice doc 14) — surface the typed PM decision.

The execution layer derives confidence from ``PortfolioDecision.conviction``,
which ``render_pm_decision`` does NOT put in the markdown. So the PM node must
ALSO expose the typed object (additively) in graph state, without changing the
markdown path or the shared ``str``-returning contract of
``invoke_structured_or_freetext`` (other callers: sentiment / RM / trader).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    render_pm_decision,
)
from tradingagents.agents.utils.structured import invoke_structured_or_freetext


# ---------------------------------------------------------------------------
# Fakes (mirrors tests/test_memory_log.py style)
# ---------------------------------------------------------------------------


def _make_pm_state(past_context=""):
    return {
        "company_of_interest": "RELIANCE.NS",
        "past_context": past_context,
        "risk_debate_state": {
            "history": "Risk debate history.",
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "judge_decision": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "count": 1,
        },
        "market_report": "Market report.",
        "sentiment_report": "Sentiment report.",
        "news_report": "News report.",
        "fundamentals_report": "Fundamentals report.",
        "investment_plan": "Research plan.",
        "trader_investment_plan": "Trader plan.",
    }


def _structured_pm_llm(decision: PortfolioDecision | None = None):
    if decision is None:
        decision = PortfolioDecision(
            rating=PortfolioRating.BUY,
            conviction="high",
            executive_summary="Enter; strong setup.",
            investment_thesis="Both sides agree the thesis is well supported.",
        )
    structured = MagicMock()
    structured.invoke.return_value = decision
    llm = MagicMock()
    llm.with_structured_output.return_value = decision and structured
    return llm


def _render(x):
    """A render fn for the helper tests (mimics render_pm_decision's signature)."""
    return f"RENDERED: {getattr(x, 'rating', x)}"


# ---------------------------------------------------------------------------
# invoke_structured_or_freetext — return_parsed flag (API-preserving)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvokeReturnParsedFlag:
    def test_default_returns_str_only_backcompat(self):
        """Default (return_parsed=False) keeps the str contract for other callers."""
        decision = PortfolioDecision(
            rating=PortfolioRating.HOLD, conviction="low",
            executive_summary="x", investment_thesis="y",
        )
        structured = MagicMock()
        structured.invoke.return_value = decision
        out = invoke_structured_or_freetext(structured, MagicMock(), "p", render_pm_decision, "PM")
        assert isinstance(out, str)
        assert "**Rating**: Hold" in out

    def test_return_parsed_true_gives_markdown_and_object(self):
        decision = PortfolioDecision(
            rating=PortfolioRating.BUY, conviction="high",
            executive_summary="x", investment_thesis="y",
        )
        structured = MagicMock()
        structured.invoke.return_value = decision
        md, parsed = invoke_structured_or_freetext(
            structured, MagicMock(), "p", render_pm_decision, "PM", return_parsed=True
        )
        assert isinstance(md, str) and "**Rating**: Buy" in md
        assert parsed is decision  # the typed object survives, not just markdown

    def test_return_parsed_true_none_when_structured_unavailable(self):
        """No structured binding -> free text + None parsed object."""
        plain = MagicMock()
        plain.invoke.return_value = MagicMock(content="free text decision")
        md, parsed = invoke_structured_or_freetext(
            None, plain, "p", _render, "PM", return_parsed=True
        )
        assert md == "free text decision"
        assert parsed is None

    def test_return_parsed_true_none_when_structured_call_fails(self):
        """Structured call raises -> fall back to free text + None parsed object."""
        structured = MagicMock()
        structured.invoke.side_effect = ValueError("bad JSON")
        plain = MagicMock()
        plain.invoke.return_value = MagicMock(content="fallback prose")
        md, parsed = invoke_structured_or_freetext(
            structured, plain, "p", _render, "PM", return_parsed=True
        )
        assert md == "fallback prose"
        assert parsed is None

    def test_other_callers_unaffected_str_still_returned_on_failure(self):
        """The shared str path (return_parsed default) is preserved on fallback too."""
        structured = MagicMock()
        structured.invoke.side_effect = ValueError("bad JSON")
        plain = MagicMock()
        plain.invoke.return_value = MagicMock(content="prose")
        out = invoke_structured_or_freetext(structured, plain, "p", _render, "RM")
        assert out == "prose"


# ---------------------------------------------------------------------------
# PM node — additive typed decision in state
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPortfolioManagerNodeSurfacesTypedDecision:
    def test_node_stores_typed_decision_with_conviction(self):
        decision = PortfolioDecision(
            rating=PortfolioRating.BUY,
            conviction="high",
            executive_summary="Enter gradually.",
            investment_thesis="Strong, consistent evidence.",
        )
        pm_node = create_portfolio_manager(_structured_pm_llm(decision))
        result = pm_node(_make_pm_state())
        pd = result["portfolio_decision"]
        assert isinstance(pd, PortfolioDecision)
        assert pd.rating is PortfolioRating.BUY
        assert pd.conviction == "high"  # the field render_pm_decision drops

    def test_markdown_path_unchanged(self):
        """final_trade_decision still carries the rendered markdown (memory/CLI/report)."""
        decision = PortfolioDecision(
            rating=PortfolioRating.OVERWEIGHT, conviction="medium",
            executive_summary="Build slowly.", investment_thesis="Reasonable case.",
        )
        pm_node = create_portfolio_manager(_structured_pm_llm(decision))
        result = pm_node(_make_pm_state())
        assert "**Rating**: Overweight" in result["final_trade_decision"]

    def test_freetext_fallback_yields_none_typed_decision(self):
        """Provider without structured output -> markdown prose + portfolio_decision None."""
        plain_response = "**Rating**: Sell\n\nExit ahead of guidance."
        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("unsupported")
        llm.invoke.return_value = MagicMock(content=plain_response)
        pm_node = create_portfolio_manager(llm)
        result = pm_node(_make_pm_state())
        assert result["final_trade_decision"] == plain_response
        assert result["portfolio_decision"] is None
