# Phase 2 — India Data Layer (progress)

> Branch: `feature/phase2-india-data` (off Phase 1). Incremental, tested slices.
> **Not committed** — awaiting review.
> Status: **Phase 2 COMPLETE — 2A, 2B, 2C, 2D, 2E + end-to-end validation passed.**

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

### 2E (part 1) — NSE market calendar ✅
- **NEW `dataflows/india_calendar.py`** — trading-day / holiday / market-hours (IST) logic.
  - Exact: weekends + market hours **09:15–15:30 IST** (`is_market_open`, tz-aware),
    `next_trading_day` / `previous_trading_day`.
  - Holidays: **conservative built-in set** (fixed-date national holidays only) +
    config `nse_holidays` override. ⚠️ Movable holidays (Holi/Good Friday/Eid/Diwali-Muhurat)
    must be added from the official NSE list before live — clearly flagged in code + config.
- **Tests:** `tests/test_india_calendar.py` (12) — weekends, hours boundaries, tz conversion,
  holiday skip, navigation. Regression: **359 passed**.

### 2D — screener.in fundamentals enrichment ✅
- **NEW `dataflows/india_fundamentals.py`** — scrapes the screener.in company page for the
  key-ratio block + **Pros/Cons** (what screener is uniquely good at; no public API) and layers
  it **on top of** the existing yfinance overview. Returned text = `screener summary + yfinance`.
  - **Fail-soft by design:** if screener is unreachable or its DOM changes, silently falls back
    to yfinance alone — never degrades below current behaviour. Raises only if **both** sources
    are empty, so the router can try a further vendor.
  - Tries consolidated page first, then standalone; exchange suffix stripped (`RELIANCE.NS` →
    `RELIANCE`). Browser UA; ToS note (personal/research, low volume) in the module docstring.
- **Registered `india_screener` vendor** in `interface.py` for `get_fundamentals`; added to
  `VENDOR_LIST`. **`get_fundamentals` defaults to `india_screener`** (tool_vendors).
- **Verified live:** RELIANCE returned real ratios (P/E 22.4, ROCE 10.3%, ROE 8.91%,
  Book Value ₹668) + Cons.
- **Tests:** `tests/test_india_fundamentals.py` (8) — parsing, screener-only / yfinance-only /
  both-empty fallbacks, suffix strip, vendor wiring.

## End-to-end India validation run ✅

**RELIANCE.NS @ 2026-06-05**, full India stack + Azure Foundry role routing, all 4 analysts
(market / news / fundamentals / social), 1 debate + 1 risk round. Wall-clock **682s (~11.4 min)**,
exit 0.

- **Final rating: `Hold`.**
- **Foundry routing ran live, no errors:** PM=gpt-5.5, research=claude-sonnet-4-6, market=grok-4.3,
  news/fundamentals=DeepSeek-V4-Pro, social=DeepSeek-V4-Flash — confirms all 5 models work together
  through the role-routing layer on a real graph run (not just the benchmark).
- **India data present in every report (marker check):**
  - Fundamentals → `ROCE`, `Cons` (screener.in enrichment reached the analyst)
  - News → `RBI`, `Nifty`, `Sensex` (india_rss macro)
  - Sentiment → `IndianStockMarket`, `Reddit`, `Indian` (India subs)
- **Graceful degradation verified under live failures:**
  - Moneycontrol RSS returned **403** → other feeds (ET/LiveMint/Hindu BL) covered the window.
  - Reddit **JSON API 403'd** for all three subs → **auto-fell-back to each sub's RSS feed**;
    sentiment still delivered IndianStockMarket data. No crash, no fabricated values.
- **Regression after 2D+2E: 367 passed, 1 skipped** (live DeepSeek call, intentional).

### 2C — Angel One (SmartAPI) prices + indicators ✅
- **NEW `dataflows/angel_one.py`** — the project's **primary India price source** (NSE/BSE cash
  equities). Two vendor functions matching existing contracts:
  - `get_stock_data_angel` → `get_stock_data` (daily OHLCV CSV, mirrors the yfinance format).
  - `get_indicators_angel` → `get_indicators` (stockstats over Angel candles; identical output
    shape to the yfinance window so reports read the same regardless of vendor).
- **Auth:** SmartConnect API key + client code + login PIN + **TOTP (pyotp)**; session cached
  module-level and reused within a process. `ANGELONE_*_STATIC_IP` are app-registration metadata
  (not used in code — SmartConnect detects caller IP).
- **Safe-by-default so it can be the default vendor without ever degrading behaviour:**
  - **No / placeholder creds → instant raise, NO network** (unit tests + CI never hit the API).
  - **Non-India ticker (no `.NS`/`.BO`, or an index like `^NSEI`) → instant `NoMarketDataError`,
    NO network** → falls straight through to yfinance via `route_to_vendor`.
  - Any live failure raises → router falls back to yfinance automatically.
  - **Look-ahead safe:** OHLCV uses yfinance's exclusive-end convention; indicators never emit a
    value past `curr_date`.
- **Registered `angelone` vendor** for `get_stock_data` + `get_indicators`; set as the
  **tool_vendors default** for both (yfinance auto-appended as fallback). Deps added to
  `pyproject.toml`: `smartapi-python>=1.5.5`, `pyotp>=2.9.0`.
- **Test guard:** `import tradingagents` loads the dev `.env`, so the conftest autouse fixture now
  **blanks `ANGELONE_*`** for the whole suite → no test ever fires a live login.
- **🟠 Boundary-day bug caught in live verification & fixed:** daily candles are stamped **00:00
  IST**, so a `09:15` `fromdate` silently **dropped the first requested trading day**. Window now
  brackets midnight (`00:00`→`23:59`); regression test asserts it.
- **Verified live (read-only, no orders):** RELIANCE.NS OHLCV `2026-06-01..06` returned 5 real
  bars (₹1320→₹1291, realistic volumes), `close_50_sma` computed with look-ahead guard, and AAPL
  fast-skipped to yfinance with no network.
- **Tests:** `tests/test_angel_one.py` (20) — exchange/token resolution, candle parsing (IST date,
  no UTC shift), creds/non-India fast-fail guards, exclusive-end, midnight-boundary, indicator
  window + look-ahead, vendor wiring. Also updated `test_dataflows_config.py` for the new
  tool_vendors defaults.

**Post-review fixes (2026-06-08):**
- **🟠 Empty indicator candles → `KeyError('Date')`:** `get_indicators_angel` filtered on
  `df["Date"]` before the empty check, so an empty candle response (no columns) crashed instead
  of raising the clean `NoMarketDataError` fallback. Now guards `df.empty or "Date" not in
  df.columns` first; regression test added.
- **🟡 Login logged the real client code:** removed — broker auth metadata shouldn't hit logs;
  now logs only "Angel One session established".

**Regression after 2C + fixes: 388 passed, 1 skipped** (live DeepSeek call, intentional).

## Remaining

Phase 2 India data layer is **complete** (2A news, 2B sentiment, 2C prices/indicators,
2D fundamentals, 2E calendar). Next phase is the execution/paper-trading spine — out of
Phase 2 scope.

> ⚠️ Before live (not paper) trading: populate `config["nse_holidays"]` with the full official
> NSE list incl. movable holidays (see `india_calendar.py`), and rotate the temp Azure keys.
