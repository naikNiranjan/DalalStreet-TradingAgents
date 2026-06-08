# DATA_SOURCES

> Binding policy for **which source is authoritative for what**, how fresh each kind of
> data must be, and what the desk does when a source is unavailable. The governing
> principle is **fail-closed**: when data is missing, stale, or conflicting, the desk
> does not trade on it.

## Authoritative source per data type

| Data type | Authoritative source (v1) | Notes |
|-----------|---------------------------|-------|
| Live/last-traded price & quotes | Broker market-data feed (Angel One SmartAPI) | yfinance is a fallback for analysis only, never for fill pricing |
| Intraday / historical OHLCV | Broker feed; yfinance fallback (`.NS` / `.BO`) | used for indicators and backtests |
| Security master (tradability, lot, tick) | Broker security master | the single source of truth for "is this tradable" |
| Fundamentals | India fundamentals source | for analysis; not used to price fills |
| News | India news source(s) with URL + timestamp | every news item must carry a source and a time |
| Sentiment | India sentiment source | advisory only; never the sole basis for a trade |
| Benchmarks | Nifty 50 / Sensex | for regime and relative-strength context |

## Freshness clocks

- **Pricing data used for sizing or fills must be fresh** (within the configured
  freshness window). A stale quote blocks the trade — see the stale-quote gate.
- Each data type carries its own freshness expectation; the strictest applies to any
  decision that depends on multiple types.
- The desk **records the timestamp** of the data it acted on, so freshness is auditable
  after the fact.

## Source precedence and conflict

- For **fill pricing**, the broker feed is authoritative. yfinance and other free
  sources are for analysis context only and are **never** used to price a simulated fill.
- When two sources disagree materially on price, the desk treats the data as **suspect**
  and does not trade until it is resolved — disagreement is a no-trade condition, not an
  averaging exercise.
- For tradability and instrument identity, the **security master is final**. If the
  security master and another source disagree, the security master wins.

## Fail-closed behavior

- **Missing data → no trade** on the affected name. The desk never fills a gap with an
  assumption.
- **Stale data → no trade** on the affected name until refreshed.
- **Source unreachable → degrade safely:** the desk may fall back to a permitted analysis
  source for *context*, but it never substitutes a fallback for the authoritative pricing
  or tradability source when placing or sizing a trade.

## Auditability

- The source and timestamp of decision-critical data are recorded alongside the
  decision, so any trade can be traced back to the exact data it relied on.
- Data that cannot be sourced and timestamped cannot be the basis for a trade.

## Authority of these rules

These are **binding constraints**. If a request or a model suggestion asks the desk to
trade on data that is missing, stale, unsourced, or from a non-authoritative source for
that purpose, the desk **declines**. When in doubt about freshness or provenance, the
desk treats the data as unusable and does not trade.
