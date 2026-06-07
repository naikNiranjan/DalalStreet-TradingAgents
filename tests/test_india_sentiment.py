"""Tests for India sentiment re-pointing (Phase 2B): Reddit subs + suffix strip,
StockTwits removal. All offline."""

from unittest.mock import patch

import pytest

from tradingagents.dataflows import reddit
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.analysts import sentiment_analyst


@pytest.mark.unit
class TestRedditIndia:
    def test_default_subreddits_are_indian(self):
        subs = set(reddit.DEFAULT_SUBREDDITS)
        assert "IndianStockMarket" in subs
        # US subs must be gone.
        assert "wallstreetbets" not in subs and "investing" not in subs

    def test_config_default_reddit_subreddits_indian(self):
        assert DEFAULT_CONFIG["reddit_subreddits"][0] == "IndianStockMarket"

    def test_search_strips_exchange_suffix(self):
        seen = {}

        def fake_fetch(term, sub, limit, timeout):
            seen["term"] = term
            return []

        with patch.object(reddit, "_fetch_subreddit", side_effect=fake_fetch):
            reddit.fetch_reddit_posts("RELIANCE.NS", subreddits=["IndianStockMarket"],
                                      inter_request_delay=0)
        # Reddit users write "RELIANCE", not "RELIANCE.NS".
        assert seen["term"] == "RELIANCE"

    def test_no_posts_uses_base_symbol_in_message(self):
        with patch.object(reddit, "_fetch_subreddit", return_value=[]):
            out = reddit.fetch_reddit_posts("HDFCBANK.BO", subreddits=["IndianStockMarket"],
                                            inter_request_delay=0)
        assert "HDFCBANK" in out and "HDFCBANK.BO" not in out


@pytest.mark.unit
class TestSentimentAnalystIndia:
    def test_stocktwits_removed_and_india_framing(self):
        msg = sentiment_analyst._build_system_message(
            ticker="RELIANCE.NS", start_date="2026-05-29", end_date="2026-06-05",
            news_block="NEWS", reddit_block="REDDIT",
        )
        assert "StockTwits" not in msg
        assert "Indian market" in msg
        assert "manipulation-prone" in msg
        assert "NEWS" in msg and "REDDIT" in msg

    def test_module_no_longer_imports_stocktwits(self):
        import inspect
        src = inspect.getsource(sentiment_analyst)
        assert "fetch_stocktwits_messages" not in src
