# TradingAgents/graph/setup.py

from typing import Any, Dict
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import *
from tradingagents.agents.utils.agent_states import AgentState

from .analyst_execution import build_analyst_execution_plan
from .conditional_logic import ConditionalLogic


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: Dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
        analyst_concurrency_limit: int = 1,
        role_llms: Dict[str, Any] = None,
    ):
        """Initialize with required components.

        ``role_llms`` maps an agent role -> its LLM (populated only for the
        azure-foundry provider). When a role is absent, the agent falls back to
        its tier default: ``deep_thinking_llm`` for managers/PM, otherwise
        ``quick_thinking_llm`` — i.e. exactly the pre-routing behavior.
        """
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic
        self.analyst_concurrency_limit = analyst_concurrency_limit
        self.role_llms = role_llms or {}

    def _llm_for(self, role: str, tier: str) -> Any:
        """Return the LLM for ``role``, falling back to the ``tier`` default.

        ``tier`` is "deep" or "quick" and selects the fallback when ``role`` is
        not in the role map (or no map is configured).
        """
        llm = self.role_llms.get(role)
        if llm is not None:
            return llm
        return self.deep_thinking_llm if tier == "deep" else self.quick_thinking_llm

    def setup_graph(
        self, selected_analysts=["market", "social", "news", "fundamentals"]
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Social media analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
        """
        plan = build_analyst_execution_plan(
            selected_analysts,
            concurrency_limit=self.analyst_concurrency_limit,
        )

        # Each agent uses its role's model (azure-foundry role routing) or the
        # tier default. Role keys here are the contract with config["model_roles"].
        analyst_factories = {
            "market": lambda: create_market_analyst(self._llm_for("market", "quick")),
            "social": lambda: create_sentiment_analyst(self._llm_for("social", "quick")),
            "news": lambda: create_news_analyst(self._llm_for("news", "quick")),
            "fundamentals": lambda: create_fundamentals_analyst(self._llm_for("fundamentals", "quick")),
        }

        # Create researcher and manager nodes
        bull_researcher_node = create_bull_researcher(self._llm_for("bull_researcher", "quick"))
        bear_researcher_node = create_bear_researcher(self._llm_for("bear_researcher", "quick"))
        research_manager_node = create_research_manager(self._llm_for("research_manager", "deep"))
        trader_node = create_trader(self._llm_for("trader", "quick"))

        # Create risk analysis nodes
        aggressive_analyst = create_aggressive_debator(self._llm_for("aggressive_debator", "quick"))
        neutral_analyst = create_neutral_debator(self._llm_for("neutral_debator", "quick"))
        conservative_analyst = create_conservative_debator(self._llm_for("conservative_debator", "quick"))
        portfolio_manager_node = create_portfolio_manager(self._llm_for("portfolio_manager", "deep"))

        # Create workflow
        workflow = StateGraph(AgentState)

        # Add analyst nodes to the graph
        for spec in plan.specs:
            workflow.add_node(spec.agent_node, analyst_factories[spec.key]())
            workflow.add_node(spec.clear_node, create_msg_delete())
            workflow.add_node(spec.tool_node, self.tool_nodes[spec.key])

        # Add other nodes
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        workflow.add_node("Aggressive Analyst", aggressive_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Conservative Analyst", conservative_analyst)
        workflow.add_node("Portfolio Manager", portfolio_manager_node)

        # Define edges
        # Start with the first analyst
        workflow.add_edge(START, plan.specs[0].agent_node)

        # Connect analysts in sequence
        for i, spec in enumerate(plan.specs):
            current_analyst = spec.agent_node
            current_tools = spec.tool_node
            current_clear = spec.clear_node

            # Add conditional edges for current analyst
            workflow.add_conditional_edges(
                current_analyst,
                getattr(self.conditional_logic, f"should_continue_{spec.key}"),
                [current_tools, current_clear],
            )
            workflow.add_edge(current_tools, current_analyst)

            # Connect to next analyst or to Bull Researcher if this is the last analyst
            if i < len(plan.specs) - 1:
                workflow.add_edge(current_clear, plan.specs[i + 1].agent_node)
            else:
                workflow.add_edge(current_clear, "Bull Researcher")

        # Add remaining edges
        workflow.add_conditional_edges(
            "Bull Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bear Researcher": "Bear Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_conditional_edges(
            "Bear Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bull Researcher": "Bull Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        workflow.add_conditional_edges(
            "Aggressive Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Conservative Analyst": "Conservative Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )
        workflow.add_conditional_edges(
            "Conservative Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Neutral Analyst": "Neutral Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )
        workflow.add_conditional_edges(
            "Neutral Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Aggressive Analyst": "Aggressive Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )

        workflow.add_edge("Portfolio Manager", END)

        return workflow
