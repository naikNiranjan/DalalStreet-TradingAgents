"""
Tests for B3 — graph wiring and config flag.

All tests are fully offline. The strategy is:
  1. Test that DEFAULT_CONFIG["inject_rules_digest"] is False.
  2. Test that GraphSetup accepts rules_digest and defaults to None.
  3. Test that GraphSetup passes rules_digest to create_portfolio_manager.
  4. Test _load_rules_digest on a TradingAgentsGraph-like shim (the preferred
     offline approach per the spec).

For test 3, if constructing GraphSetup + setup_graph offline proves infeasible
due to deep LangGraph/ToolNode dependencies, we unit-test _load_rules_digest
directly, which is the approach the spec sanctions as equally valid.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# B3.1: DEFAULT_CONFIG["inject_rules_digest"] is False
# ---------------------------------------------------------------------------


class TestDefaultConfigFlag:
    def test_inject_rules_digest_present(self):
        from tradingagents.default_config import DEFAULT_CONFIG

        assert "inject_rules_digest" in DEFAULT_CONFIG, (
            "DEFAULT_CONFIG must have 'inject_rules_digest' key"
        )

    def test_inject_rules_digest_is_false(self):
        from tradingagents.default_config import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["inject_rules_digest"] is False, (
            "inject_rules_digest must default to False (opt-in feature)"
        )


# ---------------------------------------------------------------------------
# B3.2: GraphSetup accepts rules_digest and defaults to None
# ---------------------------------------------------------------------------


class TestGraphSetupSignature:
    def test_init_accepts_rules_digest(self):
        import inspect
        from tradingagents.graph.setup import GraphSetup

        sig = inspect.signature(GraphSetup.__init__)
        assert "rules_digest" in sig.parameters, (
            "GraphSetup.__init__ must accept a rules_digest parameter"
        )

    def test_rules_digest_default_is_none(self):
        import inspect
        from tradingagents.graph.setup import GraphSetup

        sig = inspect.signature(GraphSetup.__init__)
        param = sig.parameters["rules_digest"]
        assert param.default is None, (
            f"rules_digest default must be None, got {param.default!r}"
        )

    def test_graph_setup_stores_rules_digest(self):
        """GraphSetup must store rules_digest as self.rules_digest."""
        from tradingagents.graph.setup import GraphSetup

        # Build minimal stubs — GraphSetup stores them but doesn't call them at __init__
        dummy_llm = MagicMock()
        dummy_llm.with_structured_output.side_effect = NotImplementedError("stub")
        dummy_tool_nodes: Dict[str, Any] = {}
        dummy_conditional = MagicMock()

        gs = GraphSetup(
            quick_thinking_llm=dummy_llm,
            deep_thinking_llm=dummy_llm,
            tool_nodes=dummy_tool_nodes,
            conditional_logic=dummy_conditional,
            rules_digest="TEST-SENTINEL",
        )
        assert gs.rules_digest == "TEST-SENTINEL"

    def test_graph_setup_rules_digest_none_by_default(self):
        from tradingagents.graph.setup import GraphSetup

        dummy_llm = MagicMock()
        dummy_llm.with_structured_output.side_effect = NotImplementedError("stub")
        dummy_conditional = MagicMock()

        gs = GraphSetup(
            quick_thinking_llm=dummy_llm,
            deep_thinking_llm=dummy_llm,
            tool_nodes={},
            conditional_logic=dummy_conditional,
        )
        assert gs.rules_digest is None


# ---------------------------------------------------------------------------
# B3.3: Wiring — create_portfolio_manager receives rules_digest from GraphSetup
# ---------------------------------------------------------------------------


class TestGraphSetupWiring:
    """Monkeypatch create_portfolio_manager and call setup_graph to verify the kwarg.

    FIX-G (c): the test directly captures the rules_digest kwarg via monkeypatch
    and does NOT wrap setup_graph in a bare except. Unexpected errors propagate so
    they are not silently hidden.
    """

    def test_setup_graph_passes_rules_digest_to_pm(self, monkeypatch):
        """GraphSetup.setup_graph must forward self.rules_digest to create_portfolio_manager."""
        import tradingagents.graph.setup as setup_module

        captured = {}

        def fake_create_pm(llm, rules_digest=None):
            captured["rules_digest"] = rules_digest
            # Return a callable (node) so setup_graph can wire it
            return lambda state: {}

        monkeypatch.setattr(setup_module, "create_portfolio_manager", fake_create_pm)

        from tradingagents.graph.setup import GraphSetup

        dummy_llm = MagicMock()
        dummy_llm.with_structured_output.side_effect = NotImplementedError("stub")

        # Build minimal tool nodes and conditional logic stubs so setup_graph can run.
        dummy_tool_node = MagicMock()
        tool_nodes = {
            "market": dummy_tool_node,
        }

        # ConditionalLogic stubs
        cond = MagicMock()
        cond.should_continue_market = lambda state: "market_clear"
        cond.should_continue_debate = lambda state: "Research Manager"
        cond.should_continue_risk_analysis = lambda state: "Portfolio Manager"

        gs = GraphSetup(
            quick_thinking_llm=dummy_llm,
            deep_thinking_llm=dummy_llm,
            tool_nodes=tool_nodes,
            conditional_logic=cond,
            rules_digest="SENTINEL-DIGEST",
        )

        # Call setup_graph — it succeeds with stub LLMs (falls back to free-text).
        # Do NOT swallow exceptions: unexpected errors must propagate and fail loudly.
        gs.setup_graph(selected_analysts=["market"])

        assert "rules_digest" in captured, (
            "create_portfolio_manager was not called by setup_graph"
        )
        assert captured["rules_digest"] == "SENTINEL-DIGEST", (
            f"Expected 'SENTINEL-DIGEST', got {captured['rules_digest']!r}"
        )


# ---------------------------------------------------------------------------
# B3.4 (removed — FIX-G d): The FakeTradingAgentsGraph shim tests that
# reimplemented _load_rules_digest inline have been removed. They were testing
# a copy of the code, not the real implementation. The real-method coverage
# via object.__new__(TradingAgentsGraph) in B3.5 is the honest coverage.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# B3.5: _load_rules_digest in the real TradingAgentsGraph
# ---------------------------------------------------------------------------

class TestTradingGraphLoadRulesDigest:
    """Unit-test _load_rules_digest in the real TradingAgentsGraph class (no full init)."""

    def test_method_exists(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        assert hasattr(TradingAgentsGraph, "_load_rules_digest"), (
            "TradingAgentsGraph must have a _load_rules_digest method"
        )

    def test_returns_none_when_flag_off(self):
        """Test _load_rules_digest directly without building a full TradingAgentsGraph."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        # Create a minimal object that only has what _load_rules_digest needs
        instance = object.__new__(TradingAgentsGraph)
        instance.config = {"inject_rules_digest": False}
        result = instance._load_rules_digest()
        assert result is None

    def test_returns_string_when_flag_on(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        instance = object.__new__(TradingAgentsGraph)
        instance.config = {"inject_rules_digest": True}
        result = instance._load_rules_digest()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_no_static_agent_os_import_in_trading_graph(self):
        """Verify agent_os is not statically imported from trading_graph.

        The layer-direction invariant requires that the spine (tradingagents/)
        never imports agent_os via a static import statement. The feature uses
        importlib.import_module at runtime so the AST walker finds no
        'from agent_os' or 'import agent_os' anywhere in the file.
        """
        import ast
        import re
        from pathlib import Path

        tg_path = Path(__file__).parent.parent.parent / "tradingagents" / "graph" / "trading_graph.py"
        source = tg_path.read_text(encoding="utf-8")

        # Check regex pattern (same as test_layer_direction.py)
        pattern = re.compile(r"^\s*(?:from|import)\s+agent_os(?:\b|\.)", re.MULTILINE)
        assert not pattern.search(source), (
            "trading_graph.py must NOT have a static 'from/import agent_os' statement "
            "(use importlib.import_module to preserve the layer-direction invariant)"
        )

        # Also verify via AST that no ImportFrom or Import node references agent_os
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not (alias.name == "agent_os" or alias.name.startswith("agent_os.")), (
                        f"Static 'import agent_os...' found in trading_graph.py: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not (mod == "agent_os" or mod.startswith("agent_os.")), (
                    f"Static 'from agent_os...' found in trading_graph.py: {mod}"
                )
