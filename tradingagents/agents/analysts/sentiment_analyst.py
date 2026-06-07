"""Sentiment analyst — multi-source sentiment analysis for a target ticker.

Previously named ``social_media_analyst``. Renamed and redesigned because
the old version had a prompt that demanded social-media analysis but the
only tool available was Yahoo Finance news — which led LLMs to fabricate
Reddit/X/StockTwits content under prompt pressure (verified live).

The agent pre-fetches two complementary data sources before the LLM is
invoked and injects them into the prompt as structured blocks:

  1. News headlines  — India market RSS / Yahoo Finance (institutional framing)
  2. Reddit posts    — Indian-market subs (r/IndianStockMarket, r/IndiaInvestments,
                       r/DalalStreetTalks); configurable via reddit_subreddits

Phase 2: the US-centric StockTwits source was removed (it indexes US cashtags
and adds no signal for NSE/BSE names). Indian retail sentiment is manipulation-
prone — especially for illiquid small-caps — so it is weighed as a soft,
contrarian-aware signal, never as ground truth.

The agent does not use tool-calling; the data is in the prompt from
turn 0. Output uses the structured-output pattern (json_schema for
OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic), falling
back to free-text generation for providers that lack native support, so
the sentiment header (band + score + confidence) is deterministic across
runs and providers instead of free-form per-model prose.

See: https://github.com/TauricResearch/TradingAgents/issues/557
See: https://github.com/TauricResearch/TradingAgents/issues/796
"""

from datetime import datetime, timedelta

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.schemas import SentimentReport, render_sentiment_report
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_news,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.reddit import fetch_reddit_posts


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph.

    Pre-fetches India news + Reddit data, injects them into the
    prompt as structured blocks, and produces a deterministic sentiment
    report via structured output (with a free-text fallback for providers
    that do not support it).
    """
    structured_llm = bind_structured(llm, SentimentReport, "Sentiment Analyst")

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = get_instrument_context_from_state(state)

        # Pre-fetch both sources. Each fetcher degrades gracefully and returns a
        # string (no exceptions surface from here), so the LLM always sees
        # something — either real data or a clear placeholder.
        subreddits = get_config().get("reddit_subreddits") or None
        news_block = get_news.func(ticker, start_date, end_date)
        reddit_block = (
            fetch_reddit_posts(ticker, subreddits=subreddits) if subreddits
            else fetch_reddit_posts(ticker)
        )

        system_message = _build_system_message(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            news_block=news_block,
            reddit_block=reddit_block,
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    "\n{system_message}\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # Format the template into a concrete message list so the structured
        # and free-text paths receive the same input. No bind_tools — the
        # data is already in the prompt.
        formatted_messages = prompt.format_messages(messages=state["messages"])

        report_text = invoke_structured_or_freetext(
            structured_llm,
            llm,
            formatted_messages,
            render_sentiment_report,
            "Sentiment Analyst",
        )

        return {
            "messages": [AIMessage(content=report_text)],
            "sentiment_report": report_text,
        }

    return sentiment_analyst_node


def _build_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    news_block: str,
    reddit_block: str,
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks."""
    return f"""You are a financial market sentiment analyst covering the **Indian market (NSE/BSE)**. Your task is to produce a comprehensive sentiment report for {ticker} covering the period from {start_date} to {end_date}, drawing on two complementary data sources that have already been collected for you.

## Data sources (pre-fetched, in this prompt)

### News headlines — India market RSS / Yahoo Finance, past 7 days
Institutional framing. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

### Reddit posts — Indian-market subreddits (e.g. r/IndianStockMarket, r/IndiaInvestments, r/DalalStreetTalks), past 7 days
Indian retail community discussion. Engagement signal via upvote score and comment count where available. Subreddit character matters (r/IndianStockMarket is high-volume and often momentum-driven; r/IndiaInvestments is more measured / long-term).

<start_of_reddit>
{reddit_block}
<end_of_reddit>

## How to analyze this data (best practices)

1. **Look for cross-source divergences.** If news framing is bearish but Reddit is overwhelmingly bullish, that mismatch is itself a signal — retail may be leaning into a thesis the news flow hasn't caught up to (or chasing while institutions are cautious).

2. **Weight Reddit posts by engagement.** A 400-upvote / 200-comment thread reflects community attention; a 3-upvote post is noise. Read the body excerpts for context — the title alone often misleads.

3. **Treat Indian retail sentiment as manipulation-prone.** Pump-and-dump and coordinated "tips" are common, especially for **illiquid small/mid-caps**. Be sceptical of sudden uniform bullishness on a thin name; flag it as a contrarian risk rather than confirmation.

4. **Distinguish opinion from event.** A news headline ("RBI holds repo rate at 5.25%") is an event; a Reddit post ("loading up on RELIANCE, sureshot") is opinion. Both are inputs but should be weighted differently.

5. **Identify recurring narrative themes.** What topic keeps coming up across sources? That's the dominant narrative driving current sentiment.

6. **Be honest about data limits.** If a source returned only a handful of items or an "<unavailable>"/"<no posts found>" placeholder, the sentiment read is less robust — flag this explicitly in the `confidence` field and the narrative.

7. **Identify catalysts and risks** that emerge across sources — upcoming earnings, RBI/SEBI actions, sector events, results, etc.

8. **Past sentiment is not predictive.** Frame your conclusions as signal for the trader to weigh alongside fundamentals and technicals, not as a price call.

## Output fields

Fill the following fields:

- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. Use Mixed when sources point in clearly different directions; Neutral only when all sources are genuinely silent.
- **overall_score**: A number from 0 (maximally bearish) to 10 (maximally bullish); 5 is neutral. Keep it consistent with overall_band.
- **confidence**: low / medium / high, based on data quality and sample size.
- **narrative**: Full source-by-source breakdown, divergences, dominant narrative themes, catalysts and risks, and a markdown summary table of key sentiment signals (direction, source, supporting evidence).

{get_language_instruction()}"""


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------
def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.

    .. deprecated::
        Import :func:`create_sentiment_analyst` directly instead.
    """
    import warnings
    warnings.warn(
        "create_social_media_analyst is deprecated and will be removed in a "
        "future version. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
