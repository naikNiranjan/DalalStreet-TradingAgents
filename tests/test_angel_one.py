"""Tests for the Angel One (SmartAPI) prices/indicators vendor (Phase 2C). Offline.

No network: SmartConnect is never imported here. We exercise the pure parsing/format
helpers, the no-network fast-fail guards (non-India suffix, missing creds), and the
end-to-end orchestration with a fake client injected via ``_get_client``.
"""

from unittest.mock import patch

import pandas as pd
import pytest

from tradingagents.dataflows import angel_one as ao
from tradingagents.dataflows.angel_one import AngelConfigError
from tradingagents.dataflows.interface import VENDOR_METHODS
from tradingagents.dataflows.symbol_utils import NoMarketDataError


# A small, deterministic candle response (IST timestamps, daily bars).
def _candle_resp(rows):
    return {"status": True, "message": "SUCCESS", "data": rows}


_ROWS = [
    ["2026-06-01T00:00:00+05:30", 100.0, 105.0, 99.0, 104.0, 1000],
    ["2026-06-02T00:00:00+05:30", 104.0, 106.0, 103.0, 105.5, 1200],
    ["2026-06-03T00:00:00+05:30", 105.5, 108.0, 105.0, 107.25, 900],
    ["2026-06-04T00:00:00+05:30", 107.0, 109.0, 106.0, 108.0, 1100],
]


class _FakeClient:
    """Minimal SmartConnect stand-in for orchestration tests."""

    def __init__(self, rows=_ROWS, token="2885"):
        self._rows = rows
        self._token = token
        self.candle_calls = []

    def searchScrip(self, exchange, searchscrip):
        return {"status": True, "data": [
            {"exchange": exchange, "tradingsymbol": f"{searchscrip}-EQ", "symboltoken": self._token},
            {"exchange": exchange, "tradingsymbol": f"{searchscrip}25JUNFUT", "symboltoken": "999"},
        ]}

    def getCandleData(self, params):
        self.candle_calls.append(params)
        return _candle_resp(self._rows)


# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestExchangeResolution:
    def test_ns_suffix_maps_to_nse(self):
        assert ao._exchange_for("RELIANCE.NS") == ("NSE", "RELIANCE")

    def test_bo_suffix_maps_to_bse(self):
        assert ao._exchange_for("TCS.BO") == ("BSE", "TCS")

    def test_non_india_raises_no_data_without_network(self):
        for sym in ("AAPL", "SPY", "^NSEI", "XAUUSD"):
            with pytest.raises(NoMarketDataError):
                ao._exchange_for(sym)


@pytest.mark.unit
class TestTokenPick:
    def test_prefers_eq_over_derivatives(self):
        data = [
            {"tradingsymbol": "RELIANCE25JUNFUT", "symboltoken": "999"},
            {"tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885"},
        ]
        assert ao._pick_token(data, "RELIANCE") == "2885"

    def test_bse_bare_symbol_match(self):
        data = [{"tradingsymbol": "TCS", "symboltoken": "11536"}]
        assert ao._pick_token(data, "TCS") == "11536"

    def test_no_match_returns_none(self):
        assert ao._pick_token([{"tradingsymbol": "OTHER-EQ", "symboltoken": "1"}], "RELIANCE") is None
        assert ao._pick_token([], "RELIANCE") is None


@pytest.mark.unit
class TestParseCandles:
    def test_parses_rows_and_uses_local_date(self):
        df = ao._parse_candles(_candle_resp(_ROWS))
        assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
        assert len(df) == 4
        # local IST date preserved (no UTC shift to 2026-05-31)
        assert df["Date"].iloc[0] == pd.Timestamp("2026-06-01")
        assert df["Close"].iloc[-1] == 108.0

    def test_empty_or_failed_response_returns_empty(self):
        assert ao._parse_candles({"status": False, "data": []}).empty
        assert ao._parse_candles({}).empty
        assert ao._parse_candles(None).empty


@pytest.mark.unit
class TestCredsGuard:
    def test_blank_creds_raise_config_error(self, monkeypatch):
        for k in ("ANGELONE_API_KEY", "ANGELONE_CLIENT_CODE", "ANGELONE_PIN", "ANGELONE_TOTP_SECRET"):
            monkeypatch.setenv(k, "")
        with pytest.raises(AngelConfigError):
            ao._creds()

    def test_placeholder_counts_as_missing(self, monkeypatch):
        monkeypatch.setenv("ANGELONE_API_KEY", "placeholder")
        monkeypatch.setenv("ANGELONE_CLIENT_CODE", "C1")
        monkeypatch.setenv("ANGELONE_PIN", "1234")
        monkeypatch.setenv("ANGELONE_TOTP_SECRET", "SEED")
        with pytest.raises(AngelConfigError):
            ao._creds()

    def test_all_present_returns_dict(self, monkeypatch):
        monkeypatch.setenv("ANGELONE_API_KEY", "k")
        monkeypatch.setenv("ANGELONE_CLIENT_CODE", "C1")
        monkeypatch.setenv("ANGELONE_PIN", "1234")
        monkeypatch.setenv("ANGELONE_TOTP_SECRET", "SEED")
        assert ao._creds()["ANGELONE_API_KEY"] == "k"


@pytest.mark.unit
class TestGetStockDataAngel:
    def test_non_india_symbol_skips_without_client(self):
        # Must not even construct a client for a US ticker.
        with patch.object(ao, "_get_client", side_effect=AssertionError("should not login")):
            with pytest.raises(NoMarketDataError):
                ao.get_stock_data_angel("AAPL", "2026-06-01", "2026-06-05")

    def test_returns_csv_with_exclusive_end(self):
        fake = _FakeClient()
        with patch.object(ao, "_get_client", return_value=fake):
            out = ao.get_stock_data_angel("RELIANCE.NS", "2026-06-01", "2026-06-04")
        assert "Angel One" in out
        assert "RELIANCE.NS" in out
        # end exclusive -> 2026-06-04 row dropped, three rows remain
        assert "2026-06-01" in out and "2026-06-03" in out
        assert "2026-06-04" not in out.split("\n\n", 1)[1]  # not in the CSV body
        assert "# Total records: 3" in out

    def test_fromdate_brackets_midnight_to_include_boundary_day(self):
        # Daily bars are stamped 00:00 IST; a 09:15 fromdate would drop the first
        # requested trading day. Assert the window starts at 00:00 (regression).
        fake = _FakeClient()
        with patch.object(ao, "_get_client", return_value=fake):
            ao.get_stock_data_angel("RELIANCE.NS", "2026-06-01", "2026-06-04")
        params = fake.candle_calls[0]
        assert params["fromdate"].endswith("00:00")
        assert params["todate"].endswith("23:59")

    def test_empty_window_raises_for_fallback(self):
        fake = _FakeClient(rows=[])
        with patch.object(ao, "_get_client", return_value=fake):
            with pytest.raises(NoMarketDataError):
                ao.get_stock_data_angel("RELIANCE.NS", "2026-06-01", "2026-06-04")

    def test_unresolvable_symbol_raises(self):
        class NoScrip(_FakeClient):
            def searchScrip(self, exchange, searchscrip):
                return {"status": True, "data": []}
        with patch.object(ao, "_get_client", return_value=NoScrip()):
            with pytest.raises(NoMarketDataError):
                ao.get_stock_data_angel("RELIANCE.NS", "2026-06-01", "2026-06-04")


@pytest.mark.unit
class TestGetIndicatorsAngel:
    def test_unsupported_indicator_raises(self):
        with pytest.raises(ValueError):
            ao.get_indicators_angel("RELIANCE.NS", "not_a_real_indicator", "2026-06-04", 5)

    def test_non_india_symbol_skips_without_client(self):
        with patch.object(ao, "_get_client", side_effect=AssertionError("should not login")):
            with pytest.raises(NoMarketDataError):
                ao.get_indicators_angel("AAPL", "rsi", "2026-06-04", 5)

    def test_empty_candles_raise_no_data_not_keyerror(self):
        # An empty candle response has no columns; the adapter must raise the
        # clean fallback error, not KeyError('Date') while filtering.
        fake = _FakeClient(rows=[])
        with patch.object(ao, "_get_client", return_value=fake):
            with pytest.raises(NoMarketDataError):
                ao.get_indicators_angel("RELIANCE.NS", "rsi", "2026-06-04", 5)

    def test_emits_window_and_description(self):
        fake = _FakeClient()
        with patch.object(ao, "_get_client", return_value=fake):
            out = ao.get_indicators_angel("RELIANCE.NS", "close_50_sma", "2026-06-04", 3)
        assert "close_50_sma values from 2026-06-01 to 2026-06-04" in out
        assert "2026-06-04:" in out
        assert "50 SMA" in out  # description appended
        # look-ahead: never requests/﻿emits a date past curr_date
        assert "2026-06-05" not in out


@pytest.mark.unit
class TestVendorWiring:
    def test_angelone_registered_for_prices_and_indicators(self):
        assert "angelone" in VENDOR_METHODS["get_stock_data"]
        assert "angelone" in VENDOR_METHODS["get_indicators"]
