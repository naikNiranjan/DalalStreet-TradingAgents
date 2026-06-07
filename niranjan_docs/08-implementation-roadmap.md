# 08 — Implementation Roadmap

> Phased, safe-by-construction. **Paper-trading fully precedes any real money.** Each
> phase is independently testable and shippable. No coding until these docs are reviewed
> + approved.
>
> **SPINE FIRST.** Phases 0–5 build a thin vertical slice (one cash-equity stock → paper →
> realistic fills → fail-closed gates → audit → report) end-to-end. The
> [Agent Operating Layer](./10-agent-operating-layer.md) (skills/memory/cron/toolsets) and
> the **F&O paper track** are **deferred tracks** layered on *after* the spine works — see
> "Deferred tracks" below.

## Pre-requisite (do before Phase 0 coding)

**Go-live success metrics are now defined** in [11-success-metrics.md](./11-success-metrics.md)
(≥30 sessions, ≥25 trades, ≤5% max DD, 0 trades after daily-loss/stale-data, 100% audit,
≥3 kill-switch drills, positive expectancy after costs+slippage, manual approval). They were
written **before** coding on purpose — so results can't be rationalized into a premature
go-live. Treat that doc as the paper→live contract.

## Phase map

| Phase | Theme | Outcome | Real-money risk |
|-------|-------|---------|-----------------|
| 0 | Baseline run | Repo runs as-is on an Indian ticker | None |
| 1 | Azure Foundry swap | All LLMs on Foundry, traced | None |
| 2 | India data layer | India-correct analysis | None |
| 3 | Execution + paper trading (+ **MVP risk gates**) | Virtual portfolio on live data, never trades past a hard gate | None (simulated) |
| 4 | Backtesting | Validated strategy over history | None |
| 5 | Ops / compliance hardening | Full guards, scheduler, audit, SEBI compliance | None |
| 6 | Go live (tiny) | Real orders, tight caps | **Real — minimal** |

---

### Phase 0 — Baseline run (½ day)
**Goal:** prove the untouched engine works on Indian stocks end-to-end.
- Install deps (`uv`/`pip`), set one LLM key, run `propagate("RELIANCE.NS", <date>)`.
- Confirm benchmark resolves to `^NSEI`, reports generate, rating prints.
- **Exit:** a clean analysis run on `RELIANCE.NS` + `HDFCBANK.NS`.

### Phase 1 — Azure AI Foundry swap (2–3 days)
**Goal:** every LLM call runs on Azure Foundry, natively, with tracing.
> ✅ Azure subscription + Foundry access already confirmed — no onboarding step; target it directly.
- Add `azure-foundry` provider: `llm_clients/azure_foundry_client.py`
  (`AzureAIOpenAIApiChatModel`) + register in `factory.py`.
- Add config keys: project endpoint, deep/quick model names, Entra auth.
- Wire `AzureAIOpenTelemetryTracer` via existing `callbacks` path.
- Add Azure env vars to `.env.example`; Entra ID (`DefaultAzureCredential`) for dev.
- Tests: provider factory returns Foundry client; a smoke run on `RELIANCE.NS`.
- **Exit:** full pipeline runs on `gpt-5.x` (deep) + `gpt-5.4-mini/nano` (quick); traces
  visible in Azure; cost observed.

### Phase 2 — India data layer (4–6 days)
**Goal:** analysts use India-appropriate data + context.
- New providers under `dataflows/india/` (or `india_data/`): Angel One prices/historical,
  screener/Tickertape fundamentals, Moneycontrol/ET/BS RSS news, macro (data.gov.in/RBI/
  FII-DII), Reddit-India + Telegram sentiment.
- Register them in the `data_vendors` / `tool_vendors` config mechanism.
- Rewrite `global_news_queries` → India macro set; re-point Reddit subs; drop StockTwits.
- Add NSE **market calendar** (holidays/hours) helper.
- Tests per provider (mocked network).
- **Exit:** analysis on `RELIANCE.NS` cites Indian news/fundamentals; macro agent tracks
  RBI/SEBI/FII-DII, not the Fed.

### Phase 3 — Execution layer + paper trading + MVP risk gates (6–10 days)
**Goal:** the system *trades* — on simulated fills over live data — and **never trades
past a hard gate.** Because Phase 3 is the first phase that *acts* (even simulated), it
ships with the **minimum fail-closed gates from day one**; Phase 5 then hardens the full
set. No "act first, add safety later."
- `execution/` package: `Broker` interface, **`PaperBroker`** (**realistic fills** —
  slippage/spread/rejection/partials, NOT fill-at-LTP), `portfolio.py`, `router.py`
  (`SignalDecision`→intent), `audit.py`. Build the **`SignalDecision` contract** +
  **security master** (symbol→token/ISIN/lot/tick) here.
- **MVP fail-closed gates (`risk/guards.py`, ship in this phase):** block on stale/missing
  data · missing audit entry · no/expired auth · daily-loss-limit hit · per-position size
  cap · max-open-positions cap · global kill-switch. Default = block.
- **India cost model** (`costs/india_costs.py`): STT/stamp/GST/SEBI/exchange/DP.
- Map the `SignalDecision` → target position via deterministic rules.
- Run daily over a small universe; produce a P&L + metrics report.
- Tests: simulator fills, cost math, portfolio accounting, **each MVP gate vetoes correctly**.
- **Exit:** a running **virtual portfolio** with India-correct costs + a daily report, where
  every order provably passed the MVP gates (and blocked orders are logged, not silently dropped).

### Phase 4 — Backtesting (3–5 days)
**Goal:** validate the strategy over history before risking money.
- Wire **`backtrader`** with the India cost model + circuit/holiday awareness.
- Backtest over ≥1 year NSE data; report CAGR, Sharpe, Sortino, max DD, hit-rate,
  turnover, cost drag.
- **Exit:** a reproducible backtest with realistic costs + a metrics summary.

### Phase 5 — Ops / compliance hardening (4–6 days)
**Goal:** make it safe + unattended + compliant. **Extends** the MVP gates from Phase 3 to
the full set (the MVP gates already exist and have been live in paper since Phase 3).
- `risk/guards.py`: add position sizing, stop-loss, exposure caps, **order-rate cap
  (<10 OPS)** on top of the Phase-3 MVP gates (stale-data/auth/daily-loss/size/kill-switch).
- `risk/market_rules.py`: circuit/ASM/GSM/F&O-ban checks before any order.
- `scheduler/`: market-hours-gated run loop + **token-refresh cron** (`pyotp`).
- Secrets → Key Vault; static IP host; finalize audit log.
- Resilience: Azure 429 backoff+jitter, retry-after handling.
- Run the **[SEBI checklist](./07-sebi-compliance.md)**.
- **Exit:** unattended paper run for days, kill-switch verified, all guards tested.

### Phase 6 — Go live, tiny (gated)
**Goal:** real orders, minimal capital, maximal caution.
- Add **`AngelOneBroker`** live adapter behind the same `Broker` interface.
- Flip `execution.mode = live` for **≤ an agreed tiny capital**.
- Start with 1–2 liquid large-caps, hard daily loss limit, manual kill-switch ready.
- Daily review of decisions vs fills vs P&L.
- **Entry gate:** only after an agreed paper-trading window (e.g. 1–3 months) with
  acceptable risk-adjusted results + all Phase-5 guards proven. → [09](./09-open-questions.md)

---

## Deferred tracks (built AFTER the Phase 0–5 spine works)

These are real and planned, but intentionally **not** part of the first vertical slice.
Starting them early would mean a fancy shell with no working trading loop.

### Track F&O — F&O paper trading (deferred; paper-only for a long time)
- Add option-chain / futures data (Dhan option chain: OI, Greeks, IV, bid/ask, volume),
  NSE contract master, **Tuesday-expiry** calendar logic (post-Sep-2025).
- F&O-specific risk: max premium loss, **no naked selling**, expiry-day rules, IV filter,
  liquidity/spread gates.
- Extend the existing LangGraph with an **Option-Chain analyst node** rather than a new
  parallel agent system.
- **Gate:** F&O stays paper-only until it **survives multiple expiry cycles** with
  acceptable results. Real-money F&O is treated as not-ready until then.

### Track AOS — Agent Operating Layer (deferred; see [doc 10](./10-agent-operating-layer.md))
- `agent_os/`: skills/playbooks, memory tiers, cron jobs, toolset isolation, context/rule
  files, reports. Built ourselves (Hermes = reference only at `references/hermes-agent`).
- **Constraint:** can schedule/remember/suggest; **never** bypasses risk gates or places
  orders. Optional Hermes sidecar via safe API/MCP much later (Phase C in doc 10).

---

## Sequencing logic
- **0→1→2** are zero-risk and make the brain correct for India before it can act.
- **3** introduces *acting*, but only simulated.
- **4** validates on history; **5** makes acting safe + legal; **6** is the only phase that
  touches real money, and only behind an explicit gate.

## Rough effort (solo, part-time)
~4–6 weeks of focused work to end of Phase 5; Phase 6 is gated by the paper-trading
window, not by code. Estimates are directional — revise after review.

## Definition of done (project)
Unattended, Azure-Foundry-native agent trading a small Indian large-cap universe in paper
mode with India-correct costs + full risk guards + audit + tracing, **and** a validated
backtest, **ready** to flip to live for tiny capital on Niranjan's explicit go.
