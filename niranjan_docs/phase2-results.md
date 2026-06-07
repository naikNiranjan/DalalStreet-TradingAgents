# Phase 2 — India Data Layer (progress)

> Branch: `feature/phase2-india-data` (off Phase 1). Incremental, tested slices.
> **Not committed** — awaiting review.

## Done

### 2A — India macro queries + RSS news vendor ✅
- **`global_news_queries`** rewritten from US (Fed/S&P) → **India** (RBI/MPC, Nifty/Sensex,
  FII-DII flows, Budget/SEBI/GST, USD-INR/crude).
- **NEW `dataflows/india_news.py`** — India market news from free RSS feeds (Economic Times
  Markets + Economy, Moneycontrol Business, LiveMint Markets, Hindu BusinessLine). Parsed
  with `parsel` (existing dep — no `feedparser` added). Browser User-Agent required (several
  publishers 403 otherwise; Business Standard 403s even so → omitted).
  - `get_global_news_india` (macro) and `get_news_india` (ticker, keyword-filtered).
  - Look-ahead guard (drops articles after the analysis date — backtest fidelity).
  - Distinguishes "feeds unreachable" (raises → router falls back to yfinance) from
    "no news in window" (clear message).
- **Registered `india_rss` vendor** in `interface.py` for `get_news` + `get_global_news`;
  added to `VENDOR_LIST`. **`get_global_news` defaults to `india_rss`** (tool_vendors), with
  yfinance fallback.
- **Verified live:** all 5 feeds reachable (~210 items); real Indian macro headlines render.
- **Tests:** `tests/test_india_news.py` (8) — parsing, window/look-ahead filter, unreachable
  fallback, keyword filter, vendor wiring.

### 2B — Drop StockTwits, re-point Reddit to India ✅
- **StockTwits removed** from the sentiment analyst (US cashtag index, no NSE/BSE signal).
  `stocktwits.py` left in place (now dead code; flag for refactor-cleaner).
- **Reddit re-pointed** to Indian subs: `IndianStockMarket`, `IndiaInvestments`,
  `DalalStreetTalks` (config key **`reddit_subreddits`**, overridable).
- **Exchange suffix stripped** for Reddit search (`RELIANCE.NS` → search "RELIANCE").
- **Sentiment prompt rewritten** for India: two sources (news + Reddit), explicit
  **manipulation-prone / illiquid-small-cap caveat**, India examples (RBI, RELIANCE).
- **Tests:** `tests/test_india_sentiment.py` (6) — India subs, suffix strip, StockTwits
  removal, India framing.

**Regression after 2A+2B: 347 passed** (was 331 at end of Phase 1), 0 failures.

### Post-review fixes (2026-06-08)
- **🟠 Backtest look-ahead:** undated RSS articles were kept for any date — could leak
  *current* headlines into a *historical* backtest window. Fixed: `_collect(drop_undated)`;
  public funcs auto-drop undated items for historical dates (`_is_historical`, before today)
  and keep them for live runs, with an explicit `drop_undated` override. Tested both paths.
- **🟡 Wording:** removed stale "Business Standard" mentions from `interface.py`,
  `default_config.py`, and the `india_news.py` docstring (BS 403s and was omitted; the one
  remaining mention explains *why* it's omitted).

## Remaining (next slices)

| Slice | Needs | Notes |
|-------|-------|-------|
| **2C — Angel One prices/indicators** | **User SmartAPI creds** (API key + client code + TOTP secret + PIN) | Primary India OHLCV + live; register `angelone` vendor for `get_stock_data`/`get_indicators`. yfinance stays fallback. |
| **2D — screener.in / Tickertape fundamentals** | — (scraping) | `india_fundamentals` vendor; India financials/ratios/shareholding. |
| **2E — NSE market calendar + India run** | — | Trading-holiday/hours helper; set India `data_vendors` defaults; re-run RELIANCE/HDFCBANK on the full India stack + azure-foundry routing. |

## What I need from you for 2C (Angel One)

Angel One SmartAPI needs, in the gitignored `.env` (never chat):
```
ANGELONE_API_KEY=...
ANGELONE_CLIENT_CODE=...      # your client/login id
ANGELONE_PIN=...             # login PIN
ANGELONE_TOTP_SECRET=...     # the TOTP seed (base32), for pyotp auto-login
```
The `smartapi-python` + `pyotp` packages will be added to deps. Until then, yfinance covers
India prices (already working), so 2D/2E can proceed without blocking on creds if preferred.
