"""
Tests for B4 — A/B token-budget acceptance and alignment checks.

Uses the same prompt-capture monkeypatch approach as test_pm_rules_injection.
All tests are fully offline.
"""

from __future__ import annotations

from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_pm_rules_injection)
# ---------------------------------------------------------------------------


def _make_fake_state(
    ticker: str = "TCS.NS",
    investment_plan: str = "Moderate bullish outlook",
    trader_plan: str = "Buy 100 shares",
    past_context: str = "",
    history: str = "Bull: positive. Bear: negative.",
) -> dict:
    return {
        "company_of_interest": ticker,
        "asset_type": "stock",
        "instrument_context": f"The instrument to analyze is `{ticker}`.",
        "investment_plan": investment_plan,
        "trader_investment_plan": trader_plan,
        "past_context": past_context,
        "risk_debate_state": {
            "history": history,
            "aggressive_history": "aggressive text",
            "conservative_history": "conservative text",
            "neutral_history": "neutral text",
            "latest_speaker": "Aggressive Analyst",
            "current_aggressive_response": "agg resp",
            "current_conservative_response": "con resp",
            "current_neutral_response": "neu resp",
            "count": 1,
            "judge_decision": None,
        },
    }


class _FakeLLM:
    def with_structured_output(self, schema):
        raise NotImplementedError("Stub LLM: no structured output")

    def invoke(self, prompt):
        class _Resp:
            content = "**Rating**: Hold\n\n**Executive Summary**: Test.\n\n**Investment Thesis**: Test."
        return _Resp()


def _make_fake_invoke(rating="Hold", conviction="medium"):
    """Factory that returns a fake invoke_structured_or_freetext capturing the prompt."""
    captured = {}

    def fake_invoke(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
        captured["prompt"] = prompt
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating, render_pm_decision
        rating_enum = getattr(PortfolioRating, rating.upper(), PortfolioRating.HOLD)
        pd = PortfolioDecision(
            rating=rating_enum,
            conviction=conviction,
            executive_summary="Hold for now.",
            investment_thesis="Balanced debate.",
        )
        return render_pm_decision(pd), pd

    return fake_invoke, captured


# ---------------------------------------------------------------------------
# B4.1: Token budget
# ---------------------------------------------------------------------------


class TestTokenBudget:
    """The digest adds at most DIGEST_TOKEN_BUDGET tokens to the PM prompt."""

    def test_overhead_within_budget(self, monkeypatch):
        """Token overhead is bounded: digest.token_estimate <= DIGEST_TOKEN_BUDGET.

        FIX-G (b): assert directly against render_digest().token_estimate rather
        than computing a differenced-ceil metric, which can give misleading results
        due to ceil rounding. The digest token estimate is the clean instrument.
        """
        from agent_os.rules.loader import render_digest, DIGEST_TOKEN_BUDGET

        digest = render_digest()
        assert digest.token_estimate <= DIGEST_TOKEN_BUDGET, (
            f"render_digest().token_estimate {digest.token_estimate} exceeds "
            f"DIGEST_TOKEN_BUDGET {DIGEST_TOKEN_BUDGET}"
        )

    def test_injected_prompt_equals_digest_separator_plus_original(self, monkeypatch):
        """The injected prompt must equal digest.text + '\\n\\n---\\n\\n' + the default(None) prompt.

        FIX-L: replaces the prior tautological test that back-computed the separator
        from the two prompts (so the equality held by construction and verified nothing).
        This test pins both the separator literal ('\\n\\n---\\n\\n') and the prepend
        order directly, so a change to either the separator string or the injection
        order will produce a real assertion failure.
        """
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from agent_os.rules.loader import render_digest

        digest = render_digest()
        digest_text = digest.text

        # Capture prompt WITHOUT digest (the baseline)
        fake_no, cap_no = _make_fake_invoke()
        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_no)
        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=None)
        node(_make_fake_state())
        prompt_without = cap_no["prompt"]

        # Capture prompt WITH digest
        fake_with, cap_with = _make_fake_invoke()
        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_with)
        node2 = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=digest_text)
        node2(_make_fake_state())
        prompt_with = cap_with["prompt"]

        # The separator is the literal "\n\n---\n\n" that the PM injects between
        # the digest and the original prompt body.
        _SEPARATOR = "\n\n---\n\n"
        expected = digest_text + _SEPARATOR + prompt_without
        assert prompt_with == expected, (
            f"Injected prompt does not equal digest.text + '\\n\\n---\\n\\n' + original.\n"
            f"  len(prompt_with)={len(prompt_with)}\n"
            f"  len(expected)={len(expected)}\n"
            f"  digest_text[:40]={digest_text[:40]!r}\n"
            f"  prompt_without[:40]={prompt_without[:40]!r}"
        )

    def test_prompt_without_digest_unchanged(self, monkeypatch):
        """prompt_without must equal the prompt captured with digest=None (baseline)."""
        from tradingagents.agents.managers import portfolio_manager as pm_module

        fake_a, cap_a = _make_fake_invoke()
        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_a)
        node_a = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=None)
        node_a(_make_fake_state())

        fake_b, cap_b = _make_fake_invoke()
        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_b)
        node_b = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=None)
        node_b(_make_fake_state())

        assert cap_a["prompt"] == cap_b["prompt"], (
            "Two runs with digest=None must produce byte-identical prompts"
        )

    def test_prompt_with_digest_longer_than_without(self, monkeypatch):
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from agent_os.rules.loader import render_digest

        digest = render_digest()

        fake_no, cap_no = _make_fake_invoke()
        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_no)
        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=None)
        node(_make_fake_state())

        fake_with, cap_with = _make_fake_invoke()
        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_with)
        node2 = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=digest.text)
        node2(_make_fake_state())

        assert len(cap_with["prompt"]) > len(cap_no["prompt"]), (
            "Prompt with digest must be longer than prompt without"
        )


# ---------------------------------------------------------------------------
# B4.2: No degradation — structured + free-text paths still work
# ---------------------------------------------------------------------------


class TestNoDegradation:
    """With digest, the node still passes through a valid PortfolioDecision."""

    def test_portfolio_decision_passed_through_structured_path(self, monkeypatch):
        """The stub returns a PortfolioDecision; portfolio_decision must be that value."""
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating, render_pm_decision
        from agent_os.rules.loader import render_digest

        digest = render_digest()
        captured_pd = {}

        def fake_invoke(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
            pd = PortfolioDecision(
                rating=PortfolioRating.BUY,
                conviction="high",
                executive_summary="Strong buy.",
                investment_thesis="All signals bullish.",
            )
            captured_pd["pd"] = pd
            return render_pm_decision(pd), pd

        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_invoke)

        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=digest.text)
        result = node(_make_fake_state())

        assert result["portfolio_decision"] is captured_pd["pd"], (
            "portfolio_decision must be the same PortfolioDecision returned by invoke"
        )

    def test_free_text_fallback_maps_none_through(self, monkeypatch):
        """When stub returns parsed=None (free-text fallback), portfolio_decision is None."""
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from agent_os.rules.loader import render_digest

        digest = render_digest()

        def fake_invoke_freetext(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
            # Simulate free-text fallback: parsed is None
            return "**Rating**: Hold\n\n**Executive Summary**: Test.\n\n**Investment Thesis**: Test.", None

        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_invoke_freetext)

        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=digest.text)
        result = node(_make_fake_state())

        assert result["portfolio_decision"] is None, (
            "portfolio_decision must be None when the free-text fallback fires"
        )

    def test_required_keys_present_with_real_digest(self, monkeypatch):
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating, render_pm_decision
        from agent_os.rules.loader import render_digest

        digest = render_digest()

        def fake_invoke(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
            pd = PortfolioDecision(
                rating=PortfolioRating.HOLD,
                conviction="medium",
                executive_summary="Balanced.",
                investment_thesis="Balanced debate.",
            )
            return render_pm_decision(pd), pd

        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_invoke)

        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=digest.text)
        result = node(_make_fake_state())

        for key in ("risk_debate_state", "final_trade_decision", "portfolio_decision"):
            assert key in result, f"Missing key '{key}' in result with real digest"


# ---------------------------------------------------------------------------
# B4.3: Alignment (structural, offline)
# ---------------------------------------------------------------------------


class TestAlignmentStructural:
    """Verify the digest REACHES the decider (PM) with correct hard-stop text.

    FIX-F: This class verifies REACHABILITY only — that the rule text appears
    in the prompt that is passed to the PM. It does NOT verify that the PM
    acts on the rule (declines, downgraded). The PM-decline/downgrade half of
    the D4.3 alignment criterion requires a live LLM and is DEFERRED (see
    test_d43_pm_decline_deferred below).
    """

    def test_digest_contains_confidence_floor_hardstop(self, monkeypatch):
        """The injected digest must contain the confidence-floor hard stop text.

        This proves the rule reaches the decider (the PM).
        """
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from agent_os.rules.loader import render_digest

        digest = render_digest()

        captured = {}

        def fake_invoke(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
            captured["prompt"] = prompt
            from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating, render_pm_decision
            pd = PortfolioDecision(
                rating=PortfolioRating.HOLD,
                conviction="low",
                executive_summary="Hold.",
                investment_thesis="Balanced.",
            )
            return render_pm_decision(pd), pd

        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_invoke)

        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=digest.text)
        node(_make_fake_state())

        prompt = captured["prompt"]
        # The confidence-floor hard stop must be visible in the full prompt
        assert "Confidence below floor" in prompt, (
            "Digest must carry 'Confidence below floor' hard-stop text to the PM"
        )
        assert "0.60" in prompt, (
            "Confidence floor value 0.60 must appear in the injected prompt"
        )

    def test_digest_contains_outside_execution_window_hardstop(self, monkeypatch):
        """The injected digest must contain the 'outside the execution window' hard stop."""
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from agent_os.rules.loader import render_digest

        digest = render_digest()
        captured = {}

        def fake_invoke(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
            captured["prompt"] = prompt
            from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating, render_pm_decision
            pd = PortfolioDecision(
                rating=PortfolioRating.HOLD,
                conviction="low",
                executive_summary="Hold.",
                investment_thesis="Balanced.",
            )
            return render_pm_decision(pd), pd

        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_invoke)

        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=digest.text)
        node(_make_fake_state())

        assert "outside the execution window" in captured["prompt"], (
            "Hard stop 'outside the execution window' must appear in the injected prompt"
        )

    def test_gate_blocks_low_confidence_regardless_of_pm_prompt(self):
        """The confidence-floor gate blocks a low-confidence signal regardless of the prompt.

        This test verifies that even if a digest-injected PM prompt were to somehow
        recommend a low-confidence trade, the deterministic confidence_floor gate in
        execution/ will block the order. The gate operates independently of the PM prompt.
        """
        from execution.risk.guards import evaluate, blocking_results, GateContext
        from execution.contracts import Action, Order, Side, SignalDecision, Quote
        from execution.security_master import Instrument
        from datetime import datetime, timedelta

        NOW = datetime(2026, 6, 9, 11, 0)
        INST = Instrument("TCS.NS", "NSE", "532540", "", lot_size=1, tick_size=0.05)

        def _fresh():
            return {k: (NOW - timedelta(hours=1)).isoformat() for k in
                    ["daily OHLCV", "security master", "news", "social", "fundamentals"]}

        # Confidence 0.35 < floor 0.60 — gate must block
        signal = SignalDecision("TCS.NS", Action.BUY, 0.35, NOW, "Buy", "d", _fresh())
        order = Order("TCS.NS", Side.BUY, 10)

        class _FakePortfolio:
            def qty(self, _): return 0
            def settled_qty(self, _): return 0
            def buying_power(self): return 10_000_000.0
            def open_position_count(self): return 0
            def held_symbols(self): return []

        quote = Quote("TCS.NS", ltp=3800.0, bid=3799.9, ask=3800.1,
                      ts=NOW, bid_qty=500, ask_qty=400)

        ctx = GateContext(
            order=order,
            signal=signal,
            instrument=INST,
            portfolio=_FakePortfolio(),
            equity=1_000_000.0,
            now=NOW,
            ref_price=3800.0,
            quote=quote,
            holidays=[],
        )
        blocked = {r.gate for r in blocking_results(evaluate(ctx))}
        assert "confidence_floor" in blocked, (
            "confidence_floor gate must block a signal with confidence 0.35 < 0.60 "
            "regardless of what the PM prompt says"
        )


# ---------------------------------------------------------------------------
# FIX-F: Deferred test — D4.3 PM-decline/downgrade behavior
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "D4.3 PM-decline/downgrade behavior requires a live LLM; the offline harness "
        "cannot exercise PM judgment. Deferred to the live A/B run, which is gated "
        "behind inject_rules_digest=False until inspected. "
        "The gate-blocks half IS covered by "
        "test_gate_blocks_low_confidence_regardless_of_pm_prompt in TestAlignmentStructural."
    )
)
def test_d43_pm_decline_downgrade_deferred():
    """[DEFERRED — live LLM required] D4.3: rule-violating scenario is declined/downgraded by PM.

    The D4.3 alignment criterion (doc 16) requires that a deliberately
    rule-violating scenario (e.g. high-confidence BUY on a name flagged stale
    or outside hours) is both:
      (a) declined or downgraded by the PM (this test — deferred), AND
      (b) blocked by the deterministic gate regardless (covered offline above).

    The live A/B run must verify that when inject_rules_digest=True, a PM
    receiving the digest with the stale/outside-hours hard stop responds with
    a HOLD or lower conviction (not a BUY) for a scenario that violates those
    hard stops. This requires a real LLM call and cannot be meaningfully asserted
    with a stub/monkeypatched invoke.
    """
    raise NotImplementedError("This test must only run against a live LLM in the A/B harness.")
