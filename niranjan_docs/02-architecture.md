# 02 — Target Architecture

## Design rule

Keep the **existing analysis graph as a pure "advice engine."** Add a new **execution
layer beside it.** Swap **adapters** (LLM → Azure Foundry, data → India sources). Never
let the LLM directly size or place orders. This keeps the system safe and keeps the fork
mergeable with upstream TauricResearch.

## Build philosophy: SPINE FIRST

Build a **thin vertical slice end-to-end before breadth**: one cash-equity stock → analysis
→ `SignalDecision` → risk gates → paper fill → audit → report. Prove that loop works and
stays inside its gates. Only then layer on the [Agent Operating Layer](./10-agent-operating-layer.md)
(skills, memory tiers, cron, toolsets — **deferred**) and the **F&O paper track**
(deferred; Tuesday-expiry-aware). Advanced features are added with evidence, on top of a
working loop — never as scaffolding underneath it.

## Three building blocks introduced before execution

- **`SignalDecision`** — the typed contract handed from the analysis engine to the
  execution layer (symbol, direction, conviction/confidence, suggested entry/invalidation/
  target, rationale, source skill). The clean seam between "brain" and "hands."
- **Security master** — maps `RELIANCE.NS` → broker symbol / instrument token / ISIN / lot
  size / tick size. Required before any order can be formed correctly.
- **Toolset isolation** — the LLM only ever sees `paper`/`data`/`analysis`/`audit` tools;
  the **`live` order toolset is not in its hands** until far later (see doc 10).

## High-level diagram

```
                         ┌─────────────────────────────────────────────┐
                         │           SCHEDULER / RUN LOOP              │
                         │  • token-refresh cron (~08:45 IST)          │
                         │  • daily/intraday analysis cadence          │
                         │  • market-hours + holiday gate              │
                         └───────────────────┬─────────────────────────┘
                                             │ for each ticker in universe
                                             ▼
   ┌──────────────────────────── ANALYSIS ENGINE (existing, adapted) ───────────────────┐
   │  LangGraph multi-agent pipeline                                                     │
   │   Analysts → Researchers (bull/bear) → Trader → Risk debate → Portfolio Manager     │
   │                                                                                     │
   │   LLM calls ──────────────►  Azure AI Foundry client (langchain-azure-ai)           │
   │   Data tools ─────────────►  India Data Layer (below)                               │
   │                                                                                     │
   │   OUTPUT: rating (Buy/Overweight/Hold/Underweight/Sell) + reasoning + confidence    │
   └───────────────────────────────────────────┬─────────────────────────────────────────┘
                                                │ rating + context
                                                ▼
   ┌──────────────────────────── EXECUTION LAYER (new) ─────────────────────────────────┐
   │  1. SIGNAL → INTENT      map rating → desired target position (deterministic rules) │
   │  2. RISK ENGINE          position sizing, stop-loss, daily loss limit, exposure cap,│
   │                          circuit/ASM/GSM/F&O-ban checks, kill-switch  ◄── HARD GATES │
   │  3. ORDER ROUTER         paper  ──► Paper Fill Simulator (realistic quote-based fills)│
   │                          live   ──► Broker Adapter (Angel/Dhan/...)                  │
   │  4. PORTFOLIO/POSITIONS  positions, cash, India cost model (STT/GST/stamp/DP)        │
   │  5. AUDIT LOG            immutable decision→order→fill trail                          │
   └───────────────────────────────────────────┬─────────────────────────────────────────┘
                                                │
                ┌───────────────────────────────┴───────────────────────────────┐
                ▼                                                                ▼
   ┌────────────────────────┐                                    ┌────────────────────────┐
   │  PAPER MODE (default)  │                                    │   LIVE MODE (opt-in)   │
   │  realistic quote fills │                                    │  real broker orders    │
   └────────────────────────┘                                    └────────────────────────┘

   Cross-cutting: Azure App Insights tracing • config profiles (paper/live) • secrets in Key Vault
```

## Package layout (proposed, additive)

```
tradingagents/                      # EXISTING — adapt in place where marked
  agents/            ...            # (adapt prompts/queries for India)
  dataflows/         ...            # + india/ subpackage of new providers
  graph/             ...            # unchanged orchestration
  llm_clients/
    azure_foundry_client.py         # NEW — Foundry-native client (langchain-azure-ai)
  default_config.py                 # + india + execution + azure config keys

execution/                          # NEW top-level package (the whole execution layer)
  brokers/
    base.py                         # Broker interface (place/modify/cancel, positions, quotes)
    paper.py                        # Paper fill simulator (realistic: slippage/spread/rejection/partials)
    angelone.py                     # Live adapter (primary)
    dhan.py                         # Live adapter / sandbox
  risk/
    sizing.py                       # position sizing
    guards.py                       # stop-loss, daily loss limit, exposure caps, kill-switch
    market_rules.py                 # hours, holidays, circuits, ASM/GSM, F&O ban
  costs/
    india_costs.py                  # STT, stamp, GST, brokerage, DP → net P&L
  portfolio.py                      # positions, cash, mark-to-market
  router.py                         # signal→intent→risk→order routing
  audit.py                          # immutable audit log

india_data/                         # NEW — India data providers (or under dataflows/india/)
  prices_angelone.py · prices_yfinance.py · fundamentals_screener.py
  news_rss.py · macro_rbi.py · sentiment_reddit_telegram.py · calendar_nse.py

scheduler/
  run_loop.py                       # daily/intraday loop
  token_refresh.py                  # broker daily-token cron

niranjan_docs/                      # THIS folder (planning)
```

> **Decided:** the new packages live **top-level** (`execution/`, `india_data/`) beside the
> upstream `tradingagents/` package — keeps execution clearly separate from the analysis
> package and minimizes merge conflicts. (Locked in [09-open-questions.md](./09-open-questions.md).)

## Key interfaces (contracts, not implementations)

### `Broker` (execution/brokers/base.py)
```
class Broker(Protocol):
    def get_quote(symbol) -> Quote
    def get_ltp(symbol) -> float
    def place_order(order: Order) -> OrderId
    def modify_order(id, ...) -> None
    def cancel_order(id) -> None
    def get_positions() -> list[Position]
    def get_order_status(id) -> OrderStatus
    def is_market_open() -> bool
```
`PaperBroker` and `AngelOneBroker`/`DhanBroker` both implement this. The agent core only
ever sees `Broker` — paper vs live is which implementation is injected.

### Risk gate (execution/risk/guards.py) — HARD, deterministic, FAIL-CLOSED
Every intended order passes through guards that can **veto** it. Default is **block**: if
any precondition can't be verified, the trade does **not** happen.
- **Fail-closed hard blocks:** stale/missing data · missing audit log · no/expired broker
  auth · missing risk state · spread too wide · illiquid contract · daily loss limit hit ·
  global kill-switch · (F&O later) naked option selling.
- daily realized+unrealized loss ≥ limit → block all new buys
- per-trade and total exposure caps; order-rate cap **well below SEBI's 10/s**
- symbol in F&O ban / GSM / frozen by circuit → block
The LLM cannot bypass these. They are pure code.

### Paper fill simulator — must be REALISTIC, not "fill at LTP"
A naive "fill at last-traded-price" simulator lies and makes paper results meaningless.
The simulator models: **slippage**, **bid/ask spread**, **order rejection**, and **partial
fills** against the live quote — so paper P&L is an honest proxy for live behavior.

### Mode switch
A single config value `execution.mode ∈ {paper, live}` (plus `paper` as the hard default)
selects `PaperBroker` vs a live adapter. Going live = flip the flag **after** validation;
no other code changes.

## Data flow for one decision (paper mode)

1. Scheduler confirms market open (NSE hours + holiday calendar).
2. For `RELIANCE.NS`: data layer fetches price/indicators/fundamentals/news/sentiment
   from India sources.
3. Analysis graph runs on Azure Foundry → returns rating + reasoning + confidence.
4. Router maps rating → target position; risk engine sizes it and applies all guards.
5. Order router (paper) sends to Paper Fill Simulator → realistic quote-based fill
   (slippage/spread/rejection/partials), after passing the MVP fail-closed gates.
6. Portfolio updates positions/cash; India cost model deducts STT/GST/etc.
7. Audit log records the full chain; Azure tracing captures LLM spans.
8. Reflection/memory (existing) later scores realized alpha and feeds it back.

## Why this shape

- **Safety:** deterministic risk gates sit between a non-deterministic brain and real money.
- **Testability:** paper simulator and each adapter are unit-testable in isolation.
- **Portability:** broker + model are adapters; neither is hard-wired into the core.
- **Mergeability:** execution lives beside the upstream package; upstream pulls stay clean.
