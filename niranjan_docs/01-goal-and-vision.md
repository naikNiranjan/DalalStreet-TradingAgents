# 01 — Goal & Vision

## One-sentence goal

Turn DalalStreet-TradingAgents into a **fully Azure AI Foundry-native, multi-agent LLM
trading system for the Indian stock market (NSE/BSE)** that **paper-trades on live data
first**, and — only after proving itself — executes **fully automated** trades through a
broker-agnostic adapter, within SEBI's retail-algo rules.

## Who it's for

A single retail trader (Niranjan), **personal/family use only** — which is exactly what
SEBI permits for self-built algos without exchange empanelment (see
[07-sebi-compliance.md](./07-sebi-compliance.md)). Not a product for others (that would
trigger RA-license / empanelment requirements).

## Objectives (in priority order)

1. **Azure-native AI** — all LLM reasoning runs on **Azure AI Foundry** models via a
   single client, with Entra ID auth and Azure observability. No direct OpenAI/Google keys.
2. **India-correct analysis** — prices, fundamentals, news, sentiment, and macro context
   sourced from India-appropriate providers; benchmarks already Nifty/Sensex.
3. **Safe-by-construction execution** — a deterministic risk layer owns sizing, stops,
   and a kill-switch; the LLM only advises. Paper mode is the default; live is opt-in.
4. **Broker-agnostic** — one `Broker` interface; swap Dhan/Angel/Zerodha without touching
   the agent core.
5. **Validated before real money** — meaningful paper-trading + backtesting track record
   before any live capital, then live with tiny size and tight caps.
6. **Auditable & observable** — full decision→order→fill audit trail and Azure tracing.

## Success criteria

| Phase | "Done" looks like |
|-------|-------------------|
| **Foundry swap** | Whole pipeline runs on Azure Foundry models for `RELIANCE.NS`; cost + traces visible in Azure. |
| **India data** | Analysts cite Indian news/fundamentals; macro agent tracks RBI/SEBI/FII-DII, not the Fed. |
| **Paper trading** | Agent runs daily, simulates **realistic quote-based fills** (slippage/spread/rejection/partials), tracks a virtual portfolio with India-correct costs, and produces a P&L + metrics report. |
| **Backtesting** | Strategy backtested over ≥1 year of NSE data with realistic costs/circuits; Sharpe, max drawdown, hit-rate reported. |
| **Go-live readiness** | ≥ an agreed paper-trading window (e.g. 1–3 months) with acceptable risk-adjusted return, all risk controls tested, kill-switch verified. |
| **Live (tiny)** | Real orders for ≤ an agreed tiny capital, with daily loss limit + kill-switch, fully logged. |

## Explicit non-goals (for v1)

- ❌ High-frequency / sub-second trading (we stay well under SEBI's 10 orders/sec line).
- ❌ Options/F&O in the **first spine** — cash-equity delivery first. F&O is a **deferred,
  paper-only track** added after the spine works, and stays paper until it survives
  multiple expiry cycles (see [doc 10](./10-agent-operating-layer.md) /
  [roadmap deferred tracks](./08-implementation-roadmap.md)).
- ❌ Selling/sharing the strategy with anyone outside immediate family.
- ❌ Letting the LLM directly size positions or place orders without deterministic gates.
- ❌ Guaranteeing returns. This is a research-grade system applied carefully to real money.

## Guiding principles

1. **Paper before live, always.** Live is a config flag flipped only after validation.
2. **Deterministic guardrails around a probabilistic brain.** LLM advises; code controls risk.
3. **Broker- and model-agnostic via adapters.** No vendor lock-in in the core.
4. **Keep upstream mergeable.** Add beside; don't rewrite. Pull TauricResearch updates later.
5. **Small, reversible steps.** Each phase is independently testable and shippable.
6. **Compliance is a feature, not an afterthought.** SEBI rules are built in from day one.

## Confirmed inputs (2026-06-07)

- **Azure** subscription with AI Foundry + GPT-5.x quota is **ready**.
- **Angel One + Dhan** broker accounts are **already held** (Angel = live, Dhan = sandbox).
- Strategy defaults **accepted**: tiny go-live capital (₹10–25k), 5–15 Nifty large-caps,
  once-daily cadence, CNC delivery only, risk caps ≤15%/position · ≤3% daily loss · ≤8
  positions, 1–3 month paper-trading window before live.

See [09-open-questions.md](./09-open-questions.md) for the full locked/open list. Still
open: TOTP-secret storage consent, hosting + static IP, and the concrete go-live metric
thresholds — all decidable before their relevant phase.
