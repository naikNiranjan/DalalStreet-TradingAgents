"""Tests for the azure-foundry provider: routing, capabilities, role wiring.

These are all offline (no network / no real keys) — they exercise the routing
logic, the capability table fixes, and the role->LLM fallback wiring.
"""

from unittest.mock import MagicMock, patch

import pytest

from tradingagents.llm_clients.factory import create_llm_client
from tradingagents.llm_clients.azure_foundry_client import AzureFoundryClient
from tradingagents.llm_clients.capabilities import get_capabilities
from tradingagents.graph.setup import GraphSetup


@pytest.mark.unit
class TestFactoryRegistration:
    def test_factory_returns_foundry_client(self):
        client = create_llm_client(provider="azure-foundry", model="grok-4.3")
        assert isinstance(client, AzureFoundryClient)
        assert client.provider == "azure-foundry"

    def test_unknown_model_is_accepted(self):
        # Foundry serves a large catalog; validation must not reject deployed ids.
        assert AzureFoundryClient("anything-new").validate_model() is True


@pytest.mark.unit
class TestRouting:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("claude-sonnet-4-6", "anthropic"),
            ("claude-opus-4-8", "anthropic"),
            ("gpt-5.5", "azure_openai"),
            ("o3-mini", "azure_openai"),
            ("DeepSeek-V4-Pro", "openai_compatible"),
            ("DeepSeek-V4-Flash", "openai_compatible"),
            ("grok-4.3", "openai_compatible"),
            ("Llama-3.3-70B-Instruct", "openai_compatible"),
        ],
    )
    def test_route(self, model, expected):
        assert AzureFoundryClient(model)._route() == expected

    def test_missing_endpoint_raises_clear_error(self, monkeypatch):
        monkeypatch.delenv("AZURE_FOUNDRY_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_FOUNDRY_API_KEY", raising=False)
        with pytest.raises(ValueError, match="AZURE_FOUNDRY_OPENAI_ENDPOINT"):
            AzureFoundryClient("DeepSeek-V4-Pro").get_llm()

    def test_gpt_role_preflights_azure_openai_env(self, monkeypatch):
        # GPT models route to the separate Azure OpenAI resource; missing creds
        # must fail at build time with a clear message (not late on first call).
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
            AzureFoundryClient("gpt-5.5").get_llm()


@pytest.mark.unit
class TestCapabilities:
    def test_capitalized_deepseek_v4_suppresses_tool_choice(self):
        # The Azure deployment id is capitalized; it must NOT fall through to
        # _DEFAULT (which would send tool_choice and 400 on DeepSeek V4).
        for model in ("DeepSeek-V4-Pro", "DeepSeek-V4-Flash",
                      "DeepSeek-V4-Pro-2026-04-23"):
            caps = get_capabilities(model)
            assert caps.supports_tool_choice is False
            assert caps.preferred_structured_method == "function_calling"
            assert caps.requires_reasoning_content_roundtrip is True

    def test_lowercase_deepseek_still_works(self):
        assert get_capabilities("deepseek-v4-pro").supports_tool_choice is False

    def test_grok_supports_tool_choice_and_schema(self):
        caps = get_capabilities("grok-4.3")
        assert caps.supports_tool_choice is True
        assert caps.supports_json_schema is True


@pytest.mark.unit
class TestRoleRouting:
    def _setup(self, role_llms):
        return GraphSetup(
            quick_thinking_llm="QUICK",
            deep_thinking_llm="DEEP",
            tool_nodes={},
            conditional_logic=None,
            role_llms=role_llms,
        )

    def test_role_specific_llm_used(self):
        gs = self._setup({"portfolio_manager": "PM_LLM", "market": "MKT_LLM"})
        assert gs._llm_for("portfolio_manager", "deep") == "PM_LLM"
        assert gs._llm_for("market", "quick") == "MKT_LLM"

    def test_unmapped_role_falls_back_to_tier(self):
        gs = self._setup({"portfolio_manager": "PM_LLM"})
        # trader unmapped -> quick tier; research_manager unmapped -> deep tier
        assert gs._llm_for("trader", "quick") == "QUICK"
        assert gs._llm_for("research_manager", "deep") == "DEEP"

    def test_no_role_map_is_pure_tier_behavior(self):
        gs = self._setup(None)
        assert gs._llm_for("market", "quick") == "QUICK"
        assert gs._llm_for("portfolio_manager", "deep") == "DEEP"


@pytest.mark.unit
class TestBuildLLMsRouting:
    def _graph(self, config):
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        g = TradingAgentsGraph.__new__(TradingAgentsGraph)
        g.config = config
        return g

    def test_foundry_builds_role_map_with_caching(self):
        cfg = {
            "llm_provider": "azure-foundry",
            "deep_think_llm": "gpt-5.5",
            "quick_think_llm": "DeepSeek-V4-Flash",
            "backend_url": None,
            "model_roles": {
                "portfolio_manager": "gpt-5.5",   # shares with deep -> built once
                "market": "grok-4.3",
            },
        }
        calls = []

        def fake_create(provider, model, base_url=None, **kw):
            calls.append(model)
            m = MagicMock()
            m.get_llm.return_value = f"llm:{model}"
            return m

        with patch("tradingagents.graph.trading_graph.create_llm_client", fake_create):
            deep, quick, roles = self._graph(cfg)._build_llms({})

        assert deep == "llm:gpt-5.5"
        assert quick == "llm:DeepSeek-V4-Flash"
        assert roles["portfolio_manager"] == "llm:gpt-5.5"
        assert roles["market"] == "llm:grok-4.3"
        # gpt-5.5 used for both deep and PM but built only once (cache by id).
        assert calls.count("gpt-5.5") == 1

    def test_azure_foundry_uses_foundry_tier_fallbacks_by_default(self):
        # A plain llm_provider=azure-foundry run (copying DEFAULT_CONFIG) must
        # fall back to a DEPLOYED Foundry model for the quick tier, not the
        # global gpt-5.4-mini (which only exists on the openai provider).
        from tradingagents.default_config import DEFAULT_CONFIG

        cfg = DEFAULT_CONFIG.copy()
        cfg["llm_provider"] = "azure-foundry"

        def fake_create(provider, model, base_url=None, **kw):
            m = MagicMock()
            m.get_llm.return_value = f"llm:{model}"
            return m

        with patch("tradingagents.graph.trading_graph.create_llm_client", fake_create):
            deep, quick, _roles = self._graph(cfg)._build_llms({})

        assert quick == "llm:DeepSeek-V4-Flash"   # not gpt-5.4-mini
        assert deep == "llm:gpt-5.5"

    def test_non_foundry_provider_has_empty_role_map(self):
        cfg = {
            "llm_provider": "openai",
            "deep_think_llm": "gpt-5.5",
            "quick_think_llm": "gpt-5.4-mini",
            "backend_url": None,
            "model_roles": {"portfolio_manager": "gpt-5.5"},  # must be ignored
        }

        def fake_create(provider, model, base_url=None, **kw):
            m = MagicMock()
            m.get_llm.return_value = f"llm:{model}"
            return m

        with patch("tradingagents.graph.trading_graph.create_llm_client", fake_create):
            deep, quick, roles = self._graph(cfg)._build_llms({})

        assert deep == "llm:gpt-5.5"
        assert quick == "llm:gpt-5.4-mini"
        assert roles == {}
