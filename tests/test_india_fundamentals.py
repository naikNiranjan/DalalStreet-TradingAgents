"""Tests for the India fundamentals enrichment vendor (Phase 2D). Offline."""

from unittest.mock import patch

import pytest

from tradingagents.dataflows import india_fundamentals as ifund
from tradingagents.dataflows.interface import VENDOR_METHODS


_SAMPLE_HTML = """<html><body>
  <h1>Reliance Industries Ltd</h1>
  <ul id="top-ratios">
    <li><span class="name">Market Cap</span><span class="value">₹ 17,47,050 Cr.</span></li>
    <li><span class="name">Current Price</span><span class="value">₹ 1,291</span></li>
    <li><span class="name">Stock P/E</span><span class="value">22.4</span></li>
  </ul>
  <div class="pros"><ul><li>Healthy cash flow generation</li></ul></div>
  <div class="cons"><ul><li>High debt levels</li><li>Low promoter holding</li></ul></div>
</body></html>"""


@pytest.mark.unit
class TestParseScreener:
    def test_parses_name_ratios_pros_cons(self):
        block = ifund._parse_screener(_SAMPLE_HTML)
        assert "Reliance Industries Ltd" in block
        assert "Market Cap: ₹ 17,47,050 Cr." in block
        assert "Stock P/E: 22.4" in block
        assert "Healthy cash flow generation" in block
        assert "High debt levels" in block
        assert "Low promoter holding" in block

    def test_empty_page_returns_none(self):
        assert ifund._parse_screener("<html><body></body></html>") is None


@pytest.mark.unit
class TestGetFundamentalsIndia:
    def test_combines_screener_and_yfinance(self):
        with patch.object(ifund, "_fetch_screener", return_value="SCREENER"), \
             patch.object(ifund, "get_yfinance_fundamentals", return_value="YFIN"):
            out = ifund.get_fundamentals_india("RELIANCE.NS")
        assert "SCREENER" in out and "YFIN" in out

    def test_screener_failure_falls_back_to_yfinance(self):
        with patch.object(ifund, "_fetch_screener", return_value=None), \
             patch.object(ifund, "get_yfinance_fundamentals", return_value="YFIN"):
            out = ifund.get_fundamentals_india("RELIANCE.NS")
        assert out == "YFIN"   # never degrades below yfinance

    def test_yfinance_failure_uses_screener_only(self):
        def boom(*a, **k):
            raise RuntimeError("yf down")
        with patch.object(ifund, "_fetch_screener", return_value="SCREENER"), \
             patch.object(ifund, "get_yfinance_fundamentals", side_effect=boom):
            out = ifund.get_fundamentals_india("RELIANCE.NS")
        assert out == "SCREENER"

    def test_both_empty_raises_for_router_fallback(self):
        def boom(*a, **k):
            raise RuntimeError("yf down")
        with patch.object(ifund, "_fetch_screener", return_value=None), \
             patch.object(ifund, "get_yfinance_fundamentals", side_effect=boom):
            with pytest.raises(RuntimeError):
                ifund.get_fundamentals_india("RELIANCE.NS")

    def test_strips_exchange_suffix_for_symbol(self):
        seen = {}
        def fake_fetch(sym):
            seen["sym"] = sym
            return "SCREENER"
        with patch.object(ifund, "_fetch_screener", side_effect=fake_fetch), \
             patch.object(ifund, "get_yfinance_fundamentals", return_value="YFIN"):
            ifund.get_fundamentals_india("HDFCBANK.NS")
        assert seen["sym"] == "HDFCBANK"


@pytest.mark.unit
class TestVendorWiring:
    def test_india_screener_registered(self):
        assert "india_screener" in VENDOR_METHODS["get_fundamentals"]
