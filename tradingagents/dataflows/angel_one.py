"""Angel One (SmartAPI) market-data adapter — NSE/BSE OHLCV + indicators (Phase 2C).

Angel One is the project's **primary India price source**. This adapter exposes two
vendor functions matching the existing contracts:

  * ``get_stock_data_angel``  -> ``get_stock_data``  (daily OHLCV CSV)
  * ``get_indicators_angel``  -> ``get_indicators``  (stockstats over Angel candles)

Design guarantees (so it can be the default vendor without ever degrading behaviour):

  * **No creds / placeholder creds  -> instant raise, NO network.** Login is only
    attempted when all four secrets are present, so unit tests (which the conftest
    guard nulls out) and CI never hit the live API.
  * **Non-India ticker (no .NS/.BO suffix, or an index like ^NSEI) -> instant
    ``NoMarketDataError``, NO network.** Angel only covers NSE/BSE cash equities, so
    US tickers and indices fall straight through to yfinance via ``route_to_vendor``.
  * **Any live failure raises**, so the router falls back to yfinance automatically.
  * **Look-ahead safe:** OHLCV uses yfinance's exclusive-end convention; indicators
    never emit a value past ``curr_date``.

Auth: SmartConnect API key + client code + login PIN + TOTP (pyotp). The session is
cached module-level and reused across calls within a process (tokens are day-valid).
The ``ANGELONE_*_STATIC_IP`` env vars are Angel app-registration metadata, not used
here — SmartConnect detects the caller IP itself.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Optional

import pandas as pd
from dateutil.relativedelta import relativedelta

from .symbol_utils import NoMarketDataError
from .y_finance import INDICATOR_DESCRIPTIONS

logger = logging.getLogger(__name__)

# SmartAPI candle interval for daily bars. Daily candles are timestamped at
# **00:00 IST**, so the request window must bracket midnight: a fromdate of 09:15
# would exclude the boundary day's 00:00 bar (silently dropping the first trading
# day). We therefore span 00:00 -> 23:59 of the requested calendar dates.
_DAILY_INTERVAL = "ONE_DAY"
_DAY_START = "00:00"
_DAY_END = "23:59"

# Extra calendar days of history fetched before the indicator look-back window so
# long indicators (200 SMA) have enough bars to stabilise at curr_date.
_INDICATOR_WARMUP_DAYS = 420

_REQUIRED_ENV = (
    "ANGELONE_API_KEY",
    "ANGELONE_CLIENT_CODE",
    "ANGELONE_PIN",
    "ANGELONE_TOTP_SECRET",
)

# Cached logged-in SmartConnect client (day-valid session), guarded for safety.
_client = None
_client_lock = threading.Lock()


class AngelConfigError(RuntimeError):
    """Raised when Angel credentials are absent/placeholder — adapter is disabled."""


# ---------------------------------------------------------------------------
# Pure helpers (no network — unit-testable)
# ---------------------------------------------------------------------------

def _exchange_for(ticker: str) -> tuple[str, str]:
    """Map a ticker to ``(exchange, base_symbol)`` for Angel, or refuse it.

    ``RELIANCE.NS`` -> ``("NSE", "RELIANCE")``; ``TCS.BO`` -> ``("BSE", "TCS")``.
    Anything else (US tickers, indices like ``^NSEI``, forex) raises
    ``NoMarketDataError`` so the router hands it to yfinance — with no network call.
    """
    t = ticker.strip().upper()
    if t.endswith(".NS"):
        return "NSE", t[:-3]
    if t.endswith(".BO"):
        return "BSE", t[:-3]
    raise NoMarketDataError(
        ticker, t, "not an NSE/BSE equity (no .NS/.BO suffix) — Angel One skipped"
    )


def _pick_token(search_data: list, base: str) -> Optional[str]:
    """Pick the cash-equity symboltoken from a searchScrip ``data`` list.

    Prefers the exact ``{BASE}-EQ`` cash symbol (NSE), then a bare ``{BASE}``
    match (BSE cash), so we never accidentally select a future/option contract.
    """
    base_u = base.upper()
    if not search_data:
        return None
    for item in search_data:
        if str(item.get("tradingsymbol", "")).upper() == f"{base_u}-EQ":
            return str(item.get("symboltoken"))
    for item in search_data:
        if str(item.get("tradingsymbol", "")).upper() == base_u:
            return str(item.get("symboltoken"))
    return None


def _parse_candles(resp: dict) -> pd.DataFrame:
    """Convert a getCandleData response into a Date/OHLCV DataFrame.

    Angel returns ``data`` rows ``[iso_ts, open, high, low, close, volume]`` with an
    IST (+05:30) timestamp. For daily bars only the calendar date matters, so we take
    the local date directly (slicing the ISO string) — avoiding any UTC shift that
    would move a 00:00+05:30 bar to the previous day. Returns an empty frame when the
    response carries no usable data.
    """
    if not resp or not resp.get("status") or not resp.get("data"):
        return pd.DataFrame()
    df = pd.DataFrame(
        resp["data"], columns=["Date", "Open", "High", "Low", "Close", "Volume"]
    )
    df["Date"] = pd.to_datetime(df["Date"].astype(str).str.slice(0, 10))
    for col in ("Open", "High", "Low", "Close", "Volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Close"]).reset_index(drop=True)
    return df


def _format_ohlcv_csv(df: pd.DataFrame, label: str, start_date: str, end_date: str) -> str:
    """Render an OHLCV frame as a CSV string mirroring the yfinance vendor's output."""
    out = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        out[col] = out[col].round(2)
    out["Volume"] = out["Volume"].astype("Int64")
    out["Date"] = out["Date"].dt.strftime("%Y-%m-%d")
    csv_string = out.to_csv(index=False)
    header = f"# Stock data for {label} from {start_date} to {end_date} (Angel One / SmartAPI)\n"
    header += f"# Total records: {len(out)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    return header + csv_string


# ---------------------------------------------------------------------------
# Session / network
# ---------------------------------------------------------------------------

def _creds() -> dict:
    """Return the four required secrets, or raise AngelConfigError if any is blank.

    A blank/placeholder value (as the test conftest sets) counts as "not configured"
    so the adapter disables itself with no network attempt.
    """
    vals = {k: (os.environ.get(k) or "").strip() for k in _REQUIRED_ENV}
    missing = [k for k, v in vals.items() if not v or v.lower() == "placeholder"]
    if missing:
        raise AngelConfigError(
            f"Angel One disabled — missing/placeholder creds: {', '.join(missing)}"
        )
    return vals


def _login():
    """Create and log in a fresh SmartConnect client using env creds + TOTP."""
    creds = _creds()  # raises (no network) when not configured
    import pyotp
    from SmartApi import SmartConnect

    client = SmartConnect(api_key=creds["ANGELONE_API_KEY"])
    totp = pyotp.TOTP(creds["ANGELONE_TOTP_SECRET"]).now()
    session = client.generateSession(
        creds["ANGELONE_CLIENT_CODE"], creds["ANGELONE_PIN"], totp
    )
    if not session or not session.get("status"):
        msg = (session or {}).get("message", "unknown error")
        raise RuntimeError(f"Angel One login failed: {msg}")
    # Don't log the client code — it's account-identifying broker auth metadata.
    logger.info("Angel One session established")
    return client


def _get_client(force_new: bool = False):
    """Return a cached logged-in SmartConnect client, logging in on first use."""
    global _client
    with _client_lock:
        if _client is None or force_new:
            _client = _login()
        return _client


def _resolve_token(client, base: str, exchange: str) -> str:
    """Resolve the cash-equity symboltoken for ``base`` on ``exchange`` via searchScrip."""
    resp = client.searchScrip(exchange, base)
    data = (resp or {}).get("data") or []
    token = _pick_token(data, base)
    if not token:
        raise NoMarketDataError(
            f"{base}.{exchange}", base, f"no {exchange} cash-equity scrip for '{base}'"
        )
    return token


def _fetch_candles(client, exchange: str, token: str, from_date: str, to_date: str) -> pd.DataFrame:
    """Fetch daily candles in [from_date, to_date] (inclusive) as a DataFrame."""
    params = {
        "exchange": exchange,
        "symboltoken": token,
        "interval": _DAILY_INTERVAL,
        "fromdate": f"{from_date} {_DAY_START}",
        "todate": f"{to_date} {_DAY_END}",
    }
    resp = client.getCandleData(params)
    return _parse_candles(resp)


# ---------------------------------------------------------------------------
# Vendor entry points
# ---------------------------------------------------------------------------

def get_stock_data_angel(symbol: str, start_date: str, end_date: str) -> str:
    """Daily OHLCV for an NSE/BSE equity. Mirrors ``get_YFin_data_online``.

    End date is treated as **exclusive** (yfinance convention) so the two vendors are
    interchangeable. Raises ``NoMarketDataError`` for non-India symbols (no network) or
    when no rows fall in the window, and ``AngelConfigError``/``RuntimeError`` on
    config/login failure — all of which let ``route_to_vendor`` fall back to yfinance.
    """
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    exchange, base = _exchange_for(symbol)  # raises for non-India, no network
    client = _get_client()
    token = _resolve_token(client, base, exchange)

    df = _fetch_candles(client, exchange, token, start_date, end_date)
    if not df.empty:
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        df = df[(df["Date"] >= start_dt) & (df["Date"] < end_dt)]  # exclusive end

    if df.empty:
        raise NoMarketDataError(
            symbol, base, f"no Angel One candles between {start_date} and {end_date}"
        )

    label = f"{base}.{('NS' if exchange == 'NSE' else 'BO')}"
    return _format_ohlcv_csv(df.reset_index(drop=True), label, start_date, end_date)


def get_indicators_angel(
    symbol: str, indicator: str, curr_date: str, look_back_days: int
) -> str:
    """Stockstats indicator window computed over Angel One candles.

    Output format matches ``get_stock_stats_indicators_window`` so reports read
    identically regardless of vendor. ``curr_date`` is inclusive (its value is the
    headline number); nothing past it is ever emitted (look-ahead guard).
    """
    if indicator not in INDICATOR_DESCRIPTIONS:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: "
            f"{list(INDICATOR_DESCRIPTIONS.keys())}"
        )

    exchange, base = _exchange_for(symbol)  # raises for non-India, no network
    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    window_start = curr_dt - relativedelta(days=look_back_days)
    fetch_start = curr_dt - relativedelta(days=look_back_days + _INDICATOR_WARMUP_DAYS)

    client = _get_client()
    token = _resolve_token(client, base, exchange)
    df = _fetch_candles(
        client, exchange, token,
        fetch_start.strftime("%Y-%m-%d"), curr_date,
    )
    # Guard before touching df["Date"]: an empty response has no columns, so
    # filtering would raise KeyError instead of the clean fallback error.
    if df.empty or "Date" not in df.columns:
        raise NoMarketDataError(
            symbol, base, f"no Angel One candles up to {curr_date} for indicators"
        )
    # Look-ahead guard: never compute on bars after curr_date.
    df = df[df["Date"] <= curr_dt]
    if df.empty:
        raise NoMarketDataError(
            symbol, base, f"no Angel One candles up to {curr_date} for indicators"
        )

    from stockstats import wrap

    sdf = wrap(df.copy())
    sdf[indicator]  # trigger calculation
    by_date = {
        d.strftime("%Y-%m-%d"): ("N/A" if pd.isna(v) else str(v))
        for d, v in zip(df["Date"], sdf[indicator])
    }

    lines = []
    cursor = curr_dt
    while cursor >= window_start:
        ds = cursor.strftime("%Y-%m-%d")
        lines.append(
            f"{ds}: {by_date.get(ds, 'N/A: Not a trading day (weekend or holiday)')}"
        )
        cursor -= relativedelta(days=1)

    return (
        f"## {indicator} values from {window_start.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
        + "\n".join(lines)
        + "\n\n"
        + INDICATOR_DESCRIPTIONS.get(indicator, "No description available.")
    )
