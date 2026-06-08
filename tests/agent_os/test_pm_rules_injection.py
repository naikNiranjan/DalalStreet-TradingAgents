"""
Tests for B2 — PM prompt seam: rules_digest injection into portfolio_manager.

All tests are fully offline: invoke_structured_or_freetext is monkeypatched so
no real LLM calls occur. The goal is to verify that:
  (a) default rules_digest=None -> prompt is byte-for-byte unchanged
  (b) with a digest, the digest text prepends the existing prompt
  (c) the node returns the required keys in both cases
"""

from __future__ import annotations

from typing import Optional, Tuple

import pytest

# ---------------------------------------------------------------------------
# Helpers — minimal fake state and LLM
# ---------------------------------------------------------------------------


def _make_fake_state(
    ticker: str = "TCS.NS",
    investment_plan: str = "Moderate bullish outlook",
    trader_plan: str = "Buy 100 shares",
    past_context: str = "",
    history: str = "Bull: positive. Bear: negative.",
) -> dict:
    """Build a minimal AgentState-compatible dict the PM node can consume."""
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
    """Stub LLM that raises NotImplementedError for with_structured_output."""

    def with_structured_output(self, schema):
        raise NotImplementedError("Stub LLM: no structured output")

    def invoke(self, prompt):
        class _Resp:
            content = "**Rating**: Hold\n\n**Executive Summary**: Test.\n\n**Investment Thesis**: Test."
        return _Resp()


# ---------------------------------------------------------------------------
# B2 tests
# ---------------------------------------------------------------------------


class TestPMSignatureAndDefaultPath:
    """create_portfolio_manager(llm, rules_digest=None) — default is byte-identical."""

    def test_signature_accepts_rules_digest(self):
        """create_portfolio_manager must accept a rules_digest keyword argument."""
        import inspect
        from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager

        sig = inspect.signature(create_portfolio_manager)
        assert "rules_digest" in sig.parameters, (
            "create_portfolio_manager must have a rules_digest parameter"
        )

    def test_default_rules_digest_is_none(self):
        """The default value for rules_digest must be None."""
        import inspect
        from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager

        sig = inspect.signature(create_portfolio_manager)
        param = sig.parameters["rules_digest"]
        assert param.default is None, (
            f"rules_digest default must be None, got {param.default!r}"
        )


class TestPMPromptWithoutDigest:
    """With rules_digest=None, the captured prompt must start with 'As the Portfolio Manager'."""

    def test_prompt_starts_with_as_portfolio_manager(self, monkeypatch):
        """Default path: no digest -> prompt unchanged."""
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

        captured = {}

        def fake_invoke(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
            captured["prompt"] = prompt
            # Return a valid (str, PortfolioDecision) tuple
            pd = PortfolioDecision(
                rating=PortfolioRating.HOLD,
                conviction="medium",
                executive_summary="Hold for now.",
                investment_thesis="Balanced debate.",
            )
            from tradingagents.agents.schemas import render_pm_decision
            return render_pm_decision(pd), pd

        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_invoke)

        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=None)
        node(_make_fake_state())

        prompt = captured["prompt"]
        assert prompt.startswith("As the Portfolio Manager"), (
            f"Default prompt must start with 'As the Portfolio Manager'; got: {prompt[:80]!r}"
        )

    def test_prompt_does_not_contain_digest_sentinel(self, monkeypatch):
        """No digest injected -> sentinel text absent."""
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

        captured = {}

        def fake_invoke(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
            captured["prompt"] = prompt
            pd = PortfolioDecision(
                rating=PortfolioRating.HOLD,
                conviction="medium",
                executive_summary="Hold.",
                investment_thesis="Balanced.",
            )
            from tradingagents.agents.schemas import render_pm_decision
            return render_pm_decision(pd), pd

        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_invoke)

        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=None)
        node(_make_fake_state())

        assert "SENTINEL-DIGEST" not in captured["prompt"]
        assert "BINDING ALIGNMENT DIGEST" not in captured["prompt"]


class TestPMPromptWithDigest:
    """With a real or sentinel digest, the digest must prepend the existing prompt."""

    SENTINEL = "SENTINEL-DIGEST-XYZ-TEST-UNIQUE"

    def test_digest_text_appears_in_prompt(self, monkeypatch):
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

        captured = {}

        def fake_invoke(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
            captured["prompt"] = prompt
            pd = PortfolioDecision(
                rating=PortfolioRating.HOLD,
                conviction="low",
                executive_summary="Hold.",
                investment_thesis="Balanced.",
            )
            from tradingagents.agents.schemas import render_pm_decision
            return render_pm_decision(pd), pd

        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_invoke)

        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=self.SENTINEL)
        node(_make_fake_state())

        assert self.SENTINEL in captured["prompt"], (
            "Digest text must appear in the prompt when rules_digest is provided"
        )

    def test_digest_appears_before_portfolio_manager_header(self, monkeypatch):
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

        captured = {}

        def fake_invoke(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
            captured["prompt"] = prompt
            pd = PortfolioDecision(
                rating=PortfolioRating.HOLD,
                conviction="low",
                executive_summary="Hold.",
                investment_thesis="Balanced.",
            )
            from tradingagents.agents.schemas import render_pm_decision
            return render_pm_decision(pd), pd

        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_invoke)

        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=self.SENTINEL)
        node(_make_fake_state())

        prompt = captured["prompt"]
        sentinel_pos = prompt.index(self.SENTINEL)
        pm_header_pos = prompt.index("As the Portfolio Manager")
        assert sentinel_pos < pm_header_pos, (
            "Digest must appear BEFORE 'As the Portfolio Manager' in the prompt"
        )

    def test_separator_between_digest_and_prompt(self, monkeypatch):
        """The separator '---' must appear between the digest and the original prompt."""
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

        captured = {}

        def fake_invoke(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
            captured["prompt"] = prompt
            pd = PortfolioDecision(
                rating=PortfolioRating.HOLD,
                conviction="low",
                executive_summary="Hold.",
                investment_thesis="Balanced.",
            )
            from tradingagents.agents.schemas import render_pm_decision
            return render_pm_decision(pd), pd

        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_invoke)

        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=self.SENTINEL)
        node(_make_fake_state())

        prompt = captured["prompt"]
        # Separator must exist somewhere between the sentinel and the PM header
        sentinel_pos = prompt.index(self.SENTINEL)
        pm_pos = prompt.index("As the Portfolio Manager")
        between = prompt[sentinel_pos:pm_pos]
        assert "---" in between, (
            "A '---' separator must separate the digest from the original prompt"
        )


class TestPMNodeOutputKeys:
    """Node must return dict with the required keys in both digest=None and digest=value cases."""

    _REQUIRED_KEYS = {"risk_debate_state", "final_trade_decision", "portfolio_decision"}

    def _run_node_with_digest(self, monkeypatch, digest_value):
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

        def fake_invoke(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
            pd = PortfolioDecision(
                rating=PortfolioRating.BUY,
                conviction="high",
                executive_summary="Buy strong.",
                investment_thesis="Very bullish.",
            )
            from tradingagents.agents.schemas import render_pm_decision
            return render_pm_decision(pd), pd

        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_invoke)

        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=digest_value)
        return node(_make_fake_state())

    def test_required_keys_present_no_digest(self, monkeypatch):
        result = self._run_node_with_digest(monkeypatch, None)
        for key in self._REQUIRED_KEYS:
            assert key in result, f"Missing key '{key}' in node output (digest=None)"

    def test_required_keys_present_with_digest(self, monkeypatch):
        result = self._run_node_with_digest(monkeypatch, "SOME-DIGEST")
        for key in self._REQUIRED_KEYS:
            assert key in result, f"Missing key '{key}' in node output (with digest)"

    def test_portfolio_decision_is_not_none_on_structured_path(self, monkeypatch):
        result = self._run_node_with_digest(monkeypatch, None)
        # The stub returns a valid PortfolioDecision, so portfolio_decision must not be None
        assert result["portfolio_decision"] is not None

    def test_final_trade_decision_is_string(self, monkeypatch):
        result = self._run_node_with_digest(monkeypatch, None)
        assert isinstance(result["final_trade_decision"], str)
        assert len(result["final_trade_decision"]) > 0


# ---------------------------------------------------------------------------
# FIX-G (a): Golden-baseline characterization test
# ---------------------------------------------------------------------------

# The EXPECTED default PM prompt, built from the known fake-state inputs used
# by _make_fake_state(). This is a characterization test: if a future change
# reorders or rewords the PM prompt body, this test will fail loudly (rather
# than silently, which would hide a regression in the byte-identical invariant).
#
# To regenerate: run the node with rules_digest=None and print captured["prompt"].
_EXPECTED_DEFAULT_PROMPT = (
    "As the Portfolio Manager, synthesize the risk analysts' debate and deliver the "
    "final trading decision.\n"
    "\n"
    "The instrument to analyze is `TCS.NS`.\n"
    "\n"
    "---\n"
    "\n"
    "**Rating Scale** (use exactly one):\n"
    "- **Buy**: Strong conviction to enter or add to position\n"
    "- **Overweight**: Favorable outlook, gradually increase exposure\n"
    "- **Hold**: Maintain current position, no action needed\n"
    "- **Underweight**: Reduce exposure, take partial profits\n"
    "- **Sell**: Exit position or avoid entry\n"
    "\n"
    "**Context:**\n"
    "- Research Manager's investment plan: **Moderate bullish outlook**\n"
    "- Trader's transaction proposal: **Buy 100 shares**\n"
    "\n"
    "**Risk Analysts Debate History:**\n"
    "Bull: positive. Bear: negative.\n"
    "\n"
    "---\n"
    "\n"
    "Be decisive and ground every conclusion in specific evidence from the analysts."
)


class TestGoldenBaselinePrompt:
    """Characterization: the default prompt (rules_digest=None) must be byte-identical.

    FIX-G (a): a future reorder/reword of the PM prompt body must fail loudly here,
    not silently allow a broken byte-identical invariant.
    """

    def test_default_prompt_matches_golden_baseline(self, monkeypatch):
        """Captured prompt with digest=None must equal the golden baseline exactly."""
        from tradingagents.agents.managers import portfolio_manager as pm_module
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

        captured = {}

        def fake_invoke(structured_llm, plain_llm, prompt, render, agent_name, **kwargs):
            captured["prompt"] = prompt
            pd = PortfolioDecision(
                rating=PortfolioRating.HOLD,
                conviction="medium",
                executive_summary="Hold for now.",
                investment_thesis="Balanced debate.",
            )
            from tradingagents.agents.schemas import render_pm_decision
            return render_pm_decision(pd), pd

        monkeypatch.setattr(pm_module, "invoke_structured_or_freetext", fake_invoke)

        node = pm_module.create_portfolio_manager(_FakeLLM(), rules_digest=None)
        node(_make_fake_state())

        assert captured["prompt"] == _EXPECTED_DEFAULT_PROMPT, (
            "The default PM prompt has changed from the golden baseline. "
            "If this is intentional, update _EXPECTED_DEFAULT_PROMPT. "
            "If not, this is a regression in the byte-identical invariant.\n"
            f"Got:\n{captured['prompt']!r}\n\n"
            f"Expected:\n{_EXPECTED_DEFAULT_PROMPT!r}"
        )
