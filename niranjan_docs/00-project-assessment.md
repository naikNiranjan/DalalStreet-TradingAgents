# 00 — Project Assessment: How good is the base repo?

> Honest engineering assessment of **TradingAgents v0.2.5** (the fork's base) as a
> foundation for an India-market automated trading agent.

## Verdict in one line

A **genuinely well-engineered research framework** for multi-agent LLM *analysis* —
clean, modular, and easy to extend — but it is **not a trading system**: it stops at
producing a rating and has **no execution, no broker, no risk engine, and a US-centric
data/news/sentiment layer**. As a *base to build on*, it's a strong 8/10. As a
*finished India trading bot*, it's a 2/10. The gap is exactly the work we're planning.

---

## What the base actually is

A LangGraph pipeline of LLM agents that mimic a trading firm:

```
Analysts (Fundamentals · Sentiment · News · Technical)
   → Researchers (Bull vs Bear debate)
     → Trader
       → Risk Mgmt (Aggressive / Conservative / Neutral debate)
         → Portfolio Manager → FINAL OUTPUT: a 5-tier rating
                                (Buy / Overweight / Hold / Underweight / Sell)
```

The "order is sent to the simulated exchange" line in the README is **conceptual** —
there is no code that places an order anywhere. The pipeline's deliverable is a
markdown decision + a parsed rating string. That's it.

---

## The Good ✅

| Area | Why it's good |
|------|---------------|
| **Architecture** | Clean separation: `agents/`, `dataflows/`, `graph/`, `llm_clients/`. Provider **factory pattern** (`llm_clients/factory.py`) makes adding Azure Foundry a ~1-file change. |
| **Config-driven** | `default_config.py` is a single source of truth with env-var overrides (`TRADINGAGENTS_*`). Vendor selection is data-driven (`data_vendors` / `tool_vendors` dicts). |
| **Already India-aware (partially)** | `benchmark_map` maps `.NS`→Nifty 50 (`^NSEI`), `.BO`→Sensex (`^BSESN`). yfinance covers NSE/BSE. Instrument identity resolves per-market. |
| **Multi-LLM, incl. Azure** | Already supports OpenAI/Anthropic/Google/xAI/DeepSeek/Qwen/GLM/MiniMax/Ollama **and Azure OpenAI**. Structured outputs + tool-calling are first-class. |
| **Production niceties** | LangGraph **checkpoint/resume**, **decision-log memory with reflection** (learns from realized alpha), Docker, structured outputs, path-traversal hardening. |
| **Tested** | ~30 test files (`tests/`) covering signal processing, symbol handling, structured agents, model validation, etc. |
| **Backtesting dep present** | `backtrader` is already a dependency — a foundation for strategy backtests. |
| **Reflection/learning loop** | After a holding window it fetches realized raw + alpha return vs the right index and feeds a reflection back into the Portfolio Manager. Rare and valuable. |

---

## The Bad / Missing ❌ (this is our work list)

| # | Gap | Severity | Notes |
|---|-----|----------|-------|
| 1 | **No execution/broker layer** | 🔴 Critical | Cannot paper-trade or go live. Needs a broker-agnostic adapter + order/position/portfolio management. |
| 2 | **No paper-trade simulator** | 🔴 Critical | No fill engine to validate on live data before real money. |
| 3 | **No risk/position-sizing engine** | 🔴 Critical | No capital allocation, stop-loss, daily loss limit, kill-switch, exposure caps. |
| 4 | **US-centric sentiment** | 🟠 High | Uses StockTwits + Reddit (US). India needs Moneycontrol/forums/Telegram/India Reddit. |
| 5 | **US-centric macro news** | 🟠 High | `global_news_queries` are all "Federal Reserve / S&P 500". Needs RBI/SEBI/Budget/FII-DII/USD-INR/crude. |
| 6 | **No India market mechanics** | 🟠 High | No market hours (9:15–15:30 IST), trading-holiday calendar, circuit limits, ASM/GSM, F&O ban list, T+1. |
| 7 | **No India cost model** | 🟠 High | No STT/stamp/GST/brokerage/DP in P&L → backtests & live P&L would be wrong. |
| 8 | **Data layer thin for India** | 🟡 Medium | yfinance is delayed + intraday-limited; Alpha Vantage India fundamentals are weak. Needs broker data + screener/Tickertape. |
| 9 | **Not Azure Foundry-native** | 🟡 Medium | Only Azure **OpenAI** (GPT) is wired; the broader Foundry catalog (DeepSeek, Llama, Grok, Phi via one client) and Entra ID auth are not. |
| 10 | **No scheduler/orchestration** | 🟡 Medium | No daily run loop, token-refresh cron, or live event loop tying analysis → decision → order. |
| 11 | **No live observability** | 🟡 Medium | No Azure App Insights / tracing wired for a production agent. |
| 12 | **Single-name, single-shot** | 🟢 Low | `propagate(ticker, date)` analyzes one stock for one date. A live agent needs a universe loop + intraday cadence. |

---

## Areas of improvement (beyond just "make it work for India")

These are quality/edge upgrades worth doing as we build:

1. **Two-tier model routing by Foundry** — route cheap "quick-think" agents to
   `gpt-5.4-nano`/Phi-4-mini and deep-reasoning agents to `gpt-5.2`/DeepSeek-V4 to cut
   cost ~10–40× on the high-volume steps. (See [04](./04-research-azure-foundry.md).)
2. **Resilience for fan-out** — multi-agent LLM calls trip sub-minute 429s on Azure;
   add backoff+jitter, honor `retry-after`, minimize `max_tokens`.
3. **Deterministic guardrails around the LLM** — the LLM gives a *rating*; a
   deterministic risk layer must own sizing, stops, and the kill-switch. Never let the
   LLM directly size or fire orders.
4. **Separate "analysis" from "execution" cleanly** — keep the existing graph as a pure
   advice engine; bolt a new `execution/` package beside it so upstream merges from the
   original repo stay easy.
5. **India-correct backtesting** — wire `backtrader` with STT/cost model + circuit/holiday
   awareness so backtests reflect reality.
6. **Audit log** — every decision → order → fill must be logged immutably (SEBI audit
   trail + your own post-mortem).
7. **Config profiles** — `paper` vs `live` profiles so going live is a flag, not a diff.

---

## Build vs. rebuild?

**Build on it. Do not rewrite.** The analysis engine is the hard, valuable part and it's
already good. We add a new **execution layer beside** it and **swap the data/LLM
adapters** — all of which the existing factory/vendor patterns are designed for. Keeping
the upstream structure intact also lets us pull future improvements from TauricResearch.

## Risk to be explicit about

This base is explicitly a **research tool** and its authors disclaim financial advice.
LLM output is **non-deterministic** — the same stock can get different ratings across
runs. Real money therefore requires: deterministic risk caps, a long paper-trading
validation period, and starting live with tiny capital. The plan enforces all three.
