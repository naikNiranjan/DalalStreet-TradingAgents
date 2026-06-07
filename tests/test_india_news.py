"""Tests for the India RSS news vendor (Phase 2A). All offline (mocked feeds)."""

from unittest.mock import patch

import pytest

from tradingagents.dataflows import india_news
from tradingagents.dataflows.interface import route_to_vendor, VENDOR_METHODS
from tradingagents.dataflows.config import set_config
from tradingagents.default_config import DEFAULT_CONFIG


_SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>RBI holds repo rate at 5.25%</title>
    <description>MPC keeps stance neutral amid easing inflation</description>
    <link>https://example.com/rbi</link>
    <pubDate>Wed, 03 Jun 2026 10:00:00 +0530</pubDate>
  </item>
  <item>
    <title>Reliance Industries hits new high</title>
    <description>RELIANCE gains on refining margins</description>
    <link>https://example.com/ril</link>
    <pubDate>Tue, 02 Jun 2026 09:00:00 +0530</pubDate>
  </item>
</channel></rss>"""


@pytest.mark.unit
class TestParseRss:
    def test_parses_items(self):
        items = india_news._parse_rss(_SAMPLE_RSS)
        assert len(items) == 2
        first = items[0]
        assert first["title"] == "RBI holds repo rate at 5.25%"
        assert "neutral" in first["summary"]
        assert first["link"] == "https://example.com/rbi"
        assert first["pub_date"].year == 2026 and first["pub_date"].month == 6

    def test_empty_xml_yields_nothing(self):
        assert india_news._parse_rss("<rss><channel></channel></rss>") == []


@pytest.mark.unit
class TestGlobalNewsIndia:
    def test_formats_and_filters_window(self):
        future = """<rss><channel><item>
          <title>Future leak</title><description>x</description>
          <link>u</link><pubDate>Wed, 10 Jun 2026 10:00:00 +0530</pubDate>
        </item></channel></rss>"""
        # Every feed returns the same combined list; dedup collapses repeats, so
        # this is independent of how many feeds _MARKET_FEEDS has.
        combined = india_news._parse_rss(_SAMPLE_RSS) + india_news._parse_rss(future)
        with patch.object(india_news, "_fetch_rss", return_value=combined):
            out = india_news.get_global_news_india("2026-06-05", look_back_days=7, limit=10)
        assert "India Market News" in out
        assert "RBI holds repo rate" in out
        assert "Future leak" not in out          # look-ahead guard

    def test_all_feeds_unreachable_raises_for_fallback(self):
        with patch.object(india_news, "_fetch_rss", return_value=None):
            with pytest.raises(RuntimeError, match="unreachable"):
                india_news.get_global_news_india("2026-06-05", look_back_days=7, limit=10)

    def test_reachable_but_empty_returns_message(self):
        with patch.object(india_news, "_fetch_rss", return_value=[]):
            out = india_news.get_global_news_india("2026-06-05", look_back_days=7, limit=10)
        assert "No India market news" in out

    def test_undated_articles_dropped_for_historical_date(self):
        # An RSS item with no pubDate must NOT leak into a historical backtest
        # window (it could be a current headline). drop_undated=True drops it;
        # the default auto-detects historical via _is_historical.
        undated = [{"title": "Undated headline", "summary": "x",
                    "link": "u", "pub_date": None}]
        with patch.object(india_news, "_fetch_rss", return_value=undated):
            hist = india_news.get_global_news_india(
                "2020-01-15", look_back_days=7, limit=10, drop_undated=True)
            live = india_news.get_global_news_india(
                "2020-01-15", look_back_days=7, limit=10, drop_undated=False)
        assert "Undated headline" not in hist   # dropped for backtest fidelity
        assert "Undated headline" in live        # kept when explicitly allowed

    def test_is_historical_helper(self):
        from datetime import datetime, timedelta
        past = datetime.now() - timedelta(days=30)
        assert india_news._is_historical(past) is True
        assert india_news._is_historical(datetime.now()) is False


@pytest.mark.unit
class TestNewsIndiaTickerFilter:
    def test_filters_by_company_keyword(self):
        with patch.object(india_news, "_fetch_rss",
                           return_value=india_news._parse_rss(_SAMPLE_RSS)):
            out = india_news.get_news_india("RELIANCE.NS", "2026-05-29", "2026-06-05")
        assert "Reliance Industries hits new high" in out
        assert "RBI holds repo rate" not in out   # no RELIANCE keyword


@pytest.mark.unit
class TestVendorWiring:
    def test_india_rss_registered_for_news_methods(self):
        assert "india_rss" in VENDOR_METHODS["get_news"]
        assert "india_rss" in VENDOR_METHODS["get_global_news"]

    def test_get_global_news_routes_to_india_rss_by_default(self):
        # DEFAULT_CONFIG sets tool_vendors get_global_news -> india_rss. The
        # function ref is bound into VENDOR_METHODS at import, so patch the dict.
        set_config(DEFAULT_CONFIG)
        with patch.dict(VENDOR_METHODS["get_global_news"],
                        {"india_rss": lambda *a, **k: "INDIA_MACRO"}):
            result = route_to_vendor("get_global_news", "2026-06-05")
        assert result == "INDIA_MACRO"
