# 06 — Research: India Data Sources

> Current as of June 2026. **Important:** none of NSE, BSE, screener.in, Tickertape, or
> Trendlyne expose a free official public REST API. Most "free" Python access scrapes
> internal JSON endpoints — unstable and ToS-grey. **Broker APIs are the only reliable,
> sanctioned route for prices.** Per-source flags below. Use respectful rate-limiting +
> caching; commercial redistribution is risky.

## Recommended India data stack

| Need | Primary (free) | Augment | Paid upgrade |
|------|----------------|---------|--------------|
| Prices/technical (live+intraday+historical) | **Angel One SmartAPI** (free incl. historical + WebSocket) | yfinance (`.NS/.BO`, indices, EOD fallback); `jugaad-data` bhavcopy | Dhan ₹499/mo; Zerodha ₹500/mo |
| Fundamentals | **screener.in (scrape)** + **Tickertape** (`Bharat-SM-Data`) | NSE/BSE filings (`bse`) | Trendlyne Pro (license-clean) |
| News | **Moneycontrol + ET + Business Standard RSS** | NewsData.io free tier; BSE announcements | StockInsights.ai |
| Macro context | **data.gov.in API** + **RBI DBIE** + **NSE FII/DII** | MoSPI eSankhyiki | Trading Economics API |
| Sentiment | **r/IndianStockMarket (Reddit API)** + **Telegram Bot API** | Moneycontrol/ValuePickr forums | X pay-per-use / Adanos |
| Mechanics/corp actions | **NSE via `jugaad-data`/`nsepython`** + hardcoded cost rules | `bse` for BSE | — |

## 1. Prices & technical
- **yfinance** (`.NS`/`.BO`, `^NSEI`, `^NSEBANK`): free but **15-min delayed**, **1-min
  data only last 7 days**, any sub-daily only last 60 days; occasional "no data" errors on
  valid symbols. Fine for **EOD/daily + indices + prototyping**, not live/deep-intraday.
- **Angel One SmartAPI**: free real-time WebSocket + free historical → **primary** live
  source. Streams ticks only (build candles yourself).
- **`jugaad-data` / `nsepython`**: scrape NSE site (bhavcopy, quotes); future-proofed on new
  NSE site. **`bse`/`BseIndiaApi`**: BSE quotes/announcements/corp actions.

## 2. Fundamentals
- **screener.in** — gold standard for Indian retail fundamentals (P&L, BS, CF, ratios,
  shareholding), **scrape only**, daily EOD refresh, ToS-grey (personal/research tolerated).
- **Tickertape** via **`Bharat-SM-Data`** — best *programmatic* route (income/BS/ratios/MF
  holdings).
- **NSE/BSE filings** (`bse`, `jugaad-data`) — authoritative shareholding/results.
- **Trendlyne Pro** — SEBI-registered, license-clean Excel/Sheets add-in + DVM scores (paid).
- **Drop Alpha Vantage for India** — weak/deprecated India fundamentals, harsh free limits.

## 3. News
- **RSS (free, direct):** Moneycontrol (granular feeds), Economic Times Markets, Business
  Standard, LiveMint → headline+summary (full body copyrighted; WebFetch per-article if
  needed). Primary feed for the news analyst.
- **NewsData.io** (free 6,000 req/mo) for keyword/ticker search; GNews/NewsAPI free tiers
  (headlines/snippets only). **BSE announcement scraping** for primary-source filings.

## 4. Macro context — replace US Fed/S&P with India topics
Track: **RBI MPC / repo rate** (context: held 5.25%, neutral, June 2026), **SEBI actions**,
**Union Budget (Feb 1)** incl. STT changes, **GST collections**, **monsoon/IMD**,
**FII/DII flows** (DII ownership now > FPI; record SIP inflows), **USD/INR** (~95, crude-
driven), **Brent crude**, **Nifty/Sensex/Bank Nifty levels + OI**, **sector rotation**.
Sources: **data.gov.in** (`datagovindia` — true REST API, key auth) for CPI/IIP/GDP;
**RBI DBIE** for repo/FX; **NSE FII/DII report**; **MoSPI eSankhyiki**.

→ Concretely: rewrite `default_config.py`'s `global_news_queries` to the India set above.

## 5. Social sentiment (re-point away from US StockTwits)
- **r/IndianStockMarket** via Reddit API (`praw`) — keep Reddit, switch subreddits.
- **Telegram Bot API** (`python-telegram-bot`) on a **curated** channel list — legal for
  channels you join; **high manipulation risk** (pump-and-dump) → treat as risk/contrarian
  signal, not ground truth.
- **Moneycontrol / ValuePickr / TradingQNA** forum scraping (research-scale, rate-limited).
- **X/Twitter**: richest Indian fintwit but **2026 access is pay-per-use / costly**; official
  free tier gone; scrapers violate ToS. **Avoid building on X** unless budget allows.
- Flag **all** social sentiment as manipulation-prone, especially illiquid small-caps.

## 6. India market mechanics the agent MUST encode
- **Hours:** 09:15–15:30 IST equities; pre-open 09:00–09:15.
- **T+1 settlement** (SEBI piloting T+0). Watch **settlement holidays** (distinct from
  trading holidays).
- **Trading holidays 2026:** ~15; **Muhurat trading Sun Nov 8, 2026**. Source: NSE calendar
  (mirrored by `jugaad-data`).
- **F&O expiry now TUESDAY** (since 1 Sep 2025; was Thursday) — hardcode if we add F&O.
- **Circuit limits / price bands** — per-stock daily caps; next-day bands from NSE
  surveillance CSV.
- **ASM** (volatility surveillance: 5% circuit, 100% margin) and **GSM** (6 stages for
  illiquid/penny — hard-flag/avoid) — NSE/BSE lists.
- **F&O ban list (MWPL)** — issued daily by NSE; no fresh F&O positions while banned.
- **Costs for accurate net P&L (2026):** STT (delivery 0.1% both sides; intraday 0.025%
  sell; futures 0.05% sell; options 0.15% premium sell) + stamp duty (buy side) + **GST 18%**
  on (brokerage+exchange+SEBI fees) + SEBI turnover ₹10/cr + exchange txn charges +
  **DP charges ₹12–30+GST per ISIN per day on delivery sells**.
  **Net = Gross − (Brokerage + STT + Exchange + GST + Stamp + SEBI + DP).** Reference:
  Zerodha brokerage calculator.

## Cross-cutting legal/ToS
- **Sanctioned/clean:** broker APIs (Angel/Dhan/Zerodha/...). Prefer wherever possible.
- **Grey (personal/research tolerated, respectful rate-limiting):** yfinance, screener/
  Tickertape/NSE/BSE scraping, forum scraping. Commercial redistribution = risky → use
  Trendlyne/licensed feeds if it ever becomes a product.
- **Avoid:** X scraping (ToS + fragile); WhatsApp at scale (not feasible/legal).

## Key sources
- jugaad-data: https://github.com/jugaad-py/jugaad-data · nsepython: https://pypi.org/project/nsepython/
- Bharat-SM-Data (Tickertape): https://bharat-sm-data.readthedocs.io/ · screener.in: https://www.screener.in/
- BSE API: https://github.com/BennyThadikaran/BseIndiaApi
- Moneycontrol RSS (Feedspot): https://rss.feedspot.com/moneycontrol_rss_feeds/ · Business Standard RSS: https://www.business-standard.com/rss-feeds/listing
- data.gov.in: https://www.data.gov.in/apis · datagovindia: https://econabhishek.github.io/datagovindia/ · RBI DBIE: https://data.rbi.org.in/DBIE/
- NSE FII/DII: https://www.nseindia.com/reports/fii-dii · NSE price bands: https://www.nseindia.com/reports/price-band-changes
- STT 2026 (ClearTax): https://cleartax.in/s/securities-transaction-tax-stt · Zerodha brokerage calc: https://zerodha.com/brokerage-calculator/
